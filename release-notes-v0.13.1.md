# EvoZeus-CoEvolve v0.13.1

## Added

- Added a user-level `UserPromptSubmit` Lesson watcher. Ordinary Chat corrections can now surface a record-only `EvoZeus · Lesson` candidate without explicitly invoking a target Skill.
- Added `global_prompt_lesson_watcher` to the target Skill capability contract and live diagnosis overlay.

## Fixed

- Kept feedback-audit JSON, signal ids, capture states, routing fields, Issue drafts, and Hook diagnostics out of normal user-facing Chat.
- Preserved neutral prompts as zero-output watcher events and made registry failures fail open.
- Upgraded SessionStart-only legacy installations by structurally adding one `UserPromptSubmit` handler while preserving unrelated hooks.

## Authorization boundary

- Automatic detection creates no Issue and performs no external write.
- Recording a Lesson requires explicit confirmation.
- Starting a fix remains a later, separate authorization.

## Verification

- `python3 -m pytest -q` (`177 passed`, `25 subtests passed`).
- Python compile gate passed for the CLI, bootstrap, global hook lifecycle, target lifecycle, preflight, global dispatcher, target hook, and Lesson watcher tests.
- Fresh isolated HOME smoke installed both Hook events, reported Harness `v0.13.1`, injected a natural-language record-only Lesson instruction for a correction, and returned only `{"continue": true}` for a neutral prompt.
- `git diff --check` passed.

## Upgrade note

Run `hook global install --approve --json` from the v0.13.1 source, review the changed Hook definition with Codex `/hooks`, and record trust separately. Stable v0.13.0 remains unchanged until explicit release authorization.
