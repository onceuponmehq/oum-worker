from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import engines, jsonl, codex_jsonl  # noqa: E402


# --- factory ------------------------------------------------------------------


def test_get_returns_claude_for_claude():
    e = engines.get("claude")
    assert e.name == "claude"
    assert e.default_binary == "claude"
    assert e.yolo_default is False
    assert e.jsonl_module is jsonl


def test_get_returns_codex_for_codex():
    e = engines.get("codex")
    assert e.name == "codex"
    assert e.default_binary == "codex"
    assert e.yolo_default is True
    assert e.jsonl_module is codex_jsonl


def test_get_unknown_raises():
    with pytest.raises(ValueError, match="unknown engine"):
        engines.get("gpt-engineer")


# --- ClaudeEngine.build_invocation -------------------------------------------


def test_claude_invocation_with_prompt(tmp_path):
    p = tmp_path / "prompt.md"
    cmd = engines.get("claude").build_invocation(
        binary="claude",
        prompt_file=p,
        headless=False,
        resume=None, session_name=None, model=None,
        yolo=False, permission_mode=None, cwd=tmp_path,
    )
    assert "$(cat" in cmd
    assert str(p) in cmd
    assert cmd.startswith("claude ")


def test_claude_invocation_cold_start():
    cmd = engines.get("claude").build_invocation(
        binary="claude",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model=None,
        yolo=False, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "$(cat" not in cmd
    assert cmd.strip() == "claude"


def test_claude_invocation_yolo_adds_skip_permissions():
    cmd = engines.get("claude").build_invocation(
        binary="claude",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "--dangerously-skip-permissions" in cmd


def test_claude_invocation_headless_uses_p_flag(tmp_path):
    p = tmp_path / "prompt.md"
    cmd = engines.get("claude").build_invocation(
        binary="claude",
        prompt_file=p, headless=True,
        resume="abc-123", session_name=None, model=None,
        yolo=False, permission_mode=None, cwd=tmp_path,
    )
    assert "claude -p" in cmd
    assert "--resume" in cmd
    assert "abc-123" in cmd


# --- CodexEngine.build_invocation --------------------------------------------


def test_codex_invocation_cold_start_has_yolo():
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=Path("/tmp/work"),
    )
    assert cmd.startswith("codex ")
    assert "--yolo" in cmd
    assert "-C /tmp/work" in cmd
    assert "$(cat" not in cmd


def test_codex_invocation_with_prompt(tmp_path):
    p = tmp_path / "prompt.md"
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=p, headless=False,
        resume=None, session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=tmp_path,
    )
    assert "$(cat" in cmd
    assert str(p) in cmd


def test_codex_invocation_no_yolo_omits_flag():
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model=None,
        yolo=False, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "--yolo" not in cmd


def test_codex_invocation_headless(tmp_path):
    p = tmp_path / "prompt.md"
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=p, headless=True,
        resume=None, session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=tmp_path,
    )
    assert cmd.startswith("codex exec ")
    assert "$(cat" in cmd


def test_codex_invocation_headless_requires_prompt():
    with pytest.raises(ValueError, match="codex headless requires a prompt"):
        engines.get("codex").build_invocation(
            binary="codex",
            prompt_file=None, headless=True,
            resume=None, session_name=None, model=None,
            yolo=True, permission_mode=None, cwd=Path("/tmp"),
        )


def test_codex_invocation_resume_subcommand_position():
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume="abc-123", session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "codex resume" in cmd
    assert "abc-123" in cmd


def test_codex_invocation_headless_resume_uses_exec_resume():
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=Path("/tmp/p.md"), headless=True,
        resume="abc-123", session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "codex exec resume abc-123" in cmd


def test_codex_invocation_resume_omits_cwd_flag():
    """`codex exec resume` rejects -C <cwd>; resumed sessions inherit the
    original session's cwd from session_meta. The builder must NOT pass -C
    when resume is set, even though it does for fresh `codex exec` spawns.
    """
    cmd_resume = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume="abc-123", session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=Path("/tmp/xyz"),
    )
    assert "-C" not in cmd_resume, cmd_resume
    assert "/tmp/xyz" not in cmd_resume, cmd_resume

    # Sanity: fresh spawn DOES still pass -C.
    cmd_fresh = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model=None,
        yolo=True, permission_mode=None, cwd=Path("/tmp/xyz"),
    )
    assert "-C" in cmd_fresh, cmd_fresh
    assert "/tmp/xyz" in cmd_fresh, cmd_fresh


def test_codex_invocation_model_passes_m_flag():
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model="gpt-5",
        yolo=True, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "-m gpt-5" in cmd
