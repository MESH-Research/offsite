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
daily snapshots for a week plus 1-week, 2-week, and monthly checkpoints for six
months (`--keep-daily 7 --keep-weekly 4 --keep-monthly 6`), rolling over
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
lives in `infra/README.md`.

## Development

```sh
uv sync
uv run pytest
uv run ruff check
```
