---
name: using-evozeus-wrapper
description: Use whenever EvoZeus-CoEvolve is invoked to diagnose, attach, adopt, repair, verify, publish, reinstall, release, or migrate the Repo-level Harness of an independent Skillware Git repository.
---

# Using EvoZeus Wrapper

Use this Skill as the operating guide for EvoZeus-CoEvolve. The root `SKILL.md` only anchors discovery and routing.

EvoZeus is the user entrypoint. EvoZeus-CoEvolve runs when an independent Skillware Git Repo needs feedback capture, release governance, Harness attachment, or Harness migration.

## First Principles

A static Skill is only useful if it can improve from real use without losing accountability. The wrapper must turn every evolution into a traceable loop:

```text
weak result
  -> feedback Issue
  -> design doc
  -> PR
  -> CHANGELOG
  -> release notes
  -> latest release check before next run
  -> updated Skill
```

Keep one source of truth and one Harness boundary:

- one physical canonical GitHub repo clone for the target Skill or runtime kit
- one `.evozeus-wrapper/` at that Git Repo root
- packages, packs, apps, and Skill directories inherit the root Harness
- `~/.evozeus/.projects/OWNER/REPO` pointing to the canonical repo
- `~/.codex/skills/<skill-name>` and optional `~/.agents/skills/<skill-name>` as runtime pointers to that canonical repo

Do not let copied runtime installs become another source of truth.

## Source Discovery Order

For wrapper-managed targets, discover source state in this order:

1. Read `.evozeus-wrapper/wrapper.json`. If only an old manifest exists, route to layout migration first.
2. Check `~/.evozeus/.projects/OWNER/REPO`.
3. Verify canonical repo origin and GitHub repo access.
4. Inspect `.codex` / `.agents` runtime installs only as pointers.
5. Use GitHub user / org / public search only when wrapper state is absent.

## Required Inputs

Before writing anything, identify:

- Target Repo: absolute independent Git repository root; a child path must be normalized to this root.
- Target type: root `SKILL.md`, root `AGENTS.md` runtime kit, multi-Skill bundle, or hook/plugin-controlled Skill bundle.
- Target GitHub repo: `OWNER/REPO`.
- Visibility: `public` or `private`.
- Skill / kit display name.
- Whether target-owned initialization is required, including its command and verification command.
- Whether the target generates child Skills; generated Skills inherit the parent Repo Harness unless they are published as independent Repos.
- Evidence boundary: public examples only, redacted examples, or private material.

If visibility is missing, ask before creating or pushing anything.

Reject Harness writes when the target is outside Git, when an active Harness exists below the Repo root, or when the current GitHub account is not an `ADMIN` of the target Repo. Diagnosis and dry-run plans remain read-only.

## Version Standard

Use `vMAJOR.MINOR.PATCH`.

- `MAJOR`: incompatible Skill behavior, required input, or output contract change.
- `MINOR`: new capability, new required evidence rule, or new harness behavior.
- `PATCH`: wording, examples, bug fixes, validation fixes, or non-breaking clarifications.

Attaching a Harness never resets the target Repo version. Preserve its current Skillware version:

1. GitHub latest release tag is the current version.
2. If GitHub has no release but `.evozeus-wrapper/CHANGELOG.md` has a latest `vMAJOR.MINOR.PATCH` entry, create or verify that release before runtime use.
3. If neither exists, stop and ask the owner to choose the current version.

Wrapper harness version is separate and recorded in `.evozeus-wrapper/wrapper.json`.

## Lifecycle

### 1. Environment Diagnosis

Use `skills/environment-diagnosis/SKILL.md`.

```bash
python3 scripts/evozeus_wrapper.py env diagnose --json
```

If the result says `next_action: install_evozeus`, install / initialize EvoZeus before touching the target repo.

### 2. Target Repo Diagnosis

Use `skills/target-skill-diagnosis/SKILL.md`.

```bash
python3 scripts/evozeus_wrapper.py skill diagnose \
  --target /absolute/path/to/target-skill-or-kit \
  --repo OWNER/REPO \
  --json
```

The diagnosis script reports facts only:

- GitHub access, visibility, default branch, and current account permission
- target kind: `single_skill`, `runtime_skill_bundle`, `hooked_skill_bundle`, `skill_bundle`, `agents_runtime`, or `unknown`
- `skills/*/SKILL.md` inventory
- evolution surface candidates and controller files
- runtime integration mode plus scoped capabilities: `repo_maintenance_hook`, `global_session_dispatcher`, `global_prompt_lesson_watcher`, `skill_entry_preflight`, `tool_gateway`, and future `skill_invocation_hook`
- Codex hook registration evidence from `.codex/hooks.json` and `.evozeus-wrapper/hooks/evozeus_wrapper_start_check.py`
- wrapper component gaps
- source contract and runtime install state

