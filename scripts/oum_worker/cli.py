"""oum-worker CLI: argparse + dispatcher."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from oum_worker.launchd import ROOT


DEFAULT_LOGS_DIR = ROOT / ".logs" / "oum-worker"
DEFAULT_TMUX_SESSION = "oum"


def workdir_from_args(args: argparse.Namespace) -> Path:
    p = Path(args.logs_dir).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _add_global(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--logs-dir",
        default=os.environ.get("OUM_WORKER_LOGS_DIR", str(DEFAULT_LOGS_DIR)),
        help=f"State + log directory root (default: {DEFAULT_LOGS_DIR})",
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oum-worker",
                                description="Manage Claude Code worker sessions.")
    _add_global(p)
    sub = p.add_subparsers(dest="verb", required=True)

    for name, helptxt in [
        ("spawn",    "Start a Claude Code worker now."),
        ("schedule", "Schedule a Claude Code worker for later via launchd."),
        ("send",     "Send a message to a live worker."),
        ("capture",  "Print the worker's most recent response."),
        ("wait",     "Block until the worker is idle."),
        ("ask",      "send + wait + capture."),
        ("list",     "List all known workers."),
        ("status",   "Show one worker's state."),
        ("kill",     "Close a worker's tmux window and unbootstrap any plist."),
        ("logs",     "Print log path or tail it."),
    ]:
        sp = sub.add_parser(name, help=helptxt)
        _add_global(sp)
        sp.set_defaults(_handler=_unimplemented(name))
    return p


def _unimplemented(name: str):
    def _h(args: argparse.Namespace) -> int:
        print(f"oum-worker: '{name}' not implemented yet", file=sys.stderr)
        return 1
    return _h


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args._handler(args)


if __name__ == "__main__":
    sys.exit(main())
