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
from pathlib import Path

from oum_worker import jsonl, launchd, state
from oum_worker import tmux as _tmux
from oum_worker.launchd import ROOT


DEFAULT_LOGS_DIR = ROOT / ".logs" / "oum-worker"
DEFAULT_TMUX_SESSION = "oum"


# ---------- helpers ----------

def workdir_from_args(args: argparse.Namespace) -> Path:
    p = Path(args.logs_dir).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _add_global(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--logs-dir",
        default=os.environ.get("OUM_WORKER_LOGS_DIR", str(DEFAULT_LOGS_DIR)),
        help=f"State + log directory root (default: {DEFAULT_LOGS_DIR})",
    )


def _read_prompt(args: argparse.Namespace) -> str:
    if getattr(args, "prompt_file", None):
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    return (getattr(args, "prompt", None) or "").strip()


def _resolve_cwd(args: argparse.Namespace) -> Path:
    return launchd.resolve_workdir(repo=getattr(args, "repo", None),
                                   cwd=getattr(args, "cwd", None))


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


def _do_kill(workdir: Path, label: str, *, purge: bool, tmux_session: str) -> None:
    s = state.read(workdir, label)
    try:
        _tmux.kill_window(s.tmux_session, s.tmux_window)
    except _tmux.TmuxError:
        pass
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
    p = sp.add_mutually_exclusive_group(required=True)
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
    sp.add_argument("--interactive", dest="headless", action="store_false", default=False)
    sp.add_argument("--headless", dest="headless", action="store_true")
    sp.add_argument("--repo")
    sp.add_argument("--cwd")
    sp.add_argument("--name", help="Claude session name (interactive only)")
    sp.add_argument("--cc-command", "--claude-bin", dest="claude_bin", default="cc")
    sp.add_argument("--permission-mode",
                    choices=["acceptEdits", "auto", "bypassPermissions",
                             "default", "dontAsk", "plan"])
    sp.add_argument("--dangerously-skip-permissions", dest="skip_permissions",
                    action="store_true")
    sp.add_argument("--tmux-session", default=DEFAULT_TMUX_SESSION)
    sp.add_argument("--replace", action="store_true")


# ---------- handlers ----------

