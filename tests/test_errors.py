import pytest

from offsite_backup.errors import CommandError, ComponentResult, RunReport


def test_command_error_carries_cmd_returncode_stderr():
    err = CommandError(["restic", "backup"], 3, "boom")
    assert err.cmd == ["restic", "backup"]
    assert err.returncode == 3
    assert err.stderr == "boom"


def test_command_error_message_mentions_command_and_code():
    err = CommandError(["restic", "backup"], 3, "boom")
    assert "restic" in str(err)
    assert "3" in str(err)


def test_component_result_defaults():
    result = ComponentResult(name="efs", ok=True)
    assert result.name == "efs"
    assert result.ok is True
    assert result.snapshot_ids == []
    assert result.error is None
    assert result.duration_s == 0.0


def test_component_results_have_independent_snapshot_lists():
    a = ComponentResult(name="a", ok=True)
    b = ComponentResult(name="b", ok=True)
    a.snapshot_ids.append("abc123")
    assert b.snapshot_ids == []


def test_run_report_ok_when_all_results_ok():
    report = RunReport(
        [ComponentResult(name="ecs", ok=True), ComponentResult(name="efs", ok=True)]
    )
    assert report.ok is True
    assert report.exit_code() == 0


def test_run_report_not_ok_when_any_result_failed():
    report = RunReport(
        [
            ComponentResult(name="ecs", ok=True),
            ComponentResult(name="db", ok=False, error="replica timed out"),
        ]
    )
    assert report.ok is False
    assert report.exit_code() == 1


def test_run_report_empty_is_ok():
    report = RunReport([])
    assert report.ok is True
    assert report.exit_code() == 0


@pytest.mark.parametrize("ok,code", [(True, 0), (False, 1)])
def test_run_report_exit_code_matches_ok(ok, code):
    report = RunReport([ComponentResult(name="s3", ok=ok)])
    assert report.exit_code() == code
