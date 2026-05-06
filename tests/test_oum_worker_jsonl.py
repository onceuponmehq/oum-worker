from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import jsonl  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "oum_worker"


def test_encode_cwd_replaces_separators_with_dashes():
    p = Path("/Users/tushar/Documents/OnceUponMe/oum-os")
    assert jsonl.encode_cwd(p) == "-Users-tushar-Documents-OnceUponMe-oum-os"


def test_find_by_session_id_returns_path_when_present(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    proj = fake_home / ".claude" / "projects" / "-x-y"
    proj.mkdir(parents=True)
    target = proj / "abc-123.jsonl"
    target.write_text("{}\n")
    monkeypatch.setenv("HOME", str(fake_home))
    found = jsonl.find_by_session_id(Path("/x/y"), "abc-123")
    assert found == target


def test_find_by_session_id_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert jsonl.find_by_session_id(Path("/x/y"), "missing") is None


def test_encode_cwd_handles_nonexistent_path():
    """resolve() should still produce a stable encoding even for paths that don't exist."""
    p = Path("/nonexistent/x/y")
    assert jsonl.encode_cwd(p) == "-nonexistent-x-y"


def test_projects_dir_raises_when_home_unset(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(RuntimeError, match="HOME"):
        jsonl.projects_dir()


def test_discover_by_prompt_returns_matching_session(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    proj = fake_home / ".claude" / "projects" / jsonl.encode_cwd(Path("/x"))
    proj.mkdir(parents=True)
    sid_a = proj / "11111111-1111-1111-1111-111111111111.jsonl"
    sid_b = proj / "22222222-2222-2222-2222-222222222222.jsonl"
    sid_a.write_text('{"type":"user","timestamp":"2026-05-06T10:00:00.000Z","message":{"role":"user","content":"different"}}\n')
    sid_b.write_text('{"type":"user","timestamp":"2026-05-06T10:00:00.000Z","message":{"role":"user","content":"hi"}}\n')
    monkeypatch.setenv("HOME", str(fake_home))

    found = jsonl.discover_by_prompt(Path("/x"), "hi", created_at="2026-05-06T10:00:00.000Z")
    assert found == "22222222-2222-2222-2222-222222222222"


def test_discover_by_prompt_returns_none_on_no_match(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    proj = fake_home / ".claude" / "projects" / jsonl.encode_cwd(Path("/x"))
    proj.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    assert jsonl.discover_by_prompt(Path("/x"), "hi", created_at="2026-05-06T10:00:00.000Z") is None


def test_discover_by_prompt_uses_mtime_tiebreaker(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    proj = fake_home / ".claude" / "projects" / jsonl.encode_cwd(Path("/x"))
    proj.mkdir(parents=True)
    sid_old = proj / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl"
    sid_new = proj / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.jsonl"
    line = '{"type":"user","timestamp":"2026-05-06T10:00:00.000Z","message":{"role":"user","content":"hi"}}\n'
    sid_old.write_text(line); sid_new.write_text(line)
    # Set mtimes: sid_old far away, sid_new very close to created_at
    import os as _os
    # 2026-05-06T10:00:00Z = 1778061600 unix. sid_new ~1s away, sid_old ancient (Nov 2023).
    _os.utime(sid_old, (1700000000, 1700000000))
    _os.utime(sid_new, (1778061601, 1778061601))
    monkeypatch.setenv("HOME", str(fake_home))

    found = jsonl.discover_by_prompt(Path("/x"), "hi", created_at="2026-05-06T10:00:00.000Z")
    assert found == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_discover_by_prompt_returns_none_when_all_candidates_outside_window(tmp_path, monkeypatch):
    """If multiple sessions match the prompt but none are within 300s of created_at,
    refuse to guess (return None) — caller must pass --session-id explicitly."""
    fake_home = tmp_path / "home"
    proj = fake_home / ".claude" / "projects" / jsonl.encode_cwd(Path("/x"))
    proj.mkdir(parents=True)
    sid_a = proj / "11111111-1111-1111-1111-111111111111.jsonl"
    sid_b = proj / "22222222-2222-2222-2222-222222222222.jsonl"
    line = '{"type":"user","timestamp":"2026-05-06T10:00:00.000Z","message":{"role":"user","content":"hi"}}\n'
    sid_a.write_text(line); sid_b.write_text(line)
    import os as _os
    # Both files: ancient mtimes, far outside 300s window
    _os.utime(sid_a, (1700000000, 1700000000))
    _os.utime(sid_b, (1700000100, 1700000100))
    monkeypatch.setenv("HOME", str(fake_home))

    found = jsonl.discover_by_prompt(Path("/x"), "hi", created_at="2026-05-06T10:00:00.000Z")
    assert found is None


def test_discover_by_prompt_returns_only_match_even_outside_window(tmp_path, monkeypatch):
    """Single candidate outside the 300s window still returns it — only ambiguity matters."""
    fake_home = tmp_path / "home"
    proj = fake_home / ".claude" / "projects" / jsonl.encode_cwd(Path("/x"))
    proj.mkdir(parents=True)
    sid = proj / "33333333-3333-3333-3333-333333333333.jsonl"
    sid.write_text('{"type":"user","timestamp":"2026-05-06T10:00:00.000Z","message":{"role":"user","content":"hi"}}\n')
    import os as _os
    _os.utime(sid, (1700000000, 1700000000))  # ancient, outside window
    monkeypatch.setenv("HOME", str(fake_home))

    found = jsonl.discover_by_prompt(Path("/x"), "hi", created_at="2026-05-06T10:00:00.000Z")
    assert found == "33333333-3333-3333-3333-333333333333"


def test_wait_for_idle_detects_end_turn_after_stability(tmp_path):
    """Replay the simple conversation: file already complete; wait sees the latest end_turn + stability."""
    src = FIXTURES / "conversation_simple.jsonl"
    target = tmp_path / "session.jsonl"
    target.write_text(src.read_text())
    # last_send_at chosen between the two user messages
    result = jsonl.wait_for_idle(
        target,
        last_send_at="2026-05-06T10:35:00.000Z",
        timeout=5.0,
        stable_ms=200,
        poll_ms=50,
    )
    assert result.idle is True
    assert result.last_assistant_text == "Doing well, thanks. Ready to help."


def test_wait_for_idle_times_out_when_no_new_lines(tmp_path):
    target = tmp_path / "empty.jsonl"
    target.write_text('{"type":"user","timestamp":"2026-05-06T10:00:00.000Z","message":{"role":"user","content":"hi"}}\n')
    result = jsonl.wait_for_idle(
        target,
        last_send_at="2026-05-06T11:00:00.000Z",  # in the future relative to file
        timeout=0.5,
        stable_ms=200,
        poll_ms=50,
    )
    assert result.idle is False
    assert result.timed_out is True


def test_extract_response_default_strips_thinking_and_tools(tmp_path):
    src = FIXTURES / "conversation_with_tools.jsonl"
    out = jsonl.extract_response(src, since="2026-05-06T10:59:00.000Z")
    assert out == "File checked, all good."


def test_extract_response_include_thinking(tmp_path):
    src = FIXTURES / "conversation_with_tools.jsonl"
    out = jsonl.extract_response(src, since="2026-05-06T10:59:00.000Z", include_thinking=True)
    assert "need to read" in out
    assert "File checked" in out


def test_extract_response_include_tool_use(tmp_path):
    src = FIXTURES / "conversation_with_tools.jsonl"
    out = jsonl.extract_response(src, since="2026-05-06T10:59:00.000Z", include_tool_use=True)
    assert "Read" in out  # tool name surfaced
    assert "File checked" in out


def test_dump_events_returns_raw_jsonl_after_since(tmp_path):
    src = FIXTURES / "conversation_with_tools.jsonl"
    target = tmp_path / "session.jsonl"
    target.write_text(src.read_text())
    out = jsonl.dump_events(target, since="2026-05-06T11:00:02.500Z")
    lines = out.splitlines()
    # Lines included: tool_result user (11:00:03), assistant text (11:00:04).
    assert len(lines) == 2
    assert "tool_result" in out
    assert "File checked, all good." in out
    # Excluded: lines 1-3 (initial user, thinking, tool_use call).
    assert "msg_T1" not in out
    assert "11:00:00" not in out


def test_dump_events_returns_empty_when_file_missing(tmp_path):
    assert jsonl.dump_events(tmp_path / "missing.jsonl", since="2026-05-06T10:00:00.000Z") == ""
