---
name: oum-worker
description: Use when scheduling, spawning, sending to, capturing from, waiting on, asking, listing, killing, or checking status of Claude Code worker sessions on Tushar's Mac. Replaces and supersedes `oum-schedule`.
---

# oum-worker

Use this skill to manage Claude Code worker sessions. Workers are `cc` (Claude Code) processes running in a shared tmux session named `oum`, identified by a caller-supplied label. Some workers are scheduled (launchd, future), some are spawned now, some are headless one-shots (`claude -p`).

Run from `/Users/tushar/Documents/OnceUponMe/oum-os`. The CLI is `scripts/oum-worker`.

## When to use which verb

- `spawn`     start a worker now (interactive in tmux, or headless)
- `schedule`  defer a worker to a future time via launchd
- `send`      type a follow-up message into a live worker's tmux pane
- `capture`   read the worker's most recent reply (from Claude's JSONL session file)
- `wait`      block until the worker is idle (stable for 1.5s after `end_turn`)
- `ask`       atomic send + wait + capture; the 80% verb for orchestrators
- `list`      show all known workers and their derived states
- `status`    show one worker
- `kill`      close the tmux window and unbootstrap any launchd plist
- `logs`      print the log path, or `--tail` it

Every worker is identified by `--label`. The same label is the tmux window name and (for scheduled workers) the launchd plist suffix. Pick a label the caller can remember (for example `keeley-014`, `cof-q1`, `nightly-eod`).

## Common shapes

Spawn a worker now and ask it something synchronously:

```bash
scripts/oum-worker spawn --label cof-q1 --new --prompt "Look up the GST rule for Anarock POS." --headless
scripts/oum-worker ask   --label cof-q1 "Confirm with the source path."
```

Spawn an interactive worker and follow up later (different shells / sessions):

```bash
scripts/oum-worker spawn --label keeley-014 --new --prompt "$(cat tasks/active/keeley-014.md)"
scripts/oum-worker ask   --label keeley-014 "What's the recommended budget split?"
scripts/oum-worker kill  --label keeley-014
```

Schedule a one-shot run (the existing oum-schedule pattern):

```bash
scripts/oum-worker schedule --in 3h --label nightly-eod --new --prompt "Run the EOD summary."
```

List and inspect:

```bash
scripts/oum-worker list
scripts/oum-worker status --label keeley-014
scripts/oum-worker logs --label keeley-014 --tail
```

## Hard rules

- Always pass `--label`; do not let the CLI auto-generate one from a timestamp when an orchestrator is the caller.
- Do not pass `--dangerously-skip-permissions` unless the user explicitly asks.
- Do not invent a session id; if `capture`/`status`/`wait` returns "session JSONL not found", let the CLI's prompt-match discovery resolve it once the worker has produced output.
- Do not edit `state.json` by hand; use the CLI verbs.
- If `oum-schedule` was previously used, prefer `oum-worker schedule` (the alias still works but prints a deprecation hint).

## State on disk

Per-worker directory under `.logs/oum-worker/<label>/`:

- `state.json`   — sidecar (label, mode, session id, timestamps, etc.)
- `prompt.md`    — initial prompt snapshot
- `tmux.log`     — pipe-pane capture of the tmux window
- `launchd.out` / `launchd.err` — only for scheduled workers

Timestamps inside `state.json` are UTC (Z suffix). Display layer converts to IST.
