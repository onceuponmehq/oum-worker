"""launchd plist builders + time parsing, lifted from scripts/oum_schedule.py."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]   # repo root
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

DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


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
        m = re.match(r"(\d+)\s*([smhd])", text[pos:])
        if not m:
            raise ValueError("duration must use units like 30m, 3h, or 1h30m")
        total += int(m.group(1)) * DURATION_UNITS[m.group(2)]
        matched = True
        pos += m.end()
    if not matched or total <= 0:
        raise ValueError("duration must be greater than zero")
    return total


def parse_at_time(value: str, now: datetime) -> datetime:
    text = value.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            raise ValueError("--at time must be a valid HH:MM value")
        target = now.astimezone(IST).replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now.astimezone(IST):
            target += timedelta(days=1)
        return target
    try:
        target = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as e:
        raise ValueError("--at must be HH:MM or YYYY-MM-DD HH:MM") from e
    if target.tzinfo is None:
        target = target.replace(tzinfo=IST)
    target = target.astimezone(IST)
    if target <= now.astimezone(IST):
        raise ValueError("--at must be in the future")
    return target


def parse_target(delay: Optional[str], at: Optional[str], *,
                 now: Optional[datetime] = None) -> datetime:
    if delay and at:
        raise ValueError("use either --in or --at, not both")
    if not delay and not at:
        raise ValueError("schedule requires --in or --at")
    cur = (now or now_ist()).astimezone(IST)
    if delay:
        return round_up_to_minute(cur + timedelta(seconds=parse_delay(delay)))
    return round_up_to_minute(parse_at_time(at, cur))


def normalize_label(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    if not text:
        raise ValueError("label must contain at least one alphanumeric character")
    if text.startswith("com."):
        return text
    return f"com.oum.schedule.{text}"


def resolve_workdir(*, repo: Optional[str], cwd: Optional[str]) -> Path:
    if repo and cwd:
        raise ValueError("use either --repo or --cwd, not both")
    if repo and repo in KNOWN_REPOS:
        return KNOWN_REPOS[repo].expanduser().resolve()
    if cwd:
        return Path(cwd).expanduser().resolve()
    return ROOT


import os
import plistlib
import subprocess


def build_plist(*, label: str, cwd: Path, command: str, target: datetime,
                stdout_path: Path, stderr_path: Path) -> bytes:
    plist = {
        "Label": label,
        "WorkingDirectory": "/tmp",
        "ProgramArguments": ["/bin/zsh", "-lic", command],
        "EnvironmentVariables": {
            "TZ": "Asia/Kolkata",
            "PATH": DEFAULT_PATH,
            "LANG": "en_US.UTF-8",
        },
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


def write_plist(path: Path, payload: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise FileExistsError(f"{path} already exists; pass replace=True")
    path.write_bytes(payload)
    path.chmod(0o644)


def bootstrap(label: str, plist_path: Path, *, replace: bool) -> None:
    uid = os.getuid()
    if replace:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=True)
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{label}"], check=True)


def unbootstrap(label: str) -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


import shlex


def _cc_invocation(*, claude_bin: str, resume: Optional[str], new_session: bool,
                   session_name: Optional[str], permission_mode: Optional[str],
                   skip_permissions: bool, prompt_file: Path, headless: bool) -> str:
    parts: list[str] = [claude_bin]
    if headless:
        parts = ["claude", "-p"]
    if resume:
        parts.extend(["--resume", shlex.quote(resume)])
    if new_session and session_name and not headless:
        parts.extend(["--name", shlex.quote(session_name)])
    if permission_mode:
        parts.extend(["--permission-mode", shlex.quote(permission_mode)])
    if skip_permissions:
        parts.append("--dangerously-skip-permissions")
    parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    return " ".join(parts)


def build_inner_command(*, cwd: Path, claude_bin: str, prompt_file: Path,
                        log_path: Path, label: str, logs_dir: Path,
                        resume: Optional[str], new_session: bool,
                        session_name: Optional[str], permission_mode: Optional[str],
                        skip_permissions: bool, tmux_session: str,
                        headless: bool) -> str:
    """Build the zsh command launchd executes when the job fires.

    Steps inside the command (in order):
    1. cd to cwd.
    2. python3 -m oum_worker.runner mark-started --label <L>   (writes started_at)
    3a. interactive: open a tmux window running cc.
    3b. headless:    run `claude -p ...` with stdout to <logs_dir>/<label>/response.txt.
    """
    mark = (
        f"python3 -m oum_worker.runner mark-started "
        f"--label {shlex.quote(label)} "
        f"--logs-dir {shlex.quote(str(logs_dir))}"
    )
    cc = _cc_invocation(
        claude_bin=claude_bin, resume=resume, new_session=new_session,
        session_name=session_name, permission_mode=permission_mode,
        skip_permissions=skip_permissions, prompt_file=prompt_file, headless=headless,
    )
    if headless:
        response = logs_dir / label / "response.txt"
        return (
            f"cd {shlex.quote(str(cwd))} && "
            f"{mark} && "
            f"{cc} > {shlex.quote(str(response))} 2>&1"
        )
    # Interactive: drive a shared tmux session
    from oum_worker.tmux import find_tmux_bin
    tmux_bin = shlex.quote(str(find_tmux_bin()))
    inner = f"cd {shlex.quote(str(cwd))} && {mark} && {cc}"
    pane_command = f"/bin/zsh -lic {shlex.quote(inner)}"
    target = f"{tmux_session}:{label}"
    return (
        f"{tmux_bin} new-session -d -s {shlex.quote(tmux_session)} -x 220 -y 50 2>/dev/null || true; "
        f"{tmux_bin} kill-window -t {shlex.quote(target)} 2>/dev/null || true; "
        f"{tmux_bin} new-window -t {shlex.quote(tmux_session + ':')} "
        f"-n {shlex.quote(label)} -c {shlex.quote(str(cwd))} {shlex.quote(pane_command)}; "
        f"{tmux_bin} setw -t {shlex.quote(target)} remain-on-exit on 2>/dev/null || true; "
        f"{tmux_bin} pipe-pane -t {shlex.quote(target)} -o {shlex.quote('cat >> ' + str(log_path))}"
    )
