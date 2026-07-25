# Engineering Everything Feasibility Case

This case evaluates whether EvoZeus-CoEvolve can add its lifecycle harness to an existing, non-project-specific Skillware package while preserving that package's native behavior and installation model.

## Why this target

[Engineering Everything](https://github.com/HaodiFan/engineering-everything) is a plugin-first bundle of 12 natural-language runtime Skills. It has its own repository identity, versioned releases, installation workflow, routing contracts, scenarios, and validation gates. It is a reusable engineering method package rather than an application-specific development project.

The selected instruction surface is `skills/using-engineering-everything/SKILL.md`. The remaining 11 Skills stay directly invocable. EvoZeus-CoEvolve adds governance and lifecycle assets under `.evozeus-wrapper/`; it does not take ownership of Engineering Everything's engineering routes or references.

## Evaluated claim

The case evaluates a bounded feasibility claim:

> A compatible existing Skillware package can receive the CoEvolve attachment and governed lifecycle substrate, retain its target-owned behavior, install from one canonical source, publish a governed release, and rehearse recovery to its prior release.

This one-target case does not establish cross-user effectiveness, improvement from frontier code or research, or superiority over self-evolution.

## Pinned revisions

| Item | Revision |
| --- | --- |
| Pre-attachment behavior baseline | [`abcd3bb26bb2c05236ac041d6cebf3af86a81357`](https://github.com/HaodiFan/engineering-everything/commit/abcd3bb26bb2c05236ac041d6cebf3af86a81357) |
| Prior recoverable release | [`v0.12.0`](https://github.com/HaodiFan/engineering-everything/releases/tag/v0.12.0), commit `ba7468a61f701cf8b8643503b8e7082885af5d22` |
| Evaluated Skillware release | [`v0.13.0`](https://github.com/HaodiFan/engineering-everything/releases/tag/v0.13.0), commit `6997b61d100708603bf80711a3d7c1604dc097fe` |
| Harness used for attachment and recovery | [`EvoZeus-CoEvolve v0.11.2`](https://github.com/MetaInFLow/EvoZeus-CoEvolve/releases/tag/v0.11.2) |
| Artifact release containing this case | `EvoZeus-CoEvolve v0.11.3` |

## Observed migration course

The first consolidated-layout validation stopped because the legacy harness did not contain the required feedback and audit policies. After those public templates were restored, the doctor gate exposed a second incompatibility: it assumed runtime links pointed to a repository root, while this plugin-first package correctly linked each installed Skill to `skills/<name>/SKILL.md` in the canonical repository.

EvoZeus-CoEvolve `v0.11.2` recovered both conditions without weakening the gates. The target retained its plugin-first layout and published `v0.13.0` through [Issue #17](https://github.com/HaodiFan/engineering-everything/issues/17), [migration PR #18](https://github.com/HaodiFan/engineering-everything/pull/18), and [release PR #19](https://github.com/HaodiFan/engineering-everything/pull/19).

## Results

| Check | Result |
| --- | --- |
| Pre-attachment native gates | PASS; 9 tests plus reference, scenario, doctor, self-evolution, lesson, and compile gates |
| Post-attachment native gates | PASS; 10 tests plus the same native gates |
| CoEvolve structure, doctor, version, and release gates | PASS |
| Target-owned Skill behavior after normalization | 12/12 Skill instruction files equal to baseline |
| Existing-client reinstall | Idempotent; link-set digest unchanged |
| Fresh synthetic client | 24 canonical Skill symlinks, 0 copied Skill entries |
| Governed Skillware release | `v0.13.0` published |
| Recovery rehearsal | `v0.12.0` checked out separately; all 9 documented native tests and gates passed |

Normalization removes release metadata and the explicitly harness-owned preflight, governance, and migration-note sections before comparing the 12 target-owned Skill instruction files. Native gates are run separately so the source comparison cannot substitute for executable validation.

## Reproduce

From the root of the pinned EvoZeus-CoEvolve release:

```bash
bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh
```

This command clones the pinned target, runs local CoEvolve and target gates, verifies normalized preservation, checks a fresh two-host install, reruns installation for idempotency, and validates the prior release in an isolated worktree.

The historical GitHub governance gates require an authenticated GitHub CLI and are opt-in:

```bash
RUN_GITHUB_GATES=1 \
  bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh
```

The structured event record is in [`feasibility-ledger.jsonl`](feasibility-ledger.jsonl); pinned metadata and claim boundaries are in [`case-manifest.yaml`](case-manifest.yaml).

## Limitations

- The sample contains one Skillware package and supports feasibility only.
- The recommended-entry preflight depends on instruction compliance because the current host has no native per-Skill invocation event.
- The case evaluates attachment, preservation, release governance, canonical installation, and recovery. It does not run a collaborative signal-to-candidate evolution engine.
- Raw sessions, real user home paths, customer data, and private feedback are excluded.
