"""Command-line entry point: parse arguments, load config, dispatch.

Exit codes: 0 success, 1 operation failure, 2 configuration/usage error.
Command handlers are injectable so tests exercise dispatch and exit-code
mapping without running real operations.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence

from environs import Env

from offsite_backup.__version__ import __version__
from offsite_backup.config import Config, load_config
from offsite_backup.errors import ConfigError

Handler = Callable[[Config, argparse.Namespace], int]

RESTORE_TARGETS = ("db", "efs", "s3", "ecs")


def _not_implemented(cfg: Config, args: argparse.Namespace) -> int:
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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for all commands."""
    parser = argparse.ArgumentParser(
        prog="offsite-backup",
        description="Portable offsite backups of AWS resources via restic.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser(
        "backup", help="back up the given targets (default: the configured set)"
    )
    backup.add_argument(
        "targets",
        nargs="*",
        default=[],
        help="components or DB source names, e.g. efs s3 wordpress",
    )

    commands.add_parser("verify", help="check repositories and snapshot freshness")
    commands.add_parser("verify-deep", help="verify while re-reading a data subset")
    commands.add_parser("prune", help="remove unreferenced repository data")
    commands.add_parser("snapshots", help="list snapshots")

    restore = commands.add_parser("restore", help="restore an artifact back to AWS")
    restore.add_argument("target", choices=RESTORE_TARGETS, help="artifact type")
    restore.add_argument(
        "rest",
        nargs=argparse.REMAINDER,
        help="target-specific options (see the restore runbook)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    handlers: Mapping[str, Handler] | None = None,
    dotenv: bool = True,
) -> int:
    """Run the CLI: parse `argv`, load configuration, dispatch to a handler.

    `handlers` overrides the default command handlers (used by tests and as
    the composition root). `dotenv` controls whether a local ``.env`` file is
    read into the environment first (disabled in tests).
    """
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if dotenv:
        Env().read_env()
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    handler = (handlers or DEFAULT_HANDLERS)[args.command]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
