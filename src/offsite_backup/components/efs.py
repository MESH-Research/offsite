"""Back up the EFS volume mounted (read-only) into the task."""

from __future__ import annotations

from offsite_backup.config import EfsConfig
from offsite_backup.errors import ComponentError
from offsite_backup.restic import Restic

TAG = "efs"


class EfsComponent:
    """Snapshot the mounted EFS tree."""

    name = TAG

    def __init__(self, cfg: EfsConfig) -> None:
        """Bind to the EFS settings (the mount path)."""
        self._cfg = cfg

    def backup(self, restic: Restic) -> list[str]:
        """Snapshot the mount path with the ``efs`` tag; return the snapshot id.

        Raises `ComponentError` when the mount path is missing, not a
        directory, or empty. An empty directory almost always means the
        volume was never mounted; snapshotting nothing would silently mask
        that and the freshness check would still pass.
        """
        mount = self._cfg.mount_path
        if not mount.is_dir():
            raise ComponentError(f"efs mount path {mount} is missing or not a directory")
        if not any(mount.iterdir()):
            raise ComponentError(f"efs mount path {mount} is empty: volume not mounted?")
        return [restic.backup_path(mount, tags=[TAG])]
