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
