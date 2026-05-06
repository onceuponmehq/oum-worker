"""Claude Code JSONL session-file location, parsing, and idle detection."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
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


TERMINAL_STOPS = {"end_turn", "max_tokens", "stop_sequence"}


@dataclass
class WaitResult:
    idle: bool
    timed_out: bool
    last_assistant_text: str
    last_stop_reason: Optional[str]


def _extract_text_blocks(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if b.get("type") == "text")
    if isinstance(content, str):
        return content
    return ""


def wait_for_idle(jsonl_path: Path, *, last_send_at: str, timeout: float = 600.0,
                  stable_ms: int = 1500, poll_ms: int = 500,
                  alive_check=lambda: True) -> WaitResult:
    """Tail jsonl_path until the latest assistant line is terminal AND the stream
    has been quiet for stable_ms.

    `alive_check` is called each tick; return False to short-circuit with
    idle=False/timed_out=False (caller treats this as 'dead').
    """
    deadline = time.monotonic() + timeout
    stable_s = stable_ms / 1000.0
    poll_s = poll_ms / 1000.0
    last_send_dt = _parse_iso_utc(last_send_at)
    pos = 0
    last_event_seen = time.monotonic()
    last_assistant_text = ""
    last_stop_reason: Optional[str] = None
    saw_terminal_assistant = False

    while True:
        if not alive_check():
            return WaitResult(idle=False, timed_out=False,
                              last_assistant_text=last_assistant_text,
                              last_stop_reason=last_stop_reason)
        if not jsonl_path.exists():
            if time.monotonic() > deadline:
                return WaitResult(False, True, last_assistant_text, last_stop_reason)
            time.sleep(poll_s); continue
        with open(jsonl_path, "r", encoding="utf-8") as f:
            f.seek(pos)
            for line in f:
                pos += len(line.encode("utf-8"))
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = d.get("timestamp")
                if ts:
                    last_event_seen = time.monotonic()
                if d.get("type") != "assistant":
                    continue
                if not ts or _parse_iso_utc(ts) <= last_send_dt:
                    continue
                msg = d.get("message", {})
                text = _extract_text_blocks(msg)
                if text:
                    last_assistant_text = text
                stop = msg.get("stop_reason")
                if stop in TERMINAL_STOPS:
                    last_stop_reason = stop
                    saw_terminal_assistant = True
        if saw_terminal_assistant and (time.monotonic() - last_event_seen) >= stable_s:
            return WaitResult(True, False, last_assistant_text, last_stop_reason)
        if time.monotonic() > deadline:
            return WaitResult(False, True, last_assistant_text, last_stop_reason)
        time.sleep(poll_s)


def extract_response(jsonl_path: Path, *, since: str,
                     include_thinking: bool = False,
                     include_tool_use: bool = False) -> str:
    """Concatenate text from assistant lines after `since` (UTC ISO 8601)."""
    if not jsonl_path.exists():
        return ""
    since_dt = _parse_iso_utc(since)
    parts: list[str] = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if not ts or _parse_iso_utc(ts) <= since_dt:
                continue
            t = d.get("type")
            msg = d.get("message", {})
            content = msg.get("content")
            if t == "assistant" and isinstance(content, list):
                for b in content:
                    bt = b.get("type")
                    if bt == "text":
                        parts.append(b.get("text", ""))
                    elif bt == "thinking" and include_thinking:
                        parts.append("[thinking] " + b.get("thinking", ""))
                    elif bt == "tool_use" and include_tool_use:
                        parts.append(f"[tool_use {b.get('name','?')}]")
            elif t == "user" and include_tool_use and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_result":
                        parts.append("[tool_result]")
    return "".join(parts).strip()


def dump_events(jsonl_path: Path, *, since: str) -> str:
    """Return raw JSONL events with timestamp > since, one object per line.

    Used by `oum-worker capture --full` for orchestrators that want the full
    trace (thinking, tool_use, tool_result, plus assistant text) instead of
    just the rendered text.
    """
    if not jsonl_path.exists():
        return ""
    since_dt = _parse_iso_utc(since)
    out_lines: list[str] = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if not ts or _parse_iso_utc(ts) <= since_dt:
                continue
            out_lines.append(line.rstrip("\n"))
    return "\n".join(out_lines)
