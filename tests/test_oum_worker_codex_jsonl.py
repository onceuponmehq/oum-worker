from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import codex_jsonl  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "oum_worker"
SIMPLE = FIXTURES / "codex_simple.jsonl"
WITH_TOOLS = FIXTURES / "codex_with_tools.jsonl"
SIMPLE_SID = "019e0500-0000-0000-0000-000000000001"
TOOLS_SID = "019e0500-0000-0000-0000-000000000002"
FIXTURE_CWD_PLACEHOLDER = "/tmp/codex-test-cwd"


def _materialize_codex_home(tmp_home: Path, fixture_path: Path,
                             sid: str, *, real_cwd: Path,
                             day: str = "2026/05/08") -> Path:
    """Copy a fixture into ~/.codex/sessions/<day>/rollout-<ts>-<sid>.jsonl
    under tmp_home, rewriting the placeholder cwd in the fixture to
    str(real_cwd.resolve()) so cross-platform tests match (/tmp →
    /private/tmp on macOS).
    """
    sessions_dir = tmp_home / ".codex" / "sessions" / day
    sessions_dir.mkdir(parents=True, exist_ok=True)
    target = sessions_dir / f"rollout-2026-05-08T10-00-00-{sid}.jsonl"
    raw = fixture_path.read_text()
    raw = raw.replace(FIXTURE_CWD_PLACEHOLDER, str(real_cwd.resolve()))
    target.write_text(raw, encoding="utf-8")
    return target


def test_find_by_session_id_returns_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real_cwd = tmp_path / "real-cwd"
    real_cwd.mkdir()
    target = _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID,
                                      real_cwd=real_cwd)
    result = codex_jsonl.find_by_session_id(real_cwd, SIMPLE_SID)
    assert result == target


def test_find_by_session_id_returns_none_when_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real_cwd = tmp_path / "real-cwd"
    real_cwd.mkdir()
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID, real_cwd=real_cwd)
    assert codex_jsonl.find_by_session_id(real_cwd, "no-such-id") is None


def test_discover_by_prompt_finds_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real_cwd = tmp_path / "real-cwd"
    real_cwd.mkdir()
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID, real_cwd=real_cwd)
    sid = codex_jsonl.discover_by_prompt(
        real_cwd, "hello codex",
        created_at="2026-05-08T10:00:30.000Z",
    )
    assert sid == SIMPLE_SID


def test_discover_by_prompt_returns_none_for_wrong_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real_cwd = tmp_path / "real-cwd"
    real_cwd.mkdir()
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID, real_cwd=real_cwd)
    assert codex_jsonl.discover_by_prompt(
        other_cwd, "hello codex",
        created_at="2026-05-08T10:00:30.000Z",
    ) is None


def test_discover_by_prompt_returns_none_for_wrong_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real_cwd = tmp_path / "real-cwd"
    real_cwd.mkdir()
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID, real_cwd=real_cwd)
    assert codex_jsonl.discover_by_prompt(
        real_cwd, "different prompt",
        created_at="2026-05-08T10:00:30.000Z",
    ) is None


# ---------- wait_for_idle ----------


def test_wait_returns_idle_after_task_complete(tmp_path):
    """JSONL already has a task_complete after last_send_at; wait_for_idle
    returns idle once the stable window passes."""
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(SIMPLE.read_text(), encoding="utf-8")
    result = codex_jsonl.wait_for_idle(
        jsonl_path,
        last_send_at="2026-05-08T10:00:30.000Z",
        timeout=2.0, stable_ms=200, poll_ms=50,
    )
    assert result.idle is True
    assert result.timed_out is False
    assert result.last_stop_reason == "task_complete"


def test_wait_times_out_when_no_task_complete(tmp_path):
    """JSONL has only task_started, no task_complete after last_send_at."""
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(
        '{"timestamp":"2026-05-08T10:00:00.000Z","type":"session_meta","payload":{"id":"x","cwd":"/x"}}\n'
        '{"timestamp":"2026-05-08T10:01:00.000Z","type":"event_msg","payload":{"type":"task_started"}}\n',
        encoding="utf-8",
    )
    result = codex_jsonl.wait_for_idle(
        jsonl_path,
        last_send_at="2026-05-08T10:00:30.000Z",
        timeout=0.6, stable_ms=200, poll_ms=50,
    )
    assert result.idle is False
    assert result.timed_out is True


def test_wait_short_circuits_when_alive_check_false(tmp_path):
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text("", encoding="utf-8")
    calls = {"n": 0}

    def alive() -> bool:
        calls["n"] += 1
        return False

    result = codex_jsonl.wait_for_idle(
        jsonl_path,
        last_send_at="2026-05-08T10:00:30.000Z",
        timeout=2.0, stable_ms=200, poll_ms=50,
        alive_check=alive,
    )
    assert result.idle is False
    assert result.timed_out is False
    assert calls["n"] >= 1
