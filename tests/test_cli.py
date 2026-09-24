import os
from unittest import mock

import pytest

from offsite_backup.__main__ import main
from offsite_backup.__version__ import __version__

VALID_ENV = {
    "RESTIC_REPOSITORY": "local:/backups/repo",
    "RESTIC_PASSWORD": "repo-pw",
}

ALL_COMMANDS = ["backup", "verify", "verify-deep", "prune", "snapshots"]


class RecordingHandler:
    """Handler double: records the dispatch and returns a fixed exit code."""

    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.calls = []

    def __call__(self, cfg, args):
        self.calls.append((cfg, args))
        return self.exit_code


def run_cli(argv, handler=None, env=VALID_ENV):
    handler = handler or RecordingHandler()
    handlers = dict.fromkeys([*ALL_COMMANDS, "restore"], handler)
    with mock.patch.dict(os.environ, env, clear=True):
        code = main(argv, handlers=handlers, dotenv=False)
    return code, handler


class TestUsageErrors:
    def test_unknown_command_exits_2_with_usage(self, capsys):
        code, handler = run_cli(["frobnicate"])
        assert code == 2
        assert handler.calls == []
        assert "usage" in capsys.readouterr().err

    def test_no_command_exits_2_with_usage(self, capsys):
        code, handler = run_cli([])
        assert code == 2
        assert handler.calls == []
        assert "usage" in capsys.readouterr().err

    def test_restore_without_target_exits_2(self, capsys):
        code, handler = run_cli(["restore"])
        assert code == 2
        assert handler.calls == []

    def test_restore_with_unknown_target_exits_2(self, capsys):
        code, handler = run_cli(["restore", "floppy"])
        assert code == 2
        assert handler.calls == []


class TestConfigErrors:
    def test_missing_env_exits_2_naming_variable(self, capsys):
        code, handler = run_cli(["verify"], env={})
        assert code == 2
        assert handler.calls == []
        assert "RESTIC_REPOSITORY" in capsys.readouterr().err


class TestDispatch:
    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_command_dispatches_with_loaded_config(self, command):
        code, handler = run_cli([command])
        assert code == 0
        ((cfg, args),) = handler.calls
        assert cfg.primary.repository == "local:/backups/repo"
        assert args.command == command

    def test_handler_exit_code_is_returned(self):
        code, _ = run_cli(["verify"], handler=RecordingHandler(exit_code=1))
        assert code == 1

    def test_backup_targets_are_parsed(self):
        _, handler = run_cli(["backup", "efs", "idms"])
        ((_, args),) = handler.calls
        assert args.targets == ["efs", "idms"]

    def test_backup_targets_default_to_empty(self):
        _, handler = run_cli(["backup"])
        ((_, args),) = handler.calls
        assert args.targets == []

    def test_restore_target_and_remaining_args(self):
        _, handler = run_cli(["restore", "db", "wordpress", "--at", "1w", "--yes"])
        ((_, args),) = handler.calls
        assert args.command == "restore"
        assert args.target == "db"
        assert args.rest == ["wordpress", "--at", "1w", "--yes"]


class TestVersion:
    def test_version_flag_prints_version_and_exits_0(self, capsys):
        code, _ = run_cli(["--version"])
        assert code == 0
        assert __version__ in capsys.readouterr().out
