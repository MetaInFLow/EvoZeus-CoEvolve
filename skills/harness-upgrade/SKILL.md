---
name: evozeus-wrapper-harness-upgrade
description: Use when inspecting, planning, approving, applying, verifying, or rolling back the versioned Harness Skill migration of an independent target Repo.
---

# Harness Upgrade

Use this Skill for target Harness maintenance. The target boundary is one independent Git Repo root. The canonical target-local Skill remains `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`; its current contract version is `v1.1.0`.

## Required lifecycle

Run every change through this sequence:

1. **Inspect**：resolve the independent Repo root, read `.evozeus-wrapper/wrapper.json`, inspect the compact activation marker, and collect historical headings/signatures as read-only discovery candidates.
2. **Plan**：load `contracts/v1/migrations/harness-migration-contract-v1.json`; use the trusted-base verifier to validate the stable protocol, both current pointers, every active direct-to-current external profile, and each hash-bound from/to closure. Select exactly one profile only after the target matches its complete immutable authority envelope. Automatic canonical profiles require the whole from closure. The reviewed v0.14 legacy profile additionally requires the frozen source envelope, hash-bound CommonMark adapter and parser lock, one unambiguous three-section AST projection, and an exact full-file complement proof. Emit the protocol, profile, adapter identity, exact from/to state, write/delete/move sets, protected business surfaces, source-release attestation, validation, rollback, and digest. A supervised plan also emits `decision=supervised_migration_available`, `operation_sha256`, `write_authorization.class=supervised_exact_plan_v1`, public `release_lineage_records`, and the actual path-specific `migration_records/current_migration_record`.
3. **Approve**：show the complete plan to the user. Automatic migration approves the exact `plan_sha256`; supervised legacy migration approves the exact `operation_sha256`. GitHub `ADMIN` authority does not approve either digest.
4. **Apply**：recompute the plan inside the same CLI invocation, require `--approve-plan` to match the applicable digest, reverify target root, source/tag, both Git indexes, full preimages, inode/mode CAS, verifier profile, adapter assets, and closure, create a receipt-bound snapshot outside the target Repo, stage every postimage, then write only the approved set through the secure mutation batch.
5. **Verify**：check every staged hash/mode and current-closure postimage. For the supervised `SKILL.md` transform, also prove the retained business complement byte-for-byte, prove every retired legacy span absent, require exactly one canonical activation block by CommonMark AST, and verify manifest, public release lineage, and profile-bound applied lineage. Run structure only from the trusted-release closure preflight plus its exact notice dependency; never execute target-owned Python during migration verification. Recheck the complete target postcondition after structure returns.
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
  --approve-plan 'sha256:<operation_sha256-or-plan_sha256>' --json

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

`one_time` means one explicit CLI invocation. Approval is never saved to a config file, manifest, snapshot preference, or reusable authorization store. A successful apply changes the preimage, so replaying its old digest against that applied state is rejected. If the owner explicitly rolls back and later manually supplies the same digest in a new invocation, that is a new approval event; the contract does not claim global permanent consumption.

## Versioned authority profiles

- `legacy-scattered-to-canonical-v1.0@v1.0.0`：manual review, `writes=false`. Regex, frontmatter, heading, terminal text, path names, and inferred layout remain discovery-only evidence for every protocol version.
- `legacy-v0.14-three-section-to-canonical-v1.1@v1.0.0`：reviewed supervised bridge for the frozen v0.14 source envelope. It is selectable only when the verifier, profile, source/tag, full file/mode envelope, manifest projection, CommonMark AST, adapter proof, and single full-file `SKILL.md` complement proof all match. It emits `supervised_migration_available`; only its exact `operation_sha256` grants the current invocation one write transaction.
- `canonical-v1.0-to-v1.1@v1.0.0`：the first concrete automatic profile. Planning is available only after the trusted-base verifier resolves its immutable v1.0/v1.1 closures and the target matches the complete v1.0 closure, stable block identity, profile identity, and adapter digest.
- `prerelease-ambiguous-to-manual-review@v1.0.0`：a v1.1 Harness without the exact migration contract and managed-block receipts remains manual, `writes=false`.
- `unknown-to-manual-review@v1.0.0`：manual review, `writes=false`.

The runtime does not select a profile by a hard-coded profile id. Each new Harness release adds one immutable current closure and a direct-to-current profile from every still-supported immutable historical closure. `history/current.json` selects the current closure; `profiles/current.json` selects the complete active upgrade star. Historical closures and profile files become append-only when their release is published. During unreleased v0.15 development, the v1.1 closure and its profiles may be completed in place; after v0.15 is released, any authority or postimage change requires a new closure/profile version and pointer rotation. The current supervised v1 schema/verifier authorizes only the exact v0.14→v1.1 envelope. v1.2 supervised support first requires a protected source rotation that publishes new schema/verifier/consumer authority; a later data-only PR can then add the new closure/profile/artifacts. Candidate data alone cannot widen v1 authority.

