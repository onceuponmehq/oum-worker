"""Per-worker state.json registry, flock-protected."""
from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


FLOCK_TIMEOUT_SECONDS = 5.0
FLOCK_POLL_SECONDS = 0.05


def utc_now_iso() -> str:
    # Single now() — splitting across two calls can produce a millisecond
    # rollover where the seconds string and the millis disagree.
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


class WorkerNotFound(Exception):
    pass


class LabelExists(Exception):
    pass


class StateBusy(Exception):
    """Raised when flock cannot be acquired within FLOCK_TIMEOUT_SECONDS."""


def _acquire_flock(fd: int, op: int) -> None:
    """Try to acquire `op` flock on fd, polling up to FLOCK_TIMEOUT_SECONDS."""
    deadline = time.monotonic() + FLOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, op | fcntl.LOCK_NB)
            return
        except OSError as e:
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                raise StateBusy(
                    f"could not acquire state.json lock within {FLOCK_TIMEOUT_SECONDS}s"
                ) from e
            time.sleep(FLOCK_POLL_SECONDS)


@dataclass
class WorkerState:
    label: str
    mode: str                              # interactive | headless | scheduled
    tmux_session: str
    tmux_window: str
    cwd: str
    claude_bin: str
    prompt_file: str
    tmux_log: str
    launchd_label: Optional[str]
    plist_path: Optional[str]
    session_id: Optional[str]
    jsonl_path: Optional[str]
    created_at: str
    started_at: Optional[str]
    ended_at: Optional[str]
    last_send_at: Optional[str]
    last_capture_at: Optional[str]


_FIELD_NAMES = {f.name for f in fields(WorkerState)}


def worker_dir(workdir: Path, label: str) -> Path:
    return workdir / label


def state_path(workdir: Path, label: str) -> Path:
    return worker_dir(workdir, label) / "state.json"


def create(
    workdir: Path,
    *,
    label: str,
    mode: str,
    cwd: Path,
    claude_bin: str,
    tmux_session: str,
    tmux_window: Optional[str] = None,
    launchd_label: Optional[str] = None,
    plist_path: Optional[str] = None,
) -> WorkerState:
    wd = worker_dir(workdir, label)
    if state_path(workdir, label).exists():
        raise LabelExists(label)
    wd.mkdir(parents=True, exist_ok=True)
    s = WorkerState(
        label=label,
        mode=mode,
        tmux_session=tmux_session,
        tmux_window=tmux_window or label,
        cwd=str(cwd),
        claude_bin=claude_bin,
        prompt_file=str(wd / "prompt.md"),
        tmux_log=str(wd / "tmux.log"),
        launchd_label=launchd_label,
        plist_path=plist_path,
        session_id=None,
        jsonl_path=None,
        created_at=utc_now_iso(),
        started_at=None,
        ended_at=None,
        last_send_at=None,
        last_capture_at=None,
    )
    _write_locked(state_path(workdir, label), s)
    return s


def read(workdir: Path, label: str) -> WorkerState:
    p = state_path(workdir, label)
    if not p.exists():
        raise WorkerNotFound(label)
    with open(p, "r", encoding="utf-8") as f:
        _acquire_flock(f.fileno(), fcntl.LOCK_SH)
        try:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise WorkerNotFound(label) from e
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return WorkerState(**{k: data.get(k) for k in _FIELD_NAMES})


def update(workdir: Path, label: str, **changes) -> WorkerState:
    unknown = set(changes) - _FIELD_NAMES
    if unknown:
        raise ValueError(f"unknown state fields: {sorted(unknown)}")
    p = state_path(workdir, label)
    if not p.exists():
        raise WorkerNotFound(label)
    with open(p, "r+", encoding="utf-8") as f:
        _acquire_flock(f.fileno(), fcntl.LOCK_EX)
        try:
            data = json.load(f)
            data.update(changes)
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return WorkerState(**{k: data.get(k) for k in _FIELD_NAMES})


def _write_locked(path: Path, s: WorkerState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        _acquire_flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as f:
            json.dump(asdict(s), f, indent=2, sort_keys=False)
            f.write("\n")
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def list_all(workdir: Path) -> list[WorkerState]:
    """Return all workers present in workdir, in arbitrary order."""
    if not workdir.exists():
        return []
    out: list[WorkerState] = []
    for child in workdir.iterdir():
        if not child.is_dir():
            continue
        if not (child / "state.json").exists():
            continue
        try:
            out.append(read(workdir, child.name))
        except (json.JSONDecodeError, WorkerNotFound):
            continue
    return out
