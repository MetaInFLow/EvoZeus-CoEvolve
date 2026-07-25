# EvoZeus-CoEvolve Phased Lifecycle Implementation Plan

> **For agentic workers:** Use the repo's development execution Skill or plan-execution process to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the phased EvoZeus-CoEvolve lifecycle from diagnosis through transform, reinstall, evolution loop scaffolding, and harness upgrade checks.

**Architecture:** Add a small lifecycle library for deterministic, testable filesystem/GitHub diagnostics, then expose it through a staged `scripts/evozeus_wrapper.py` CLI. Keep the existing `evozeus_wrapper_preflight.py` and `evozeus_wrapper_bootstrap.py` as compatibility surfaces while the new CLI coordinates phases and emits JSON reports.

**Tech Stack:** Python 3 standard library, `argparse`, `dataclasses`, `json`, `pathlib`, `subprocess`, `unittest`, GitHub CLI (`gh`) as an external dependency.

---

## File Structure

- Create `scripts/evozeus_wrapper_lifecycle.py`: pure-ish helper functions and dataclasses for environment diagnosis, skill diagnosis, manifest loading, symlink/reinstall planning, and wrapper upgrade comparison.
- Create `scripts/evozeus_wrapper.py`: staged CLI with `env`, `skill`, `publish`, `loop`, and `harness` command groups.
- Modify `scripts/evozeus_wrapper_bootstrap.py`: write `.evozeus_evoinfra/wrapper.json` and stop treating `~/.evozeus/.projects/OWNER/REPO` as a copied mirror in future paths.
- Modify `scripts/evozeus_wrapper_preflight.py`: expose wrapper manifest/upgrade checks without weakening existing checks.
- Create `tests/test_evozeus_wrapper_lifecycle.py`: unit tests with temporary directories and fake command runners.
- Create sub-skill docs under `skills/*/SKILL.md`: router-facing stage instructions.
- Modify `SKILL.md`, `README.md`, and `docs/harness-contract.md`: update docs to reflect staged lifecycle, one physical repo, symlink installs, wrapper manifest, and harness version axis.
- Modify `templates/target/WRAPPER.md` and `templates/target/docs/index.md`: describe canonical repo pointer and wrapper manifest.

## Task 1: Add Lifecycle Library Skeleton and Tests

**Files:**
- Create: `scripts/evozeus_wrapper_lifecycle.py`
- Create: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for path kinds, remote parsing, stage labels, and skill name parsing**

Add this initial test file:

```python
from pathlib import Path
import tempfile
import unittest

from scripts.evozeus_wrapper_lifecycle import (
    path_kind,
    repo_from_remote,
    skill_name_from_skill_md,
    stage_label,
)


class LifecycleBasicsTest(unittest.TestCase):
    def test_repo_from_remote_supports_https_and_ssh(self):
        self.assertEqual(repo_from_remote("https://github.com/MetaInFLow/EvoZeus.git"), "MetaInFLow/EvoZeus")
        self.assertEqual(repo_from_remote("git@github.com:MetaInFLow/EvoZeus-CoEvolve.git"), "MetaInFLow/EvoZeus-CoEvolve")
        self.assertIsNone(repo_from_remote("https://example.com/MetaInFLow/EvoZeus.git"))

    def test_stage_label_uses_five_stage_contract(self):
        self.assertEqual(stage_label("environment"), "[1/5] Environment Diagnosis")
        self.assertEqual(stage_label("target_skill"), "[2/5] Target Skill Diagnosis")
        self.assertEqual(stage_label("transform"), "[3/5] Target Skill Transform")
        self.assertEqual(stage_label("publish"), "[4/5] Publish & Reinstall")
        self.assertEqual(stage_label("loop"), "[5/5] Continuous Evolution Loop")

    def test_path_kind_distinguishes_missing_directory_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing"
            directory = root / "dir"
            directory.mkdir()
            file_path = root / "file.txt"
            file_path.write_text("x", encoding="utf-8")
            link = root / "link"
            link.symlink_to(directory)

            self.assertEqual(path_kind(missing), "missing")
            self.assertEqual(path_kind(directory), "directory")
            self.assertEqual(path_kind(file_path), "file")
            self.assertEqual(path_kind(link), "symlink")

    def test_skill_name_from_skill_md_reads_frontmatter_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            skill.write_text('---\nname: "resume-screening"\n---\n# Body\n', encoding="utf-8")
            self.assertEqual(skill_name_from_skill_md(skill), "resume-screening")

            no_name = Path(tmp) / "NO_NAME.md"
            no_name.write_text("# Body\n", encoding="utf-8")
            self.assertIsNone(skill_name_from_skill_md(no_name))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify they fail because the module does not exist**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.evozeus_wrapper_lifecycle`.

