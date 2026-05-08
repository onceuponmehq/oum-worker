from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import config, launchd  # noqa: E402

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
    assert launchd.normalize_label("nightly").startswith("com.agentworker.schedule.")


def test_normalize_label_preserves_explicit_com_prefix():
    assert launchd.normalize_label("com.acme.x") == "com.acme.x"


def test_resolve_workdir_known_alias(tmp_path):
    """Known repo aliases resolve through config, not hardcoded OUM paths."""
    cfg = config.WorkerConfig.defaults(default_cwd=tmp_path).with_updates(
        repo_aliases={"repo": tmp_path / "repo"}
    )
    p = launchd.resolve_workdir(repo="repo", cwd=None, cfg=cfg)
    assert p == tmp_path / "repo"


import plistlib


def test_build_plist_contains_calendar_interval(tmp_path):
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)
    payload = launchd.build_plist(
        label="com.example.worker.demo",
        cwd=tmp_path,
        command="echo hi",
        target=target,
        stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
    )
    parsed = plistlib.loads(payload)
    assert parsed["Label"] == "com.example.worker.demo"
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


# --- --env KEY=VALUE injection -------------------------------------------------


def test_parse_env_pairs_accepts_KEY_VALUE():
    assert launchd.parse_env_pairs(["FOO=bar"]) == {"FOO": "bar"}


def test_parse_env_pairs_handles_value_with_equals():
    parsed = launchd.parse_env_pairs(["URL=https://example.com/?a=1&b=2"])
    assert parsed == {"URL": "https://example.com/?a=1&b=2"}


def test_parse_env_pairs_refuses_reserved_TZ_PATH_LANG():
    for reserved in ("TZ", "PATH", "LANG"):
        with pytest.raises(ValueError, match="reserved"):
            launchd.parse_env_pairs([f"{reserved}=clobber"])


def test_parse_env_pairs_refuses_empty_key_or_no_equals():
    with pytest.raises(ValueError, match="missing '='"):
        launchd.parse_env_pairs(["NO_EQUALS_SIGN"])
    with pytest.raises(ValueError, match="empty key"):
        launchd.parse_env_pairs(["=value-only"])


def test_parse_env_pairs_returns_empty_for_none_or_empty():
    assert launchd.parse_env_pairs(None) == {}
    assert launchd.parse_env_pairs([]) == {}


def test_parse_env_pairs_later_value_overrides_earlier():
    assert launchd.parse_env_pairs(["FOO=first", "FOO=second"]) == {"FOO": "second"}


def test_build_plist_includes_user_env(tmp_path):
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)
    payload = launchd.build_plist(
        label="com.example.worker.envtest",
        cwd=tmp_path, command="echo hi", target=target,
        stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
        env_pairs={"FOO": "bar", "OUM_TASK_ID": "X"},
    )
    parsed = plistlib.loads(payload)
    env = parsed["EnvironmentVariables"]
    assert env["FOO"] == "bar"
    assert env["OUM_TASK_ID"] == "X"
    # Baseline keys still present.
    assert env["LANG"] == "en_US.UTF-8"
    assert "TZ" in env
    assert "PATH" in env


def test_build_plist_baseline_unaffected_when_env_pairs_omitted(tmp_path):
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)
    payload = launchd.build_plist(
        label="com.example.worker.no-env",
        cwd=tmp_path, command="echo hi", target=target,
        stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
    )
    parsed = plistlib.loads(payload)
    assert set(parsed["EnvironmentVariables"].keys()) == {"TZ", "PATH", "LANG"}


def test_build_plist_skips_reserved_keys_passed_via_env_pairs(tmp_path):
    """Defence in depth: even if a caller hands us TZ/PATH/LANG, ignore them."""
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)
    payload = launchd.build_plist(
        label="com.example.worker.reserved",
        cwd=tmp_path, command="echo hi", target=target,
        stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
        env_pairs={"TZ": "Etc/Mars", "FOO": "bar"},
    )
    env = plistlib.loads(payload)["EnvironmentVariables"]
    assert env["TZ"] != "Etc/Mars"  # baseline preserved
    assert env["FOO"] == "bar"


def test_build_inner_command_exports_user_env_before_cd(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path / "work",
        claude_bin="cc",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "tmux.log",
        label="envjob",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name="envjob",
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
        env_pairs={"FOO": "bar", "OUM_TASK_ID": "2026-05-06-001"},
    )
    assert "export FOO=bar" in cmd
    assert "export OUM_TASK_ID=2026-05-06-001" in cmd
    cd_token = f"cd {tmp_path / 'work'}"
    assert cmd.index("export FOO=bar") < cmd.index(cd_token)
    assert " && " in cmd  # exports are chained with cd


def test_build_inner_command_quotes_env_values_with_metacharacters(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path / "work",
        claude_bin="cc",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "tmux.log",
        label="metaenv",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name="metaenv",
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
        env_pairs={"X": "alpha;beta gamma"},
    )
    assert "alpha;beta gamma" in cmd
    # Bareword form (no shlex.quote) would let the inner shell split on the
    # space and execute `beta gamma` — confirm we do not emit it.
    assert "export X=alpha;beta gamma" not in cmd


def test_build_inner_command_without_env_pairs_unchanged(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path / "work",
        claude_bin="cc",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "tmux.log",
        label="plain",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name="plain",
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
    )
    assert "export " not in cmd


def test_build_inner_command_headless_exports_env_before_cd(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path / "work",
        claude_bin="claude",
        prompt_file=tmp_path / "p.md",
        log_path=tmp_path / "out.txt",
        label="hl-env",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name=None,
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=True,
        env_pairs={"OUM_TASK_ID": "2026-05-06-002"},
    )
    cd_token = f"cd {tmp_path / 'work'}"
    assert cmd.index("export OUM_TASK_ID=2026-05-06-002") < cmd.index(cd_token)


# --- Optional prompt_file (cold-start interactive) ----------------------------


def test_cc_invocation_omits_prompt_when_none():
    """With prompt_file=None, the resulting command has no `$(cat ...)` arg.

    Cold-start interactive: `claude` runs with no initial message, exactly
    like a user typing `claude` by hand.
    """
    cmd = launchd._cc_invocation(
        claude_bin="claude",
        resume=None,
        new_session=True,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        prompt_file=None,
        headless=False,
    )
    assert "$(cat" not in cmd
    assert cmd.strip() == "claude"


def test_cc_invocation_with_prompt_file_unchanged(tmp_path):
    """Existing behaviour: prompt_file=<path> still produces `$(cat <path>)`."""
    p = tmp_path / "prompt.md"
    cmd = launchd._cc_invocation(
        claude_bin="claude",
        resume=None,
        new_session=True,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        prompt_file=p,
        headless=False,
    )
    assert "$(cat" in cmd
    assert str(p) in cmd


def test_build_inner_command_interactive_no_prompt(tmp_path):
    """With prompt_file=None, the inner command runs `claude` with no
    `$(cat ...)` substitution — cold-start interactive."""
    cmd = launchd.build_inner_command(
        cwd=tmp_path,
        claude_bin="cc",
        prompt_file=None,
        log_path=tmp_path / "tmux.log",
        label="cold",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name=None,
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
    )
    assert "$(cat" not in cmd
    # Sanity: cd, mark-started, cc invocation all still present.
    assert "cd " in cmd
    assert "mark-started" in cmd
    assert "cc" in cmd
