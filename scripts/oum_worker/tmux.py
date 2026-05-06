"""tmux session/window operations for oum-worker."""
from __future__ import annotations

import functools
import shlex
import subprocess
from pathlib import Path
from typing import Optional


_CANDIDATES = (
    "/opt/homebrew/bin/tmux",
    "/usr/local/bin/tmux",
    "/usr/bin/tmux",
)


class TmuxError(Exception):
    pass


@functools.lru_cache(maxsize=1)
def find_tmux_bin() -> Path:
    """Locate the tmux binary. Cached because `wait` calls _run hundreds of
    times during a poll loop and re-resolving on every call would risk
    falling back to a `zsh -lic command -v tmux` shellout per tick."""
    for c in _CANDIDATES:
        p = Path(c)
        if p.exists():
            return p
    r = subprocess.run(["/bin/zsh", "-lic", "command -v tmux"],
                       capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("/") and Path(line).exists():
            return Path(line)
    raise FileNotFoundError("tmux not found. Install with: brew install tmux")


def _run(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    bin_ = find_tmux_bin()
    return subprocess.run([str(bin_), *args], capture_output=True, text=True, check=check)


def ensure_session(session: str, *, width: int = 220, height: int = 50) -> None:
    r = _run("has-session", "-t", session)
    if r.returncode != 0:
        _run("new-session", "-d", "-s", session,
             "-x", str(width), "-y", str(height), check=True)
    # Always (re)install the after-new-window hook — including for sessions that
    # already existed before our first ensure_session call. Otherwise, an existing
    # `oum` session a user already has open would never get the hook, and our
    # spawned interactive workers would lose their windows on fast-exit commands.
    # The hook is idempotent (re-setting overwrites) so this is safe to repeat.
    # `set-hook -t session` is per-session — does not leak server-wide like
    # `set-window-option -g` would.
    _run("set-hook", "-t", session, "after-new-window",
         "set-window-option remain-on-exit on", check=True)


def window_exists(session: str, window: str) -> bool:
    r = _run("list-windows", "-t", session, "-F", "#{window_name}")
    if r.returncode != 0:
        return False
    return window in r.stdout.split()


def kill_window(session: str, window: str) -> None:
    _run("kill-window", "-t", f"{session}:{window}")


def open_window(*, session: str, window: str, cwd: Path, command: str,
                log_path: Path, replace: bool = False) -> None:
    """Open `window` inside `session` running `command` in `cwd`.

    - Creates session if missing (with remain-on-exit as the session-default).
    - If window already exists and replace=False: raises TmuxError.
    - If replace=True: kill the existing window first.
    - Pipes pane output to log_path.
    """
    ensure_session(session)
    if window_exists(session, window):
        if not replace:
            raise TmuxError(f"window {window} already exists in session {session}")
        kill_window(session, window)
    target = f"{session}:{window}"
    _run("new-window", "-t", f"{session}:", "-n", window, "-c", str(cwd), command, check=True)
    _run("pipe-pane", "-t", target, "-o", f"cat >> {shlex.quote(str(log_path))}")


def send_text(session: str, window: str, text: str, *, submit: bool = True) -> None:
    """Send `text` into the pane. If submit=True, append Enter."""
    target = f"{session}:{window}"
    if submit:
        _run("send-keys", "-t", target, text, "Enter", check=True)
    else:
        _run("send-keys", "-t", target, text, check=True)


def send_file(session: str, window: str, path: Path, *, submit: bool = True) -> None:
    """Send file contents via tmux paste-buffer (avoids shell quoting / shortcut keys)."""
    target = f"{session}:{window}"
    _run("load-buffer", str(path), check=True)
    _run("paste-buffer", "-t", target, check=True)
    if submit:
        _run("send-keys", "-t", target, "Enter", check=True)


def capture_pane(session: str, window: str) -> str:
    target = f"{session}:{window}"
    r = _run("capture-pane", "-t", target, "-p")
    return r.stdout if r.returncode == 0 else ""
