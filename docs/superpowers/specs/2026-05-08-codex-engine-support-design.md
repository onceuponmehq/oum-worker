# Codex engine support for oum-worker

**Status:** Approved (design)
**Date:** 2026-05-08
**Owner:** Tushar
**Approach:** A — engine module with strategy pattern, full parity for capture / wait / ask

## Background

`oum-worker` today is built around Claude Code conventions. The CLI invocation
shape, the JSONL session-file location, and the idle-detection heuristics are
all Claude-specific. The new ask: support OpenAI's Codex CLI as a peer engine,
so a single `oum-worker spawn ... --engine codex` command produces a codex
session in tmux that the rest of the verbs (`attach`, `send`, `capture`,
`wait`, `ask`, `kill`, `list`, `status`, `logs`, `schedule`) operate on
identically.

Earlier brainstorming considered a smaller scope — codex would only get
lifecycle verbs and a tmux-pane "capture", with `wait` / `ask` erroring. That
got expanded after researching codex's session log format: codex emits a
clean `task_complete` event at end-of-turn, which makes idle detection and
clean response extraction tractable. So the agreed scope is full parity:
both engines are first-class citizens.

## Goals

1. **Per-spawn engine selection.** `oum-worker spawn` and `oum-worker
   schedule` accept `--engine claude|codex`. Default is `claude` if the flag
   is omitted. Engine is recorded in `state.json` at spawn time and is the
   single source of truth for every later verb. No config-driven default.
2. **All lifecycle verbs work for both engines.** `attach`, `send`, `kill`,
   `list`, `status`, `logs` are engine-agnostic and work without code
   changes beyond reading `state.engine` where they need to.
3. **Codex defaults to YOLO.** A codex spawn passes `--yolo` (the hidden
   alias for `--dangerously-bypass-approvals-and-sandbox`) by default.
   `--no-yolo` opts out. Claude's behaviour is unchanged: yolo is opt-in
   via the existing `--dangerously-skip-permissions` flag, which a new
   user-facing `--yolo` flag can also map to.
4. **Codex JSONL parsing** with the same surface as the Claude parser:
   `discover_by_prompt`, `find_by_session_id`, `wait_for_idle`,
   `extract_response`, `dump_events`. Implemented in a new
   `codex_jsonl.py` module.
5. **Idle detection for codex** by polling for an `event_msg` with
   `payload.type == "task_complete"` whose timestamp is later than
   `last_send_at`, then waiting for a stable quiet window matching the
   `stable_ms` argument. Same `WaitResult` shape as the Claude parser.
6. **Tool-use rendering for codex** — `--include-tool-use` shows
   `function_call` (name + args, truncated) and `function_call_output`
   (truncated to 500 chars). `--include-thinking` shows `reasoning`
   (using the `summary` array if present, else a `[thinking encrypted]`
   marker).
7. **Capture / wait / ask all route through the right per-engine
   parser.** No tmux-pane fallback is needed any more; both engines
   expose the same verb-level behaviour even though the underlying log
   files are in different locations and have different shapes.
8. **Mixing engines per label** — a label can be `--replace`'d with a
   different engine. State.json's `engine` field reflects the currently
   running engine; downstream verbs always read it from there, not from
   CLI flags. No special migration code: `--replace` already purges the
   worker dir, and `spawn` re-creates it with the new engine.

## Non-goals

