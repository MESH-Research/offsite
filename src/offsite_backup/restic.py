"""Thin wrapper around the restic CLI.

One `Restic` instance per repository; every method builds an argv list and
delegates to the injected `Runner`. Repository location and password travel
via the child environment (never argv); JSON output parsing lives here and
nowhere else.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from offsite_backup.config import RepoConfig
from offsite_backup.errors import CommandError, ConfigError
from offsite_backup.proc import Runner, run

# restic emits RFC3339 timestamps with nanosecond precision; fromisoformat
# accepts at most microseconds, so surplus fractional digits are trimmed.
_EXCESS_FRACTION = re.compile(r"\.(\d{6})\d+")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(_EXCESS_FRACTION.sub(r".\1", value))


@dataclass(frozen=True)
class Snapshot:
    """One snapshot record as reported by ``restic snapshots --json``."""

    id: str
    short_id: str
    time: datetime
    tags: tuple[str, ...]
    paths: tuple[str, ...]
    hostname: str


class Restic:
    """Command builder/executor for a single restic repository."""

    def __init__(
        self,
        repo: RepoConfig,
        *,
        host: str,
        cache_dir: Path | None = None,
        ssh_home: Path | None = None,
        runner: Runner = run,
    ) -> None:
        """Bind the wrapper to `repo`.

        `host` is the stable snapshot source label (``--host``). `ssh_home`,
        when given, is exported as ``$HOME`` so ssh picks up the client config
        written by `prepare_ssh_home`. `cache_dir` sets ``RESTIC_CACHE_DIR``.
        """
        self.repo = repo
        self.host = host
        self._cache_dir = cache_dir
        self._ssh_home = ssh_home
        self._runner = runner

    def _env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = {
            "RESTIC_REPOSITORY": self.repo.repository,
            "RESTIC_PASSWORD": self.repo.password,
        }
        if self._cache_dir is not None:
            env["RESTIC_CACHE_DIR"] = str(self._cache_dir)
        if self._ssh_home is not None:
            env["HOME"] = str(self._ssh_home)
        if extra:
            env.update(extra)
        return env

    def _run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        extra_env: Mapping[str, str] | None = None,
    ):
        return self._runner(
            ["restic", "--json", *args], env=self._env(extra_env), check=check
        )

    def _backup(
        self,
        args: Sequence[str],
        tags: Sequence[str],
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        cmd = ["backup", "--host", self.host]
        for tag in tags:
            cmd += ["--tag", tag]
        cmd += list(args)
        result = self._run(cmd, extra_env=extra_env)
        for line in result.stdout.splitlines():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("message_type") == "summary":
                return message["snapshot_id"]
        raise CommandError(
            ["restic", *cmd], result.returncode, "restic backup produced no summary line"
        )

    def ensure_repo(self) -> None:
        """Initialise the repository unless it already exists."""
        if self._run(["cat", "config"], check=False).returncode != 0:
            self._run(["init"])

    def ensure_repo_from(self, source: RepoConfig) -> None:
        """Initialise as a mirror of `source` (shared chunker parameters).

        Matching chunker parameters are what make deduplication survive
        ``restic copy`` between the repositories. No-op when the repository
        already exists.
        """
        if self._run(["cat", "config"], check=False).returncode != 0:
            self._run(
                ["init", "--from-repo", source.repository, "--copy-chunker-params"],
                extra_env={"RESTIC_FROM_PASSWORD": source.password},
            )

    def backup_path(self, path: Path, tags: Sequence[str]) -> str:
        """Back up a directory tree; return the new snapshot id."""
        return self._backup([str(path)], tags)

    def backup_stdin_command(
        self,
        command: Sequence[str],
        filename: str,
        tags: Sequence[str],
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        """Stream `command`'s stdout into a snapshot as `filename`.

        restic runs the command itself (``--stdin-from-command``) and fails
        the snapshot if it exits non-zero — unlike a shell pipe, which would
        silently truncate. Returns the new snapshot id.
        """
        args = ["--stdin-from-command", "--stdin-filename", filename, "--", *command]
        return self._backup(args, tags, extra_env=extra_env)

    def snapshots(
        self, tags: Sequence[str] = (), latest: int | None = None
    ) -> list[Snapshot]:
        """List snapshots, optionally filtered by tags / limited to latest N."""
        cmd = ["snapshots"]
        for tag in tags:
            cmd += ["--tag", tag]
        if latest is not None:
            cmd += ["--latest", str(latest)]
        result = self._run(cmd)
        return [
            Snapshot(
                id=record["id"],
                short_id=record["short_id"],
                time=_parse_time(record["time"]),
                tags=tuple(record.get("tags") or ()),
                paths=tuple(record.get("paths") or ()),
                hostname=record.get("hostname", ""),
            )
            for record in json.loads(result.stdout or "[]")
        ]

    def forget(
        self,
        *,
        keep_daily: int,
        keep_weekly: int,
        keep_monthly: int,
        group_by: str = "tags",
    ) -> None:
        """Apply the retention policy (never prunes).

        Zero-valued keep arguments are omitted (restic rejects 0); a policy
        with nothing to keep raises `ConfigError` rather than forgetting
        everything.
        """
        keeps = []
        for flag, value in (
            ("--keep-daily", keep_daily),
            ("--keep-weekly", keep_weekly),
            ("--keep-monthly", keep_monthly),
        ):
            if value:
                keeps += [flag, str(value)]
        if not keeps:
            raise ConfigError("retention policy keeps nothing; refusing to forget")
        self._run(["forget", "--group-by", group_by, *keeps])

    def prune(self) -> None:
        """Remove unreferenced data from the repository."""
        self._run(["prune"])

    def check(self, read_data_subset: str | None = None) -> None:
        """Verify repository integrity, optionally re-reading a data subset."""
        cmd = ["check"]
        if read_data_subset is not None:
            cmd += ["--read-data-subset", read_data_subset]
        self._run(cmd)

    def unlock(self) -> None:
        """Remove stale locks left behind by killed runs."""
        self._run(["unlock"])

    def copy_from(self, source: RepoConfig, snapshot_ids: Sequence[str]) -> None:
        """Copy the given snapshots from `source` into this repository."""
        self._run(
            ["copy", "--from-repo", source.repository, *snapshot_ids],
            extra_env={"RESTIC_FROM_PASSWORD": source.password},
        )
