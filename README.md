# deploy-pgbench

`deploy_pgbench.py` deploys one or more pgbench loader nodes in AWS or GCP, installs `pgbench` on them with Ansible, and runs pgbench against an existing PostgreSQL server.

The script does not deploy PostgreSQL. You provide the PostgreSQL host, port, user, database, and password when initializing or running pgbench.

## Prerequisites

- Python 3.9 or newer
- Terraform in `PATH`
- Ansible in `PATH`
- AWS or GCP credentials configured for Terraform. For GCP, pass `--gcp-service-account-file` to use a service account JSON file explicitly.
- An SSH key pair for connecting to the loader nodes

Install the Python dependency:

```sh
python3 -m pip install -r requirements.txt
```

## Deploy Loader Nodes

AWS defaults to `c7i.4xlarge` loader nodes:

```sh
python3 deploy_pgbench.py deploy \
  --provider aws \
  --name pgbench \
  --aws-region us-east-1 \
  --nodes 2 \
  --auto-approve
```

GCP defaults to `c4-standard-16` loader nodes:

```sh
python3 deploy_pgbench.py deploy \
  --provider gcp \
  --name pgbench \
  --gcp-project my-project \
  --gcp-service-account-file ~/.config/gcloud/pgbench-service-account.json \
  --gcp-region us-central1 \
  --gcp-zone us-central1-a \
  --nodes 2 \
  --auto-approve
```

Override the loader shape with `--instance-type`.

By default, the script creates a new VPC/network, subnet, and security group/firewall. To deploy into an existing network, pass both `--vpc-id` and `--subnet-id`:

```sh
python3 deploy_pgbench.py deploy \
  --provider aws \
  --vpc-id vpc-1234567890abcdef0 \
  --subnet-id subnet-1234567890abcdef0
```

Use `--private-only` when the loader nodes should not receive public IPs. In that mode, run Ansible from a host that can reach the private loader IPs.

### `deploy` Options

Common options:

- `--work-dir`: Directory for generated Terraform and Ansible files. Default: `.deploy-pgbench`.
- `--name`: Stack name used for cloud resources. Default: `pgbench`.
- `--provider`: Cloud provider. Required. Supported values: `aws`, `gcp`.
- `--nodes`: Number of pgbench loader nodes. Default: `1`.
- `--instance-type`: Loader instance type. Defaults to `c7i.4xlarge` on AWS and `c4-standard-16` on GCP.
- `--ssh-user`: SSH user for Ansible. Default: `ubuntu`.
- `--ssh-private-key-path`: Private key used by Ansible. Default: `~/.ssh/id_rsa`.
- `--ssh-public-key-path`: Public key installed on loader nodes. Default: `~/.ssh/id_rsa.pub`.
- `--ssh-cidr`: CIDR allowed to SSH to loader nodes. Default: `0.0.0.0/0`.
- `--vpc-id`: Existing AWS VPC id or GCP network id, name, or self-link. Must be passed with `--subnet-id`.
- `--subnet-id`: Existing AWS subnet id or GCP subnetwork id, name, or self-link. Must be passed with `--vpc-id`.
- `--vpc-cidr`: CIDR for a newly created VPC/network. Default: `10.44.0.0/16`.
- `--subnet-cidr`: CIDR for a newly created subnet. Default: `10.44.1.0/24`.
- `--owner`: Optional owner value for deployed infrastructure. AWS uses an `Owner` tag; GCP applies an `owner` label and instance metadata where supported.
- `--private-only`: Do not assign public IPs to loader nodes.
- `--skip-setup`: Skip the Ansible package installation step.
- `--auto-approve`: Pass `-auto-approve` to `terraform apply`.

AWS options:

- `--aws-region`: AWS region. Default: `AWS_REGION`, then `AWS_DEFAULT_REGION`, then `us-east-1`.
- `--aws-profile`: AWS profile for Terraform. Default: `AWS_PROFILE`, or empty.
- `--aws-ami-id`: Ubuntu AMI id. If omitted, Terraform finds Ubuntu 24.04.
- `--aws-key-name`: Existing EC2 key pair name. If omitted, Terraform creates one from `--ssh-public-key-path`.

GCP options:

- `--gcp-project`: GCP project id. Default: `GOOGLE_CLOUD_PROJECT`, or empty. Required for GCP deployments.
- `--gcp-region`: GCP region. Default: `us-central1`.
- `--gcp-zone`: GCP zone. Default: `us-central1-a`.
- `--gcp-image`: GCE boot image. Default: `ubuntu-os-cloud/ubuntu-2404-lts-amd64`.
- `--gcp-service-account-file`: Path to a GCP service account JSON credentials file for Terraform. Default: `GOOGLE_APPLICATION_CREDENTIALS`, or empty. If empty, the Google provider uses its standard application default credential lookup.

## Redeploy Loader Setup

Rerun the Ansible setup playbook against the hosts currently listed in the generated inventory:

```sh
python3 deploy_pgbench.py redeploy --provider aws
```

`redeploy` does not run Terraform and does not refresh hosts from Terraform outputs. It uses the current `.deploy-pgbench/<provider>-<name>/inventory.ini`, refreshes the Ansible playbooks from `ansible/`, and reruns `setup_pgbench.yml`.

### `redeploy` Options

- `--work-dir`: Directory containing generated Terraform and Ansible files. Default: `.deploy-pgbench`.
- `--name`: Stack name. Default: `pgbench`.
- `--provider`: Provider for the stack. Optional when only one matching stack exists. Supported values: `aws`, `gcp`.
- `--limit`: Ansible host or group limit for setup. Default: `pgbench_loaders`.

## Initialize Pgbench

