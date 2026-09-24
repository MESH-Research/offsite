# offsite-backup

Portable offsite backups of the Knowledge Commons AWS estate to Hetzner Storage
Boxes, in open, non-AWS-specific formats, using [restic](https://restic.net/).

One Docker image, configured entirely by environment variables, run as ECS
Fargate scheduled tasks inside the VPC. It backs up:

- **RDS databases** — MariaDB (WordPress) and Postgres (InvenioRDM,
  Profiles/IDMS): a temporary read replica is created, dumped as plain SQL
  streamed straight into restic, then destroyed. The live site is never
  touched.
- **EFS** — the volume is mounted read-only into the task and snapshotted.
- **S3 assets** — staged to ephemeral storage with rclone in bounded batches,
  then snapshotted.
- **ECS task definitions** — exported as JSON.

Snapshots land in one or more restic repositories over SFTP. Retention keeps
weekly snapshots for 4 weeks, and monthly checkpoints for six
months (`--keep-weekly 4 --keep-monthly 6`), rolling over
automatically. Weekly `restic check` and monthly read-data verification guard
the repositories; ntfy and email report every run.

## Usage

```sh
python -m offsite_backup backup            # entire suite
python -m offsite_backup backup efs idms   # individual targets
python -m offsite_backup verify
python -m offsite_backup restore db wordpress --at 1w --yes
```

Documentation of the environment-variable contract, deployment (task
definitions, IAM policies, EventBridge schedules), and the restore runbook
will live in `infra/README.md`. Design questions and the reasoning behind
them are answered in [docs/FAQ.md](docs/FAQ.md); the full environment
contract is documented in [docs/environment.md](docs/environment.md) and,
in copy-paste form, in [.env.example](.env.example).

## Development

```sh
uv sync
uv run pytest
uv run ruff check
```
