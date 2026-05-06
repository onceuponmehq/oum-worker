#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_PATH = "/Users/tushar/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
KNOWN_REPOS = {
    "oum-os": ROOT,
    "os": ROOT,
    "onceuponme": ROOT.parent,
    "backend": ROOT.parent / "codebase" / "backend",
    "frontend": ROOT.parent / "codebase" / "frontend",
    "accounting": ROOT.parent / "codebase" / "accounting",
}
DURATION_UNITS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
}
TMUX_SESSION = "oum"
TMUX_CANDIDATE_PATHS = (
    "/opt/homebrew/bin/tmux",
    "/usr/local/bin/tmux",
    "/usr/bin/tmux",
)
# Reserved env keys are written by build_launchd_plist as the baseline
# EnvironmentVariables. User-supplied --env values must not clobber them so
# that scheduled jobs always inherit the same timezone, PATH, and locale.
RESERVED_ENV_KEYS = frozenset({"TZ", "PATH", "LANG"})


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def parse_env_pairs(values: list[str] | None) -> dict[str, str]:
    """Parse repeated --env KEY=VALUE flags into a dict.

    Splits on the first ``=`` so VALUE may itself contain equals signs
    (e.g. URLs with query strings). Refuses reserved baseline keys so
    they can't be overridden from the command line.

    Later occurrences of the same KEY override earlier ones.
    """
    if not values:
        return {}

    pairs: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(
                f"--env value {raw!r} must be KEY=VALUE (missing '=')"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(
                f"--env value {raw!r} has an empty key"
            )
        if key in RESERVED_ENV_KEYS:
            raise ValueError(
                f"--env key {key!r} is reserved (baseline plist env). "
                "Pick a different name."
            )
        pairs[key] = value
    return pairs


def now_ist() -> datetime:
    return datetime.now(IST)


def round_up_to_minute(value: datetime) -> datetime:
    if value.second == 0 and value.microsecond == 0:
        return value
    return (value + timedelta(minutes=1)).replace(second=0, microsecond=0)


def parse_delay(value: str) -> int:
    text = value.strip().lower()
    if not text:
        raise ValueError("duration must not be empty")

    total = 0
    pos = 0
    matched = False
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = re.match(r"(\d+)\s*([smhd])", text[pos:])
        if not match:
            raise ValueError("duration must use units like 30m, 3h, or 1h30m")
        amount = int(match.group(1))
        unit = match.group(2)
        total += amount * DURATION_UNITS[unit]
        matched = True
        pos += match.end()

    if not matched or total <= 0:
        raise ValueError("duration must be greater than zero")
    return total


def parse_at_time(value: str, now: datetime) -> datetime:
    text = value.strip()
    time_only = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if time_only:
        hour = int(time_only.group(1))
        minute = int(time_only.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("--at time must be a valid HH:MM value")
        target = now.astimezone(IST).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now.astimezone(IST):
            target = target + timedelta(days=1)
        return target

    try:
        target = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as error:
        raise ValueError("--at must be HH:MM or YYYY-MM-DD HH:MM") from error

    if target.tzinfo is None:
        target = target.replace(tzinfo=IST)
    target = target.astimezone(IST)
    if target <= now.astimezone(IST):
        raise ValueError("--at must be in the future")
    return target


def parse_target(delay: str | None, at: str | None, now: datetime | None = None) -> datetime:
    if delay and at:
        raise ValueError("use either --in or --at, not both")
    if not delay and not at:
        raise ValueError("schedule requires --in or --at")

    current = (now or now_ist()).astimezone(IST)
    if delay:
        return round_up_to_minute(current + timedelta(seconds=parse_delay(delay)))
    return round_up_to_minute(parse_at_time(at or "", current))


def normalize_label(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("label must not be empty")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    if not text:
        raise ValueError("label must contain at least one alphanumeric character")
    if text.startswith("com."):
        return text
    return f"com.oum.schedule.{text}"


def resolve_workdir(repo: str | None, cwd: str | None) -> Path:
    if repo and cwd:
        raise ValueError("use either --repo or --cwd, not both")
    selected = repo or cwd
    if not selected:
        return ROOT
    if repo and repo in KNOWN_REPOS:
        return KNOWN_REPOS[repo].expanduser().resolve()
    return Path(selected).expanduser().resolve()


def default_label(target: datetime) -> str:
    return normalize_label(f"claude-{target.strftime('%Y%m%d-%H%M')}")


def find_tmux_bin() -> Path:
    """Locate a tmux binary so the launchd job can drive a shared session."""
    for candidate in TMUX_CANDIDATE_PATHS:
        path = Path(candidate)
        if path.exists():
            return path
    result = subprocess.run(
        ["/bin/zsh", "-lic", "command -v tmux"],
        capture_output=True,
        text=True,
    )
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("/") and Path(line).exists():
            return Path(line)
    raise FileNotFoundError(
        "tmux not found. Install with: brew install tmux"
    )


def window_name_for(label: str, prefix: str = "com.oum.schedule.") -> str:
    name = label[len(prefix):] if label.startswith(prefix) else label
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return name[:40] or "job"


def write_prompt_file(prompt: str, label: str, logs_dir: Path) -> Path:
    if not prompt:
        raise ValueError("prompt must not be empty")
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"{label}.prompt.md"
    path.write_text(prompt, encoding="utf-8")
    path.chmod(0o644)
    return path


def build_tmux_command(
    *,
    cwd: Path,
    claude_bin: str,
    prompt_file: Path,
    log_path: Path,
    label: str,
    resume: str | None,
    new_session: bool,
    session_name: str | None,
    permission_mode: str | None,
    skip_permissions: bool,
    tmux_bin: Path,
    tmux_session: str = TMUX_SESSION,
    env_pairs: dict[str, str] | None = None,
) -> str:
    """
    Build the shell command launchd executes.

    Steps the command performs:
    1. Create the shared tmux session if missing.
    2. Drop any prior window with the same name so --replace is idempotent.
    3. Open a new window that runs cc inside a login interactive zsh.
    4. Mark the window remain-on-exit so the user can review the final state.
    5. Pipe the window's output to the log file for grep / audit.

    ``env_pairs`` (if any) are exported inside the inner zsh chain BEFORE
    the ``cd`` so the inner ``claude`` process inherits them. launchd
    passes env to the outermost shell, but the tmux + interactive-zsh
    chain may strip or reset variables; an explicit ``export`` in the
    inner command is the safe path.
    """
    if resume and new_session:
        raise ValueError("use either --resume or --new, not both")

    cc_parts: list[str] = [str(claude_bin)]
    if resume:
        cc_parts.extend(["--resume", shlex.quote(resume)])
    if new_session and session_name:
        cc_parts.extend(["--name", shlex.quote(session_name)])
    if permission_mode:
        cc_parts.extend(["--permission-mode", shlex.quote(permission_mode)])
    if skip_permissions:
        cc_parts.append("--dangerously-skip-permissions")
    cc_parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    cc_invocation = " ".join(cc_parts)

    # Sequence: exports → cd → claude. Exports first so any cd-side hooks
    # (chpwd, direnv, etc.) see the new env.
    segments: list[str] = []
    if env_pairs:
        for key, value in env_pairs.items():
            segments.append(f"export {key}={shlex.quote(value)}")
    segments.append(f"cd {shlex.quote(str(cwd))}")
    segments.append(cc_invocation)
    inner = " && ".join(segments)
    pane_command = f"/bin/zsh -lic {shlex.quote(inner)}"

    window = window_name_for(label)
    target = f"{tmux_session}:{window}"
    tmux = shlex.quote(str(tmux_bin))

    return (
        f"{tmux} new-session -d -s {shlex.quote(tmux_session)} -x 220 -y 50 2>/dev/null || true; "
        f"{tmux} kill-window -t {shlex.quote(target)} 2>/dev/null || true; "
        f"{tmux} new-window -t {shlex.quote(tmux_session + ':')} "
        f"-n {shlex.quote(window)} -c {shlex.quote(str(cwd))} {shlex.quote(pane_command)}; "
        f"{tmux} setw -t {shlex.quote(target)} remain-on-exit on 2>/dev/null || true; "
        f"{tmux} pipe-pane -t {shlex.quote(target)} -o {shlex.quote(f'cat >> {log_path}')}"
    )


def build_launchd_plist(
    *,
    label: str,
    cwd: Path,
    command: str,
    target: datetime,
    stdout_path: Path,
    stderr_path: Path,
    env_pairs: dict[str, str] | None = None,
) -> bytes:
    environment: dict[str, str] = {
        "TZ": "Asia/Kolkata",
        "PATH": DEFAULT_PATH,
        "LANG": "en_US.UTF-8",
    }
    if env_pairs:
        for key, value in env_pairs.items():
            if key in RESERVED_ENV_KEYS:
                # Defence in depth: parse_env_pairs already refuses these,
                # but if a caller passes the dict directly, keep baseline.
                continue
            environment[key] = value

    plist = {
        "Label": label,
        # Keep launchd's own chdir target on an unprotected path. The command
        # below performs the requested `cd` after bash starts; setting this to
        # a Documents path can fail before the shell runs on macOS.
        "WorkingDirectory": "/tmp",
        "ProgramArguments": ["/bin/zsh", "-lic", command],
        "EnvironmentVariables": environment,
        "StartCalendarInterval": {
            "Month": target.month,
            "Day": target.day,
            "Hour": target.hour,
            "Minute": target.minute,
        },
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "RunAtLoad": False,
        "LaunchOnlyOnce": True,
        "AbandonProcessGroup": True,
    }
    return plistlib.dumps(plist, sort_keys=False)


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt and args.prompt_file:
        raise ValueError("--prompt and --prompt-file are mutually exclusive")
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if args.prompt:
        return args.prompt.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return input("Prompt to send to Claude: ").strip()


def interactive_fill(args: argparse.Namespace) -> None:
    if not args.resume and not args.new_session:
        mode = input("Start new session or resume existing? [new/resume]: ").strip().lower()
        if mode.startswith("r"):
            args.resume = input("Claude session id or resume search text: ").strip()
        else:
            args.new_session = True

    if not args.delay and not args.at:
        raw = input("Run when? Use delay like 3h, or exact time like 17:30 / 2026-05-06 17:30: ").strip()
        try:
            parse_delay(raw)
            args.delay = raw
        except ValueError:
            args.at = raw


def write_plist(path: Path, payload: bytes, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise FileExistsError(f"{path} already exists; pass --replace to overwrite")
    path.write_bytes(payload)
    path.chmod(0o644)


def bootstrap(label: str, plist_path: Path, replace: bool) -> None:
    uid = os.getuid()
    if replace:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=True)
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{label}"], check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Schedule a one-shot Claude Code run via launchd.",
        epilog=(
            "Examples:\n"
            "  scripts/oum-schedule --in 3h --resume SESSION_ID --prompt 'Continue the task.'\n"
            "  scripts/oum-schedule --at '17:30' --new --name followup --prompt-file /tmp/prompt.md\n"
            "  scripts/oum-schedule --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    schedule = parser.add_mutually_exclusive_group()
    schedule.add_argument("--in", dest="delay", help="Delay before running, e.g. 30m, 3h, 1h30m")
    schedule.add_argument("--at", help="Run at HH:MM today/tomorrow or YYYY-MM-DD HH:MM in IST")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", help="Resume a Claude session by id or search text")
    mode.add_argument("--new", action="store_true", dest="new_session", help="Start a new Claude session")

    parser.add_argument("--prompt", help="Prompt to send to Claude")
    parser.add_argument("--prompt-file", help="Read the prompt from a file")
    repo_group = parser.add_mutually_exclusive_group()
    repo_group.add_argument(
        "--repo",
        help=(
            "Known repo alias or path for the scheduled run. "
            f"Known: {', '.join(sorted(KNOWN_REPOS))}"
        ),
    )
    repo_group.add_argument("--cwd", help="Working directory path for the scheduled Claude run")
    parser.add_argument("--name", help="Session name when starting a new Claude session")
    parser.add_argument("--label", help="launchd label. Short names are prefixed with com.oum.schedule.")
    parser.add_argument(
        "--cc-command",
        "--claude-bin",
        dest="claude_bin",
        default="cc",
        help="Command used to run Claude Code. Defaults to the zsh `cc` alias.",
    )
    parser.add_argument("--permission-mode", choices=["acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"])
    parser.add_argument("--dangerously-skip-permissions", action="store_true", dest="skip_permissions")
    parser.add_argument("--launch-agents-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    parser.add_argument("--logs-dir", default=str(ROOT / ".logs" / "launchd"))
    parser.add_argument(
        "--tmux-session",
        default=TMUX_SESSION,
        help=f"tmux session that holds scheduled job windows (default: {TMUX_SESSION})",
    )
    parser.add_argument("--replace", action="store_true", help="Replace an existing plist with the same label")
    parser.add_argument("--no-bootstrap", action="store_true", help="Write the plist but do not load it with launchctl")
    parser.add_argument("--dry-run", action="store_true", help="Print the plist and launchctl action without writing")
    parser.add_argument(
        "--env",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Inject KEY=VALUE env var into the launchd plist and the inner "
            "claude invocation. Repeatable. Reserved keys (TZ, PATH, LANG) "
            "are rejected."
        ),
    )
    return parser


def run(args: argparse.Namespace) -> int:
    interactive_fill(args)
    try:
        env_pairs = parse_env_pairs(getattr(args, "env", None))
        prompt = read_prompt(args)
        target = parse_target(args.delay, args.at)
        label = normalize_label(args.label) if args.label else default_label(target)
        cwd = resolve_workdir(args.repo, args.cwd)
        claude_bin = args.claude_bin
        logs_dir = Path(args.logs_dir).expanduser()
        launch_agents_dir = Path(args.launch_agents_dir).expanduser()
        plist_path = launch_agents_dir / f"{label}.plist"
        log_path = logs_dir / f"{label}.out"
        tmux_bin = find_tmux_bin()
        prompt_file = write_prompt_file(prompt, label, logs_dir)
        command = build_tmux_command(
            cwd=cwd,
            claude_bin=claude_bin,
            prompt_file=prompt_file,
            log_path=log_path,
            label=label,
            resume=args.resume,
            new_session=args.new_session,
            session_name=args.name,
            permission_mode=args.permission_mode,
            skip_permissions=args.skip_permissions,
            tmux_bin=tmux_bin,
            tmux_session=args.tmux_session,
            env_pairs=env_pairs,
        )
        payload = build_launchd_plist(
            label=label,
            cwd=cwd,
            command=command,
            target=target,
            stdout_path=logs_dir / f"{label}.launchd.out",
            stderr_path=logs_dir / f"{label}.launchd.err",
            env_pairs=env_pairs,
        )
    except (OSError, ValueError, FileExistsError, FileNotFoundError) as error:
        eprint(f"error: {error}")
        return 2

    window = window_name_for(label)

    if args.dry_run:
        print(f"Would write: {plist_path}")
        print(f"Would run at: {target.strftime('%Y-%m-%d %H:%M IST')}")
        print(f"Prompt:  {prompt_file}")
        print(f"Log:     {log_path}")
        print(f"Window:  tmux a -t {args.tmux_session}  →  window '{window}'")
        if not args.no_bootstrap:
            print(f"Would bootstrap: launchctl bootstrap gui/{os.getuid()} {plist_path}")
        print()
        print(payload.decode("utf-8"), end="")
        return 0

    try:
        write_plist(plist_path, payload, args.replace)
        if not args.no_bootstrap:
            bootstrap(label, plist_path, args.replace)
    except (OSError, subprocess.CalledProcessError, FileExistsError) as error:
        eprint(f"error: {error}")
        return 2

    print(f"Scheduled {label}")
    print(f"  Plist:   {plist_path}")
    print(f"  Runs at: {target.strftime('%Y-%m-%d %H:%M IST')}")
    print(f"  Prompt:  {prompt_file}")
    print(f"  Log:     {log_path}")
    print(f"  Window:  tmux a -t {args.tmux_session}  →  window '{window}'")
    print(f"  Switch:  Ctrl-b w (window list), Ctrl-b d (detach)")
    if args.no_bootstrap:
        print("Not loaded yet (--no-bootstrap).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
