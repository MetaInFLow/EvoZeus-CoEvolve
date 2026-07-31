import base64
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.evozeus_branch_consumer import (
    CONTRACT_SHA256,
    CORE_REVISION,
    PLANNER_SHA256,
    ConsumerError,
    compute_resume_key,
    error_report,
    ledger_path_for,
    run_core_planner,
    validate_resume_plan_path,
    verify_managed_snapshot,
    write_ledger_plan,
)
from scripts.evozeus_wrapper_bootstrap import copy_templates, inject_evolution_method
from scripts.evozeus_wrapper_lifecycle import (
    WRAPPER_MANAGED_FILES,
    build_wrapper_manifest,
    write_wrapper_manifest,
)
from scripts.evozeus_wrapper_preflight import (
    MAINTAINER_REQUIRED_FILES,
    TRUSTED_CONTROL_SOURCES,
    _canonical_harness_entry_block,
    check_branch_pr_metadata,
    check_pr,
    check_trusted_pr_checkouts,
)


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "templates" / "target"
REPO = "MetaInFLow/example-skill"
FIXED_DATE = "20260731"
TARGET_BRANCH = "codex/bug/20260731-skill-fix-feedback-flow"


def run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def create_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    run(["git", "init", "-b", "main"], repo)
    run(["git", "config", "user.name", "Branch Consumer Test"], repo)
    run(["git", "config", "user.email", "branch-consumer@example.com"], repo)
    (repo / "fixture.txt").write_text("base\n", encoding="utf-8")
    run(["git", "add", "fixture.txt"], repo)
    run(["git", "commit", "-m", "test: base"], repo)
    run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"], repo)
    run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], repo)
    return repo


def fake_github_bin(root: Path) -> Path:
    binary_dir = root / "bin"
    binary_dir.mkdir()
    gh = binary_dir / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["api", "user"]:
    if os.environ.get("FAKE_GH_IDENTITY_UNAVAILABLE") == "1":
        raise SystemExit(1)
    print(json.dumps({"login": os.environ.get("FAKE_GH_LOGIN", "alice")}))
    raise SystemExit(0)
if args[:2] == ["api", "graphql"]:
    if os.environ.get("FAKE_GH_PERMISSION_UNAVAILABLE") == "1":
        raise SystemExit(1)
    permission = os.environ.get("FAKE_GH_PERMISSION", "WRITE")
    print(json.dumps({"data": {"repository": {"viewerPermission": permission}}}))
    raise SystemExit(0)
if args[:2] == ["api", "repos/MetaInFLow/example-skill"]:
    if os.environ.get("FAKE_GH_REPOSITORY_UNAVAILABLE") == "1":
        raise SystemExit(1)
    private = os.environ.get("FAKE_REPO_PRIVATE", "0") == "1"
    allow_forking = os.environ.get("FAKE_REPO_ALLOW_FORKING", "1") == "1"
    print(json.dumps({
        "private": private,
        "archived": False,
        "disabled": False,
        "allow_forking": allow_forking,
    }))
    raise SystemExit(0)
if args[:2] == ["api", "repos/MetaInFLow/example-skill/issues/36"]:
    state = os.environ.get("FAKE_GH_ISSUE_STATE", "open")
    labels = [] if os.environ.get("FAKE_GH_ISSUE_UNCLASSIFIED") == "1" else [{"name": "skill-feedback"}]
    issue = {
        "number": 36,
        "state": state,
        "title": "Reusable Skill feedback",
        "labels": labels,
    }
    if os.environ.get("FAKE_GH_ISSUE_IS_PR") == "1":
        issue["pull_request"] = {}
    print(json.dumps(issue))
    raise SystemExit(0)
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return binary_dir


def planner_env(binary_dir: Path, **values: str) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": str(binary_dir) + os.pathsep + os.environ["PATH"],
        **values,
    }


def options(repo: Path, worktree: Path, **overrides: str | None) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "profile": "coevolve_target_skillware_consumer",
        "repo": REPO,
        "repo_path": str(repo),
        "base": "origin/main",
        "issue": f"{REPO}#36",
        "actor": "alice",
        "type": "bug",
        "component": "skill",
        "summary": "fix-feedback-flow",
        "permission": "direct",
        "worktree": str(worktree),
        "date": FIXED_DATE,
        "resume_plan": None,
    }
    values.update(overrides)
    return values


def execute_plan(
    repo: Path,
    worktree: Path,
    ledger_root: Path,
    env: dict[str, str],
    *,
    approve_save_plan: bool = False,
    **overrides: str | None,
) -> tuple[dict, int]:
    return run_core_planner(
        options(repo, worktree, **overrides),
        ledger_root=ledger_root,
        approve_save_plan=approve_save_plan,
        asset_root=ASSET_ROOT,
        env=env,
    )


def blocker_codes(plan: dict) -> set[str]:
    return {blocker["code"] for blocker in plan["blockers"]}


