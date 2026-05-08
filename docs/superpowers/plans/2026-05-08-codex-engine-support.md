# Codex engine support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--engine claude|codex` to `oum-worker spawn` / `schedule` so users can drive either Claude Code or Codex CLI through every existing verb (spawn, attach, send, capture, wait, ask, kill, list, status, logs, schedule), with full JSONL parity for codex.

**Architecture:** A new `engines.py` strategy module with `ClaudeEngine` and `CodexEngine` builds invocations. A new `codex_jsonl.py` mirrors `jsonl.py`'s surface (`discover_by_prompt`, `find_by_session_id`, `wait_for_idle`, `extract_response`, `dump_events`) over codex's session-log format. State.json gains an `engine` field; capture/wait/ask handlers dispatch through `engines.get(state.engine).jsonl_module`. YOLO defaults on for codex, opt-out via `--no-yolo`.

**Tech Stack:** Python 3.11+, argparse, pytest, tmux, launchd. Standard-library only at runtime.

---

## Task 1: Codex JSONL fixtures + `find_by_session_id` / `discover_by_prompt`

**Files:**
- Create: `tests/fixtures/oum_worker/codex_simple.jsonl`
- Create: `tests/fixtures/oum_worker/codex_with_tools.jsonl`
- Create: `scripts/oum_worker/codex_jsonl.py`
- Create: `tests/test_oum_worker_codex_jsonl.py`

- [ ] **Step 1: Create the simple fixture**

Write `tests/fixtures/oum_worker/codex_simple.jsonl`:

```jsonl
{"timestamp":"2026-05-08T10:00:00.000Z","type":"session_meta","payload":{"id":"019e0500-0000-0000-0000-000000000001","timestamp":"2026-05-08T10:00:00.000Z","cwd":"/tmp/codex-test-cwd","originator":"oum-worker-test","cli_version":"0.0.0"}}
{"timestamp":"2026-05-08T10:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"t1","model_context_window":100000}}
{"timestamp":"2026-05-08T10:01:01.000Z","type":"event_msg","payload":{"type":"user_message","message":"hello codex"}}
{"timestamp":"2026-05-08T10:01:30.000Z","type":"event_msg","payload":{"type":"agent_message","message":"Hi there!","phase":"final","memory_citation":null}}
{"timestamp":"2026-05-08T10:01:35.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"t1","last_agent_message":"Hi there!"}}
```

- [ ] **Step 2: Create the with-tools fixture**

Write `tests/fixtures/oum_worker/codex_with_tools.jsonl`:

```jsonl
{"timestamp":"2026-05-08T10:00:00.000Z","type":"session_meta","payload":{"id":"019e0500-0000-0000-0000-000000000002","timestamp":"2026-05-08T10:00:00.000Z","cwd":"/tmp/codex-test-cwd","originator":"oum-worker-test","cli_version":"0.0.0"}}
{"timestamp":"2026-05-08T10:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"t1","model_context_window":100000}}
{"timestamp":"2026-05-08T10:01:01.000Z","type":"event_msg","payload":{"type":"user_message","message":"list files and report"}}
{"timestamp":"2026-05-08T10:01:05.000Z","type":"response_item","payload":{"type":"reasoning","summary":[{"type":"summary_text","text":"need to list dir"}],"content":null,"encrypted_content":null}}
{"timestamp":"2026-05-08T10:01:06.000Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"ls -la\"}","call_id":"call_001"}}
{"timestamp":"2026-05-08T10:01:07.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call_001","output":"file1.txt\nfile2.txt"}}
{"timestamp":"2026-05-08T10:01:08.000Z","type":"event_msg","payload":{"type":"exec_command_end","call_id":"call_001","process_id":"123","turn_id":"t1","exit_code":0}}
{"timestamp":"2026-05-08T10:01:30.000Z","type":"event_msg","payload":{"type":"agent_message","message":"Found 2 files.","phase":"final","memory_citation":null}}
{"timestamp":"2026-05-08T10:01:31.000Z","type":"response_item","payload":{"type":"reasoning","summary":[],"content":null,"encrypted_content":"AAAAencrypted"}}
{"timestamp":"2026-05-08T10:01:35.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"t1","last_agent_message":"Found 2 files."}}
```

- [ ] **Step 3: Write failing tests for `find_by_session_id` and `discover_by_prompt`**

Write `tests/test_oum_worker_codex_jsonl.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import codex_jsonl  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "oum_worker"
SIMPLE = FIXTURES / "codex_simple.jsonl"
WITH_TOOLS = FIXTURES / "codex_with_tools.jsonl"
SIMPLE_SID = "019e0500-0000-0000-0000-000000000001"
TOOLS_SID = "019e0500-0000-0000-0000-000000000002"
FIXTURE_CWD = Path("/tmp/codex-test-cwd")


def _materialize_codex_home(tmp_home: Path, fixture_path: Path,
                             sid: str, day: str = "2026/05/08") -> Path:
    """Copy a fixture into ~/.codex/sessions/<day>/rollout-<ts>-<sid>.jsonl
    under tmp_home and return the path."""
    sessions_dir = tmp_home / ".codex" / "sessions" / day
    sessions_dir.mkdir(parents=True, exist_ok=True)
    target = sessions_dir / f"rollout-2026-05-08T10-00-00-{sid}.jsonl"
    target.write_text(fixture_path.read_text(), encoding="utf-8")
    return target


def test_find_by_session_id_returns_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID)
    result = codex_jsonl.find_by_session_id(FIXTURE_CWD, SIMPLE_SID)
    assert result == target


def test_find_by_session_id_returns_none_when_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID)
    assert codex_jsonl.find_by_session_id(FIXTURE_CWD, "no-such-id") is None


def test_discover_by_prompt_finds_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID)
    sid = codex_jsonl.discover_by_prompt(
        FIXTURE_CWD, "hello codex",
        created_at="2026-05-08T10:00:30.000Z",
    )
    assert sid == SIMPLE_SID


def test_discover_by_prompt_returns_none_for_wrong_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID)
    assert codex_jsonl.discover_by_prompt(
        Path("/tmp/other-cwd"), "hello codex",
        created_at="2026-05-08T10:00:30.000Z",
    ) is None


def test_discover_by_prompt_returns_none_for_wrong_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize_codex_home(tmp_path, SIMPLE, SIMPLE_SID)
    assert codex_jsonl.discover_by_prompt(
        FIXTURE_CWD, "different prompt",
        created_at="2026-05-08T10:00:30.000Z",
    ) is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_codex_jsonl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'oum_worker.codex_jsonl'`.

- [ ] **Step 5: Create `codex_jsonl.py` with both functions**

Write `scripts/oum_worker/codex_jsonl.py`:

```python
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


def _candidate_dirs(root: Path, created_at_dt: datetime) -> list[Path]:
    """Return date-partitioned subdirs to scan: created_at's day plus
    the day before and after (UTC), filtering out any that don't exist."""
    out: list[Path] = []
    for delta_days in (0, -1, 1):
        d = (created_at_dt + timedelta(days=delta_days)).astimezone(timezone.utc)
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_codex_jsonl.py -v`
Expected: PASS for all 5 tests.

- [ ] **Step 7: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/codex_jsonl.py tests/test_oum_worker_codex_jsonl.py tests/fixtures/oum_worker/codex_simple.jsonl tests/fixtures/oum_worker/codex_with_tools.jsonl
git commit -m "$(cat <<'EOF'
codex_jsonl: find_by_session_id and discover_by_prompt

New parser module mirrors jsonl.py's surface for codex sessions
stored under ~/.codex/sessions/<YYYY>/<MM>/<DD>/. Discovery scans
the day-of, day-before, and day-after partitions, falling back to
a 7-day backward scan; matches by session_meta.cwd plus first
user_message text. Same mtime tiebreaker semantics as the Claude
parser.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `codex_jsonl.wait_for_idle`

**Files:**
- Modify: `scripts/oum_worker/codex_jsonl.py`
- Modify: `tests/test_oum_worker_codex_jsonl.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_codex_jsonl.py`:

