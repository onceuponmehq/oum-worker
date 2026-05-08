from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import config, launchd  # noqa: E402


def test_load_config_reads_json_and_resolves_relative_paths(tmp_path):
    (tmp_path / "apps" / "main").mkdir(parents=True)
    cfg_path = tmp_path / "oum-worker.json"
    cfg_path.write_text(json.dumps({
        "logs_dir": ".worker-logs",
        "default_cwd": ".",
        "tmux_session": "agents",
        "claude_bin": "claude",
        "timezone": "Asia/Kolkata",
        "launchd_label_prefix": "com.example.worker.",
        "path": "/custom/bin:/usr/bin:/bin",
        "repo_aliases": {
            "main": "apps/main"
        }
    }))

    cfg = config.load_config(cfg_path)

    assert cfg.logs_dir == tmp_path / ".worker-logs"
    assert cfg.default_cwd == tmp_path
    assert cfg.tmux_session == "agents"
    assert cfg.claude_bin == "claude"
    assert cfg.timezone == "Asia/Kolkata"
    assert cfg.launchd_label_prefix == "com.example.worker."
    assert cfg.path == "/custom/bin:/usr/bin:/bin"
    assert cfg.repo_aliases["main"] == tmp_path / "apps" / "main"


def test_environment_overrides_config_file(tmp_path, monkeypatch):
    cfg_path = tmp_path / "oum-worker.json"
    cfg_path.write_text(json.dumps({
        "logs_dir": "file-logs",
        "tmux_session": "file-session",
        "repo_aliases": {"file": "from-file"}
    }))
    monkeypatch.setenv("OUM_WORKER_LOGS_DIR", str(tmp_path / "env-logs"))
    monkeypatch.setenv("OUM_WORKER_TMUX_SESSION", "env-session")
    monkeypatch.setenv("OUM_WORKER_REPO_ALIASES", json.dumps({
        "api": str(tmp_path / "services" / "api")
    }))

    cfg = config.load_config(cfg_path)

    assert cfg.logs_dir == tmp_path / "env-logs"
    assert cfg.tmux_session == "env-session"
    assert cfg.repo_aliases == {"api": tmp_path / "services" / "api"}


def test_launchd_label_prefix_comes_from_config(tmp_path):
    cfg = config.WorkerConfig.defaults(default_cwd=tmp_path).with_updates(
        launchd_label_prefix="com.example.worker."
    )

    assert launchd.normalize_label("nightly", cfg=cfg) == "com.example.worker.nightly"


def test_resolve_workdir_uses_config_aliases(tmp_path):
    app = tmp_path / "apps" / "main"
    app.mkdir(parents=True)
    cfg = config.WorkerConfig.defaults(default_cwd=tmp_path).with_updates(
        repo_aliases={"main": app}
    )

    assert launchd.resolve_workdir(repo="main", cwd=None, cfg=cfg) == app
    with pytest.raises(ValueError, match="unknown repo alias"):
        launchd.resolve_workdir(repo="missing", cwd=None, cfg=cfg)


def test_public_repo_metadata_exists():
    for rel in [
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "pyproject.toml",
        "configs/oum-worker.example.json",
    ]:
        assert (ROOT / rel).exists(), f"missing public metadata file: {rel}"


def test_codex_bin_default_is_codex():
    cfg = config.WorkerConfig.defaults()
    assert cfg.codex_bin == "codex"


def test_codex_bin_loaded_from_json(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"codex_bin": "/opt/codex/bin/codex"}))
    cfg = config.load_config(p)
    assert cfg.codex_bin == "/opt/codex/bin/codex"


def test_codex_bin_loaded_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OUM_WORKER_CODEX_BIN", "/usr/local/bin/codex")
    cfg = config.load_config(None)
    assert cfg.codex_bin == "/usr/local/bin/codex"