- [ ] **Step 3: Implement the minimal lifecycle helpers**

Create `scripts/evozeus_wrapper_lifecycle.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


STAGE_LABELS = {
    "environment": "[1/5] Environment Diagnosis",
    "target_skill": "[2/5] Target Skill Diagnosis",
    "transform": "[3/5] Target Skill Transform",
    "publish": "[4/5] Publish & Reinstall",
    "loop": "[5/5] Continuous Evolution Loop",
}


def stage_label(stage: str) -> str:
    try:
        return STAGE_LABELS[stage]
    except KeyError:
        raise ValueError(f"unknown lifecycle stage: {stage}") from None


def path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "missing"


def repo_from_remote(remote_url: str) -> str | None:
    remote_url = remote_url.strip()
    match = re.match(r"^https://github\.com/([^/]+/[^/.]+)(?:\.git)?$", remote_url)
    if match:
        return match.group(1)
    match = re.match(r"^git@github\.com:([^/]+/[^/.]+)(?:\.git)?$", remote_url)
    if match:
        return match.group(1)
    return None


def skill_name_from_skill_md(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
```

Expected: PASS for all four tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/evozeus_wrapper_lifecycle.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: add wrapper lifecycle helpers"
```

## Task 2: Implement Environment Diagnosis

**Files:**
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Create: `scripts/evozeus_wrapper.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for environment diagnosis using a fake command runner**

Append:

```python
from scripts.evozeus_wrapper_lifecycle import diagnose_environment


class EnvironmentDiagnosisTest(unittest.TestCase):
    def test_diagnose_environment_reports_home_and_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            evozeus_home = home / ".evozeus"
            evozeus_home.mkdir()
            (evozeus_home / "runtime").mkdir()
            (evozeus_home / ".projects").mkdir()

            def runner(args, cwd=None):
                return {"returncode": 0, "stdout": "", "stderr": ""}

            report = diagnose_environment(home=home, runner=runner)
            self.assertEqual(report["stage"], "environment_diagnosis")
            self.assertEqual(report["evozeus_home"]["exists"], True)
            self.assertEqual(report["dependencies"]["git"], "ok")
            self.assertEqual(report["dependencies"]["gh"], "ok")
            self.assertEqual(report["dependencies"]["gh_auth"], "ok")
```

- [ ] **Step 2: Run test and verify it fails because `diagnose_environment` is missing**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.EnvironmentDiagnosisTest -v
```

Expected: FAIL with import or attribute error.

- [ ] **Step 3: Implement `CommandRunner`, command status helpers, and `diagnose_environment`**

Add a small command runner wrapper using `subprocess.run`, then implement `diagnose_environment(home: Path, runner=run_command)` returning the JSON-compatible report from the spec. Dependency checks should call `git --version`, `gh --version`, and `gh auth status`; values are `ok` or `missing`/`failed`.

- [ ] **Step 4: Add CLI `env diagnose`**

Create `scripts/evozeus_wrapper.py` with `argparse` command group:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evozeus_wrapper_lifecycle import diagnose_environment, stage_label


def print_report(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(stage_label("environment"))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run staged EvoZeus-CoEvolve lifecycle commands.")
    sub = parser.add_subparsers(dest="group", required=True)
    env = sub.add_parser("env")
    env_sub = env.add_subparsers(dest="command", required=True)
    env_diag = env_sub.add_parser("diagnose")
    env_diag.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.group == "env" and args.command == "diagnose":
        print_report(diagnose_environment(Path.home()), args.json)
        return 0
    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests and CLI**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
python3 scripts/evozeus_wrapper.py env diagnose --json
```

Expected: tests PASS; CLI prints JSON with `stage: environment_diagnosis`.

- [ ] **Step 6: Commit**

```bash
git add scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: add environment diagnosis stage"
```

## Task 3: Implement Target Skill Diagnosis