def test_snapshot_is_hash_bound_offline_and_rejects_symlinked_parent(tmp_path: Path) -> None:
    assets = verify_managed_snapshot(ASSET_ROOT)
    provenance = assets["provenance"]

    assert provenance["source_revision"] == CORE_REVISION
    assert provenance["runtime_network_fetch"] is False
    assert provenance["contract"]["sha256"] == CONTRACT_SHA256
    assert provenance["planner"]["sha256"] == PLANNER_SHA256
    assert f"/blob/{CORE_REVISION}/" in provenance["contract"]["immutable_source_url"]
    assert f"/blob/{CORE_REVISION}/" in provenance["planner"]["immutable_source_url"]

    copied = tmp_path / "assets"
    shutil.copytree(ASSET_ROOT, copied)
    outside = tmp_path / "outside-contracts"
    shutil.copytree(copied / "contracts", outside)
    shutil.rmtree(copied / "contracts")
    (copied / "contracts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ConsumerError) as caught:
        verify_managed_snapshot(copied)
    assert caught.value.code == "snapshot_symlink"


def test_ledger_paths_reject_repo_traversal_and_noncanonical_resume_names(tmp_path: Path) -> None:
    malicious = {
        "repo": {"canonical": "../escape"},
        "resume": {"key": "branch_v1_0123456789abcdef01234567"},
    }
    with pytest.raises(ConsumerError) as caught:
        ledger_path_for(malicious, tmp_path / "ledger")
    assert caught.value.code == "ledger_repo_invalid"

    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir(mode=0o700)
    invalid = ledger_root / "unexpected.json"
    invalid.write_text("{}\n", encoding="utf-8")
    invalid.chmod(0o600)
    with pytest.raises(ConsumerError) as caught:
        validate_resume_plan_path(invalid, ledger_root)
    assert caught.value.code == "resume_path_invalid"


def test_repo_contained_ledger_root_blocks_before_any_write(tmp_path: Path) -> None:
    repo = create_repo(tmp_path)
    ledger_root = repo / ".private-branch-ledger"
    before = {
        "status": run(["git", "status", "--porcelain=v1", "--untracked-files=all"], repo),
        "refs": run(["git", "for-each-ref", "--format=%(refname):%(objectname)"], repo),
        "worktrees": run(["git", "worktree", "list", "--porcelain"], repo),
    }

    with pytest.raises(ConsumerError) as caught:
        execute_plan(
            repo,
            tmp_path / "isolated-worktree",
            ledger_root,
            planner_env(fake_github_bin(tmp_path)),
            approve_save_plan=True,
        )
    after = {
        "status": run(["git", "status", "--porcelain=v1", "--untracked-files=all"], repo),
        "refs": run(["git", "for-each-ref", "--format=%(refname):%(objectname)"], repo),
        "worktrees": run(["git", "worktree", "list", "--porcelain"], repo),
    }

    assert caught.value.code == "ledger_location_unsafe"
    assert after == before
    assert not ledger_root.exists()


def test_clean_new_resume_and_private_ledger_bind_full_identity(tmp_path: Path) -> None:
    repo = create_repo(tmp_path)
    worktree = tmp_path / "isolated-worktree"
    ledger_root = tmp_path / "private-ledger"
    env = planner_env(fake_github_bin(tmp_path))

    initial, returncode = execute_plan(
        repo,
        worktree,
        ledger_root,
        env,
        approve_save_plan=True,
    )
    assert returncode == 0
    assert initial["blockers"] == []
    assert initial["repo"]["canonical"] == REPO
    assert initial["base"]["ref"] == "origin/main"
    assert initial["base"]["commit"] == run(["git", "rev-parse", "origin/main"], repo)
    assert initial["branch"]["target"] == TARGET_BRANCH
    assert initial["permission_path"]["resolved"] == "direct"
    assert initial["issue_evidence"]["state"] == "OPEN"
    assert initial["ledger"]["status"] == "saved"
    assert initial["consumer_operation"] == {"writes": True, "scope": "local_private_ledger_only"}

    ledger_path = Path(initial["ledger"]["path"])
    saved = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600
    for directory in (ledger_root, ledger_root / "MetaInFLow", ledger_root / "MetaInFLow/example-skill"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    serialized = json.dumps(saved, ensure_ascii=False)
    assert str(repo) not in serialized
    assert str(worktree) not in serialized
    assert saved["ledger"]["path_redacted"] is True
    assert str(repo) not in json.dumps(initial["pr_metadata"], ensure_ascii=False)
    assert str(worktree) not in json.dumps(initial["pr_metadata"], ensure_ascii=False)
    assert initial["pr_metadata"]["profile"] == "coevolve_target_skillware_consumer"
    assert initial["pr_metadata"]["purpose"] == {
        "type": "bug",
        "component": "skill",
        "summary": "fix-feedback-flow",
    }

    run(["git", "worktree", "add", "-b", TARGET_BRANCH, str(worktree), "origin/main"], repo)
    resumed, resumed_code = execute_plan(
        repo,
        worktree,
        ledger_root,
        env,
        resume_plan=str(ledger_path),
    )
    assert resumed_code == 0
    assert resumed["resume"]["decision"] == "resume"
    assert resumed["worktree"]["registered"] is True

    owner_directory = ledger_root / "MetaInFLow"
    owner_directory.chmod(0o755)
    with pytest.raises(ConsumerError) as caught:
        validate_resume_plan_path(ledger_path, ledger_root)
    assert caught.value.code == "ledger_permissions"
    owner_directory.chmod(0o700)

    identity_mutations = [
        (("resume", "key"), "branch_v1_000000000000000000000000"),
        (("repo", "canonical"), "MetaInFLow/other-skill"),
        (("actor", "id"), "mallory"),
        (("base", "ref"), "origin/master"),
        (("base", "commit"), "0" * 40),
        (("branch", "target"), "codex/bug/20260731-skill-other"),
        (("permission_path", "resolved"), "fork"),
    ]
    for keys, value in identity_mutations:
        changed = copy.deepcopy(saved)
        changed[keys[0]][keys[1]] = value
        with pytest.raises(ConsumerError) as caught:
            write_ledger_plan(ledger_path, changed)
        assert caught.value.code == "ledger_collision"


def test_core_scenarios_cover_dirty_wrong_base_collision_fork_and_no_pr(tmp_path: Path) -> None:
    binary_dir = fake_github_bin(tmp_path)
    ledger_root = tmp_path / "ledger"

    dirty_root = tmp_path / "dirty"
    dirty_root.mkdir()
    dirty_repo = create_repo(dirty_root)
    (dirty_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty, dirty_code = execute_plan(
        dirty_repo,
        dirty_root / "worktree",
        ledger_root,
        planner_env(binary_dir),
    )
    assert dirty_code == 2
    assert "dirty_tree" in blocker_codes(dirty)

    wrong_root = tmp_path / "wrong-base"
    wrong_root.mkdir()
    wrong_repo = create_repo(wrong_root)
    (wrong_repo / "fixture.txt").write_text("new head\n", encoding="utf-8")
    run(["git", "add", "fixture.txt"], wrong_repo)
    run(["git", "commit", "-m", "test: move head"], wrong_repo)
    wrong, wrong_code = execute_plan(
        wrong_repo,
        wrong_root / "worktree",
        ledger_root,
        planner_env(binary_dir),
    )
    assert wrong_code == 2
    assert "wrong_base_commit" in blocker_codes(wrong)

    collision_root = tmp_path / "collision"
    collision_root.mkdir()
    collision_repo = create_repo(collision_root)
    run(["git", "branch", TARGET_BRANCH, "origin/main"], collision_repo)
    collision, collision_code = execute_plan(
        collision_repo,
        collision_root / "worktree",
        ledger_root,
        planner_env(binary_dir),
    )
    assert collision_code == 2
    assert "branch_collision" in blocker_codes(collision)

    fork_root = tmp_path / "fork"
    fork_root.mkdir()
    fork_repo = create_repo(fork_root)
    fork, fork_code = execute_plan(
        fork_repo,
        fork_root / "worktree",
        ledger_root,
        planner_env(binary_dir, FAKE_GH_PERMISSION="READ"),
        permission="fork",
    )
    assert fork_code == 0
    assert fork["permission_path"]["resolved"] == "fork"
    assert fork["repo"]["source"] == "alice/example-skill"

    local_root = tmp_path / "local"
    local_root.mkdir()
    local_repo = create_repo(local_root)
    local, local_code = execute_plan(
        local_repo,
        local_root / "worktree",
        ledger_root,
        planner_env(
            binary_dir,
            FAKE_GH_PERMISSION="READ",
            FAKE_REPO_PRIVATE="1",
            FAKE_REPO_ALLOW_FORKING="0",
        ),
        permission="local",
    )
    assert local_code == 0
    assert local["permission_path"]["resolved"] == "local"
    assert local["permission_path"]["push_allowed"] is False
    assert local["permission_path"]["pull_request_allowed"] is False


def test_missing_or_partial_github_evidence_cannot_grant_remote_write(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-gh"
    missing_root.mkdir()
    missing_repo = create_repo(missing_root)
    isolated_bin = missing_root / "isolated-bin"
    isolated_bin.mkdir()
    for command in ("git", "node"):
        executable = shutil.which(command)
        assert executable
        (isolated_bin / command).symlink_to(executable)
    missing_env = {**os.environ, "PATH": str(isolated_bin)}
    missing, missing_code = execute_plan(
        missing_repo,
        missing_root / "worktree",
        tmp_path / "ledger",
        missing_env,
        permission="local",
    )
    assert missing_code == 2
    assert missing["permission_evidence"]["source"] == "unavailable"
    assert missing["permission_path"]["resolved"] == "local"
    assert "issue_evidence_unavailable" in blocker_codes(missing)

    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    partial_repo = create_repo(partial_root)
    partial, partial_code = execute_plan(
        partial_repo,
        partial_root / "worktree",
        tmp_path / "ledger",
        planner_env(
            fake_github_bin(partial_root),
            FAKE_GH_PERMISSION="WRITE",
            FAKE_GH_REPOSITORY_UNAVAILABLE="1",
        ),
    )
    assert partial_code == 2
    assert partial["permission_evidence"]["source"] == "github_api_partial"
    assert partial["permission_path"]["resolved"] == "local"
    assert "permission_expectation_mismatch" in blocker_codes(partial)


def test_live_issue_gate_blocks_closed_pull_request_and_unclassified_issue(tmp_path: Path) -> None:
    binary_dir = fake_github_bin(tmp_path)
    scenarios = (
        ({"FAKE_GH_ISSUE_STATE": "closed"}, "issue_not_open"),
        ({"FAKE_GH_ISSUE_IS_PR": "1"}, "issue_is_pull_request"),
        ({"FAKE_GH_ISSUE_UNCLASSIFIED": "1"}, "issue_not_feedback"),
    )
    for index, (environment, blocker) in enumerate(scenarios):
        root = tmp_path / f"issue-{index}"
        root.mkdir()
        repo = create_repo(root)
        plan, returncode = execute_plan(
            repo,
            root / "worktree",
            tmp_path / "ledger",
            planner_env(binary_dir, **environment),
        )
        assert returncode == 2
        assert blocker in blocker_codes(plan)


def test_issue_to_pr_cli_derives_canonical_repo_and_runs_target_gate(tmp_path: Path) -> None:
    repo = create_repo(tmp_path)
    (repo / "SKILL.md").write_text(
        '---\nname: "example-skill"\n---\n\n# Example Skill\n\nRun business flow.\n',
        encoding="utf-8",
    )
    replacements = {
        "DATE": "2026-07-31",
        "INITIAL_VERSION": "v0.1.0",
        "CURRENT_VERSION": "v0.1.0",
        "REPO_NAME": REPO,
        "REPO_URL": f"https://github.com/{REPO}",
        "SKILL_NAME": "example-skill",
        "VISIBILITY": "public",
        "WRAPPER_VERSION": "v0.14.0",
    }
    copy_templates(repo, replacements, force=False)
    inject_evolution_method(repo, replacements)
    write_wrapper_manifest(
        repo,
        build_wrapper_manifest(REPO, "v0.14.0", WRAPPER_MANAGED_FILES, []),
        force=True,
    )
    run(["git", "add", "."], repo)
    run(["git", "commit", "-m", "test: attach harness"], repo)
    run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], repo)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evozeus_wrapper.py"),
            "loop",
            "issue-to-pr",
            "--target",
            str(repo),
            "--issue",
            f"{REPO}#36",
            "--actor",
            "alice",
            "--type",
            "bug",
            "--component",
            "skill",
            "--summary",
            "fix-feedback-flow",
            "--permission",
            "direct",
            "--worktree",
            str(tmp_path / "isolated-worktree"),
            "--json",
        ],
        env=planner_env(fake_github_bin(tmp_path)),
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 0, result.stderr
    assert report["repo"]["canonical"] == REPO
    assert report["permission_path"]["resolved"] == "direct"
    assert report["consumer"]["runtime_network_fetch"] is False
    assert report["repository_boundary"]["repo_root"] == str(repo)


def test_pr_metadata_gate_recomputes_event_identity_and_live_issue(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    shutil.copytree(ASSET_ROOT, target / ".evozeus-wrapper")
    resume_key = compute_resume_key(
        profile="coevolve_target_skillware_consumer",
        repo=REPO,
        base_ref="origin/main",
        issue=f"{REPO}#36",
        actor="alice",
        permission="direct",
        component="skill",
        summary="fix-feedback-flow",
    )
    body = f"""# Skill Evolution PR

## Contributor Branch Plan

- Resume key: {resume_key}
- Core source revision: {CORE_REVISION}
- Contract SHA-256: {CONTRACT_SHA256}
- Profile: coevolve_target_skillware_consumer
- Purpose type / component / summary: bug / skill / fix-feedback-flow
- Canonical repo: {REPO}
- Base ref / commit: origin/main / {'1' * 40}
- Target branch: {TARGET_BRANCH}
- Issue: {REPO}#36
- Verified actor: alice
- Permission path: direct
"""
    issue_runner = lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0],
        0,
        json.dumps({
            "number": 36,
            "state": "open",
            "title": "Reusable defect",
            "labels": [{"name": "skill-feedback"}],
        }),
        "",
    )
    trusted_context = {
        "github_repository": REPO,
        "github_head_ref": TARGET_BRANCH,
        "github_head_repo": REPO,
        "github_actor": "alice",
        "github_base_ref": "main",
        "github_base_sha": "1" * 40,
        "issue_runner": issue_runner,
    }
    check_branch_pr_metadata(target, body, **trusted_context)

    with pytest.raises(SystemExit):
        check_branch_pr_metadata(
            target,
            body.replace("- Permission path: direct", "- Permission path: local"),
            **trusted_context,
        )

    mismatches = (
        {"github_repository": "MetaInFLow/other-skill"},
        {"github_head_ref": "codex/bug/20260731-skill-other"},
        {"github_head_repo": "mallory/example-skill"},
        {"github_actor": "mallory"},
        {"github_base_ref": "master"},
        {"github_base_sha": "2" * 40},
    )
    for mismatch in mismatches:
        with pytest.raises(SystemExit):
            check_branch_pr_metadata(target, body, **{**trusted_context, **mismatch})

    with pytest.raises(SystemExit):
        check_branch_pr_metadata(
            target,
            body.replace(resume_key, "branch_v1_0123456789abcdef01234567"),
            **trusted_context,
        )

    closed_issue_runner = lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0],
        0,
        json.dumps({
            "number": 36,
            "state": "closed",
            "title": "Reusable defect",
            "labels": [{"name": "skill-feedback"}],
        }),
        "",
    )
    with pytest.raises(SystemExit):
        check_branch_pr_metadata(
            target,
            body,
            **{**trusted_context, "issue_runner": closed_issue_runner},
        )


