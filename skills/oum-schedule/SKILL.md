---
name: oum-schedule
description: Deprecated. Forwards to oum-worker. Use this skill only if a caller specifically references `oum-schedule`.
---

# oum-schedule (deprecated)

This skill has been superseded by `oum-worker`. The `scripts/oum-schedule` CLI still works as a backwards-compat alias and prints a deprecation hint to stderr, but new agent and human use should call `scripts/oum-worker schedule` directly.

For the full set of worker primitives — spawn, send, capture, wait, ask, list, status, kill, logs — see `skills/oum-worker/SKILL.md`.

If a request would have triggered this skill, invoke `oum-worker` instead.