```python
def test_wait_returns_idle_after_task_complete(tmp_path):
    """If the JSONL already has a task_complete after last_send_at,
    wait_for_idle returns idle quickly once the stable window passes."""
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(SIMPLE.read_text(), encoding="utf-8")
    result = codex_jsonl.wait_for_idle(
        jsonl_path,
        last_send_at="2026-05-08T10:00:30.000Z",
        timeout=2.0, stable_ms=200, poll_ms=50,
    )
    assert result.idle is True
    assert result.timed_out is False
    assert result.last_stop_reason == "task_complete"


def test_wait_times_out_when_no_task_complete(tmp_path):
    """JSONL has only task_started, no task_complete after last_send_at."""
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(
        '{"timestamp":"2026-05-08T10:00:00.000Z","type":"session_meta","payload":{"id":"x","cwd":"/x"}}\n'
        '{"timestamp":"2026-05-08T10:01:00.000Z","type":"event_msg","payload":{"type":"task_started"}}\n',
        encoding="utf-8",
    )
    result = codex_jsonl.wait_for_idle(
        jsonl_path,
        last_send_at="2026-05-08T10:00:30.000Z",
        timeout=0.6, stable_ms=200, poll_ms=50,
    )
    assert result.idle is False
    assert result.timed_out is True


def test_wait_short_circuits_when_alive_check_false(tmp_path):
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text("", encoding="utf-8")
    calls = {"n": 0}
    def alive():
        calls["n"] += 1
        return False
    result = codex_jsonl.wait_for_idle(
        jsonl_path,
        last_send_at="2026-05-08T10:00:30.000Z",
        timeout=2.0, stable_ms=200, poll_ms=50,
        alive_check=alive,
    )
    assert result.idle is False
    assert result.timed_out is False
    assert calls["n"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_codex_jsonl.py -k wait -v`
Expected: FAIL — `AttributeError: module 'oum_worker.codex_jsonl' has no attribute 'wait_for_idle'`.

- [ ] **Step 3: Append `wait_for_idle` to `codex_jsonl.py`**

Append to `scripts/oum_worker/codex_jsonl.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_codex_jsonl.py -v`
Expected: PASS for all 8 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/codex_jsonl.py tests/test_oum_worker_codex_jsonl.py
git commit -m "$(cat <<'EOF'
codex_jsonl: wait_for_idle polls for task_complete + stable window

Mirrors jsonl.wait_for_idle structure. Idle signal is event_msg
payload.type == 'task_complete' with timestamp > last_send_at.
Reuses jsonl.WaitResult so callers can branch on stop reason
uniformly across engines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `codex_jsonl.extract_response` and `dump_events`

**Files:**
- Modify: `scripts/oum_worker/codex_jsonl.py`
- Modify: `tests/test_oum_worker_codex_jsonl.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_codex_jsonl.py`:

```python
def test_extract_response_text_only():
    """Default: agent_message text concatenated, after `since`."""
    out = codex_jsonl.extract_response(
        SIMPLE, since="2026-05-08T10:00:30.000Z",
    )
    assert out == "Hi there!"


def test_extract_response_with_tool_use():
    out = codex_jsonl.extract_response(
        WITH_TOOLS, since="2026-05-08T10:00:30.000Z",
        include_tool_use=True,
    )
    assert "Found 2 files." in out
    assert "[tool_use exec_command" in out
    assert "ls -la" in out
    assert "[tool_result" in out
    assert "file1.txt" in out


def test_extract_response_with_thinking_renders_summary_and_encrypted():
    out = codex_jsonl.extract_response(
        WITH_TOOLS, since="2026-05-08T10:00:30.000Z",
        include_thinking=True,
    )
    assert "[thinking] need to list dir" in out
    assert "[thinking encrypted]" in out


def test_extract_response_skips_events_at_or_before_since():
    out = codex_jsonl.extract_response(
        SIMPLE, since="2026-05-08T10:01:30.000Z",
    )
    # agent_message is at 10:01:30 exactly, must be excluded
    assert out == ""


def test_dump_events_emits_lines_after_since():
    out = codex_jsonl.dump_events(
        SIMPLE, since="2026-05-08T10:00:30.000Z",
    )
    lines = out.split("\n")
    # All events after 10:00:30 are: task_started, user_message,
    # agent_message, task_complete = 4 lines
    assert len(lines) == 4
    assert all('"timestamp"' in line for line in lines)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_codex_jsonl.py -k 'extract or dump' -v`
Expected: FAIL — `AttributeError: module 'oum_worker.codex_jsonl' has no attribute 'extract_response'`.

- [ ] **Step 3: Append `extract_response` and `dump_events`**

Append to `scripts/oum_worker/codex_jsonl.py`:

```python
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
    events that have a non-empty summary, or [thinking encrypted]
    when only encrypted_content is present.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_codex_jsonl.py -v`
Expected: PASS for all 13 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/codex_jsonl.py tests/test_oum_worker_codex_jsonl.py
git commit -m "$(cat <<'EOF'
codex_jsonl: extract_response and dump_events

extract_response concatenates agent_message text by default;
--include-tool-use also emits function_call / function_call_output
markers (truncated to 500 chars); --include-thinking renders
reasoning summary entries or a [thinking encrypted] marker.

dump_events emits raw JSONL lines after `since`, matching the
shape of jsonl.dump_events for orchestrators that want the full
event stream.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `engines.py` with `ClaudeEngine` and `CodexEngine`

**Files:**
- Create: `scripts/oum_worker/engines.py`
- Create: `tests/test_oum_worker_engines.py`

- [ ] **Step 1: Write failing tests**

Write `tests/test_oum_worker_engines.py`:

```python
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
    # `codex resume <sid> [flags...]` — resume subcommand precedes flags
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


def test_codex_invocation_model_passes_m_flag():
    cmd = engines.get("codex").build_invocation(
        binary="codex",
        prompt_file=None, headless=False,
        resume=None, session_name=None, model="gpt-5",
        yolo=True, permission_mode=None, cwd=Path("/tmp"),
    )
    assert "-m gpt-5" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_engines.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'oum_worker.engines'`.

- [ ] **Step 3: Create `engines.py`**

Write `scripts/oum_worker/engines.py`:

```python
"""Engine strategy module.

Each engine knows how to build a shell invocation for its CLI and which
JSONL parser to use for capture / wait / ask. The CLI dispatches every
verb through `engines.get(state.engine).{build_invocation, jsonl_module}`
so the rest of the codebase doesn't branch on engine name.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Optional

from oum_worker import jsonl, codex_jsonl


@dataclass(frozen=True)
class Engine:
    name: str
    default_binary: str
    yolo_default: bool
    jsonl_module: ModuleType
    _build_invocation: callable  # injected at module load

    def build_invocation(self, **kwargs) -> str:
        return self._build_invocation(**kwargs)


def _claude_build(*, binary: str, prompt_file: Optional[Path],
                  headless: bool, resume: Optional[str],
                  session_name: Optional[str], model: Optional[str],
                  yolo: bool, permission_mode: Optional[str],
                  cwd: Path) -> str:
    """Mirrors the historical `_cc_invocation`. Claude does not use the
    cwd flag (zsh cd's into it before claude runs), so cwd is ignored.
    `model` is unused for claude; honoured by the engine layer to keep
    a single API.
    """
    parts: list[str] = [binary] + (["-p"] if headless else [])
    if resume:
        parts.extend(["--resume", shlex.quote(resume)])
    if (not headless) and session_name:
        parts.extend(["--name", shlex.quote(session_name)])
    if permission_mode:
        parts.extend(["--permission-mode", shlex.quote(permission_mode)])
    if yolo:
        parts.append("--dangerously-skip-permissions")
    if prompt_file is not None:
        parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    return " ".join(parts)


def _codex_build(*, binary: str, prompt_file: Optional[Path],
                 headless: bool, resume: Optional[str],
                 session_name: Optional[str], model: Optional[str],
                 yolo: bool, permission_mode: Optional[str],
                 cwd: Path) -> str:
    """Codex CLI shape:

      codex [exec] [resume <sid>] [--yolo] [-m model] [-C cwd] ["$(cat prompt)"]

    `resume` is a subcommand for codex (not a flag) and must precede
    the flag list. Headless uses the `exec` subcommand. session_name
    and permission_mode are silently ignored (claude-only concepts);
    the CLI emits a one-line warning at spawn time when those flags
    are explicitly passed for engine=codex.
    """
    if headless and prompt_file is None:
        raise ValueError("codex headless requires a prompt")

    parts: list[str] = [binary]
    if headless:
        parts.append("exec")
    if resume:
        parts.extend(["resume", shlex.quote(resume)])
    if yolo:
        parts.append("--yolo")
    if model:
        parts.extend(["-m", shlex.quote(model)])
    parts.extend(["-C", shlex.quote(str(cwd))])
    if prompt_file is not None:
        parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    return " ".join(parts)


_CLAUDE = Engine(
    name="claude",
    default_binary="claude",
    yolo_default=False,
    jsonl_module=jsonl,
    _build_invocation=_claude_build,
)

_CODEX = Engine(
    name="codex",
    default_binary="codex",
    yolo_default=True,
    jsonl_module=codex_jsonl,
    _build_invocation=_codex_build,
)

_REGISTRY = {"claude": _CLAUDE, "codex": _CODEX}


def get(name: str) -> Engine:
    if name not in _REGISTRY:
        raise ValueError(f"unknown engine {name!r} (known: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def known_names() -> list[str]:
    return sorted(_REGISTRY.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_engines.py -v`
