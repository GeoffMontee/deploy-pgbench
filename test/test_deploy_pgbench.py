import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy_pgbench


def parse_args(*args):
    return deploy_pgbench.build_parser().parse_args(list(args))


def test_default_instance_types():
    assert deploy_pgbench.default_instance_type("aws") == "c7i.4xlarge"
    assert deploy_pgbench.default_instance_type("gcp") == "c4-standard-16"


@pytest.mark.parametrize(
    ("command", "value"),
    [
        ("deploy", "0"),
        ("deploy", "-1"),
    ],
)
def test_deploy_rejects_non_positive_node_counts(command, value):
    with pytest.raises(SystemExit):
        parse_args(command, "--provider", "aws", "--nodes", value)


def test_run_parser_sets_expected_defaults():
    args = parse_args(
        "run",
        "--provider",
        "aws",
        "--pg-host",
        "postgres.example.com",
        "--pg-user",
        "benchmark",
        "--pg-database",
        "benchmark",
    )

    assert args.clients == 64
    assert args.jobs == 16
    assert args.time == 600
    assert args.progress == 10
    assert args.limit == "pgbench_loaders"


def test_redeploy_parser_sets_expected_defaults():
    args = parse_args("redeploy", "--provider", "aws")

    assert args.name == "pgbench"
    assert args.work_dir == ".deploy-pgbench"
    assert args.limit == "pgbench_loaders"


def test_deploy_parser_accepts_owner_tag():
    args = parse_args("deploy", "--provider", "aws", "--owner", "data-platform")

    assert args.owner == "data-platform"


def test_deploy_parser_accepts_gcp_service_account_file():
    args = parse_args(
        "deploy",
        "--provider",
        "gcp",
        "--gcp-service-account-file",
        "/tmp/service-account.json",
    )

    assert args.gcp_service_account_file == "/tmp/service-account.json"


def test_infrastructure_assets_are_external_files():
    assert not hasattr(deploy_pgbench, "AWS_TERRAFORM")
    assert not hasattr(deploy_pgbench, "GCP_TERRAFORM")
    assert not hasattr(deploy_pgbench, "SETUP_PLAYBOOK")
    assert not hasattr(deploy_pgbench, "PGBENCH_PLAYBOOK")
    assert (deploy_pgbench.ASSET_DIR / "terraform" / "aws" / "main.tf").exists()
    assert (deploy_pgbench.ASSET_DIR / "terraform" / "gcp" / "main.tf").exists()
    assert (deploy_pgbench.ASSET_DIR / "ansible" / "setup_pgbench.yml").exists()
    assert (deploy_pgbench.ASSET_DIR / "ansible" / "pgbench.yml").exists()


def test_setup_playbook_installs_pgbench_package():
    playbook = (deploy_pgbench.ASSET_DIR / "ansible" / "setup_pgbench.yml").read_text()

    assert "postgresql-contrib" in playbook


def test_pgbench_playbook_has_connection_preflight():
    playbook = (deploy_pgbench.ASSET_DIR / "ansible" / "pgbench.yml").read_text()

    assert "ansible.builtin.wait_for_connection" in playbook
    assert "pgbench --version" in playbook


def test_pg_vars_prefers_explicit_password(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "from-env")
    args = argparse.Namespace(
        pg_host="postgres.example.com",
        pg_port=5432,
        pg_user="benchmark",
        pg_database="benchmark",
        pg_password="from-arg",
        pg_password_env="PGPASSWORD",
        pg_sslmode="require",
        extra_args="--protocol prepared",
        clients=32,
        jobs=8,
        time=60,
        transactions=0,
        rate=0,
        progress=5,
    )

    values = deploy_pgbench.pg_vars(args, mode="run")

    assert values == {
        "pgbench_mode": "run",
        "pg_host": "postgres.example.com",
        "pg_port": 5432,
        "pg_user": "benchmark",
        "pg_database": "benchmark",
        "pg_password": "from-arg",
        "pg_sslmode": "require",
        "pgbench_extra_args": "--protocol prepared",
        "pgbench_clients": 32,
        "pgbench_jobs": 8,
        "pgbench_time": 60,
        "pgbench_transactions": 0,
        "pgbench_rate": 0,
        "pgbench_progress": 5,
    }


def test_pg_vars_reads_password_from_configured_environment(monkeypatch):
    monkeypatch.setenv("CUSTOM_PG_PASSWORD", "from-custom-env")
    args = argparse.Namespace(
        pg_host="postgres.example.com",
        pg_port=5432,
        pg_user="benchmark",
        pg_database="benchmark",
        pg_password="",
        pg_password_env="CUSTOM_PG_PASSWORD",
        pg_sslmode="prefer",
        extra_args="",
        scale=1000,
    )

    values = deploy_pgbench.pg_vars(args, mode="initialize")

    assert values["pg_password"] == "from-custom-env"
    assert values["pgbench_scale"] == 1000
    assert "pgbench_clients" not in values


