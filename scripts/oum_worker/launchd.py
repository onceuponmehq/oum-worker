"""launchd plist builders + time parsing."""
from __future__ import annotations

import os
import plistlib
import re
import shlex
import subprocess
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from oum_worker import config as worker_config

SCRIPTS_DIR = Path(__file__).resolve().parents[1]

DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# Reserved env keys are written by build_plist as the baseline
# EnvironmentVariables. User-supplied --env values must not clobber them so
# that scheduled jobs always inherit the same timezone, PATH, and locale.
RESERVED_ENV_KEYS = frozenset({"TZ", "PATH", "LANG"})


def parse_env_pairs(values: list[str] | None) -> dict[str, str]:
    """Parse repeated --env KEY=VALUE flags into a dict.

    Splits on the first ``=`` so VALUE may itself contain equals signs
    (e.g. URLs with query strings). Refuses reserved baseline keys so
    they can't be overridden from the command line.

    Later occurrences of the same KEY override earlier ones.
    """
    if not values:
        return {}

    pairs: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(
                f"--env value {raw!r} must be KEY=VALUE (missing '=')"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--env value {raw!r} has an empty key")
        if key in RESERVED_ENV_KEYS:
            raise ValueError(
                f"--env key {key!r} is reserved (baseline plist env). "
                "Pick a different name."
            )
        pairs[key] = value
    return pairs


def _zone(cfg: worker_config.WorkerConfig | None = None,
          now: datetime | None = None) -> tzinfo:
    if cfg is None and now is not None and now.tzinfo is not None:
        return now.tzinfo
    return ZoneInfo((cfg or worker_config.load_config()).timezone)


def now_in_timezone(cfg: worker_config.WorkerConfig | None = None) -> datetime:
    return datetime.now(_zone(cfg))


def round_up_to_minute(value: datetime) -> datetime:
    if value.second == 0 and value.microsecond == 0:
        return value
    return (value + timedelta(minutes=1)).replace(second=0, microsecond=0)


def parse_delay(value: str) -> int:
    text = value.strip().lower()
    if not text:
        raise ValueError("duration must not be empty")
    total = 0
    pos = 0
    matched = False
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = re.match(r"(\d+)\s*([smhd])", text[pos:])
        if not m:
            raise ValueError("duration must use units like 30m, 3h, or 1h30m")
        total += int(m.group(1)) * DURATION_UNITS[m.group(2)]
        matched = True
        pos += m.end()
    if not matched or total <= 0:
        raise ValueError("duration must be greater than zero")
    return total


def parse_at_time(value: str, now: datetime, *,
                  cfg: worker_config.WorkerConfig | None = None) -> datetime:
    zone = _zone(cfg, now)
    text = value.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            raise ValueError("--at time must be a valid HH:MM value")
        target = now.astimezone(zone).replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now.astimezone(zone):
            target += timedelta(days=1)
        return target
    try:
        target = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as e:
        raise ValueError("--at must be HH:MM or YYYY-MM-DD HH:MM") from e
    if target.tzinfo is None:
        target = target.replace(tzinfo=zone)
    target = target.astimezone(zone)
    if target <= now.astimezone(zone):
        raise ValueError("--at must be in the future")
    return target


def parse_target(delay: Optional[str], at: Optional[str], *,
                 now: Optional[datetime] = None,
                 cfg: worker_config.WorkerConfig | None = None) -> datetime:
    if delay and at:
        raise ValueError("use either --in or --at, not both")
    if not delay and not at:
        raise ValueError("schedule requires --in or --at")
    zone = _zone(cfg, now)
    cur = (now or now_in_timezone(cfg)).astimezone(zone)
    if delay:
        return round_up_to_minute(cur + timedelta(seconds=parse_delay(delay)))
    return round_up_to_minute(parse_at_time(at, cur, cfg=cfg))


def normalize_label(value: str, *,
                    cfg: worker_config.WorkerConfig | None = None) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    if not text:
        raise ValueError("label must contain at least one alphanumeric character")
    if text.startswith("com."):
        return text
    prefix = (cfg or worker_config.load_config()).launchd_label_prefix
    return f"{prefix}{text}"


def resolve_workdir(*, repo: Optional[str], cwd: Optional[str],
                    cfg: worker_config.WorkerConfig | None = None) -> Path:
    cfg = cfg or worker_config.load_config()
    if repo and cwd:
        raise ValueError("use either --repo or --cwd, not both")
    if repo:
        if repo not in cfg.repo_aliases:
            known = ", ".join(sorted(cfg.repo_aliases)) or "(none configured)"
            raise ValueError(f"unknown repo alias {repo!r}; known aliases: {known}")
        return cfg.repo_aliases[repo].expanduser().resolve()
    if cwd:
        return Path(cwd).expanduser().resolve()
    return cfg.default_cwd


def build_plist(*, label: str, cwd: Path, command: str, target: datetime,
                stdout_path: Path, stderr_path: Path,
                env_pairs: dict[str, str] | None = None,
                cfg: worker_config.WorkerConfig | None = None) -> bytes:
    cfg = cfg or worker_config.load_config()
    environment: dict[str, str] = {
        "TZ": cfg.timezone,
        "PATH": cfg.path,
        "LANG": "en_US.UTF-8",
    }
    if env_pairs:
        for key, value in env_pairs.items():
            # Defence in depth: parse_env_pairs already refuses these,
            # but if a caller passes the dict directly, keep baseline.
            if key in RESERVED_ENV_KEYS:
                continue
            environment[key] = value
    plist = {
        "Label": label,
        "WorkingDirectory": "/tmp",
        "ProgramArguments": ["/bin/zsh", "-lic", command],
        "EnvironmentVariables": environment,
        "StartCalendarInterval": {
            "Month": target.month,
            "Day": target.day,
            "Hour": target.hour,
            "Minute": target.minute,
        },
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "RunAtLoad": False,
        "LaunchOnlyOnce": True,
        "AbandonProcessGroup": True,
    }
    return plistlib.dumps(plist, sort_keys=False)


def write_plist(path: Path, payload: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise FileExistsError(f"{path} already exists; pass replace=True")
    path.write_bytes(payload)
    path.chmod(0o644)


def bootstrap(label: str, plist_path: Path, *, replace: bool) -> None:
    uid = os.getuid()
    if replace:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=True)
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{label}"], check=True)


def unbootstrap(label: str) -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cc_invocation(*, claude_bin: str, resume: Optional[str], new_session: bool,
                   session_name: Optional[str], permission_mode: Optional[str],
                   skip_permissions: bool, prompt_file: Optional[Path],
                   headless: bool) -> str:
    """Back-compat shim. Delegates to engines.ClaudeEngine.

    Kept so that other tests and any external consumer that imported
    this helper continue to work. Local import avoids any chance of an
    import cycle with the engines module.
    """
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
        cwd=Path.cwd(),  # claude ignores cwd; placeholder
    )


