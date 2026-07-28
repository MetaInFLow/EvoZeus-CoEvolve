---
name: evozeus-wrapper-target-skill-diagnosis
description: Use when identifying the target Skill, AGENTS.md-root runtime kit, or hook/plugin-controlled Skill bundle; local installs; candidate repos; GitHub repo state; harness state; architecture; evolution surface; component gaps; and publication boundary.
---

# Target Skill Diagnosis

Use this stage after environment diagnosis and before bootstrap, adopt, repair, publish, or reinstall.

## Required Inputs

- Target folder. It may be a single Skill with root `SKILL.md`, a runtime kit with root `AGENTS.md` and `skills/*/SKILL.md`, or a hook/plugin-controlled bundle where a startup hook loads a control Skill.
- Target GitHub repo in `OWNER/REPO` format when known.
- Skill name override only when the target surface cannot identify it.

## Command

```bash
python3 scripts/evozeus_wrapper.py skill diagnose \
  --target /absolute/path/to/skill \
  --repo OWNER/REPO \
  --json
```

## Decide

Ask the user only when:

- The target path is missing or has no detectable evolution surface.
- Multiple local repos can be the canonical source.
- Multiple install copies differ from the canonical repo.
- Visibility is not explicitly `public` or `private`.
- Private data could enter docs, Issues, release notes, or Pages.

## Required Order

1. Confirm environment diagnosis has passed. If `~/.evozeus` is missing, install / initialize EvoZeus before target transform.
2. Check GitHub repo access, visibility, default branch, and current account permission.
3. Classify the target architecture:
   - `single_skill`
   - `runtime_skill_bundle`
   - `hooked_skill_bundle`
   - `skill_bundle`
   - `agents_runtime`
   - `unknown`
4. Report runtime integration mode and each scoped capability from `skill.integration`. Distinguish project maintenance, user-level session dispatch, user-level prompt Lesson watching, Skill-entry preflight, tool gateway, and future Skill invocation hooks. Do not call project hooks, prompt watchers, or wrapper CLI commands native per-Skill invocation hooks.
5. Report Skill inventory from `skills/*/SKILL.md` when present.
6. Report `evolution_surface` facts: candidate instruction surfaces, controller files, and evidence boundaries. Do not treat script candidates as final placement.
7. Use `skills/evolution-surface-diagnosis/SKILL.md` to browse the whole repo and choose the controlling instruction surface.
8. Report `component_gaps`: missing wrapper files, manifest, changelog, and status-check concept after the surface decision is known.
9. Hand the diagnosis JSON and surface decision to `skills/status-assessment/SKILL.md`; do not write user-facing status analysis in the script.

## Stop Conditions

- Target Skill identity is ambiguous.
- The user has not chosen visibility.
- Sensitive data cannot be safely redacted.
