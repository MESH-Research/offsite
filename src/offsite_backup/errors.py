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


class NotificationError(Exception):
    """A notification channel failed to deliver.

    Collected and returned by `Notifier.notify` rather than raised past it:
    a broken channel must never fail the operation being reported on.
    """

    def __init__(self, channel: str, target: str, cause: BaseException) -> None:
        super().__init__(f"{channel} {target}: {cause}")
        self.channel = channel
        self.target = target
        self.cause = cause
