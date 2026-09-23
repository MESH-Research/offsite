"""Outcome records for component runs and whole-run reports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComponentResult:
    """Outcome of one component's backup run."""

    name: str
    ok: bool
    snapshot_ids: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class RunReport:
    """Aggregated outcome of a whole run."""

    results: list[ComponentResult]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def exit_code(self) -> int:
        return 0 if self.ok else 1
