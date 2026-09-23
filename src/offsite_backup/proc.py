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
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def __call__(
        self,
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> ProcResult: ...


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
