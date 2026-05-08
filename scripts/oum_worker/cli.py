"""oum-worker CLI: argparse + dispatcher."""
from __future__ import annotations

import argparse
import json as _json
import os
import shlex
import shutil as _shutil
import subprocess
import sys
import time as _time
from dataclasses import asdict as _asdict
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo as _ZI

from oum_worker import config as worker_config
from oum_worker import jsonl, launchd, state
from oum_worker import tmux as _tmux


DEFAULT_TMUX_SESSION = worker_config.DEFAULT_TMUX_SESSION


def _utc_iso_to_display(s: str | None, cfg: worker_config.WorkerConfig) -> str:
    """Convert UTC ISO 8601 (Z suffix) to the configured timezone for display."""
    if not s:
        return "-"
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        zone = _ZI(cfg.timezone)
        d = _dt.fromisoformat(s2).astimezone(zone)
        return d.strftime("%Y-%m-%d %H:%M %Z")
    except (ValueError, AttributeError):
        return s


# ---------- helpers ----------

def config_from_args(args: argparse.Namespace) -> worker_config.WorkerConfig:
    return worker_config.load_config(getattr(args, "config", None))


def workdir_from_args(args: argparse.Namespace) -> Path:
    cfg = config_from_args(args)
    p = Path(args.logs_dir).expanduser().resolve() if getattr(args, "logs_dir", None) else cfg.logs_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def _add_global(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="Path to JSON config file (or OUM_WORKER_CONFIG).",
    )
    parser.add_argument(
        "--logs-dir",
        default=argparse.SUPPRESS,
        help="State + log directory root (overrides config and OUM_WORKER_LOGS_DIR).",
    )


def _read_prompt(args: argparse.Namespace) -> str:
    if getattr(args, "prompt_file", None):
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    return (getattr(args, "prompt", None) or "").strip()


def _resolve_cwd(args: argparse.Namespace, cfg: worker_config.WorkerConfig) -> Path:
    return launchd.resolve_workdir(repo=getattr(args, "repo", None),
                                   cwd=getattr(args, "cwd", None),
                                   cfg=cfg)


def _resolve_claude_bin(args: argparse.Namespace,
                        cfg: worker_config.WorkerConfig) -> str:
    return getattr(args, "claude_bin", None) or cfg.claude_bin


def _resolve_tmux_session(args: argparse.Namespace,
                          cfg: worker_config.WorkerConfig) -> str:
    return getattr(args, "tmux_session", None) or cfg.tmux_session


def _resolve_session_id(workdir: Path, s: state.WorkerState) -> state.WorkerState:
    if s.session_id:
        return s
    prompt = Path(s.prompt_file).read_text(encoding="utf-8") if Path(s.prompt_file).exists() else ""
    sid = jsonl.discover_by_prompt(Path(s.cwd), prompt, created_at=s.created_at)
    if not sid:
        return s
    jsonl_path = jsonl.find_by_session_id(Path(s.cwd), sid)
    state.update(workdir, s.label,
                 session_id=sid,
                 jsonl_path=str(jsonl_path) if jsonl_path else None)
    return state.read(workdir, s.label)


def _compute_state(s: state.WorkerState) -> str:
    if s.mode == "headless" and s.ended_at:
        return "dead"
    if s.mode == "scheduled" and not s.started_at:
        return "scheduled"
    alive = _tmux.window_exists(s.tmux_session, s.tmux_window) if s.mode != "headless" else False
    if alive:
        return "running"
    if s.started_at and not s.ended_at:
        return "dead"
    return "scheduled" if s.launchd_label else "dead"


def _do_kill(workdir: Path, label: str, *, purge: bool) -> None:
    s = state.read(workdir, label)
    # kill_window is best-effort: it does not raise on a missing window
    # (no check=True under the hood). The TmuxError type is reserved for
    # collision errors in open_window; we don't expect it here.
    _tmux.kill_window(s.tmux_session, s.tmux_window)
    if s.launchd_label:
        launchd.unbootstrap(s.launchd_label)
    state.update(workdir, label, ended_at=state.utc_now_iso())
    if purge:
        _shutil.rmtree(state.worker_dir(workdir, label), ignore_errors=True)


# ---------- arg group helpers ----------

