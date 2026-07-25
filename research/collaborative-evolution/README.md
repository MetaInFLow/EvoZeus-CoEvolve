# EvoZeus-CoEvolve Paper Artifact

## Paper metadata

- Title: **EvoZeus-CoEvolve: An Add-On Harness for Collaborative Evolution of Existing Skillware**
- Author: **Haodi Fan**
- Affiliation: **MetaInFlow**
- Email: **anthonyfan@metainflow.cn**
- GitHub: <https://github.com/MetaInFLow/EvoZeus-CoEvolve>
- Foundational Skillware paper: <https://arxiv.org/abs/2607.18970>

## Artifact purpose

This directory is the canonical public entry for the paper artifact. It pins the public EvoZeus components used by the system, maps paper claims to executable evidence, and separates current implementation from planned multi-source evolution work.

The current release supports a system/protocol paper and a feasibility evaluation of the attachment and lifecycle substrate. Cross-user effectiveness, incremental value from frontier code or research, and superiority over self-evolution remain data-gated claims.

## Files

- [`artifact-manifest.yaml`](artifact-manifest.yaml): immutable public component revisions and roles.
- [`claims-to-evidence.md`](claims-to-evidence.md): implemented, partial, planned, and data-gated claim boundaries.
- [`../../CITATION.cff`](../../CITATION.cff): single-author software and paper citation metadata.

## Current reproduction entry

At the pinned CoEvolve revision:

```bash
python3 -m pytest -q
```

This command verifies the current attachment/lifecycle implementation. It does not measure Collaborative Evolution effectiveness. A paper feasibility ledger and one-target end-to-end evolution case will be added before an empirical Results section is released.
