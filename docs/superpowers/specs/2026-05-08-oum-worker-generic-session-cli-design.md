# oum-worker as a generic Claude Code session CLI

**Status:** Approved (design)
**Date:** 2026-05-08
**Owner:** Tushar
**Approach:** A — Minimal additions

## Background

`oum-worker` is the macOS CLI that already handles spawning, scheduling, and
talking to Claude Code processes — interactive in `tmux` or headless via
`claude -p`. It is config-driven (no hardcoded private paths) and
intentionally narrow: spawn / schedule / send / capture / wait / ask /
list / status / kill / logs.

What it currently optimizes for: orchestrators driving background workers.
What it does *not* optimize for: a human typing one command to drop into an
interactive Claude Code session in tmux. That use case works today only by
passing a throw-away `--prompt` and then manually running `tmux attach -t
workers`.

The asks for this design:

1. One CLI, two audiences. A human can use the same tool to start an
   interactive session. An orchestrator agent uses the same tool to
   automate.
2. No breaking changes for callers that already script `oum-worker`.
3. No new state schema, no new transport, no fundamental rethinking.

## Goals

- Add a single new verb (`attach`) that lets a human enter the tmux pane
  for an existing labelled session, and refuses cleanly when called from a
  context where attaching would not work (headless mode, dead window,
  non-tty stdin).
- Make `--prompt` optional when spawning or scheduling an interactive
  session, so a user can start a cold Claude Code session with no initial
  message.
- Polish help text and `SKILL.md` so the tool reads as session-management,
  not just worker-management.

## Non-goals

- Renaming the `oum-worker` binary or any existing verb.
- Altering `state.json` schema, launchd plist generation, JSONL session
  discovery, or `config.py` keys.
- Wrapping `oum-worker` as an MCP server. (Could be a follow-up if agents
  benefit from a typed tool surface.)
- Multi-worker orchestration primitives (session pools, named groups,
  hand-off between human and agent). Out of scope.
- Auto-attach on spawn. The chosen UX is two explicit verbs (`spawn` then
  `attach`), not a flag on spawn.

## Design

### 1. New verb: `attach`

```
oum-worker attach --label <label>
```

Behaviour, in order:

1. Resolve `workdir` from config / flags exactly like every other verb.
2. `state.read(workdir, label)` to load the worker. On `WorkerNotFound`,
   print `no worker named '<label>'` to stderr, exit 1.
3. If `s.mode == "headless"`, print `cannot attach to headless worker
   '<label>'` to stderr, exit 2. Headless workers have no tmux window.
4. If `_tmux.window_exists(s.tmux_session, s.tmux_window)` is false, print
   `worker '<label>' window is not alive (try respawning with --replace)`
   to stderr, exit 2.
5. If `sys.stdin.isatty()` is false, print `attach requires a tty (you
   probably meant 'oum-worker send' or 'oum-worker ask')` to stderr, exit
   2. This is the agent-mistake guard — without it, `tmux attach`
   inherits a non-tty stdin and either hangs or errors deep in tmux.
6. Run `tmux select-window -t <session>:<window>` so the user lands on
   the right pane on attach (the pane is in the right session, but `tmux
   attach` reattaches to whichever window was last active).
7. `os.execvp("tmux", ["tmux", "attach", "-t", session])` to replace the
   Python process with tmux. Detach (Ctrl-B D) returns control to the
   user's shell, not to `oum-worker`.

The verb adds **no new state fields, no new config keys, no new modules**.
It lives in `cli.py` next to `_handle_kill` and uses the existing
`_tmux` module.

### 2. `--prompt` becomes optional for interactive

Currently `_add_spawn_args` puts `--prompt` and `--prompt-file` in a
mutually-exclusive group with `required=True`. Both `_handle_spawn` and
`_handle_schedule` then call `_read_prompt(args)` and reject empty
strings.

Change:

- `_add_spawn_args`: drop `required=True` from the prompt group.
- `_handle_spawn`: replace `if not prompt: error` with
  `if args.headless and not prompt: error`. The headless code path
  literally invokes `claude -p <prompt>` and there is nothing useful to
  do without a prompt.