def _add_spawn_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--label", required=True)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--new", dest="new_session", action="store_true")
    g.add_argument("--resume", help="Claude session id to resume")
    # Optional: required for --headless (gated in handlers), free for
    # interactive — a cold-start session opens `claude` with no prompt.
    p = sp.add_mutually_exclusive_group(required=False)
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("--interactive", dest="headless", action="store_false", default=False)
    mode.add_argument("--headless", dest="headless", action="store_true")
    sp.add_argument("--repo")
    sp.add_argument("--cwd")
    sp.add_argument("--name", help="Claude session name (interactive only)")
    sp.add_argument("--cc-command", "--claude-bin", dest="claude_bin", default=None)
    sp.add_argument("--permission-mode",
                    choices=["acceptEdits", "auto", "bypassPermissions",
                             "default", "dontAsk", "plan"])
    sp.add_argument("--dangerously-skip-permissions", dest="skip_permissions",
                    action="store_true")
    sp.add_argument("--tmux-session", default=None)
    sp.add_argument("--replace", action="store_true")
    sp.add_argument(
        "--env",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Inject KEY=VALUE env var into the worker. Repeatable. "
            "Reserved keys (TZ, PATH, LANG) are rejected."
        ),
    )


# ---------- handlers ----------

def _handle_spawn(args: argparse.Namespace) -> int:
    cfg = config_from_args(args)
    workdir = workdir_from_args(args)
    cwd = _resolve_cwd(args, cfg)
    claude_bin = _resolve_claude_bin(args, cfg)
    tmux_session = _resolve_tmux_session(args, cfg)
    prompt = _read_prompt(args)
    if args.headless and not prompt:
        print("error: --headless requires --prompt or --prompt-file",
              file=sys.stderr)
        return 1

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
            claude_bin=claude_bin,
            tmux_session=tmux_session,
        )
    except state.LabelExists:
        print(f"error: label {args.label!r} already exists (pass --replace to overwrite)",
              file=sys.stderr)
        return 5

    Path(s.prompt_file).write_text(prompt, encoding="utf-8")

    if args.headless:
        return _spawn_headless(args, s, prompt, env_pairs=env_pairs)
    return _spawn_interactive(args, s, env_pairs=env_pairs)


