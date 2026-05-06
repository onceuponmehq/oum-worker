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
