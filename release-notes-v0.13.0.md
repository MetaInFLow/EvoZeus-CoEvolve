# EvoZeus-CoEvolve v0.13.0

This release gives every upgraded target Skill a configurable, read-only EvoZeus Notice system while preserving the existing authorization boundaries between normal business work, feedback intake, maintenance, UAT, and Stable release.

## Included

- A target-local `notice-policy.json` with stable Emoji, tag, and state mappings for Skill, Lesson, Evolution, Maintenance, Advisory, Blocked, UAT, and Release events.
- A pure-standard-library `evozeus_notice.py` renderer with human-readable and JSON output and an explicit `writes=false` contract.
- Runtime identity rendering through the target Notice policy, including Skill release, Harness version, and development/UAT/Stable channel.
- A record-only Lesson flow shown after the current business correction; Issue submission and fix execution remain separately authorized.
- Bootstrap, migration, contract-bundle, structure, and doctor support for installing and validating the target Notice files.

## Runtime Contract

```text
🧙🏻‍♂️ `EvoZeus · 受管 Skill` [OWNER/REPO](https://github.com/OWNER/REPO) · Skill vX.Y.Z · Harness v0.13.0 · `渠道：开发版/UAT/正式版`

💡 `EvoZeus · Lesson` 待记录 · <reusable lesson>
```

Normal business output remains untagged. Rendering a Notice never creates an Issue, changes a Skill, creates a branch/worktree, enters UAT, or publishes Stable.

## Verification

- `python3 -m pytest -q` — 157 passed, 8 subtests passed.
- Python compile checks for the Notice CLI, lifecycle, preflight, dispatcher, and target hook scripts.
- Real target-Skill migration smoke added the Notice policy and CLI, passed structure validation, rendered development/UAT identity labels, produced a record-only Lesson Notice, and left no Notice bytecode cache.
- The single EvoZeus UAT product candidate completed local channel and Doctor validation before promotion.

## Upgrade

Run the authoritative dry-run before applying the managed-file upgrade:

```text
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.13.0 --dry-run --json
```
