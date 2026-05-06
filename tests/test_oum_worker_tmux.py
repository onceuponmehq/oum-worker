from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import tmux  # noqa: E402

TMUX_BIN = shutil.which("tmux")
SESSION = "oum-worker-test"


def _have_tmux() -> bool:
    return TMUX_BIN is not None


pytestmark = pytest.mark.skipif(not _have_tmux(), reason="tmux not installed")


@pytest.fixture(autouse=True)
def _cleanup_session():
    yield
    if _have_tmux():
        subprocess.run([TMUX_BIN, "kill-session", "-t", SESSION],
                       check=False, capture_output=True)


def test_find_tmux_bin_returns_path():
    p = tmux.find_tmux_bin()
    assert p.exists()


def test_ensure_session_creates_when_missing():
    tmux.ensure_session(SESSION)
    out = subprocess.run([TMUX_BIN, "list-sessions"],
                         capture_output=True, text=True)
    assert SESSION in out.stdout


def test_window_exists_after_ensure():
    tmux.ensure_session(SESSION)
    # Default tmux session has one window; pick its name.
    out = subprocess.run([TMUX_BIN, "list-windows", "-t", SESSION, "-F", "#{window_name}"],
                         capture_output=True, text=True)
    win = out.stdout.strip().splitlines()[0]
    assert tmux.window_exists(SESSION, win) is True
    assert tmux.window_exists(SESSION, "no-such-window") is False


def test_kill_window_removes_target():
    tmux.ensure_session(SESSION)
    subprocess.run([TMUX_BIN, "new-window", "-t", f"{SESSION}:", "-n", "throwaway"],
                   check=True)
    assert tmux.window_exists(SESSION, "throwaway") is True
    tmux.kill_window(SESSION, "throwaway")
    time.sleep(0.1)
    assert tmux.window_exists(SESSION, "throwaway") is False


def test_open_window_runs_command_and_remains_on_exit(tmp_path):
    tmux.ensure_session(SESSION)
    log = tmp_path / "out.log"
    tmux.open_window(
        session=SESSION,
        window="testwin",
        cwd=tmp_path,
        command='/bin/zsh -c "echo hello-from-window > out.log"',
        log_path=log,
    )
    # The command writes to a file in the worker's cwd; give it a moment.
    time.sleep(0.5)
    assert (tmp_path / "out.log").read_text().strip() == "hello-from-window"
    assert tmux.window_exists(SESSION, "testwin") is True   # remain-on-exit on
