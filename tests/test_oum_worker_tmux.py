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
    deadline = time.time() + 5.0
    out_file = tmp_path / "out.log"
    while time.time() < deadline:
        if out_file.exists() and out_file.read_text().strip() == "hello-from-window":
            break
        time.sleep(0.05)
    else:
        pytest.fail("command output never appeared")
    assert tmux.window_exists(SESSION, "testwin") is True   # remain-on-exit on


def test_open_window_collision_without_replace_raises():
    tmux.ensure_session(SESSION)
    log = Path("/tmp/oum-worker-collision-test.log")
    tmux.open_window(
        session=SESSION, window="dup", cwd=Path("/tmp"),
        command='/bin/zsh -c "sleep 30"', log_path=log,
    )
    with pytest.raises(tmux.TmuxError, match="already exists"):
        tmux.open_window(
            session=SESSION, window="dup", cwd=Path("/tmp"),
            command='/bin/zsh -c "sleep 30"', log_path=log,
        )


def test_open_window_replace_succeeds_after_collision(tmp_path):
    tmux.ensure_session(SESSION)
    marker = tmp_path / "marker"
    log = tmp_path / "log"
    tmux.open_window(
        session=SESSION, window="reuse", cwd=tmp_path,
        command='/bin/zsh -c "sleep 30"', log_path=log,
    )
    # Replace and run a different command this time.
    tmux.open_window(
        session=SESSION, window="reuse", cwd=tmp_path,
        command=f'/bin/zsh -c "echo replaced > {marker}"',
        log_path=log,
        replace=True,
    )
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if marker.exists() and marker.read_text().strip() == "replaced":
            break
        time.sleep(0.05)
    else:
        pytest.fail("replaced window did not run new command")
    assert tmux.window_exists(SESSION, "reuse") is True


def test_open_window_pipe_pane_writes_log(tmp_path):
    tmux.ensure_session(SESSION)
    log = tmp_path / "pipe.log"
    tmux.open_window(
        session=SESSION, window="piped", cwd=tmp_path,
        command='/bin/zsh -c "echo unique-pipe-marker; sleep 5"',
        log_path=log,
    )
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if log.exists() and "unique-pipe-marker" in log.read_text():
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"pipe-pane log never received marker; got: {log.read_text() if log.exists() else '(no file)'}")


def test_send_text_inserts_into_window(tmp_path):
    """Use an interactive bash so typed lines are interpreted as commands."""
    tmux.ensure_session(SESSION)
    out_file = tmp_path / "received"
    tmux.open_window(
        session=SESSION,
        window="echo-win",
        cwd=tmp_path,
        command="/bin/bash --noprofile --norc -i",
        log_path=tmp_path / "log.out",
    )
    time.sleep(0.3)
    tmux.send_text(SESSION, "echo-win", f"echo hello-tmux > {out_file}")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if out_file.exists() and out_file.read_text().strip() == "hello-tmux":
            break
        time.sleep(0.05)
    else:
        pytest.fail("send_text payload never reached window")


def test_send_file_inserts_buffer_into_window(tmp_path):
    """Buffer contents pasted into an interactive shell are executed as commands."""
    tmux.ensure_session(SESSION)
    out_file = tmp_path / "received-file"
    payload = tmp_path / "payload.txt"
    payload.write_text(f"echo from-buffer > {out_file}\n")
    tmux.open_window(
        session=SESSION,
        window="buf-win",
        cwd=tmp_path,
        command="/bin/bash --noprofile --norc -i",
        log_path=tmp_path / "log.out",
    )
    time.sleep(0.3)
    tmux.send_file(SESSION, "buf-win", payload)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if out_file.exists() and "from-buffer" in out_file.read_text():
            break
        time.sleep(0.05)
    else:
        pytest.fail("send_file buffer never reached window")


def test_capture_pane_returns_text(tmp_path):
    tmux.ensure_session(SESSION)
    tmux.open_window(
        session=SESSION,
        window="cap-win",
        cwd=tmp_path,
        command='/bin/zsh -c "echo unique-marker-xyz; sleep 5"',
        log_path=tmp_path / "log.out",
    )
    deadline = time.time() + 3.0
    out = ""
    while time.time() < deadline:
        out = tmux.capture_pane(SESSION, "cap-win")
        if "unique-marker-xyz" in out:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"capture_pane never saw marker; got: {out[:200]}")
