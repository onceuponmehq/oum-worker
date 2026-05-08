---
name: oum-worker
description: Use when scheduling, spawning, attaching to, sending to, capturing from, waiting on, asking, listing, killing, or checking status of Claude Code sessions. Replaces and supersedes `oum-schedule`.
---

# oum-worker

Use this skill to manage Claude Code sessions. A session is a Claude Code process running in a configured tmux session, identified by a caller-supplied label. Some sessions are scheduled (launchd), some are spawned now, some are headless one-shots (`claude -p`). The same CLI is used for human-driven interactive sessions and agent-driven automation.

Use `oum-worker` if installed from `pyproject.toml`; in a source checkout, `scripts/oum-worker` is the wrapper. Private deployment details such as repo aliases, timezone, launchd prefix, logs dir, tmux session, and Claude binary must come from `--config`, `OUM_WORKER_CONFIG`, or environment variables.

## When to use which verb

- `spawn`     start a session now (interactive in tmux, or headless)
- `schedule`  defer a session to a future time via launchd
- `attach`    drop your terminal into a running interactive session
- `send`      type a follow-up message into a live session's tmux pane
- `capture`   read the session's most recent reply (from Claude's JSONL session file)
- `wait`      block until the session is idle (stable for 1.5s after `end_turn`)
- `ask`       atomic send + wait + capture; the 80% verb for orchestrators
- `list`      show all known sessions and their derived states
- `status`    show one session
- `kill`      close the tmux window and unbootstrap any launchd plist
- `logs`      print the log path, or `--tail` it

Every session is identified by `--label`. The same label is the tmux window name and (for scheduled sessions) the launchd plist suffix. Pick a label the caller can remember, such as `feature-review`, `cofounder-q1`, or `nightly-summary`.

## Common shapes

Spawn a worker now and ask it something synchronously:

```bash
oum-worker --config .oum-worker.json spawn --label research-q1 --new --prompt "Check the release notes and summarize the breaking changes." --headless
oum-worker --config .oum-worker.json ask   --label research-q1 "Confirm with the source path."
```

Spawn an interactive worker and follow up later (different shells / sessions):

```bash
oum-worker --config .oum-worker.json spawn --label feature-014 --new --prompt "$(cat tasks/active/feature-014.md)"
oum-worker --config .oum-worker.json ask   --label feature-014 "What's the recommended next step?"
oum-worker --config .oum-worker.json kill  --label feature-014
```

Schedule a one-shot run (the existing oum-schedule pattern):

```bash
oum-worker --config .oum-worker.json schedule --in 3h --label nightly-summary --new --prompt "Run the nightly summary."
```

Drop into a fresh interactive Claude Code session in tmux (no initial prompt):

```bash
oum-worker --config .oum-worker.json spawn  --label adhoc --new
oum-worker --config .oum-worker.json attach --label adhoc
```

List and inspect:

```bash
oum-worker --config .oum-worker.json list
oum-worker --config .oum-worker.json status --label feature-014
oum-worker --config .oum-worker.json logs --label feature-014 --tail
```

## Hard rules

- Always pass `--label`; do not let the CLI auto-generate one from a timestamp when an orchestrator is the caller.
- Do not pass `--dangerously-skip-permissions` unless the user explicitly asks.
- Do not invent a session id; if `capture`/`status`/`wait` returns "session JSONL not found", let the CLI's prompt-match discovery resolve it once the worker has produced output.
- Do not edit `state.json` by hand; use the CLI verbs.
- Do not hardcode private repo paths or label prefixes in prompts or scripts; put them in config.
- If `oum-schedule` was previously used, prefer `oum-worker schedule` (the alias still works but prints a deprecation hint).
- If you spawn an interactive session without `--prompt`, `capture` / `wait` / `ask` cannot resolve the session id until the session has produced its first user message. Either pass `--prompt` up front, or send a real first message via `oum-worker send` before calling `capture` / `ask`.
- `attach` requires a tty and refuses on headless sessions; it is for human attachment, not orchestrators. Orchestrators use `send` / `ask`.

## State on disk

Per-worker directory under `.logs/oum-worker/<label>/`:

- `state.json`   — sidecar (label, mode, session id, timestamps, etc.)
- `prompt.md`    — initial prompt snapshot
- `tmux.log`     — pipe-pane capture of the tmux window
- `launchd.out` / `launchd.err` — only for scheduled workers

Timestamps inside `state.json` are UTC (Z suffix). Display uses the configured timezone.