def test_write_inventory_creates_expected_hosts(tmp_path):
    deploy_pgbench.write_inventory(
        tmp_path,
        {"ansible_hosts": ["203.0.113.10", "203.0.113.11"]},
        {"ssh_private_key_path": "/tmp/key.pem", "ssh_user": "ubuntu"},
    )

    inventory = (tmp_path / "inventory.ini").read_text()

    assert "loader_0 ansible_host=203.0.113.10" in inventory
    assert "loader_1 ansible_host=203.0.113.11" in inventory
    assert "ansible_user=ubuntu" in inventory
    assert "ansible_ssh_private_key_file=/tmp/key.pem" in inventory


def test_write_inventory_rejects_empty_hosts(tmp_path):
    with pytest.raises(deploy_pgbench.PgbenchDeployError, match="ansible_hosts"):
        deploy_pgbench.write_inventory(
            tmp_path,
            {"ansible_hosts": []},
            {"ssh_private_key_path": "/tmp/key.pem", "ssh_user": "ubuntu"},
        )


def test_run_ansible_sets_resilient_default_ssh_args(tmp_path, monkeypatch):
    calls = []

    monkeypatch.delenv("ANSIBLE_SSH_ARGS", raising=False)
    monkeypatch.setattr(
        deploy_pgbench,
        "run_process",
        lambda cmd, **kwargs: calls.append((cmd, kwargs)),
    )

    deploy_pgbench.run_ansible(tmp_path, "pgbench.yml", limit="loader_0")

    cmd, kwargs = calls[0]
    assert cmd == ["ansible-playbook", "-i", "inventory.ini", "pgbench.yml", "--limit", "loader_0"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["ANSIBLE_SSH_ARGS"] == deploy_pgbench.DEFAULT_ANSIBLE_SSH_ARGS


def test_run_ansible_preserves_user_ssh_args(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setenv("ANSIBLE_SSH_ARGS", "-o UserKnownHostsFile=/tmp/known_hosts")
    monkeypatch.setattr(
        deploy_pgbench,
        "run_process",
        lambda cmd, **kwargs: calls.append((cmd, kwargs)),
    )

    deploy_pgbench.run_ansible(tmp_path, "pgbench.yml")

    assert calls[0][1]["env"]["ANSIBLE_SSH_ARGS"] == "-o UserKnownHostsFile=/tmp/known_hosts"


def test_redeploy_reruns_setup_playbook_on_existing_inventory(tmp_path, monkeypatch):
    stack_dir = tmp_path / "aws-pgbench"
    stack_dir.mkdir()
    (stack_dir / "deploy_pgbench_config.json").write_text("{}")
    (stack_dir / "inventory.ini").write_text("[pgbench_loaders]\nloader_0 ansible_host=203.0.113.10\n")
    (stack_dir / "setup_pgbench.yml").write_text("---\n# stale playbook\n")
    calls = []

    monkeypatch.setattr(deploy_pgbench, "require_executable", lambda name: calls.append(("require", name)))
    monkeypatch.setattr(
        deploy_pgbench,
        "run_ansible",
        lambda stack_dir, playbook, **kwargs: calls.append(("ansible", stack_dir, playbook, kwargs)),
    )
    args = argparse.Namespace(work_dir=str(tmp_path), provider="aws", name="pgbench", limit="pgbench_loaders")

    deploy_pgbench.redeploy_command(args)

    assert calls == [
        ("require", "ansible-playbook"),
        ("ansible", stack_dir, "setup_pgbench.yml", {"limit": "pgbench_loaders"}),
    ]
    assert "postgresql-contrib" in (stack_dir / "setup_pgbench.yml").read_text()


def test_redeploy_requires_existing_inventory(tmp_path, monkeypatch):
    stack_dir = tmp_path / "aws-pgbench"
    stack_dir.mkdir()
    (stack_dir / "deploy_pgbench_config.json").write_text("{}")
    monkeypatch.setattr(deploy_pgbench, "require_executable", lambda name: None)
    args = argparse.Namespace(work_dir=str(tmp_path), provider="aws", name="pgbench", limit="pgbench_loaders")

    with pytest.raises(deploy_pgbench.PgbenchDeployError, match="missing inventory file"):
        deploy_pgbench.redeploy_command(args)


def test_resolve_existing_stack_dir_finds_single_matching_stack(tmp_path):
    stack_dir = tmp_path / "aws-pgbench"
    stack_dir.mkdir()
    (stack_dir / "deploy_pgbench_config.json").write_text("{}")

    assert deploy_pgbench.resolve_existing_stack_dir(str(tmp_path), None, "pgbench") == stack_dir


def test_resolve_existing_stack_dir_requires_provider_when_ambiguous(tmp_path):
    for provider in ("aws", "gcp"):
        stack_dir = tmp_path / f"{provider}-pgbench"
        stack_dir.mkdir()
        (stack_dir / "deploy_pgbench_config.json").write_text("{}")

    with pytest.raises(deploy_pgbench.PgbenchDeployError, match="multiple stacks"):
        deploy_pgbench.resolve_existing_stack_dir(str(tmp_path), None, "pgbench")


def test_validate_deploy_args_requires_vpc_and_subnet_together():
    args = argparse.Namespace(
        vpc_id="vpc-123",
        subnet_id="",
        provider="aws",
        aws_key_name="existing-key",
        ssh_public_key_path="/tmp/missing.pub",
        ssh_private_key_path="/tmp/missing",
        gcp_project="",
    )

    with pytest.raises(deploy_pgbench.PgbenchDeployError, match="both --vpc-id and --subnet-id"):
        deploy_pgbench.validate_deploy_args(args)


def test_validate_deploy_args_rejects_missing_gcp_service_account_file():
    args = argparse.Namespace(
        vpc_id="",
        subnet_id="",
        provider="gcp",
        gcp_project="project-id",
        gcp_service_account_file="/tmp/missing-service-account.json",
        aws_key_name="",
        ssh_public_key_path="/tmp/missing.pub",
        ssh_private_key_path="/tmp/missing",
    )

    with pytest.raises(deploy_pgbench.PgbenchDeployError, match="GCP service account file not found"):
        deploy_pgbench.validate_deploy_args(args)


def test_write_stack_files_writes_aws_tfvars(tmp_path):
    public_key = tmp_path / "id_rsa.pub"
    public_key.write_text("ssh-rsa AAAATEST user@example.com\n")
    args = argparse.Namespace(
        provider="aws",
        name="bench",
        nodes=2,
        ssh_public_key_path=str(public_key),
        ssh_cidr="203.0.113.0/24",
        vpc_id="",
        subnet_id="",
        vpc_cidr="10.44.0.0/16",
        subnet_cidr="10.44.1.0/24",
        owner="data-platform",
        private_only=True,
        aws_region="us-east-2",
        aws_profile="dev",
        aws_ami_id="ami-123",
        aws_key_name="bench-key",
    )

    deploy_pgbench.write_stack_files(tmp_path, args, "c7i.8xlarge")

    tfvars = json.loads((tmp_path / "terraform.tfvars.json").read_text())
    assert tfvars["instance_type"] == "c7i.8xlarge"
    assert tfvars["node_count"] == 2
    assert tfvars["owner"] == "data-platform"
    assert tfvars["associate_public_ip"] is False
    assert tfvars["aws_region"] == "us-east-2"
    assert tfvars["aws_key_name"] == "bench-key"
    assert "Owner = var.owner" in (tmp_path / "main.tf").read_text()
    assert (tmp_path / "setup_pgbench.yml").exists()
    assert (tmp_path / "pgbench.yml").exists()


def test_write_stack_files_writes_gcp_service_account_file(tmp_path):
    public_key = tmp_path / "id_rsa.pub"
    credentials_file = tmp_path / "service-account.json"
    public_key.write_text("ssh-rsa AAAATEST user@example.com\n")
    credentials_file.write_text('{"type": "service_account"}\n')
    args = argparse.Namespace(
        provider="gcp",
        name="bench",
        nodes=1,
        ssh_user="ubuntu",
        ssh_public_key_path=str(public_key),
        ssh_cidr="203.0.113.0/24",
        vpc_id="",
        subnet_id="",
        vpc_cidr="10.44.0.0/16",
        subnet_cidr="10.44.1.0/24",
        owner="data-platform",
        private_only=False,
        gcp_project="project-id",
        gcp_region="us-central1",
        gcp_zone="us-central1-a",
        gcp_image="ubuntu-os-cloud/ubuntu-2404-lts-amd64",
        gcp_service_account_file=str(credentials_file),
    )

    deploy_pgbench.write_stack_files(tmp_path, args, "c4-standard-16")

    tfvars = json.loads((tmp_path / "terraform.tfvars.json").read_text())
    assert tfvars["gcp_service_account_file"] == str(credentials_file.resolve())
    assert tfvars["gcp_project"] == "project-id"
    assert "credentials = var.gcp_service_account_file" in (tmp_path / "main.tf").read_text()
