"""Codex CLI session-file location, parsing, and idle detection.

Mirrors the surface of `jsonl.py` (Claude Code) so that the engine
strategy in `engines.py` can dispatch verbs like capture/wait/ask
through whichever module matches the session's engine.

Codex stores session logs under
``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<ISO>-<sid>.jsonl``
with three top-level event types we care about:

- ``session_meta`` (always the first line): carries ``payload.id``
  (session id) and ``payload.cwd`` (working dir).
- ``event_msg``: turn lifecycle and assistant/user text. Subtypes
  include ``task_started``, ``user_message``, ``agent_message``,
  ``task_complete``, ``exec_command_end``.
- ``response_item``: model-side records — ``message``, ``reasoning``,
  ``function_call``, ``function_call_output``.

The clean idle signal is ``event_msg`` with
``payload.type == "task_complete"`` (equivalent to Claude's
``stop_reason == "end_turn"``).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from oum_worker.jsonl import WaitResult, _parse_iso_utc  # reuse


def _codex_sessions_root() -> Path:
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME environment variable not set")
    return Path(home) / ".codex" / "sessions"


def _read_session_meta(jsonl_path: Path) -> Optional[dict]:
    """Return the parsed session_meta payload, or None if not present
    or malformed. Reads only the first line."""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            line = f.readline()
    except OSError:
        return None
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None
    if d.get("type") != "session_meta":
        return None
    payload = d.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _first_user_message(jsonl_path: Path) -> Optional[str]:
    """Return the first user_message text, or None if absent."""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "event_msg":
                    continue
                payload = d.get("payload", {})
                if payload.get("type") == "user_message":
                    return payload.get("message")
    except OSError:
        return None
    return None


def find_by_session_id(cwd: Path, session_id: str) -> Optional[Path]:
    """Locate a codex JSONL by id, verifying its session_meta matches."""
    root = _codex_sessions_root()
    if not root.exists():
        return None
    pattern = f"*-{session_id}.jsonl"
    for path in root.rglob(pattern):
        meta = _read_session_meta(path)
        if meta and meta.get("id") == session_id:
            return path
    return None


def _candidate_dirs(root: Path, target_dt: datetime) -> list[Path]:
    """Return date-partitioned subdirs to scan: target's day plus
    the day before and the day after (UTC). Filters out non-existent."""
    out: list[Path] = []
    for delta_days in (-1, 0, 1):
        d = (target_dt + timedelta(days=delta_days)).astimezone(timezone.utc)
        sub = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        if sub.exists():
            out.append(sub)
    return out


def discover_by_prompt(cwd: Path, prompt: str, *, created_at: str,
                       tiebreaker_window_seconds: int = 300) -> Optional[str]:
    """Find the codex session-id whose session_meta cwd matches ``cwd``
    and whose first user_message matches ``prompt``.

    ``created_at`` is the worker's state.json:created_at (UTC ISO 8601
    with Z). When multiple files match, the one with the smallest mtime
    delta to ``created_at`` wins. If the best delta exceeds
    ``tiebreaker_window_seconds`` and there is more than one match,
    return None (ambiguous) — same policy as the Claude parser.
    """
    root = _codex_sessions_root()
    if not root.exists():
        return None

    target = (prompt or "").strip()
    target_dt = _parse_iso_utc(created_at)
    target_cwd_str = str(cwd.resolve())

    candidate_files: list[Path] = []
    for sub in _candidate_dirs(root, target_dt):
        candidate_files.extend(sub.glob("*.jsonl"))

    # Fallback: 7-day backward scan if the date-bounded scan came up empty.
    if not candidate_files:
        for delta_days in range(0, 7):
            d = (target_dt - timedelta(days=delta_days)).astimezone(timezone.utc)
            sub = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
            if sub.exists():
                candidate_files.extend(sub.glob("*.jsonl"))

    candidates: list[tuple[float, str]] = []
    for path in candidate_files:
        meta = _read_session_meta(path)
        if not meta:
            continue
        if str(meta.get("cwd", "")) != target_cwd_str:
            continue
        first = _first_user_message(path)
        if first is None or first.strip() != target:
            continue
        delta = abs(path.stat().st_mtime - target_dt.timestamp())
        candidates.append((delta, str(meta.get("id"))))

    if not candidates:
        return None
    candidates.sort()
    best_delta, best_sid = candidates[0]
    if best_delta > tiebreaker_window_seconds and len(candidates) > 1:
        return None
    return best_sid


def wait_for_idle(jsonl_path: Path, *, last_send_at: str,
                  timeout: float = 600.0, stable_ms: int = 1500,
                  poll_ms: int = 500,
                  alive_check=lambda: True) -> WaitResult:
    """Tail jsonl_path until an event_msg with payload.type == 'task_complete'
    arrives (timestamp > last_send_at) AND the file is quiet for stable_ms.

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
    saw_terminal = False

    while True:
        if not alive_check():
            return WaitResult(idle=False, timed_out=False,
                              last_assistant_text=last_assistant_text,
                              last_stop_reason=last_stop_reason)
        if not jsonl_path.exists():
            if time.monotonic() > deadline:
                return WaitResult(False, True, last_assistant_text, last_stop_reason)
            time.sleep(poll_s)
            continue
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
                if d.get("type") != "event_msg":
                    continue
                payload = d.get("payload", {})
                sub = payload.get("type")
                if not ts or _parse_iso_utc(ts) <= last_send_dt:
                    continue
                if sub == "agent_message":
                    msg = payload.get("message")
                    if isinstance(msg, str):
                        last_assistant_text = msg
                if sub == "task_complete":
                    last_stop_reason = "task_complete"
                    last_msg = payload.get("last_agent_message")
                    if isinstance(last_msg, str) and last_msg:
                        last_assistant_text = last_msg
                    saw_terminal = True
        if saw_terminal and (time.monotonic() - last_event_seen) >= stable_s:
            return WaitResult(True, False, last_assistant_text, last_stop_reason)
        if time.monotonic() > deadline:
            return WaitResult(False, True, last_assistant_text, last_stop_reason)
        time.sleep(poll_s)


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "...(truncated)"


