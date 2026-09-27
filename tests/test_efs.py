import pytest

from conftest import FakeRestic
from offsite_backup.components.efs import EfsComponent
from offsite_backup.config import EfsConfig
from offsite_backup.errors import CommandError, ComponentError


def component(path):
    return EfsComponent(EfsConfig(mount_path=path))


class TestBackup:
    def test_populated_mount_is_snapshotted_with_efs_tag(self, tmp_path):
        mount = tmp_path / "efs"
        (mount / "wp-content" / "uploads").mkdir(parents=True)
        (mount / "wp-content" / "uploads" / "a.jpg").write_text("jpeg")
        restic = FakeRestic()

        ids = component(mount).backup(restic)

        assert ids == ["snap1"]
        ((tags, files),) = restic.backups
        assert tags == ["efs"]
        assert files == {"wp-content/uploads/a.jpg": "jpeg"}

    def test_restic_failure_propagates(self, tmp_path):
        mount = tmp_path / "efs"
        mount.mkdir()
        (mount / "f").write_text("x")
        with pytest.raises(CommandError):
            component(mount).backup(FakeRestic(fail=True))


class TestMountGuards:
    def test_missing_mount_path_is_an_error(self, tmp_path):
        restic = FakeRestic()
        with pytest.raises(ComponentError, match="efs"):
            component(tmp_path / "efs").backup(restic)
        assert restic.backups == []

    def test_empty_mount_is_an_error_not_an_empty_snapshot(self, tmp_path):
        mount = tmp_path / "efs"
        mount.mkdir()
        restic = FakeRestic()
        with pytest.raises(ComponentError, match="empty"):
            component(mount).backup(restic)
        assert restic.backups == []

    def test_mount_path_that_is_a_file_is_an_error(self, tmp_path):
        mount = tmp_path / "efs"
        mount.write_text("not a directory")
        with pytest.raises(ComponentError):
            component(mount).backup(FakeRestic())
