---
name: evozeus-wrapper-harness-upgrade
description: Use when inspecting, planning, approving, applying, verifying, or rolling back the versioned Harness Skill migration of an independent target Repo.
---

# Harness Upgrade

Use this Skill for target Harness maintenance. The target boundary is one independent Git Repo root. The canonical target-local Skill remains `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`; its current contract version is `v1.1.0`.

## Required lifecycle

Run every change through this sequence:

1. **Inspect**：resolve the independent Repo root, read `.evozeus-wrapper/wrapper.json`, inspect the compact activation marker, and collect historical headings/signatures as read-only discovery candidates.
2. **Plan**：load `contracts/v1/migrations/harness-migration-contract-v1.json`; use the trusted-base verifier to validate the stable protocol, both current pointers, the selected external official profile, and its hash-bound from/to closures before deriving any automatic write authority; emit the protocol, profile, adapter identity, exact from/to state, write/delete/move sets, protected business surfaces, source-release attestation, validation, rollback, and `plan_sha256`.
3. **Approve**：show the complete plan to the user and obtain approval for that exact `sha256:<digest>`. GitHub `ADMIN` authority does not approve a plan.
4. **Apply**：recompute the plan, require `--approve-plan` to match, verify every preimage and the official immutable source release, create a receipt-bound snapshot outside the target Repo, stage all postimages, then write only the approved set.
5. **Verify**：check every postimage, preserve marker-external business bytes exactly, and run target structure validation.
6. **Rollback**：on any apply or verification failure, validate the whole snapshot and receipt before the first restore mutation. Manual rollback requires `--approve`.

## Commands

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-check \
  --target /absolute/path/to/skill --json

python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/skill \
  --latest-version v0.15.0 \
  --dry-run --json

python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/skill \
  --latest-version v0.15.0 \
  --approve-plan 'sha256:<exact-plan-digest>' --json

python3 scripts/evozeus_wrapper.py harness rollback-migration \
  --target /absolute/path/to/skill \
  --snapshot /absolute/path/to/trusted-snapshot \
  --approve --json

python3 scripts/evozeus_wrapper.py harness upgrade-all \
  --latest-version v0.15.0 --dry-run --json

python3 scripts/evozeus_wrapper.py harness upgrade-all \
  --latest-version v0.15.0 --approve \
  --approve-plan 'sha256:<exact-batch-plan-digest>' --json
```

Batch apply requires both `--approve` and the exact dry-run `batch_plan_sha256` through `--approve-plan`. The approved batch retains each target's exact `plan_sha256` and passes that digest into the per-target apply call. If any target is replanned to a different digest, stop the batch before that target write and restore earlier targets from their validated snapshots.

## Versioned authority profiles

- `legacy-scattered-to-canonical-v1.0@v1.0.0`：manual review, `writes=false`. Regex, frontmatter, heading, terminal text, path names, and inferred layout remain discovery-only evidence for every protocol version.
- `canonical-v1.0-to-v1.1@v1.0.0`：automatic planning is available only after the trusted-base verifier resolves the hash-bound external official profile and immutable v1.0/v1.1 closures, and manifest identity, stable block identity, exact frozen Harness Skill preimage, exact preflight preimage, and adapter digest all match.
- `prerelease-ambiguous-to-manual-review@v1.0.0`：a v1.1 Harness without the exact migration contract and managed-block receipts remains manual, `writes=false`.
- `unknown-to-manual-review@v1.0.0`：manual review, `writes=false`.

Destructive authority comes exclusively from the verified external official profile, its immutable closure diff, adapter identity, and exact preimage hashes. Frontmatter and regex findings never grant write authority. A missing field, duplicate marker, unknown layout, extra legacy candidate, dirty target, changed preimage, changed protected surface, untrusted source, or unapproved plan digest blocks all writes.

## Source and snapshot trust

- Release identity comes from the fixed `MetaInFLow/EvoZeus-CoEvolve` GitHub API tag attestation. The local tag ref object, peeled commit, and clean `HEAD` must match that attestation; configured Git remotes and `origin` names supply diagnostics only and grant no trust.
- The tagged contract manifest must bind the contract digest. Every source file used as a postimage must equal both its declared digest and its bytes in that tag.
- An unreleased branch may inspect and plan. Its apply status remains `source_unreleased`, `writes=false`.
- The snapshot base must stay outside the target Repo. Reject symlinks in the base, transaction directory, descriptor, files tree, and backup paths.
- Rollback accepts only a direct child of the trusted snapshot root with the expected schema, transaction identity, plan digest, descriptor digest receipt, backup-set digest, complete metadata, and valid backup hashes.

## Protected target surfaces

- The compact activation block uses the stable `evozeus-harness-entry` marker and manifest-bound relative link.
- Fresh attach may add the block only when the surface has zero canonical markers and zero historical candidates.
- Historical candidates never authorize deletion, relocation, or replacement. Route them to a reviewed adapter/profile backlog.
- The canonical v1.0→v1.1 profile does not write the instruction surface. Its full-file preimage hash is a compare-and-swap protected surface and its bytes must remain identical after apply.
- Pre-existing unknown files at managed destinations are preserved and block bootstrap/repair, including `--force`, unless an exact trusted preimage or manifest receipt proves ownership.

## Stop conditions

Stop with `writes=false` when any condition below holds:

- `.evozeus-wrapper/wrapper.json` is missing, ambiguous, incomplete, or conflicts with a legacy manifest.
- The plan lacks an automatic profile or any independent protocol/profile/adapter identity.
- The source release, remote tag commit, contract, adapter, frozen preimage, or postimage cannot be verified exactly.
- The target Git status is unavailable or dirty.
- A write path escapes the target, traverses a symlink, has an unsafe parent type, or changed after planning.
- `--approve-plan` is absent or differs from the current `plan_sha256`.
- Snapshot creation or complete pre-rollback validation fails.

Read-only diagnosis can continue after a stop. Any repair requires a new versioned profile or an explicitly reviewed manual procedure.
