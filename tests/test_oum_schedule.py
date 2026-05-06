from __future__ import annotations

import plistlib
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import oum_schedule  # noqa: E402


IST = ZoneInfo("Asia/Kolkata")


def test_parse_delay_accepts_compound_duration() -> None:
    assert oum_schedule.parse_delay("3h") == 10_800
    assert oum_schedule.parse_delay("1h30m") == 5_400
    assert oum_schedule.parse_delay("2d 4h 15m") == 188_100


def test_parse_delay_rejects_missing_unit() -> None:
    with pytest.raises(ValueError, match="duration must use units"):
        oum_schedule.parse_delay("90")


def test_parse_target_from_delay_rounds_up_to_next_minute() -> None:
    now = datetime(2026, 5, 6, 10, 5, 20, tzinfo=IST)

    target = oum_schedule.parse_target(delay="3h", at=None, now=now)

    assert target == datetime(2026, 5, 6, 13, 6, tzinfo=IST)


def test_parse_target_from_time_only_rolls_to_tomorrow_when_time_passed() -> None:
    now = datetime(2026, 5, 6, 18, 0, tzinfo=IST)

    target = oum_schedule.parse_target(delay=None, at="17:30", now=now)

    assert target == datetime(2026, 5, 7, 17, 30, tzinfo=IST)


def test_parse_target_from_absolute_time_uses_ist_for_naive_values() -> None:
    now = datetime(2026, 5, 6, 10, 0, tzinfo=IST)

    target = oum_schedule.parse_target(delay=None, at="2026-05-06 17:30", now=now)

    assert target == datetime(2026, 5, 6, 17, 30, tzinfo=IST)


def test_resolve_workdir_supports_known_repo_aliases() -> None:
    assert oum_schedule.resolve_workdir(repo="oum-os", cwd=None) == ROOT
    assert oum_schedule.resolve_workdir(repo="backend", cwd=None) == (
        ROOT.parent / "codebase" / "backend"
    ).resolve()


def test_resolve_workdir_supports_absolute_paths() -> None:
    assert oum_schedule.resolve_workdir(repo="/tmp", cwd=None) == Path("/tmp").resolve()


def test_resolve_workdir_rejects_repo_and_cwd_together() -> None:
    with pytest.raises(ValueError, match="use either --repo or --cwd"):
        oum_schedule.resolve_workdir(repo="backend", cwd="/tmp")


def test_window_name_strips_prefix_and_sanitizes() -> None:
    assert oum_schedule.window_name_for("com.oum.schedule.plan-tier1") == "plan-tier1"
    assert oum_schedule.window_name_for("weird name!") == "weird-name"
    assert oum_schedule.window_name_for("") == "job"


def test_window_name_truncates_to_forty_chars() -> None:
    long = "com.oum.schedule." + "x" * 80
    name = oum_schedule.window_name_for(long)
    assert len(name) == 40
    assert name == "x" * 40


