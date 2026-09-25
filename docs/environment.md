# Environment variable reference

Every variable the container reads, with defaults and purpose. Alphabetical
within each section. `<SRC>` is an uppercased name from `DB_SOURCES`
(e.g. `wordpress` → `DB_WORDPRESS_*`); `<N>` is a group number counting from
1 with no gaps.

AWS credentials are deliberately absent: the container uses the standard
boto3 credential chain (ECS task role in production; `AWS_PROFILE`/`AWS_*`
variables locally — set `AWS_REGION` if no default region is configured).

## Mandatory

| Variable | Function |
|---|---|
| `RESTIC_PASSWORD` | Password for the primary restic repository. |
| `RESTIC_REPOSITORY` | Primary repository location. Either restic form: `sftp:user@host:path` (scp-like) or `sftp://user@host[:port]/path` (URL; an embedded port overrides `SSH_PORT`). `local:/path` works for smoke testing. |

## Mandatory when a feature is enabled

| Variable | Required when | Function |
|---|---|---|
| `DB_<SRC>_DATABASES` | source listed in `DB_SOURCES` | Comma-separated database names to dump; one snapshot per database. |
| `DB_<SRC>_ENGINE` | source listed in `DB_SOURCES` | `mariadb` or `postgres`; selects dump tooling and replica handling. |
| `DB_<SRC>_INSTANCE_ID` | source listed in `DB_SOURCES` | RDS instance identifier of the production database to replicate. |
| `DB_<SRC>_PASSWORD` | source listed in `DB_SOURCES` | Database password (passed via `MYSQL_PWD`/`PGPASSWORD`, never argv). |
| `DB_<SRC>_USER` | source listed in `DB_SOURCES` | Database user with dump privileges. |
| `NTFY_<N>_PASSWORD` | `NTFY_<N>_USER` is set | HTTP Basic auth password. Username and password must be set together. |
| `NTFY_<N>_TOPIC` | ntfy target `<N>` defined | Topic the notification is posted to. |
| `NTFY_<N>_URL` | ntfy target `<N>` defined | Base URL of the ntfy server. |
| `NTFY_<N>_USER` | `NTFY_<N>_PASSWORD` is set | HTTP Basic auth username. Username and password must be set together; omit both only for a public, unauthenticated topic. |
| `RESTIC_MIRROR_<N>_PASSWORD` | mirror `<N>` defined | Password for the mirror repository. |
| `RESTIC_MIRROR_<N>_REPOSITORY` | mirror `<N>` defined | Mirror repository location (defines the mirror; same forms as `RESTIC_REPOSITORY`). |
| `RESTIC_MIRROR_<N>_SSH_KNOWN_HOSTS` | mirror is sftp and the primary has no material to inherit | Pinned host key for the mirror's host. |
| `RESTIC_MIRROR_<N>_SSH_PRIVATE_KEY` | mirror is sftp and the primary has no material to inherit | Private key for the mirror's host. |
| `SMTP_FROM` | `EMAIL_TO` is set | From address for error mail. |
| `SMTP_HOST` | `EMAIL_TO` is set | SMTP server used to deliver error mail. |
| `SSH_KNOWN_HOSTS` | `RESTIC_REPOSITORY` is sftp | Pinned host key entry/entries for the primary repository host (`StrictHostKeyChecking yes`). |
| `SSH_PRIVATE_KEY` | `RESTIC_REPOSITORY` is sftp | Private key (PEM, multiline) for the primary repository host. |

## Optional

| Variable | Default | Function |
|---|---|---|
| `COMPONENTS` | `ecs,db,efs,s3` | Default target set for `backup` with no arguments; may include DB source names. |
| `DB_SOURCES` | *(empty — no databases)* | Comma-separated database source names; each needs a `DB_<SRC>_*` group and becomes a CLI target (`backup idms`). Reserved names: `db`, `ecs`, `efs`, `s3`, `all`. |
| `DB_<SRC>_PARAMETER_GROUP` | *(unset)* | Postgres only: DB parameter group for the replica, raising `max_standby_streaming_delay` so replication replay cannot cancel the dump. |
| `DB_<SRC>_REPLICA_CLASS` | *(source's class)* | Instance class for the temporary read replica. |
| `DB_<SRC>_SECURITY_GROUP_IDS` | *(empty)* | Security groups attached to the replica so the backup task can reach it. |
| `ECS_CLUSTERS` | *(discover all)* | Comma-separated ECS clusters whose task definitions/services are exported. |
| `EFS_MOUNT_PATH` | `/mnt/efs` | Where the EFS volume is mounted (read-only) inside the task. |
| `EMAIL_TO` | *(empty — email disabled)* | Comma-separated recipients for error mail. |
| `ENV_FILE` | *(unset — environs' default search)* | Path to a `.env` file to load before parsing; must exist if set. Read from the real environment only (it can't come from the file it names); existing environment values are never overridden. |
| `KEEP_DAILY` | `0` *(disabled)* | Daily checkpoints for `restic forget`. Supported but deliberately unused; `0` omits the flag. |
| `KEEP_MONTHLY` | `6` | Month-end checkpoints retained (see [FAQ](FAQ.md) for the exact keep-set). |
| `KEEP_WEEKLY` | `4` | Week-end checkpoints retained. |
| `MAX_SNAPSHOT_AGE_HOURS` | `48` | `verify` fails if any required tag's newest snapshot is older than this. |
| `PING_URL` | *(unset)* | Dead-man switch: pinged on success, `<url>/fail` on failure. |
| `REPLICA_LAG_WAIT_SECONDS` | `1800` | Longest wait for a replica to catch up before dumping. |
| `REPLICA_TIMEOUT_SECONDS` | `7200` | Longest wait for a replica to become available. |
| `RESTIC_CACHE_DIR` | *(restic default)* | Restic metadata cache; point at ephemeral storage in ECS. |
| `RESTIC_HOST` | `mesh-offsite` | Stable snapshot **source** label (`--host`); names the FROM side, never a storage server — see [FAQ](FAQ.md). |
| `RESTIC_MIRROR_<N>_SSH_PORT` | *(primary's `SSH_PORT`)* | SSH port for the mirror's host. |
| `S3_BUCKETS` | *(discover all)* | Comma-separated buckets to back up; unset discovers every bucket minus excludes. |
| `S3_EXCLUDE_BUCKETS` | *(empty)* | Buckets to skip during discovery. |
| `SMTP_PASSWORD` | *(no auth)* | SMTP password. |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS). |
| `SMTP_USER` | *(no auth)* | SMTP user. |
| `SSH_PORT` | `23` | SSH port for the primary repository host (Hetzner Storage Boxes use 23). A port embedded in an `sftp://` URL wins. |
| `STAGING_BUDGET_BYTES` | `182536110080` *(170 GiB)* | Upper bound for S3 staging; must fit the task's ephemeral storage. Buckets above it are batched by prefix. |
| `STAGING_DIR` | `/mnt/staging` | Staging area for S3 bucket syncs. |
