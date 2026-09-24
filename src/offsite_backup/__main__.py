"""Command-line entry point: parse arguments, load config, dispatch.

Exit codes: 0 success, 1 operation failure, 2 configuration/usage error.
Command handlers are injectable so tests exercise dispatch and exit-code
mapping without running real operations.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace

import click
from environs import Env

from offsite_backup.__version__ import __version__
from offsite_backup.config import Config, load_config
from offsite_backup.errors import ConfigError

Handler = Callable[[Config, SimpleNamespace], int]

RESTORE_TARGETS = ("db", "efs", "s3", "ecs")


def _not_implemented(cfg: Config, args: SimpleNamespace) -> int:
    raise NotImplementedError(f"command {args.command!r} is not implemented yet")


#: Composition root: later build phases replace these stubs with real handlers.
DEFAULT_HANDLERS: dict[str, Handler] = {
    "backup": _not_implemented,
    "verify": _not_implemented,
    "verify-deep": _not_implemented,
    "prune": _not_implemented,
    "snapshots": _not_implemented,
    "restore": _not_implemented,
}


def _dispatch(ctx: click.Context, command: str, **params: object) -> int:
    """Load configuration and hand off to the handler registered for `command`.

    Runs after Click has fully parsed the command, so usage errors never
    require a valid configuration.
    """
    if ctx.obj["dotenv"]:
        Env().read_env()
    try:
        cfg = load_config()
    except ConfigError as exc:
        click.echo(f"configuration error: {exc}", err=True)
        return 2
    handler = ctx.obj["handlers"][command]
    return handler(cfg, SimpleNamespace(command=command, **params))


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
