"""Configuration loading for oum-worker.

The public CLI should not know about a caller's private repositories, launchd
label namespace, timezone, or preferred Claude binary. Those belong in a local
JSON config file or environment variables.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_LABEL_PREFIX = "com.agentworker.schedule."
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DEFAULT_TMUX_SESSION = "workers"
DEFAULT_CLAUDE_BIN = "claude"
DEFAULT_CODEX_BIN = "codex"
DEFAULT_TIMEZONE = "UTC"


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(value: str | os.PathLike[str], *, base: Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _validate_timezone(value: str) -> str:
    ZoneInfo(value)
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON config at {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must be a JSON object")
    return data


def _coerce_aliases(raw: Any, *, base: Path) -> dict[str, Path]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("repo_aliases must be a JSON object")
    aliases: dict[str, Path] = {}
    for name, path in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("repo alias names must be non-empty strings")
        if not isinstance(path, str):
            raise ValueError(f"repo alias {name!r} must map to a string path")
        aliases[name] = _resolve_path(path, base=base)
    return aliases


@dataclass(frozen=True)
class WorkerConfig:
    default_cwd: Path
    logs_dir: Path
    tmux_session: str
    claude_bin: str
    codex_bin: str
    timezone: str
    launchd_label_prefix: str
    path: str
    repo_aliases: dict[str, Path]
    scripts_dir: Path

    @classmethod
    def defaults(cls, *, default_cwd: Path | None = None) -> "WorkerConfig":
        cwd = (default_cwd or Path.cwd()).expanduser().resolve()
        return cls(
            default_cwd=cwd,
            logs_dir=(cwd / ".logs" / "oum-worker").resolve(),
            tmux_session=DEFAULT_TMUX_SESSION,
            claude_bin=DEFAULT_CLAUDE_BIN,
            codex_bin=DEFAULT_CODEX_BIN,
            timezone=_validate_timezone(os.environ.get("TZ") or DEFAULT_TIMEZONE),
            launchd_label_prefix=DEFAULT_LABEL_PREFIX,
            path=DEFAULT_PATH,
            repo_aliases={},
            scripts_dir=_scripts_dir(),
        )

    def with_updates(self, **changes: Any) -> "WorkerConfig":
        if "timezone" in changes and changes["timezone"] is not None:
            changes["timezone"] = _validate_timezone(str(changes["timezone"]))
        return replace(self, **changes)


def _apply_file_config(cfg: WorkerConfig, data: dict[str, Any], *, base: Path) -> WorkerConfig:
    changes: dict[str, Any] = {}
    if "default_cwd" in data:
        changes["default_cwd"] = _resolve_path(data["default_cwd"], base=base)
    if "logs_dir" in data:
        changes["logs_dir"] = _resolve_path(data["logs_dir"], base=base)
    if "tmux_session" in data:
        changes["tmux_session"] = str(data["tmux_session"])
    if "claude_bin" in data:
        changes["claude_bin"] = str(data["claude_bin"])
    if "codex_bin" in data:
        changes["codex_bin"] = str(data["codex_bin"])
    if "timezone" in data:
        changes["timezone"] = str(data["timezone"])
    if "launchd_label_prefix" in data:
        changes["launchd_label_prefix"] = str(data["launchd_label_prefix"])
    if "path" in data:
        changes["path"] = str(data["path"])
    if "repo_aliases" in data:
        changes["repo_aliases"] = _coerce_aliases(data["repo_aliases"], base=base)
    if "scripts_dir" in data:
        changes["scripts_dir"] = _resolve_path(data["scripts_dir"], base=base)
    return cfg.with_updates(**changes)


def _apply_env_config(cfg: WorkerConfig) -> WorkerConfig:
    changes: dict[str, Any] = {}
    path_base = Path.cwd()
    env_path_keys = {
        "OUM_WORKER_DEFAULT_CWD": "default_cwd",
        "OUM_WORKER_LOGS_DIR": "logs_dir",
        "OUM_WORKER_SCRIPTS_DIR": "scripts_dir",
    }
    for env_key, cfg_key in env_path_keys.items():
        value = os.environ.get(env_key)
        if value:
            changes[cfg_key] = _resolve_path(value, base=path_base)

    string_keys = {
        "OUM_WORKER_TMUX_SESSION": "tmux_session",
        "OUM_WORKER_CLAUDE_BIN": "claude_bin",
        "OUM_WORKER_CODEX_BIN": "codex_bin",
        "OUM_WORKER_TIMEZONE": "timezone",
        "OUM_WORKER_LAUNCHD_LABEL_PREFIX": "launchd_label_prefix",
        "OUM_WORKER_PATH": "path",
    }
    for env_key, cfg_key in string_keys.items():
        value = os.environ.get(env_key)
        if value:
            changes[cfg_key] = value

    aliases = os.environ.get("OUM_WORKER_REPO_ALIASES")
    if aliases:
        try:
            raw = json.loads(aliases)
        except json.JSONDecodeError as e:
            raise ValueError(f"OUM_WORKER_REPO_ALIASES must be JSON: {e}") from e
        changes["repo_aliases"] = _coerce_aliases(raw, base=path_base)

    return cfg.with_updates(**changes)


def load_config(config_path: str | os.PathLike[str] | None = None) -> WorkerConfig:
    """Load worker configuration from defaults, optional JSON, then env vars.

    Precedence, from lowest to highest:
    1. Generic public defaults.
    2. JSON file passed by `--config` or `OUM_WORKER_CONFIG`.
    3. Environment variables such as `OUM_WORKER_LOGS_DIR`.
    """
    cfg = WorkerConfig.defaults()
    selected = config_path or os.environ.get("OUM_WORKER_CONFIG")
    if selected:
        path = Path(selected).expanduser().resolve()
        cfg = _apply_file_config(cfg, _read_json_object(path), base=path.parent)
    return _apply_env_config(cfg)
