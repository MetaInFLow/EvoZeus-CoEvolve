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
- [`examples/engineering-everything/`](examples/engineering-everything/): first public attachment, preservation, release, and recovery feasibility case.
- [`../../CITATION.cff`](../../CITATION.cff): single-author software and paper citation metadata.

## Current reproduction entry

At the pinned CoEvolve revision:

```bash
python3 -m pytest -q
bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh
```

The test suite verifies the attachment/lifecycle implementation and the public evidence package. The case script clones pinned Engineering Everything revisions, reruns target-native and CoEvolve gates, compares all 12 normalized Skill instruction files, checks a 24-symlink fresh-client install and idempotent reinstall, and rehearses the prior release in an isolated worktree.

The authenticated GitHub doctor, version, and release gates can also be rerun with `RUN_GITHUB_GATES=1`. These commands establish one-target feasibility. They do not measure Collaborative Evolution effectiveness or the value of user, frontier-code, or frontier-research signals.
