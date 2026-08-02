---
name: evozeus-wrapper-target-skill-transform
description: Use when attaching, adopting, repairing, or verifying the root Harness of an independent Skillware Git repository after diagnosis.
---

# Target Skill Transform

Use this stage only after target Skill diagnosis has identified the canonical repo, target architecture, GitHub permission, evolution surface facts, component gaps, and harness state; `skills/evolution-surface-diagnosis/SKILL.md` has selected the instruction surface; and `skills/status-assessment/SKILL.md` has explained the result and cleared blockers.

## Modes

- `attach`: GitHub Repo exists and its root Harness is missing.
- `adopt`: compatibility alias for attaching to an existing Repo.
- `bootstrap`: deprecated compatibility alias; it cannot create a Harness before the Repo exists.
- `repair`: harness is partial.
- `verify`: harness is complete or needs structure verification.

If diagnosis returns `migration_required`, run `harness migrate-layout` before entering any transform mode.

## Commands

```bash
python3 scripts/evozeus_wrapper.py skill transform \
  --mode attach \
  --target /absolute/path/to/skill \
  --repo OWNER/REPO \
  --instruction-surface <relative path> \
  --visibility private \
  --dry-run \
  --json

python3 scripts/evozeus_wrapper.py skill transform --mode verify --target /absolute/path/to/skill
```

## Rules

- Do not change target Skill business rules.
- Only the independent Git Repo root may receive `.evozeus-wrapper/`; child paths inherit it.
- Require a matching GitHub origin and verified `ADMIN` permission before any Harness write.
- Single Skill targets use root `SKILL.md`; runtime kits often use root `AGENTS.md`. Both use the same compact canonical Harness Skill activation block before their main flow.
- Hook/plugin-controlled Skill bundles use the instruction surface selected by `skills/evolution-surface-diagnosis/SKILL.md`, for example `skills/<control-skill>/SKILL.md`.
- Do not create a fake root `SKILL.md`.
- Write the complete shared contract once at `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`; keep the instruction-surface activation block at eight lines or fewer.
- Inject `.evozeus-wrapper/scripts/evozeus_branch_consumer.py`, the pinned Core planner, contract, and provenance; record their managed paths under manifest `contributor_branch`.
- Validate the offline snapshot before reporting attach/repair success. Runtime planning always collects live permission evidence and does not download contract code.
- Record the canonical path, independent Harness Skill version, and wrapper-managed identity in `.evozeus-wrapper/wrapper.json`.
- Legacy migration removes only the old status, self-evolution, wrapper, and refresh-note sections that carry wrapper heading, ownership, and terminal signatures. Preserve every target-owned section and its newline bytes; route missing terminal signatures to approved repair.
- Add `.evozeus-wrapper/docs/migrations/README.md` so future wrapper harness upgrades have a migration ledger.
- Do not overwrite existing files without explicit user confirmation.
- Keep `.evozeus-wrapper/wrapper.json` as the only operational harness manifest.

## Stop Conditions

- Managed files conflict with user edits and no merge decision exists.
- Visibility or data boundary is unresolved.
- Target is outside Git, target Repo does not exist remotely, or a nested Harness is present.
- Current GitHub account is not an administrator of the target Repo.
