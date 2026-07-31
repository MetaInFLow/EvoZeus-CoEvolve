# EvoZeus-CoEvolve v0.11.2

## Plugin-first Skillware compatibility

This patch release enables the paper feasibility target, Engineering Everything, to preserve its plugin-first installation model while using the CoEvolve source contract.

## Fixed

- Runtime symlinks may point to direct `skills/<name>/SKILL.md` entries inside the canonical Skillware repository when those links are recorded in the wrapper manifest.
- Diagnosis and doctor checks use manifest-managed install links for multi-Skill bundles.
- Legacy layout migration seeds missing feedback and audit policies from the public CoEvolve templates before structure post-validation.

## Boundaries

- Child Skill pointers must remain direct entries under the canonical repository's `skills/` directory and contain `SKILL.md`.
- The change does not claim native per-Skill invocation hooks.
- Target business instructions remain untouched by the source-contract compatibility fix.

## Verification

- `python3 -m pytest -q` — 126 passed.
- Python compilation checks passed for all CoEvolve entrypoints.
- Engineering Everything structure validation passed after policy recovery.
