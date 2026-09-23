"""SSH material handling for restic's sftp backend.

Key/known-hosts env values are materialised as files at runtime, and the
`-o sftp.command=...` string restic needs is derived from the repository URL
itself so the two can never drift.
"""

from __future__ import annotations

from pathlib import Path

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


def parse_sftp_user_host(repository: str) -> str:
    """Extract ``user@host`` from a ``sftp:user@host:path`` repository URL.

    Raises `ConfigError` for non-sftp repositories or malformed URLs.
    """
    if not repository.startswith("sftp:"):
        raise ConfigError(f"not an sftp repository: {repository!r}")
    remainder = repository[len("sftp:") :]
    user_host, sep, path = remainder.partition(":")
    if not sep or not path:
        raise ConfigError(f"sftp repository missing a :path suffix: {repository!r}")
    if "@" not in user_host:
        raise ConfigError(f"sftp repository missing user@host: {repository!r}")
    return user_host


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
    user_host = parse_sftp_user_host(repo.repository)
    key = materialize_key(repo.ssh_private_key, workdir / KEY_FILENAME)
    known_hosts = materialize_known_hosts(repo.ssh_known_hosts, workdir / KNOWN_HOSTS_FILENAME)
    return sftp_command(user_host, repo.ssh_port, key, known_hosts)
