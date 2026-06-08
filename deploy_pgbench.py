#!/usr/bin/env python3
"""Deploy pgbench loader nodes with Terraform and run pgbench with Ansible."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional


AWS_DEFAULT_INSTANCE_TYPE = "c7i.4xlarge"
GCP_DEFAULT_INSTANCE_TYPE = "c4-standard-16"
DEFAULT_WORK_DIR = ".deploy-pgbench"
DEFAULT_ANSIBLE_SSH_ARGS = (
    "-C "
    "-o ControlMaster=no "
    "-o ControlPersist=no "
    "-o ServerAliveInterval=30 "
    "-o ServerAliveCountMax=6 "
    "-o TCPKeepAlive=yes"
)


ASSET_DIR = Path(__file__).resolve().parent


class PgbenchDeployError(RuntimeError):
    """Raised for expected user-facing failures."""


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except PgbenchDeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}", file=sys.stderr)
        return exc.returncode

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy pgbench loader nodes in AWS or GCP and run pgbench with Ansible.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy = subparsers.add_parser(
        "deploy",
        help="Create loader infrastructure and install pgbench.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_stack_args(deploy)
    deploy.add_argument("--provider", choices=["aws", "gcp"], required=True)
    deploy.add_argument("--nodes", type=positive_int, default=1, help="Number of pgbench loader nodes.")
    deploy.add_argument("--instance-type", help="Loader instance type. Defaults depend on provider.")
    deploy.add_argument("--ssh-user", default="ubuntu", help="SSH user for Ansible.")
    deploy.add_argument("--ssh-private-key-path", default="~/.ssh/id_rsa", help="Private key used by Ansible.")
    deploy.add_argument("--ssh-public-key-path", default="~/.ssh/id_rsa.pub", help="Public key installed on loader nodes.")
    deploy.add_argument("--ssh-cidr", default="0.0.0.0/0", help="CIDR allowed to SSH to loader nodes.")
    deploy.add_argument("--vpc-id", default="", help="Existing VPC/network id, name, or self-link.")
    deploy.add_argument("--subnet-id", default="", help="Existing subnet/subnetwork id, name, or self-link.")
    deploy.add_argument("--vpc-cidr", default="10.44.0.0/16", help="CIDR for a newly created VPC/network.")
    deploy.add_argument("--subnet-cidr", default="10.44.1.0/24", help="CIDR for a newly created subnet.")
    deploy.add_argument("--owner", default="", help="Optional owner value for deployed infrastructure tags or labels.")
    deploy.add_argument("--private-only", action="store_true", help="Do not assign public IPs to loader nodes.")
    deploy.add_argument("--skip-setup", action="store_true", help="Skip the Ansible package installation step.")
    deploy.add_argument("--auto-approve", action="store_true", help="Pass -auto-approve to terraform apply.")
    add_aws_deploy_args(deploy)
    add_gcp_deploy_args(deploy)
    deploy.set_defaults(func=deploy_command)

    redeploy = subparsers.add_parser(
        "redeploy",
        help="Rerun the setup playbook against the current inventory hosts.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_stack_args(redeploy)
    add_optional_provider_arg(redeploy)
    redeploy.add_argument("--limit", default="pgbench_loaders", help="Ansible host/group limit for setup.")
    redeploy.set_defaults(func=redeploy_command)

    initialize = subparsers.add_parser(
        "initialize-db",
        help="Run pgbench --initialize against an existing PostgreSQL server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_stack_args(initialize)
    add_optional_provider_arg(initialize)
    add_pg_connection_args(initialize)
    initialize.add_argument("--scale", type=positive_int, default=100, help="pgbench scale factor.")
    initialize.add_argument("--extra-args", default="", help="Extra arguments appended to pgbench.")
    initialize.add_argument("--limit", default="loader_0", help="Ansible host/group limit for initialization.")
    initialize.set_defaults(func=initialize_db_command)

    run = subparsers.add_parser(
        "run",
        help="Run a pgbench workload against an existing PostgreSQL server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_stack_args(run)
    add_optional_provider_arg(run)
    add_pg_connection_args(run)
    run.add_argument("--clients", type=positive_int, default=64, help="Number of pgbench clients per loader.")
    run.add_argument("--jobs", type=positive_int, default=16, help="Number of pgbench worker threads per loader.")
    run.add_argument("--time", type=non_negative_int, default=600, help="Duration in seconds. Set 0 to omit.")
    run.add_argument("--transactions", type=non_negative_int, default=0, help="Transactions per client. Set 0 to omit.")
    run.add_argument("--rate", type=non_negative_int, default=0, help="Target rate per loader. Set 0 to omit.")
    run.add_argument("--progress", type=non_negative_int, default=10, help="Progress interval in seconds. Set 0 to omit.")
    run.add_argument("--extra-args", default="", help="Extra arguments appended to pgbench.")
    run.add_argument("--limit", default="pgbench_loaders", help="Ansible host/group limit for the workload.")
    run.set_defaults(func=run_command)

    show = subparsers.add_parser(
        "show",
        help="Show Terraform outputs and generated inventory.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_stack_args(show)
    add_optional_provider_arg(show)
    show.set_defaults(func=show_command)

    destroy = subparsers.add_parser(
        "destroy",
        help="Destroy loader infrastructure.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_stack_args(destroy)
    add_optional_provider_arg(destroy)
    destroy.add_argument("--auto-approve", action="store_true", help="Pass -auto-approve to terraform destroy.")
    destroy.set_defaults(func=destroy_command)

    return parser


def add_stack_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR, help="Directory for generated Terraform and Ansible files.")
    parser.add_argument("--name", default="pgbench", help="Stack name used for cloud resources.")


def add_optional_provider_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=["aws", "gcp"], help="Provider for the stack. Required if ambiguous.")


def add_aws_deploy_args(parser: argparse.ArgumentParser) -> None:
    aws = parser.add_argument_group("AWS")
    aws.add_argument("--aws-region", default=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")))
    aws.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE", ""), help="AWS profile for Terraform.")
    aws.add_argument("--aws-ami-id", default="", help="Ubuntu AMI id. If omitted, Terraform finds Ubuntu 24.04.")
    aws.add_argument("--aws-key-name", default="", help="Existing EC2 key pair name. If omitted, Terraform creates one.")


def add_gcp_deploy_args(parser: argparse.ArgumentParser) -> None:
    gcp = parser.add_argument_group("GCP")
    gcp.add_argument("--gcp-project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""), help="GCP project id.")
    gcp.add_argument("--gcp-region", default="us-central1")
    gcp.add_argument("--gcp-zone", default="us-central1-a")
    gcp.add_argument("--gcp-image", default="ubuntu-os-cloud/ubuntu-2404-lts-amd64", help="GCE boot image.")
    gcp.add_argument(
        "--gcp-service-account-file",
        default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        help="Path to a GCP service account JSON credentials file for Terraform.",
    )


def add_pg_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pg-host", required=True, help="Existing PostgreSQL host or endpoint.")
    parser.add_argument("--pg-port", type=positive_int, default=5432, help="Existing PostgreSQL port.")
    parser.add_argument("--pg-user", required=True, help="PostgreSQL user.")
    parser.add_argument("--pg-database", required=True, help="PostgreSQL database.")
    parser.add_argument("--pg-password", default="", help="PostgreSQL password. Prefer --pg-password-env for shell history.")
    parser.add_argument("--pg-password-env", default="PGPASSWORD", help="Environment variable containing the password.")
    parser.add_argument("--pg-sslmode", default="prefer", help="libpq PGSSLMODE used by pgbench.")


def deploy_command(args: argparse.Namespace) -> None:
    require_executable("terraform")
    if not args.skip_setup:
        require_executable("ansible-playbook")

    validate_deploy_args(args)
    stack_dir = get_stack_dir(args.work_dir, args.provider, args.name)
    stack_dir.mkdir(parents=True, exist_ok=True)

    instance_type = args.instance_type or default_instance_type(args.provider)
    config = {
        "provider": args.provider,
        "name": args.name,
        "node_count": args.nodes,
        "instance_type": instance_type,
        "ssh_user": args.ssh_user,
        "ssh_private_key_path": str(expand_path(args.ssh_private_key_path)),
        "ssh_public_key_path": str(expand_path(args.ssh_public_key_path)),
        "associate_public_ip": not args.private_only,
        "owner": args.owner,
    }

    write_stack_files(stack_dir, args, instance_type)
    write_json(stack_dir / "deploy_pgbench_config.json", config)

    run_process(["terraform", "init"], cwd=stack_dir)
    apply_cmd = ["terraform", "apply"]
    if args.auto_approve:
        apply_cmd.append("-auto-approve")
    run_process(apply_cmd, cwd=stack_dir)

    outputs = terraform_outputs(stack_dir)
    write_inventory(stack_dir, outputs, config)

    if not args.skip_setup:
        run_ansible(stack_dir, "setup_pgbench.yml")

    print_summary(stack_dir, outputs)


def redeploy_command(args: argparse.Namespace) -> None:
    require_executable("ansible-playbook")
    stack_dir = resolve_existing_stack_dir(args.work_dir, args.provider, args.name)
    inventory = stack_dir / "inventory.ini"
    if not inventory.exists():
        raise PgbenchDeployError(f"missing inventory file: {inventory}")

    sync_ansible_files(stack_dir)
    run_ansible(stack_dir, "setup_pgbench.yml", limit=args.limit)


def initialize_db_command(args: argparse.Namespace) -> None:
    require_executable("ansible-playbook")
    stack_dir = resolve_existing_stack_dir(args.work_dir, args.provider, args.name)
    config = read_config(stack_dir)
    ensure_inventory(stack_dir, config)
    sync_ansible_files(stack_dir)
    vars_file = write_extra_vars(stack_dir, pg_vars(args, mode="initialize"))
    try:
        run_ansible(stack_dir, "pgbench.yml", limit=args.limit, extra_vars_file=vars_file)
    finally:
        vars_file.unlink(missing_ok=True)


def run_command(args: argparse.Namespace) -> None:
    require_executable("ansible-playbook")
    stack_dir = resolve_existing_stack_dir(args.work_dir, args.provider, args.name)
    config = read_config(stack_dir)
    ensure_inventory(stack_dir, config)
    sync_ansible_files(stack_dir)
    vars_file = write_extra_vars(stack_dir, pg_vars(args, mode="run"))
    try:
        run_ansible(stack_dir, "pgbench.yml", limit=args.limit, extra_vars_file=vars_file)
    finally:
        vars_file.unlink(missing_ok=True)


def show_command(args: argparse.Namespace) -> None:
    require_executable("terraform")
    stack_dir = resolve_existing_stack_dir(args.work_dir, args.provider, args.name)
    outputs = terraform_outputs(stack_dir)
    print_summary(stack_dir, outputs)

    inventory = stack_dir / "inventory.ini"
    if inventory.exists():
        print()
        print(f"Inventory: {inventory}")
        print(inventory.read_text())


def destroy_command(args: argparse.Namespace) -> None:
    require_executable("terraform")
    stack_dir = resolve_existing_stack_dir(args.work_dir, args.provider, args.name)
    run_process(["terraform", "init"], cwd=stack_dir)
    destroy_cmd = ["terraform", "destroy"]
    if args.auto_approve:
        destroy_cmd.append("-auto-approve")
    run_process(destroy_cmd, cwd=stack_dir)


def validate_deploy_args(args: argparse.Namespace) -> None:
    if bool(args.vpc_id) != bool(args.subnet_id):
        raise PgbenchDeployError("pass both --vpc-id and --subnet-id, or neither")

    if args.provider == "gcp" and not args.gcp_project:
        raise PgbenchDeployError("GCP deployments require --gcp-project or GOOGLE_CLOUD_PROJECT")

    if args.provider == "gcp" and args.gcp_service_account_file and not expand_path(args.gcp_service_account_file).exists():
        raise PgbenchDeployError(f"GCP service account file not found: {expand_path(args.gcp_service_account_file)}")

    public_key_required = args.provider == "gcp" or not args.aws_key_name
    if public_key_required and not expand_path(args.ssh_public_key_path).exists():
        raise PgbenchDeployError(f"SSH public key not found: {expand_path(args.ssh_public_key_path)}")

    if not expand_path(args.ssh_private_key_path).exists():
        raise PgbenchDeployError(f"SSH private key not found: {expand_path(args.ssh_private_key_path)}")


def write_stack_files(stack_dir: Path, args: argparse.Namespace, instance_type: str) -> None:
    copy_asset(ASSET_DIR / "terraform" / args.provider / "main.tf", stack_dir / "main.tf")
    sync_ansible_files(stack_dir)

    tfvars: dict[str, Any] = {
        "name": args.name,
        "owner": args.owner,
        "instance_type": instance_type,
        "node_count": args.nodes,
        "ssh_public_key_path": str(expand_path(args.ssh_public_key_path)),
        "ssh_cidr": args.ssh_cidr,
        "vpc_id": args.vpc_id,
        "subnet_id": args.subnet_id,
        "vpc_cidr": args.vpc_cidr,
        "subnet_cidr": args.subnet_cidr,
        "associate_public_ip": not args.private_only,
    }

    if args.provider == "aws":
        tfvars.update(
            {
                "aws_region": args.aws_region,
                "aws_profile": args.aws_profile,
                "aws_ami_id": args.aws_ami_id,
                "aws_key_name": args.aws_key_name,
            }
        )
    else:
        tfvars.update(
            {
                "gcp_project": args.gcp_project,
                "gcp_region": args.gcp_region,
                "gcp_zone": args.gcp_zone,
                "gcp_image": args.gcp_image,
                "gcp_service_account_file": str(expand_path(args.gcp_service_account_file)) if args.gcp_service_account_file else "",
                "ssh_user": args.ssh_user,
            }
        )

    write_json(stack_dir / "terraform.tfvars.json", tfvars)


def sync_ansible_files(stack_dir: Path) -> None:
    copy_asset(ASSET_DIR / "ansible" / "setup_pgbench.yml", stack_dir / "setup_pgbench.yml")
    copy_asset(ASSET_DIR / "ansible" / "pgbench.yml", stack_dir / "pgbench.yml")


def copy_asset(source: Path, destination: Path) -> None:
    if not source.exists():
        raise PgbenchDeployError(f"required asset file not found: {source}")
    shutil.copyfile(source, destination)


def ensure_inventory(stack_dir: Path, config: dict[str, Any]) -> None:
    inventory = stack_dir / "inventory.ini"
    if inventory.exists():
        return

    outputs = terraform_outputs(stack_dir)
    write_inventory(stack_dir, outputs, config)


def write_inventory(stack_dir: Path, outputs: dict[str, Any], config: dict[str, Any]) -> None:
    hosts = outputs.get("ansible_hosts", [])
    if not hosts:
        raise PgbenchDeployError("Terraform output did not include any ansible_hosts")

    private_key = config["ssh_private_key_path"]
    ssh_user = config["ssh_user"]

    lines = ["[pgbench_loaders]"]
    for index, host in enumerate(hosts):
        if not host:
            raise PgbenchDeployError("Terraform returned an empty Ansible host; check public/private IP settings")
        lines.append(f"loader_{index} ansible_host={host}")

    lines.extend(
        [
            "",
            "[pgbench_loaders:vars]",
            f"ansible_user={ssh_user}",
            f"ansible_ssh_private_key_file={private_key}",
            "ansible_python_interpreter=/usr/bin/python3",
            "ansible_ssh_common_args='-o StrictHostKeyChecking=accept-new'",
            "",
        ]
    )
    (stack_dir / "inventory.ini").write_text("\n".join(lines))


def pg_vars(args: argparse.Namespace, mode: str) -> dict[str, Any]:
    password = args.pg_password or os.environ.get(args.pg_password_env, "")
    common = {
        "pgbench_mode": mode,
        "pg_host": args.pg_host,
        "pg_port": args.pg_port,
        "pg_user": args.pg_user,
        "pg_database": args.pg_database,
        "pg_password": password,
        "pg_sslmode": args.pg_sslmode,
        "pgbench_extra_args": args.extra_args,
    }

    if mode == "initialize":
        common["pgbench_scale"] = args.scale
    else:
        common.update(
            {
                "pgbench_clients": args.clients,
                "pgbench_jobs": args.jobs,
                "pgbench_time": args.time,
                "pgbench_transactions": args.transactions,
                "pgbench_rate": args.rate,
                "pgbench_progress": args.progress,
            }
        )

    return common


def run_ansible(
    stack_dir: Path,
    playbook: str,
    *,
    limit: Optional[str] = None,
    extra_vars_file: Optional[Path] = None,
) -> None:
    cmd = ["ansible-playbook", "-i", "inventory.ini", playbook]
    if limit:
        cmd.extend(["--limit", limit])
    if extra_vars_file:
        cmd.extend(["--extra-vars", f"@{extra_vars_file}"])
    env = os.environ.copy()
    env.setdefault("ANSIBLE_SSH_ARGS", DEFAULT_ANSIBLE_SSH_ARGS)
    run_process(cmd, cwd=stack_dir, env=env)


def terraform_outputs(stack_dir: Path) -> dict[str, Any]:
    result = run_process(["terraform", "output", "-json"], cwd=stack_dir, capture=True)
    raw_outputs = json.loads(result.stdout or "{}")
    return {key: value.get("value") for key, value in raw_outputs.items()}


def print_summary(stack_dir: Path, outputs: dict[str, Any]) -> None:
    print(f"Stack directory: {stack_dir}")
    for key in sorted(outputs):
        print(f"{key}: {json.dumps(outputs[key])}")


def write_extra_vars(stack_dir: Path, values: dict[str, Any]) -> Path:
    fd, path = tempfile.mkstemp(prefix="pgbench-vars-", suffix=".json", dir=stack_dir)
    vars_path = Path(path)
    with os.fdopen(fd, "w") as handle:
        json.dump(values, handle, indent=2)
        handle.write("\n")
    vars_path.chmod(0o600)
    return vars_path


def read_config(stack_dir: Path) -> dict[str, Any]:
    config_path = stack_dir / "deploy_pgbench_config.json"
    if not config_path.exists():
        raise PgbenchDeployError(f"missing config file: {config_path}")
    return json.loads(config_path.read_text())


def resolve_existing_stack_dir(work_dir: str, provider: Optional[str], name: str) -> Path:
    if provider:
        stack_dir = get_stack_dir(work_dir, provider, name)
        if not stack_dir.exists():
            raise PgbenchDeployError(f"stack directory does not exist: {stack_dir}")
        return stack_dir

    base_dir = expand_path(work_dir)
    matches = sorted(base_dir.glob(f"*-{name}/deploy_pgbench_config.json"))
    if len(matches) == 1:
        return matches[0].parent
    if not matches:
        raise PgbenchDeployError("no stack found; pass --provider or run deploy first")
    providers = ", ".join(path.parent.name for path in matches)
    raise PgbenchDeployError(f"multiple stacks match --name {name!r}: {providers}; pass --provider")


def get_stack_dir(work_dir: str, provider: str, name: str) -> Path:
    return expand_path(work_dir) / f"{provider}-{name}"


def default_instance_type(provider: str) -> str:
    return AWS_DEFAULT_INSTANCE_TYPE if provider == "aws" else GCP_DEFAULT_INSTANCE_TYPE


def expand_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_process(
    cmd: list[str],
    *,
    cwd: Path,
    capture: bool = False,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
        env=env,
    )


def require_executable(name: str) -> None:
    if not shutil.which(name):
        raise PgbenchDeployError(f"required executable not found in PATH: {name}")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to 0")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
