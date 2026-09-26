import os
from unittest import mock

import pytest

from conftest import FakeRunner
from offsite_backup.config import load_config
from offsite_backup.errors import ConfigError, NotificationError
from offsite_backup.orchestrator import run_backup, select_targets
from offsite_backup.restic import Restic

BASE_ENV = {
    "RESTIC_REPOSITORY": "local:/backups/primary",
    "RESTIC_PASSWORD": "primary-pw",
    "RESTIC_MIRROR_1_REPOSITORY": "local:/backups/mirror",
    "RESTIC_MIRROR_1_PASSWORD": "mirror-pw",
    "DB_SOURCES": "wordpress,idms",
    "DB_WORDPRESS_ENGINE": "mariadb",
    "DB_WORDPRESS_INSTANCE_ID": "wp",
    "DB_WORDPRESS_USER": "u",
    "DB_WORDPRESS_PASSWORD": "p",
    "DB_WORDPRESS_DATABASES": "commons",
    "DB_IDMS_ENGINE": "postgres",
    "DB_IDMS_INSTANCE_ID": "idms",
    "DB_IDMS_USER": "u",
    "DB_IDMS_PASSWORD": "p",
    "DB_IDMS_DATABASES": "idms",
}


def config(**overrides):
    with mock.patch.dict(os.environ, {**BASE_ENV, **overrides}, clear=True):
        return load_config()


class StubComponent:
    def __init__(self, name, ids=(), error=None):
        self.name = name
        self.ids = list(ids)
        self.error = error
        self.ran = False

    def backup(self, restic):
        self.ran = True
        if self.error is not None:
            raise self.error
        return list(self.ids)


class RecordingNotifier:
    def __init__(self, errors=()):
        self.notifications = []
        self.errors = list(errors)

    def notify(self, notification):
        self.notifications.append(notification)
        return self.errors


