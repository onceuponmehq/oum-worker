from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import state  # noqa: E402


def test_create_and_read_roundtrip(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    s = state.create(
        workdir,
        label="alpha",
        mode="interactive",
        cwd=Path("/somewhere"),
        claude_bin="cc",
        tmux_session="oum",
    )
    assert s.label == "alpha"
    loaded = state.read(workdir, "alpha")
    assert loaded.label == "alpha"
    assert loaded.mode == "interactive"
    assert loaded.cwd == "/somewhere"
    assert loaded.created_at.endswith("Z")


def test_read_missing_label_raises(tmp_path):
    with pytest.raises(state.WorkerNotFound):
        state.read(tmp_path, "nope")


def test_update_mutates_under_flock(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="a", mode="interactive", cwd=Path("/x"),
                 claude_bin="cc", tmux_session="oum")
    state.update(workdir, "a", last_send_at="2026-05-06T10:00:00.000Z")
    s = state.read(workdir, "a")
    assert s.last_send_at == "2026-05-06T10:00:00.000Z"


def test_concurrent_updates_serialize(tmp_path):
    """Two threads updating different fields should both persist."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="a", mode="interactive", cwd=Path("/x"),
                 claude_bin="cc", tmux_session="oum")

    def setter(field, value):
        state.update(workdir, "a", **{field: value})

    t1 = threading.Thread(target=setter, args=("last_send_at", "2026-05-06T10:00:00.000Z"))
    t2 = threading.Thread(target=setter, args=("last_capture_at", "2026-05-06T10:01:00.000Z"))
    t1.start(); t2.start(); t1.join(); t2.join()

    s = state.read(workdir, "a")
    assert s.last_send_at == "2026-05-06T10:00:00.000Z"
    assert s.last_capture_at == "2026-05-06T10:01:00.000Z"


def test_label_collision_raises(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="a", mode="interactive", cwd=Path("/x"),
                 claude_bin="cc", tmux_session="oum")
    with pytest.raises(state.LabelExists):
        state.create(workdir, label="a", mode="interactive", cwd=Path("/x"),
                     claude_bin="cc", tmux_session="oum")


def test_read_corrupt_json_raises_worker_not_found(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    s = state.create(workdir, label="bad", mode="interactive", cwd=Path("/x"),
                     claude_bin="cc", tmux_session="oum")
    # Corrupt the state.json
    Path(s.prompt_file).parent.joinpath("state.json").write_text("{not valid json")
    with pytest.raises(state.WorkerNotFound):
        state.read(workdir, "bad")


def test_list_all_returns_known_workers(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="a", mode="interactive", cwd=Path("/x"),
                 claude_bin="cc", tmux_session="oum")
    state.create(workdir, label="b", mode="headless", cwd=Path("/y"),
                 claude_bin="cc", tmux_session="oum")
    labels = sorted(s.label for s in state.list_all(workdir))
    assert labels == ["a", "b"]


def test_list_all_skips_directories_without_state_json(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "stray").mkdir()  # directory but no state.json
    assert state.list_all(workdir) == []


def test_flock_timeout_raises_state_busy(tmp_path):
    """If another process holds LOCK_EX past FLOCK_TIMEOUT_SECONDS,
    the next caller raises StateBusy instead of hanging forever."""
    import fcntl
    import multiprocessing
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="busy", mode="interactive", cwd=Path("/x"),
                 claude_bin="cc", tmux_session="oum")
    p = state.state_path(workdir, "busy")

    def _hold(path_str, ready_event, release_event):
        fd = open(path_str, "r+")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        ready_event.set()
        release_event.wait(timeout=10)
        fd.close()

    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    release = ctx.Event()
    proc = ctx.Process(target=_hold, args=(str(p), ready, release))
    proc.start()
    try:
        ready.wait(timeout=2)
        # Shorten the timeout for the test
        original = state.FLOCK_TIMEOUT_SECONDS
        state.FLOCK_TIMEOUT_SECONDS = 0.3
        try:
            with pytest.raises(state.StateBusy):
                state.update(workdir, "busy", last_send_at="2026-05-06T10:00:00.000Z")
        finally:
            state.FLOCK_TIMEOUT_SECONDS = original
    finally:
        release.set()
        proc.join(timeout=2)


def test_utc_now_iso_uses_single_now_call():
    """The function should compute the seconds and millis from one datetime instance."""
    # Indirect check: the format string is well-formed and the result parses
    # back as a single timestamp. (The bug was a millisecond rollover where
    # seconds and millis disagreed; we can't deterministically reproduce that,
    # but we can verify the result is always a parseable UTC ISO 8601.)
    from datetime import datetime as _dt
    s = state.utc_now_iso()
    assert s.endswith("Z")
    parsed = _dt.fromisoformat(s.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
