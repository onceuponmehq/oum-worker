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
    _os.utime(sid_old, (1700000000, 1700000000))           # ancient
    _os.utime(sid_new, (1746524400, 1746524400))           # close to 2026-05-06T10:00:00 UTC
    monkeypatch.setenv("HOME", str(fake_home))

    found = jsonl.discover_by_prompt(Path("/x"), "hi", created_at="2026-05-06T10:00:00.000Z")
    assert found == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
