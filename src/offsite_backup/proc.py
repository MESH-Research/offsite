"""The only place subprocesses are spawned.

Everything else builds argv lists and delegates to a `Runner`; production code
uses `run`, tests inject fakes. No ``shell=True`` anywhere in this codebase.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from offsite_backup.errors import CommandError

log = logging.getLogger(__name__)


@dataclass
class ProcResult:
    """Captured outcome of a finished subprocess.

    `stdout` and `stderr` are decoded text. When a command is run with
    ``check=False``, a non-zero `returncode` is reported here instead of
    raising `CommandError`.
    """

    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    """The callable seam through which every external command executes.

    Production code passes `run`; tests inject a fake that matches on argv
    and returns canned `ProcResult`s (or raises `CommandError`). Anything that
    shells out — restic, rclone, mariadb-dump, pg_dump, ssh — takes a `Runner`
    parameter rather than spawning processes itself, so behaviour can be
    tested without touching the system.
    """

    def __call__(
        self,
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> ProcResult:
        """Execute `cmd` and return its captured outcome; see `run`."""
        ...


def run(
    cmd: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> ProcResult:
    """Run `cmd`, capturing text output.

    `env` entries are overlaid on the current process environment (they never
    replace it wholesale, so PATH etc. survive). With `check`, a non-zero exit
    or a timeout raises `CommandError`; timeouts use returncode -1.
    """
    argv = list(cmd)
    merged_env = {**os.environ, **env} if env else None
    log.debug("running: %s", " ".join(argv))
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, env=merged_env, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandError(argv, -1, f"timed out after {timeout}s") from exc
    if check and completed.returncode != 0:
        raise CommandError(argv, completed.returncode, completed.stderr)
    return ProcResult(completed.returncode, completed.stdout, completed.stderr)
