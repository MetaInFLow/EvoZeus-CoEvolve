# Changelog

All notable changes to EvoZeus-CoEvolve are recorded here.

## [Unreleased]

### Added

- Added the first versioned Collaborative Evolution contract bundle with a hash-bound `external-sidecar` attachment schema and target-template inventory.
- Added contract tests that require `external-sidecar` to have zero target writes and bind the existing governed templates to a deterministic tree hash.

### Verification

- `python3 -m pytest -q` (132 passed)

## [v0.11.4] - 2026-07-26

### Fixed

- Corrected the paper and software metadata to the same two-author order used by the Skillware paper: Haodi Fan and Zucong Lan.
- Added both author emails to `CITATION.cff`, the artifact manifest, README files, and repository-level author metadata rules.
- Added a regression test that fails when either author or email disappears from public metadata.

### Changed

- Bumped the paper artifact and current harness release examples to `v0.11.4`.
- Kept the Engineering Everything feasibility observations and all implementation/effectiveness claim boundaries unchanged.

### Verification

- `python3 -m pytest -q` (129 passed)
- Parsed `CITATION.cff` and both artifact manifests as YAML.
- Verified both authors and emails across repository publication surfaces.

## [v0.11.3] - 2026-07-25

### Added

- Published Engineering Everything as the first public non-project-specific Skillware feasibility case.
- Added a pinned case manifest, public event ledger, and one-command reproduction script.
- Added artifact tests that validate the ledger schema, privacy boundary, public revision pins, and one-target claim boundary.

### Changed

- Promoted claim C7 from pending to completed one-target feasibility evidence.
- Bumped the paper artifact, generated harness version, citation metadata, and current examples to `v0.11.3`.
- Kept multi-source effectiveness, frontier code/research transfer, and superiority claims data gated.

### Verification