- No `~/.codex/sessions` retention or cleanup (codex's own concern).
- No fork support beyond passing through the session id to `codex resume
  <sid>`. Codex's `codex fork <sid>` subcommand is not exposed.
- No simultaneous-multi-engine under one label. One engine at a time per
  label, swappable via `--replace`.
- No migration of in-flight Claude sessions to codex or vice versa.
- No new top-level binary or rename. Stays `oum-worker`.
- No translation between engine-specific concepts beyond what already
  exists (e.g. claude `--name` and codex `-m model` stay engine-specific
  and emit a warning if combined with the wrong `--engine`).

## Design

### 1. Engine module — `scripts/oum_worker/engines.py`

A new module with a small strategy interface:

```python
class Engine(Protocol):
    name: str                                # "claude" | "codex"
    default_binary: str                      # "claude" | "codex"
    yolo_default: bool                       # claude=False, codex=True
    jsonl_module: ModuleType                 # jsonl or codex_jsonl

    def build_invocation(
        self, *,
        binary: str,
        prompt_file: Optional[Path],
        headless: bool,
        resume: Optional[str],
        session_name: Optional[str],
        model: Optional[str],
        yolo: bool,
        permission_mode: Optional[str],
        cwd: Path,
    ) -> str:
        ...
```

Two implementations:

**ClaudeEngine.build_invocation** — lifts the existing `_cc_invocation`
body verbatim. `yolo=True` adds `--dangerously-skip-permissions`.
`prompt_file=None` skips the trailing `"$(cat ...)"`. `model`,
`session_name`, `permission_mode` honoured exactly as today.

**CodexEngine.build_invocation:**
- Headless → `<bin> exec [resume <sid>] [--yolo] [-m <model>] [-C <cwd>]
  "$(cat <prompt-file>)"`. If `prompt_file is None`, raise `ValueError`
  ("codex headless requires a prompt"). The CLI gates this earlier with
  a clean error message; the engine's raise is defence in depth.
- Interactive → `<bin> [resume <sid>] [--yolo] [-m <model>] [-C <cwd>]
  ["$(cat <prompt-file>)"]`. Skip the trailing prompt arg when
  `prompt_file is None` for cold-start.
- `session_name` and `permission_mode` are silently ignored for codex
  (the CLI emits a one-line warning to stderr at spawn time so the user
  knows their flag was a no-op).
- `resume` is implemented as the `resume <sid>` *subcommand* preceding
  flags: `codex resume <sid> [--yolo] [-m <model>] ...`. (Codex uses a
  subcommand, not a flag.) For headless: `codex exec resume <sid> ...`.

A factory:

```python
def get(name: str) -> Engine: ...
```

returns the engine for `name`, raising `ValueError` for an unknown name.

`launchd._cc_invocation` becomes a thin pass-through to
`engines.get("claude").build_invocation(...)` so existing callers and
tests keep working. `launchd.build_inner_command` gains an `engine: str
= "claude"` keyword argument and dispatches.

### 2. Codex JSONL parser — `scripts/oum_worker/codex_jsonl.py`

Parallel surface to `jsonl.py`. Functions:

```python
def find_by_session_id(cwd: Path, session_id: str) -> Optional[Path]: ...

def discover_by_prompt(cwd: Path, prompt: str, *, created_at: str,
                       tiebreaker_window_seconds: int = 300) -> Optional[str]: ...

def wait_for_idle(jsonl_path: Path, *, last_send_at: str,
                  timeout: float = 600.0, stable_ms: int = 1500,
                  poll_ms: int = 500,
                  alive_check=lambda: True) -> WaitResult: ...

def extract_response(jsonl_path: Path, *, since: str,
                     include_thinking: bool = False,
                     include_tool_use: bool = False) -> str: ...

def dump_events(jsonl_path: Path, *, since: str) -> str: ...
```

`WaitResult` is the same dataclass as `jsonl.WaitResult` (shared from
`jsonl` to avoid duplication; `codex_jsonl` imports it).

#### Storage layout

Codex sessions live at:

```
~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<ISO>-<sid>.jsonl
```

`<sid>` matches the `payload.id` field of the first line, which is the
`session_meta` event. The cwd is in `session_meta.payload.cwd` (absolute
path string). There is no per-cwd directory the way Claude has — every
session for every cwd shares the date-partitioned tree.

#### `find_by_session_id`

Glob `~/.codex/sessions/**/*.jsonl` for files matching
`*-<session_id>.jsonl`. (The session id is the suffix of the filename
before `.jsonl`.) Verify by reading the first line and checking
`session_meta.payload.id == session_id`. Return path or None.

#### `discover_by_prompt`

Bound the search by date to keep it cheap: parse `created_at` (UTC
ISO) into a date, scan the directory for that date plus the day
before and the day after (handles UTC midnight crossings). If no
candidates match, fall back to a wider scan of the last 7 days.

For each candidate:
1. Read first line; require `type == "session_meta"`.
2. Verify `payload.cwd == str(cwd.resolve())`.
3. Read forward until the first `event_msg` with
   `payload.type == "user_message"`.
4. Compare `payload.message.strip()` to `prompt.strip()`.
5. Record (mtime distance to `created_at`, `payload.id` from
   `session_meta`).

Mtime tiebreaker: pick the candidate with smallest delta to
`created_at.timestamp()`. If best delta exceeds
`tiebreaker_window_seconds` and there is more than one candidate,
return None (ambiguous). Same logic as the Claude parser.

#### `wait_for_idle`

Tail the file. Each `event_msg` whose `payload.type == "task_complete"`
and whose `timestamp > last_send_dt` flips a `saw_terminal` flag and
captures `payload.last_agent_message` for the result. Once the flag is
set AND the file has been quiet for `stable_ms`, return idle. Same
loop structure as `jsonl.wait_for_idle`. The `WaitResult` carries the
last agent message and a synthetic `last_stop_reason` of
`"task_complete"` so callers that branch on stop reason still work.

`alive_check` is honoured every poll tick (lets the caller short-
circuit when the tmux window dies).

#### `extract_response`

Iterate events with `timestamp > since`:
- For `event_msg` with `payload.type == "agent_message"`: append
  `payload.message`.
- For `event_msg` with `payload.type == "task_complete"`: ignore
  (its `last_agent_message` is a duplicate of agent_message events).
- For `response_item` with `payload.type == "reasoning"` and
  `include_thinking`:
  - If `payload.summary` is a non-empty list, render each entry as
    `[thinking] <text>`.
  - Else if `payload.encrypted_content` is set, render
    `[thinking encrypted]` once per reasoning event.
- For `response_item` with `payload.type == "function_call"` and
  `include_tool_use`: render
  `[tool_use <name> <truncated_args>]` (truncate args to 500 chars).
- For `response_item` with `payload.type == "function_call_output"`
  and `include_tool_use`: render `[tool_result <truncated_output>]`
  (truncate to 500 chars).

Concatenate, return `.strip()` of the result. Same shape as the
Claude `extract_response`.

#### `dump_events`

Same as Claude's: emit raw JSONL lines whose `timestamp > since`, one
per line, joined by newlines.

### 3. CLI changes — `scripts/oum_worker/cli.py`

#### New flags on `spawn` and `schedule`

- `--engine {claude,codex}` — required to be one of the two; default
  `claude`.
- `--yolo` / `--no-yolo` — controls bypass behaviour. Implemented as
  an argparse mutually-exclusive group (passing both is a usage
  error, exit 2). When neither is passed, the engine's default
  applies (claude=False, codex=True).
  - Claude: `--yolo` enables `--dangerously-skip-permissions`.
    `--no-yolo` is the same as omitting (default off). The existing
    `--dangerously-skip-permissions` continues to work and is
    additive — passing it is equivalent to `--yolo`.
  - Codex: default is yolo on. `--no-yolo` strips the flag.
    `--yolo` is a no-op (matches the default) but accepted for
    symmetry with claude.
- `--codex-bin PATH` — parallel to `--cc-command` / `--claude-bin`.
  Resolves codex binary; defaults to config `codex_bin` then to
  `"codex"`.
- `--model MODEL` — passes to codex `-m`. For claude it is currently
  unused; if explicitly passed with `--engine claude`, the CLI emits a
  one-line warning to stderr.

#### Existing flags — engine-specific behaviour

- `--cc-command` / `--claude-bin` only applies when `--engine claude`.
  Passing it with `--engine codex` triggers a warning.
- `--name` (claude `--name <session-name>`) — claude-only; warn if
  combined with `--engine codex`.
- `--permission-mode` — claude-only; warn if combined with codex.
- `--dangerously-skip-permissions` — claude-only; warn if combined
  with codex (codex has its own approval-policy mechanism via
  `-a/--ask-for-approval`, which we don't surface in this spec).

Warnings go to stderr in the form
`warning: --<flag> ignored for engine=codex`. They never fail the
command — they're purely advisory.

#### Binary-existence check

After `--engine` is resolved and the binary path is computed, the
spawn handler runs `shutil.which(binary)` (or, if the path contains
`/`, `Path(binary).exists()`). If absent, exit 5 with
`error: <engine> binary not found at '<path>' (install <engine> or pass --<engine>-bin)`.

This catches "you forgot to install codex" before tmux opens a
window that immediately dies.

#### State.json

`WorkerState` gains `engine: Optional[str]`. `state.read()` defaults
the field to `"claude"` when absent (backwards compat for any
state.json written before this change). `state.create()` accepts
`engine` and writes it.

#### `_resolve_session_id`, `_handle_capture`, `_handle_wait`,
`_handle_ask`

Each reads `state.engine` (defaulting to `"claude"`) and routes:

```python
engine_mod = engines.get(s.engine or "claude").jsonl_module
```

then calls the same surface (`discover_by_prompt`, `find_by_session_id`,
`wait_for_idle`, `extract_response`, `dump_events`) on whichever
module came back. The verb handlers do not branch on engine name —
they only branch on the module they dispatch through.

#### `_handle_attach`

No change. It reads `state.tmux_session` and `state.tmux_window` and
execs `tmux attach` — engine-agnostic. The "headless rejection" gate
remains because both engines support headless mode and the rejection
is "no tmux window for headless workers", not engine-specific.

#### `_handle_kill`

No change. It kills the tmux window and unbootstraps any plist —
engine-agnostic.

### 4. Config — `scripts/oum_worker/config.py`

New keys:

- `codex_bin: str` — default `"codex"`. Honored when `--codex-bin`
  is omitted.

No `default_engine` key. Per-spawn flag with hard default of
`"claude"` is intentional; we don't want a config that quietly
changes which engine gets used by every spawn.

### 5. Documentation

- `skills/oum-worker/SKILL.md` — engine matrix, codex caveats. The
  orchestrator copy at `oum-os/skills/oum-worker/SKILL.md` is updated
  in lockstep, preserving its OUM-specific content.
- `README.md` — `--engine` flag, codex example, capability matrix
  showing both engines support spawn / attach / send / capture / wait
  / ask / kill / list / status / logs / schedule.

### 6. Files touched

**Created:**
- `scripts/oum_worker/engines.py`
- `scripts/oum_worker/codex_jsonl.py`
- `tests/test_oum_worker_engines.py`
- `tests/test_oum_worker_codex_jsonl.py`
- `tests/fixtures/oum_worker/codex_simple.jsonl`
- `tests/fixtures/oum_worker/codex_with_tools.jsonl`

**Modified:**
- `scripts/oum_worker/cli.py` — flags, engine resolution, binary
  check, engine-dispatching capture/wait/ask, warnings.
- `scripts/oum_worker/launchd.py` — `_cc_invocation` becomes a
  delegation; `build_inner_command` accepts `engine: str`.
- `scripts/oum_worker/state.py` — `engine` field on `WorkerState`.
- `scripts/oum_worker/config.py` — `codex_bin` key.
- `tests/test_oum_worker_cli.py` — codex spawn / capture / wait /
  ask / attach / replace-cross-engine / no-yolo.
- `tests/test_oum_worker_state.py` — engine round-trip; default for
  missing field.
- `tests/test_oum_worker_launchd.py` — `_cc_invocation` claude path
  unchanged; `build_inner_command(engine="codex", ...)` produces a
  codex-shaped command.
- `skills/oum-worker/SKILL.md` (in-repo).
- `README.md`.

External-repo update (not in this commit chain, mirror manually):
- `oum-os/skills/oum-worker/SKILL.md` (orchestrator copy).

### 7. Tests

**`tests/test_oum_worker_engines.py`:**
- ClaudeEngine: build_invocation with prompt, with prompt=None
  (cold start), headless, resume, yolo on/off, model (warns), name,
  permission_mode.
- CodexEngine: same matrix but resume becomes `resume <sid>`
  subcommand position; yolo defaults on; headless requires prompt
  (raises ValueError); name/permission_mode/cwd handling.
- `engines.get("claude")` and `engines.get("codex")` return the
  right object.
- `engines.get("nope")` raises ValueError.

**`tests/test_oum_worker_codex_jsonl.py`:**
- `find_by_session_id` returns the right path; None when unknown.
- `discover_by_prompt` finds the session for a given cwd + prompt.
- `discover_by_prompt` returns None when cwd doesn't match.
- `discover_by_prompt` returns None when prompt doesn't match.
- `discover_by_prompt` mtime tiebreaker.
- `wait_for_idle` returns idle on a JSONL with a `task_complete`
  later than `last_send_at`.
- `wait_for_idle` returns timeout when no `task_complete` arrives.
- `wait_for_idle` honours `alive_check`.
- `extract_response` default: text-only.
- `extract_response` `--include-tool-use`: emits function_call /
  function_call_output markers.
- `extract_response` `--include-thinking`: emits reasoning summary
  / encrypted marker.
- `dump_events` emits raw lines after `since`.

**`tests/test_oum_worker_cli.py` additions:**
- `test_spawn_codex_writes_engine_in_state` — `--engine codex`
  records `engine: "codex"` in state.json.
- `test_spawn_codex_yolo_default_on` — invocation contains `--yolo`.
- `test_spawn_codex_no_yolo_strips_flag` — invocation does not
  contain `--yolo` when `--no-yolo` is passed.
- `test_spawn_codex_headless_requires_prompt` — exit 1 with clean
  message.
- `test_capture_codex_uses_codex_jsonl` — fixture-driven; capture
  on a codex worker reads codex_jsonl.
- `test_wait_codex_returns_zero_on_task_complete` — fixture-driven.
- `test_ask_codex_round_trip` — send + wait + capture against a
  codex worker (fixture jsonl, stub binary).
- `test_replace_cross_engine` — spawn claude, replace with codex;
  state.engine flips.
- `test_attach_works_for_codex` — attach validation gates pass for
  a codex worker.
- `test_codex_binary_missing_errors_early` — `--engine codex
  --codex-bin /nope` exits 5 before tmux opens.
- `test_warning_on_engine_mismatched_flag` — `--engine codex --name
  foo` succeeds with a stderr warning containing `ignored for
  engine=codex`.

**`tests/test_oum_worker_state.py` additions:**
- `test_engine_round_trips` — create with `engine="codex"`, read
  back same.
- `test_engine_defaults_to_claude_when_missing` — old state.json
  without engine field reads as `engine="claude"`.

**`tests/test_oum_worker_launchd.py` additions:**
- `test_cc_invocation_still_works_via_engines` — call
  `_cc_invocation` directly, get the same string the
  ClaudeEngine produces.
- `test_build_inner_command_codex_uses_codex_engine` — pass
  `engine="codex"`, get a codex-shaped command.

Approximate total: ~30 new tests.

### 8. Backwards compatibility

- `state.json` files written before this change have no `engine`
  field. `state.read()` defaults missing field to `"claude"`.
- `_cc_invocation` keeps its current signature and external behaviour;
  only its body changes (delegates to `engines.get("claude")`).
- All existing CLI flags retain their meaning. `--engine` defaults
  to `claude`, so any existing call site works.
- New flags (`--engine`, `--yolo`, `--no-yolo`, `--codex-bin`,
  `--model`) are additive.
- The new `engine` field on `WorkerState` defaults to `None` from
  Python's perspective but reads as `"claude"` through `state.read()`.

## Risks and edge cases

- **Codex session log scan cost.** Globbing `~/.codex/sessions/**`
  on machines with thousands of sessions could be slow. Bounding
  the discover scan to the day of `created_at` plus adjacent days
  (and a 7-day fallback) keeps it cheap in practice.
- **Encrypted reasoning blocks.** Most codex `reasoning` events
  carry only `encrypted_content`, not a usable `summary`. The
  `[thinking encrypted]` marker is a documented limitation rather
  than a bug.
- **Codex `task_complete` includes `last_agent_message`**, which
  is a duplicate of the agent_message events. We ignore it in
  `extract_response` to avoid double-emitting; we keep it for
  `wait_for_idle` because it confirms the turn ended cleanly.
- **`codex resume <sid>` semantics.** Codex's resume is a
  subcommand, not a flag. Our `--resume <sid>` translates to
  `codex resume <sid>` (interactive) or `codex exec resume <sid>`
  (headless). Codex's `--last` shorthand is not exposed; users
  who want it can pass `--resume <sid>` after grabbing the id from
  `oum-worker status`.
- **Engine-mismatched flag warnings.** The list of "claude-only"
  flags is hand-maintained. If we add a new claude-only flag and
  forget to add it to the warning list, the user sees a silent
  no-op when combined with codex. Mitigation: tests for each
  warning case lock the behaviour.
- **`--replace` cross-engine.** State.json's `engine` field flips,
  but the `prompt.md` from the old engine is purged along with the
  rest of the worker dir. New engine starts fresh.
- **Codex YOLO is dangerous by definition.** The default-on
  behaviour was the user's explicit ask. Documentation flags this
  prominently in SKILL.md and README.md.

## Open questions

None blocking.
