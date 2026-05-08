# oum-worker

`oum-worker` is a small macOS CLI for managing Claude Code or Codex CLI sessions — both interactive sessions you drive yourself and headless workers driven by orchestrators.

It provides primitives to:

- spawn Claude Code or Codex now in `tmux` (with or without a starting prompt) or headless mode
- attach your terminal to a running interactive session
- schedule a session for later with `launchd`
- send follow-up messages to a live session
- wait until the session is idle (engine-aware: `end_turn` for Claude, `task_complete` for Codex)
- capture the latest assistant response from the engine's JSONL session files
- list, inspect, tail, and kill managed sessions

The CLI is intentionally not tied to any private task schema. Deployment-specific details such as repo aliases, logs directory, launchd label prefix, timezone, PATH, tmux session, and CLI binaries come from config.

## Install

From a checkout:

```bash
python3 -m pip install -e .
```

Runtime dependencies:

- macOS for `launchd` scheduling
- `tmux` for interactive workers
- Claude Code CLI available as `claude`, `cc`, or a configured path (for `--engine claude`, default)
- Codex CLI available as `codex` or a configured path (for `--engine codex`)
- Python 3.11+

## Quick Start

Create a local config:

```bash
cp configs/oum-worker.example.json .oum-worker.json
```

Edit paths for your machine, then run:

```bash
oum-worker --config .oum-worker.json spawn \
  --label demo \
  --new \
  --prompt "Say hello from a managed worker."
```

Ask a follow-up:

```bash
oum-worker --config .oum-worker.json ask \
  --label demo \
  "Now summarize that in five words."
```

Schedule a one-shot worker:

```bash
oum-worker --config .oum-worker.json schedule \
  --in 30m \
  --label later-demo \
  --new \
  --prompt "Run the nightly summary."
```

Or drop into a fresh interactive Claude Code session in tmux with no initial prompt:

```bash
oum-worker --config .oum-worker.json spawn  --label adhoc --new
oum-worker --config .oum-worker.json attach --label adhoc
```

Detach with `Ctrl-B D` and re-attach later with the same `attach` command.

Spawn a Codex CLI session (yolo on by default; `--no-yolo` to opt out):

```bash
oum-worker --config .oum-worker.json spawn  --label cx --new --engine codex
oum-worker --config .oum-worker.json attach --label cx
```

Inspect and clean up:

```bash
oum-worker --config .oum-worker.json list
oum-worker --config .oum-worker.json status --label demo
oum-worker --config .oum-worker.json logs --label demo --tail
oum-worker --config .oum-worker.json kill --label demo --purge
```

## Configuration

Config precedence is:

1. Generic public defaults
2. JSON file passed by `--config` or `OUM_WORKER_CONFIG`
3. Environment variables
4. Per-command flags such as `--logs-dir`, `--cwd`, `--cc-command`, and `--tmux-session`

Supported JSON keys:

```json
{
  "default_cwd": "/absolute/path/to/default/project",
  "logs_dir": ".logs/oum-worker",
  "tmux_session": "workers",
  "claude_bin": "claude",
  "timezone": "UTC",
  "launchd_label_prefix": "com.example.worker.",
  "path": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
  "repo_aliases": {
    "app": "/absolute/path/to/app"
  },
  "scripts_dir": "/absolute/path/to/source/scripts"
}
```

Environment overrides:

- `OUM_WORKER_CONFIG`
- `OUM_WORKER_DEFAULT_CWD`
- `OUM_WORKER_LOGS_DIR`
- `OUM_WORKER_TMUX_SESSION`
- `OUM_WORKER_CLAUDE_BIN`
- `OUM_WORKER_TIMEZONE`
- `OUM_WORKER_LAUNCHD_LABEL_PREFIX`
- `OUM_WORKER_PATH`
- `OUM_WORKER_REPO_ALIASES` as a JSON object
- `OUM_WORKER_SCRIPTS_DIR`

## Commands

```bash
oum-worker spawn    --label <label> [--engine claude|codex] (--new | --resume <session-id>) [--prompt TEXT | --prompt-file PATH] [--headless] [--yolo|--no-yolo] [--model M]
oum-worker schedule --label <label> [--engine claude|codex] (--in 30m | --at 18:00) (--new | --resume <session-id>) [--prompt TEXT | --prompt-file PATH] [--headless] [--yolo|--no-yolo] [--model M]
oum-worker attach   --label <label>
oum-worker send     --label <label> "message"
oum-worker capture  --label <label> [--full] [--include-thinking] [--include-tool-use]
oum-worker wait     --label <label> [--timeout 600] [--stable-ms 1500]
oum-worker ask      --label <label> "message"
oum-worker list     [--json]
oum-worker status   --label <label> [--json]
oum-worker logs     --label <label> [--tail] [--launchd]
oum-worker kill     --label <label> [--purge]
```

`--prompt` / `--prompt-file` is required for `--headless`. For interactive
spawn or schedule it is optional — omitting it opens the engine cold in
the tmux pane. `attach` requires a tty and refuses headless workers.

`--engine claude` is the default; `--engine codex` runs the Codex CLI.
For codex, `--yolo` is on by default (passes `--yolo`, an alias for
`--dangerously-bypass-approvals-and-sandbox`); pass `--no-yolo` if you
don't want it. Codex sessions read from `~/.codex/sessions/<YYYY>/<MM>/<DD>/`
for `capture` / `wait` / `ask`. A label is bound to one engine for its
lifetime; respawn with `--replace --engine <other>` to switch.

## Development

```bash
PYTHONPATH=scripts pytest tests/test_oum_worker_*.py -q
```

The project uses only the Python standard library at runtime.

## Security

Do not commit local configs containing private paths, credentials, API keys, MCP secrets, or production repository mappings. Keep those in untracked `.oum-worker.json` files or environment variables.

For a Once Upon Me deployment, use `configs/onceuponme.example.json` as a placeholder-only template and write the real paths into an untracked `.oum-worker.json`.

See `SECURITY.md` for vulnerability reporting.
