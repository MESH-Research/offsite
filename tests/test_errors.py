from offsite_backup.errors import CommandError, NotificationError


def test_command_error_carries_cmd_returncode_stderr():
    err = CommandError(["restic", "backup"], 3, "boom")
    assert err.cmd == ["restic", "backup"]
    assert err.returncode == 3
    assert err.stderr == "boom"


def test_command_error_message_mentions_command_and_code():
    err = CommandError(["restic", "backup"], 3, "boom")
    assert "restic" in str(err)
    assert "3" in str(err)


def test_notification_error_carries_channel_target_and_cause():
    cause = OSError("connection refused")
    err = NotificationError("ntfy", "https://ntfy.example.org/kc", cause)
    assert err.channel == "ntfy"
    assert err.target == "https://ntfy.example.org/kc"
    assert err.cause is cause
    assert "ntfy" in str(err)
    assert "connection refused" in str(err)
