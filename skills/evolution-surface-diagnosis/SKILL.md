---
name: evozeus-wrapper-evolution-surface-diagnosis
description: Use after target repo diagnosis when deciding where EvoZeus-CoEvolve self-evolution instructions should be placed. Applies to root SKILL.md repos, AGENTS.md runtime kits, multi-Skill bundles, and hook/plugin-controlled Skill systems. Use this Skill to inspect the whole repo, interpret script-produced facts, choose the controlling instruction surface, and explain the evidence before transform or preflight.
---

# Evolution Surface Diagnosis

Use this Skill after `python3 scripts/evozeus_wrapper.py skill diagnose --json` and before any transform, repair, harness upgrade, or structure preflight that needs an instruction surface.

The CLI scripts collect facts. This Skill makes the judgment. Do not treat `evolution_surface.candidates` as final placement.

## Required Inputs

- Target repo path.
- Target diagnosis JSON from:

```bash
python3 scripts/evozeus_wrapper.py skill diagnose \
  --target /absolute/path/to/repo \
  --repo OWNER/REPO \
  --json
```

If the JSON is missing `skill.root_files`, `skill.top_level_dirs`, `skill.plugin_manifests`, `skill.hook_files`, `skill.skill_inventory`, or `skill.evolution_surface.candidates`, rerun diagnosis first.

## Repo Browsing Requirement

Before deciding, inspect the repo broadly enough to understand how agents are actually controlled:

```bash
rg --files /absolute/path/to/repo
```

Then read only the relevant control surfaces:

- Root instruction files: `AGENTS.md`, `SKILL.md`, `CLAUDE.md`, `OPENCLAW.md`, `HERMES.md`, or equivalent root agent files when present.
- Plugin manifests reported by diagnosis, such as `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`, `.kimi-plugin/plugin.json`, `gemini-extension.json`, `package.json`, or `.opencode/INSTALL.md`.
- Hook files reported by diagnosis, especially session-start, startup, install, or runtime hook files.
- Candidate `skills/*/SKILL.md` files referenced by hooks/manifests or named as routing, bootstrap, session, runtime, index, or orchestrator surfaces.
- README / architecture docs only when they clarify the runtime control chain.

Do not scan every business Skill body when controller files already prove the control path. Do scan additional Skill bodies when hooks/manifests are vague or multiple candidates look plausible.

## Decision Procedure

1. Separate facts from inference:
   - Facts come from files and diagnosis JSON.
   - Inference is your conclusion about which file controls agent behavior.
2. Build the control chain:
   - What file is loaded first by the agent/runtime?
   - Does a root instruction file delegate to another Skill?
   - Do hooks or plugin manifests load a specific Skill at session start?
   - Does that loaded Skill route or constrain other Skills?
3. Choose the instruction surface:
   - If root `SKILL.md` is the direct Skill instruction, choose `SKILL.md`.
   - If root `AGENTS.md` controls a runtime kit or repo-level agent behavior, choose `AGENTS.md`.
   - If hooks/plugin manifests load a control Skill and that Skill governs session behavior, choose that `skills/<name>/SKILL.md`.
   - If multiple surfaces control different runtimes, choose the common upstream surface. If there is no common upstream surface, stop and ask the owner to choose the runtime boundary.
   - If no surface is proven, stop and ask the owner to identify the controlling instruction surface.
4. Check whether the chosen surface contains exactly one compact activation block linking to `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`.
5. Check whether the manifest records the same canonical path, `harness_skill_version`, and wrapper-managed identity.
6. State component impact: which canonical Harness Skill or entry contract gap needs repair.

## Evidence Rules

- Do not choose a hook-loaded Skill only because its name sounds like a control Skill. A hook, plugin manifest, root instruction, or runtime doc must connect it to session behavior.
- Do not choose root `SKILL.md` just to satisfy wrapper convention.
- Do not choose root `AGENTS.md` if a hook-loaded control Skill is the actual first loaded surface.
- Do not rely on examples from another repo. Use only this target repo's files.
- If the script's candidate list conflicts with repo evidence, prefer repo evidence and say why.

## Output Shape

Use concise Chinese, with concrete file paths.

```text
自进化说明位置判断：<一句话结论>

选择结果：
- instruction_surface: <relative path, or NEEDS_OWNER_CHOICE>
- confidence: <high|medium|low>
- target_kind: <diagnosis target_kind>

控制链证据：
- <fact: file path + what it proves>

排除项：
- <candidate path>: <why it is not the controlling surface>

缺失项更新：
- <chosen path> canonical Harness Skill activation block
- .evozeus-wrapper/skills/using-evozeus-harness/SKILL.md contract
- <other relevant wrapper gaps>

下一步命令：
python3 scripts/evozeus_wrapper.py skill transform --mode <attach|adopt|repair|verify> \
  --target /absolute/path/to/repo \
  --repo OWNER/REPO \
  --instruction-surface <relative path> \
  --visibility <public|private> \
  --dry-run \
  --json
```

For `verify`, use preflight only after `.evozeus-wrapper/wrapper.json` records the chosen `instruction_surface` or the repo has a root `SKILL.md` / `AGENTS.md`.

## Stop Conditions

- Target repo files are unavailable.
- GitHub permission or source contract diagnosis is blocked.
- No controlling instruction surface can be proven.
- Multiple independent runtimes exist and no common upstream instruction surface exists.
