# EvoZeus-CoEvolve v0.11.3

This patch release publishes the first public feasibility case for attaching the CoEvolve lifecycle harness to existing Skillware.

## Added

- A pinned Engineering Everything case manifest and public feasibility ledger.
- A reproduction script covering target-native gates, CoEvolve structure validation, normalized preservation of 12 Skill instruction files, fresh-client canonical installation, reinstall idempotency, and prior-release recovery.
- Artifact tests for revision pins, event completeness, privacy-safe paths, and claim boundaries.

## Evidence result

- Engineering Everything `v0.13.0` was released through a governed Issue/PR/release path.
- All post-attachment target-native gates passed, including 10 unit tests.
- Normalized target-owned Skill instructions remained equal for 12/12 runtime Skills.
- A fresh synthetic Codex + Agents installation produced 24 symlinks and zero copied Skill entries; a second installation was idempotent.
- An isolated `v0.12.0` recovery checkout passed all nine prior native tests and documented gates.

## Claim boundary

This release establishes one-target attachment and governed-lifecycle feasibility. It does not report cross-user effectiveness, frontier code/research transfer gains, or superiority over self-evolution.

## Verification

- `python3 -m pytest -q` — 128 passed.
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py`
- `bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh`
- `RUN_GITHUB_GATES=1 bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh`
