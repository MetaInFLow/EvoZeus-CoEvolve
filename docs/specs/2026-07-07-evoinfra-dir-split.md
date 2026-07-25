# EvoInfra Directory Split

> 历史说明：本方案已由 `2026-07-18-consolidated-target-layout.md` 取代。`.evozeus_evoinfra/` 现在只作为 layout v1 迁移输入，不是可继续运行的兼容路径。

## Related Issue

- Related issue: https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/6

## Problem

`~/.evozeus` is the global EvoZeus install and project pointer home. Reusing `.evozeus` inside every wrapped target repo made it ambiguous whether a path referred to global installation state or target-local harness state.

## Decision

- Keep global installation state under `~/.evozeus`.
- Move target repo-local wrapper harness state to `<repo>/.evozeus_evoinfra`.
- Treat `<repo>/.evozeus` as legacy fallback only.

## Implementation

- New wrapper manifest path: `.evozeus_evoinfra/wrapper.json`.
- New feedback policy path: `.evozeus_evoinfra/feedback-policy.json`.
- New audit rule path: `.evozeus_evoinfra/audit-rule.md`.
- Legacy manifests are read when the new path is absent.
- New and legacy manifests conflict when both exist with different JSON content.
- `harness upgrade` migrates legacy files to the new directory.

## Verification

- Unit tests cover new path writes, legacy fallback, conflict detection, and migration.
- CLI `upgrade-check` reports new path fields.
- Daxing Skill was migrated as the first target validation.
