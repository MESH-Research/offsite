"""SSH material handling for restic's sftp backend.

Key/known-hosts env values are materialised as files at runtime, and the
`-o sftp.command=...` string restic needs is derived from the repository URL
itself so the two can never drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from offsite_backup.config import RepoConfig
from offsite_backup.errors import ConfigError

KEY_FILENAME = "id_offsite"
KNOWN_HOSTS_FILENAME = "known_hosts"


def _write(content: str, dest: Path, mode: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content.rstrip("\n") + "\n")
    dest.chmod(mode)
    return dest


def materialize_key(material: str, dest: Path) -> Path:
    """Write private-key material to `dest` with 0600 permissions.

    Ensures exactly one trailing newline (OpenSSH rejects keys without one).
    """
    return _write(material, dest, 0o600)


def materialize_known_hosts(entries: str, dest: Path) -> Path:
    """Write known-hosts entries to `dest`, ensuring a trailing newline."""
    return _write(entries, dest, 0o600)


@dataclass(frozen=True)
class SftpTarget:
    """Connection details parsed from an sftp repository string."""

    user_host: str
    port: int | None


def parse_sftp_repository(repository: str) -> SftpTarget:
    """Parse either sftp repository form restic accepts.

    ``sftp://user@host[:port]/path`` is a true URL; a port given there takes
    precedence over any separately configured SSH port. ``sftp:user@host:path``
    is the scp-like form, which is not a URL and carries no port.

    Raises `ConfigError` for non-sftp repositories or malformed values.
    """
    if repository.startswith("sftp://"):
        parts = urlsplit(repository)
        try:
            port = parts.port
        except ValueError as exc:
            raise ConfigError(f"sftp repository has an invalid port: {repository!r}") from exc
        if not parts.username or not parts.hostname:
            raise ConfigError(f"sftp repository missing user@host: {repository!r}")
        if parts.path in ("", "/"):
            raise ConfigError(f"sftp repository missing a path: {repository!r}")
        return SftpTarget(user_host=f"{parts.username}@{parts.hostname}", port=port)
    if not repository.startswith("sftp:"):
        raise ConfigError(f"not an sftp repository: {repository!r}")
    # The scp-like form is not a URL: urlsplit returns an empty netloc and dumps
    # `user@host:path` into .path, so it has to be split by hand.
    remainder = repository[len("sftp:") :]
    user_host, sep, path = remainder.partition(":")
    if not sep or not path:
        raise ConfigError(f"sftp repository missing a :path suffix: {repository!r}")
    if "@" not in user_host:
        raise ConfigError(f"sftp repository missing user@host: {repository!r}")
    return SftpTarget(user_host=user_host, port=None)


def sftp_command(user_host: str, port: int, key: Path, known_hosts: Path) -> str:
    """Render the ssh command restic should use for the sftp backend."""
    return (
        f"ssh -p {port} -i {key} -o UserKnownHostsFile={known_hosts} "
        f"-o StrictHostKeyChecking=yes {user_host} -s sftp"
    )


def prepare_ssh(repo: RepoConfig, workdir: Path) -> str | None:
    """Materialise `repo`'s SSH files under `workdir` and return the sftp command.

    Returns None for non-sftp repositories (nothing is written).
    """
    if not repo.repository.startswith("sftp:"):
        return None
    if repo.ssh_private_key is None or repo.ssh_known_hosts is None:
        raise ConfigError(f"sftp repository {repo.repository!r} has no SSH material")
    target = parse_sftp_repository(repo.repository)
    port = target.port if target.port is not None else repo.ssh_port
    key = materialize_key(repo.ssh_private_key, workdir / KEY_FILENAME)
    known_hosts = materialize_known_hosts(repo.ssh_known_hosts, workdir / KNOWN_HOSTS_FILENAME)
    return sftp_command(target.user_host, port, key, known_hosts)
