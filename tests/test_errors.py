from offsite_backup.errors import CommandError


def test_command_error_carries_cmd_returncode_stderr():
    err = CommandError(["restic", "backup"], 3, "boom")
    assert err.cmd == ["restic", "backup"]
    assert err.returncode == 3
    assert err.stderr == "boom"


def test_command_error_message_mentions_command_and_code():
    err = CommandError(["restic", "backup"], 3, "boom")
    assert "restic" in str(err)
    assert "3" in str(err)
