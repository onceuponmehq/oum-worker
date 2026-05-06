"""Inner entrypoint invoked by launchd to mark a scheduled worker as started."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oum_worker import state


def mark_started(workdir: Path, *, label: str) -> None:
    state.update(workdir, label, started_at=state.utc_now_iso())


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oum_worker.runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    ms = sub.add_parser("mark-started")
    ms.add_argument("--label", required=True)
    ms.add_argument("--logs-dir", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "mark-started":
        mark_started(Path(args.logs_dir).expanduser().resolve(), label=args.label)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