Expected: PASS for all 14 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/engines.py tests/test_oum_worker_engines.py
git commit -m "$(cat <<'EOF'
engines: strategy module with ClaudeEngine and CodexEngine

Each engine builds its CLI invocation and points at its JSONL
parser. The CLI will dispatch every verb through
engines.get(state.engine) so the rest of the codebase never
branches on engine name.

ClaudeEngine.build mirrors the historical _cc_invocation.
CodexEngine.build emits 'codex [exec] [resume <sid>] [--yolo]
[-m model] -C <cwd> ["\$(cat prompt)"]'. Codex headless without
a prompt raises ValueError as defence in depth (CLI gates this
earlier).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `state.py` — `engine` field on `WorkerState`

**Files:**
- Modify: `scripts/oum_worker/state.py`
- Modify: `tests/test_oum_worker_state.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_state.py`:

```python
def test_engine_round_trips(tmp_path):
    """create with engine='codex' -> read returns engine='codex'."""
    from oum_worker import state as _state
    s = _state.create(tmp_path, label="cx", mode="interactive", cwd=tmp_path,
                      claude_bin="codex", tmux_session="x", engine="codex")
    assert s.engine == "codex"
    s2 = _state.read(tmp_path, "cx")
    assert s2.engine == "codex"


def test_engine_defaults_to_claude_when_missing(tmp_path):
    """An old state.json without `engine` should read as engine='claude'."""
    import json
    from oum_worker import state as _state
    wd = tmp_path / "legacy"
    wd.mkdir()
    state_path = wd / "state.json"
    # Hand-write a legacy state.json missing the 'engine' field.
    state_path.write_text(json.dumps({
        "label": "legacy",
        "mode": "interactive",
        "tmux_session": "x",
        "tmux_window": "legacy",
        "cwd": str(tmp_path),
        "claude_bin": "claude",
        "prompt_file": str(wd / "prompt.md"),
        "tmux_log": str(wd / "tmux.log"),
        "launchd_label": None,
        "plist_path": None,
        "session_id": None,
        "jsonl_path": None,
        "created_at": "2026-05-08T00:00:00.000Z",
        "started_at": None,
        "ended_at": None,
        "last_send_at": None,
        "last_capture_at": None,
    }))
    s = _state.read(tmp_path, "legacy")
    assert s.engine == "claude"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_state.py -k engine -v`
Expected: FAIL — `TypeError: state.create() got an unexpected keyword argument 'engine'` and `AttributeError: 'WorkerState' object has no attribute 'engine'`.

- [ ] **Step 3: Modify `WorkerState` and `create()` signatures**

Edit `scripts/oum_worker/state.py`. Change `WorkerState`:

```python
@dataclass
class WorkerState:
    label: str
    mode: str
    tmux_session: str
    tmux_window: str
    cwd: str
    claude_bin: str
    prompt_file: str
    tmux_log: str
    launchd_label: Optional[str]
    plist_path: Optional[str]
    session_id: Optional[str]
    jsonl_path: Optional[str]
    created_at: str
    started_at: Optional[str]
    ended_at: Optional[str]
    last_send_at: Optional[str]
    last_capture_at: Optional[str]
    engine: str = "claude"
```

(The `engine` default of `"claude"` is what makes legacy state.json files round-trip cleanly.)

- [ ] **Step 4: Modify `create()` to accept `engine`**

Edit `scripts/oum_worker/state.py:create()`:

```python
def create(
    workdir: Path,
    *,
    label: str,
    mode: str,
    cwd: Path,
    claude_bin: str,
    tmux_session: str,
    tmux_window: Optional[str] = None,
    launchd_label: Optional[str] = None,
    plist_path: Optional[str] = None,
    engine: str = "claude",
) -> WorkerState:
    wd = worker_dir(workdir, label)
    if state_path(workdir, label).exists():
        raise LabelExists(label)
    wd.mkdir(parents=True, exist_ok=True)
    s = WorkerState(
        label=label,
        mode=mode,
        tmux_session=tmux_session,
        tmux_window=tmux_window or label,
        cwd=str(cwd),
        claude_bin=claude_bin,
        prompt_file=str(wd / "prompt.md"),
        tmux_log=str(wd / "tmux.log"),
        launchd_label=launchd_label,
        plist_path=plist_path,
        session_id=None,
        jsonl_path=None,
        created_at=utc_now_iso(),
        started_at=None,
        ended_at=None,
        last_send_at=None,
        last_capture_at=None,
        engine=engine,
    )
    _write_locked(state_path(workdir, label), s)
    return s
```

- [ ] **Step 5: Update `read()` to default missing `engine` to `"claude"`**

The existing `read()` reads via:

```python
return WorkerState(**{k: data.get(k) for k in _FIELD_NAMES})
```

`data.get("engine")` returns `None` for legacy state.json. We want to coerce `None` to `"claude"`. Edit `read()`:

```python
def read(workdir: Path, label: str) -> WorkerState:
    p = state_path(workdir, label)
    if not p.exists():
        raise WorkerNotFound(label)
    with open(p, "r", encoding="utf-8") as f:
        _acquire_flock(f.fileno(), fcntl.LOCK_SH)
        try:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise WorkerNotFound(label) from e
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    fields = {k: data.get(k) for k in _FIELD_NAMES}
    if fields.get("engine") is None:
        fields["engine"] = "claude"
    return WorkerState(**fields)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_state.py -v`
Expected: PASS for all state tests including the two new ones.

- [ ] **Step 7: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/state.py tests/test_oum_worker_state.py
git commit -m "$(cat <<'EOF'
state: add engine field to WorkerState; default missing to 'claude'

WorkerState.engine is the per-label single source of truth for
which CLI to use (claude or codex). Legacy state.json files
without the field read as engine='claude' so existing labels keep
working unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `config.py` — `codex_bin` key

**Files:**
- Modify: `scripts/oum_worker/config.py`
- Modify: `tests/test_oum_worker_config.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_oum_worker_config.py`:

```python
def test_codex_bin_default_is_codex():
    from oum_worker import config as _cfg
    cfg = _cfg.WorkerConfig.defaults()
    assert cfg.codex_bin == "codex"


def test_codex_bin_loaded_from_json(tmp_path):
    import json
    from oum_worker import config as _cfg
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"codex_bin": "/opt/codex/bin/codex"}))
    cfg = _cfg.load_config(p)
    assert cfg.codex_bin == "/opt/codex/bin/codex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_config.py -k codex -v`
Expected: FAIL — `AttributeError: 'WorkerConfig' object has no attribute 'codex_bin'`.

- [ ] **Step 3: Read `config.py` to find the right insertion point**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && grep -n 'claude_bin' scripts/oum_worker/config.py`
Expected output: lines showing where `claude_bin` is declared and consumed.

- [ ] **Step 4: Add `codex_bin` to the dataclass and JSON loader**

Edit `scripts/oum_worker/config.py`:

1. In the `@dataclass` (or class) definition for `WorkerConfig`, add a `codex_bin: str` field with default `"codex"`. Place it next to `claude_bin`.
2. In `defaults()`, set `codex_bin="codex"` next to `claude_bin`.
3. In `load_config()` / wherever JSON keys are mapped to fields, add `"codex_bin"` to the list of recognized keys.
4. In `with_updates()` if it exists, ensure `codex_bin` is one of the fields it can update (most dataclass-based with_updates pick this up automatically via `dataclasses.replace`).

The exact edit depends on the file's current shape — the engineer should follow the same pattern `claude_bin` uses.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_config.py -v`
Expected: PASS for all config tests.

- [ ] **Step 6: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/config.py tests/test_oum_worker_config.py
git commit -m "$(cat <<'EOF'
config: add codex_bin key (default 'codex')

Parallel to claude_bin. Honored by --codex-bin / OUM_WORKER_CODEX_BIN
in subsequent CLI work. (Env var support to be added when CLI
spawn/schedule consume the value.)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `launchd.py` — `_cc_invocation` delegates; `build_inner_command` accepts `engine`

**Files:**
- Modify: `scripts/oum_worker/launchd.py`
- Modify: `tests/test_oum_worker_launchd.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_launchd.py`:

