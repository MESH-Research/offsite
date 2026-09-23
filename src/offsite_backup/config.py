"""Environment-variable configuration parsing.

`load_config` is the only entry point; it never reads ``os.environ`` itself —
the CLI passes the environment mapping in, which keeps parsing pure and
testable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from offsite_backup.errors import ConfigError

DEFAULT_RESTIC_HOST = "mesh-offsite"
DEFAULT_SSH_PORT = 23
DEFAULT_KEEP_WEEKLY = 4
DEFAULT_KEEP_MONTHLY = 6
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 48
DEFAULT_STAGING_BUDGET_BYTES = 170 * 2**30
DEFAULT_STAGING_DIR = "/mnt/staging"
DEFAULT_EFS_MOUNT_PATH = "/mnt/efs"
DEFAULT_REPLICA_TIMEOUT_SECONDS = 7200
DEFAULT_REPLICA_LAG_WAIT_SECONDS = 1800
DEFAULT_SMTP_PORT = 587

BUILTIN_COMPONENTS = ("ecs", "db", "efs", "s3")
RESERVED_SOURCE_NAMES = frozenset({*BUILTIN_COMPONENTS, "all"})
DB_ENGINES = frozenset({"mariadb", "postgres"})

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
    buckets: tuple[str, ...] | None  # None = discover all, minus excludes
    exclude_buckets: tuple[str, ...]
    staging_budget_bytes: int
    staging_dir: Path


@dataclass(frozen=True)
class EfsConfig:
    mount_path: Path


@dataclass(frozen=True)
class EcsConfig:
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
    ntfy: tuple[NtfyTarget, ...] = ()
    email: EmailConfig | None = None
    ping_url: str | None = None


@dataclass(frozen=True)
class Config:
    primary: RepoConfig
    mirrors: tuple[RepoConfig, ...]
    restic_host: str
    cache_dir: Path | None
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
        return (self.primary, *self.mirrors)


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigError(f"{key} is required")
    return value


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _split_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _load_primary(env: Mapping[str, str]) -> RepoConfig:
    repository = _require(env, "RESTIC_REPOSITORY")
    password = _require(env, "RESTIC_PASSWORD")
    ssh_port = _get_int(env, "SSH_PORT", DEFAULT_SSH_PORT)
    key = env.get("SSH_PRIVATE_KEY") or None
    known_hosts = env.get("SSH_KNOWN_HOSTS") or None
    if repository.startswith("sftp:"):
        if key is None:
            raise ConfigError("SSH_PRIVATE_KEY is required for sftp repositories")
        if known_hosts is None:
            raise ConfigError("SSH_KNOWN_HOSTS is required for sftp repositories")
    return RepoConfig(
        repository=repository,
        password=password,
        ssh_private_key=key,
        ssh_known_hosts=known_hosts,
        ssh_port=ssh_port,
    )


def _load_mirrors(env: Mapping[str, str], primary: RepoConfig) -> tuple[RepoConfig, ...]:
    indices = {int(m.group(1)) for key in env if (m := _MIRROR_KEY.match(key))}
    mirrors = []
    for n in sorted(indices):
        if n != len(mirrors) + 1:
            raise ConfigError(
                f"RESTIC_MIRROR_{n} is defined but RESTIC_MIRROR_{len(mirrors) + 1} is missing"
            )
        prefix = f"RESTIC_MIRROR_{n}_"
        repository = _require(env, prefix + "REPOSITORY")
        password = _require(env, prefix + "PASSWORD")
        key = env.get(prefix + "SSH_PRIVATE_KEY") or primary.ssh_private_key
        known_hosts = env.get(prefix + "SSH_KNOWN_HOSTS") or primary.ssh_known_hosts
        ssh_port = _get_int(env, prefix + "SSH_PORT", primary.ssh_port)
        if repository.startswith("sftp:"):
            if key is None:
                raise ConfigError(f"{prefix}SSH_PRIVATE_KEY is required for sftp repositories")
            if known_hosts is None:
                raise ConfigError(f"{prefix}SSH_KNOWN_HOSTS is required for sftp repositories")
        mirrors.append(
            RepoConfig(
                repository=repository,
                password=password,
                ssh_private_key=key,
                ssh_known_hosts=known_hosts,
                ssh_port=ssh_port,
            )
        )
    return tuple(mirrors)


def _load_db_sources(env: Mapping[str, str]) -> tuple[DbSourceConfig, ...]:
    sources = []
    for name in _split_list(env.get("DB_SOURCES", "")):
        if name in RESERVED_SOURCE_NAMES:
            raise ConfigError(f"DB_SOURCES name {name!r} is reserved")
        prefix = f"DB_{name.upper()}_"
        engine = _require(env, prefix + "ENGINE")
        if engine not in DB_ENGINES:
            raise ConfigError(
                f"{prefix}ENGINE must be one of {sorted(DB_ENGINES)}, got {engine!r}"
            )
        sources.append(
            DbSourceConfig(
                name=name,
                engine=engine,
                instance_id=_require(env, prefix + "INSTANCE_ID"),
                user=_require(env, prefix + "USER"),
                password=_require(env, prefix + "PASSWORD"),
                databases=_split_list(_require(env, prefix + "DATABASES")),
                replica_class=env.get(prefix + "REPLICA_CLASS") or None,
                security_group_ids=_split_list(env.get(prefix + "SECURITY_GROUP_IDS", "")),
                parameter_group=env.get(prefix + "PARAMETER_GROUP") or None,
            )
        )
    return tuple(sources)


def _load_components(
    env: Mapping[str, str], db_sources: tuple[DbSourceConfig, ...]
) -> tuple[str, ...]:
    raw = env.get("COMPONENTS", "").strip()
    if not raw:
        return BUILTIN_COMPONENTS
    components = _split_list(raw)
    allowed = set(BUILTIN_COMPONENTS) | {source.name for source in db_sources}
    for component in components:
        if component not in allowed:
            raise ConfigError(f"COMPONENTS contains unknown target {component!r}")
    return components


def _load_ntfy_targets(env: Mapping[str, str]) -> tuple[NtfyTarget, ...]:
    indices = {int(m.group(1)) for key in env if (m := _NTFY_KEY.match(key))}
    targets = []
    for n in sorted(indices):
        if n != len(targets) + 1:
            raise ConfigError(f"NTFY_{n} is defined but NTFY_{len(targets) + 1} is missing")
        prefix = f"NTFY_{n}_"
        user = env.get(prefix + "USER") or None
        password = env.get(prefix + "PASSWORD") or None
        if user and not password:
            raise ConfigError(f"{prefix}PASSWORD is required when {prefix}USER is set")
        targets.append(
            NtfyTarget(
                url=_require(env, prefix + "URL"),
                topic=_require(env, prefix + "TOPIC"),
                user=user,
                password=password,
            )
        )
    return tuple(targets)


def _load_email(env: Mapping[str, str]) -> EmailConfig | None:
    to = _split_list(env.get("EMAIL_TO", ""))
    if not to:
        return None
    return EmailConfig(
        to=to,
        smtp_host=_require(env, "SMTP_HOST"),
        smtp_from=_require(env, "SMTP_FROM"),
        smtp_port=_get_int(env, "SMTP_PORT", DEFAULT_SMTP_PORT),
        smtp_user=env.get("SMTP_USER") or None,
        smtp_password=env.get("SMTP_PASSWORD") or None,
    )


def _load_notify(env: Mapping[str, str]) -> NotifyConfig:
    return NotifyConfig(
        ntfy=_load_ntfy_targets(env),
        email=_load_email(env),
        ping_url=env.get("PING_URL") or None,
    )


def load_config(env: Mapping[str, str]) -> Config:
    """Parse a full `Config` from an environment mapping.

    Raises `ConfigError` naming the offending variable when a required value
    is missing or invalid.
    """
    primary = _load_primary(env)
    db_sources = _load_db_sources(env)
    cache_dir = env.get("RESTIC_CACHE_DIR") or None
    s3_buckets_raw = env.get("S3_BUCKETS", "").strip()
    ecs_clusters_raw = env.get("ECS_CLUSTERS", "").strip()
    return Config(
        primary=primary,
        mirrors=_load_mirrors(env, primary),
        restic_host=env.get("RESTIC_HOST") or DEFAULT_RESTIC_HOST,
        cache_dir=Path(cache_dir) if cache_dir else None,
        keep_weekly=_get_int(env, "KEEP_WEEKLY", DEFAULT_KEEP_WEEKLY),
        keep_monthly=_get_int(env, "KEEP_MONTHLY", DEFAULT_KEEP_MONTHLY),
        max_snapshot_age_hours=_get_int(
            env, "MAX_SNAPSHOT_AGE_HOURS", DEFAULT_MAX_SNAPSHOT_AGE_HOURS
        ),
        components=_load_components(env, db_sources),
        db_sources=db_sources,
        replica_timeout_s=_get_int(env, "REPLICA_TIMEOUT_SECONDS", DEFAULT_REPLICA_TIMEOUT_SECONDS),
        replica_lag_wait_s=_get_int(
            env, "REPLICA_LAG_WAIT_SECONDS", DEFAULT_REPLICA_LAG_WAIT_SECONDS
        ),
        s3=S3Config(
            buckets=_split_list(s3_buckets_raw) if s3_buckets_raw else None,
            exclude_buckets=_split_list(env.get("S3_EXCLUDE_BUCKETS", "")),
            staging_budget_bytes=_get_int(
                env, "STAGING_BUDGET_BYTES", DEFAULT_STAGING_BUDGET_BYTES
            ),
            staging_dir=Path(env.get("STAGING_DIR") or DEFAULT_STAGING_DIR),
        ),
        efs=EfsConfig(mount_path=Path(env.get("EFS_MOUNT_PATH") or DEFAULT_EFS_MOUNT_PATH)),
        ecs=EcsConfig(clusters=_split_list(ecs_clusters_raw) if ecs_clusters_raw else None),
        notify=_load_notify(env),
    )
