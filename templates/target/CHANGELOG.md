# Changelog

All notable changes to {{SKILL_NAME}} are recorded here.

Wrapper harness migrations are recorded under `.evozeus-wrapper/docs/migrations/`. Add them here only when the migration also changes this Skill's behavior or release contract.

Issue-to-PR changes must reference the private contributor branch ledger resume key and copy only public-safe `pr_metadata` into the PR. Local Repo/worktree/ledger paths remain outside this changelog.

## [Unreleased]

### Skill changes

- None yet.

### Feedback / Issues

- None yet.

### Verification

- None yet.

## [{{INITIAL_VERSION}}] - {{DATE}}

### Skill changes

- Initialized the EvoZeus-CoEvolve self-evolving dashboard.

### Feedback / Issues

- Initial harness creation.

### Verification

- `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure`
- `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py release --tag {{INITIAL_VERSION}} --release-notes release-notes.md`

## Release Notes Policy

Every release must include:

- The Skill behavior that changed.
- The related feedback Issue or design doc.
- The verification performed.
- Known limitations or rollback notes.

Release tags must use `vMAJOR.MINOR.PATCH`:

- `MAJOR`: incompatible Skill behavior or output contract change.
- `MINOR`: new capability, new required evidence rule, or new harness behavior.
- `PATCH`: wording, examples, bug fixes, validation fixes, or non-breaking clarifications.
