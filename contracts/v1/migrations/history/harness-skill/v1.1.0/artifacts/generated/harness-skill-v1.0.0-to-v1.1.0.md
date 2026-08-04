# EvoZeus-CoEvolve Harness Skill Migration: v1.0.0 -> v1.1.0

- Protocol：`evozeus-official-upgrade@v1.0.0`
- Profile：`canonical-v1.0-to-v1.1@v1.0.0`
- Target wrapper：`v0.14.0 -> v0.15.0`
- Contract bundle：`v1.0.0 -> v1.2.0`
- Harness Skill：`v1.0.0 -> v1.1.0`
- Delete set：empty
- Move set：empty
- Protected business surface：`manifest.instruction_surface` remains byte-exact.
- Version fact source：`.evozeus-wrapper/wrapper.json`.
- Rendered documentation：`WRAPPER.md` and `docs/index.md` remain byte-exact; refresh is deferred until a trusted install receipt proves the render inputs.
- Rollback：restore the approved complete snapshot, then verify every preimage.

This ledger is a deterministic release artifact. Apply receipts record target-specific plan,
snapshot and verification identities outside this file.
