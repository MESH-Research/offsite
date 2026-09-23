"""Exception types and result records shared across the backup pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class ComponentError(Exception):
    """Raised inside a component for failures it cannot recover from."""


class CommandError(Exception):
    """Raised when an external command exits non-zero or times out."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"command {cmd!r} failed with exit code {returncode}: {stderr.strip()}")
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class ComponentResult:
    """Outcome of one component's backup run."""

    name: str
    ok: bool
    snapshot_ids: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class RunReport:
    """Aggregated outcome of a whole run."""

    results: list[ComponentResult]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def exit_code(self) -> int:
        return 0 if self.ok else 1
