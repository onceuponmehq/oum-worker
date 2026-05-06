# Contributing

Thanks for improving `oum-worker`.

## Development Setup

```bash
python3 -m pip install -e .
PYTHONPATH=scripts pytest tests/test_oum_worker_*.py -q
```

Runtime code should stay Python standard-library only unless a dependency removes substantial complexity.

## Design Rules

- Keep the CLI independent of any company-specific task schema.
- Put deployment-specific values in config, not code.
- Prefer small modules with one responsibility.
- Add or update tests for behavior changes.
- Do not commit logs, local configs, JSONL transcripts, credentials, or private repo aliases.

## Pull Requests

Before opening a PR:

```bash
PYTHONPATH=scripts pytest tests/test_oum_worker_*.py -q
```

Describe the behavior change, config impact, and any macOS/tmux/launchd assumptions.