Do not treat script-produced `evolution_surface.candidates` as final placement.

### 3. Evolution Surface Diagnosis

Use `skills/evolution-surface-diagnosis/SKILL.md`.

Browse the whole target repo enough to prove what controls agent behavior:

- root instruction files such as `AGENTS.md` or `SKILL.md`
- plugin manifests
- session/startup hooks
- candidate control Skills under `skills/*/SKILL.md`
- architecture docs only when they clarify the control chain

Choose `instruction_surface` only from repo evidence. For hook/plugin-controlled systems, pass the chosen relative path into transform with `--instruction-surface`.

Do not call a wrapper CLI command a runtime hook. A Codex project-local hook is only `repo_maintenance_hook` evidence for the canonical repository. It does not prove per-Skill invocation coverage. `prompt_runtime_check` is the Skill-entry fallback until Codex provides a native `SkillInvoke` event.

### 4. Status Assessment

Use `skills/status-assessment/SKILL.md`.

Explain to the user:

- environment status
- GitHub access
- repo architecture
- chosen instruction surface and evidence
- runtime integration mode and whether host hook evidence exists
- missing wrapper components
- version status
- blockers
- next command

Do not move this user-facing judgment into Python scripts.

### 5. Transform

Use `skills/target-skill-transform/SKILL.md`.

```bash
python3 scripts/evozeus_wrapper.py skill transform \
  --mode <attach|adopt|repair|verify> \
  --target /absolute/path/to/target-skill-or-kit \
  --repo OWNER/REPO \
  --instruction-surface <relative path> \
  --visibility <public|private> \
  --dry-run \
  --json
```

For `verify`, run:

```bash
python3 scripts/evozeus_wrapper.py skill transform --mode verify --target /absolute/path/to/target-skill-or-kit
```

Transform must add wrapper-owned material only. It must not rewrite target business rules.

### 6. Publish And Reinstall

Use `skills/publish-reinstall/SKILL.md`.

```bash
python3 scripts/evozeus_wrapper.py publish reinstall \
  --skill-name target-skill \
  --canonical-path /absolute/path/to/canonical/repo \
  --target codex \
  --dry-run \
  --json
```

Runtime installs should become symlinks or pointers to the canonical repo, not copied forks.
Run the same command without `--dry-run` to apply missing or incorrect symlinks. If the plan finds a real directory, review it and rerun with `--approve-archive`; the original is moved under `~/.evozeus/archives/runtime-installs/`, never deleted.

The generated manifest records onboarding separately from wrapper implementation:

- installation uses a canonical repo symlink;
- invocation remains owned by the target Skill's canonical `SKILL.md`;
- required initialization must provide both a target-owned command and verification;
- child Skills inherit the parent Repo Harness. A separate Harness lifecycle is allowed only after the child is published as an independent Repo.

User-level global enforcement is a separate lifecycle from the portable target harness:

```bash
python3 scripts/evozeus_wrapper.py hook global plan --json
python3 scripts/evozeus_wrapper.py hook global install --approve --json
python3 scripts/evozeus_wrapper.py hook global status --json
```

After installation, review the new registration with Codex `/hooks`, then record the result with `hook global trust`. Installation and trust must never be reported as the same state.

The installed Core-owned command serves two user-level capabilities: `global_session_dispatcher` for task-start health and `global_prompt_lesson_watcher` for ordinary `UserPromptSubmit` turns. CoEvolve owns registration and trust lifecycle only. EvoZeus Core reads the registered-target inventory and runs the digest-bound Session Signal method; Session Signal owns candidate detection, target selection, and model-only guidance. The Hook remains fail-open and never records an Issue automatically.

### 7. Evolution Loop

Use `skills/evolution-loop/SKILL.md`.

Every behavior change must flow through:

```text
feedback Issue -> design doc -> PR -> CHANGELOG -> release -> latest release check
```

When the global prompt watcher is installed, ordinary Chat corrections can be surfaced before a target Skill is selected. After the user confirms recording or the target needs deterministic routing, run:

```bash
python3 scripts/evozeus_wrapper.py loop audit --target /absolute/path/to/target-skill --user-input "<input>" --json
```