```python
def test_cc_invocation_still_works_via_engines(tmp_path):
    """_cc_invocation kept as a back-compat shim; should equal what the
    Claude engine produces."""
    from oum_worker import engines
    p = tmp_path / "p.md"
    cmd_legacy = launchd._cc_invocation(
        claude_bin="claude", resume=None, new_session=True,
        session_name=None, permission_mode=None,
        skip_permissions=False, prompt_file=p, headless=False,
    )
    cmd_engine = engines.get("claude").build_invocation(
        binary="claude", prompt_file=p, headless=False,
        resume=None, session_name=None, model=None,
        yolo=False, permission_mode=None, cwd=tmp_path,
    )
    assert cmd_legacy == cmd_engine


def test_build_inner_command_codex_uses_codex_engine(tmp_path):
    cmd = launchd.build_inner_command(
        cwd=tmp_path / "work",
        claude_bin="codex",  # binary path
        prompt_file=None,    # cold-start interactive
        log_path=tmp_path / "tmux.log",
        label="cx-int",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name=None,
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
        engine="codex",
    )
    assert "codex --yolo -C" in cmd or "codex --yolo" in cmd
    assert "$(cat" not in cmd
    # The mark-started runner step must still come before the codex
    # invocation, exactly like the claude path.
    assert "mark-started" in cmd
    assert cmd.index("mark-started") < cmd.index("codex")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_launchd.py -k 'cc_invocation_still_works or codex_uses_codex' -v`
Expected: FAIL — `build_inner_command` does not accept `engine` kwarg.

- [ ] **Step 3: Make `_cc_invocation` delegate to engines**

Edit `scripts/oum_worker/launchd.py:_cc_invocation`. Replace the body with:

```python
def _cc_invocation(*, claude_bin: str, resume: Optional[str], new_session: bool,
                   session_name: Optional[str], permission_mode: Optional[str],
                   skip_permissions: bool, prompt_file: Optional[Path],
                   headless: bool) -> str:
    """Back-compat shim. Delegates to engines.ClaudeEngine.

    Kept so that other tests and any external consumer that imported
    this helper continue to work.
    """
    # Local import to avoid a circular import at module load time
    # (engines imports launchd? No, engines imports jsonl + codex_jsonl
    # only, so a top-level import here is safe — but keeping it local
    # keeps the import graph minimal during tests.)
    from oum_worker import engines  # noqa: WPS433
    return engines.get("claude").build_invocation(
        binary=claude_bin,
        prompt_file=prompt_file,
        headless=headless,
        resume=resume,
        session_name=session_name if new_session else None,
        model=None,
        yolo=skip_permissions,
        permission_mode=permission_mode,
        cwd=Path.cwd(),  # claude ignores cwd in build; placeholder
    )
```

- [ ] **Step 4: Add `engine` kwarg to `build_inner_command`**

Edit `scripts/oum_worker/launchd.py:build_inner_command`. Add `engine: str = "claude"` to the signature and replace the body's `_cc_invocation` call with an engines-dispatch:

```python
def build_inner_command(*, cwd: Path, claude_bin: str,
                        prompt_file: Optional[Path],
                        log_path: Path, label: str, logs_dir: Path,
                        resume: Optional[str], new_session: bool,
                        session_name: Optional[str], permission_mode: Optional[str],
                        skip_permissions: bool, tmux_session: str,
                        headless: bool,
                        env_pairs: dict[str, str] | None = None,
                        scripts_dir: Path | None = None,
                        engine: str = "claude",
                        model: Optional[str] = None,
                        yolo: Optional[bool] = None) -> str:
    """Build the zsh command launchd executes when the job fires.

    `engine` selects which engines.Engine to use for the actual
    invocation. `yolo` defaults to None which means "use the engine's
    default" (claude=False, codex=True). Pass False explicitly to
    disable yolo for codex.
    """
    from oum_worker import engines  # noqa: WPS433

    eng = engines.get(engine)
    effective_yolo = eng.yolo_default if yolo is None else yolo
    # skip_permissions kept for back-compat with claude callers; treated
    # as `yolo` when set.
    if skip_permissions:
        effective_yolo = True

    mark = (
        f"PYTHONPATH={shlex.quote(str(scripts_dir or SCRIPTS_DIR))} "
        f"python3 -m oum_worker.runner mark-started "
        f"--label {shlex.quote(label)} "
        f"--logs-dir {shlex.quote(str(logs_dir))}"
    )
    cc = eng.build_invocation(
        binary=claude_bin,
        prompt_file=prompt_file,
        headless=headless,
        resume=resume,
        session_name=session_name if new_session else None,
        model=model,
        yolo=effective_yolo,
        permission_mode=permission_mode,
        cwd=cwd,
    )
    exports = _env_export_prefix(env_pairs)
    if headless:
        response = logs_dir / label / "response.txt"
        return (
            f"{exports}"
            f"cd {shlex.quote(str(cwd))} && "
            f"{mark} && "
            f"{cc} > {shlex.quote(str(response))} 2>&1"
        )
    from oum_worker.tmux import find_tmux_bin
    tmux_bin = shlex.quote(str(find_tmux_bin()))
    inner = f"{exports}cd {shlex.quote(str(cwd))} && {mark} && {cc}"
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_launchd.py -v`
Expected: PASS for all launchd tests including the two new ones.

- [ ] **Step 6: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/launchd.py tests/test_oum_worker_launchd.py
git commit -m "$(cat <<'EOF'
launchd: _cc_invocation delegates to engines; build_inner_command takes engine kwarg

_cc_invocation becomes a thin back-compat shim that calls
engines.get('claude').build_invocation; existing callers and
external imports keep working.

build_inner_command accepts engine='claude'|'codex' and dispatches
through the registry. New optional kwargs: engine, model, yolo.
skip_permissions=True still implies yolo=True for back-compat.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `cli.py` — `--engine`, `--codex-bin`, `--model`, `--yolo`/`--no-yolo` flags

**Files:**
- Modify: `scripts/oum_worker/cli.py`

- [ ] **Step 1: Add the new flags to `_add_spawn_args`**

Edit `scripts/oum_worker/cli.py:_add_spawn_args`. After the existing `--cc-command`/`--claude-bin` line, add:

```python
    sp.add_argument("--engine", choices=["claude", "codex"], default="claude",
                    help="Which engine to spawn (default: claude).")
    sp.add_argument("--codex-bin", dest="codex_bin", default=None,
                    help="Path to codex binary (used when --engine codex).")
    sp.add_argument("--model", default=None,
                    help="Model name (codex -m; ignored for claude).")
    yolo_group = sp.add_mutually_exclusive_group()
    yolo_group.add_argument("--yolo", dest="yolo", action="store_true",
                            default=None,
                            help="Bypass approvals (claude: --dangerously-skip-permissions; codex: --yolo). Default off for claude, on for codex.")
    yolo_group.add_argument("--no-yolo", dest="yolo", action="store_false",
                            help="Disable yolo (relevant for codex).")
```

- [ ] **Step 2: Add a binary-existence check helper**

Edit `scripts/oum_worker/cli.py`. Add near the other `_resolve_*` helpers:

```python
def _resolve_engine_binary(args: argparse.Namespace,
                           cfg: worker_config.WorkerConfig,
                           engine_name: str) -> str:
    if engine_name == "claude":
        return getattr(args, "claude_bin", None) or cfg.claude_bin
    if engine_name == "codex":
        return getattr(args, "codex_bin", None) or cfg.codex_bin
    raise ValueError(f"unknown engine {engine_name!r}")


def _verify_binary_exists(binary: str, engine_name: str) -> Optional[str]:
    """Return None if the binary is found; else an error string ready to
    print to stderr. Treats any value containing '/' as a literal path
    (must exist); else looks up via shutil.which."""
    import shutil as _sh
    if "/" in binary:
        if Path(binary).exists():
            return None
        return (f"error: {engine_name} binary not found at {binary!r} "
                f"(install {engine_name} or pass --{engine_name}-bin)")
    if _sh.which(binary):
        return None
    return (f"error: {engine_name} binary {binary!r} not on PATH "
            f"(install {engine_name} or pass --{engine_name}-bin)")
```

- [ ] **Step 3: Wire the binary check into `_handle_spawn` and `_handle_schedule`**