**Files:**
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `scripts/evozeus_wrapper.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for skill diagnosis**

Add tests that create a temporary Skill folder with `SKILL.md`, a fake Codex install path, a fake `~/.evozeus/.projects` path, and verify the report includes `skill`, `repo`, `installs`, `harness`, and `publication`.

- [ ] **Step 2: Implement limited-path install scanning**

Implement:

```python
def diagnose_skill(target: Path, repo: str | None, skill_name: str | None, home: Path, workspace_roots: list[Path] | None = None, runner=run_command) -> dict:
    target = target.expanduser().resolve()
    skill_md = target / "SKILL.md"
    inferred_name = skill_name or skill_name_from_skill_md(skill_md) or target.name
    install_paths = [
        home / ".codex" / "skills" / inferred_name,
        home / ".agents" / "skills" / inferred_name,
    ]
    installs = [describe_install_path(path, target) for path in install_paths if path.exists() or path.is_symlink()]
    return {
        "stage": "target_skill_diagnosis",
        "skill": {
            "name": inferred_name,
            "target_path": str(target),
            "has_skill_md": skill_md.exists(),
        },
        "repo": diagnose_repo_state(target, repo, home, workspace_roots or [], runner),
        "installs": installs,
        "harness": diagnose_harness_state(target),
        "publication": {
            "visibility": None,
            "sensitive_risk": "unknown",
        },
    }
```

Rules:

- Resolve target.
- Detect `SKILL.md`.
- Infer Skill name from `SKILL.md` or target folder.
- Inspect `~/.codex/skills/<skill-name>` and `~/.agents/skills/<skill-name>`.
- Inspect `~/.evozeus/.projects/OWNER/REPO` when repo is present.
- Do not recurse through the entire home directory.
- Detect harness state as `missing`, `partial`, or `complete` using required wrapper files from `evozeus_wrapper_preflight.py`.

- [ ] **Step 3: Add CLI `skill diagnose`**

Add:

```bash
python3 scripts/evozeus_wrapper.py skill diagnose --target /path/to/skill --repo OWNER/REPO --json
```

Expected output stage: `target_skill_diagnosis`.

- [ ] **Step 4: Run tests and a local diagnosis smoke**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
python3 scripts/evozeus_wrapper.py skill diagnose --target examples/minimal-static-skill --repo MetaInFLow/minimal-static-skill --json
```

Expected: tests PASS; smoke prints JSON and does not write files.

- [ ] **Step 5: Commit**

```bash
git add scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: add target skill diagnosis stage"
```

## Task 4: Add Wrapper Manifest Support

**Files:**
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `scripts/evozeus_wrapper_bootstrap.py`
- Modify: `templates/target/WRAPPER.md`
- Modify: `templates/target/docs/index.md`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for manifest read/write**

Test that `.evozeus_evoinfra/wrapper.json` can be written and loaded with `wrapper_repo`, `wrapper_version`, `canonical_repo`, `managed_files`, and `install_links`.

- [ ] **Step 2: Implement manifest helpers**

Implement:

```python
def wrapper_manifest_path(target: Path) -> Path:
    return target / ".evozeus" / "wrapper.json"

def load_wrapper_manifest(target: Path) -> dict | None:
    path = wrapper_manifest_path(target)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

def build_wrapper_manifest(repo: str, wrapper_version: str, managed_files: list[str], install_links: list[str]) -> dict:
    return {
        "wrapper_repo": "MetaInFLow/EvoZeus-CoEvolve",
        "wrapper_version": wrapper_version,
        "applied_at": date.today().isoformat(),
        "canonical_repo": repo,
        "managed_files": managed_files,
        "install_links": install_links,
    }

def write_wrapper_manifest(target: Path, manifest: dict, force: bool = False) -> str:
    path = wrapper_manifest_path(target)
    if path.exists() and not force:
        return f"skip existing {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"write {path}"
```

- [ ] **Step 3: Update bootstrap to write manifest**

In `evozeus_wrapper_bootstrap.py`, import or mirror the manifest helper. After template copy and self-evolution injection, write `.evozeus_evoinfra/wrapper.json`. Use current initial wrapper version until repo release wiring exists.

- [ ] **Step 4: Update target templates**

Change template language from “local mirror copy” to “canonical pointer / symlink target” where applicable. Keep data-safety warnings.