Use the returned route, severity, evidence boundary, and Issue draft before creating or recommending a Skill Feedback Issue.

If the result has `should_capture=true`, finish the original business correction and then show `user_notice.display_text` as a separate Lesson block. Wait for explicit feedback-submission confirmation. The audit does not persist a signal or return an executable Issue command. Creating an Issue requires explicit confirmation; implementing a fix, creating a branch/design doc, or opening a PR requires a later separate authorization.

Treat the full audit object as machine-only. Never expose its JSON, signal id, capture state, route, severity, evidence boundary, or Issue draft in normal Chat.

### 8. Harness Upgrade

Use `skills/harness-upgrade/SKILL.md`.

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-check \
  --target /absolute/path/to/target-skill-or-kit \
  --json

python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/target-skill-or-kit \
  --latest-version v0.14.0 \
  --dry-run \
  --json

python3 scripts/evozeus_wrapper.py harness upgrade-all \
  --latest-version v0.14.0 \
  --dry-run \
  --json
```

Apply the same `migrate-layout` command without `--dry-run` only after the plan has no conflicts and the user approves it. Migration moves old wrapper files into `.evozeus-wrapper/`, rewrites references, updates the layout v2 manifest, records the migration, and removes only empty legacy wrapper directories. It must not rewrite target Skill business logic.

For wrapper `v0.10.0+`, treat target-local and user-level hooks as separate capabilities:

- `.codex/hooks.json` registers the project-local `SessionStart` adapter for `startup|resume`.
- `.evozeus-wrapper/hooks/evozeus_wrapper_start_check.py` reads `.evozeus-wrapper/wrapper.json` and emits Codex hook JSON.
- The project hook reports `capability=repo_maintenance_hook` and `scope=canonical_repository`; it is not a per-Skill invocation hook.
- `~/.codex/hooks.json` may separately register the global dispatcher, which aggregates every registered wrapped Skill at task start.
- The user-level registration also includes `UserPromptSubmit` on the Core-owned runtime endpoint. Core delegates normal-Chat turns to the digest-bound Session Signal method and returns only validated model guidance. This does not identify exact Skill invocation by itself.
- Non-managed hooks require Codex review/trust through `/hooks` before they run.
- Project and global hooks share a successful latest-release cache. Deterministic local source-contract errors block; compatible outdated harnesses warn and allow normal business execution. A normal Skill invocation never authorizes Harness maintenance writes. An unknown remote version with no usable cache also warns and allows.
- `upgrade-all` verifies the authoritative latest version, clean Git state and write access for every target before writing. It backs up the complete migration write set, including target-owned files containing legacy wrapper path references, and rolls all targets back if any apply step fails.

## GitHub Operations

Use `gh` only after the independent target Repo, local changes, visibility, and administrator authority are reviewed.

The target Repo must already exist before Harness attachment:

```bash
git add .
git commit -m "Attach EvoZeus CoEvolve Harness"
git push
gh api --method POST repos/OWNER/REPO/pages -f build_type=workflow
gh variable set EVOZEUS_PAGES_ENABLED --body true --repo OWNER/REPO
```

For private repos, use `--private` and keep `EVOZEUS_PAGES_ENABLED` unset unless the current plan supports private Pages. Push and workflow dispatch still run maintainer validation in repository-only mode. Do not put sensitive content into `.evozeus-wrapper/docs/`; GitHub Pages can become an external publishing surface depending on plan and settings.

## Stop Conditions

Stop and ask when:

- `~/.evozeus` is missing.
- `git` or `gh` is missing, or `gh auth status` fails.
- target repo visibility is not chosen.
- target repo name or canonical source is ambiguous.
- GitHub `ADMIN` permission cannot be verified for a Harness write or upload.
- target GitHub Repo does not exist or origin does not match.
- target is a normal folder or a Repo subdirectory with its own nested Harness.
- existing target repo has no GitHub release and no `.evozeus-wrapper/CHANGELOG.md` version entry.
- an old scattered layout is detected but its migration plan has conflicts.
- no controlling instruction surface can be proven.
- the user wants to publish secrets, raw private sessions, customer data, or unredacted commercial context.
- GitHub Pages would expose sensitive content.

## Output Shape

Keep user-facing output concise and factual:

1. Current lifecycle stage.
2. Target repo and canonical path.
3. GitHub access and visibility.
4. Architecture and instruction surface decision.
5. Missing components and blockers.
6. Next command.
7. Verification results.
