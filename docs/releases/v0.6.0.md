# EvoZeus-CoEvolve v0.6.0

## Summary

This release separates global EvoZeus installation state from target repo-local wrapper harness state.

Global state remains under `~/.evozeus`. Wrapped target repos now use `.evozeus_evoinfra/` for their wrapper manifest, feedback policy, and audit rule.

## Changes

- Added target infra constants and new public manifest path `.evozeus_evoinfra/wrapper.json`.
- Added legacy `.evozeus/wrapper.json` fallback and conflict detection.
- Implemented `harness upgrade` writes for legacy directory migration.
- Added `loop audit` for feedback capture decisions and Issue draft generation.
- Routed wrapper feedback audit issues to `MetaInFLow/EvoZeus-CoEvolve`.
- Hardened release gating: local changelog versions ahead of GitHub latest release now fail unless `--no-release-needed` is explicit.
- Updated templates, docs, preflight, bootstrap, and tests.

## Verification

- `python3 -m unittest tests/test_evozeus_wrapper_lifecycle.py`
- `python3 scripts/evozeus_wrapper.py harness upgrade-check --target /Users/anthonyf/.codex/skills/daxing-phase2-project-management --latest-version v0.6.0 --json`
- `python3 scripts/evozeus_wrapper.py loop audit --target /Users/anthonyf/.codex/skills/daxing-phase2-project-management --user-input "这个 wrapper 没有自动 issue 回收，有问题" --json`

## Rollback

Restore the previous v0.5.0 release if target migration tooling fails. Already migrated target repos can be restored by moving `.evozeus_evoinfra/` back to `.evozeus/` and resetting `wrapper_version` to `v0.5.0`.
