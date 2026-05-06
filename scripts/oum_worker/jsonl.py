"""Claude Code JSONL session-file location, parsing, and idle detection."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


def encode_cwd(cwd: Path) -> str:
    """Convert /a/b/c → -a-b-c, matching ~/.claude/projects/<encoded>/ layout."""
    return "-" + "-".join(p for p in str(cwd.resolve()).split("/") if p)


def projects_dir() -> Path:
    """Return ~/.claude/projects/. Raises RuntimeError if HOME is unset."""
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME environment variable not set")
    return Path(home) / ".claude" / "projects"


def find_by_session_id(cwd: Path, session_id: str) -> Optional[Path]:
    p = projects_dir() / encode_cwd(cwd) / f"{session_id}.jsonl"
    return p if p.exists() else None


def _parse_iso_utc(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _first_user_message(jsonl_path: Path) -> Optional[str]:
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "user":
                continue
            msg = d.get("message", {})
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                if texts:
                    return "\n".join(texts)
    return None


def discover_by_prompt(cwd: Path, prompt: str, *, created_at: str,
                       tiebreaker_window_seconds: int = 300) -> Optional[str]:
    """Find session-id whose first user message matches `prompt`.

    `created_at` is the worker's state.json:created_at (UTC ISO 8601 with Z).
    If multiple sessions match, prefer the one whose mtime is closest to
    `created_at` and within `tiebreaker_window_seconds`.
    """
    pdir = projects_dir() / encode_cwd(cwd)
    if not pdir.exists():
        return None
    target = prompt.strip()
    target_dt = _parse_iso_utc(created_at)
    candidates: list[tuple[float, str]] = []
    for f in pdir.glob("*.jsonl"):
        first = _first_user_message(f)
        if first is None or first.strip() != target:
            continue
        delta = abs(f.stat().st_mtime - target_dt.timestamp())
        candidates.append((delta, f.stem))
    if not candidates:
        return None
    candidates.sort()
    best_delta, best_sid = candidates[0]
    if best_delta > tiebreaker_window_seconds and len(candidates) > 1:
        return None  # ambiguous
    return best_sid
