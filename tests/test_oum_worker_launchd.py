from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import launchd  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def test_parse_delay_compound():
    assert launchd.parse_delay("3h") == 10_800
    assert launchd.parse_delay("1h30m") == 5_400
    assert launchd.parse_delay("2d 4h 15m") == 188_100


def test_parse_delay_rejects_no_unit():
    with pytest.raises(ValueError):
        launchd.parse_delay("90")


def test_parse_target_delay_rounds_up():
    now = datetime(2026, 5, 6, 10, 5, 20, tzinfo=IST)
    assert launchd.parse_target("3h", None, now=now) == datetime(2026, 5, 6, 13, 6, tzinfo=IST)


def test_parse_target_time_rolls_to_tomorrow():
    now = datetime(2026, 5, 6, 18, 0, tzinfo=IST)
    assert launchd.parse_target(None, "17:30", now=now) == datetime(2026, 5, 7, 17, 30, tzinfo=IST)


def test_normalize_label_adds_prefix():
    assert launchd.normalize_label("nightly").startswith("com.oum.schedule.")


def test_normalize_label_preserves_explicit_com_prefix():
    assert launchd.normalize_label("com.acme.x") == "com.acme.x"


def test_resolve_workdir_known_alias(tmp_path):
    """Known repo aliases resolve to the OUM monorepo paths."""
    p = launchd.resolve_workdir(repo="oum-os", cwd=None)
    assert p.name == "oum-os"


import plistlib


def test_build_plist_contains_calendar_interval(tmp_path):
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)
    payload = launchd.build_plist(
        label="com.oum.schedule.demo",
        cwd=tmp_path,
        command="echo hi",
        target=target,
        stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
    )
    parsed = plistlib.loads(payload)
    assert parsed["Label"] == "com.oum.schedule.demo"
    assert parsed["StartCalendarInterval"]["Hour"] == 17
    assert parsed["StartCalendarInterval"]["Minute"] == 30
    assert parsed["LaunchOnlyOnce"] is True
    assert parsed["AbandonProcessGroup"] is True


def test_write_plist_refuses_overwrite_without_replace(tmp_path):
    p = tmp_path / "x.plist"
    launchd.write_plist(p, b"<plist></plist>", replace=False)
    with pytest.raises(FileExistsError):
        launchd.write_plist(p, b"<plist></plist>", replace=False)
    launchd.write_plist(p, b"<plist></plist>", replace=True)  # no error


def test_build_inner_command_calls_mark_started_then_cc(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path,
        claude_bin="cc",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "tmux.log",
        label="demo",
        logs_dir=tmp_path / "logs",
        resume=None,
        new_session=True,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        tmux_session="oum-worker-test",
        headless=False,
    )
    assert "_inner mark-started" in cmd or "runner mark-started" in cmd
    assert "--label demo" in cmd or "'demo'" in cmd
    assert cmd.index("mark-started") < cmd.index("cc ")


def test_build_inner_command_headless_uses_claude_p(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path,
        claude_bin="claude",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "out.txt",
        label="hl",
        logs_dir=tmp_path / "logs",
        resume="abc-123",
        new_session=False,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        tmux_session="oum-worker-test",
        headless=True,
    )
    assert "claude -p" in cmd
    assert "--resume" in cmd
    assert "abc-123" in cmd


def test_build_inner_command_sets_pythonpath_for_runner(tmp_path):
    """Without PYTHONPATH, `python3 -m oum_worker.runner` would die with
    ModuleNotFoundError after the inner command's `cd` runs."""
    cmd = launchd.build_inner_command(
        cwd=tmp_path,
        claude_bin="cc",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "tmux.log",
        label="ppath",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name=None,
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
    )
    assert "PYTHONPATH=" in cmd
    assert str(launchd.SCRIPTS_DIR) in cmd
    # PYTHONPATH must come before the runner module-load
    assert cmd.index("PYTHONPATH=") < cmd.index("oum_worker.runner")


def test_build_inner_command_headless_honors_claude_bin(tmp_path):
    """--cc-command should set the binary even in headless mode (was hardcoded `claude`)."""
    cmd = launchd.build_inner_command(
        cwd=tmp_path,
        claude_bin="/path/to/stub-cc",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "out.txt",
        label="hl-bin",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=False, session_name=None,
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=True,
    )
    assert "/path/to/stub-cc -p" in cmd