def _env_export_prefix(env_pairs: dict[str, str] | None) -> str:
    """Return ``export K=V && ...`` to prepend to the inner shell chain.

    Returns the empty string when there are no exports. Reserved keys are
    skipped (defence in depth — `parse_env_pairs` already refused them).

    launchd passes EnvironmentVariables to the outermost shell, but the
    tmux + interactive-zsh chain may strip or reset variables; an explicit
    ``export`` in the inner command is the safe path so the inner ``claude``
    process inherits them.
    """
    if not env_pairs:
        return ""
    parts = [
        f"export {key}={shlex.quote(value)}"
        for key, value in env_pairs.items()
        if key not in RESERVED_ENV_KEYS
    ]
    if not parts:
        return ""
    return " && ".join(parts) + " && "


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

    `engine` selects which engines.Engine builds the inner CLI invocation.
    `yolo=None` means "use the engine's default" (claude=False, codex=True);
    pass False explicitly to disable yolo for codex. `skip_permissions=True`
    is back-compat for claude callers and forces yolo on regardless.

    Steps inside the command (in order):
    1. export user-supplied env vars (if any).
    2. cd to cwd.
    3. python3 -m oum_worker.runner mark-started --label <L>   (writes started_at)
    4a. interactive: open a tmux window running cc.
    4b. headless:    run `<engine> ...` with stdout to <logs_dir>/<label>/response.txt.
    """
    from oum_worker import engines as _engines  # noqa: WPS433
    eng = _engines.get(engine)
    effective_yolo = eng.yolo_default if yolo is None else yolo
    if skip_permissions:
        effective_yolo = True

    # PYTHONPATH lets `python3 -m oum_worker.runner` resolve from any cwd —
    # without it, the launchd inner command would die with ModuleNotFoundError
    # because we cd into the worker's cwd before running this.
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
    # Interactive: drive a shared tmux session
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