Edit both handlers. After the engine is resolved (which we'll do in Task 9 inside the handlers), add:

```python
    err = _verify_binary_exists(engine_binary, args.engine)
    if err:
        print(err, file=sys.stderr)
        return 5
```

The full integration of `args.engine` and `engine_binary` happens in Task 9 — this task only adds the parser surface and the helper; we'll consume them next.

- [ ] **Step 4: Smoke-test the parser surface**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m oum_worker.cli spawn --help 2>&1 | grep -E '\-\-(engine|codex-bin|model|yolo|no-yolo)'`
Expected: all five flags appear.

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ -v 2>&1 | tail -5`
Expected: all existing tests still pass (we haven't changed runtime behaviour yet — only added unused parser surface).

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py
git commit -m "$(cat <<'EOF'
cli: add --engine, --codex-bin, --model, --yolo / --no-yolo flags

Parser surface only — runtime dispatch wires up in the next commit.
--yolo and --no-yolo are a mutually-exclusive group; default None
means "use engine default" (claude=False, codex=True).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `cli.py` — engine routing in `spawn` and `schedule`

**Files:**
- Modify: `scripts/oum_worker/cli.py`
- Modify: `tests/test_oum_worker_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_cli.py`:

```python
# ---------- engine: codex ----------


@pytest.mark.skipif(TMUX_BIN is None, reason="tmux required")
def test_spawn_codex_writes_engine_in_state(tmp_path):
    stub = tmp_path / "stub-codex"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    try:
        r = _run_cli(
            "spawn",
            "--label", "cx",
            "--new",
            "--engine", "codex",
            "--codex-bin", str(stub),
            "--tmux-session", TEST_TMUX_SESSION,
            "--cwd", str(tmp_path),
            "--logs-dir", str(tmp_path / "logs"),
        )
        assert r.returncode == 0, r.stderr
        data = _json.loads((tmp_path / "logs" / "cx" / "state.json").read_text())
        assert data["engine"] == "codex"
    finally:
        _cleanup_tmux()


def test_spawn_codex_headless_requires_prompt(tmp_path):
    """`--engine codex --headless` without --prompt should exit cleanly."""
    stub = tmp_path / "stub-codex"
    stub.write_text('#!/bin/zsh\nexit 0\n')
    stub.chmod(0o755)
    r = _run_cli(
        "spawn",
        "--label", "cx-hl",
        "--new",
        "--engine", "codex",
        "--headless",
        "--codex-bin", str(stub),
        "--cwd", str(tmp_path),
        "--logs-dir", str(tmp_path / "logs"),
    )
    assert r.returncode == 1
    assert "prompt" in r.stderr.lower()


def test_spawn_codex_binary_missing_errors_early(tmp_path):
    r = _run_cli(
        "spawn",
        "--label", "cx-missing",
        "--new",
        "--engine", "codex",
        "--codex-bin", "/no/such/codex",
        "--cwd", str(tmp_path),
        "--logs-dir", str(tmp_path / "logs"),
    )
    assert r.returncode == 5
    assert "codex" in r.stderr.lower()
    assert "not found" in r.stderr.lower()


def test_schedule_codex_inner_command_has_yolo(tmp_path):
    plist_dir = tmp_path / "LaunchAgents"
    stub = tmp_path / "stub-codex"
    stub.write_text('#!/bin/zsh\nexit 0\n')
    stub.chmod(0o755)
    r = _run_cli(
        "schedule",
        "--in", "1h",
        "--label", "cx-sched",
        "--new",
        "--engine", "codex",
        "--codex-bin", str(stub),
        "--prompt", "hello",
        "--launch-agents-dir", str(plist_dir),
        "--no-bootstrap",
        "--cwd", str(tmp_path),
        "--tmux-session", TEST_TMUX_SESSION,
        "--logs-dir", str(tmp_path / "logs"),
    )
    assert r.returncode == 0, r.stderr
    import plistlib
    data = _json.loads((tmp_path / "logs" / "cx-sched" / "state.json").read_text())
    plist_path = plist_dir / (data["launchd_label"] + ".plist")
    parsed = plistlib.loads(plist_path.read_bytes())
    inner = " ".join(parsed["ProgramArguments"])
    assert "--yolo" in inner
    assert data["engine"] == "codex"


def test_schedule_codex_no_yolo_strips_flag(tmp_path):
    plist_dir = tmp_path / "LaunchAgents"
    stub = tmp_path / "stub-codex"
    stub.write_text('#!/bin/zsh\nexit 0\n')
    stub.chmod(0o755)
    r = _run_cli(
        "schedule",
        "--in", "1h",
        "--label", "cx-sched-noyolo",
        "--new",
        "--engine", "codex",
        "--no-yolo",
        "--codex-bin", str(stub),
        "--prompt", "hello",
        "--launch-agents-dir", str(plist_dir),
        "--no-bootstrap",
        "--cwd", str(tmp_path),
        "--tmux-session", TEST_TMUX_SESSION,
        "--logs-dir", str(tmp_path / "logs"),
    )
    assert r.returncode == 0, r.stderr
    import plistlib
    data = _json.loads((tmp_path / "logs" / "cx-sched-noyolo" / "state.json").read_text())
    plist_path = plist_dir / (data["launchd_label"] + ".plist")
    parsed = plistlib.loads(plist_path.read_bytes())
    inner = " ".join(parsed["ProgramArguments"])
    assert "--yolo" not in inner
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k 'codex' -v`
Expected: FAIL — multiple failures because the spawn/schedule handlers don't yet read `args.engine` or pass `engine=` to `state.create` / `build_inner_command`.

- [ ] **Step 3: Update `_handle_spawn` to use the engine**

Edit `scripts/oum_worker/cli.py:_handle_spawn`. Replace the body:

```python
def _handle_spawn(args: argparse.Namespace) -> int:
    cfg = config_from_args(args)
    workdir = workdir_from_args(args)
    cwd = _resolve_cwd(args, cfg)
    engine_binary = _resolve_engine_binary(args, cfg, args.engine)
    tmux_session = _resolve_tmux_session(args, cfg)
    prompt = _read_prompt(args)
    if args.headless and not prompt:
        print("error: --headless requires --prompt or --prompt-file",
              file=sys.stderr)
        return 1

    err = _verify_binary_exists(engine_binary, args.engine)
    if err:
        print(err, file=sys.stderr)
        return 5

    try:
        env_pairs = launchd.parse_env_pairs(getattr(args, "env", None))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.replace:
        try:
            _do_kill(workdir, args.label, purge=True)
        except state.WorkerNotFound:
            pass

    try:
        s = state.create(
            workdir,
            label=args.label,
            mode="headless" if args.headless else "interactive",
            cwd=cwd,
            claude_bin=engine_binary,
            tmux_session=tmux_session,
            engine=args.engine,
        )
    except state.LabelExists:
        print(f"error: label {args.label!r} already exists (pass --replace to overwrite)",
              file=sys.stderr)
        return 5

    Path(s.prompt_file).write_text(prompt, encoding="utf-8")

    if args.headless:
        return _spawn_headless(args, s, prompt, env_pairs=env_pairs)
    return _spawn_interactive(args, s, env_pairs=env_pairs)
```

- [ ] **Step 4: Update `_spawn_interactive` to dispatch through the engine**

Replace the existing body:

```python
def _spawn_interactive(args: argparse.Namespace, s: state.WorkerState,
                       *, env_pairs: dict[str, str] | None = None) -> int:
    from oum_worker import engines
    eng = engines.get(s.engine or "claude")
    prompt_text = Path(s.prompt_file).read_text(encoding="utf-8")
    prompt_arg: Optional[Path] = Path(s.prompt_file) if prompt_text else None

    yolo = eng.yolo_default if args.yolo is None else args.yolo
    # back-compat: --dangerously-skip-permissions still implies yolo for claude
    if args.skip_permissions:
        yolo = True

    cc = eng.build_invocation(
        binary=s.claude_bin,
        prompt_file=prompt_arg,
        headless=False,
        resume=args.resume,
        session_name=args.name if args.new_session else None,
        model=args.model,
        yolo=yolo,
        permission_mode=args.permission_mode,
        cwd=Path(s.cwd),
    )
    exports = launchd._env_export_prefix(env_pairs)
    inner = f"{exports}cd {shlex.quote(s.cwd)} && {cc}"
    pane_command = f"/bin/zsh -lic {shlex.quote(inner)}"
    _tmux.open_window(
        session=s.tmux_session,
        window=s.tmux_window,
        cwd=Path(s.cwd),
        command=pane_command,
        log_path=Path(s.tmux_log),
        replace=False,
    )
    state.update(workdir_from_args(args), s.label, started_at=state.utc_now_iso())
    print(f"Spawned interactive {s.engine} session {s.label}")
    print(f"  Window: {s.tmux_session}:{s.tmux_window}  →  oum-worker attach --label {s.label}")
    print(f"  Log:    {s.tmux_log}")
    return 0
```

- [ ] **Step 5: Update `_spawn_headless` similarly**

Replace the body:

```python
def _spawn_headless(args: argparse.Namespace, s: state.WorkerState, prompt: str,
                    *, env_pairs: dict[str, str] | None = None) -> int:
    from oum_worker import engines
    eng = engines.get(s.engine or "claude")
    yolo = eng.yolo_default if args.yolo is None else args.yolo
    if args.skip_permissions:
        yolo = True

    # Build the headless invocation as a shell-quoted command and run it
    # via /bin/zsh -lic so the chain matches the launchd path. We need
    # the prompt file to exist on disk; _handle_spawn already wrote it.
    inner = eng.build_invocation(
        binary=s.claude_bin,
        prompt_file=Path(s.prompt_file),
        headless=True,
        resume=args.resume,
        session_name=None,  # claude --name only meaningful in interactive
        model=args.model,
        yolo=yolo,
        permission_mode=args.permission_mode,
        cwd=Path(s.cwd),
    )

    response_path = Path(s.tmux_log).parent / "response.txt"
    state.update(workdir_from_args(args), s.label, started_at=state.utc_now_iso())

    subprocess_env: dict[str, str] | None = None
    if env_pairs:
        subprocess_env = {**os.environ, **env_pairs}

    cmd = ["/bin/zsh", "-lic", f"cd {shlex.quote(s.cwd)} && {inner}"]
    with open(response_path, "w") as out:
        r = subprocess.run(cmd, cwd=s.cwd, stdout=out, stderr=subprocess.STDOUT,
                           env=subprocess_env)
    state.update(workdir_from_args(args), s.label, ended_at=state.utc_now_iso())
    if r.returncode != 0:
        print(f"headless {s.engine} session {s.label} exited {r.returncode}",
              file=sys.stderr)
        return 4
    print(response_path.read_text())
    return 0
```

- [ ] **Step 6: Update `_handle_schedule` to pass engine and yolo through**

Edit `scripts/oum_worker/cli.py:_handle_schedule`. Find the body and apply these changes:

1. Right after `tmux_session = _resolve_tmux_session(args, cfg)`, replace the engine_binary resolution:

```python
    engine_binary = _resolve_engine_binary(args, cfg, args.engine)
```

2. After the prompt/headless gate, add the binary check:

```python
    err = _verify_binary_exists(engine_binary, args.engine)
    if err:
        print(err, file=sys.stderr)
        return 5
```

3. In the `state.create(...)` call, pass `engine=args.engine` and use `claude_bin=engine_binary`.

4. In the `launchd.build_inner_command(...)` call, pass:
   - `engine=args.engine`
   - `model=args.model`
   - `yolo=args.yolo` (None means "engine default", which build_inner_command handles)

The rest of `_handle_schedule` is unchanged.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k 'codex' -v`
Expected: PASS for all five new codex tests.

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ 2>&1 | tail -5`
Expected: 0 failures across the whole suite.

- [ ] **Step 8: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py tests/test_oum_worker_cli.py
git commit -m "$(cat <<'EOF'
cli: route spawn / schedule through the engine module

--engine selects the engine (default claude). engine_binary
resolves from --codex-bin/--cc-command + config + defaults; missing
binaries error early with exit 5. state.json records the engine.
spawn_interactive / spawn_headless / handle_schedule all dispatch
through engines.get(state.engine).build_invocation, so codex
spawns get the right shape (codex [exec] [resume <sid>] [--yolo]
[-m model] -C cwd ...) and yolo defaults on for codex.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `cli.py` — `_resolve_session_id` / capture / wait / ask dispatch through engine

**Files:**
- Modify: `scripts/oum_worker/cli.py`
- Modify: `tests/test_oum_worker_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_cli.py`:

```python
def test_capture_codex_uses_codex_jsonl(tmp_path, monkeypatch):
    """Capture on a codex worker reads codex_jsonl, not jsonl."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    workdir = tmp_path / "logs"
    workdir.mkdir()
    cwd = tmp_path / "proj"
    cwd.mkdir()

    # Materialize a codex session log under the fake HOME.
    sid = "019e0500-0000-0000-0000-000000000099"
    sess_dir = tmp_path / "fake-home" / ".codex" / "sessions" / "2026" / "05" / "08"
    sess_dir.mkdir(parents=True)
    fixture = ROOT / "tests" / "fixtures" / "oum_worker" / "codex_simple.jsonl"
    sess_path = sess_dir / f"rollout-2026-05-08T10-00-00-{sid}.jsonl"
    raw = fixture.read_text()
    # Patch fixture cwd to the test cwd and id to our sid
    raw = raw.replace("/tmp/codex-test-cwd", str(cwd.resolve()))
    raw = raw.replace("019e0500-0000-0000-0000-000000000001", sid)
    sess_path.write_text(raw)

    from oum_worker import state as _state
    s = _state.create(workdir, label="cx-cap", mode="interactive",
                      cwd=cwd, claude_bin="codex", tmux_session="x",
                      engine="codex")
    Path(s.prompt_file).write_text("hello codex", encoding="utf-8")
    _state.update(workdir, "cx-cap",
                  last_send_at="2026-05-08T10:00:30.000Z")

    r = _run_cli("capture", "--label", "cx-cap",
                 "--logs-dir", str(workdir),
                 env={**os.environ, "HOME": str(tmp_path / "fake-home")})
    assert r.returncode == 0, r.stderr
    assert "Hi there!" in r.stdout


def test_wait_codex_returns_zero_on_task_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    workdir = tmp_path / "logs"
    workdir.mkdir()
    cwd = tmp_path / "proj"
    cwd.mkdir()

    sid = "019e0500-0000-0000-0000-000000000098"
    sess_dir = tmp_path / "fake-home" / ".codex" / "sessions" / "2026" / "05" / "08"
    sess_dir.mkdir(parents=True)
    fixture = ROOT / "tests" / "fixtures" / "oum_worker" / "codex_simple.jsonl"
    sess_path = sess_dir / f"rollout-2026-05-08T10-00-00-{sid}.jsonl"
    raw = fixture.read_text()
    raw = raw.replace("/tmp/codex-test-cwd", str(cwd.resolve()))
    raw = raw.replace("019e0500-0000-0000-0000-000000000001", sid)
    sess_path.write_text(raw)

    from oum_worker import state as _state
    s = _state.create(workdir, label="cx-wait", mode="headless",
                      cwd=cwd, claude_bin="codex", tmux_session="x",
                      engine="codex")
    Path(s.prompt_file).write_text("hello codex", encoding="utf-8")
    _state.update(workdir, "cx-wait", session_id=sid,
                  jsonl_path=str(sess_path),
                  last_send_at="2026-05-08T10:00:30.000Z")

    r = _run_cli("wait", "--label", "cx-wait",
                 "--timeout", "3", "--stable-ms", "200", "--poll-ms", "100",
                 "--logs-dir", str(workdir),
                 env={**os.environ, "HOME": str(tmp_path / "fake-home")})
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k 'capture_codex or wait_codex' -v`
Expected: FAIL — `_resolve_session_id` and `_handle_wait` still hard-call `jsonl.*`, so a codex worker tries to read claude's session dir and finds nothing.

- [ ] **Step 3: Update `_resolve_session_id` to dispatch on engine**

Edit `scripts/oum_worker/cli.py:_resolve_session_id`:

```python
def _resolve_session_id(workdir: Path, s: state.WorkerState) -> state.WorkerState:
    if s.session_id:
        return s
    from oum_worker import engines
    engine_mod = engines.get(s.engine or "claude").jsonl_module
    prompt = Path(s.prompt_file).read_text(encoding="utf-8") if Path(s.prompt_file).exists() else ""
    sid = engine_mod.discover_by_prompt(Path(s.cwd), prompt, created_at=s.created_at)
    if not sid:
        return s
    jsonl_path = engine_mod.find_by_session_id(Path(s.cwd), sid)
    state.update(workdir, s.label,
                 session_id=sid,
                 jsonl_path=str(jsonl_path) if jsonl_path else None)
    return state.read(workdir, s.label)
```

- [ ] **Step 4: Update `_handle_capture` to dispatch on engine**

Edit `scripts/oum_worker/cli.py:_handle_capture`:

```python
def _handle_capture(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    s = _resolve_session_id(workdir, s)
    if not s.jsonl_path:
        return 0
    from oum_worker import engines
    engine_mod = engines.get(s.engine or "claude").jsonl_module
    since = args.since or s.last_send_at or s.created_at
    if args.full:
        out = engine_mod.dump_events(Path(s.jsonl_path), since=since)
    else:
        out = engine_mod.extract_response(
            Path(s.jsonl_path), since=since,
            include_thinking=args.include_thinking,
            include_tool_use=args.include_tool_use,
        )
    print(out)
    state.update(workdir, args.label, last_capture_at=state.utc_now_iso())
    return 0
```

- [ ] **Step 5: Update `_handle_wait` to dispatch on engine**

Edit `scripts/oum_worker/cli.py:_handle_wait`. Replace the `jsonl.wait_for_idle` call with an engine-dispatched one. The minimal diff:

```python
def _handle_wait(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    s = _resolve_session_id(workdir, s)
    if not s.jsonl_path:
        deadline = _time.monotonic() + args.discover_timeout
        while _time.monotonic() < deadline:
            s = _resolve_session_id(workdir, state.read(workdir, args.label))
            if s.jsonl_path:
                break
            _time.sleep(0.5)
        if not s.jsonl_path:
            print("session JSONL not found; check `oum-worker logs --launchd`", file=sys.stderr)
            return 2

    from oum_worker import engines
    engine_mod = engines.get(s.engine or "claude").jsonl_module

    def _alive() -> bool:
        return _tmux.window_exists(s.tmux_session, s.tmux_window) or s.mode == "headless"

    last_send = s.last_send_at or s.created_at
    result = engine_mod.wait_for_idle(
        Path(s.jsonl_path), last_send_at=last_send,
        timeout=args.timeout, stable_ms=args.stable_ms, poll_ms=args.poll_ms,
        alive_check=_alive,
    )
    if result.idle:
        return 0
    if result.timed_out:
        print(f"timeout after {args.timeout}s", file=sys.stderr)
        return 3
    state.update(workdir, args.label, ended_at=state.utc_now_iso())
    print("worker died before reply", file=sys.stderr)
    return 2
```

- [ ] **Step 6: `_handle_ask` already composes send + wait + capture**

`_handle_ask` calls the other three handlers, which now route through the engine. No change needed.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k 'codex' -v`
Expected: PASS for all codex-prefixed tests including capture and wait.

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ 2>&1 | tail -5`
Expected: 0 failures across the whole suite.

- [ ] **Step 8: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py tests/test_oum_worker_cli.py
git commit -m "$(cat <<'EOF'
cli: capture / wait / ask dispatch through engine.jsonl_module

_resolve_session_id, _handle_capture, _handle_wait now read
state.engine and call discover_by_prompt / find_by_session_id /
extract_response / dump_events / wait_for_idle on the right
parser. _handle_ask composes the others, so it picks this up
for free. ask now works on codex sessions end-to-end.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Engine-mismatched flag warnings + `--replace` cross-engine test

**Files:**
- Modify: `scripts/oum_worker/cli.py`
- Modify: `tests/test_oum_worker_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_cli.py`:

```python
def test_warning_on_engine_mismatched_flag(tmp_path):
    """`--engine codex --name foo` should succeed but emit a warning."""
    stub = tmp_path / "stub-codex"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    if TMUX_BIN is None:
        pytest.skip("tmux required")
    try:
        r = _run_cli(
            "spawn",
            "--label", "cx-warn",
            "--new",
            "--engine", "codex",
            "--name", "ignored-name",
            "--codex-bin", str(stub),
            "--tmux-session", TEST_TMUX_SESSION,
            "--cwd", str(tmp_path),
            "--logs-dir", str(tmp_path / "logs"),
        )
        assert r.returncode == 0, r.stderr
        assert "ignored for engine=codex" in r.stderr
        assert "--name" in r.stderr
    finally:
        _cleanup_tmux()


def test_replace_cross_engine_flips_state(tmp_path):
    """spawn label=foo engine=claude, then --replace with engine=codex.
    State.engine should be codex; the new prompt.md is whatever the
    second spawn wrote."""
    if TMUX_BIN is None:
        pytest.skip("tmux required")
    stub_claude = tmp_path / "stub-cc"
    stub_claude.write_text('#!/bin/zsh\nsleep 30\n')
    stub_claude.chmod(0o755)
    stub_codex = tmp_path / "stub-codex"
    stub_codex.write_text('#!/bin/zsh\nsleep 30\n')
    stub_codex.chmod(0o755)
    try:
        r1 = _run_cli(
            "spawn", "--label", "swap", "--new",
            "--prompt", "hi from claude",
            "--engine", "claude", "--cc-command", str(stub_claude),
            "--tmux-session", TEST_TMUX_SESSION,
            "--cwd", str(tmp_path),
            "--logs-dir", str(tmp_path / "logs"),
        )
        assert r1.returncode == 0, r1.stderr

        r2 = _run_cli(
            "spawn", "--label", "swap", "--new",
            "--engine", "codex", "--codex-bin", str(stub_codex),
            "--replace",
            "--tmux-session", TEST_TMUX_SESSION,
            "--cwd", str(tmp_path),
            "--logs-dir", str(tmp_path / "logs"),
        )
        assert r2.returncode == 0, r2.stderr

        data = _json.loads((tmp_path / "logs" / "swap" / "state.json").read_text())
        assert data["engine"] == "codex"
    finally:
        _cleanup_tmux()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k 'engine_mismatched or replace_cross_engine' -v`
Expected: FAIL — no warning emitted; replace test may pass or fail depending on whether state.json is recreated correctly.

- [ ] **Step 3: Add `_warn_engine_mismatches` helper and call it**

Edit `scripts/oum_worker/cli.py`. Add near the other helpers:

```python
def _warn_engine_mismatches(args: argparse.Namespace) -> None:
    """Emit one-line stderr warnings when claude-only flags are passed
    with --engine codex (or vice versa). Never fails the command —
    purely advisory."""
    eng = args.engine
    if eng == "codex":
        # Claude-only flags
        if getattr(args, "name", None):
            print("warning: --name ignored for engine=codex", file=sys.stderr)
        if getattr(args, "permission_mode", None):
            print("warning: --permission-mode ignored for engine=codex",
                  file=sys.stderr)
        if getattr(args, "skip_permissions", False):
            print("warning: --dangerously-skip-permissions ignored for engine=codex (use --yolo / --no-yolo)",
                  file=sys.stderr)
        if getattr(args, "claude_bin", None):
            print("warning: --cc-command/--claude-bin ignored for engine=codex",
                  file=sys.stderr)
    elif eng == "claude":
        # Codex-only flags
        if getattr(args, "model", None):
            print("warning: --model ignored for engine=claude",
                  file=sys.stderr)
        if getattr(args, "codex_bin", None):
            print("warning: --codex-bin ignored for engine=claude",
                  file=sys.stderr)
```

Then in `_handle_spawn` and `_handle_schedule`, call `_warn_engine_mismatches(args)` immediately after `engine_binary` is resolved:

```python
    engine_binary = _resolve_engine_binary(args, cfg, args.engine)
    _warn_engine_mismatches(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -v 2>&1 | tail -10`
Expected: all CLI tests pass.

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ 2>&1 | tail -5`
Expected: 0 failures across the whole suite.

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py tests/test_oum_worker_cli.py
git commit -m "$(cat <<'EOF'
cli: warn on engine-mismatched flags; verify --replace cross-engine

_warn_engine_mismatches emits one-line stderr warnings when a
caller passes claude-only flags with --engine codex (--name,
--permission-mode, --dangerously-skip-permissions, --cc-command)
or codex-only flags with --engine claude (--model, --codex-bin).
Never fails the command.

Cross-engine --replace already works (purges + recreates state.json
with the new engine); test locks the behaviour.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Update SKILL.md and README.md (in-repo)

**Files:**
- Modify: `skills/oum-worker/SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Update `skills/oum-worker/SKILL.md`**

Edit `skills/oum-worker/SKILL.md`:

1. Update the frontmatter description to mention the engine choice. Replace:

```
description: Use when scheduling, spawning, attaching to, sending to, capturing from, waiting on, asking, listing, killing, or checking status of Claude Code sessions. Replaces and supersedes `oum-schedule`.
```

with:

```
description: Use when scheduling, spawning, attaching to, sending to, capturing from, waiting on, asking, listing, killing, or checking status of Claude Code or Codex sessions. Pass --engine codex to use Codex CLI; default is Claude Code. Replaces and supersedes `oum-schedule`.
```

2. After the lead paragraph, add a new section:

```markdown
## Engines

`oum-worker` drives two CLIs interchangeably:

- **Claude Code** (default; `--engine claude`) — uses `claude`. YOLO is opt-in via `--yolo` or `--dangerously-skip-permissions`.
- **Codex CLI** (`--engine codex`) — uses `codex`. **YOLO is on by default** (passes `--yolo`); opt out with `--no-yolo`. Codex sessions live at `~/.codex/sessions/<YYYY>/<MM>/<DD>/`.

Every label is bound to one engine for its lifetime. To switch engines, respawn with `--replace --engine <other>`. The engine is recorded in `state.json` and downstream verbs (`capture`, `wait`, `ask`) automatically dispatch to the right session-log parser.

Engine-specific flags emit a stderr warning when passed against the wrong engine and are then ignored:

- claude-only: `--name`, `--permission-mode`, `--dangerously-skip-permissions`, `--cc-command` / `--claude-bin`
- codex-only: `--model`, `--codex-bin`
```

3. Append to "Common shapes":

```markdown
Spawn a codex interactive session (yolo on by default) and attach:

```bash
oum-worker --config .oum-worker.json spawn  --label cx --new --engine codex
oum-worker --config .oum-worker.json attach --label cx
```

Spawn a codex headless session and capture the reply:

```bash
oum-worker --config .oum-worker.json spawn --label cx-h --new --engine codex --headless --prompt "summarize repo"
oum-worker --config .oum-worker.json capture --label cx-h
```
```

4. Append to "Hard rules":

```markdown
- Codex's YOLO mode (`--yolo`) bypasses approvals and the sandbox. Default-on for codex was an explicit project decision; pass `--no-yolo` if you don't want it.
- A label is bound to one engine for its lifetime. To swap engines, respawn with `--replace --engine <other>`; the prompt and session id are reset.
```

- [ ] **Step 2: Update `README.md`**

Edit `README.md`:

1. Update the lead bullets to mention codex:

```markdown
- spawn Claude Code or Codex now in `tmux` (with or without a starting prompt) or headless mode
```

2. Replace the runtime dependency line for the CLI:

```markdown
- Claude Code CLI available as `claude`, `cc`, or a configured path (for `--engine claude`, default)
- Codex CLI available as `codex` or a configured path (for `--engine codex`)
```

3. Add a new "Engine selection" section before "Configuration":

```markdown
## Engine selection

Pass `--engine claude` (default) or `--engine codex` to `spawn` / `schedule`. The engine is recorded in `state.json`; `capture` / `wait` / `ask` automatically use the right session-log parser thereafter.

```bash
# Claude Code (default)
oum-worker --config .oum-worker.json spawn --label foo --new

# Codex CLI (yolo on by default; --no-yolo to opt out)
oum-worker --config .oum-worker.json spawn --label foo --new --engine codex
```

Codex's YOLO mode (`--yolo`, an alias for `--dangerously-bypass-approvals-and-sandbox`) bypasses approvals AND the sandbox. It is on by default for codex spawns; pass `--no-yolo` if you don't want it.
```

4. Update the commands block to include `--engine`:

```bash
oum-worker spawn    --label <label> [--engine claude|codex] (--new | --resume <session-id>) [--prompt TEXT | --prompt-file PATH] [--headless] [--yolo|--no-yolo] [--model M]
oum-worker schedule --label <label> [--engine claude|codex] (--in 30m | --at 18:00) (--new | --resume <session-id>) [--prompt TEXT | --prompt-file PATH] [--headless] [--yolo|--no-yolo] [--model M]
```

- [ ] **Step 3: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add skills/oum-worker/SKILL.md README.md
git commit -m "$(cat <<'EOF'
docs: SKILL.md and README cover engine selection and codex caveats

- New "Engines" section in SKILL.md with claude vs codex comparison,
  YOLO defaults, mismatched-flag warnings.
- New cold-start codex example and headless+capture codex example.
- New Hard rules: codex YOLO is default-on; engine is per-label
  for lifetime; swap with --replace.
- README adds "Engine selection" section with examples and updated
  commands block reflecting the new flags.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Update orchestrator skill copy (in `oum-os` repo)

**Files:**
- Modify: `/Users/tushar/Documents/OnceUponMe/oum-os/skills/oum-worker/SKILL.md`

- [ ] **Step 1: Mirror the new "Engines" section into the orchestrator skill**

Edit the file. Add the same "Engines" section after the lead paragraph, preserving all OUM-specific content. Add the same codex Common-shapes example. Add the same Hard rules.

- [ ] **Step 2: Stage only this file**

```bash
cd /Users/tushar/Documents/OnceUponMe/oum-os
git add skills/oum-worker/SKILL.md
git status -s | grep -v skills/oum-worker/SKILL.md | head -3
```

(Other unrelated edits in the orchestrator repo stay unstaged.)

- [ ] **Step 3: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/oum-os
git commit -m "$(cat <<'EOF'
skills/oum-worker: document codex engine support

Mirrors the upstream oum-worker repo SKILL.md changes so
orchestrator agents see codex as a peer engine.

Adds:
- Engines section with claude vs codex comparison and YOLO defaults
- Cold-start codex spawn + attach example
- Codex headless + capture example
- Hard rules: codex YOLO is default-on; one engine per label
- Note that engine-mismatched flags warn-and-ignore

OUM-specific content (config path, OUM_TASK_ID env injection,
tmux session 'oum', label prefix, etc.) preserved unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Final verification

- [ ] **Step 1: Run the full upstream-repo test suite**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ -v 2>&1 | tail -15`
Expected: all tests pass.

- [ ] **Step 2: Smoke-check the CLI surface**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m oum_worker.cli --help 2>&1 | head -25`
Expected: lists `attach` and the rest of the verbs.

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m oum_worker.cli spawn --help 2>&1 | grep -E '\-\-(engine|codex-bin|model|yolo|no-yolo)'`
Expected: all five new flags appear.

- [ ] **Step 3: End-to-end smoke against a stub codex binary**

```bash
cd /tmp && rm -rf oum-codex-smoke && mkdir oum-codex-smoke && cd oum-codex-smoke
cat > stub-codex <<'EOF'
#!/bin/zsh
echo "stub codex called with: $@"
sleep 30
EOF
chmod +x stub-codex

PYTHONPATH=/Users/tushar/Documents/OnceUponMe/os/oum-worker/scripts \
  python3 -m oum_worker.cli spawn \
    --label cx-smoke \
    --new \
    --engine codex \
    --codex-bin "$(pwd)/stub-codex" \
    --tmux-session oum-cx-smoke \
    --cwd "$(pwd)" \
    --logs-dir "$(pwd)/logs"

cat logs/cx-smoke/state.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print("engine:", d["engine"])'

PYTHONPATH=/Users/tushar/Documents/OnceUponMe/os/oum-worker/scripts \
  python3 -m oum_worker.cli list --logs-dir "$(pwd)/logs"

PYTHONPATH=/Users/tushar/Documents/OnceUponMe/os/oum-worker/scripts \
  python3 -m oum_worker.cli kill --label cx-smoke --tmux-session oum-cx-smoke --logs-dir "$(pwd)/logs" --purge

tmux kill-session -t oum-cx-smoke 2>/dev/null || true
```

Expected: spawn succeeds; state.json reports `engine: codex`; list shows the worker; kill cleans up.

- [ ] **Step 4: Commit any stray fix-ups**

If steps 1-3 surfaced issues, fix and commit. Otherwise this task is a no-op.

---

## Self-Review

**Spec coverage:**
- Goal 1 (per-spawn engine flag with default claude) → Tasks 8, 9 ✓
- Goal 2 (lifecycle verbs work for both engines) → Task 9 (spawn/schedule), Task 10 (capture/wait/ask), attach/kill/list/status/logs already engine-agnostic ✓
- Goal 3 (codex YOLO default-on, opt-out via --no-yolo) → Task 4 (yolo_default), Task 8 (parser), Task 9 (handler dispatch) ✓
- Goal 4 (codex_jsonl module mirrors jsonl.py surface) → Tasks 1, 2, 3 ✓
- Goal 5 (idle detection via task_complete) → Task 2 ✓
- Goal 6 (tool-use rendering for codex) → Task 3 ✓
- Goal 7 (capture/wait/ask all route through engine module) → Task 10 ✓
- Goal 8 (mixing engines per label via --replace) → Task 11 ✓

**Placeholder scan:** none.

**Type consistency:** `Engine.build_invocation` signature matches across all callers (claude `_cc_invocation` shim in launchd.py, `_spawn_interactive`, `_spawn_headless`, `build_inner_command`). `WorkerState.engine` is a `str` with default `"claude"` — `state.read()` coerces None to `"claude"` for legacy files. `engines.get(name)` raises `ValueError` consistently.

**Files-touched alignment:** Spec §6 lists 11 created/modified files; tasks together touch exactly: `scripts/oum_worker/{engines,codex_jsonl,cli,launchd,state,config}.py`, `tests/test_oum_worker_{engines,codex_jsonl,cli,launchd,state,config}.py`, `tests/fixtures/oum_worker/codex_{simple,with_tools}.jsonl`, `skills/oum-worker/SKILL.md`, `README.md`, plus the orchestrator-side `oum-os/skills/oum-worker/SKILL.md`. Matches.
