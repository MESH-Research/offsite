"""Tests for the subprocess seam.

This is the one test module allowed to run real subprocesses — tiny, fast
commands only — because `run` is the boundary everything else mocks.
"""

import pytest

from offsite_backup.errors import CommandError
from offsite_backup.proc import run


def test_captures_stdout():
    result = run(["echo", "hello"])
    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""


def test_captures_stderr():
    result = run(["sh", "-c", "echo oops 1>&2"])
    assert result.returncode == 0
    assert result.stderr == "oops\n"


def test_nonzero_exit_raises_command_error_with_details():
    with pytest.raises(CommandError) as excinfo:
        run(["sh", "-c", "echo bad 1>&2; exit 3"])
    assert excinfo.value.returncode == 3
    assert "bad" in excinfo.value.stderr
    assert excinfo.value.cmd == ["sh", "-c", "echo bad 1>&2; exit 3"]


def test_nonzero_exit_returns_result_when_check_disabled():
    result = run(["sh", "-c", "exit 3"], check=False)
    assert result.returncode == 3


def test_env_overlays_without_clobbering_path():
    result = run(["sh", "-c", "echo $FOO; command -v sh"], env={"FOO": "bar"})
    assert result.stdout.startswith("bar\n")
    # `command -v sh` succeeding proves PATH survived the overlay
    assert "/sh" in result.stdout


def test_timeout_raises_command_error():
    with pytest.raises(CommandError) as excinfo:
        run(["sleep", "5"], timeout=0.1)
    assert excinfo.value.returncode == -1
    assert "timed out" in str(excinfo.value)