- [ ] **Step 5: Run tests and bootstrap smoke in a temp directory**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
tmp="$(mktemp -d)"
cp -R examples/minimal-static-skill "$tmp/minimal-static-skill"
python3 scripts/evozeus_wrapper_bootstrap.py "$tmp/minimal-static-skill" --skill-name "Minimal Static Skill" --repo MetaInFLow/minimal-static-skill-dev --visibility private || true
```

Expected: tests PASS. Bootstrap may stop if `gh` cannot verify repo state; if it runs, target contains `.evozeus_evoinfra/wrapper.json`.

- [ ] **Step 6: Commit**

```bash
git add scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_bootstrap.py templates/target/WRAPPER.md templates/target/docs/index.md tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: record wrapper manifest"
```

## Task 5: Implement Transform Verify and Dry-Run Modes

**Files:**
- Modify: `scripts/evozeus_wrapper.py`
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for transform planning**

Test that `plan_transform_action(harness_state="missing", repo_exists=False)` returns `bootstrap`, existing repo returns `adopt`, partial returns `repair`, complete returns `verify`.

- [ ] **Step 2: Implement transform planning**

Implement a pure `plan_transform_action` helper and CLI:

```bash
python3 scripts/evozeus_wrapper.py skill transform --mode verify --target /path/to/skill
python3 scripts/evozeus_wrapper.py skill transform --mode bootstrap --target /path/to/skill --repo OWNER/REPO --visibility private --dry-run
```

For this task, `verify` should run the existing `evozeus_wrapper_preflight.py structure`; `bootstrap/adopt/repair` should support `--dry-run` and print the planned files without writing.

- [ ] **Step 3: Run tests and verify mode smoke**

Run:

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
python3 scripts/evozeus_wrapper.py skill transform --mode verify --target . || true
```

Expected: tests PASS; verify against current wrapper repo may fail structure because this repo is the wrapper source, not a target Skill repo. The failure should be clear and non-destructive.

- [ ] **Step 4: Commit**

```bash
git add scripts/evozeus_wrapper.py scripts/evozeus_wrapper_lifecycle.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: add transform planning stage"
```

## Task 6: Implement Publish Reinstall Planning and Symlink Verification

**Files:**
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `scripts/evozeus_wrapper.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for reinstall plan**

Test:

- Missing install path produces `create_symlink`.
- Symlink to canonical path produces `already_linked`.
- Real directory with identical `SKILL.md` hash produces `archive_then_symlink`.
- Real directory with different `SKILL.md` hash produces `needs_user_confirmation`.

- [ ] **Step 2: Implement reinstall planning**

Implement:

```python
def plan_reinstall(skill_name: str, canonical_path: Path, home: Path, targets: list[str]) -> dict:
    runtime_roots = {
        "codex": home / ".codex" / "skills",
        "agents": home / ".agents" / "skills",
    }
    selected = ["codex", "agents"] if "all" in targets else targets
    actions = []
    for target_name in selected:
        root = runtime_roots[target_name] if target_name in runtime_roots else Path(target_name).expanduser()
        install_path = root / skill_name if target_name in runtime_roots else root
        actions.append(classify_install_action(install_path, canonical_path))
    return {
        "stage": "publish_reinstall",
        "skill_name": skill_name,
        "canonical_path": str(canonical_path.expanduser().resolve()),
        "actions": actions,
    }
```

The function must not write files. It returns actions and reasons.

- [ ] **Step 3: Add CLI `publish reinstall --dry-run`**

Add:

```bash
python3 scripts/evozeus_wrapper.py publish reinstall --skill-name resume-screening --canonical-path /path/to/repo --target codex --dry-run --json
```

For this task, only `--dry-run` is implemented. Non-dry-run should fail with a clear message: `write operations are not implemented until archive confirmation is added`.

- [ ] **Step 4: Run tests**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/evozeus_wrapper.py scripts/evozeus_wrapper_lifecycle.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: plan symlink reinstall"
```

## Task 7: Add Evolution Loop and Harness Upgrade Scaffolding

**Files:**
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `scripts/evozeus_wrapper.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: Write failing tests for permission and upgrade state helpers**

Test helpers:

- `classify_pr_permission(write=True, fork=True)` returns `direct_pr`.
- `classify_pr_permission(write=False, fork=True)` returns `fork_pr`.
- `classify_wrapper_upgrade(current="v0.1.0", latest="v0.1.1", managed_dirty=False)` returns `auto_pr`.
- `classify_wrapper_upgrade(current="v0.1.0", latest="v1.0.0", managed_dirty=False)` returns `requires_confirmation`.

- [ ] **Step 2: Implement pure classification helpers**

Implement semantic version parsing for `vMAJOR.MINOR.PATCH` and the classification helpers.

- [ ] **Step 3: Add scaffold CLI commands**

Add:

```bash
python3 scripts/evozeus_wrapper.py loop lesson --dry-run
python3 scripts/evozeus_wrapper.py loop issue-to-pr --dry-run
python3 scripts/evozeus_wrapper.py harness upgrade-check --target /path/to/skill --json
```

These commands should not create Issues or PRs yet. They should print the required permission checks and next action.

- [ ] **Step 4: Run tests**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/evozeus_wrapper.py scripts/evozeus_wrapper_lifecycle.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: scaffold evolution and harness upgrade stages"
```