def test_build_tmux_command_includes_session_window_and_log(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("hi")
    log_path = tmp_path / "out.log"

    command = oum_schedule.build_tmux_command(
        cwd=Path("/tmp/oum"),
        claude_bin="cc",
        prompt_file=prompt_file,
        log_path=log_path,
        label="com.oum.schedule.daily-review",
        resume=None,
        new_session=True,
        session_name="daily-review",
        permission_mode="plan",
        skip_permissions=True,
        tmux_bin=Path("/opt/homebrew/bin/tmux"),
        tmux_session="oum",
    )

    assert "/opt/homebrew/bin/tmux" in command
    assert "new-session -d -s oum" in command
    assert "kill-window -t oum:daily-review" in command
    assert "new-window -t oum: " in command
    assert "-n daily-review" in command
    assert "-c /tmp/oum" in command
    assert "remain-on-exit on" in command
    assert "pipe-pane -t oum:daily-review" in command
    assert f"cat >> {log_path}" in command
    # The cc invocation must be wrapped in /bin/zsh -lic so aliases load.
    assert "/bin/zsh -lic" in command
    assert "cc --name daily-review --permission-mode plan --dangerously-skip-permissions" in command
    assert f"cat {prompt_file}" in command


def test_build_tmux_command_resume_path() -> None:
    command = oum_schedule.build_tmux_command(
        cwd=Path("/tmp/oum"),
        claude_bin="cc",
        prompt_file=Path("/tmp/p.md"),
        log_path=Path("/tmp/out.log"),
        label="com.oum.schedule.resume-job",
        resume="abc-123",
        new_session=False,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        tmux_bin=Path("/opt/homebrew/bin/tmux"),
    )

    assert "cc --resume abc-123 " in command
    assert "--name" not in command
    assert "--permission-mode" not in command
    assert "--dangerously-skip-permissions" not in command


def test_build_tmux_command_rejects_resume_and_new_together() -> None:
    with pytest.raises(ValueError, match="use either --resume or --new"):
        oum_schedule.build_tmux_command(
            cwd=Path("/tmp"),
            claude_bin="cc",
            prompt_file=Path("/tmp/p.md"),
            log_path=Path("/tmp/out.log"),
            label="com.oum.schedule.bad",
            resume="abc",
            new_session=True,
            session_name=None,
            permission_mode=None,
            skip_permissions=False,
            tmux_bin=Path("/opt/homebrew/bin/tmux"),
        )


def test_write_prompt_file_creates_file_with_content(tmp_path: Path) -> None:
    path = oum_schedule.write_prompt_file("hello world", "com.oum.schedule.foo", tmp_path)
    assert path == tmp_path / "com.oum.schedule.foo.prompt.md"
    assert path.read_text() == "hello world"


def test_write_prompt_file_rejects_empty_prompt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prompt must not be empty"):
        oum_schedule.write_prompt_file("", "x", tmp_path)


def test_build_launchd_plist_is_one_shot_calendar_job() -> None:
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)

    payload = oum_schedule.build_launchd_plist(
        label="com.oum.schedule.test",
        cwd=Path("/Users/tushar/Documents/OnceUponMe/oum-os"),
        command="tmux new-window -t oum: -n test echo hi",
        target=target,
        stdout_path=Path("/tmp/stdout.log"),
        stderr_path=Path("/tmp/stderr.log"),
    )

    parsed = plistlib.loads(payload)
    assert parsed["Label"] == "com.oum.schedule.test"
    assert parsed["WorkingDirectory"] == "/tmp"
    assert parsed["ProgramArguments"] == [
        "/bin/zsh",
        "-lic",
        "tmux new-window -t oum: -n test echo hi",
    ]
    assert parsed["StartCalendarInterval"] == {
        "Month": 5,
        "Day": 6,
        "Hour": 17,
        "Minute": 30,
    }
    assert parsed["LaunchOnlyOnce"] is True
    assert parsed["RunAtLoad"] is False
    assert parsed["StandardOutPath"] == "/tmp/stdout.log"
    assert parsed["StandardErrorPath"] == "/tmp/stderr.log"


def test_normalize_label_prefixes_short_names() -> None:
    assert oum_schedule.normalize_label("resume-copy") == "com.oum.schedule.resume-copy"
    assert oum_schedule.normalize_label("com.example.job") == "com.example.job"


def test_parser_defaults_to_cc_command() -> None:
    parser = oum_schedule.build_parser()

    args = parser.parse_args(["--in", "1h", "--new", "--prompt", "hi", "--no-bootstrap"])

    assert args.claude_bin == "cc"


def test_parser_accepts_repo_alias() -> None:
    parser = oum_schedule.build_parser()

    args = parser.parse_args(["--in", "1h", "--new", "--repo", "backend", "--prompt", "hi"])

    assert args.repo == "backend"


# --- Phase 3a: --env KEY=VALUE injection -----------------------------------


def test_env_pair_parsing_accepts_KEY_VALUE() -> None:
    assert oum_schedule.parse_env_pairs(["FOO=bar"]) == {"FOO": "bar"}


def test_env_pair_parsing_handles_value_with_equals() -> None:
    parsed = oum_schedule.parse_env_pairs(["URL=https://example.com/?a=1&b=2"])

    # Split is on the first '=' only — VALUE keeps the rest verbatim.
    assert parsed == {"URL": "https://example.com/?a=1&b=2"}


