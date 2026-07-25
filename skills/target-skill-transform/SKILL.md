---
name: evozeus-wrapper-target-skill-transform
description: Use when bootstrapping, adopting, repairing, or verifying a target Skill harness after diagnosis.
---

# Target Skill Transform

Use this stage only after target Skill diagnosis has identified the canonical repo, target architecture, GitHub permission, evolution surface facts, component gaps, and harness state; `skills/evolution-surface-diagnosis/SKILL.md` has selected the instruction surface; and `skills/status-assessment/SKILL.md` has explained the result and cleared blockers.

## Modes

- `bootstrap`: GitHub repo does not exist and harness is missing.
- `adopt`: GitHub repo exists and harness is missing.
- `repair`: harness is partial.
- `verify`: harness is complete or needs structure verification.

If diagnosis returns `migration_required`, run `harness migrate-layout` before entering any transform mode.

## Commands

```bash
python3 scripts/evozeus_wrapper.py skill transform \
  --mode bootstrap \
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
- Single Skill targets use root `SKILL.md`; `SKILL.md` must put `EvoZeus-CoEvolve 状态检查` immediately after frontmatter so version/source checks run before the main chain.
- Runtime kit targets often use root `AGENTS.md`; `AGENTS.md` must put `EvoZeus-CoEvolve 状态检查` before the main runtime instructions.
- Hook/plugin-controlled Skill bundles use the instruction surface selected by `skills/evolution-surface-diagnosis/SKILL.md`, for example `skills/<control-skill>/SKILL.md`.
- Do not create a fake root `SKILL.md`.
- Other instruction-surface changes are append-only: add the self-evolution method and `EvoZeus-CoEvolve` section if missing.
- Add `.evozeus-wrapper/docs/migrations/README.md` so future wrapper harness upgrades have a migration ledger.
- Do not overwrite existing files without explicit user confirmation.
- Keep `.evozeus-wrapper/wrapper.json` as the only operational harness manifest.

## Stop Conditions

- Managed files conflict with user edits and no merge decision exists.
- Visibility or data boundary is unresolved.