class Harness:
    """Primary + mirror Restic instances on separate FakeRunners, plus a notifier."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.primary_runner = FakeRunner()
        self.mirror_runner = FakeRunner()
        self.restics = [
            Restic(cfg.primary, host=cfg.restic_host, runner=self.primary_runner),
            Restic(cfg.mirrors[0], host=cfg.restic_host, runner=self.mirror_runner),
        ]
        self.notifier = RecordingNotifier()
        self.ticks = iter(range(0, 10_000, 5))

    def run(self, components, targets=()):
        return run_backup(
            self.cfg,
            restics=self.restics,
            components=components,
            notifier=self.notifier,
            targets=targets,
            clock=lambda: float(next(self.ticks)),
        )


class TestSelectTargets:
    def test_default_set_expands_db_to_every_source(self):
        assert select_targets(config(), []) == ["ecs", "wordpress", "idms", "efs", "s3"]

    def test_explicit_targets_keep_their_order(self):
        assert select_targets(config(), ["efs", "idms"]) == ["efs", "idms"]

    def test_db_keyword_expands_and_duplicates_collapse(self):
        assert select_targets(config(), ["db", "wordpress"]) == ["wordpress", "idms"]

    def test_unknown_target_raises(self):
        with pytest.raises(ConfigError, match="floppy"):
            select_targets(config(), ["floppy"])

    def test_no_db_sources_means_db_expands_to_nothing(self):
        cfg = config(DB_SOURCES="", COMPONENTS="ecs,db")
        assert select_targets(cfg, []) == ["ecs"]


class TestGreenRun:
    def test_all_components_run_in_order_and_report_ok(self):
        h = Harness(config())
        ecs, efs = StubComponent("ecs", ["aaa"]), StubComponent("efs", ["bbb"])
        report = h.run([efs, ecs], targets=["ecs", "efs"])
        assert report.ok is True
        assert [r.name for r in report.results] == ["ecs", "efs"]
        assert [r.snapshot_ids for r in report.results] == [["aaa"], ["bbb"]]
        assert all(r.duration_s > 0 for r in report.results)

    def test_retention_applied_to_primary_and_mirror(self):
        h = Harness(config())
        h.run([StubComponent("ecs", ["aaa"])], targets=["ecs"])
        primary_forget = h.primary_runner.command_matching("forget")
        mirror_forget = h.mirror_runner.command_matching("forget")
        assert primary_forget is not None and "--keep-weekly" in primary_forget
        assert mirror_forget is not None
        assert "--prune" not in primary_forget

    def test_new_snapshots_copied_to_mirror(self):
        h = Harness(config())
        h.run([StubComponent("ecs", ["aaa"]), StubComponent("efs", ["bbb"])], targets=["ecs", "efs"])
        copy = h.mirror_runner.command_matching("copy")
        assert copy is not None
        assert "aaa" in copy and "bbb" in copy
        assert "local:/backups/primary" in copy

    def test_success_notification_sent(self):
        h = Harness(config())
        h.run([StubComponent("ecs", ["aaa"])], targets=["ecs"])
        (notification,) = h.notifier.notifications
        assert notification.ok is True
        assert "backup" in notification.title

    def test_notification_delivery_errors_do_not_fail_the_run(self):
        h = Harness(config())
        h.notifier = RecordingNotifier(
            errors=[NotificationError("ntfy", "https://x/y", OSError("down"))]
        )
        report = h.run([StubComponent("ecs", ["aaa"])], targets=["ecs"])
        assert report.ok is True


class TestFailures:
    def test_failed_component_does_not_stop_the_others(self):
        h = Harness(config())
        ecs = StubComponent("ecs", error=RuntimeError("export exploded"))
        efs = StubComponent("efs", ["bbb"])
        report = h.run([ecs, efs], targets=["ecs", "efs"])
        assert efs.ran is True
        assert report.ok is False
        assert report.exit_code() == 1
        failed = next(r for r in report.results if r.name == "ecs")
        assert failed.ok is False
        assert "export exploded" in failed.error

    def test_no_retention_anywhere_when_a_component_failed(self):
        h = Harness(config())
        h.run(
            [StubComponent("ecs", error=RuntimeError("x")), StubComponent("efs", ["bbb"])],
            targets=["ecs", "efs"],
        )
        assert h.primary_runner.command_matching("forget") is None
        assert h.mirror_runner.command_matching("forget") is None

    def test_successful_snapshots_still_copied_when_another_component_failed(self):
        h = Harness(config())
        h.run(
            [StubComponent("ecs", error=RuntimeError("x")), StubComponent("efs", ["bbb"])],
            targets=["ecs", "efs"],
        )
        copy = h.mirror_runner.command_matching("copy")
        assert copy is not None and "bbb" in copy

    def test_failure_notification_names_component_and_error(self):
        h = Harness(config())
        h.run([StubComponent("ecs", error=RuntimeError("export exploded"))], targets=["ecs"])
        (notification,) = h.notifier.notifications
        assert notification.ok is False
        assert "ecs" in notification.body
        assert "export exploded" in notification.body

    def test_mirror_copy_failure_is_reported_but_primary_still_forgets(self):
        h = Harness(config())
        h.mirror_runner.on("copy", returncode=1, stderr="mirror unreachable")
        report = h.run([StubComponent("ecs", ["aaa"])], targets=["ecs"])
        assert report.ok is False
        mirror = next(r for r in report.results if r.name.startswith("mirror:"))
        assert "mirror unreachable" in mirror.error
        assert h.primary_runner.command_matching("forget") is not None
        assert h.mirror_runner.command_matching("forget") is None

    def test_unreachable_primary_repository_short_circuits(self):
        h = Harness(config())
        h.primary_runner.on("cat", "config", returncode=1, stderr="no repo")
        h.primary_runner.on("init", returncode=1, stderr="permission denied")
        ecs = StubComponent("ecs", ["aaa"])
        report = h.run([ecs], targets=["ecs"])
        assert ecs.ran is False
        assert report.ok is False
        (result,) = report.results
        assert result.name == "repository"
        assert "permission denied" in result.error
        (notification,) = h.notifier.notifications
        assert notification.ok is False

    def test_selected_target_without_a_component_is_a_config_error(self):
        h = Harness(config())
        with pytest.raises(ConfigError, match="efs"):
            h.run([StubComponent("ecs", ["aaa"])], targets=["ecs", "efs"])
        assert h.notifier.notifications == []
