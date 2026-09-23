"""Exception types shared across the backup pipeline."""

from __future__ import annotations


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
