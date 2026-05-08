# oum-worker generic session CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `oum-worker` usable as a one-tool entry point for both human-driven Claude Code sessions and agent automation by adding an `attach` verb and making `--prompt` optional for interactive spawn/schedule.

**Architecture:** Approach A from the spec — minimal additions, no breaking changes. `_cc_invocation` and `build_inner_command` accept `Optional[Path]` for `prompt_file` and skip the `"$(cat ...)"` arg when None. New `attach` verb wraps `tmux select-window` + `tmux attach` with validation gates (label exists, not headless, window alive, stdin is tty). Help text and SKILL.md polish.

**Tech Stack:** Python 3.11+, argparse, pytest, tmux, launchd. Standard-library only at runtime.

---

## Task 1: `_cc_invocation` accepts `Optional[Path]` for `prompt_file`

**Files:**
- Modify: `scripts/oum_worker/launchd.py:_cc_invocation`
- Test: `tests/test_oum_worker_launchd.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_oum_worker_launchd.py`:

```python
def test_cc_invocation_omits_prompt_when_none():
    """With prompt_file=None, the resulting command has no `$(cat ...)` arg.

    This is the cold-start interactive path: `claude` runs with no initial
    message, exactly like a user typing `claude` by hand.
    """
    cmd = launchd._cc_invocation(
        claude_bin="claude",
        resume=None,
        new_session=True,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        prompt_file=None,
        headless=False,
    )
    assert "$(cat" not in cmd
    assert cmd.strip() == "claude"


def test_cc_invocation_with_prompt_file_unchanged(tmp_path):
    """Existing behavior: prompt_file=<path> still produces `$(cat <path>)`."""
    p = tmp_path / "prompt.md"
    cmd = launchd._cc_invocation(
        claude_bin="claude",
        resume=None,
        new_session=True,
        session_name=None,
        permission_mode=None,
        skip_permissions=False,
        prompt_file=p,
        headless=False,
    )
    assert "$(cat" in cmd
    assert str(p) in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_launchd.py::test_cc_invocation_omits_prompt_when_none -v`
Expected: FAIL — TypeError or assertion error (current code calls `shlex.quote(str(prompt_file))` which would raise TypeError on None).

- [ ] **Step 3: Update `_cc_invocation` signature and body**

Edit `scripts/oum_worker/launchd.py`. Change the function signature and body:

```python
def _cc_invocation(*, claude_bin: str, resume: Optional[str], new_session: bool,
                   session_name: Optional[str], permission_mode: Optional[str],
                   skip_permissions: bool, prompt_file: Optional[Path],
                   headless: bool) -> str:
    # Headless mode honors --cc-command but appends -p as the headless flag
    # (Claude Code's headless invocation is always `<bin> -p ...`).
    parts: list[str] = [claude_bin] + (["-p"] if headless else [])
    if resume:
        parts.extend(["--resume", shlex.quote(resume)])
    if new_session and session_name and not headless:
        parts.extend(["--name", shlex.quote(session_name)])
    if permission_mode:
        parts.extend(["--permission-mode", shlex.quote(permission_mode)])
    if skip_permissions:
        parts.append("--dangerously-skip-permissions")
    if prompt_file is not None:
        parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    return " ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_launchd.py -v`
Expected: PASS for the two new tests; existing launchd tests still pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/launchd.py tests/test_oum_worker_launchd.py
git commit -m "$(cat <<'EOF'
launchd: _cc_invocation accepts Optional[Path] for prompt_file

When prompt_file is None, skip the trailing '\$(cat ...)' arg so
the resulting command is plain 'claude' — matches a manual `claude`
invocation, used by the cold-start interactive path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `build_inner_command` plumbs `Optional[Path]` through to `_cc_invocation`

**Files:**
- Modify: `scripts/oum_worker/launchd.py:build_inner_command`
- Test: `tests/test_oum_worker_launchd.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_oum_worker_launchd.py`:

