# Security Policy

## Reporting

Please report vulnerabilities privately by emailing `contact@onceuponme.in`.

Include:

- affected version or commit
- reproduction steps
- expected impact
- any relevant logs with secrets removed

Please do not open a public issue for a vulnerability before it has been triaged.

## Secret Handling

`oum-worker` config files can contain private local paths, repo aliases, and command paths. They should not contain API keys or service credentials.

Before publishing a fork or extracted repo:

- keep `.oum-worker.json` untracked
- scrub `.logs/`
- scrub Claude Code JSONL transcripts
- enable GitHub secret scanning and push protection
- review `.mcp.json`, shell profiles, and launchd plists before committing
