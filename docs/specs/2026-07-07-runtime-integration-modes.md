# Runtime Integration Modes for Wrapped Skills

> Superseded for current behavior by `2026-07-20-global-dispatcher-and-skill-entry-preflight.md`. This file remains as the historical v0.5-v0.7 design record; project-local hook presence is no longer treated as native Skill-invocation coverage.

## Related Issue

- MetaInFLow/EvoZeus-CoEvolve#4

## Problem

EvoZeus-CoEvolve used the word `hook` for multiple mechanisms:

- Codex project-local hooks or host-native lifecycle hooks that run automatically;
- wrapper CLI commands such as `hook start-check`;
- prompt-level runtime checks written into `SKILL.md`.

That made wrapped Skills look hook-backed even when no host hook was installed.

## Decision

Record the actual integration level in `.evozeus_evoinfra/wrapper.json` and expose it in diagnosis and harness upgrade plans.

Supported modes:

- `native_host_hook`: Codex project-local hooks are registered in `.codex/hooks.json` with the wrapper adapter, or other host/plugin lifecycle hook evidence is present.
- `bootstrap_skill`: plugin skill infrastructure is present, but no host hook files are detected.
- `prompt_runtime_check`: a root instruction surface can ask the agent to run checks; this is prompt-compliance fallback.
- `manual_only`: no runtime instruction surface or host integration is detected.

Wrapper CLI commands are not runtime hooks unless a host integration calls them.

## Implementation Plan

- Add `classify_integration_mode` to lifecycle diagnosis.
- Include `integration` in target architecture, target diagnosis, wrapper manifests, and harness upgrade plans.
- Add preflight validation that fails if a manifest claims `native_host_hook` without Codex project-local hook files or other host/plugin lifecycle hook evidence.
- Update docs and templates to say `integration.mode` instead of treating all checks as hooks.

## Verification Plan

- Unit tests cover single Skill, hook/plugin bundle, Codex project-local hooks, and Codex plugin with `hooks: {}`.
- Run `python3 -m unittest tests/test_evozeus_wrapper_lifecycle.py`.
- Run `scripts/evozeus_wrapper.py skill diagnose` against representative targets and inspect `integration.mode`.

## Release Plan

This is a wrapper harness behavior change. The next wrapper release should mention that hook semantics are now explicit, verifiable, and aligned with Codex project-local hooks.
