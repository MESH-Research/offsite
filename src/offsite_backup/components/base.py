"""The contract every backup component implements."""

from __future__ import annotations

from typing import Protocol

from offsite_backup.restic import Restic


class Component(Protocol):
    """One kind of artifact that can be backed up into restic.

    `backup` does the work against the primary repository and returns the
    snapshot ids it created. It may raise: the orchestrator converts failures
    into a failed `ComponentResult`. Components must clean up their own
    scratch space in a ``finally`` so a failure never leaks staging data.
    """

    name: str

    def backup(self, restic: Restic) -> list[str]:
        """Back up this component into `restic`; return new snapshot ids."""
        ...
