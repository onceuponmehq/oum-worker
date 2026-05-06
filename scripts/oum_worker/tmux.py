"""tmux session/window operations for oum-worker."""
from __future__ import annotations

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


def find_tmux_bin() -> Path:
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
    if r.returncode == 0:
        return
    _run("new-session", "-d", "-s", session, "-x", str(width), "-y", str(height), check=True)
    # Session-scoped hook fires after every new window in this session and
    # applies remain-on-exit on the freshly-created window. This avoids the
    # race where a fast-exit command finishes before a separate setw call lands.
    # `set-hook -t session` is per-session, unlike `set-window-option -g`
    # which would be server-wide and leak to unrelated sessions.
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
