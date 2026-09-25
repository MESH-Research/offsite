"""Environment-variable configuration parsing, built on `environs`.

`load_config()` reads the process environment directly; environs provides the
type conversions (`env.int`, `env.list`, `env.path`) and validation. All
environs errors are re-raised as `ConfigError`, whose message names the
offending variable. Tests isolate parsing by patching ``os.environ``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from environs import Env, EnvError
from marshmallow.validate import OneOf

from offsite_backup.errors import ConfigError

DEFAULT_RESTIC_HOST = "mesh-offsite"
DEFAULT_SSH_PORT = 23
# Daily checkpoints are a supported option but deliberately unused: 0 disables
# the flag entirely (restic rejects zero-valued --keep-* arguments).
DEFAULT_KEEP_DAILY = 0
DEFAULT_KEEP_WEEKLY = 4
DEFAULT_KEEP_MONTHLY = 6
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 48
DEFAULT_STAGING_BUDGET_BYTES = 170 * 2**30
DEFAULT_STAGING_DIR = Path("/mnt/staging")
DEFAULT_EFS_MOUNT_PATH = Path("/mnt/efs")
DEFAULT_REPLICA_TIMEOUT_SECONDS = 7200
DEFAULT_REPLICA_LAG_WAIT_SECONDS = 1800
DEFAULT_SMTP_PORT = 587

BUILTIN_COMPONENTS = ("ecs", "db", "efs", "s3")
RESERVED_SOURCE_NAMES = frozenset({*BUILTIN_COMPONENTS, "all"})
DB_ENGINES = ("mariadb", "postgres")

_MIRROR_KEY = re.compile(r"^RESTIC_MIRROR_(\d+)_")
_NTFY_KEY = re.compile(r"^NTFY_(\d+)_")


@dataclass(frozen=True)
class RepoConfig:
    """One restic repository (the primary or a mirror)."""

    repository: str
    password: str
    ssh_private_key: str | None = None
    ssh_known_hosts: str | None = None
    ssh_port: int = DEFAULT_SSH_PORT


@dataclass(frozen=True)
class DbSourceConfig:
    """One named RDS database source (e.g. wordpress, invenio, idms)."""

    name: str
    engine: str  # "mariadb" | "postgres"
    instance_id: str
    user: str
    password: str
    databases: tuple[str, ...]
    replica_class: str | None = None
    security_group_ids: tuple[str, ...] = ()
    parameter_group: str | None = None


@dataclass(frozen=True)
class S3Config:
    """S3 asset backup settings: bucket selection and staging limits."""

    buckets: tuple[str, ...] | None  # None = discover all, minus excludes
    exclude_buckets: tuple[str, ...]
    staging_budget_bytes: int
    staging_dir: Path


@dataclass(frozen=True)
class EfsConfig:
    """EFS backup settings: where the volume is mounted in the task."""

    mount_path: Path


@dataclass(frozen=True)
class EcsConfig:
    """ECS export settings: which clusters to describe."""

    clusters: tuple[str, ...] | None  # None = discover all


@dataclass(frozen=True)
class NtfyTarget:
    """One ntfy notifiee: a topic on a server, with optional Basic auth."""

    url: str
    topic: str
    user: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class EmailConfig:
    """One SMTP transport delivering error mail to any number of recipients."""

    to: tuple[str, ...]
    smtp_host: str
    smtp_from: str
    smtp_port: int = DEFAULT_SMTP_PORT
    smtp_user: str | None = None
    smtp_password: str | None = None


@dataclass(frozen=True)
class NotifyConfig:
    """All configured notifiees plus the optional dead-man ping URL."""

    ntfy: tuple[NtfyTarget, ...] = ()
    email: EmailConfig | None = None
    ping_url: str | None = None


@dataclass(frozen=True)
class Config:
    """The complete parsed configuration for one container run."""

    primary: RepoConfig
    mirrors: tuple[RepoConfig, ...]
    restic_host: str
    cache_dir: Path | None
    keep_daily: int
    keep_weekly: int
    keep_monthly: int
    max_snapshot_age_hours: int
    components: tuple[str, ...]
    db_sources: tuple[DbSourceConfig, ...]
    replica_timeout_s: int
    replica_lag_wait_s: int
    s3: S3Config
    efs: EfsConfig
    ecs: EcsConfig
    notify: NotifyConfig

    @property
    def repos(self) -> tuple[RepoConfig, ...]:
        """Return the primary repository followed by all mirrors."""
        return (self.primary, *self.mirrors)


def _names(env: Env, key: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in env.list(key, []) if item.strip())


def _optional_names(env: Env, key: str) -> tuple[str, ...] | None:
    return _names(env, key) or None


def _numbered_groups(pattern: re.Pattern[str], label: str) -> list[int]:
    """Return indices of numbered env groups, enforcing contiguous numbering from 1."""
    indices = sorted({int(m.group(1)) for key in os.environ if (m := pattern.match(key))})
    for position, n in enumerate(indices, start=1):
        if n != position:
            raise ConfigError(f"{label}_{n} is defined but {label}_{position} is missing")
    return indices


def _require_ssh(repository: str, key: str | None, known_hosts: str | None, prefix: str) -> None:
    if not repository.startswith("sftp:"):
        return
    if key is None:
        raise ConfigError(f"{prefix}SSH_PRIVATE_KEY is required for sftp repositories")
    if known_hosts is None:
        raise ConfigError(f"{prefix}SSH_KNOWN_HOSTS is required for sftp repositories")


def _load_primary(env: Env) -> RepoConfig:
    repo = RepoConfig(
        repository=env.str("RESTIC_REPOSITORY"),
        password=env.str("RESTIC_PASSWORD"),
        ssh_private_key=env.str("SSH_PRIVATE_KEY", None),
        ssh_known_hosts=env.str("SSH_KNOWN_HOSTS", None),
        ssh_port=env.int("SSH_PORT", DEFAULT_SSH_PORT),
    )
    _require_ssh(repo.repository, repo.ssh_private_key, repo.ssh_known_hosts, prefix="")
    return repo


def _load_mirrors(env: Env, primary: RepoConfig) -> tuple[RepoConfig, ...]:
    mirrors = []
    for n in _numbered_groups(_MIRROR_KEY, "RESTIC_MIRROR"):
        with env.prefixed(f"RESTIC_MIRROR_{n}_"):
            mirror = RepoConfig(
                repository=env.str("REPOSITORY"),
                password=env.str("PASSWORD"),
                ssh_private_key=env.str("SSH_PRIVATE_KEY", None) or primary.ssh_private_key,
                ssh_known_hosts=env.str("SSH_KNOWN_HOSTS", None) or primary.ssh_known_hosts,
                ssh_port=env.int("SSH_PORT", primary.ssh_port),
            )
        _require_ssh(
            mirror.repository,
            mirror.ssh_private_key,
            mirror.ssh_known_hosts,
            prefix=f"RESTIC_MIRROR_{n}_",
        )
        mirrors.append(mirror)
    return tuple(mirrors)


def _load_db_sources(env: Env) -> tuple[DbSourceConfig, ...]:
    sources = []
    for name in _names(env, "DB_SOURCES"):
        if name in RESERVED_SOURCE_NAMES:
            raise ConfigError(f"DB_SOURCES name {name!r} is reserved")
        with env.prefixed(f"DB_{name.upper()}_"):
            sources.append(
                DbSourceConfig(
                    name=name,
                    engine=env.str("ENGINE", validate=OneOf(DB_ENGINES)),
                    instance_id=env.str("INSTANCE_ID"),
                    user=env.str("USER"),
                    password=env.str("PASSWORD"),
                    databases=tuple(s.strip() for s in env.list("DATABASES") if s.strip()),
                    replica_class=env.str("REPLICA_CLASS", None),
                    security_group_ids=_names(env, "SECURITY_GROUP_IDS"),
                    parameter_group=env.str("PARAMETER_GROUP", None),
                )
            )
    return tuple(sources)


def _load_components(env: Env, db_sources: tuple[DbSourceConfig, ...]) -> tuple[str, ...]:
    components = _names(env, "COMPONENTS")
    if not components:
        return BUILTIN_COMPONENTS
    allowed = set(BUILTIN_COMPONENTS) | {source.name for source in db_sources}
    for component in components:
        if component not in allowed:
            raise ConfigError(f"COMPONENTS contains unknown target {component!r}")
    return components


def _load_ntfy(env: Env) -> tuple[NtfyTarget, ...]:
    targets = []
    for n in _numbered_groups(_NTFY_KEY, "NTFY"):
        with env.prefixed(f"NTFY_{n}_"):
            target = NtfyTarget(
                url=env.str("URL"),
                topic=env.str("TOPIC"),
                user=env.str("USER", None),
                password=env.str("PASSWORD", None),
            )
        # ntfy Basic auth needs both halves; one without the other is a
        # misconfiguration, not an unauthenticated target.
        if target.user and not target.password:
            raise ConfigError(f"NTFY_{n}_PASSWORD is required when NTFY_{n}_USER is set")
        if target.password and not target.user:
            raise ConfigError(f"NTFY_{n}_USER is required when NTFY_{n}_PASSWORD is set")
        targets.append(target)
    return tuple(targets)


def _load_email(env: Env) -> EmailConfig | None:
    to = _names(env, "EMAIL_TO")
    if not to:
        return None
    return EmailConfig(
        to=to,
        smtp_host=env.str("SMTP_HOST"),
        smtp_from=env.str("SMTP_FROM"),
        smtp_port=env.int("SMTP_PORT", DEFAULT_SMTP_PORT),
        smtp_user=env.str("SMTP_USER", None),
        smtp_password=env.str("SMTP_PASSWORD", None),
    )


def load_config(env: Env | None = None) -> Config:
    """Parse a full `Config` from the process environment.

    `env` may be a pre-built environs `Env` — typically one that has already
    read a ``.env`` file (environs keeps such values inside the instance
    rather than in ``os.environ``). Defaults to a fresh `Env` reading only the
    process environment. Raises `ConfigError` naming the offending variable
    when a required value is missing or invalid.
    """
    env = env if env is not None else Env()
    try:
        primary = _load_primary(env)
        db_sources = _load_db_sources(env)
        return Config(
            primary=primary,
            mirrors=_load_mirrors(env, primary),
            restic_host=env.str("RESTIC_HOST", DEFAULT_RESTIC_HOST),
            cache_dir=env.path("RESTIC_CACHE_DIR", None),
            keep_daily=env.int("KEEP_DAILY", DEFAULT_KEEP_DAILY),
            keep_weekly=env.int("KEEP_WEEKLY", DEFAULT_KEEP_WEEKLY),
            keep_monthly=env.int("KEEP_MONTHLY", DEFAULT_KEEP_MONTHLY),
            max_snapshot_age_hours=env.int(
                "MAX_SNAPSHOT_AGE_HOURS", DEFAULT_MAX_SNAPSHOT_AGE_HOURS
            ),
            components=_load_components(env, db_sources),
            db_sources=db_sources,
            replica_timeout_s=env.int("REPLICA_TIMEOUT_SECONDS", DEFAULT_REPLICA_TIMEOUT_SECONDS),
            replica_lag_wait_s=env.int(
                "REPLICA_LAG_WAIT_SECONDS", DEFAULT_REPLICA_LAG_WAIT_SECONDS
            ),
            s3=S3Config(
                buckets=_optional_names(env, "S3_BUCKETS"),
                exclude_buckets=_names(env, "S3_EXCLUDE_BUCKETS"),
                staging_budget_bytes=env.int(
                    "STAGING_BUDGET_BYTES", DEFAULT_STAGING_BUDGET_BYTES
                ),
                staging_dir=env.path("STAGING_DIR", DEFAULT_STAGING_DIR),
            ),
            efs=EfsConfig(mount_path=env.path("EFS_MOUNT_PATH", DEFAULT_EFS_MOUNT_PATH)),
            ecs=EcsConfig(clusters=_optional_names(env, "ECS_CLUSTERS")),
            notify=NotifyConfig(
                ntfy=_load_ntfy(env),
                email=_load_email(env),
                ping_url=env.str("PING_URL", None),
            ),
        )
    except EnvError as exc:
        raise ConfigError(str(exc)) from exc