- `python3 -m pytest -q` (128 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py`
- `bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh`
- `RUN_GITHUB_GATES=1 bash research/collaborative-evolution/examples/engineering-everything/reproduce.sh`

## [v0.11.2] - 2026-07-25

### Fixed

- Accepted manifest-managed runtime symlinks that point to direct `skills/<name>/SKILL.md` entries inside a canonical plugin-first Skillware repository.
- Used manifest `install_links` during diagnosis and doctor checks instead of assuming every Skillware package installs at the repository root.
- Seeded missing feedback and audit policies during legacy layout migration so post-validation can complete without weakening the structure gate.

### Verification

- `python3 -m pytest -q` (126 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py`
- Engineering Everything plugin-first feasibility target: structure gate passed after v1-to-v2 harness migration and policy recovery.

## [v0.11.1] - 2026-07-25

### Added

- Added `CITATION.cff` with the paper title and initial author metadata.
- Added `research/collaborative-evolution/` as the canonical public paper artifact entry.
- Added immutable public component revisions and an explicit claims-to-evidence map.

### Changed

- Positioned EvoZeus-CoEvolve as the modular paper-system entry while keeping the implemented attachment/lifecycle boundary explicit.
- Linked the Skillware foundation paper, author GitHub profile, and public artifact from the README.
- Preserved all existing `.evozeus-wrapper/`, Python module, environment variable, and installed Skill compatibility identifiers.

### Verification

- `python3 -m pytest -q` (124 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py`
- Parsed `CITATION.cff` and `research/collaborative-evolution/artifact-manifest.yaml` successfully as YAML.
- Verified every pinned public component commit through the GitHub API.

## [v0.11.0] - 2026-07-25

### Changed

- Renamed the product and canonical repository from `EvoZeus-wrapper` to `EvoZeus-CoEvolve`.
- Updated user-facing status sections, generated documentation, release endpoints, and repository references to the new product name.
- Preserved `.evozeus-wrapper/`, `evozeus_wrapper.py`, `EVOZEUS_WRAPPER_*`, and existing Skill slugs as stable compatibility identifiers for installed harnesses.
- Added migration handling that upgrades legacy status headings without duplicating the status section or changing target-owned Skill content.
- Bumped newly generated wrapper harnesses to `v0.11.0`.

### Verification

- `python3 -m pytest -q` (124 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py templates/global/evozeus_wrapper_dispatcher.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py`

## [v0.10.1] - 2026-07-20

### Fixed

- Preserved YAML frontmatter, target-owned H1 headings, introduction prose, CRLF line endings, whitespace, and business content when refreshing or inserting the wrapper status section, including indented ATX headings and fenced code blocks.
- Scoped legacy command and wrapper-version rewrites to wrapper-owned Markdown sections instead of applying them across target-owned content.
- Recorded actual layout transitions in instruction-surface notes and labeled consolidated-v2 version-only upgrades as `Version Refresh Note` rather than layout migrations.

### Changed

- Bumped newly generated wrapper harnesses to `v0.10.1`.

### Verification

- `python3 -m pytest -q` (123 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py templates/global/evozeus_wrapper_dispatcher.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py`

## [v0.10.0] - 2026-07-20

### Added

- Added an explicit user-level Codex `SessionStart` dispatcher lifecycle with plan, install, status, trust, uninstall, structured merge, backup, rollback, and idempotency.
- Added strict aggregate gating for all registered wrapped Skills, using one authoritative latest-release lookup with fresh and bounded stale cache fallback.
- Added transactional `harness upgrade-all` planning and apply support with authoritative-version, clean-Git and write-access prevalidation, complete write-set backups, cross-target rollback, and idempotency.
- Added capability-scoped manifest and diagnosis fields for project maintenance, global session dispatch, Skill-entry preflight, tool gateways, plugin lifecycle, and future Skill invocation hooks.

### Fixed

- Stopped treating target project-local hooks as evidence of native per-Skill invocation coverage. They are now reported as `repo_maintenance_hook` with `canonical_repository` scope.
- Restricted generated project hook matchers to verified `startup|resume` sources and made project/global checks reuse the shared latest-release cache.
- Separated runtime Skill installation, global hook installation, and Codex trust status in lifecycle reports.
- Preserved unrelated handlers even when they share one `SessionStart` entry with the EvoZeus dispatcher, and made populated upgrade plans JSON-safe.
- Required portable manifests to keep user-level installation/trust state unset and to back Skill-entry capability claims with a real status prelude.

### Changed

- Bumped newly generated wrapper harnesses to `v0.10.0`.
- Updated the Skill entry preflight contract to remain the precise, prompt-enforced fallback until Codex provides a native `SkillInvoke` lifecycle event.

### Verification

- `python3 -m pytest -q` (118 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py templates/global/evozeus_wrapper_dispatcher.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py`
- Temporary user-home install/trust/status/uninstall smoke test and consumer-workspace dispatcher test.

## [v0.9.1] - 2026-07-18

### Fixed

- Made `migrate-layout` prevalidate and safely merge `.codex/hooks.json`, preserving unrelated hooks while creating or refreshing exactly one wrapper `SessionStart` registration.
- Allowed a newer wrapper version to repair an already-consolidated but incomplete v2 harness, with a version-specific migration record.
- Refreshed the wrapper status prelude, authoritative no-override upgrade command, manifest hook/integration facts, dashboard contract, and append-only migration note during layout migration.
- Added structure post-validation so an incomplete migration cannot return a successful report.
- Rewrote the generated dashboard contact link to `.evozeus-wrapper/docs`.
- Split push/workflow-dispatch validation from optional GitHub Pages deployment. Private or unsupported repositories now pass maintainer validation in repository-only mode instead of failing at `configure-pages`.

### Added

- Added the manifest `dashboard` deployment contract and `EVOZEUS_PAGES_ENABLED=true` opt-in for Pages deployment.
- Added complete v0.6 legacy-target migration coverage, malformed/custom hook merge coverage, business-section preservation, hook smoke testing, and Pages workflow regression coverage.

### Verification

- `python3 -m pytest -q` (78 passed)
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py`
- Target JSON/YAML template parsing and temporary complete legacy migration validation.
- Real v0.7 target copy CLI migration followed by successful maintainer validation.

## [v0.9.0] - 2026-07-18

### Added

- Added apply mode to `publish reinstall`, with full prevalidation, canonical `SKILL.md` validation, safe symlink creation/relinking, explicit `--approve-archive`, deterministic EvoZeus archive placement, and rollback on write failures.
- Added manifest onboarding contracts for installation, invocation, target-owned initialization, and generated child Skills.
- Added `.evozeus-wrapper/docs/onboarding.md` and preflight enforcement for required initialization evidence, non-inherited child hooks, `/hooks` trust review, separate child wrapper lifecycles, and consumer-project smoke tests.

### Fixed

- Fixed `harness upgrade-check` self-comparison when `--latest-version` is omitted. The command now resolves the authoritative GitHub latest release and reports `latest_unknown` with source, timestamp, and error details when lookup fails.
- Updated the Codex `SessionStart` hook to refresh the GitHub latest release after installation instead of relying on an install-time version constant.
- Updated generated guidance so upgrade checks no longer pass the installed wrapper version back as the latest version.

### Changed

- Extended the legacy layout migration to generate the onboarding guide and add a default onboarding contract to migrated manifests.
- Bumped newly generated wrapper harness manifests to `v0.9.0`.

### Verification

- `python3 -m pytest -q`
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py`
- Real target dry-run of `harness upgrade-check` without `--latest-version`, confirming authoritative GitHub release discovery.

## [v0.8.0] - 2026-07-18

### Added

- Added `harness migrate-layout` with dry-run and apply modes for the one-time scattered-v1 to consolidated-v2 target migration.
- Added conflict detection, path rewrite, migration records, empty legacy directory cleanup, and layout migration regression coverage.

### Changed

- Consolidated wrapper-owned target artifacts under `.evozeus-wrapper/`.
- Kept only host-required thin entrypoints in `.codex/hooks.json` and `.github/`.
- Changed legacy `.evozeus_evoinfra/` and `.evozeus/wrapper.json` handling from runtime fallback to migration-required input.
- Moved GitHub Pages publishing to a workflow that builds `.evozeus-wrapper/docs/`.
- Bumped newly generated harness manifests to `layout_version=2` and wrapper version `v0.8.0`.

### Verification

- `python3 -m pytest -q`
- `python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py`
- Temporary v0.7-shaped target dry-run, apply, structure, and runtime migration checks.

## [v0.7.0] - 2026-07-08

### Added

- Added official Codex project-local hook registration to wrapped target harnesses via `.codex/hooks.json`.
- Added the `SessionStart` wrapper adapter at `.codex/hooks/evozeus_wrapper_start_check.py`.
- Recorded Codex hook registration metadata in `.evozeus_evoinfra/wrapper.json`.

### Changed

- Treat complete Codex project-local hook files as `native_host_hook` evidence without requiring a plugin manifest.
- Updated preflight validation, harness upgrade planning, templates, and docs to distinguish official Codex hooks from wrapper CLI commands.
- Bumped the generated wrapper harness version to `v0.7.0`.

### Verification

- `python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v`
- `python3 -m py_compile scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py scripts/evozeus_wrapper_bootstrap.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py`
- `python3 scripts/evozeus_wrapper.py skill transform --mode bootstrap --target examples/minimal-static-skill --repo MetaInFLow/minimal-static-skill --visibility private --dry-run --json`

## [v0.6.0] - 2026-07-07

### Changed

- Renamed target repo-local harness infra from `.evozeus` to `.evozeus_evoinfra`.
- Kept global EvoZeus installation and project pointers under `~/.evozeus`.
- Added legacy manifest fallback, conflict detection, migration execution, and JSON output fields for `target_infra_dir`, `legacy_infra_dir`, `manifest_path`, `legacy_manifest_detected`, and `migration_required`.
- Added `loop audit` to produce feedback capture decisions and Skill Feedback Issue drafts.
- Routed wrapper feedback audit issues to `MetaInFLow/EvoZeus-CoEvolve` instead of the target Skill repo.
- Hardened `version` preflight so local changelog versions ahead of GitHub latest release fail unless `--no-release-needed` is explicit.
- Added target templates for feedback policy and audit rule files.

### Verification

- `python3 -m unittest tests/test_evozeus_wrapper_lifecycle.py`
- `python3 scripts/evozeus_wrapper.py harness upgrade-check --target /Users/anthonyf/.codex/skills/daxing-phase2-project-management --latest-version v0.6.0 --json`
- `python3 scripts/evozeus_wrapper.py loop audit --target /Users/anthonyf/.codex/skills/daxing-phase2-project-management --user-input "这个 wrapper 没有自动 issue 回收，有问题" --json`
