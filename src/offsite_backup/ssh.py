"""SSH client configuration for restic's sftp backend.

Instead of pinning a single ``-o sftp.command=...`` (which is global per
restic invocation and therefore breaks ``restic copy`` between two sftp
repositories on different hosts), we materialise an OpenSSH client config
under a private ``$HOME``: one ``Match host ... user ...`` block per
repository selects the right identity file and port, and restic's default
ssh invocation does the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from offsite_backup.config import RepoConfig
from offsite_backup.errors import ConfigError


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


def prepare_ssh_home(repos: Sequence[RepoConfig], home: Path) -> Path:
    """Materialise SSH client config for every sftp repo under ``home``/.ssh.

    Writes one identity file per sftp repository, an aggregated known_hosts,
    and a config whose ``Match host ... user ...`` blocks select the right
    identity and port per connection — including when a single ``restic copy``
    talks to two different sftp hosts. Repositories sharing host and user are
    written once. Non-sftp repositories are skipped entirely; when no sftp
    repository is present nothing is written.

    Returns ``home``, which callers set as ``$HOME`` for restic subprocesses.
    """
    sftp_repos = [r for r in repos if r.repository.startswith("sftp:")]
    if not sftp_repos:
        return home

    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    known_hosts = ssh_dir / "known_hosts"

    blocks: list[str] = []
    kh_entries: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, repo in enumerate(sftp_repos, start=1):
        if repo.ssh_private_key is None or repo.ssh_known_hosts is None:
            raise ConfigError(f"sftp repository {repo.repository!r} has no SSH material")
        target = parse_sftp_repository(repo.repository)
        user, _, hostname = target.user_host.partition("@")
        if (hostname, user) in seen:
            continue
        seen.add((hostname, user))

        identity = _write(repo.ssh_private_key, ssh_dir / f"id_{index}", 0o600)
        entry = repo.ssh_known_hosts.rstrip("\n")
        if entry not in kh_entries:
            kh_entries.append(entry)
        port = target.port if target.port is not None else repo.ssh_port
        blocks.append(
            f"Match host {hostname} user {user}\n  IdentityFile {identity}\n  Port {port}\n"
        )

    defaults = (
        "Host *\n"
        "  IdentitiesOnly yes\n"
        "  StrictHostKeyChecking yes\n"
        f"  UserKnownHostsFile {known_hosts}\n"
    )
    _write("\n".join([defaults, *blocks]), ssh_dir / "config", 0o600)
    _write("\n".join(kh_entries), known_hosts, 0o600)
    return home


def _write(content: str, dest: Path, mode: int) -> Path:
    dest.write_text(content.rstrip("\n") + "\n")
    dest.chmod(mode)
    return dest
