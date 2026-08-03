# Reviewed legacy v0.14.0 → Harness Skill v1.1.0 applied lineage

- Record role: `applied_migration_lineage`
- Profile: `legacy-v0.14-three-section-to-canonical-v1.1@v1.0.0`
- Source envelope: `legacy-wrapper-v0.14-three-section@v1.0.0` (`sha256:d9082554c2ccb0605c8cad5512c9c085e75384eb74df5e3d84f111eaf4d3d346`)
- Target wrapper: `v0.14.0` → `v0.15.0`
- Harness Skill: absent → `using-evozeus-harness@v1.1.0`
- Instruction shape: reviewed three-section Prompt → one canonical Harness activation
- Retained business complement: byte-exact (`sha256:3822b34e173d290cd2a93ccd083f706546dcc90c2069c77d4d9b3bcf74db8b2e`)
- Approval: one target, one full-file preimage, one exact `operation_sha256`
- Rollback: restore the receipt-bound snapshot on any failed operation or postcondition; manual rollback requires the same target binding, validated snapshot receipt, and explicit approval.

The canonical `harness-skill-v1.0.0-to-v1.1.0.md` file is release lineage shared by every v1.1.0 installation. This file records only the reviewed v0.14.0 supervised arrival path and is never materialized by fresh attach or automatic canonical upgrades.