def make_control_checkout(root: Path, version: str, marker: str) -> str:
    root.mkdir()
    for relative_path in TRUSTED_CONTROL_SOURCES:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative_path == ".codex/hooks.json":
            path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "custom",
                                    "hooks": [{"type": "command", "command": "echo target-owned"}],
                                },
                                {
                                    "matcher": "startup|resume",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/usr/bin/python3 .evozeus-wrapper/hooks/evozeus_wrapper_start_check.py",
                                            "statusMessage": marker,
                                        }
                                    ],
                                },
                            ]
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        else:
            path.write_text(f"{marker}:{relative_path}\n", encoding="utf-8")
    for relative_path in MAINTAINER_REQUIRED_FILES:
        if relative_path == ".evozeus-wrapper/wrapper.json" or relative_path in TRUSTED_CONTROL_SOURCES:
            continue
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"base-bound:{relative_path}\n", encoding="utf-8")
    manifest = root / ".evozeus-wrapper/wrapper.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            build_wrapper_manifest(
                REPO,
                version,
                WRAPPER_MANAGED_FILES,
                [],
                instruction_surface="SKILL.md",
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        f"# Business Skill\n\n{_canonical_harness_entry_block()}\n\nBusiness instructions.\n",
        encoding="utf-8",
    )
    run(["git", "init", "-b", "main"], root)
    run(["git", "config", "user.name", "Trust Gate Test"], root)
    run(["git", "config", "user.email", "trust-gate@example.com"], root)
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "test: control checkout"], root)
    return run(["git", "rev-parse", "HEAD"], root)