## Task 8: Add Stage Sub-Skills and Documentation Updates

**Files:**
- Create: `skills/environment-diagnosis/SKILL.md`
- Create: `skills/target-skill-diagnosis/SKILL.md`
- Create: `skills/target-skill-transform/SKILL.md`
- Create: `skills/publish-reinstall/SKILL.md`
- Create: `skills/evolution-loop/SKILL.md`
- Create: `skills/harness-upgrade/SKILL.md`
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `docs/harness-contract.md`

- [ ] **Step 1: Create focused sub-skill docs**

Each sub-skill should include:

- Trigger context.
- Required inputs.
- Stop conditions.
- Which CLI command to run.
- What user decisions are allowed in that stage.

- [ ] **Step 2: Update root `SKILL.md` as router**

The root skill should explain the five-stage lifecycle and route to sub-skills. It should keep the visibility/data-safety stop conditions.

- [ ] **Step 3: Update README and contract**

Document:

- `scripts/evozeus_wrapper.py` staged CLI.
- One physical repo invariant.
- `~/.evozeus/.projects` as pointer, not copied mirror.
- Runtime symlink installation.
- `.evozeus_evoinfra/wrapper.json`.
- Wrapper harness version axis.

- [ ] **Step 4: Run docs checks**

```bash
rg -n "local Skill mirror|复制到 `~/.evozeus|repo 化前的本地 Skill 项目镜像" README.md SKILL.md docs templates scripts
git diff --check
```

Expected: stale mirror language is either removed or deliberately explained as historical compatibility; `git diff --check` passes.

- [ ] **Step 5: Commit**

```bash
git add skills SKILL.md README.md docs/harness-contract.md
git commit -m "docs: add staged wrapper subskills"
```

## Task 9: Final Verification

**Files:**
- No new files unless verification reveals a narrow fix.

- [ ] **Step 1: Run unit tests**

```bash
python3 -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI smoke checks**

```bash
python3 scripts/evozeus_wrapper.py env diagnose --json
python3 scripts/evozeus_wrapper.py skill diagnose --target examples/minimal-static-skill --repo MetaInFLow/minimal-static-skill --json
python3 scripts/evozeus_wrapper.py publish reinstall --skill-name minimal-static-skill --canonical-path "$PWD/examples/minimal-static-skill" --target codex --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-check --target examples/minimal-static-skill --json
```

Expected: every command exits 0 and emits JSON or clear dry-run output.

- [ ] **Step 3: Run existing preflight checks**

```bash
python3 scripts/evozeus_wrapper_preflight.py doctor --repo MetaInFLow/EvoZeus-CoEvolve
python3 scripts/evozeus_wrapper_preflight.py release --target /tmp/nonexistent --tag v0.1.0 --skip-gh || true
```

Expected: doctor passes when `gh` is authenticated. The release negative smoke should fail clearly because the target is absent.

- [ ] **Step 4: Check formatting and git status**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files changed or clean after final commit.

- [ ] **Step 5: Commit final fixes if any**

```bash
git add .
git commit -m "test: verify phased wrapper lifecycle"
```

Only run this commit if verification required additional fixes.

## Self-Review

Spec coverage:

- Environment diagnosis is covered by Tasks 2 and 8.
- Target Skill diagnosis is covered by Task 3.
- Target Skill transform is covered by Tasks 4 and 5.
- Publish/reinstall symlink planning is covered by Task 6.
- Evolution loop is covered by Task 7.
- Harness version axis is covered by Tasks 4 and 7.
- Sub-skill decomposition and docs are covered by Task 8.
- Verification is covered by Task 9.

Scope note:

This plan implements full read-only diagnosis, staged CLI, manifest support, dry-run transform/reinstall planning, and docs/sub-skill routing. Destructive or remote-write operations such as creating Issues, creating PRs, replacing install directories, pushing releases, and enabling Pages remain gated behind future confirmation-specific implementation. That matches the spec's rule that write, publish, and irreversible actions require explicit confirmation.