```python
def test_build_inner_command_interactive_no_prompt(tmp_path):
    """With prompt_file=None, the inner command runs `claude` with no
    `$(cat ...)` substitution — cold-start interactive."""
    cmd = launchd.build_inner_command(
        cwd=tmp_path,
        claude_bin="cc",
        prompt_file=None,
        log_path=tmp_path / "tmux.log",
        label="cold",
        logs_dir=tmp_path / "logs",
        resume=None, new_session=True, session_name=None,
        permission_mode=None, skip_permissions=False,
        tmux_session="oum-worker-test", headless=False,
    )
    assert "$(cat" not in cmd
    # Sanity: the inner command still has cd, mark-started, cc invocation.
    assert "cd " in cmd
    assert "mark-started" in cmd
    assert "cc" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_launchd.py::test_build_inner_command_interactive_no_prompt -v`
Expected: FAIL — current signature is `prompt_file: Path`, passing None will TypeError when the function tries to use it.

- [ ] **Step 3: Update `build_inner_command` signature**

Edit `scripts/oum_worker/launchd.py`. Change the `prompt_file` annotation in `build_inner_command` from `Path` to `Optional[Path]`. The body already passes `prompt_file` straight to `_cc_invocation`, which now handles None correctly. No other body changes needed.

```python
def build_inner_command(*, cwd: Path, claude_bin: str,
                        prompt_file: Optional[Path],
                        log_path: Path, label: str, logs_dir: Path,
                        resume: Optional[str], new_session: bool,
                        session_name: Optional[str], permission_mode: Optional[str],
                        skip_permissions: bool, tmux_session: str,
                        headless: bool,
                        env_pairs: dict[str, str] | None = None,
                        scripts_dir: Path | None = None) -> str:
```