def test_trusted_release_source_map_covers_every_managed_publisher_output() -> None:
    expected = set(MAINTAINER_REQUIRED_FILES) - {
        ".evozeus-wrapper/wrapper.json",
        ".evozeus-wrapper/CHANGELOG.md",
    }
    assert set(TRUSTED_CONTROL_SOURCES) == expected


def official_upgrade_runner(
    candidate: Path,
    changed_paths: set[str],
    *,
    extra_entries: tuple[dict, ...] = (),
    prerelease: bool = False,
    source_overrides: dict[str, bytes] | None = None,
):
    source_to_target = {source: target for target, source in TRUSTED_CONTROL_SOURCES.items()}

    def runner(command, **kwargs):
        endpoint = command[2]
        if "/collaborators/" in endpoint:
            payload = {
                "permission": "admin",
                "user": {"permissions": {"admin": True}},
            }
        elif endpoint.endswith("/releases/tags/v1.1.0"):
            payload = {
                "tag_name": "v1.1.0",
                "draft": False,
                "prerelease": prerelease,
                "published_at": "2026-07-31T00:00:00Z",
            }
        elif "/pulls/7/files?" in endpoint:
            payload = [
                *(
                    {
                        "filename": path,
                        "status": "added" if path.endswith("-v1.0.0-to-v1.1.0.md") else "modified",
                    }
                    for path in sorted(changed_paths)
                ),
                *extra_entries,
            ]
        else:
            source_path = endpoint.split("/contents/", 1)[1].split("?ref=", 1)[0]
            candidate_content = (candidate / source_to_target[source_path]).read_bytes()
            if source_path == "templates/target/.codex/hooks.json":
                candidate_hooks = json.loads(candidate_content)
                wrapper_entry = candidate_hooks["hooks"]["SessionStart"][-1]
                candidate_content = (
                    json.dumps({"hooks": {"SessionStart": [wrapper_entry]}}, indent=2) + "\n"
                ).encode("utf-8")
            content = (source_overrides or {}).get(source_path, candidate_content)
            payload = {
                "content": base64.b64encode(content).decode("ascii")
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    return runner


def make_upgrade_checkouts(tmp_path: Path) -> tuple[Path, Path, str, str, set[str]]:
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    base_sha = make_control_checkout(trusted, "v1.0.0", "base")
    make_control_checkout(candidate, "v1.1.0", "official")
    migration_path = ".evozeus-wrapper/docs/migrations/2026-07-31-v1.0.0-to-v1.1.0.md"
    migration = candidate / migration_path
    migration.parent.mkdir(parents=True, exist_ok=True)
    migration.write_text(
        "# Harness Refresh\n\n- Wrapper 版本：v1.0.0 -> v1.1.0\n\n## 验证\n\n- passed\n\n## 回滚\n\n- revert\n",
        encoding="utf-8",
    )
    run(["git", "add", migration_path], candidate)
    run(["git", "commit", "-m", "test: add migration record"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    changed_paths = {
        *TRUSTED_CONTROL_SOURCES,
        ".evozeus-wrapper/wrapper.json",
        migration_path,
    }
    return trusted, candidate, base_sha, head_sha, changed_paths


def test_trusted_control_gate_accepts_official_admin_upgrade(tmp_path: Path) -> None:
    trusted, candidate, base_sha, head_sha, changed_paths = make_upgrade_checkouts(tmp_path)
    mode = check_trusted_pr_checkouts(
        candidate,
        trusted,
        github_head_sha=head_sha,
        github_base_sha=base_sha,
        github_repository=REPO,
        github_head_repo=REPO,
        github_actor="alice",
        github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
        github_pr_number=7,
        github_api_runner=official_upgrade_runner(candidate, changed_paths),
    )
    assert mode == "official_harness_upgrade"


def test_trusted_control_gate_accepts_real_publisher_managed_output(tmp_path: Path) -> None:
    trusted, candidate, base_sha, _, changed_paths = make_upgrade_checkouts(tmp_path)
    manifest = json.loads((candidate / ".evozeus-wrapper/wrapper.json").read_text(encoding="utf-8"))
    replacements = {
        "SKILL_NAME": "example-skill",
        "REPO_NAME": REPO,
        "REPO_URL": f"https://github.com/{REPO}",
        "CURRENT_VERSION": "v0.1.0",
        "INITIAL_VERSION": "v0.1.0",
        "DATE": manifest["applied_at"],
        "VISIBILITY": "public",
        "WRAPPER_VERSION": "v1.1.0",
    }
    source_overrides = {
        source_path: (ROOT / source_path).read_bytes()
        for source_path in TRUSTED_CONTROL_SOURCES.values()
    }
    for relative_path, source_path in TRUSTED_CONTROL_SOURCES.items():
        source_text = source_overrides[source_path].decode("utf-8")
        for key, value in replacements.items():
            source_text = source_text.replace(f"{{{{{key}}}}}", value)
        destination = candidate / relative_path
        if relative_path == ".codex/hooks.json":
            trusted_hooks = json.loads((trusted / relative_path).read_text(encoding="utf-8"))
            official_entry = json.loads(source_text)["hooks"]["SessionStart"][0]
            preserved = trusted_hooks["hooks"]["SessionStart"][:-1]
            rendered = copy.deepcopy(trusted_hooks)
            rendered["hooks"]["SessionStart"] = [*preserved, official_entry]
            destination.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
        else:
            destination.write_text(source_text, encoding="utf-8")
    run(["git", "add", "."], candidate)
    run(["git", "commit", "-m", "test: render official managed output"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)

    mode = check_trusted_pr_checkouts(
        candidate,
        trusted,
        github_head_sha=head_sha,
        github_base_sha=base_sha,
        github_repository=REPO,
        github_head_repo=REPO,
        github_actor="alice",
        github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
        github_pr_number=7,
        github_api_runner=official_upgrade_runner(
            candidate,
            changed_paths,
            source_overrides=source_overrides,
        ),
    )
    assert mode == "official_harness_upgrade"


def test_trusted_control_gate_rejects_fork_copy_of_official_upgrade(tmp_path: Path) -> None:
    trusted, candidate, base_sha, head_sha, changed_paths = make_upgrade_checkouts(tmp_path)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo="alice/example-skill",
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(candidate, changed_paths),
        )


def test_trusted_control_gate_rejects_prerelease_upgrade(tmp_path: Path) -> None:
    trusted, candidate, base_sha, head_sha, changed_paths = make_upgrade_checkouts(tmp_path)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(
                candidate,
                changed_paths,
                prerelease=True,
            ),
        )


def test_trusted_control_gate_rejects_upgrade_manifest_identity_change(tmp_path: Path) -> None:
    trusted, candidate, base_sha, _, changed_paths = make_upgrade_checkouts(tmp_path)
    manifest = candidate / ".evozeus-wrapper/wrapper.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["canonical_repo"] = "mallory/other-skill"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    run(["git", "add", str(manifest.relative_to(candidate))], candidate)
    run(["git", "commit", "-m", "test: change target identity"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(candidate, changed_paths),
        )


@pytest.mark.parametrize(
    "mutation",
    ["drop_managed_file", "weaken_hook_registration", "extra_top_level_field"],
)
def test_trusted_control_gate_rejects_noncanonical_upgrade_manifest(
    tmp_path: Path,
    mutation: str,
) -> None:
    trusted, candidate, base_sha, _, changed_paths = make_upgrade_checkouts(tmp_path)
    manifest = candidate / ".evozeus-wrapper/wrapper.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if mutation == "drop_managed_file":
        data["managed_files"].remove(".github/workflows/evozeus-wrapper-preflight.yml")
    elif mutation == "weaken_hook_registration":
        data["hook_registration"]["codex"].pop("trust_review")
    else:
        data["candidate_defined_trust"] = True
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(["git", "add", str(manifest.relative_to(candidate))], candidate)
    run(["git", "commit", "-m", f"test: {mutation}"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(candidate, changed_paths),
        )


def test_trusted_control_gate_rejects_official_upgrade_with_business_file(tmp_path: Path) -> None:
    trusted, candidate, base_sha, head_sha, changed_paths = make_upgrade_checkouts(tmp_path)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(candidate, changed_paths | {"business.py"}),
        )


def test_trusted_control_gate_rejects_business_mutation_in_instruction_surface(tmp_path: Path) -> None:
    trusted, candidate, base_sha, _, changed_paths = make_upgrade_checkouts(tmp_path)
    surface = candidate / "SKILL.md"
    surface.write_text(
        surface.read_text(encoding="utf-8").replace("Business instructions.", "Changed business instructions."),
        encoding="utf-8",
    )
    run(["git", "add", "SKILL.md"], candidate)
    run(["git", "commit", "-m", "test: mutate business surface"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(candidate, changed_paths | {"SKILL.md"}),
        )


def test_trusted_control_gate_rejects_renamed_business_file(tmp_path: Path) -> None:
    trusted, candidate, base_sha, head_sha, changed_paths = make_upgrade_checkouts(tmp_path)
    rename = {
        "filename": ".evozeus-wrapper/docs/migrations/2026-07-31-v1.0.0-to-v1.1.0.md",
        "previous_filename": "business.py",
        "status": "renamed",
    }
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(
                candidate,
                changed_paths,
                extra_entries=(rename,),
            ),
        )


def test_trusted_control_gate_rejects_business_pr_tampering(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    base_sha = make_control_checkout(trusted, "v1.0.0", "base")
    make_control_checkout(candidate, "v1.0.0", "base")
    tampered = candidate / ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py"
    tampered.write_text("untrusted candidate validator\n", encoding="utf-8")
    run(["git", "add", str(tampered.relative_to(candidate))], candidate)
    run(["git", "commit", "-m", "test: tamper validator"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)

    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref=TARGET_BRANCH,
            github_pr_number=7,
            github_api_runner=lambda *args, **kwargs: pytest.fail("business tamper must fail before API lookup"),
        )


def test_trusted_control_gate_rejects_business_manifest_tampering(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    base_sha = make_control_checkout(trusted, "v1.0.0", "base")
    make_control_checkout(candidate, "v1.0.0", "base")
    manifest = candidate / ".evozeus-wrapper/wrapper.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["canonical_repo"] = "mallory/other-skill"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    run(["git", "add", str(manifest.relative_to(candidate))], candidate)
    run(["git", "commit", "-m", "test: tamper manifest"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref=TARGET_BRANCH,
            github_pr_number=7,
            github_api_runner=lambda *args, **kwargs: pytest.fail("business tamper must fail before API lookup"),
        )


def test_trusted_control_gate_rejects_symlinked_business_changelog(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    base_sha = make_control_checkout(trusted, "v1.0.0", "base")
    make_control_checkout(candidate, "v1.0.0", "base")
    outside = tmp_path / "outside-changelog.md"
    outside.write_text("# Changelog\n\n## Unreleased\n\n- external\n", encoding="utf-8")
    changelog = candidate / ".evozeus-wrapper/CHANGELOG.md"
    changelog.unlink()
    changelog.symlink_to(outside)
    run(["git", "add", str(changelog.relative_to(candidate))], candidate)
    run(["git", "commit", "-m", "test: symlink changelog"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)

    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref=TARGET_BRANCH,
            github_pr_number=7,
            github_api_runner=lambda *args, **kwargs: pytest.fail("symlink must fail before API lookup"),
        )


def test_trusted_control_gate_rejects_business_hooks_tampering(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    base_sha = make_control_checkout(trusted, "v1.0.0", "base")
    make_control_checkout(candidate, "v1.0.0", "base")
    hooks = candidate / ".codex/hooks.json"
    hooks.write_text('{"hooks":{"SessionStart":[]}}\n', encoding="utf-8")
    run(["git", "add", str(hooks.relative_to(candidate))], candidate)
    run(["git", "commit", "-m", "test: weaken hooks"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref=TARGET_BRANCH,
            github_pr_number=7,
            github_api_runner=lambda *args, **kwargs: pytest.fail("business tamper must fail before API lookup"),
        )


def test_trusted_control_gate_rejects_hooks_not_bound_to_official_release(tmp_path: Path) -> None:
    trusted, candidate, base_sha, head_sha, changed_paths = make_upgrade_checkouts(tmp_path)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(
                candidate,
                changed_paths,
                source_overrides={"templates/target/.codex/hooks.json": b"unofficial hooks\n"},
            ),
        )


def test_trusted_control_gate_rejects_stale_unrefreshed_managed_file(tmp_path: Path) -> None:
    trusted, candidate, base_sha, _, changed_paths = make_upgrade_checkouts(tmp_path)
    relative_path = ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py"
    (candidate / relative_path).write_bytes((trusted / relative_path).read_bytes())
    run(["git", "add", relative_path], candidate)
    run(["git", "commit", "-m", "test: leave managed file stale"], candidate)
    head_sha = run(["git", "rev-parse", "HEAD"], candidate)
    with pytest.raises(SystemExit):
        check_trusted_pr_checkouts(
            candidate,
            trusted,
            github_head_sha=head_sha,
            github_base_sha=base_sha,
            github_repository=REPO,
            github_head_repo=REPO,
            github_actor="alice",
            github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
            github_pr_number=7,
            github_api_runner=official_upgrade_runner(
                candidate,
                changed_paths - {relative_path},
                source_overrides={
                    "scripts/evozeus_wrapper_preflight.py": b"official refreshed preflight\n"
                },
            ),
        )


def test_official_upgrade_profile_skips_contributor_plan_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "candidate"
    trusted = tmp_path / "trusted"
    target.mkdir()
    trusted.mkdir()
    monkeypatch.setattr(
        "scripts.evozeus_wrapper_preflight.check_trusted_pr_checkouts",
        lambda *args, **kwargs: "official_harness_upgrade",
    )
    monkeypatch.setattr(
        "scripts.evozeus_wrapper_preflight.load_wrapper_manifest",
        lambda _target: {"wrapper_version": "v1.1.0"},
    )
    monkeypatch.setattr("scripts.evozeus_wrapper_preflight.check_maintainer", lambda *args, **kwargs: None)
    args = SimpleNamespace(
        target=str(target),
        pr_body=str(tmp_path / "publisher-body-without-contributor-plan.md"),
        trusted_root=str(trusted),
        github_head_sha="1" * 40,
        github_base_sha="2" * 40,
        github_repository=REPO,
        github_head_repo=REPO,
        github_actor="alice",
        github_head_ref="evozeus/harness-v1.0.0-to-v1.1.0",
        github_pr_number=7,
    )
    check_pr(args)


@pytest.mark.parametrize(
    ("returncode", "blockers", "expected_code"),
    [
        (2, [], "planner_output_invalid"),
        (0, [{"code": "synthetic", "message": "blocked"}], "planner_output_invalid"),
        (3, [], "planner_failed"),
    ],
)
def test_planner_exit_and_blockers_must_agree_without_stderr_leakage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    blockers: list[dict],
    expected_code: str,
) -> None:
    secret = "PRIVATE-PLANNER-STDERR"
    monkeypatch.setattr(
        "scripts.evozeus_branch_consumer.verify_managed_snapshot",
        lambda _root=None: {"planner_path": tmp_path / "planner.mjs"},
    )
    monkeypatch.setattr(
        "scripts.evozeus_branch_consumer.shutil.which",
        lambda _command, path=None: "/usr/bin/node",
    )
    monkeypatch.setattr(
        "scripts.evozeus_branch_consumer.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], returncode, json.dumps({"blockers": blockers}), secret
        ),
    )

    with pytest.raises(ConsumerError) as caught:
        run_core_planner(
            {"profile": "coevolve_target_skillware_consumer"},
            ledger_root=tmp_path / "ledger",
            approve_save_plan=False,
        )
    report = error_report(caught.value)
    assert caught.value.code == expected_code
    assert secret not in str(caught.value)
    assert secret not in json.dumps(report)


def test_planner_timeout_is_stable_and_does_not_expose_process_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "PRIVATE-TIMEOUT-OUTPUT"
    monkeypatch.setattr(
        "scripts.evozeus_branch_consumer.verify_managed_snapshot",
        lambda _root=None: {"planner_path": tmp_path / "planner.mjs"},
    )
    monkeypatch.setattr(
        "scripts.evozeus_branch_consumer.shutil.which",
        lambda _command, path=None: "/usr/bin/node",
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output=secret, stderr=secret)

    monkeypatch.setattr("scripts.evozeus_branch_consumer.subprocess.run", timeout)
    with pytest.raises(ConsumerError) as caught:
        run_core_planner(
            {"profile": "coevolve_target_skillware_consumer"},
            ledger_root=tmp_path / "ledger",
            approve_save_plan=False,
            timeout_seconds=0.01,
        )
    assert caught.value.code == "planner_timeout"
    assert secret not in str(caught.value)
    assert secret not in json.dumps(error_report(caught.value))
