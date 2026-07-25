# Claims to Evidence

## Evidence policy

Repository code and tests establish implementation and engineering behavior. They do not establish population-level effectiveness. Every paper claim is labeled by its current evidence state.

| ID | Claim | State | Public evidence | Paper use |
|---|---|---|---|---|
| C1 | Collaborative Evolution can be defined around shared Skillware identity, population signals, independent validation, and governed redistribution. | Definition | paper formalism and artifact protocol | Introduction and Methodology |
| C2 | Existing compatible Skillware can receive an add-on lifecycle harness with bounded, managed changes. | Implemented substrate | diagnosis, bootstrap, migration, preflight, reinstall, and regression tests in this repo | System design and feasibility evaluation |
| C3 | The reference implementation is modular and can pin public EvoZeus components under one artifact entry. | Implemented artifact | `artifact-manifest.yaml` and public component revisions | Artifact description |
| C4 | Use evidence can be extracted with privacy-aware references. | Implemented in component | pinned `EvoZeus-session-signal-skill` revision | Method component description |
| C5 | Frontier code and research sources produce falsifiable transfer hypotheses. | Planned | no public end-to-end adapter and hypothesis ledger yet | Architecture and future evaluation only |
| C6 | Multi-source Collaborative Evolution improves held-out users or tasks. | Data gated | no completed baseline comparison or source ablation | Excluded from current Results |
| C7 | A governed release and recovery case works end to end for a representative target. | Feasibility case pending | release/recovery substrate exists; public paper ledger pending | Required before empirical Results |

## Current code verification

The frozen pre-paper baseline passed 123 CoEvolve lifecycle tests. The paper release must rerun the suite at the exact artifact revision and record the command, environment, exit code, and immutable commit.

## Legacy identifiers

`.evozeus-wrapper/`, `evozeus_wrapper.py`, `EVOZEUS_WRAPPER_*`, and existing Skill slugs remain stable compatibility identifiers. Their presence does not change the public system and paper name `EvoZeus-CoEvolve`.
