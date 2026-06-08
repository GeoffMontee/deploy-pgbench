# Agent Guidance

This repository contains a Python CLI, `deploy_pgbench.py`, that copies Terraform and Ansible source files to deploy pgbench loader nodes in AWS or GCP. Keep changes small, explicit, and aligned with the existing CLI structure.

## Documentation

- Update `README.md` whenever user-facing flags, defaults, generated files, prerequisites, or workflows change.
- Keep command-line option documentation complete for every subcommand.
- Keep examples copy-pasteable and avoid implying that the script deploys PostgreSQL. PostgreSQL is always an existing external service.
- Document cloud-specific behavior for both AWS and GCP when behavior differs, including default instance types.
- Do not document secrets inline. Prefer environment variables such as `PGPASSWORD` in examples.

## Tests

- Add or update pytest cases under `test/` for parser behavior, validation, generated config, inventory rendering, and helper functions.
- Keep tests offline: do not call Terraform, Ansible, AWS, GCP, or a live PostgreSQL server from unit tests.
- Use `tmp_path`, `monkeypatch`, and simple fake outputs instead of writing persistent files outside pytest-managed directories.
- Run `python3 -m pytest test` after substantive changes when pytest is available.

## Subcommand Behavior

- `deploy` should create or update loader infrastructure only. It may generate Terraform and Ansible files, run `terraform apply`, and optionally run the setup playbook.
- `add-loaders` should only increase the existing stack's Terraform `node_count`, refresh inventory, and run setup for the newly added loaders unless the user provides a broader limit.
- `redeploy` should be Ansible-only: use the current `inventory.ini`, refresh Ansible playbooks from `ansible/`, and rerun setup without running Terraform.
- `initialize-db` should run `pgbench --initialize` against the provided PostgreSQL target and should default to a single loader unless the user chooses another Ansible limit.
- `run` should execute pgbench workloads against the provided PostgreSQL target and default to all loader nodes.
- `show` should be read-only with respect to cloud infrastructure and should report Terraform outputs and generated inventory.
- `destroy` should only destroy loader infrastructure managed by the generated Terraform stack.
- Preserve the contract that both `--vpc-id` and `--subnet-id` are required when using an existing network.
- Preserve provider defaults unless the user explicitly asks to change them: AWS uses `c7i.4xlarge`; GCP uses `c4-standard-16`.

## Infrastructure Assets

- Terraform source files live under `terraform/`; Ansible source playbooks live under `ansible/`.
- Do not reintroduce large Terraform or Ansible templates as inline Python strings.
- When adding deployment variables, update the relevant Terraform files, Python tfvars generation, README option docs, and tests together.
