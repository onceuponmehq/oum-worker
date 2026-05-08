"""Engine strategy module.

Each engine knows how to build a shell invocation for its CLI and which
JSONL parser to use for capture / wait / ask. The CLI dispatches every
verb through ``engines.get(state.engine).{build_invocation,
jsonl_module}`` so the rest of the codebase doesn't branch on engine
name.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional

from oum_worker import jsonl, codex_jsonl


@dataclass(frozen=True)
class Engine:
    name: str
    default_binary: str
    yolo_default: bool
    jsonl_module: ModuleType
    _build_invocation: Callable[..., str]

    def build_invocation(self, **kwargs) -> str:
        return self._build_invocation(**kwargs)


def _claude_build(*, binary: str, prompt_file: Optional[Path],
                  headless: bool, resume: Optional[str],
                  session_name: Optional[str], model: Optional[str],
                  yolo: bool, permission_mode: Optional[str],
                  cwd: Path) -> str:
    """Mirrors the historical `_cc_invocation`. Claude does not use the
    cwd flag (zsh cd's into it before claude runs); cwd is accepted to
    keep the engine API uniform but ignored. ``model`` is unused for
    claude.
    """
    parts: list[str] = [binary] + (["-p"] if headless else [])
    if resume:
        parts.extend(["--resume", shlex.quote(resume)])
    if (not headless) and session_name:
        parts.extend(["--name", shlex.quote(session_name)])
    if permission_mode:
        parts.extend(["--permission-mode", shlex.quote(permission_mode)])
    if yolo:
        parts.append("--dangerously-skip-permissions")
    if prompt_file is not None:
        parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    return " ".join(parts)


def _codex_build(*, binary: str, prompt_file: Optional[Path],
                 headless: bool, resume: Optional[str],
                 session_name: Optional[str], model: Optional[str],
                 yolo: bool, permission_mode: Optional[str],
                 cwd: Path) -> str:
    """Codex CLI shape:

      codex [exec] [resume <sid>] [--yolo] [-m model] -C <cwd> ["$(cat prompt)"]

    `resume` is a subcommand for codex (not a flag) and must precede
    the flag list. Headless uses the `exec` subcommand. session_name
    and permission_mode are silently ignored (claude-only concepts);
    the CLI emits a one-line warning at spawn time when those flags
    are explicitly passed for engine=codex.
    """
    if headless and prompt_file is None:
        raise ValueError("codex headless requires a prompt")

    parts: list[str] = [binary]
    if headless:
        parts.append("exec")
    if resume:
        parts.extend(["resume", shlex.quote(resume)])
    if yolo:
        parts.append("--yolo")
    if model:
        parts.extend(["-m", shlex.quote(model)])
    parts.extend(["-C", shlex.quote(str(cwd))])
    if prompt_file is not None:
        parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    return " ".join(parts)


_CLAUDE = Engine(
    name="claude",
    default_binary="claude",
    yolo_default=False,
    jsonl_module=jsonl,
    _build_invocation=_claude_build,
)

_CODEX = Engine(
    name="codex",
    default_binary="codex",
    yolo_default=True,
    jsonl_module=codex_jsonl,
    _build_invocation=_codex_build,
)

_REGISTRY = {"claude": _CLAUDE, "codex": _CODEX}


def get(name: str) -> Engine:
    if name not in _REGISTRY:
        raise ValueError(f"unknown engine {name!r} (known: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def known_names() -> list[str]:
    return sorted(_REGISTRY.keys())