Initialize the pgbench schema on an existing PostgreSQL server:

```sh
export PGPASSWORD='postgres-password'

python3 deploy_pgbench.py initialize-db \
  --provider aws \
  --pg-host postgres.example.com \
  --pg-user benchmark \
  --pg-database benchmark \
  --scale 1000
```

`initialize-db` runs on `loader_0` by default. Use `--limit` to choose another Ansible host or group.

### `initialize-db` Options

Stack options:

- `--work-dir`: Directory containing generated Terraform and Ansible files. Default: `.deploy-pgbench`.
- `--name`: Stack name. Default: `pgbench`.
- `--provider`: Provider for the stack. Optional when only one matching stack exists. Supported values: `aws`, `gcp`.

PostgreSQL connection options:

- `--pg-host`: Existing PostgreSQL host or endpoint. Required.
- `--pg-port`: Existing PostgreSQL port. Default: `5432`.
- `--pg-user`: PostgreSQL user. Required.
- `--pg-database`: PostgreSQL database. Required.
- `--pg-password`: PostgreSQL password. Prefer `--pg-password-env` to avoid shell history.
- `--pg-password-env`: Environment variable containing the password. Default: `PGPASSWORD`.
- `--pg-sslmode`: libpq `PGSSLMODE` used by pgbench. Default: `prefer`.

Pgbench options:

- `--scale`: pgbench scale factor. Default: `100`.
- `--async-timeout`: Maximum seconds to allow the remote pgbench initialize job to run. Default: `86400`.
- `--poll-interval`: Seconds between Ansible polls while waiting for the remote pgbench initialize job. Default: `15`.
- `--extra-args`: Extra arguments appended to `pgbench`.
- `--limit`: Ansible host or group limit for initialization. Default: `loader_0`.

## Run Pgbench

Run a workload from all loader nodes:

```sh
export PGPASSWORD='postgres-password'

python3 deploy_pgbench.py run \
  --provider aws \
  --pg-host postgres.example.com \
  --pg-user benchmark \
  --pg-database benchmark \
  --clients 64 \
  --jobs 16 \
  --time 600 \
  --progress 10
```

Additional pgbench flags can be passed with `--extra-args`.

The script refreshes the copied `pgbench.yml` before each `run` or `initialize-db` execution. Pgbench runs through Ansible async polling, so the remote job is not tied to one long-lived SSH command session. Ansible also runs with SSH keepalives and SSH multiplexing disabled by default to avoid idle pgbench jobs failing with `Shared connection ... closed` or `Broken pipe`. To override those SSH defaults, set `ANSIBLE_SSH_ARGS` before running the script.

### `run` Options

Stack options:

- `--work-dir`: Directory containing generated Terraform and Ansible files. Default: `.deploy-pgbench`.
- `--name`: Stack name. Default: `pgbench`.
- `--provider`: Provider for the stack. Optional when only one matching stack exists. Supported values: `aws`, `gcp`.

PostgreSQL connection options:

- `--pg-host`: Existing PostgreSQL host or endpoint. Required.
- `--pg-port`: Existing PostgreSQL port. Default: `5432`.
- `--pg-user`: PostgreSQL user. Required.
- `--pg-database`: PostgreSQL database. Required.
- `--pg-password`: PostgreSQL password. Prefer `--pg-password-env` to avoid shell history.
- `--pg-password-env`: Environment variable containing the password. Default: `PGPASSWORD`.
- `--pg-sslmode`: libpq `PGSSLMODE` used by pgbench. Default: `prefer`.

Pgbench workload options:

- `--clients`: Number of pgbench clients per loader. Default: `64`.
- `--jobs`: Number of pgbench worker threads per loader. Default: `16`.
- `--time`: Duration in seconds. Set `0` to omit. Default: `600`.
- `--transactions`: Transactions per client. Set `0` to omit. Default: `0`.
- `--rate`: Target rate per loader. Set `0` to omit. Default: `0`.
- `--progress`: Progress interval in seconds. Set `0` to omit. Default: `10`.
- `--async-timeout`: Maximum seconds to allow the remote pgbench workload job to run. Default: `86400`.
- `--poll-interval`: Seconds between Ansible polls while waiting for remote pgbench workloads. Default: `15`.
- `--extra-args`: Extra arguments appended to `pgbench`.
- `--limit`: Ansible host or group limit for the workload. Default: `pgbench_loaders`.

## Show Deployment Details

```sh
python3 deploy_pgbench.py show --provider aws
```

This prints Terraform outputs and the generated Ansible inventory.

### `show` Options

- `--work-dir`: Directory containing generated Terraform and Ansible files. Default: `.deploy-pgbench`.
- `--name`: Stack name. Default: `pgbench`.
- `--provider`: Provider for the stack. Optional when only one matching stack exists. Supported values: `aws`, `gcp`.

## Destroy Loader Nodes

```sh
python3 deploy_pgbench.py destroy \
  --provider aws \
  --auto-approve
```

### `destroy` Options

- `--work-dir`: Directory containing generated Terraform and Ansible files. Default: `.deploy-pgbench`.
- `--name`: Stack name. Default: `pgbench`.
- `--provider`: Provider for the stack. Optional when only one matching stack exists. Supported values: `aws`, `gcp`.
- `--auto-approve`: Pass `-auto-approve` to `terraform destroy`.

## Generated Files

Source Terraform files live under `terraform/`. Source Ansible playbooks live under `ansible/`.

Deployment files are copied and written under `.deploy-pgbench/<provider>-<name>/`. That directory contains generated Terraform files, Ansible playbooks, inventory, Terraform variables, and Terraform state, so it is ignored by git.
