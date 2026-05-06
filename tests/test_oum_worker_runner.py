from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import state, runner  # noqa: E402


def test_mark_started_writes_timestamp(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="x", mode="scheduled", cwd=Path("/x"),
                 claude_bin="cc", tmux_session="oum")
    runner.mark_started(workdir, label="x")
    s = state.read(workdir, "x")
    assert s.started_at is not None
    assert s.started_at.endswith("Z")


def test_mark_started_via_module_main(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    state.create(workdir, label="y", mode="scheduled", cwd=Path("/y"),
                 claude_bin="cc", tmux_session="oum")
    r = subprocess.run(
        [sys.executable, "-m", "oum_worker.runner", "mark-started",
         "--label", "y", "--logs-dir", str(workdir)],
        cwd=str(ROOT / "scripts"),
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    s = state.read(workdir, "y")
    assert s.started_at is not None