def _handle_spawn(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    cwd = _resolve_cwd(args)
    prompt = _read_prompt(args)
    if not prompt:
        print("error: prompt is empty", file=sys.stderr)
        return 1

    if args.replace:
        try:
            _do_kill(workdir, args.label, purge=True, tmux_session=args.tmux_session)
        except state.WorkerNotFound:
            pass

    try:
        s = state.create(
            workdir,
            label=args.label,
            mode="headless" if args.headless else "interactive",
            cwd=cwd,
            claude_bin=args.claude_bin,
            tmux_session=args.tmux_session,
        )
    except state.LabelExists:
        print(f"error: label {args.label!r} already exists (pass --replace to overwrite)",
              file=sys.stderr)
        return 5

    Path(s.prompt_file).write_text(prompt, encoding="utf-8")

    if args.headless:
        return _spawn_headless(args, s, prompt)
    return _spawn_interactive(args, s)


def _spawn_interactive(args: argparse.Namespace, s: state.WorkerState) -> int:
    cc = launchd._cc_invocation(
        claude_bin=args.claude_bin, resume=args.resume, new_session=args.new_session,
        session_name=args.name, permission_mode=args.permission_mode,
        skip_permissions=args.skip_permissions, prompt_file=Path(s.prompt_file),
        headless=False,
    )
    pane_command = f"/bin/zsh -lic {shlex.quote(f'cd {shlex.quote(s.cwd)} && {cc}')}"
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


def _spawn_headless(args: argparse.Namespace, s: state.WorkerState, prompt: str) -> int:
    cmd = ["claude", "-p"]
    if args.resume:
        cmd.extend(["--resume", args.resume])
    if args.permission_mode:
        cmd.extend(["--permission-mode", args.permission_mode])
    if args.skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.append(prompt)

    response_path = Path(s.tmux_log).parent / "response.txt"
    state.update(workdir_from_args(args), s.label, started_at=state.utc_now_iso())
    with open(response_path, "w") as out:
        r = subprocess.run(cmd, cwd=s.cwd, stdout=out, stderr=subprocess.STDOUT)
    state.update(workdir_from_args(args), s.label, ended_at=state.utc_now_iso())
    if r.returncode != 0:
        print(f"headless worker {s.label} exited {r.returncode}", file=sys.stderr)
        return 4
    print(response_path.read_text())
    return 0


def _handle_schedule(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    cwd = _resolve_cwd(args)
    prompt = _read_prompt(args)
    if not prompt:
        print("error: prompt is empty", file=sys.stderr)
        return 1

    if args.replace:
        try:
            _do_kill(workdir, args.label, purge=True, tmux_session=args.tmux_session)
        except state.WorkerNotFound:
            pass

    try:
        s = state.create(
            workdir, label=args.label, mode="scheduled", cwd=cwd,
            claude_bin=args.claude_bin, tmux_session=args.tmux_session,
        )
    except state.LabelExists:
        print(f"error: label {args.label!r} already exists", file=sys.stderr)
        return 5

    Path(s.prompt_file).write_text(prompt, encoding="utf-8")
    target = launchd.parse_target(args.delay, args.at)
    launchd_label = launchd.normalize_label(args.label)
    plist_path = Path(args.launch_agents_dir).expanduser() / f"{launchd_label}.plist"

    inner_cmd = launchd.build_inner_command(
        cwd=cwd, claude_bin=args.claude_bin, prompt_file=Path(s.prompt_file),
        log_path=Path(s.tmux_log), label=args.label, logs_dir=workdir,
        resume=args.resume, new_session=args.new_session, session_name=args.name,
        permission_mode=args.permission_mode, skip_permissions=args.skip_permissions,
        tmux_session=args.tmux_session, headless=args.headless,
    )
    payload = launchd.build_plist(
        label=launchd_label, cwd=cwd, command=inner_cmd, target=target,
        stdout_path=workdir / args.label / "launchd.out",
        stderr_path=workdir / args.label / "launchd.err",
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
    print(f"  Runs at: {target.strftime('%Y-%m-%d %H:%M IST')}")
    print(f"  Prompt:  {s.prompt_file}")
    print(f"  Window:  tmux a -t {args.tmux_session}  →  '{args.label}'")
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
    send_args = argparse.Namespace(
        logs_dir=args.logs_dir, label=args.label,
        tmux_session=args.tmux_session, text=args.text, file=None,
    )
    rc = _handle_send(send_args)
    if rc != 0:
        return rc
    wait_args = argparse.Namespace(
        logs_dir=args.logs_dir, label=args.label,
        timeout=args.timeout, stable_ms=args.stable_ms,
        poll_ms=500, discover_timeout=30.0,
    )
    rc = _handle_wait(wait_args)
    if rc != 0:
        return rc
    cap_args = argparse.Namespace(
        logs_dir=args.logs_dir, label=args.label,
        full=False, include_thinking=args.include_thinking,
        include_tool_use=args.include_tool_use, since=None,
    )
    return _handle_capture(cap_args)


def _handle_list(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    workers = state.list_all(workdir)
    if args.json:
        rows = [{"state": _compute_state(s), **_asdict(s)} for s in workers]
        print(_json.dumps(rows, indent=2))
        return 0
    if not workers:
        print("(no workers)")
        return 0
    print(f"{'LABEL':<20s} {'MODE':<12s} {'STATE':<10s} {'CREATED':<25s}")
    for s in workers:
        print(f"{s.label:<20s} {s.mode:<12s} {_compute_state(s):<10s} {s.created_at:<25s}")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
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
    print(f"  created:      {s.created_at}")
    print(f"  started:      {s.started_at or '-'}")
    print(f"  last_send:    {s.last_send_at or '-'}")
    print(f"  tmux:         {s.tmux_session}:{s.tmux_window}  →  tmux a -t {s.tmux_session}")
    print(f"  log:          {s.tmux_log}")
    return 0


def _handle_kill(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        _do_kill(workdir, args.label, purge=args.purge,
                 tmux_session=args.tmux_session)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    return 0


def _handle_logs(args: argparse.Namespace) -> int:
    workdir = workdir_from_args(args)
    try:
        s = state.read(workdir, args.label)
    except state.WorkerNotFound:
        print(f"no worker named {args.label!r}", file=sys.stderr)
        return 1
    if args.launchd:
        target = workdir / args.label / "launchd.out"
    else:
        target = Path(s.tmux_log)
    print(target)
    if args.tail and target.exists():
        os.execvp("tail", ["tail", "-F", str(target)])
    return 0


# ---------- parser ----------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oum-worker",
                                description="Manage Claude Code worker sessions.")
    _add_global(p)
    sub = p.add_subparsers(dest="verb", required=True)

    sp_spawn = sub.add_parser("spawn", help="Start a Claude Code worker now.")
    _add_global(sp_spawn)
    _add_spawn_args(sp_spawn)
    sp_spawn.set_defaults(_handler=_handle_spawn)

    sp_sched = sub.add_parser("schedule", help="Schedule a Claude Code worker for later via launchd.")
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
    sp_send.add_argument("--tmux-session", default=DEFAULT_TMUX_SESSION)
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
    sp_ask.add_argument("--tmux-session", default=DEFAULT_TMUX_SESSION)
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
    sp_kill.add_argument("--tmux-session", default=DEFAULT_TMUX_SESSION)
    sp_kill.set_defaults(_handler=_handle_kill)

    sp_logs = sub.add_parser("logs", help="Print log path or tail it.")
    _add_global(sp_logs)
    sp_logs.add_argument("--label", required=True)
    sp_logs.add_argument("--tail", action="store_true")
    sp_logs.add_argument("--launchd", action="store_true")
    sp_logs.set_defaults(_handler=_handle_logs)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args._handler(args)


if __name__ == "__main__":
    sys.exit(main())