def test_env_pair_parsing_refuses_reserved_TZ_PATH_LANG() -> None:
    for reserved in ("TZ", "PATH", "LANG"):
        with pytest.raises(ValueError, match="reserved"):
            oum_schedule.parse_env_pairs([f"{reserved}=clobber"])


def test_env_pair_parsing_refuses_empty_key_or_no_equals() -> None:
    with pytest.raises(ValueError, match="missing '='"):
        oum_schedule.parse_env_pairs(["NO_EQUALS_SIGN"])
    with pytest.raises(ValueError, match="empty key"):
        oum_schedule.parse_env_pairs(["=value-only"])


def test_env_pair_parsing_returns_empty_dict_for_none_or_empty() -> None:
    assert oum_schedule.parse_env_pairs(None) == {}
    assert oum_schedule.parse_env_pairs([]) == {}


def test_env_pair_parsing_later_value_overrides_earlier() -> None:
    parsed = oum_schedule.parse_env_pairs(["FOO=first", "FOO=second"])
    assert parsed == {"FOO": "second"}


def test_plist_includes_user_env() -> None:
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)

    payload = oum_schedule.build_launchd_plist(
        label="com.oum.schedule.envtest",
        cwd=Path("/tmp"),
        command="echo hi",
        target=target,
        stdout_path=Path("/tmp/o.log"),
        stderr_path=Path("/tmp/e.log"),
        env_pairs={"FOO": "bar", "OUM_TASK_ID": "X"},
    )

    parsed = plistlib.loads(payload)
    env = parsed["EnvironmentVariables"]
    assert env["FOO"] == "bar"
    assert env["OUM_TASK_ID"] == "X"
    # Baseline keys still present and unchanged.
    assert env["TZ"] == "Asia/Kolkata"
    assert env["LANG"] == "en_US.UTF-8"
    assert "PATH" in env


def test_plist_baseline_unaffected_when_env_pairs_omitted() -> None:
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)

    payload = oum_schedule.build_launchd_plist(
        label="com.oum.schedule.no-env",
        cwd=Path("/tmp"),
        command="echo hi",
        target=target,
        stdout_path=Path("/tmp/o.log"),
        stderr_path=Path("/tmp/e.log"),
    )

    parsed = plistlib.loads(payload)
    env = parsed["EnvironmentVariables"]
    assert set(env.keys()) == {"TZ", "PATH", "LANG"}


