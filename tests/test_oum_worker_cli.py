from __future__ import annotations

import json as _json
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


# ---------- skeleton tests (T15) ----------

def test_help_lists_all_verbs():
    r = _run_cli("--help")
    assert r.returncode == 0
    for verb in ["spawn", "schedule", "send", "capture", "wait", "ask",
                 "list", "status", "kill", "logs"]:
        assert verb in r.stdout, f"verb {verb!r} missing from --help: {r.stdout}"


def test_unknown_verb_exits_nonzero():
    r = _run_cli("nope")
    assert r.returncode != 0


# ---------- spawn (T16) ----------

@pytest.mark.skipif(TMUX_BIN is None, reason="tmux required")
def test_spawn_interactive_creates_window_and_state(tmp_path):
    stub = tmp_path / "stub-cc"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    try:
        r = _run_cli(
            "spawn",
            "--label", "spawn-test",
            "--new",
            "--prompt", "hello",
            "--cc-command", str(stub),
            "--tmux-session", TEST_TMUX_SESSION,
            "--cwd", str(tmp_path),
            "--logs-dir", str(tmp_path / "logs"),
        )
        assert r.returncode == 0, r.stderr
        sj = (tmp_path / "logs" / "spawn-test" / "state.json")
        assert sj.exists()
        out = subprocess.run([TMUX_BIN, "list-windows", "-t", TEST_TMUX_SESSION,
                              "-F", "#{window_name}"], capture_output=True, text=True)
        assert "spawn-test" in out.stdout
    finally:
        _cleanup_tmux()


def test_spawn_label_collision_exit_5(tmp_path):
    if TMUX_BIN is None:
        pytest.skip("tmux required")
    stub = tmp_path / "stub-cc"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    try:
        r1 = _run_cli("spawn", "--label", "dup", "--new", "--prompt", "hi",
                      "--cc-command", str(stub),
                      "--tmux-session", TEST_TMUX_SESSION,
                      "--cwd", str(tmp_path),
                      "--logs-dir", str(tmp_path / "logs"))
        assert r1.returncode == 0, r1.stderr
        r2 = _run_cli("spawn", "--label", "dup", "--new", "--prompt", "hi",
                      "--cc-command", str(stub),
                      "--tmux-session", TEST_TMUX_SESSION,
                      "--cwd", str(tmp_path),
                      "--logs-dir", str(tmp_path / "logs"))
        assert r2.returncode == 5
    finally:
        _cleanup_tmux()


# ---------- schedule (T17) ----------

def test_schedule_writes_plist_and_state(tmp_path):
    plist_dir = tmp_path / "LaunchAgents"
    r = _run_cli(
        "schedule",
        "--in", "1h",
        "--label", "sched-test",
        "--new",
        "--prompt", "scheduled hi",
        "--launch-agents-dir", str(plist_dir),
        "--no-bootstrap",
        "--cwd", str(tmp_path),
        "--tmux-session", TEST_TMUX_SESSION,
        "--logs-dir", str(tmp_path / "logs"),
    )
    assert r.returncode == 0, r.stderr
    sj = (tmp_path / "logs" / "sched-test" / "state.json")
    assert sj.exists()
    data = _json.loads(sj.read_text())
    assert data["mode"] == "scheduled"
    assert data["launchd_label"].endswith("sched-test")
    assert (plist_dir / (data["launchd_label"] + ".plist")).exists()


# ---------- send + capture (T18) ----------

@pytest.mark.skipif(TMUX_BIN is None, reason="tmux required")
def test_send_updates_last_send_at(tmp_path):
    stub = tmp_path / "stub-cc"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    try:
        r = _run_cli("spawn", "--label", "send-test", "--new", "--prompt", "hi",
                     "--cc-command", str(stub),
                     "--tmux-session", TEST_TMUX_SESSION,
                     "--cwd", str(tmp_path),
                     "--logs-dir", str(tmp_path / "logs"))
        assert r.returncode == 0, r.stderr
        time.sleep(0.3)
        r2 = _run_cli("send", "--label", "send-test", "hello-from-send",
                      "--tmux-session", TEST_TMUX_SESSION,
                      "--logs-dir", str(tmp_path / "logs"))
        assert r2.returncode == 0, r2.stderr
        data = _json.loads((tmp_path / "logs" / "send-test" / "state.json").read_text())
        assert data["last_send_at"] is not None
    finally:
        _cleanup_tmux()


