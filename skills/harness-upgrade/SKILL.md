---
name: evozeus-wrapper-harness-upgrade
description: Use when checking or upgrading the wrapper harness version embedded in a target Skill repo.
---

# Harness Upgrade

Use this stage to keep target Skill infrastructure aligned with `MetaInFLow/EvoZeus-CoEvolve`.

## Commands

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-check --target /absolute/path/to/skill --json
python3 scripts/evozeus_wrapper.py harness migrate-layout --target /absolute/path/to/skill --latest-version v0.14.0 --dry-run --json
python3 scripts/evozeus_wrapper.py harness migrate-layout --target /absolute/path/to/skill --latest-version v0.14.0 --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.14.0 --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.14.0 --approve --json
```

## Rules

- Skill release version and wrapper harness version are separate axes.
- Resolve every target to its independent Git Repo root. Skill, package, pack, and app directories inherit that root Harness.
- Reject plain folders and any active Harness manifest below the Repo root.
- Read-only checks and dry-run plans do not require administrator authority. Every Harness write, upgrade, or upload requires verified GitHub `ADMIN` permission.
- `upgrade-check` resolves GitHub latest release by default. It may report `latest_unknown`, but must never self-substitute the installed version.
- Only update harness-managed files.
- Do not touch target Skill business rules.
- Preserve Codex project-local hook registration at `.codex/hooks.json`; keep its adapter under `.evozeus-wrapper/hooks/` and label it `repo_maintenance_hook` with `canonical_repository` scope.
- Prevalidate `.codex/hooks.json` as structured JSON, preserve unrelated hooks, and create or refresh exactly one wrapper SessionStart registration.
- Keep the full invocation, maintenance, authorization, UAT, Release, and rollback contract in `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`.
- Keep exactly one compact activation block of at most eight lines in the diagnosed instruction surface. Its Markdown label and relative link must both equal the manifest `harness_skill_path`.
- Record `harness_skill_path`, `harness_skill_version`, and `harness_skill_managed=true` in `.evozeus-wrapper/wrapper.json`.
- Record every wrapper migration under `.evozeus-wrapper/docs/migrations/` with from/to wrapper version, file moves, validation, and rollback.
- Update `.evozeus-wrapper/wrapper.json` to `layout_version=2` only after all destination conflicts are cleared.
- Add the onboarding guide and default onboarding contract during legacy layout migration; do not leave migrated manifests structurally incomplete.
- During legacy migration, remove only sections with a proven wrapper ownership signature and preserve all target-owned business bytes. Stop appending migration notes to the instruction surface.
- Refresh the canonical Harness Skill, compact activation block, and manifest integration; require structure post-validation before reporting success.
- Keep workflow validation active independently of optional Pages deployment; Pages requires `EVOZEUS_PAGES_ENABLED=true`.
- Old `.evozeus_evoinfra/` and `.evozeus/wrapper.json` paths are migration inputs, not runtime fallbacks.
- Major wrapper upgrades require explicit user confirmation.
- `upgrade-all` must prevalidate every registered target before the first write and restore every target snapshot if any apply step fails.
- Resolve authority before deciding targets are current. The requested latest version must match the dispatcher cache, environment override, or GitHub latest release. Every target must have a verifiable clean Git worktree, writable write-set files and parents, and no symlink in any write path.
- Snapshot every file the migration may rewrite, move, refresh, or delete. Legacy wrapper path references in target-owned files are part of this explicit write set even though business semantics remain unchanged.
- Apply the same repository-boundary rule to direct `migrate-layout`: reject absolute paths, `..` traversal, symlinked write paths, and manifest-selected instruction surfaces outside the target.
- A target harness manifest declares global capability ownership and scope, but live user-level dispatcher installation/trust comes from `hook global status` or the diagnosis overlay.
- The global dispatcher is a native `SessionStart` aggregate gate, not a native per-Skill invocation hook.

## Stop Conditions

- `.evozeus-wrapper/wrapper.json` is missing and no legacy layout can be migrated or the user has not approved repair.
- A migration destination differs from its legacy source.
- Managed files, including `.codex/hooks.json` or `.evozeus-wrapper/hooks/evozeus_wrapper_start_check.py`, have local edits and no merge strategy exists.
