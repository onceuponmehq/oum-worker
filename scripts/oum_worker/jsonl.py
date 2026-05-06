"""Claude Code JSONL session-file location, parsing, and idle detection."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


def encode_cwd(cwd: Path) -> str:
    """Convert /a/b/c → -a-b-c, matching ~/.claude/projects/<encoded>/ layout."""
    return "-" + "-".join(p for p in str(cwd.resolve() if cwd.exists() else cwd).split("/") if p)


def projects_dir() -> Path:
    return Path(os.environ.get("HOME", "")) / ".claude" / "projects"


def find_by_session_id(cwd: Path, session_id: str) -> Optional[Path]:
    p = projects_dir() / encode_cwd(cwd) / f"{session_id}.jsonl"
    return p if p.exists() else None