def test_capture_returns_empty_when_jsonl_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    _state.create(workdir, label="cap-test", mode="interactive", cwd=tmp_path,
                  claude_bin="cc", tmux_session="x")
    r = _run_cli("capture", "--label", "cap-test",
                 "--logs-dir", str(workdir),
                 env={**os.environ, "HOME": str(tmp_path / "fake-home")})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# ---------- wait (T19) ----------

def test_wait_returns_zero_when_jsonl_already_terminal(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    from oum_worker.jsonl import encode_cwd
    cwd = tmp_path / "proj"
    cwd.mkdir()
    proj = fake_home / ".claude" / "projects" / encode_cwd(cwd)
    proj.mkdir(parents=True)
    sid = "deadbeef-dead-beef-dead-beefdeadbeef"
    src = ROOT / "tests" / "fixtures" / "oum_worker" / "conversation_simple.jsonl"
    (proj / f"{sid}.jsonl").write_text(src.read_text())

    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    # mode=headless so wait's alive_check doesn't require a real tmux window
    _state.create(workdir, label="wait-test", mode="headless", cwd=cwd,
                  claude_bin="cc", tmux_session="x")
    _state.update(workdir, "wait-test", session_id=sid,
                  jsonl_path=str(proj / f"{sid}.jsonl"),
                  last_send_at="2026-05-06T10:35:00.000Z")

    r = _run_cli("wait", "--label", "wait-test",
                 "--timeout", "3", "--stable-ms", "200", "--poll-ms", "100",
                 "--logs-dir", str(workdir),
                 env={**os.environ, "HOME": str(fake_home)})
    assert r.returncode == 0, r.stderr


# ---------- list + status (T20) ----------

def test_list_shows_known_labels(tmp_path):
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    _state.create(workdir, label="alpha", mode="interactive", cwd=tmp_path,
                  claude_bin="cc", tmux_session="x")
    _state.create(workdir, label="beta", mode="headless", cwd=tmp_path,
                  claude_bin="cc", tmux_session="x")
    r = _run_cli("list", "--logs-dir", str(workdir))
    assert r.returncode == 0
    assert "alpha" in r.stdout and "beta" in r.stdout


def test_status_for_known_label(tmp_path):
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    _state.create(workdir, label="solo", mode="interactive", cwd=tmp_path,
                  claude_bin="cc", tmux_session="x")
    r = _run_cli("status", "--label", "solo", "--logs-dir", str(workdir))
    assert r.returncode == 0
    assert "solo" in r.stdout


def test_status_unknown_label_exit_1(tmp_path):
    (tmp_path / "logs").mkdir()
    r = _run_cli("status", "--label", "nope", "--logs-dir", str(tmp_path / "logs"))
    assert r.returncode == 1


# ---------- kill + logs (T21) ----------

@pytest.mark.skipif(TMUX_BIN is None, reason="tmux required")
def test_kill_marks_ended_and_removes_window(tmp_path):
    stub = tmp_path / "stub-cc"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    try:
        _run_cli("spawn", "--label", "kill-test", "--new", "--prompt", "hi",
                 "--cc-command", str(stub),
                 "--tmux-session", TEST_TMUX_SESSION,
                 "--cwd", str(tmp_path),
                 "--logs-dir", str(tmp_path / "logs"))
        time.sleep(0.3)
        r = _run_cli("kill", "--label", "kill-test",
                     "--tmux-session", TEST_TMUX_SESSION,
                     "--logs-dir", str(tmp_path / "logs"))
        assert r.returncode == 0, r.stderr
        out = subprocess.run([TMUX_BIN, "list-windows", "-t", TEST_TMUX_SESSION,
                              "-F", "#{window_name}"], capture_output=True, text=True)
        assert "kill-test" not in out.stdout
        data = _json.loads((tmp_path / "logs" / "kill-test" / "state.json").read_text())
        assert data["ended_at"] is not None
    finally:
        _cleanup_tmux()


def test_logs_prints_path(tmp_path):
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    s = _state.create(workdir, label="logs-test", mode="interactive", cwd=tmp_path,
                      claude_bin="cc", tmux_session="x")
    r = _run_cli("logs", "--label", "logs-test", "--logs-dir", str(workdir))
    assert r.returncode == 0
    assert s.tmux_log in r.stdout