def _spawn_interactive(args: argparse.Namespace, s: state.WorkerState,
                       *, env_pairs: dict[str, str] | None = None) -> int:
    # Cold-start: empty prompt.md means the user just wants `claude` to
    # open in tmux with no initial message. Pass prompt_file=None so the
    # invocation skips the trailing `"$(cat ...)"` arg.
    prompt_arg: Optional[Path] = (
        Path(s.prompt_file)
        if Path(s.prompt_file).read_text(encoding="utf-8")
        else None
    )
    cc = launchd._cc_invocation(
        claude_bin=s.claude_bin, resume=args.resume, new_session=args.new_session,
        session_name=args.name, permission_mode=args.permission_mode,
        skip_permissions=args.skip_permissions, prompt_file=prompt_arg,
        headless=False,
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
    print(f"Spawned interactive worker {s.label}")
    print(f"  Window: {s.tmux_session}:{s.tmux_window}  →  tmux a -t {s.tmux_session}")
    print(f"  Log:    {s.tmux_log}")
    return 0


def _spawn_headless(args: argparse.Namespace, s: state.WorkerState, prompt: str,
                    *, env_pairs: dict[str, str] | None = None) -> int:
    cmd = [s.claude_bin, "-p"]
    if args.resume:
        cmd.extend(["--resume", args.resume])
    if args.permission_mode:
        cmd.extend(["--permission-mode", args.permission_mode])
    if args.skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.append(prompt)

    subprocess_env: dict[str, str] | None = None
    if env_pairs:
        subprocess_env = {**os.environ, **env_pairs}

    response_path = Path(s.tmux_log).parent / "response.txt"
    state.update(workdir_from_args(args), s.label, started_at=state.utc_now_iso())
    with open(response_path, "w") as out:
        r = subprocess.run(cmd, cwd=s.cwd, stdout=out, stderr=subprocess.STDOUT,
                           env=subprocess_env)
    state.update(workdir_from_args(args), s.label, ended_at=state.utc_now_iso())
    if r.returncode != 0:
        print(f"headless worker {s.label} exited {r.returncode}", file=sys.stderr)
        return 4
    print(response_path.read_text())
    return 0


def _handle_schedule(args: argparse.Namespace) -> int:
    cfg = config_from_args(args)
    workdir = workdir_from_args(args)
    cwd = _resolve_cwd(args, cfg)
    claude_bin = _resolve_claude_bin(args, cfg)
    tmux_session = _resolve_tmux_session(args, cfg)
    prompt = _read_prompt(args)
    if args.headless and not prompt:
        print("error: --headless requires --prompt or --prompt-file",
              file=sys.stderr)
        return 1

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
            workdir, label=args.label, mode="scheduled", cwd=cwd,
            claude_bin=claude_bin, tmux_session=tmux_session,
        )
    except state.LabelExists:
        print(f"error: label {args.label!r} already exists", file=sys.stderr)
        return 5

    Path(s.prompt_file).write_text(prompt, encoding="utf-8")
    target = launchd.parse_target(args.delay, args.at, cfg=cfg)
    launchd_label = launchd.normalize_label(args.label, cfg=cfg)
    plist_path = Path(args.launch_agents_dir).expanduser() / f"{launchd_label}.plist"

    # Interactive cold-start (empty prompt) → prompt_file=None so the tmux
    # pane runs plain `claude`. Headless always has a non-empty prompt
    # because the gate above rejected empty headless invocations.
    prompt_arg: Optional[Path] = Path(s.prompt_file) if prompt else None
    inner_cmd = launchd.build_inner_command(
        cwd=cwd, claude_bin=claude_bin, prompt_file=prompt_arg,
        log_path=Path(s.tmux_log), label=args.label, logs_dir=workdir,
        resume=args.resume, new_session=args.new_session, session_name=args.name,
        permission_mode=args.permission_mode, skip_permissions=args.skip_permissions,
        tmux_session=tmux_session, headless=args.headless,
        env_pairs=env_pairs,
        scripts_dir=cfg.scripts_dir,
    )
    payload = launchd.build_plist(
        label=launchd_label, cwd=cwd, command=inner_cmd, target=target,
        stdout_path=workdir / args.label / "launchd.out",
        stderr_path=workdir / args.label / "launchd.err",
        env_pairs=env_pairs,
        cfg=cfg,
    )

    if args.dry_run:
        print(f"Would write {plist_path}")
        print(payload.decode("utf-8"))
        return 0

    launchd.write_plist(plist_path, payload, replace=args.replace)
    state.update(workdir, args.label, launchd_label=launchd_label,
                 plist_path=str(plist_path))
    if not args.no_bootstrap:
        launchd.bootstrap(launchd_label, plist_path, replace=args.replace)

    print(f"Scheduled {launchd_label}")
    print(f"  Plist:   {plist_path}")
    print(f"  Runs at: {target.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"  Prompt:  {s.prompt_file}")
    print(f"  Window:  tmux a -t {tmux_session}  →  '{args.label}'")
    return 0


def _handle_send(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    if not _tmux.window_exists(s.tmux_session, s.tmux_window):
        print(f"worker {args.label} window not alive", file=sys.stderr)
        return 2
    if getattr(args, "file", None):
        _tmux.send_file(s.tmux_session, s.tmux_window, Path(args.file))
    else:
        _tmux.send_text(s.tmux_session, s.tmux_window, args.text)
    state.update(workdir, args.label, last_send_at=state.utc_now_iso())
    return 0


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
    since = args.since or s.last_send_at or s.created_at
    if args.full:
        out = jsonl.dump_events(Path(s.jsonl_path), since=since)
    else:
        out = jsonl.extract_response(
            Path(s.jsonl_path), since=since,
            include_thinking=args.include_thinking,
            include_tool_use=args.include_tool_use,
        )
    print(out)
    state.update(workdir, args.label, last_capture_at=state.utc_now_iso())
    return 0


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

    def _alive() -> bool:
        return _tmux.window_exists(s.tmux_session, s.tmux_window) or s.mode == "headless"

    last_send = s.last_send_at or s.created_at
    result = jsonl.wait_for_idle(
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


def _handle_ask(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    # Headless ask requires the prior subprocess to have exited and a session id
    # to be discoverable so we can resume. We don't currently spawn a fresh
    # `claude -p --resume <sid>` for follow-ups; flag this until that lands.
    if s.mode == "headless":
        print("error: ask --headless follow-ups not yet implemented; "
              "spawn a fresh headless worker per request",
              file=sys.stderr)
        return 4
    send_args = argparse.Namespace(
        config=getattr(args, "config", None),
        logs_dir=getattr(args, "logs_dir", None), label=args.label,
        tmux_session=args.tmux_session, text=args.text, file=None,
    )
    rc = _handle_send(send_args)
    if rc != 0:
        return rc
    wait_args = argparse.Namespace(
        config=getattr(args, "config", None),
        logs_dir=getattr(args, "logs_dir", None), label=args.label,
        timeout=args.timeout, stable_ms=args.stable_ms,
        poll_ms=500, discover_timeout=30.0,
    )
    rc = _handle_wait(wait_args)
    if rc != 0:
        return rc
    cap_args = argparse.Namespace(
        config=getattr(args, "config", None),
        logs_dir=getattr(args, "logs_dir", None), label=args.label,
        full=False, include_thinking=args.include_thinking,
        include_tool_use=args.include_tool_use, since=None,
    )
    return _handle_capture(cap_args)


def _handle_list(args: argparse.Namespace) -> int:
    cfg = config_from_args(args)
    workdir = workdir_from_args(args)
    workers = state.list_all(workdir)
    if args.json:
        rows = [{"state": _compute_state(s), **_asdict(s)} for s in workers]
        print(_json.dumps(rows, indent=2))
        return 0
    if not workers:
        print("(no workers)")
        return 0
    print(f"{'LABEL':<20s} {'MODE':<12s} {'STATE':<10s} {'CREATED':<22s}")
    for s in workers:
        print(f"{s.label:<20s} {s.mode:<12s} {_compute_state(s):<10s} "
              f"{_utc_iso_to_display(s.created_at, cfg):<22s}")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    cfg = config_from_args(args)
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    if args.json:
        print(_json.dumps({"state": _compute_state(s), **_asdict(s)}, indent=2))
        return 0
    print(f"{s.label} ({s.mode})")
    print(f"  state:        {_compute_state(s)}")
    print(f"  cwd:          {s.cwd}")
    print(f"  session_id:   {s.session_id or '-'}")
    print(f"  created:      {_utc_iso_to_display(s.created_at, cfg)}")
    print(f"  started:      {_utc_iso_to_display(s.started_at, cfg)}")
    print(f"  last_send:    {_utc_iso_to_display(s.last_send_at, cfg)}")
    print(f"  tmux:         {s.tmux_session}:{s.tmux_window}  →  tmux a -t {s.tmux_session}")
    print(f"  log:          {s.tmux_log}")
    return 0


def _handle_kill(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        _do_kill(workdir, args.label, purge=args.purge)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    return 0


def _do_attach(session: str, window: str) -> int:
    """Focus the named tmux window then exec into `tmux attach`.

    Split out so tests can monkeypatch this without execvp'ing tmux.
    On success this never returns because os.execvp replaces the
    process; the trailing 0 is only reached if execvp itself raises and
    that exception propagates up.
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
              f"(try respawning with 'oum-worker spawn ... --replace')",
              file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("attach requires a tty (you probably meant "
              "'oum-worker send' or 'oum-worker ask')", file=sys.stderr)
        return 2
    return _do_attach(s.tmux_session, s.tmux_window)


def _handle_logs(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    if args.launchd:
        out_path = workdir / args.label / "launchd.out"
        err_path = workdir / args.label / "launchd.err"
        print(out_path)
        print(err_path)
        if args.tail:
            paths = [str(p) for p in (out_path, err_path) if p.exists()]
            if paths:
                os.execvp("tail", ["tail", "-F", *paths])
        return 0
    target = Path(s.tmux_log)
    print(target)
    if args.tail and target.exists():
        os.execvp("tail", ["tail", "-F", str(target)])
    return 0


# ---------- parser ----------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oum-worker",
        description="Manage Claude Code sessions (interactive in tmux or headless).",
    )
    _add_global(p)
    sub = p.add_subparsers(dest="verb", required=True)

    sp_spawn = sub.add_parser("spawn", help="Start a Claude Code session now.")
    _add_global(sp_spawn)
    _add_spawn_args(sp_spawn)
    sp_spawn.set_defaults(_handler=_handle_spawn)

    sp_sched = sub.add_parser("schedule", help="Schedule a Claude Code session for later via launchd.")
    _add_global(sp_sched)
    _add_spawn_args(sp_sched)
    when = sp_sched.add_mutually_exclusive_group(required=True)
    when.add_argument("--in", dest="delay")
    when.add_argument("--at", dest="at")
    sp_sched.add_argument("--launch-agents-dir",
                          default=str(Path.home() / "Library" / "LaunchAgents"))
    sp_sched.add_argument("--no-bootstrap", action="store_true")
    sp_sched.add_argument("--dry-run", action="store_true")
    sp_sched.set_defaults(_handler=_handle_schedule)

    sp_send = sub.add_parser("send", help="Send a message to a live worker.")
    _add_global(sp_send)
    sp_send.add_argument("--label", required=True)
    sp_send.add_argument("--tmux-session", default=None)
    g = sp_send.add_mutually_exclusive_group(required=True)
    g.add_argument("text", nargs="?")
    g.add_argument("--file")
    sp_send.set_defaults(_handler=_handle_send)

    sp_cap = sub.add_parser("capture", help="Print the worker's most recent response.")
    _add_global(sp_cap)
    sp_cap.add_argument("--label", required=True)
    sp_cap.add_argument("--full", action="store_true")
    sp_cap.add_argument("--include-thinking", action="store_true")
    sp_cap.add_argument("--include-tool-use", action="store_true")
    sp_cap.add_argument("--since")
    sp_cap.set_defaults(_handler=_handle_capture)

    sp_wait = sub.add_parser("wait", help="Block until the worker is idle.")
    _add_global(sp_wait)
    sp_wait.add_argument("--label", required=True)
    sp_wait.add_argument("--timeout", type=float, default=600.0)
    sp_wait.add_argument("--stable-ms", type=int, default=1500)
    sp_wait.add_argument("--poll-ms", type=int, default=500)
    sp_wait.add_argument("--discover-timeout", type=float, default=30.0)
    sp_wait.set_defaults(_handler=_handle_wait)

    sp_ask = sub.add_parser("ask", help="send + wait + capture")
    _add_global(sp_ask)
    sp_ask.add_argument("--label", required=True)
    sp_ask.add_argument("text")
    sp_ask.add_argument("--tmux-session", default=None)
    sp_ask.add_argument("--timeout", type=float, default=600.0)
    sp_ask.add_argument("--stable-ms", type=int, default=1500)
    sp_ask.add_argument("--include-thinking", action="store_true")
    sp_ask.add_argument("--include-tool-use", action="store_true")
    sp_ask.set_defaults(_handler=_handle_ask)

    sp_list = sub.add_parser("list", help="List all known workers.")
    _add_global(sp_list)
    sp_list.add_argument("--json", action="store_true")
    sp_list.set_defaults(_handler=_handle_list)

    sp_status = sub.add_parser("status", help="Show one worker's state.")
    _add_global(sp_status)
    sp_status.add_argument("--label", required=True)
    sp_status.add_argument("--json", action="store_true")
    sp_status.set_defaults(_handler=_handle_status)

    sp_kill = sub.add_parser("kill", help="Close window and unbootstrap any plist.")
    _add_global(sp_kill)
    sp_kill.add_argument("--label", required=True)
    sp_kill.add_argument("--purge", action="store_true")
    sp_kill.add_argument("--tmux-session", default=None)
    sp_kill.set_defaults(_handler=_handle_kill)

    sp_logs = sub.add_parser("logs", help="Print log path or tail it.")
    _add_global(sp_logs)
    sp_logs.add_argument("--label", required=True)
    sp_logs.add_argument("--tail", action="store_true")
    sp_logs.add_argument("--launchd", action="store_true")
    sp_logs.set_defaults(_handler=_handle_logs)

    sp_attach = sub.add_parser(
        "attach",
        help="Attach your terminal to a running interactive session.",
    )
    _add_global(sp_attach)
    sp_attach.add_argument("--label", required=True)
    sp_attach.set_defaults(_handler=_handle_attach)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args._handler(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
