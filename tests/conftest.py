"""Shared test doubles and fixtures."""

from dataclasses import dataclass

import pytest

from offsite_backup.errors import CommandError
from offsite_backup.proc import ProcResult


@dataclass
class FakeCall:
    """One recorded invocation of the FakeRunner."""

    cmd: list[str]
    env: dict[str, str] | None


class FakeRunner:
    """Runner double: match argv tokens to canned results, recording calls.

    Rules registered with `on()` are tried in order; a rule matches when all
    its tokens appear in the argv. Unmatched commands succeed with empty
    output. Non-zero canned results raise `CommandError` when `check` is set,
    mirroring the real runner.
    """

    def __init__(self):
        self.calls: list[FakeCall] = []
        self._rules: list[tuple[tuple[str, ...], ProcResult]] = []

    def on(self, *tokens, stdout="", stderr="", returncode=0):
        """Register a canned result for commands containing all `tokens`."""
        self._rules.append((tokens, ProcResult(returncode, stdout, stderr)))

    def __call__(self, cmd, *, env=None, check=True, timeout=None):
        """Record the call and return the first matching canned result."""
        argv = list(cmd)
        self.calls.append(FakeCall(argv, dict(env) if env else None))
        for tokens, result in self._rules:
            if all(token in argv for token in tokens):
                if check and result.returncode != 0:
                    raise CommandError(argv, result.returncode, result.stderr)
                return result
        return ProcResult(0, "", "")

    @property
    def commands(self) -> list[list[str]]:
        """Return every recorded argv."""
        return [call.cmd for call in self.calls]

    def command_matching(self, *tokens) -> list[str] | None:
        """Return the first recorded argv containing all `tokens`, if any."""
        for cmd in self.commands:
            if all(token in cmd for token in tokens):
                return cmd
        return None


@pytest.fixture
def fake_runner():
    """Provide a fresh FakeRunner."""
    return FakeRunner()