- `_handle_schedule`: same change. A scheduled interactive session can
  legitimately open cold (e.g. "open Claude in tmux at 6pm so I can use
  it then").

Mechanics for the empty-prompt interactive case:

- `_handle_spawn` still calls `Path(s.prompt_file).write_text(prompt,
  encoding="utf-8")` so the file exists (kept for state consistency
  and for the case where the user later sends a message and we want a
  place to record the seed). The file is empty.
- `launchd._cc_invocation` currently always appends
  `"$(cat <prompt_file>)"` as a positional arg to `claude`. With an
  empty file this would expand to `claude ""`, which would try to
  start `claude` with an empty initial message. We make `prompt_file`
  in `_cc_invocation` an `Optional[Path]`: when `None`, the helper
  skips the trailing positional arg and the resulting command is just
  `claude` with its other flags. The cold-start tmux pane then runs
  `claude` exactly the way a user would by hand.
- `_spawn_interactive` passes `prompt_file=None` when `prompt == ""`,
  otherwise `Path(s.prompt_file)` as today. Same change in
  `build_inner_command` for the scheduled-interactive case.
- The headless path is unaffected: `_handle_spawn` rejects empty
  prompts before reaching `_cc_invocation`, so `prompt_file` is
  always set in headless invocations.
- `_resolve_session_id` calls `jsonl.discover_by_prompt`, which
  iterates JSONL files in the cwd's projects dir and matches each
  file's first user message against the strip()'d prompt text. An
  empty prompt would match a JSONL whose first user message is also
  empty — in practice no Claude Code session has an empty first user
  message, so `discover_by_prompt` returns `None`. Therefore
  `capture` / `wait` / `ask` cannot resolve the session id for a
  cold-started interactive worker. The user must either pass a real
  `--prompt` up front or send a real first message via `oum-worker
  send` before invoking `capture` / `ask`. We do not change the
  discovery logic to accommodate this (any "newest JSONL in cwd"
  fallback risks attaching the wrong session).

Documented user-facing limitation: spawn-without-prompt is for human
interactive use; if you want `capture` / `ask` from an orchestrator,
either pass `--prompt` or send a real first message via `oum-worker
send` before calling `capture` / `ask`.

### 3. Polish

- `_build_parser`'s description: `"Manage Claude Code worker sessions."`
  → `"Manage Claude Code sessions (interactive in tmux or headless)."`
- `spawn` help: `"Start a Claude Code worker now."` → `"Start a Claude
  Code session now."`
- `schedule` help: `"Schedule a Claude Code worker for later via
  launchd."` → `"Schedule a Claude Code session for later via launchd."`
- `attach` help: `"Attach your terminal to a running interactive
  session."`
- `SKILL.md` (`skills/oum-worker/SKILL.md`):
  - Reword the lead paragraph from "worker sessions" to "Claude Code
    sessions". `worker` stays in the schema field names (`mode:
    headless` etc.) but disappears from prose.
  - Add `attach` to the verb table.
  - Add the cold-start caveat: "If you spawn interactively without a
    `--prompt`, `capture` / `ask` cannot resolve a session id until
    you've sent at least one real message into the tmux pane."
- `README.md`: add `attach` to the commands block. No deeper rewrite.

`launchd_label_prefix` and other config keys with the word "worker" in
them stay. Touching them would break every existing local config and is
out of scope.

### 4. Tests

`tests/test_oum_worker_cli.py` (or sibling, matching existing test
naming) gets:

- `test_attach_missing_label` — exits 1 on unknown label.
- `test_attach_headless_rejected` — creates a state in `mode=headless`,
  asserts exit 2 with the right stderr.
- `test_attach_dead_window` — monkeypatches `_tmux.window_exists` to
  return False, asserts exit 2.
- `test_attach_no_tty` — monkeypatches `sys.stdin.isatty` to return
  False, asserts exit 2.
- `test_attach_calls_tmux` — monkeypatches the small `_do_attach(session,
  window)` helper (introduced so this is testable without `execvp`'ing)
  and asserts it is called with the right arguments.
- `test_spawn_interactive_no_prompt` — `--label foo --new --interactive`
  with no prompt: state created, `prompt.md` is empty, exit 0. Also
  asserts the tmux command issued does NOT contain `$(cat .../prompt.md)`.
- `test_spawn_headless_no_prompt` — `--label foo --new --headless` with
  no prompt: exit 1, error mentions `--headless requires --prompt`.
- `test_spawn_headless_empty_prompt_string` — `--prompt ""` (explicit
  empty) on headless: same exit 1 (same gate as missing prompt).
- `test_cc_invocation_omits_prompt_when_none` — unit test on
  `launchd._cc_invocation` directly: with `prompt_file=None`, the
  resulting command string ends with the last flag and has no
  trailing `"$(cat ...)"` segment.
- `test_schedule_interactive_no_prompt` — `--label foo --new --in 30m
  --launch-agents-dir <tmp>`: succeeds with empty prompt, plist
  written, plist's inner command does NOT contain a `cat <prompt
  file>` substitution.

The `execvp` itself is not tested. The verb's logic is split into a
`_handle_attach(args)` (validation gates, returns int on rejection,
otherwise calls `_do_attach`) and `_do_attach(session, window)`
(focuses the window, execs tmux). Tests target `_handle_attach` and a
mocked `_do_attach`.

### 5. Files touched

- `scripts/oum_worker/cli.py` — new verb wiring, prompt-optional logic,
  help-text edits.
- `scripts/oum_worker/launchd.py` — `_cc_invocation` accepts
  `Optional[Path]` for `prompt_file` and skips the trailing
  `"$(cat ...)"` arg when None. `build_inner_command` passes None
  for cold-started interactive sessions.
- `skills/oum-worker/SKILL.md` — verb table, prose, caveat.
- `README.md` — one-line addition.
- `tests/` — new tests above; existing tests for `launchd._cc_invocation`
  / `build_inner_command` get a no-prompt case.

No changes to `tmux.py`, `state.py`, `jsonl.py`, `config.py`,
`runner.py`, `pyproject.toml`, or any config example.

### 6. Backwards compatibility

- All existing verbs and flags retain their names and behaviour.
- `--prompt` going from required to optional is a relaxation; every
  existing call site keeps working.
- `attach` is a brand-new verb; nothing collides.
- `state.json` schema unchanged.
- No env var or config key changes.

The only external visible change is help text. Scripts that grep
`oum-worker --help` for verb names still find them.

## Risks and edge cases

- **`tmux select-window` failure.** If the window dies between
  `window_exists` and `select-window`, `select-window` errors and the
  attach aborts. The user sees a tmux error and can re-run after
  respawning. We do not paper over this.
- **Multiple sessions sharing the configured `tmux_session`.** The
  current model already uses one tmux session per `tmux_session` config
  key, with one window per label. `attach` reuses that. Two labels in
  the same session means `attach` brings the user to a focused window
  but they can still cycle to the other window with `Ctrl-B N`. This is
  by design.
- **Empty-prompt interactive then `oum-worker ask`.** Documented
  limitation; the CLI cannot make this work without a prompt-match
  fallback. Out of scope for this change.
- **`os.execvp("tmux", ...)`.** The Python process is replaced. Anything
  that wraps `oum-worker` in a script and expects to run code after
  `attach` returns — won't see that code execute. This matches `ssh
  host` semantics and is the right call for a "drop me into tmux"
  verb. Documented in the help text.

## Open questions

None.

## Appendix — concrete UX after this change

Human flow:

```
$ oum-worker spawn --label review --new
Spawned interactive session review
  Window: workers:review  →  oum-worker attach --label review
  Log:    .logs/oum-worker/review/tmux.log

$ oum-worker attach --label review
... user is now inside the tmux pane talking to Claude ...
... Ctrl-B D to detach ...

$ oum-worker attach --label review
... reattaches to the same pane ...

$ oum-worker kill --label review --purge
```

Agent flow (unchanged from today):

```
oum-worker spawn --label feature-014 --new --prompt "$(cat task.md)" --headless
oum-worker spawn --label feature-014 --new --prompt "$(cat task.md)"
oum-worker ask   --label feature-014 "What's the next step?"
oum-worker capture --label feature-014 --include-tool-use
oum-worker kill  --label feature-014 --purge
```
