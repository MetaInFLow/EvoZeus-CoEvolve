# EvoZeus-CoEvolve v0.11.4

This patch release corrects the public paper and software metadata to match the two-author record used by the foundational Skillware paper.

## Correct author record

1. Haodi Fan — MetaInFlow — `anthonyfan@metainflow.cn`
2. Zucong Lan — MetaInFlow — `neillan@metainflow.cn`

The order and email spelling are now locked in `AGENTS.md`, `CITATION.cff`, the artifact manifest, README files, regression tests, and the corrected arXiv source bundle.

## Evidence boundary

This release changes publication metadata and current version records. It does not change the Engineering Everything feasibility observations, the implemented attachment/lifecycle substrate, or the planned/data-gated status of the collaborative evolution engine and effectiveness experiments.

## Verification

- `python3 -m pytest -q` — 129 passed.
- `CITATION.cff` and artifact manifests parsed as YAML.
- Both names and both emails were verified on every public metadata surface.