`release_lineage_records` identifies deterministic release history shared by every target at the same current closure. v1.1 fresh attach, automatic, and supervised paths all materialize `harness-skill-v1.0.0-to-v1.1.0.md`. `migration_records/current_migration_record` identifies the actual arrival path: the reviewed v0.14 profile alone creates `reviewed-legacy-v0.14.0-to-harness-skill-v1.1.0.md`, bound to the exact profile, envelope, wrapper/Harness/instruction transitions, retained complement, and rollback policy. Fresh attach never creates this applied record, and automatic profiles never reference it. The runtime fails closed when these fields overlap, drift, or disagree with verified operations.

Destructive authority comes exclusively from the verified external official profile and its exact evidence chain. Automatic profiles use their immutable closure diff and complete from-closure match. The reviewed legacy profile uses its frozen envelope plus the hash-bound CommonMark adapter proof and exact retained-byte complement; frontmatter and regex remain non-authoritative evidence. A missing field, Setext/ATX/HTML duplicate, mixed newline surface, present must-be-absent path, changed exact byte/mode/inode, target-root or Git-index drift, untrusted source, ambiguous profile match, or unapproved applicable digest blocks all writes.

## Source and snapshot trust

- Release identity comes from the fixed `MetaInFLow/EvoZeus-CoEvolve` GitHub API tag attestation. The local tag ref object, peeled commit, and clean `HEAD` must match that attestation; configured Git remotes and `origin` names supply diagnostics only and grant no trust.
- The tagged contract manifest must bind the contract digest. Every source file used as a postimage must equal both its declared digest and its bytes in that tag.
- Changes to the trusted verifier, migration consumers, protocol, schemas, or repository governance always use two PRs: land the protected source rotation first, then open a data-only migration PR against that reachable Commit. This split applies under every merge strategy. The official PR check classifies the full diff from trusted base code; ordinary PRs return `not_applicable`, protected-source PRs return `rotation_required` without executing candidate code, and data-only migration PRs enter exact candidate verification.
- The trusted protocol declares the complete construction-source allowlist. Candidate closures may bind `templates/target/` or the individually reviewed source scripts named by that protocol. Repository workflow/governance files, ordinary docs, unknown scripts, and every undeclared source path are rejected even when a candidate closure attempts to bind them.
- Every immutable closure `construction_revision` must remain an ancestor of the final stacked landing and Release Commit. The verifier checks every historical closure against repository history plus frozen construction-source bytes and modes. Squash, rebase, an unmerged side object, or a stacked landing that drops ancestry blocks main/UAT CI and Release. A merge Commit or a source-first Commit followed by a data-only Commit preserves the required ancestry.
- The official PR workflow runs for every PR, but current verified external state has no `main` ruleset/branch protection, required environment, or immutable-release gate. Treat v0.15 official release as an apply prerequisite and verified external governance as a publish prerequisite; do not claim the workflow is already an enforced merge gate.
- An unreleased branch may inspect and plan. Its apply status remains `source_unreleased`, `writes=false`.
- The snapshot base must stay outside the target Repo. Reject symlinks in the base, transaction directory, descriptor, files tree, and backup paths.
- Rollback accepts only a direct child of the trusted snapshot root with the expected schema, transaction identity, plan digest, descriptor digest receipt, backup-set digest, complete metadata, and valid backup hashes.

## Protected target surfaces

- The compact activation block uses the stable `evozeus-harness-entry` marker and manifest-bound relative link.
- Fresh attach may add the block only when the surface has zero canonical markers and zero historical candidates.
- Historical candidates alone never authorize deletion, relocation, or replacement. Route unmatched candidates to a reviewed adapter/profile backlog.
- Automatic canonical profiles do not write the instruction surface. The reviewed v0.14 supervised profile may replace `SKILL.md` only as one full-file postimage whose deleted legacy spans and retained business-byte complement are both proven by the bound CommonMark adapter. No other protected surface becomes writable.
- Pre-existing unknown files at managed destinations are preserved and block bootstrap/repair, including `--force`, unless an exact trusted preimage or manifest receipt proves ownership.

## Stop conditions

Stop with `writes=false` when any condition below holds:

- `.evozeus-wrapper/wrapper.json` is missing, ambiguous, incomplete, or conflicts with a legacy manifest.
- The plan lacks one verified automatic or supervised profile, or any independent protocol/profile/adapter identity.
- The source release, remote tag commit, contract, adapter, full from closure, or postimage cannot be verified exactly.
- The target Git status is unavailable or dirty.
- A write path escapes the target, traverses a symlink, has an unsafe parent type, or changed after planning.
- `--approve-plan` is absent or differs from the current `operation_sha256` for supervised legacy migration, or from `plan_sha256` for automatic migration.
- Snapshot creation or complete pre-rollback validation fails.

Read-only diagnosis can continue after a stop. Any repair requires a new versioned profile or an explicitly reviewed manual procedure.
