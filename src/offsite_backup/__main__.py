"""Command-line entry point: parse arguments, load config, dispatch.

Exit codes: 0 success, 1 operation failure, 2 configuration/usage error.
Command handlers are injectable so tests exercise dispatch and exit-code
mapping without running real operations.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

import boto3
import click
from environs import Env

from offsite_backup.__version__ import __version__
from offsite_backup.components.base import Component
from offsite_backup.components.ecs import EcsComponent
from offsite_backup.config import Config, load_config
from offsite_backup.errors import ConfigError
from offsite_backup.notify import Notifier
from offsite_backup.orchestrator import run_backup
from offsite_backup.restic import Restic
from offsite_backup.ssh import prepare_ssh_home

Handler = Callable[[Config, SimpleNamespace], int]

RESTORE_TARGETS: tuple[str, ...] = ("db", "efs", "s3", "ecs")


def _not_implemented(cfg: Config, args: SimpleNamespace) -> int:
    raise NotImplementedError(f"command {args.command!r} is not implemented yet")


def _build_components(cfg: Config) -> list[Component]:
    """Compose the components implemented so far; later build steps add more.

    Scratch space lives under the stable ``STAGING_DIR`` (never a random temp
    directory): restic records absolute paths in snapshots, and a stable path
    is what lets it find the parent snapshot and skip unchanged files.
    """
    return [EcsComponent(cfg.ecs, boto3.client("ecs"), cfg.staging_dir / "ecs")]


def _run_backup(cfg: Config, args: SimpleNamespace) -> int:
    """Handle ``backup``: compose the real collaborators and run the orchestrator."""
    with tempfile.TemporaryDirectory(prefix="offsite-backup-") as tmp:
        workdir = Path(tmp)
        ssh_home = prepare_ssh_home(cfg.repos, workdir / "ssh")
        restics = [
            Restic(repo, host=cfg.restic_host, cache_dir=cfg.cache_dir, ssh_home=ssh_home)
            for repo in cfg.repos
        ]
        report = run_backup(
            cfg,
            restics=restics,
            components=_build_components(cfg),
            notifier=Notifier(cfg.notify),
            targets=list(args.targets),
        )
    return report.exit_code()


#: Composition root: later build phases replace the remaining stubs with real handlers.
DEFAULT_HANDLERS: dict[str, Handler] = {
    "backup": _run_backup,
    "verify": _not_implemented,
    "verify-deep": _not_implemented,
    "prune": _not_implemented,
    "snapshots": _not_implemented,
    "restore": _not_implemented,
}


def _load_dotenv() -> Env:
    """Return an `Env` with a ``.env`` file loaded; real environment wins.

    environs keeps file values inside the `Env` instance (not ``os.environ``),
    so the same instance must be handed to `load_config`. ``ENV_FILE`` names
    the file explicitly and must exist — a configured but missing file is an
    error, not a silent no-op. Without it, environs' default search runs,
    which starts from this package's own directory and is therefore only
    useful for local development from a source checkout.
    """
    env = Env()
    path = os.environ.get("ENV_FILE")
    if path:
        if not Path(path).is_file():
            raise ConfigError(f"ENV_FILE points to a missing file: {path}")
        env.read_env(path, recurse=False)
    else:
        env.read_env()
    return env


def _dispatch(ctx: click.Context, command: str, **params: object) -> int:
    """Load configuration and hand off to the handler registered for `command`.

    Runs after Click has fully parsed the command, so usage errors never
    require a valid configuration.
    """
    try:
        cfg = load_config(_load_dotenv() if ctx.obj["dotenv"] else None)
        handler = ctx.obj["handlers"][command]
        return handler(cfg, SimpleNamespace(command=command, **params))
    except ConfigError as exc:
        click.echo(f"configuration error: {exc}", err=True)
        return 2


@click.group(help="Portable offsite backups of AWS resources via restic.")
@click.version_option(__version__, prog_name="offsite-backup")
def cli() -> None:
    """Root command group; subcommands do the work."""


@cli.command(help="Back up the given targets (default: the configured set).")
@click.argument("targets", nargs=-1)
@click.pass_context
def backup(ctx: click.Context, targets: tuple[str, ...]) -> int:
    """Run backups for TARGETS (components or DB source names)."""
    return _dispatch(ctx, "backup", targets=list(targets))


@cli.command(help="Check repositories and snapshot freshness.")
@click.pass_context
def verify(ctx: click.Context) -> int:
    """Run the weekly verification."""
    return _dispatch(ctx, "verify")


@cli.command("verify-deep", help="Verify while re-reading a data subset.")
@click.pass_context
def verify_deep(ctx: click.Context) -> int:
    """Run the monthly deep verification."""
    return _dispatch(ctx, "verify-deep")


@cli.command(help="Remove unreferenced repository data.")
@click.pass_context
def prune(ctx: click.Context) -> int:
    """Prune every repository."""
    return _dispatch(ctx, "prune")


@cli.command(help="List snapshots.")
@click.pass_context
def snapshots(ctx: click.Context) -> int:
    """List snapshots as a table."""
    return _dispatch(ctx, "snapshots")


@cli.command(
    help="Restore an artifact back to AWS.",
    context_settings={"ignore_unknown_options": True},
)
@click.argument("target", type=click.Choice(RESTORE_TARGETS))
@click.argument("rest", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def restore(ctx: click.Context, target: str, rest: tuple[str, ...]) -> int:
    """Restore TARGET; remaining options are target-specific (see the runbook)."""
    return _dispatch(ctx, "restore", target=target, rest=list(rest))


def main(
    argv: Sequence[str] | None = None,
    *,
    handlers: Mapping[str, Handler] | None = None,
    dotenv: bool = True,
) -> int:
    """Run the CLI and return its exit code.

    `handlers` overrides the default command handlers (used by tests and as
    the composition root). `dotenv` controls whether a local ``.env`` file is
    read into the environment first (disabled in tests).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    obj = {"handlers": handlers or DEFAULT_HANDLERS, "dotenv": dotenv}
    try:
        result = cli.main(
            args=list(argv) if argv is not None else None,
            prog_name="offsite-backup",
            standalone_mode=False,
            obj=obj,
        )
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
