---
name: evozeus-wrapper
description: Use when EvoZeus routes an existing SKILL.md folder, AGENTS.md runtime kit, or hook/plugin-controlled Skill bundle into the EvoZeus-CoEvolve attachment and lifecycle harness.
---

# EvoZeus-CoEvolve

This root Skill is only the wrapper entrypoint. It keeps the stable `evozeus-wrapper` compatibility slug and serves as the thin EvoZeus-CoEvolve attachment entrypoint.

When this Skill applies, immediately use `skills/using-evozeus-wrapper/SKILL.md` for the operating workflow. Do not duplicate lifecycle details here.

## Role

EvoZeus-CoEvolve is the paper-system and artifact entry under EvoZeus. This repository directly wraps an existing local Skill folder, `AGENTS.md` runtime kit, or hook/plugin-controlled Skill bundle with the attachment and governed lifecycle substrate used by the larger Collaborative Evolution design.

The wrapper exists to add:

- canonical source tracking
- GitHub repo / Pages dashboard
- feedback Issues
- design docs
- changelog and release governance
- preflight checks
- target-local configurable EvoZeus Notice policy and read-only renderer
- wrapper harness migration records

It must not rewrite target business rules or absorb the evolution runtime, scanner, or prompt management platform.

## Required Flow

Use these Skills in order:

1. `skills/using-evozeus-wrapper/SKILL.md` - full operating workflow and routing.
2. `skills/environment-diagnosis/SKILL.md` - local EvoZeus and tool readiness.
3. `skills/target-skill-diagnosis/SKILL.md` - target repo facts, GitHub access, architecture, candidates, gaps.
4. `skills/evolution-surface-diagnosis/SKILL.md` - whole-repo instruction surface decision.
5. `skills/status-assessment/SKILL.md` - user-understandable assessment and next step.
6. `skills/target-skill-transform/SKILL.md` - bootstrap / adopt / repair / verify planning.
7. `skills/publish-reinstall/SKILL.md` - canonical repo and runtime pointer handling.
8. `skills/evolution-loop/SKILL.md` - feedback-to-release loop.
9. `skills/harness-upgrade/SKILL.md` - wrapper harness version migrations.

## Hard Boundaries

- If `~/.evozeus` is missing, stop and install / initialize EvoZeus before target transform.
- If visibility is missing, ask `public` or `private` before creating or pushing anything.
- If an existing repo has no GitHub release and no `.evozeus-wrapper/CHANGELOG.md` version entry, ask the owner to choose the current Skill / kit version.
- If no controlling instruction surface can be proven, run evolution surface diagnosis and ask the owner when needed.
- Keep wrapper-owned additions append-only and do not rewrite target business logic.
