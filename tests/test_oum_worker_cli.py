from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

TMUX_BIN = shutil.which("tmux")
TEST_TMUX_SESSION = "oum-worker-cli-test"


def _run_cli(*args, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "oum_worker.cli", *args],
        cwd=str(cwd or ROOT / "scripts"),
        capture_output=True, text=True, env=env,
    )


def _cleanup_tmux():
    if TMUX_BIN:
        subprocess.run([TMUX_BIN, "kill-session", "-t", TEST_TMUX_SESSION],
                       check=False, capture_output=True)


def test_help_lists_all_verbs():
    r = _run_cli("--help")
    assert r.returncode == 0
    for verb in ["spawn", "schedule", "send", "capture", "wait", "ask",
                 "list", "status", "kill", "logs"]:
        assert verb in r.stdout, f"verb {verb!r} missing from --help: {r.stdout}"


def test_unknown_verb_exits_nonzero():
    r = _run_cli("nope")
    assert r.returncode != 0
