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
| C7 | A governed release and recovery case works end to end for a representative target. | Completed feasibility case | `examples/engineering-everything/`: pinned manifest, event ledger, reproduction script, target release, and rollback evidence | Feasibility Evaluation; one-target scope only |

## Current code verification

EvoZeus-CoEvolve `v0.11.4` passed 129 repository tests. The public case additionally reruns the pinned target's native gates, source-preservation comparison, fresh-client installation, idempotency check, and prior-release recovery rehearsal. Release metadata records the immutable GitHub revision and command outcomes.

The completed C7 evidence establishes attachment and governed-lifecycle feasibility for one target. C5 and C6 remain planned or data gated; this case cannot be cited as evidence of collaborative improvement quality or superiority over self-evolution.

## Legacy identifiers

`.evozeus-wrapper/`, `evozeus_wrapper.py`, `EVOZEUS_WRAPPER_*`, and existing Skill slugs remain stable compatibility identifiers. Their presence does not change the public system and paper name `EvoZeus-CoEvolve`.