def extract_response(jsonl_path: Path, *, since: str,
                     include_thinking: bool = False,
                     include_tool_use: bool = False) -> str:
    """Concatenate text from agent_message events after `since`.

    With include_tool_use, also emit [tool_use ...] for response_item
    function_call and [tool_result ...] for function_call_output.
    With include_thinking, emit [thinking] <summary> for reasoning
    events with a non-empty summary, or [thinking encrypted] when
    only encrypted_content is present.
    """
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
            payload = d.get("payload", {}) or {}
            sub = payload.get("type")
            if t == "event_msg" and sub == "agent_message":
                msg = payload.get("message")
                if isinstance(msg, str):
                    parts.append(msg)
            elif t == "response_item" and sub == "reasoning" and include_thinking:
                summary = payload.get("summary") or []
                if isinstance(summary, list) and summary:
                    for entry in summary:
                        if isinstance(entry, dict):
                            text = entry.get("text", "")
                            if text:
                                parts.append(f"[thinking] {text}")
                elif payload.get("encrypted_content"):
                    parts.append("[thinking encrypted]")
            elif t == "response_item" and sub == "function_call" and include_tool_use:
                name = payload.get("name", "?")
                args = payload.get("arguments", "")
                parts.append(f"[tool_use {name} {_truncate(args, 500)}]")
            elif t == "response_item" and sub == "function_call_output" and include_tool_use:
                output = payload.get("output", "")
                if isinstance(output, str):
                    parts.append(f"[tool_result {_truncate(output, 500)}]")
    return "".join(parts).strip()


def dump_events(jsonl_path: Path, *, since: str) -> str:
    """Return raw JSONL events with timestamp > since, one per line."""
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