(Body unchanged — `_cc_invocation` already gates on None.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_launchd.py -v`
Expected: PASS — all launchd tests including the new one.

- [ ] **Step 5: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/launchd.py tests/test_oum_worker_launchd.py
git commit -m "$(cat <<'EOF'
launchd: build_inner_command accepts Optional[Path] for prompt_file

Plumbs None through to _cc_invocation for cold-start interactive
sessions in scheduled (launchd) mode.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Make `--prompt` optional in argparse for spawn/schedule

**Files:**
- Modify: `scripts/oum_worker/cli.py:_add_spawn_args`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_oum_worker_cli.py` (after the existing `_run_cli` helper / TMUX_BIN / TEST_TMUX_SESSION setup):

```python
@pytest.mark.skipif(TMUX_BIN is None, reason="tmux required")
def test_spawn_interactive_without_prompt_succeeds(tmp_path):
    """Cold-start: --interactive with no --prompt opens claude in tmux.

    Uses a stub `claude` so the test doesn't actually start Claude Code.
    """
    stub = tmp_path / "stub-cc"
    stub.write_text('#!/bin/zsh\nsleep 30\n')
    stub.chmod(0o755)
    try:
        r = _run_cli(
            "spawn",
            "--label", "cold-start",
            "--new",
            # no --prompt, no --prompt-file
            "--cc-command", str(stub),
            "--tmux-session", TEST_TMUX_SESSION,
            "--cwd", str(tmp_path),
            "--logs-dir", str(tmp_path / "logs"),
        )
        assert r.returncode == 0, r.stderr
        sj = tmp_path / "logs" / "cold-start" / "state.json"
        assert sj.exists()
        prompt_md = tmp_path / "logs" / "cold-start" / "prompt.md"
        assert prompt_md.exists()
        assert prompt_md.read_text() == ""
    finally:
        _cleanup_tmux()


def test_spawn_headless_without_prompt_fails(tmp_path):
    """Headless still requires a prompt — there's nothing to send to claude -p."""
    r = _run_cli(
        "spawn",
        "--label", "headless-no-prompt",
        "--new",
        "--headless",
        "--cwd", str(tmp_path),
        "--logs-dir", str(tmp_path / "logs"),
    )
    assert r.returncode == 1
    assert "--headless requires --prompt" in r.stderr or "prompt" in r.stderr.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py::test_spawn_interactive_without_prompt_succeeds tests/test_oum_worker_cli.py::test_spawn_headless_without_prompt_fails -v`
Expected: FAIL — argparse currently rejects missing prompt with exit 2 ("one of the arguments --prompt --prompt-file is required").

- [ ] **Step 3: Make the argparse group optional**

Edit `scripts/oum_worker/cli.py`. In `_add_spawn_args`, change:

```python
    p = sp.add_mutually_exclusive_group(required=True)
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
```

to:

```python
    p = sp.add_mutually_exclusive_group(required=False)
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
```

- [ ] **Step 4: Update `_handle_spawn` to gate empty-prompt error on headless**

Edit `scripts/oum_worker/cli.py:_handle_spawn`. Change:

```python
    prompt = _read_prompt(args)
    if not prompt:
        print("error: prompt is empty", file=sys.stderr)
        return 1
```

to:

```python
    prompt = _read_prompt(args)
    if args.headless and not prompt:
        print("error: --headless requires --prompt or --prompt-file",
              file=sys.stderr)
        return 1
```

- [ ] **Step 5: Update `_handle_schedule` the same way**

Edit `scripts/oum_worker/cli.py:_handle_schedule`. Change:

```python
    prompt = _read_prompt(args)
    if not prompt:
        print("error: prompt is empty", file=sys.stderr)
        return 1
```

to:

```python
    prompt = _read_prompt(args)
    if args.headless and not prompt:
        print("error: --headless requires --prompt or --prompt-file",
              file=sys.stderr)
        return 1
```

- [ ] **Step 6: Update `_spawn_interactive` to pass `prompt_file=None` when prompt is empty**

Edit `scripts/oum_worker/cli.py:_spawn_interactive`. Change:

```python
    cc = launchd._cc_invocation(
        claude_bin=s.claude_bin, resume=args.resume, new_session=args.new_session,
        session_name=args.name, permission_mode=args.permission_mode,
        skip_permissions=args.skip_permissions, prompt_file=Path(s.prompt_file),
        headless=False,
    )
```

to:

```python
    prompt_text = Path(s.prompt_file).read_text(encoding="utf-8")
    prompt_arg = Path(s.prompt_file) if prompt_text else None
    cc = launchd._cc_invocation(
        claude_bin=s.claude_bin, resume=args.resume, new_session=args.new_session,
        session_name=args.name, permission_mode=args.permission_mode,
        skip_permissions=args.skip_permissions, prompt_file=prompt_arg,
        headless=False,
    )
```

- [ ] **Step 7: Update `_handle_schedule` interactive branch to pass `prompt_file=None` when empty**

Edit `scripts/oum_worker/cli.py:_handle_schedule`. Find the call to `launchd.build_inner_command` and change:

```python
    inner_cmd = launchd.build_inner_command(
        cwd=cwd, claude_bin=claude_bin, prompt_file=Path(s.prompt_file),
        ...
```

to:

```python
    prompt_arg = Path(s.prompt_file) if prompt else None
    inner_cmd = launchd.build_inner_command(
        cwd=cwd, claude_bin=claude_bin, prompt_file=prompt_arg,
        ...
```

(Headless path here always has a non-empty prompt because of the gate above; interactive uses the caller's prompt or None.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -v`
Expected: PASS for new tests AND all existing CLI tests.

- [ ] **Step 9: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py tests/test_oum_worker_cli.py
git commit -m "$(cat <<'EOF'
cli: --prompt becomes optional for interactive spawn/schedule

Cold-start use case: a human can run 'oum-worker spawn --label foo
--new' to drop into a fresh Claude Code session in tmux with no
initial message. Headless still requires --prompt because there's
nothing useful to do with 'claude -p' otherwise.

When the prompt is empty, the interactive path passes
prompt_file=None to _cc_invocation and build_inner_command so the
resulting tmux pane runs plain 'claude' rather than 'claude ""'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `attach` verb

**Files:**
- Modify: `scripts/oum_worker/cli.py` — new `_handle_attach`, `_do_attach`, parser wiring
- Test: `tests/test_oum_worker_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oum_worker_cli.py`:

```python
def test_attach_unknown_label_exit_1(tmp_path):
    (tmp_path / "logs").mkdir()
    r = _run_cli("attach", "--label", "ghost",
                 "--logs-dir", str(tmp_path / "logs"))
    assert r.returncode == 1
    assert "no worker named" in r.stderr or "ghost" in r.stderr


def test_attach_headless_rejected(tmp_path):
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    _state.create(workdir, label="hl", mode="headless", cwd=tmp_path,
                  claude_bin="cc", tmux_session="x")
    r = _run_cli("attach", "--label", "hl", "--logs-dir", str(workdir))
    assert r.returncode == 2
    assert "headless" in r.stderr.lower()


def test_attach_dead_window_rejected(tmp_path):
    """Worker exists in state.json but its tmux window doesn't — should refuse."""
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    _state.create(workdir, label="dead", mode="interactive", cwd=tmp_path,
                  claude_bin="cc", tmux_session="oum-worker-no-such-session")
    r = _run_cli("attach", "--label", "dead", "--logs-dir", str(workdir))
    assert r.returncode == 2
    assert "not alive" in r.stderr.lower() or "respawn" in r.stderr.lower()


def test_attach_no_tty_rejected(tmp_path):
    """When stdin is not a tty (e.g. an agent calling attach), refuse early
    so tmux doesn't hang on a non-interactive stdin."""
    workdir = tmp_path / "logs"
    workdir.mkdir()
    from oum_worker import state as _state
    _state.create(workdir, label="dead", mode="interactive", cwd=tmp_path,
                  claude_bin="cc", tmux_session="oum-worker-no-such-session")
    # _run_cli's subprocess pipes stdin -> not a tty.
    r = _run_cli("attach", "--label", "dead", "--logs-dir", str(workdir))
    assert r.returncode == 2
    # Error must surface that we declined; either "tty" or "not alive"
    # depending on which gate fires first. Both are acceptable signals.
    assert "tty" in r.stderr.lower() or "not alive" in r.stderr.lower()


def test_attach_calls_tmux_when_all_gates_pass(tmp_path, monkeypatch):
    """Unit-test _handle_attach in-process: monkeypatch isatty, window_exists,
    and _do_attach so we never actually exec tmux."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from oum_worker import cli as _cli
    from oum_worker import tmux as _tmux
    from oum_worker import state as _state
    workdir = tmp_path / "logs"
    workdir.mkdir()
    _state.create(workdir, label="ok", mode="interactive", cwd=tmp_path,
                  claude_bin="cc", tmux_session="some-sess")
    monkeypatch.setattr(_tmux, "window_exists", lambda s, w: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    captured = {}
    def fake_do_attach(session, window):
        captured["session"] = session
        captured["window"] = window
        return 0
    monkeypatch.setattr(_cli, "_do_attach", fake_do_attach)
    import argparse
    args = argparse.Namespace(label="ok", logs_dir=str(workdir), config=None)
    rc = _cli._handle_attach(args)
    assert rc == 0
    assert captured == {"session": "some-sess", "window": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k attach -v`
Expected: FAIL — `attach` is an unknown verb (argparse exit 2 for all CLI-level tests; AttributeError for the in-process test).

- [ ] **Step 3: Add `_do_attach` and `_handle_attach` to `cli.py`**

Edit `scripts/oum_worker/cli.py`. Add these handlers above the `# ---------- parser ----------` line, after `_handle_logs`:

```python
def _do_attach(session: str, window: str) -> int:
    """Focus the named tmux window, then exec into `tmux attach`.

    Split out so tests can monkeypatch this without execvp'ing tmux.
    Returns 0 only in the (untested) case where execvp fails to find tmux;
    on success this never returns because the process is replaced.
    """
    tmux_bin = str(_tmux.find_tmux_bin())
    subprocess.run([tmux_bin, "select-window", "-t", f"{session}:{window}"],
                   check=False)
    os.execvp(tmux_bin, [tmux_bin, "attach", "-t", session])
    return 0


def _handle_attach(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    if s.mode == "headless":
        print(f"cannot attach to headless worker {args.label!r} "
              f"(headless workers have no tmux window)", file=sys.stderr)
        return 2
    if not _tmux.window_exists(s.tmux_session, s.tmux_window):
        print(f"worker {args.label!r} window is not alive "
              f"(try 'oum-worker spawn ... --replace' to respawn)",
              file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("attach requires a tty (you probably meant "
              "'oum-worker send' or 'oum-worker ask')", file=sys.stderr)
        return 2
    return _do_attach(s.tmux_session, s.tmux_window)
```

- [ ] **Step 4: Wire `attach` into `_build_parser`**

Edit `scripts/oum_worker/cli.py:_build_parser`. After the `sp_logs` block and before the final `return p`, add:

```python
    sp_attach = sub.add_parser("attach",
                               help="Attach your terminal to a running interactive session.")
    _add_global(sp_attach)
    sp_attach.add_argument("--label", required=True)
    sp_attach.set_defaults(_handler=_handle_attach)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py -k attach -v`
Expected: PASS for all five attach tests.

Then run the full suite to make sure nothing else broke:

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ -v`
Expected: PASS for everything.

- [ ] **Step 6: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py tests/test_oum_worker_cli.py
git commit -m "$(cat <<'EOF'
cli: add 'attach' verb to enter a running interactive session

oum-worker attach --label <label> looks up the worker, validates
that it's interactive, alive in tmux, and that stdin is a tty,
then execs tmux attach with the right window focused.

Refusing on non-tty stdin guards against an agent calling attach
by mistake (tmux would otherwise inherit a non-tty stdin and hang
or error deep in tmux).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Polish help text

**Files:**
- Modify: `scripts/oum_worker/cli.py:_build_parser`

- [ ] **Step 1: Update parser description and verb help**

Edit `scripts/oum_worker/cli.py:_build_parser`. Make these changes:

```python
    p = argparse.ArgumentParser(prog="oum-worker",
                                description="Manage Claude Code sessions (interactive in tmux or headless).")
```

```python
    sp_spawn = sub.add_parser("spawn", help="Start a Claude Code session now.")
```

```python
    sp_sched = sub.add_parser("schedule", help="Schedule a Claude Code session for later via launchd.")
```

(Other verbs read fine as-is.)

- [ ] **Step 2: Confirm help still emits all verbs**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py::test_help_lists_all_verbs -v`
Expected: PASS (test asserts the verb list, not the wording).

Add a quick assertion that `attach` shows up too. Edit `tests/test_oum_worker_cli.py:test_help_lists_all_verbs`:

```python
def test_help_lists_all_verbs():
    r = _run_cli("--help")
    assert r.returncode == 0
    for verb in ["spawn", "schedule", "send", "capture", "wait", "ask",
                 "list", "status", "kill", "logs", "attach"]:
        assert verb in r.stdout, f"verb {verb!r} missing from --help: {r.stdout}"
```

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/test_oum_worker_cli.py::test_help_lists_all_verbs -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add scripts/oum_worker/cli.py tests/test_oum_worker_cli.py
git commit -m "$(cat <<'EOF'
cli: rephrase help text from 'worker' to 'session' (cosmetic)

The CLI now manages interactive sessions as well as background
workers; description and per-verb help reflect that. No flags or
verbs change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update SKILL.md

**Files:**
- Modify: `skills/oum-worker/SKILL.md`

- [ ] **Step 1: Update the verb table and add the cold-start caveat**

Edit `skills/oum-worker/SKILL.md`. Specifically:

1. Insert a new row after the `kill` row in the verb table (around line 23 in the current file):

```markdown
- `attach`    drop your terminal into a running interactive session
```

2. In the lead paragraph, replace "Claude Code worker sessions" with "Claude Code sessions". The first paragraph becomes:

```markdown
Use this skill to manage Claude Code sessions. Sessions are Claude Code processes running in a configured tmux session, identified by a caller-supplied label. Some sessions are scheduled (launchd, future), some are spawned now, some are headless one-shots (`claude -p`).
```

3. Under "Common shapes", append a new section:

```markdown
Spawn a cold interactive session (no initial prompt) and attach:

```bash
oum-worker --config .oum-worker.json spawn  --label adhoc --new
oum-worker --config .oum-worker.json attach --label adhoc
```
```

4. Append a paragraph to "Hard rules" (after the existing list):

```markdown
- If you spawn an interactive session without `--prompt`, `capture` / `wait` / `ask` cannot resolve the session id until the session has produced its first user message. Either pass `--prompt` up front, or send a real first message via `oum-worker send` before calling `capture` / `ask`.
```

- [ ] **Step 2: Sanity-check by re-reading**

Run: `cat /Users/tushar/Documents/OnceUponMe/os/oum-worker/skills/oum-worker/SKILL.md` and verify each change above is present.

- [ ] **Step 3: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add skills/oum-worker/SKILL.md
git commit -m "$(cat <<'EOF'
skills: document attach verb and cold-start interactive caveat

Add attach to the verb table, replace "worker sessions" with
"sessions" in the lead paragraph, document that capture/ask cannot
resolve a session id for a cold-started interactive worker until it
has produced its first user message.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add attach to the commands block**

Edit `README.md`. Find the `## Commands` block. Add a row for `attach` after `ask`:

```bash
oum-worker attach   --label <label>
```

- [ ] **Step 2: Add a one-liner about cold-start**

In the `## Quick Start` block, append after the existing examples (before `## Configuration`):

```markdown
Drop into a fresh interactive Claude Code session in tmux:

```bash
oum-worker --config .oum-worker.json spawn  --label adhoc --new
oum-worker --config .oum-worker.json attach --label adhoc
```
```

- [ ] **Step 3: Commit**

```bash
cd /Users/tushar/Documents/OnceUponMe/os/oum-worker
git add README.md
git commit -m "$(cat <<'EOF'
docs: document attach verb and cold-start interactive flow in README

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m pytest tests/ -v`
Expected: PASS for everything.

- [ ] **Step 2: Manual smoke check the CLI surface**

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m oum_worker.cli --help`
Expected: lists `attach` as a verb; description mentions "sessions".

Run: `cd /Users/tushar/Documents/OnceUponMe/os/oum-worker && PYTHONPATH=scripts python3 -m oum_worker.cli attach --help`
Expected: shows the attach help, including `--label`.

- [ ] **Step 3: Commit any final fix-up**

If steps 1-2 surfaced anything, fix and commit. Otherwise this task is a no-op.

---

## Self-Review

**Spec coverage:**
- Goal 1 (`attach` verb) → Task 4 ✓
- Goal 2 (`--prompt` optional for interactive) → Tasks 1, 2, 3 ✓
- Goal 3 (polish) → Tasks 5, 6, 7 ✓
- All test cases from spec §4 → Tasks 1-4 (the `_cc_invocation` no-prompt unit test in Task 1, schedule no-prompt indirectly covered by Task 2's `build_inner_command` test, attach gates in Task 4, spawn no-prompt in Task 3) ✓

**Placeholder scan:** none.

**Type consistency:** `_cc_invocation` and `build_inner_command` both move `prompt_file` from `Path` to `Optional[Path]`. `_do_attach(session, window)` signature is consistent across Task 4. `_handle_attach(args)` returns int.

**Files-touched alignment:** Tasks together touch exactly: `scripts/oum_worker/cli.py`, `scripts/oum_worker/launchd.py`, `skills/oum-worker/SKILL.md`, `README.md`, `tests/test_oum_worker_cli.py`, `tests/test_oum_worker_launchd.py`. Matches the spec's "Files touched" section.
