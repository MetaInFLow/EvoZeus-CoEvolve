# EvoZeus-CoEvolve v0.8.0

## Summary

This release consolidates wrapper-owned target artifacts under `.evozeus-wrapper/` and introduces a one-time upgrade migration from the scattered v1 layout.

Legacy `.evozeus_evoinfra/` and `.evozeus/wrapper.json` paths are migration inputs only. Managed execution now requires the layout v2 manifest at `.evozeus-wrapper/wrapper.json`.

## Changes

- Added `harness migrate-layout` dry-run and apply modes.
- Added source/destination planning, clean-worktree enforcement, conflict blocking, reference rewriting, migration records, and empty legacy directory cleanup.
- Consolidated changelog, wrapper guide, policies, hook adapter, preflight, dashboard, design docs, and migration records under `.evozeus-wrapper/`.
- Kept only host-required thin entrypoints in `.codex/hooks.json` and `.github/`.
- Added `layout_version=2` to generated wrapper manifests.
- Moved GitHub Pages publishing to a workflow that builds `.evozeus-wrapper/docs/`.
- Fixed runtime reference parsing so command arguments are not treated as part of referenced file paths.

## Migration

Review the migration plan first:

```bash
python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/target \
  --latest-version v0.8.0 \
  --dry-run \
  --json
```

Apply only when the target worktree is clean and the plan has no conflicts:

```bash
python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/target \
  --latest-version v0.8.0 \
  --json
```

## Verification

- `python3 -m pytest -q`: 55 tests passed.
- Python compilation passed for all wrapper scripts.
- YAML and JSON target templates parsed successfully.
- A real v0.7 target produced a clean 12-move dry-run plan.
- A temporary v0.7 target copy completed apply, structure, and runtime checks with no legacy manifest remaining.

## Rollback

Run migration in a clean target worktree and commit it separately. Revert that migration commit to restore the previous target layout. To roll back EvoZeus-CoEvolve itself, reinstall or check out `v0.7.0`.
