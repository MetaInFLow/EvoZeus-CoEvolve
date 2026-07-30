# EvoZeus-CoEvolve v0.14.0

This release makes the independent Git repository the single governance boundary for an EvoZeus Evolution Harness and adds an administrator gate for every Harness mutation.

## Included

- One active Evolution Harness per independent Git repository root.
- Automatic normalization from nested Skill, package, pack, app, and example paths to the repository root.
- Explicit rejection of plain folders and repositories containing an active nested Harness.
- Read-only diagnosis and dry-run planning for ordinary users.
- Verified GitHub `ADMIN` permission before Harness attachment, migration, upgrade, or upload.
- Child Skill onboarding that inherits the parent Repo Harness unless the child becomes an independent repository.

## Governance Contract

```text
Independent Git Repo
  -> one root .evozeus-wrapper/
  -> one Owner / Issue / PR / UAT / Release / rollback boundary
  -> nested Skills and packages inherit the root Harness
```

Harness maintenance never changes the target Skillware release version. Existing nested Harnesses are reported as conflicts and are not overwritten or merged automatically.

## Verification

- Full Python test suite, including Repo-root normalization, plain-folder rejection, nested-Harness rejection, and GitHub administrator authority.
- Python compile checks for the lifecycle, bootstrap, preflight, global dispatcher, and Notice modules.
- Contract bundle hash and source-revision verification.
- Target Skill and nested Skill structure validation.

## Upgrade

Generate the authoritative plan before applying any managed-file change:

```text
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.14.0 --dry-run --json
```

Applying the upgrade requires explicit authorization and verified `ADMIN` permission on every target repository.