def test_pane_command_exports_user_env_before_cd(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("hi")
    log_path = tmp_path / "out.log"

    command = oum_schedule.build_tmux_command(
        cwd=Path("/tmp/oum"),
        claude_bin="cc",
        prompt_file=prompt_file,
        log_path=log_path,
        label="com.oum.schedule.envjob",
        resume=None,
        new_session=True,
        session_name="envjob",
        permission_mode=None,
        skip_permissions=False,
        tmux_bin=Path("/opt/homebrew/bin/tmux"),
        env_pairs={"FOO": "bar", "OUM_TASK_ID": "2026-05-06-001"},
    )

    # shlex.quote returns barewords for safe values (no shell metachars).
    # The exports must therefore appear as `export KEY=VALUE` chained with
    # ` && `, before the cd into the workdir.
    assert "export FOO=bar" in command
    assert "export OUM_TASK_ID=2026-05-06-001" in command
    assert command.index("export FOO=bar") < command.index("cd /tmp/oum")
    assert command.index("export OUM_TASK_ID=2026-05-06-001") < command.index(
        "cd /tmp/oum"
    )
    # Exports are joined into the inner zsh chain with ` && `.
    assert " && cd /tmp/oum && cc " in command


def test_pane_command_quotes_values_with_shell_metacharacters(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("hi")
    log_path = tmp_path / "out.log"

    command = oum_schedule.build_tmux_command(
        cwd=Path("/tmp/oum"),
        claude_bin="cc",
        prompt_file=prompt_file,
        log_path=log_path,
        label="com.oum.schedule.metaenv",
        resume=None,
        new_session=True,
        session_name="metaenv",
        permission_mode=None,
        skip_permissions=False,
        tmux_bin=Path("/opt/homebrew/bin/tmux"),
        # Value contains $, space, and semicolon — shlex.quote must
        # wrap it so the inner shell does not expand or split.
        env_pairs={"X": "alpha;beta gamma"},
    )

    # Outer pane_command wraps the entire inner with single quotes, so a
    # literal single quote inside the value renders as the standard
    # '"'"' escape sequence. We only need to check the inner-quoted form.
    # shlex.quote("alpha;beta gamma") -> 'alpha;beta gamma'
    # That single-quoted form survives outer wrapping as: '"'"'alpha;beta gamma'"'"'
    assert "export X=" in command
    # The dangerous metacharacters must appear inside a quoted region —
    # confirm by reconstructing the outer wrapping.
    assert "alpha;beta gamma" in command
    # And no bareword export of those metachars (which would be unsafe).
    assert "export X=alpha;beta gamma" not in command


def test_pane_command_without_env_pairs_unchanged(tmp_path: Path) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("hi")

    command = oum_schedule.build_tmux_command(
        cwd=Path("/tmp/oum"),
        claude_bin="cc",
        prompt_file=prompt_file,
        log_path=tmp_path / "out.log",
        label="com.oum.schedule.plain",
        resume=None,
        new_session=True,
        session_name="plain",
        permission_mode=None,
        skip_permissions=False,
        tmux_bin=Path("/opt/homebrew/bin/tmux"),
    )

    assert "export " not in command
    # cd into the workdir is still present and well-formed.
    assert "cd /tmp/oum && cc " in command


def test_dry_run_includes_env_in_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        oum_schedule, "find_tmux_bin", lambda: Path("/opt/homebrew/bin/tmux")
    )

    parser = oum_schedule.build_parser()
    args = parser.parse_args(
        [
            "--in",
            "1m",
            "--new",
            "--name",
            "smoke",
            "--prompt",
            "test",
            "--env",
            "FOO=bar",
            "--env",
            "OUM_TASK_ID=2026-05-06-001",
            "--logs-dir",
            str(tmp_path / "logs"),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
            "--dry-run",
        ]
    )

    rc = oum_schedule.run(args)
    captured = capsys.readouterr()

    assert rc == 0
    # Plist body is dumped after the metadata header.
    assert "<key>EnvironmentVariables</key>" in captured.out
    assert "<key>FOO</key>" in captured.out
    assert "<string>bar</string>" in captured.out
    assert "<key>OUM_TASK_ID</key>" in captured.out
    assert "<string>2026-05-06-001</string>" in captured.out
    # Baseline still present.
    assert "<key>TZ</key>" in captured.out
    assert "<string>Asia/Kolkata</string>" in captured.out


def test_repeated_env_flags_merge(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        oum_schedule, "find_tmux_bin", lambda: Path("/opt/homebrew/bin/tmux")
    )

    parser = oum_schedule.build_parser()
    args = parser.parse_args(
        [
            "--in",
            "1m",
            "--new",
            "--name",
            "merge",
            "--prompt",
            "test",
            "--env",
            "A=1",
            "--env",
            "B=2",
            "--logs-dir",
            str(tmp_path / "logs"),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
            "--dry-run",
        ]
    )

    rc = oum_schedule.run(args)
    captured = capsys.readouterr()

    assert rc == 0
    # Find the EnvironmentVariables block and parse it back to a dict.
    body = captured.out
    plist_start = body.index("<?xml")
    payload = body[plist_start:].encode("utf-8")
    parsed = plistlib.loads(payload)
    env = parsed["EnvironmentVariables"]
    assert env["A"] == "1"
    assert env["B"] == "2"


def test_run_reports_error_for_malformed_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        oum_schedule, "find_tmux_bin", lambda: Path("/opt/homebrew/bin/tmux")
    )

    parser = oum_schedule.build_parser()
    args = parser.parse_args(
        [
            "--in",
            "1m",
            "--new",
            "--name",
            "bad",
            "--prompt",
            "test",
            "--env",
            "BROKEN_NO_EQUALS",
            "--logs-dir",
            str(tmp_path / "logs"),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
            "--dry-run",
        ]
    )

    rc = oum_schedule.run(args)
    captured = capsys.readouterr()

    assert rc == 2
    assert "missing '='" in captured.err
