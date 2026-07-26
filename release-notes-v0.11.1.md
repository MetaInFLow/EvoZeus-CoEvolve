# EvoZeus-CoEvolve v0.11.1

## Paper artifact release

This release makes EvoZeus-CoEvolve directly citable as the reference implementation for:

> **EvoZeus-CoEvolve: An Add-On Harness for Collaborative Evolution of Existing Skillware**

Author: Haodi Fan, MetaInFlow, `anthonyfan@metainflow.cn`.

## Added

- `CITATION.cff` with initial software and preferred paper citation metadata.
- A canonical `research/collaborative-evolution/` artifact entry.
- A manifest that fixes the public EvoZeus component revisions used by the paper system.
- A claims-to-evidence map that distinguishes implemented, partial, planned, and data-gated claims.
- README links to the author, public artifact, and foundational Skillware paper.

## Compatibility

This documentation and artifact release does not change the target harness layout or execution behavior. `.evozeus-wrapper/`, `evozeus_wrapper.py`, `EVOZEUS_WRAPPER_*`, and existing installed Skill slugs remain stable.

## Verification

- `python3 -m pytest -q` — 124 passed.
- Python compilation checks passed for the CLI, bootstrap, lifecycle, preflight, and global hook entrypoints.
- Citation and artifact YAML files parsed successfully.
- All pinned public component commits were verified on GitHub.
