"""Run a backup end to end.

Select targets, make sure every repository exists, run each component
against the primary repository (continuing past failures), copy new
snapshots to the mirrors, apply retention only where the whole run was
green, and notify. Returns a `RunReport`; never raises for component
failures.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

from offsite_backup.components.base import Component
from offsite_backup.config import BUILTIN_COMPONENTS, Config
from offsite_backup.errors import ConfigError
from offsite_backup.notify import Notifier, notification_for
from offsite_backup.restic import Restic
from offsite_backup.results import ComponentResult, RunReport

log = logging.getLogger(__name__)

OPERATION = "backup"


def select_targets(cfg: Config, targets: Sequence[str]) -> list[str]:
    """Resolve requested targets into an ordered, de-duplicated list of component names.

    With no targets the configured default set (``COMPONENTS``) is used.
    ``db`` expands to every configured database source; a source name selects
    just that source. Unknown names raise `ConfigError`.
    """
    sources = [source.name for source in cfg.db_sources]
    resolved: list[str] = []
    for target in targets or cfg.components:
        if target == "db":
            resolved += sources
        elif target in BUILTIN_COMPONENTS or target in sources:
            resolved.append(target)
        else:
            raise ConfigError(f"unknown backup target {target!r}")
    return list(dict.fromkeys(resolved))


def run_backup(
    cfg: Config,
    *,
    restics: Sequence[Restic],
    components: Sequence[Component],
    notifier: Notifier,
    targets: Sequence[str] = (),
    clock: Callable[[], float] = time.monotonic,
) -> RunReport:
    """Back up the selected targets; return the aggregated report.

    `restics` is the primary repository followed by its mirrors. Retention
    (``forget``, never prune) runs on the primary only when every component
    succeeded, and on a mirror only when additionally its copy succeeded.
    Mirror and retention failures appear as their own failed results. The
    report is sent through `notifier`; delivery failures are logged.
    """
    selected = select_targets(cfg, targets)
    available = {component.name: component for component in components}
    missing = [name for name in selected if name not in available]
    if missing:
        raise ConfigError(f"no component available for target(s): {', '.join(missing)}")

    primary, mirrors = restics[0], list(restics[1:])
    results: list[ComponentResult] = []

    try:
        primary.ensure_repo()
        for mirror in mirrors:
            mirror.ensure_repo_from(primary.repo)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        log.error("repository unavailable: %s", exc)
        results.append(ComponentResult(name="repository", ok=False, error=str(exc)))
        return _finish(results, notifier)

    for name in selected:
        results.append(_run_component(available[name], primary, clock))
    components_ok = all(result.ok for result in results)
    new_ids = [sid for result in results if result.ok for sid in result.snapshot_ids]

    healthy_mirrors: list[Restic] = []
    for mirror in mirrors:
        label = f"mirror:{mirror.repo.repository}"
        started = clock()
        try:
            if new_ids:
                mirror.copy_from(primary.repo, new_ids)
            healthy_mirrors.append(mirror)
        except Exception as exc:  # noqa: BLE001
            log.error("%s copy failed: %s", label, exc)
            results.append(
                ComponentResult(name=label, ok=False, error=str(exc), duration_s=clock() - started)
            )

    if components_ok:
        for repo in [primary, *healthy_mirrors]:
            try:
                repo.forget(
                    keep_daily=cfg.keep_daily,
                    keep_weekly=cfg.keep_weekly,
                    keep_monthly=cfg.keep_monthly,
                )
            except Exception as exc:  # noqa: BLE001
                label = f"retention:{repo.repo.repository}"
                log.error("%s failed: %s", label, exc)
                results.append(ComponentResult(name=label, ok=False, error=str(exc)))
    else:
        log.warning("skipping retention: not every component succeeded")

    return _finish(results, notifier)


def _run_component(
    component: Component, primary: Restic, clock: Callable[[], float]
) -> ComponentResult:
    started = clock()
    log.info("backing up %s", component.name)
    try:
        ids = list(component.backup(primary))
    except Exception as exc:
        log.exception("%s failed", component.name)
        return ComponentResult(
            name=component.name, ok=False, error=str(exc), duration_s=clock() - started
        )
    return ComponentResult(
        name=component.name, ok=True, snapshot_ids=ids, duration_s=clock() - started
    )


def _finish(results: list[ComponentResult], notifier: Notifier) -> RunReport:
    report = RunReport(results)
    for error in notifier.notify(notification_for(OPERATION, report)):
        log.warning("notification failed: %s", error)
    return report
