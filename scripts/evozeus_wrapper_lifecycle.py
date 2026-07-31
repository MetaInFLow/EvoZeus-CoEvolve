#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    from .evozeus_wrapper_global_hook import read_global_hook_status
    from .evozeus_notice import load_notice_policy, render_notice
except ImportError:
    from evozeus_wrapper_global_hook import read_global_hook_status
    from evozeus_notice import load_notice_policy, render_notice

try:
    from .evozeus_branch_consumer import ConsumerError as BranchConsumerError
    from .evozeus_branch_consumer import verify_managed_snapshot
except ImportError:
    from evozeus_branch_consumer import ConsumerError as BranchConsumerError
    from evozeus_branch_consumer import verify_managed_snapshot


STAGE_LABELS = {
    "environment": "[1/5] Environment Diagnosis",
    "target_skill": "[2/5] Target Skill Diagnosis",
    "transform": "[3/5] Target Skill Transform",
    "publish": "[4/5] Publish & Reinstall",
    "loop": "[5/5] Continuous Evolution Loop",
}
CONTRIBUTOR_GATE_REQUIRED_CHECK = "EvoZeus Contributor Gate"
GITHUB_ACTIONS_APP_ID = 15368

GLOBAL_EVOZEUS_HOME = ".evozeus"
GLOBAL_EVOZEUS_PROJECTS_DIR = ".projects"
TARGET_EVOINFRA_DIR = ".evozeus-wrapper"
LEGACY_TARGET_EVOINFRA_DIR = ".evozeus_evoinfra"
OLDEST_TARGET_EVOINFRA_DIR = ".evozeus"
TARGET_WRAPPER_MANIFEST = f"{TARGET_EVOINFRA_DIR}/wrapper.json"
LEGACY_TARGET_WRAPPER_MANIFEST = f"{LEGACY_TARGET_EVOINFRA_DIR}/wrapper.json"
OLDEST_TARGET_WRAPPER_MANIFEST = f"{OLDEST_TARGET_EVOINFRA_DIR}/wrapper.json"
TARGET_CHANGELOG = f"{TARGET_EVOINFRA_DIR}/CHANGELOG.md"
TARGET_WRAPPER_GUIDE = f"{TARGET_EVOINFRA_DIR}/WRAPPER.md"
TARGET_FEEDBACK_POLICY = f"{TARGET_EVOINFRA_DIR}/policies/feedback-policy.json"
TARGET_AUDIT_RULE = f"{TARGET_EVOINFRA_DIR}/policies/audit-rule.md"
TARGET_NOTICE_POLICY = f"{TARGET_EVOINFRA_DIR}/policies/notice-policy.json"
LEGACY_TARGET_FEEDBACK_POLICY = f"{LEGACY_TARGET_EVOINFRA_DIR}/feedback-policy.json"
LEGACY_TARGET_AUDIT_RULE = f"{LEGACY_TARGET_EVOINFRA_DIR}/audit-rule.md"
OLDEST_TARGET_FEEDBACK_POLICY = f"{OLDEST_TARGET_EVOINFRA_DIR}/feedback-policy.json"
OLDEST_TARGET_AUDIT_RULE = f"{OLDEST_TARGET_EVOINFRA_DIR}/audit-rule.md"
CODEX_HOOKS_CONFIG = ".codex/hooks.json"
CODEX_START_HOOK_SCRIPT = f"{TARGET_EVOINFRA_DIR}/hooks/evozeus_wrapper_start_check.py"
CODEX_START_HOOK_EVENT = "SessionStart"
CODEX_START_HOOK_MATCHER = "startup|resume"
TARGET_DASHBOARD_INDEX = f"{TARGET_EVOINFRA_DIR}/docs/index.md"
TARGET_DASHBOARD_CONFIG = f"{TARGET_EVOINFRA_DIR}/docs/_config.yml"
TARGET_DESIGN_TEMPLATE = f"{TARGET_EVOINFRA_DIR}/docs/design-doc-template.md"
TARGET_DESIGNS_README = f"{TARGET_EVOINFRA_DIR}/docs/designs/README.md"
TARGET_MIGRATIONS_README = f"{TARGET_EVOINFRA_DIR}/docs/migrations/README.md"
TARGET_ONBOARDING_GUIDE = f"{TARGET_EVOINFRA_DIR}/docs/onboarding.md"
TARGET_PREFLIGHT_SCRIPT = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus_wrapper_preflight.py"
TARGET_NOTICE_SCRIPT = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus_notice.py"
TARGET_BRANCH_CONSUMER_SCRIPT = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus_branch_consumer.py"
TARGET_BRANCH_CONTRACT = f"{TARGET_EVOINFRA_DIR}/contracts/v1/contributor-branch-contract.json"
TARGET_BRANCH_PROVENANCE = f"{TARGET_EVOINFRA_DIR}/contracts/v1/contributor-branch-provenance.json"
TARGET_BRANCH_PLANNER = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus-branch-preflight.mjs"
TARGET_HARNESS_SKILL = f"{TARGET_EVOINFRA_DIR}/skills/using-evozeus-harness/SKILL.md"
HARNESS_SKILL_VERSION = "v1.1.0"
HARNESS_ENTRY_BEGIN = "<!-- evozeus-harness-entry:v1 -->"
HARNESS_ENTRY_END = "<!-- /evozeus-harness-entry -->"
PR_TEMPLATE_PATH = ".github/pull_request_template.md"
PR_PLAN_BEGIN = "<!-- evozeus-contributor-branch-plan:v1 -->"
PR_PLAN_END = "<!-- /evozeus-contributor-branch-plan -->"
PR_PLAN_HEADING = "## Contributor Branch Plan"
PR_TEMPLATE_MANAGED_BASELINE_SHA256 = frozenset(
    {
        "665b9f164a9174aef97a2a664de1a1f5bb8616069833e86f6fe7a4d62fbd8926",
        "1b236cfb74c7c02d66aefa301f0a6ca264120587dd8f0a5e22efb152e6973de4",
        "b0764492f1d39035f7cb4ec8f82b3e66f42b032a5607f14390011d9a0fbc6bd0",
        "74540cf6a2fc343efd632bfc4a6027831f317ca055900f2033bd5b7c954b693e",
        "639527014104f356b03d6dc659a692113e33dfd2662728e05f0c9c0030e8c52f",
        "82abdbe67712bf623c128184f94f0f43eeee7c35e1a9e7051e0ec9b3b13344e5",
    }
)
HARNESS_SKILL_REQUIRED_TERMS = (
    TARGET_WRAPPER_MANIFEST,
    TARGET_NOTICE_POLICY,
    "prompt_runtime_check",
    "bootstrap_skill",
    "integration.capabilities",
    "SkillInvoke",
    "runtime-only install",
    "doctor --target .",
    "identity --json",
    "Feedback Issue",
    "Issue-to-PR",
    "Harness 维护",
    "UAT",
    "Release",
    "rollback",
    "普通 Skill 调用不授权",
    "evozeus_branch_consumer.py",
    "--approve-save-plan",
    "issue_evidence",
    "permission_evidence",
    "pull_request_target",
    "EvoZeus Contributor Gate",
    "evozeus/harness-vX-to-vY",
    "隔离 worktree",
)

REQUIRED_WRAPPER_FILES = [
    TARGET_CHANGELOG,
    TARGET_WRAPPER_GUIDE,
    TARGET_WRAPPER_MANIFEST,
    TARGET_FEEDBACK_POLICY,
    TARGET_AUDIT_RULE,
    TARGET_NOTICE_POLICY,
    CODEX_HOOKS_CONFIG,
    CODEX_START_HOOK_SCRIPT,
    TARGET_DASHBOARD_INDEX,
    TARGET_DASHBOARD_CONFIG,
    TARGET_DESIGN_TEMPLATE,
    TARGET_DESIGNS_README,
    TARGET_MIGRATIONS_README,
    TARGET_ONBOARDING_GUIDE,
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/skill-feedback.yml",
    ".github/pull_request_template.md",
    ".github/workflows/evozeus-wrapper-preflight.yml",
    TARGET_PREFLIGHT_SCRIPT,
    TARGET_NOTICE_SCRIPT,
    TARGET_BRANCH_CONSUMER_SCRIPT,
    TARGET_BRANCH_CONTRACT,
    TARGET_BRANCH_PROVENANCE,
    TARGET_BRANCH_PLANNER,
    TARGET_HARNESS_SKILL,
]

WRAPPER_MANAGED_FILES = [
    TARGET_CHANGELOG,
    TARGET_WRAPPER_GUIDE,
    TARGET_FEEDBACK_POLICY,
    TARGET_AUDIT_RULE,
    TARGET_NOTICE_POLICY,
    CODEX_HOOKS_CONFIG,
    CODEX_START_HOOK_SCRIPT,
    TARGET_DASHBOARD_INDEX,
    TARGET_DASHBOARD_CONFIG,
    TARGET_DESIGN_TEMPLATE,
    TARGET_DESIGNS_README,
    TARGET_MIGRATIONS_README,
    TARGET_ONBOARDING_GUIDE,
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/skill-feedback.yml",
    ".github/pull_request_template.md",
    ".github/workflows/evozeus-wrapper-preflight.yml",
    TARGET_PREFLIGHT_SCRIPT,
    TARGET_NOTICE_SCRIPT,
    TARGET_BRANCH_CONSUMER_SCRIPT,
    TARGET_BRANCH_CONTRACT,
    TARGET_BRANCH_PROVENANCE,
    TARGET_BRANCH_PLANNER,
    TARGET_HARNESS_SKILL,
]

LEGACY_LAYOUT_FILE_MAP = (
    ("CHANGELOG.md", TARGET_CHANGELOG),
    ("WRAPPER.md", TARGET_WRAPPER_GUIDE),
    ("docs/index.md", TARGET_DASHBOARD_INDEX),
    ("docs/_config.yml", TARGET_DASHBOARD_CONFIG),
    ("docs/design-doc-template.md", TARGET_DESIGN_TEMPLATE),
    ("scripts/evozeus_wrapper_preflight.py", TARGET_PREFLIGHT_SCRIPT),
    (".codex/hooks/evozeus_wrapper_start_check.py", CODEX_START_HOOK_SCRIPT),
    (LEGACY_TARGET_FEEDBACK_POLICY, TARGET_FEEDBACK_POLICY),
    (LEGACY_TARGET_AUDIT_RULE, TARGET_AUDIT_RULE),
    (OLDEST_TARGET_FEEDBACK_POLICY, TARGET_FEEDBACK_POLICY),
    (OLDEST_TARGET_AUDIT_RULE, TARGET_AUDIT_RULE),
)
LEGACY_LAYOUT_TREE_MAP = (
    ("docs/designs", f"{TARGET_EVOINFRA_DIR}/docs/designs"),
    ("docs/wrapper-migrations", f"{TARGET_EVOINFRA_DIR}/docs/migrations"),
)

WRAPPER_REPO = "MetaInFLow/EvoZeus-CoEvolve"
INITIAL_SKILL_VERSION = "v0.1.0"
VERSION_HEADER_RE = re.compile(r"^##\s+\[?(v\d+\.\d+\.\d+)\]?\b", re.MULTILINE)
STATUS_SECTION_HEADING = "## EvoZeus-CoEvolve 状态检查"
LEGACY_STATUS_SECTION_HEADING = "## EvoZeus-wrapper 状态检查"
EVOLUTION_SECTION_HEADING = "## 自进化方法"
WRAPPER_SECTION_HEADING = "## EvoZeus-CoEvolve"
LEGACY_WRAPPER_SECTION_HEADING = "## EvoZeus-wrapper"
WRAPPER_MIGRATION_README = TARGET_MIGRATIONS_README
CONTROL_SKILL_NAME_TOKENS = (
    "bootstrap",
    "control",
    "controller",
    "entry",
    "index",
    "init",
    "loader",
    "loading",
    "orchestrator",
    "router",
    "routing",
    "runtime",
    "session",
    "start",
    "startup",
)
CONTROL_SKILL_TEXT_TERMS = (
    "available skills",
    "bootstrap",
    "control",
    "hook",
    "invoke",
    "load skills",
    "loaded by",
    "plugin",
    "route",
    "routing",
    "session start",
    "session-start",
    "skill usage",
    "startup",
    "启动",
    "入口",
    "加载",
    "路由",
    "控制",
)
FEEDBACK_CAPTURE_TERMS = (
    "不满意",
    "不对",
    "错了",
    "有问题",
    "问题",
    "缺陷",
    "为什么",
    "没有",
    "没",
    "应该",
    "期望",
    "纠正",
    "wrong",
    "bug",
    "issue",
    "missing",
    "broken",
    "defect",
)
WRAPPER_ROUTE_TERMS = (
    "evozeus",
    "wrapper",
    "harness",
    "hook",
    "release",
    "版本",
    "发布",
    "issue",
    "回收",
    "检测",
    "skill",
    "preflight",
)
TARGET_ROUTE_TERMS = (
    "大兴",
    "飞书",
    "feishu",
    "base",
    "多维表",
    "需求池",
    "子任务",
    "排期",
    "状态",
    "验收",
    "进度",
)


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


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(args: list[str], cwd: Path | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "command not found"}
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def latest_changelog_tag_from_text(changelog: str) -> str | None:
    match = VERSION_HEADER_RE.search(changelog)
    return match.group(1) if match else None


def latest_changelog_tag(target: Path) -> str | None:
    changelog = target / TARGET_CHANGELOG
    if not changelog.exists():
        changelog = target / "CHANGELOG.md"
    if not changelog.exists():
        return None
    return latest_changelog_tag_from_text(changelog.read_text(encoding="utf-8"))


def read_latest_release(repo: str | None, runner=run_command) -> dict[str, Any] | None:
    if not repo:
        return None
    result = runner(["gh", "release", "view", "--repo", repo, "--json", "tagName,url,publishedAt"])
    if result["returncode"] != 0:
        return {
            "exists": False,
            "tag": None,
            "url": None,
            "published_at": None,
            "error": (result.get("stderr") or result.get("stdout") or "").strip() or "latest release not found",
        }
    try:
        data = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        return {
            "exists": False,
            "tag": None,
            "url": None,
            "published_at": None,
            "error": "could not parse latest release response",
        }
    tag = data.get("tagName")
    if not tag:
        return {
            "exists": False,
            "tag": None,
            "url": data.get("url"),
            "published_at": data.get("publishedAt"),
            "error": "latest release response has no tagName",
        }
    return {
        "exists": True,
        "tag": tag,
        "url": data.get("url"),
        "published_at": data.get("publishedAt"),
        "error": None,
    }


def resolve_latest_wrapper_release(explicit_version: str | None = None) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    if explicit_version:
        return {
            "version": explicit_version,
            "source": "explicit",
            "checked_at": checked_at,
            "url": None,
            "error": None,
        }

    release = read_latest_release(WRAPPER_REPO) or {}
    if release.get("exists") and release.get("tag"):
        return {
            "version": release["tag"],
            "source": "github_latest_release",
            "checked_at": checked_at,
            "url": release.get("url"),
            "error": None,
        }
    return {
        "version": None,
        "source": "unavailable",
        "checked_at": checked_at,
        "url": None,
        "error": release.get("error") or "GitHub latest release is unavailable",
    }


def diagnose_skill_version(
    target: Path,
    repo_exists: bool | None,
    latest_release: dict[str, Any] | None,
) -> dict[str, Any]:
    changelog_tag = latest_changelog_tag(target)

    if repo_exists is False:
        return {
            "status": "repository_required",
            "current_tag": None,
            "changelog_tag": changelog_tag,
            "latest_release_tag": None,
            "rule": "create and publish the independent repository before attaching a Harness",
            "requires_owner_choice": True,
        }

    if repo_exists is None:
        return {
            "status": "repo_state_unknown",
            "current_tag": changelog_tag,
            "changelog_tag": changelog_tag,
            "latest_release_tag": None,
            "rule": "verify GitHub repo state before choosing the Skill version",
            "requires_owner_choice": True,
        }

    latest_tag = latest_release.get("tag") if latest_release and latest_release.get("exists") else None
    if latest_tag:
        status = "adopt_existing_release"
        requires_owner_choice = False
        current_tag = latest_tag
        rule = "existing repos keep GitHub latest release as the Skill version"
        if changelog_tag:
            try:
                latest_key = version_key(latest_tag)
                changelog_key = version_key(changelog_tag)
            except ValueError:
                return {
                    "status": "invalid_version_tag",
                    "current_tag": changelog_tag or latest_tag,
                    "changelog_tag": changelog_tag,
                    "latest_release_tag": latest_tag,
                    "rule": "Skill releases must use vMAJOR.MINOR.PATCH",
                    "requires_owner_choice": True,
                }
            if changelog_key == latest_key:
                status = "local_matches_latest_release"
                current_tag = changelog_tag
            elif changelog_key < latest_key:
                status = "local_changelog_behind_release"
                current_tag = changelog_tag
            else:
                status = "local_changelog_ahead_of_release"
                current_tag = changelog_tag
        return {
            "status": status,
            "current_tag": current_tag,
            "changelog_tag": changelog_tag,
            "latest_release_tag": latest_tag,
            "rule": rule,
            "requires_owner_choice": False,
        }

    if changelog_tag:
        return {
            "status": "github_release_missing_create_from_changelog",
            "current_tag": changelog_tag,
            "changelog_tag": changelog_tag,
            "latest_release_tag": None,
            "rule": "existing repos without GitHub releases should create a release for the latest changelog tag before runtime use",
            "requires_owner_choice": False,
        }

    return {
        "status": "missing_version_requires_owner_choice",
        "current_tag": None,
        "changelog_tag": None,
        "latest_release_tag": None,
        "rule": "existing repos must not be reset to v0.1.0; choose or recover the current Skill version first",
        "requires_owner_choice": True,
    }


def command_status(args: list[str], runner=run_command) -> str:
    result = runner(args)
    return "ok" if result["returncode"] == 0 else "failed"


def diagnose_environment(home: Path = Path.home(), runner=run_command) -> dict[str, Any]:
    home = home.expanduser().resolve()
    evozeus_home = home / GLOBAL_EVOZEUS_HOME
    runtime_dir = evozeus_home / "runtime"
    projects_dir = evozeus_home / GLOBAL_EVOZEUS_PROJECTS_DIR

    git_status = command_status(["git", "--version"], runner)
    gh_status = command_status(["gh", "--version"], runner)
    gh_auth_status = command_status(["gh", "auth", "status"], runner) if gh_status == "ok" else "failed"
    mother_repo_access = "unknown"
    if gh_status == "ok" and gh_auth_status == "ok":
        mother_view = runner(["gh", "repo", "view", "MetaInFLow/EvoZeus", "--json", "nameWithOwner,url,visibility"])
        mother_repo_access = "ok" if mother_view["returncode"] == 0 else "failed"

    return {
        "stage": "environment_diagnosis",
        "next_action": "continue_to_target_repo_diagnosis" if evozeus_home.exists() else "install_evozeus",
        "evozeus_home": {
            "exists": evozeus_home.exists(),
            "path": str(evozeus_home),
            "runtime_exists": runtime_dir.exists(),
            "projects_exists": projects_dir.exists(),
            "required_action": "none" if evozeus_home.exists() else "install_evozeus",
        },
        "mother_repo": {
            "remote": "MetaInFLow/EvoZeus",
            "candidates": [],
            "canonical_path": None,
            "needs_user_choice": False,
            "remote_access": mother_repo_access,
        },
        "dependencies": {
            "git": git_status,
            "gh": gh_status,
            "gh_auth": gh_auth_status,
        },
    }


def repo_projects_pointer(home: Path, repo: str | None) -> Path | None:
    if not repo or "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return home / GLOBAL_EVOZEUS_HOME / GLOBAL_EVOZEUS_PROJECTS_DIR / owner / name


def resolve_path(path: Path) -> str | None:
    if not (path.exists() or path.is_symlink()):
        return None
    try:
        return str(path.resolve())
    except OSError:
        return None


def runtime_pointer_scope(canonical_path: Path, resolved_path: str | None) -> str | None:
    if not resolved_path:
        return None
    canonical = canonical_path.expanduser().resolve()
    resolved = Path(resolved_path).expanduser().resolve()
    if resolved == canonical:
        return "canonical_repo"
    if resolved.parent == canonical / "skills" and (resolved / "SKILL.md").is_file():
        return "canonical_subskill"
    return None


def target_canonical_path(target: Path, runner=run_command) -> str:
    git_root_result = runner(["git", "-C", str(target), "rev-parse", "--show-toplevel"])
    if git_root_result["returncode"] == 0 and git_root_result.get("stdout"):
        return str(Path(git_root_result["stdout"].strip()).expanduser().resolve())
    return str(target.expanduser().resolve())


def independent_repo_root(target: Path, runner=run_command) -> Path:
    """Return the one repository root allowed to own an Evolution Harness."""
    requested = target.expanduser().resolve()
    if not requested.is_dir():
        raise ValueError(f"target must be an existing directory: {requested}")
    result = runner(["git", "-C", str(requested), "rev-parse", "--show-toplevel"])
    if result["returncode"] != 0 or not result.get("stdout", "").strip():
        raise ValueError(
            "Evolution Harness requires an independent Git repository; "
            f"no Git repository contains target: {requested}"
        )
    root = Path(result["stdout"].strip()).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"resolved Git repository root is unavailable: {root}")
    return root


def nested_harness_manifests(repo_root: Path) -> list[str]:
    """List active Harness manifests below the repository root boundary."""
    repo_root = repo_root.expanduser().resolve()
    patterns = (
        f"**/{TARGET_EVOINFRA_DIR}/wrapper.json",
        f"**/{LEGACY_TARGET_EVOINFRA_DIR}/wrapper.json",
        f"**/{OLDEST_TARGET_EVOINFRA_DIR}/wrapper.json",
    )
    allowed_root_manifests = {
        repo_root / TARGET_WRAPPER_MANIFEST,
        repo_root / LEGACY_TARGET_WRAPPER_MANIFEST,
        repo_root / OLDEST_TARGET_WRAPPER_MANIFEST,
    }
    found: set[str] = set()
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if path in allowed_root_manifests:
                continue
            if ".git" in path.parts:
                continue
            found.add(path.relative_to(repo_root).as_posix())
    return sorted(found)


def resolve_harness_target(target: Path, runner=run_command) -> dict[str, Any]:
    """Normalize any path inside a repo to the repo-owned Harness boundary."""
    requested = target.expanduser().resolve()
    repo_root = independent_repo_root(requested, runner)
    nested = nested_harness_manifests(repo_root)
    return {
        "requested_target": str(requested),
        "repo_root": str(repo_root),
        "requested_is_repo_root": requested == repo_root,
        "nested_harness_manifests": nested,
        "eligible": not nested,
        "rule": "Only an independent Git repository root may own one Evolution Harness.",
    }


def git_origin_repo(path: Path, runner=run_command) -> str | None:
    remote_result = runner(["git", "-C", str(path), "remote", "get-url", "origin"])
    if remote_result["returncode"] != 0:
        return None
    return repo_from_remote(remote_result.get("stdout", ""))


def require_repo_admin(
    target: Path,
    expected_repo: str | None = None,
    runner=run_command,
) -> dict[str, Any]:
    """Require administrator authority before any Harness mutation or upload."""
    repo_root = independent_repo_root(target, runner)
    origin_repo = git_origin_repo(repo_root, runner)
    if not origin_repo:
        raise ValueError("Harness maintenance requires a GitHub origin on the target repository")
    if expected_repo and origin_repo.lower() != expected_repo.lower():
        raise ValueError(
            "target GitHub origin does not match the requested repository: "
            f"origin={origin_repo}; requested={expected_repo}"
        )
    result = runner(
        [
            "gh",
            "repo",
            "view",
            origin_repo,
            "--json",
            "nameWithOwner,viewerPermission,url,visibility,defaultBranchRef",
        ]
    )
    if result["returncode"] != 0:
        detail = (result.get("stderr") or result.get("stdout") or "GitHub access check failed").strip()
        raise ValueError(f"cannot verify target repository administrator authority: {detail}")
    data = parse_repo_view(result.get("stdout") or "")
    permission = data.get("viewerPermission")
    if permission != "ADMIN":
        raise ValueError(
            "Harness mutation and upload require ADMIN permission on the target repository; "
            f"current permission={permission or 'unknown'}"
        )
    return {
        "repo_root": str(repo_root),
        "repository": data.get("nameWithOwner") or origin_repo,
        "url": data.get("url"),
        "visibility": data.get("visibility"),
        "default_branch": (data.get("defaultBranchRef") or {}).get("name"),
        "viewer_permission": permission,
        "verified": True,
    }


def require_contributor_gate_protection(
    repository: str,
    default_branch: str | None,
    runner=run_command,
) -> dict[str, Any]:
    """Fail closed unless the default branch requires the trusted PR gate context."""
    if not default_branch:
        raise ValueError("cannot verify Contributor Gate protection without a GitHub default branch")
    endpoint = (
        f"repos/{repository}/branches/{quote(default_branch, safe='')}/"
        "protection/required_status_checks"
    )
    result = runner(["gh", "api", endpoint, "--hostname", "github.com"])
    if result["returncode"] != 0:
        raise ValueError(
            "cannot verify default-branch required status checks; configure "
            f"{CONTRIBUTOR_GATE_REQUIRED_CHECK!r} on {default_branch} before attaching the Harness"
        )
    try:
        data = json.loads(result.get("stdout") or "")
    except json.JSONDecodeError as exc:
        raise ValueError("default-branch required status check evidence is invalid") from exc
    if not isinstance(data, dict):
        raise ValueError("default-branch required status check evidence is invalid")
    checks = data.get("checks", [])
    trusted_check = next(
        (
            item
            for item in checks
            if isinstance(item, dict)
            and item.get("context") == CONTRIBUTOR_GATE_REQUIRED_CHECK
            and item.get("app_id") == GITHUB_ACTIONS_APP_ID
        ),
        None,
    ) if isinstance(checks, list) else None
    if trusted_check is None:
        raise ValueError(
            f"default branch {default_branch} does not require {CONTRIBUTOR_GATE_REQUIRED_CHECK!r} "
            f"from GitHub Actions app_id={GITHUB_ACTIONS_APP_ID}; configure the exact bound check "
            "before attaching the Harness"
        )
    return {
        "schema_version": "evozeus.coevolve.required-check-evidence.v1",
        "repository": repository,
        "branch": default_branch,
        "context": CONTRIBUTOR_GATE_REQUIRED_CHECK,
        "app_id": GITHUB_ACTIONS_APP_ID,
        "source": "github_branch_protection_api",
        "verified": True,
        "writes": False,
    }


def describe_install_path(path: Path, target: Path) -> dict[str, Any]:
    target_skill_hash = file_sha256(target / "SKILL.md")
    install_skill_hash = file_sha256(path / "SKILL.md")
    resolved = resolve_path(path)
    return {
        "path": str(path),
        "kind": path_kind(path),
        "resolved_path": resolved,
        "has_skill_md": (path / "SKILL.md").exists(),
        "skill_md_hash": install_skill_hash,
        "matches_target_skill_md": bool(target_skill_hash and install_skill_hash and target_skill_hash == install_skill_hash),
    }


def diagnose_harness_state(target: Path) -> dict[str, Any]:
    manifest_status = wrapper_manifest_status(target)
    required_files = REQUIRED_WRAPPER_FILES + [TARGET_WRAPPER_MANIFEST]
    present = [rel for rel in required_files if (target / rel).exists()]
    missing = [rel for rel in required_files if not (target / rel).exists()]
    legacy_present = sorted(
        str(path.relative_to(target))
        for paths in _legacy_layout_sources(target).values()
        for path in paths
    )
    if manifest_status["legacy_manifest_detected"] and not manifest_status["current_manifest_detected"]:
        present.extend(manifest_status["legacy_manifest_paths"])
    manifest = load_wrapper_manifest(target, allow_legacy=True)
    if manifest_status["migration_required"] or legacy_present:
        state = "migration_required"
    elif not present:
        state = "missing"
    elif not missing:
        state = "complete"
    else:
        state = "partial"
    return {
        "state": state,
        "present_files": present,
        "legacy_files": legacy_present,
        "missing_files": missing,
        "wrapper_version": manifest.get("wrapper_version") if manifest else None,
        **manifest_status,
    }


def diagnose_repo_state(target: Path, repo: str | None, home: Path, workspace_roots: list[Path], runner=run_command) -> dict[str, Any]:
    exists_on_github: bool | None = None
    latest_release: dict[str, Any] | None = None
    repo_info: dict[str, Any] = {}
    access: dict[str, Any] = {
        "checked": bool(repo),
        "status": "not_requested" if not repo else "unknown",
        "viewer_permission": None,
        "can_read": False,
        "can_write": False,
        "can_admin": False,
    }
    if repo:
        view = runner(
            [
                "gh",
                "repo",
                "view",
                repo,
                "--json",
                "nameWithOwner,url,visibility,viewerPermission,defaultBranchRef",
            ]
        )
        exists_on_github = view["returncode"] == 0
        if exists_on_github:
            repo_info = parse_repo_view(view.get("stdout") or "")
            permission = repo_info.get("viewerPermission")
            access = {
                "checked": True,
                "status": "ok",
                "viewer_permission": permission,
                "can_read": True,
                "can_write": permission in {"ADMIN", "MAINTAIN", "WRITE"},
                "can_admin": permission == "ADMIN",
            }
            latest_release = read_latest_release(repo, runner)
        else:
            access = {
                "checked": True,
                "status": "failed",
                "viewer_permission": None,
                "can_read": False,
                "can_write": False,
                "can_admin": False,
                "error": (view.get("stderr") or view.get("stdout") or "").strip() or "repo access check failed",
            }

    git_root = None
    git_root_result = runner(["git", "-C", str(target), "rev-parse", "--show-toplevel"])
    if git_root_result["returncode"] == 0 and git_root_result.get("stdout"):
        git_root = git_root_result["stdout"].strip()

    pointer = repo_projects_pointer(home, repo)
    candidates = []
    if git_root:
        candidates.append(git_root)
    if pointer and (pointer.exists() or pointer.is_symlink()):
        candidates.append(str(pointer))
    for root in workspace_roots:
        if root.exists():
            candidates.append(str(root.expanduser().resolve()))

    unique_candidates = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    return {
        "name": repo,
        "exists_on_github": exists_on_github,
        "info": repo_info,
        "visibility": repo_info.get("visibility"),
        "default_branch": (repo_info.get("defaultBranchRef") or {}).get("name"),
        "access": access,
        "latest_release": latest_release,
        "candidates": unique_candidates,
        "canonical_path": unique_candidates[0] if len(unique_candidates) == 1 else None,
        "needs_user_choice": len(unique_candidates) > 1,
        "projects_pointer": str(pointer) if pointer else None,
    }


def parse_repo_view(stdout: str) -> dict[str, Any]:
    if not stdout.strip():
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def skill_entries(target: Path) -> list[dict[str, Any]]:
    skills_dir = target / "skills"
    if not skills_dir.exists():
        return []
    entries = []
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        entries.append(
            {
                "path": str(path.relative_to(target)),
                "directory": str(path.parent.relative_to(target)),
                "name": skill_name_from_skill_md(path) or path.parent.name,
            }
        )
    return entries


def existing_relative_files(target: Path, paths: list[str]) -> list[str]:
    return [path for path in paths if (target / path).is_file()]


def plugin_manifest_files(target: Path) -> list[str]:
    candidates = [
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        ".kimi-plugin/plugin.json",
        ".opencode/INSTALL.md",
        "gemini-extension.json",
        "package.json",
    ]
    return existing_relative_files(target, candidates)


def hook_files(target: Path) -> list[str]:
    hooks = existing_relative_files(
        target,
        [
            CODEX_HOOKS_CONFIG,
            ".codex/config.toml",
            CODEX_START_HOOK_SCRIPT,
        ],
    )
    hooks_dir = target / "hooks"
    if hooks_dir.is_dir():
        hooks.extend(
            str(path.relative_to(target))
            for path in sorted(hooks_dir.iterdir())
            if path.is_file()
        )
    return list(dict.fromkeys(hooks))


def classify_integration_mode(
    target_kind: str,
    root_entry: str | None,
    hook_files: list[str],
    plugin_manifests: list[str],
    skill_entries: list[dict[str, Any]],
    skill_entry_preflight_installed: bool = False,
) -> dict[str, Any]:
    codex_project_hook = CODEX_HOOKS_CONFIG in hook_files and CODEX_START_HOOK_SCRIPT in hook_files
    plugin_lifecycle_hook = bool(hook_files and plugin_manifests and skill_entries)
    if plugin_manifests and skill_entries:
        mode = "bootstrap_skill"
        description = (
            "Plugin skills are present and may have lifecycle hooks, but no native Skill-invocation "
            "event is available."
        )
    elif root_entry:
        mode = "prompt_runtime_check"
        description = (
            "The instruction surface can require a Skill-entry preflight, but enforcement depends on "
            "prompt compliance."
        )
    else:
        mode = "manual_only"
        description = "No runtime instruction surface or host integration was detected."

    capabilities = {
        "repo_maintenance_hook": {
            "installed": codex_project_hook,
            "native_enforced": codex_project_hook,
            "event": CODEX_START_HOOK_EVENT if codex_project_hook else None,
            "scope": "canonical_repository",
            "covers_skill_invocation": False,
        },
        "plugin_lifecycle_hook": {
            "installed": plugin_lifecycle_hook,
            "native_enforced": plugin_lifecycle_hook,
            "scope": "plugin_lifecycle",
            "covers_skill_invocation": False,
        },
        "global_session_dispatcher": {
            "installed": False,
            "native_enforced": False,
            "event": CODEX_START_HOOK_EVENT,
            "scope": "all_registered_wrapped_skills",
            "covers_skill_invocation": False,
        },
        "skill_entry_preflight": {
            "installed": skill_entry_preflight_installed,
            "native_enforced": False,
            "scope": "selected_skill_instruction_surface",
            "covers_skill_invocation": skill_entry_preflight_installed,
        },
        "tool_gateway": {
            "installed": False,
            "native_enforced": False,
            "event": "PreToolUse",
            "scope": "toolized_execution_path",
            "covers_skill_invocation": False,
        },
        "skill_invocation_hook": {
            "supported": False,
            "installed": False,
            "event": None,
        },
    }

    return {
        "mode": mode,
        "native_skill_invocation_hook_installed": False,
        "native_host_hook_installed": False,
        "codex_project_hook": codex_project_hook,
        "plugin_lifecycle_hook": plugin_lifecycle_hook,
        "capabilities": capabilities,
        "manual_wrapper_command": "not_runtime_integration",
        "target_kind": target_kind,
        "root_entry": root_entry,
        "hook_files": hook_files,
        "plugin_manifests": plugin_manifests,
        "skill_count": len(skill_entries),
        "description": description,
    }


def normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def read_text_if_small(path: Path, limit: int = 200_000) -> str:
    if not path.is_file():
        return ""
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def controller_corpus(target: Path, controllers: list[str]) -> str:
    parts: list[str] = []
    for controller in controllers:
        path = target / controller
        parts.append(controller)
        parts.append(read_text_if_small(path, limit=80_000))
    return normalize_match_text("\n".join(parts))


def skill_entry_identifiers(entry: dict[str, Any]) -> list[str]:
    values = [
        entry.get("path", ""),
        entry.get("directory", ""),
        Path(entry.get("directory", "")).name,
        entry.get("name", ""),
    ]
    identifiers = []
    for value in values:
        item = str(value).strip().strip('"').strip("'").lower()
        if len(item) >= 3 and item not in identifiers:
            identifiers.append(item)
    return identifiers


def hook_loaded_skill_candidate_facts(
    target: Path,
    skill_inventory: list[dict[str, Any]],
    hooks: list[str],
    plugins: list[str],
) -> list[dict[str, Any]]:
    controllers = hooks + plugins
    if not controllers:
        return []

    corpus = controller_corpus(target, controllers)
    candidates: list[dict[str, Any]] = []
    for entry in skill_inventory:
        skill_path = entry["path"]
        full_path = target / skill_path
        skill_text = normalize_match_text(read_text_if_small(full_path, limit=80_000))
        name_basis = normalize_match_text(
            f"{entry.get('name', '')} {entry.get('directory', '')} {entry.get('path', '')}"
        )
        referenced_identifiers = [
            identifier
            for identifier in skill_entry_identifiers(entry)
            if identifier in corpus
        ]
        name_hints = [token for token in CONTROL_SKILL_NAME_TOKENS if token in name_basis]
        text_hints = [term for term in CONTROL_SKILL_TEXT_TERMS if term in skill_text]
        is_only_skill = len(skill_inventory) == 1

        if not (referenced_identifiers or name_hints or text_hints or is_only_skill):
            continue
        candidates.append(
            {
                "path": skill_path,
                "role": "hook_loaded_skill_instruction",
                "reason": "script-surfaced hook/plugin candidate; final placement requires diagnosis Skill review",
                "evidence": {
                    "controller_referenced_identifiers": referenced_identifiers,
                    "name_or_path_hints": name_hints,
                    "instruction_text_hints": text_hints,
                    "only_skill_in_bundle": is_only_skill,
                },
                "controlled_by": controllers,
                "has_wrapper_status_check": surface_has_status_check(full_path),
            }
        )

    return sorted(candidates, key=lambda item: item["path"])


def surface_has_status_check(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if _has_canonical_harness_entry(text):
        return True
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + len("\n---\n") :]
    stripped = text.lstrip()
    if stripped.startswith((STATUS_SECTION_HEADING, LEGACY_STATUS_SECTION_HEADING)):
        return True
    lines = stripped.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).lstrip().startswith(
            (STATUS_SECTION_HEADING, LEGACY_STATUS_SECTION_HEADING)
        )
    return False


def safe_target_relative_file(target: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", raw) or "\\" in raw:
        return None
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    target = target.expanduser().resolve()
    candidate = target / relative
    cursor = candidate
    while cursor != target:
        if cursor.is_symlink():
            return None
        cursor = cursor.parent
    try:
        candidate.resolve(strict=True).relative_to(target)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def collect_evolution_surface_facts(target: Path, skill_inventory: list[dict[str, Any]]) -> dict[str, Any]:
    plugins = plugin_manifest_files(target)
    hooks = hook_files(target)
    candidates: list[dict[str, Any]] = []

    def add_candidate(path: str, role: str, reason: str, controlled_by: list[str] | None = None) -> None:
        full_path = target / path
        if not full_path.is_file():
            return
        candidates.append(
            {
                "path": path,
                "role": role,
                "reason": reason,
                "controlled_by": controlled_by or [],
                "has_wrapper_status_check": surface_has_status_check(full_path),
            }
        )

    add_candidate(
        "SKILL.md",
        "root_skill_instruction",
        "root SKILL.md is the direct Skill instruction surface",
    )
    add_candidate(
        "AGENTS.md",
        "root_agent_instruction",
        "root AGENTS.md controls repository-level agent behavior",
    )
    candidates.extend(hook_loaded_skill_candidate_facts(target, skill_inventory, hooks, plugins))

    if not candidates and len(skill_inventory) == 1:
        add_candidate(
            skill_inventory[0]["path"],
            "only_skill_instruction",
            "single discovered skills/*/SKILL.md is the only available Skill instruction surface",
        )

    return {
        "status": "needs_skill_diagnosis" if candidates else "needs_owner_choice",
        "selected": None,
        "candidates": candidates,
        "instruction_placement": None,
        "controller_files": hooks + plugins,
        "diagnosis_skill": "skills/evolution-surface-diagnosis/SKILL.md",
        "selection_rule": (
            "scripts collect facts and candidate instruction surfaces only; "
            "the evolution-surface diagnosis Skill must browse the whole repo and decide the controlling surface"
        ),
        "script_fact_boundary": (
            "do not treat candidates as final placement; use them as evidence for the diagnosis Skill"
        ),
    }


def assess_component_gaps(target: Path, evolution_surface: dict[str, Any]) -> dict[str, Any]:
    required = REQUIRED_WRAPPER_FILES + [TARGET_WRAPPER_MANIFEST]
    missing_files = [rel for rel in required if not (target / rel).exists()]
    present_files = [rel for rel in required if (target / rel).exists()]
    missing_concepts = []
    selected = evolution_surface.get("selected")
    if not selected:
        missing_concepts.append("evolution surface diagnosis result")
    elif not selected.get("has_wrapper_status_check"):
        missing_concepts.append(f"{selected['path']} canonical Harness Skill activation block")
    if not (target / TARGET_CHANGELOG).exists():
        missing_concepts.append("Skill or kit release changelog")
    if not wrapper_manifest_path(target).exists():
        missing_concepts.append("wrapper manifest")

    return {
        "present_files": present_files,
        "missing_files": missing_files,
        "missing_concepts": missing_concepts,
    }


def detect_target_architecture(target: Path) -> dict[str, Any]:
    target = target.expanduser().resolve()
    has_root_skill = (target / "SKILL.md").exists()
    has_agents = (target / "AGENTS.md").exists()
    entries = skill_entries(target)
    plugins = plugin_manifest_files(target)
    hooks = hook_files(target)
    dir_names = [
        "runtime",
        "agents",
        "skills",
        "automation",
        "cases",
        "knowledge",
        "templates",
        "state",
        "config",
    ]
    present_dirs = [name for name in dir_names if (target / name).is_dir()]
    file_names = [
        "AGENTS.md",
        "SKILL.md",
        "ARCHITECTURE.md",
        "MAINTENANCE.md",
        "README.md",
        "ONLINE-DOCS.md",
        "CLAUDE.md",
        "OPENCLAW.md",
        "HERMES.md",
    ]
    present_files = [name for name in file_names if (target / name).is_file()]
    evolution_surface = collect_evolution_surface_facts(target, entries)

    if hooks and plugins and entries:
        target_kind = "hooked_skill_bundle"
    elif has_root_skill and not entries:
        target_kind = "single_skill"
    elif has_agents and entries and {"runtime", "agents", "automation"}.issubset(set(present_dirs)):
        target_kind = "runtime_skill_bundle"
    elif entries:
        target_kind = "skill_bundle"
    elif has_agents:
        target_kind = "agents_runtime"
    else:
        target_kind = "unknown"

    if target_kind == "hooked_skill_bundle":
        architecture_style = "plugin_hook_controlled_skill_system"
    elif target_kind == "runtime_skill_bundle":
        architecture_style = "managed_runtime_skill_bundle"
    elif target_kind == "skill_bundle":
        architecture_style = "multi_skill_bundle"
    elif target_kind == "single_skill":
        architecture_style = "single_skill_repo"
    elif target_kind == "agents_runtime":
        architecture_style = "agent_runtime"
    else:
        architecture_style = "unknown"

    root_entry = "SKILL.md" if has_root_skill else "AGENTS.md" if has_agents else None
    selected_instruction_surface = root_entry
    current_manifest = target / TARGET_WRAPPER_MANIFEST
    if current_manifest.is_file():
        try:
            manifest_data = json.loads(current_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest_data = {}
        manifest_surface = (
            manifest_data.get("instruction_surface")
            if isinstance(manifest_data, dict)
            else None
        )
        if safe_target_relative_file(target, manifest_surface) is not None:
            selected_instruction_surface = manifest_surface
    integration = classify_integration_mode(
        target_kind=target_kind,
        root_entry=root_entry,
        hook_files=hooks,
        plugin_manifests=plugins,
        skill_entries=entries,
        skill_entry_preflight_installed=(
            bool(selected_instruction_surface)
            and surface_has_status_check(target / selected_instruction_surface)
        ),
    )
    integration["instruction_surface"] = selected_instruction_surface
    verification_candidates = [
        str(path.relative_to(target))
        for path in sorted((target / "automation").glob("*.py"))
    ] if (target / "automation").is_dir() else []
    component_gaps = assess_component_gaps(target, evolution_surface)

    return {
        "target_kind": target_kind,
        "architecture_style": architecture_style,
        "root_entry": root_entry,
        "evolution_surface": evolution_surface,
        "component_gaps": component_gaps,
        "root_files": present_files,
        "top_level_dirs": present_dirs,
        "plugin_manifests": plugins,
        "hook_files": hooks,
        "integration": integration,
        "skill_inventory": {
            "count": len(entries),
            "entries": entries,
        },
        "verification_candidates": verification_candidates,
    }


def diagnose_skill(
    target: Path,
    repo: str | None,
    skill_name: str | None,
    home: Path = Path.home(),
    workspace_roots: list[Path] | None = None,
    runner=run_command,
) -> dict[str, Any]:
    home = home.expanduser().resolve()
    target = target.expanduser().resolve()
    skill_md = target / "SKILL.md"
    architecture = detect_target_architecture(target)
    global_hook_status = read_global_hook_status(home)
    global_capability = architecture["integration"]["capabilities"][
        "global_session_dispatcher"
    ]
    global_capability.update(
        {
            "installed": global_hook_status["status"] == "installed",
            "native_enforced": global_hook_status["native_enforced"],
            "trust_status": global_hook_status["trust_status"],
            "status_source": "user_runtime_diagnosis",
        }
    )
    inferred_name = skill_name or skill_name_from_skill_md(skill_md) or target.name
    manifest = load_wrapper_manifest(target, allow_legacy=True)
    manifest_install_paths = [
        Path(item).expanduser()
        for item in (manifest or {}).get("install_links", [])
        if isinstance(item, str) and item.strip()
    ]
    install_paths = manifest_install_paths or [
        home / ".codex" / "skills" / inferred_name,
        home / ".agents" / "skills" / inferred_name,
    ]
    install_paths = list(dict.fromkeys(install_paths))
    installs = [
        describe_install_path(path, target)
        for path in install_paths
        if path.exists() or path.is_symlink()
    ]

    manifest_repo = manifest.get("canonical_repo") if manifest else None
    effective_repo = repo or manifest_repo
    repo_state = diagnose_repo_state(target, effective_repo, home, workspace_roots or [], runner)
    version = diagnose_skill_version(target, repo_state["exists_on_github"], repo_state["latest_release"])
    harness = diagnose_harness_state(target)
    source_contract = diagnose_source_contract(
        target=target,
        requested_repo=repo,
        skill_name=inferred_name,
        home=home,
        installs=installs,
        runner=runner,
    )
    return {
        "stage": "target_skill_diagnosis",
        "skill": {
            "name": inferred_name,
            "target_path": str(target),
            "has_skill_md": skill_md.exists(),
            "root_entry": architecture["root_entry"],
            "target_kind": architecture["target_kind"],
            "architecture_style": architecture["architecture_style"],
            "evolution_surface": architecture["evolution_surface"],
            "component_gaps": architecture["component_gaps"],
            "root_files": architecture["root_files"],
            "top_level_dirs": architecture["top_level_dirs"],
            "plugin_manifests": architecture["plugin_manifests"],
            "hook_files": architecture["hook_files"],
            "integration": architecture["integration"],
            "skill_inventory": architecture["skill_inventory"],
            "verification_candidates": architecture["verification_candidates"],
        },
        "repo": repo_state,
        "version": version,
        "installs": installs,
        "harness": harness,
        "source_contract": source_contract,
        "publication": {
            "visibility": repo_state.get("visibility"),
            "sensitive_risk": "unknown",
        },
    }


def wrapper_manifest_path(target: Path) -> Path:
    return target / TARGET_EVOINFRA_DIR / "wrapper.json"


def legacy_wrapper_manifest_path(target: Path) -> Path:
    return target / LEGACY_TARGET_EVOINFRA_DIR / "wrapper.json"


def oldest_wrapper_manifest_path(target: Path) -> Path:
    return target / OLDEST_TARGET_EVOINFRA_DIR / "wrapper.json"


def _read_manifest_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid wrapper manifest JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"wrapper manifest must be a JSON object: {path}")
    return data


def wrapper_manifest_status(target: Path) -> dict[str, Any]:
    candidates = [
        ("current", wrapper_manifest_path(target)),
        ("legacy_evoinfra", legacy_wrapper_manifest_path(target)),
        ("legacy_evozeus", oldest_wrapper_manifest_path(target)),
    ]
    detected = [(source, path, _read_manifest_json(path)) for source, path in candidates if path.exists()]
    active_source, active_path, active_manifest = detected[0] if detected else ("missing", None, None)
    conflict = any(manifest != active_manifest for _, _, manifest in detected[1:])
    source = "conflict" if conflict else active_source
    current_exists = wrapper_manifest_path(target).exists()
    legacy_paths = [path for name, path, _ in detected if name != "current"]
    legacy_exists = bool(legacy_paths)

    return {
        "target_infra_dir": TARGET_EVOINFRA_DIR,
        "legacy_infra_dir": LEGACY_TARGET_EVOINFRA_DIR,
        "oldest_infra_dir": OLDEST_TARGET_EVOINFRA_DIR,
        "manifest_path": TARGET_WRAPPER_MANIFEST,
        "legacy_manifest_path": LEGACY_TARGET_WRAPPER_MANIFEST,
        "oldest_manifest_path": OLDEST_TARGET_WRAPPER_MANIFEST,
        "active_manifest_path": str(active_path) if active_path else None,
        "active_manifest_relpath": str(active_path.relative_to(target)) if active_path else None,
        "manifest_source": source,
        "current_manifest_detected": current_exists,
        "legacy_manifest_detected": legacy_exists,
        "legacy_manifest_paths": [str(path.relative_to(target)) for path in legacy_paths],
        "migration_required": legacy_exists or not current_exists and bool(detected),
        "duplicate_legacy_detected": legacy_exists and current_exists and not conflict,
        "conflict": conflict,
    }


def load_wrapper_manifest(target: Path, allow_legacy: bool = False) -> dict[str, Any] | None:
    status = wrapper_manifest_status(target)
    if status["conflict"]:
        raise ValueError(
            "conflicting wrapper manifests: " + ", ".join(
                [TARGET_WRAPPER_MANIFEST, LEGACY_TARGET_WRAPPER_MANIFEST, OLDEST_TARGET_WRAPPER_MANIFEST]
            )
        )
    if status["migration_required"] and not allow_legacy:
        raise ValueError(
            "legacy wrapper layout requires migration before managed use: "
            + ", ".join(status["legacy_manifest_paths"])
        )
    active = status["active_manifest_path"]
    if not active:
        return None
    return _read_manifest_json(Path(active))


def diagnose_source_contract(
    target: Path,
    requested_repo: str | None,
    skill_name: str,
    home: Path,
    installs: list[dict[str, Any]],
    runner=run_command,
) -> dict[str, Any]:
    manifest = load_wrapper_manifest(target, allow_legacy=True)
    discovery_order = [
        TARGET_WRAPPER_MANIFEST,
        f"{LEGACY_TARGET_WRAPPER_MANIFEST} / {OLDEST_TARGET_WRAPPER_MANIFEST} (migration detection only)",
        "~/.evozeus/.projects/OWNER/REPO",
        "canonical repo git origin / GitHub repo",
        "~/.codex/skills/<skill-name> and ~/.agents/skills/<skill-name>",
        "current user/org/public GitHub search fallback",
    ]
    if not manifest:
        return {
            "managed": False,
            "status": "unmanaged",
            "discovery_order": discovery_order,
            "errors": [],
            "warnings": [],
            "canonical_repo": requested_repo,
            "canonical_path": target_canonical_path(target, runner),
            "projects_pointer": None,
            "runtime_installs": installs,
        }

    errors: list[str] = []
    warnings: list[str] = []
    manifest_state = wrapper_manifest_status(target)
    if manifest_state["migration_required"]:
        errors.append(
            "legacy wrapper layout detected; run EvoZeus-CoEvolve harness upgrade before managed execution"
        )
    manifest_repo = manifest.get("canonical_repo")
    if not manifest_repo:
        errors.append(f"{TARGET_WRAPPER_MANIFEST} is missing canonical_repo")
    if requested_repo and manifest_repo and requested_repo != manifest_repo:
        errors.append(f"--repo {requested_repo} does not match wrapper canonical_repo {manifest_repo}")

    canonical_repo = manifest_repo or requested_repo
    canonical_path = target_canonical_path(target, runner)
    pointer = repo_projects_pointer(home, canonical_repo)
    pointer_info = {
        "path": str(pointer) if pointer else None,
        "kind": path_kind(pointer) if pointer else "missing",
        "resolved_path": resolve_path(pointer) if pointer else None,
    }

    if not pointer:
        errors.append("cannot derive ~/.evozeus/.projects pointer because canonical_repo is missing")
    elif not pointer.exists() and not pointer.is_symlink():
        errors.append(f"project pointer is missing: {pointer}")
    elif not pointer.is_symlink():
        errors.append(f"project pointer must be a symlink to the canonical repo: {pointer}")
    elif pointer_info["resolved_path"] != canonical_path:
        errors.append(
            "project pointer does not resolve to canonical repo: "
            f"{pointer} -> {pointer_info['resolved_path']} expected {canonical_path}"
        )

    origin_repo = git_origin_repo(Path(canonical_path), runner)
    if origin_repo and canonical_repo and origin_repo != canonical_repo:
        errors.append(f"canonical repo origin {origin_repo} does not match wrapper canonical_repo {canonical_repo}")
    elif not origin_repo:
        warnings.append("canonical repo has no GitHub origin yet; this is only acceptable before first publish")

    runtime_reports = []
    canonical_root = Path(canonical_path)
    for install in installs:
        report = dict(install)
        pointer_scope = runtime_pointer_scope(canonical_root, install.get("resolved_path"))
        if install["kind"] == "symlink" and pointer_scope == "canonical_repo":
            report["source_contract"] = "runtime_pointer_ok"
            report["pointer_scope"] = pointer_scope
        elif install["kind"] == "symlink" and pointer_scope == "canonical_subskill":
            report["source_contract"] = "runtime_subskill_pointer_ok"
            report["pointer_scope"] = pointer_scope
        elif install["kind"] == "directory":
            report["source_contract"] = "runtime_real_directory_warning"
            warnings.append(
                f"runtime install is a real directory, not a canonical repo symlink: {install['path']}"
            )
        elif install["kind"] == "symlink":
            report["source_contract"] = "runtime_pointer_mismatch"
            errors.append(
                f"runtime symlink does not resolve to canonical repo: "
                f"{install['path']} -> {install.get('resolved_path')} expected {canonical_path} "
                "or a direct canonical skills/<name> entry"
            )
        else:
            report["source_contract"] = "runtime_install_unusable"
            warnings.append(f"runtime install is not a symlink directory: {install['path']}")
        runtime_reports.append(report)

    status = "error" if errors else "warning" if warnings else "ok"
    return {
        "managed": True,
        "status": status,
        "discovery_order": discovery_order,
        "target_infra_dir": TARGET_EVOINFRA_DIR,
        "legacy_infra_dir": LEGACY_TARGET_EVOINFRA_DIR,
        "manifest_path": TARGET_WRAPPER_MANIFEST,
        "legacy_manifest_detected": wrapper_manifest_status(target)["legacy_manifest_detected"],
        "migration_required": wrapper_manifest_status(target)["migration_required"],
        "errors": errors,
        "warnings": warnings,
        "canonical_repo": canonical_repo,
        "canonical_path": canonical_path,
        "canonical_origin_repo": origin_repo,
        "projects_pointer": pointer_info,
        "runtime_installs": runtime_reports,
    }


def build_onboarding_contract(
    *,
    repo: str,
    skill_name: str,
    init_command: str | None = None,
    init_verification: str | None = None,
    generates_child_skills: bool = False,
) -> dict[str, Any]:
    init_command = init_command.strip() if init_command else None
    init_verification = init_verification.strip() if init_verification else None
    if bool(init_command) != bool(init_verification):
        raise ValueError("required initialization must provide both command and verification")
    quoted_skill_name = shlex.quote(skill_name)

    child_verification = (
        "Run the parent repository structure preflight and pass a child consumer-project smoke test. "
        "Create a separate Harness only after the child becomes an independent Git repository."
        if generates_child_skills
        else "not_applicable"
    )
    return {
        "installation": {
            "mode": "canonical_repo_symlink",
            "command": (
                "python3 scripts/evozeus_wrapper.py publish reinstall "
                f"--skill-name {quoted_skill_name} --canonical-path <canonical-repo-path> --target codex --json"
            ),
            "verification": (
                f"test -L \"$HOME/.codex/skills\"/{quoted_skill_name} && python3 {TARGET_PREFLIGHT_SCRIPT} "
                f"doctor --repo {repo}"
            ),
        },
        "invocation": {
            "mode": "host_skill_discovery",
            "owner": "target_skill",
            "instruction": (
                f"Start a new host session in a consumer project and invoke {skill_name} using the "
                "trigger contract in its canonical SKILL.md."
            ),
            "verification": (
                f"Confirm the host selects the canonical {skill_name}/SKILL.md and pass a "
                "consumer-project smoke test."
            ),
        },
        "initialization": {
            "required": bool(init_command),
            "owner": "target_skill",
            "command": init_command,
            "verification": init_verification,
        },
        "generated_child_skills": {
            "supported": generates_child_skills,
            "hooks_inherited": False,
            "repo_harness_inherited": generates_child_skills,
            "attachment": "inherited_repo_harness" if generates_child_skills else "not_applicable",
            "separate_harness_boundary": "independent_git_repository",
            "trust_review": "parent_repo_host_adapter" if generates_child_skills else "not_applicable",
            "verification": child_verification,
        },
    }


def build_dashboard_contract() -> dict[str, str]:
    return {
        "deployment_mode": "opt_in_github_pages",
        "enablement_variable": "EVOZEUS_PAGES_ENABLED",
        "enabled_value": "true",
        "fallback": "repository_only",
        "capability_check": "confirm repository Pages support before enabling deployment",
    }


def build_status_section(replacements: dict[str, str]) -> str:
    return f"""\
{STATUS_SECTION_HEADING}

本段是 Skill 入口 preflight。Agent 选中本 Skill 后、进入业务主链路前执行；它基本绑定当前 Skill，但依赖 instruction compliance，不是 native Skill invocation hook。

`{TARGET_WRAPPER_MANIFEST}` 分开记录 capability：`repo_maintenance_hook` 只在 canonical repository 作为活动项目时原生触发；`global_session_dispatcher` 在每个任务启动时聚合检查全部 wrapped Skills；本入口仍记录为 `prompt_runtime_check`。当前 Codex 没有 `SkillInvoke` 事件，不得把前两者描述成 per-Skill native invocation hook。

若当前只是 runtime-only install，缺少维护资产时不要把安装副本当作事实源，回 canonical repo 处理 wrapper harness 或 Skill release。

1. Skill release 状态
   - 当前记录版本：`{replacements["CURRENT_VERSION"]}`
   - 检查命令：`python3 {TARGET_PREFLIGHT_SCRIPT} version --repo {replacements["REPO_NAME"]}`
   - 如果 GitHub latest release 更新：先更新 canonical repo，并确认 runtime install 仍指向 canonical repo。
   - 如果本地版本领先 GitHub release：先完成 changelog、验证和 `vMAJOR.MINOR.PATCH` release，再把它当作稳定运行版本。
2. Wrapper harness 状态
   - 当前 wrapper 版本：`{replacements["WRAPPER_VERSION"]}`
   - 事实源：`{TARGET_WRAPPER_MANIFEST}`
   - 检查命令：在 EvoZeus-CoEvolve repo 运行 `python3 scripts/evozeus_wrapper.py harness upgrade-check --target <this-skill-repo> --json`
   - 如果 wrapper 落后且 `upgrade-check` 未发现冲突或不兼容：报告当前与最新版本；兼容的旧 wrapper 只作为维护提醒，不阻塞业务主链路。
   - 普通 Skill 调用不授权 Harness 升级或其他维护写入。只有用户明确请求 Harness 维护或升级后，才运行 `harness upgrade --dry-run` 生成方案；实际写入仍需单独确认。
3. Source contract 状态
   - 检查命令：`python3 {TARGET_PREFLIGHT_SCRIPT} doctor --repo {replacements["REPO_NAME"]}`
   - 如果 `~/.evozeus/.projects`、git origin 或 runtime install 不一致：先修复为同一个 canonical repo，再继续。
4. 调用身份头
   - 检查命令：`python3 {TARGET_PREFLIGHT_SCRIPT} identity --json`
   - 读取 `runtime_identity.display_line`，并将其原样放在本次 Skill invocation 第一条用户可见输出的第一行。
   - 身份头固定以 `🧙🏻‍♂️` 开始；禁止使用 HTML、自定义图片或 shortcode 替代。
   - 同一次 invocation 的后续 commentary 和 final 不重复；下一次 invocation 再展示一次。
5. EvoZeus Notice
   - 渲染入口：`python3 {TARGET_NOTICE_SCRIPT} render --kind <kind> --state <state> --message <message> [--action <action>] [--json]`。
   - 配置事实源：`{TARGET_NOTICE_POLICY}`。普通业务进度不展示 EvoZeus Tag。
   - 用户纠错、不满意或复盘发现可复用机制缺陷时，先完成当前业务纠正，再运行 feedback audit，并通过 `--context` 传入一句脱敏、可复用、可行动的 Lesson 摘要；在同一响应末尾原样显示 `user_notice.display_text`。
   - Lesson Notice 的 Tag 为 `EvoZeus · Lesson`、状态为 `待记录`，只询问是否记录到 Skill Feedback Issue。
   - Lesson 记录、Skill 修复、Harness 维护、UAT 与正式发布分别使用配置中的独立 kind；任何 Notice 都不扩张写入授权。

解决顺序：Source contract 损坏、manifest 无效、迁移冲突或已确认不兼容时停止业务流程并说明原因；其他情况完成只读检查后直接进入主链路。
"""


def build_wrapper_manifest(
    repo: str,
    wrapper_version: str,
    managed_files: list[str],
    install_links: list[str],
    instruction_surface: str | None = None,
    integration: dict[str, Any] | None = None,
    onboarding: dict[str, Any] | None = None,
    dashboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_managed_files = list(dict.fromkeys([
        *managed_files,
        TARGET_HARNESS_SKILL,
        TARGET_BRANCH_CONSUMER_SCRIPT,
        TARGET_BRANCH_CONTRACT,
        TARGET_BRANCH_PROVENANCE,
        TARGET_BRANCH_PLANNER,
    ]))
    default_hook_files = []
    if CODEX_HOOKS_CONFIG in effective_managed_files and CODEX_START_HOOK_SCRIPT in effective_managed_files:
        default_hook_files = [CODEX_HOOKS_CONFIG, CODEX_START_HOOK_SCRIPT]
    effective_integration = integration or classify_integration_mode(
        target_kind="single_skill",
        root_entry=instruction_surface or "SKILL.md",
        hook_files=default_hook_files,
        plugin_manifests=[],
        skill_entries=[],
        skill_entry_preflight_installed=True,
    )
    repo_hook_installed = bool(
        (effective_integration.get("capabilities") or {})
        .get("repo_maintenance_hook", {})
        .get("installed")
    )
    manifest = {
        "wrapper_repo": WRAPPER_REPO,
        "wrapper_version": wrapper_version,
        "applied_at": date.today().isoformat(),
        "layout_version": 2,
        "target_wrapper_dir": TARGET_EVOINFRA_DIR,
        "target_infra_dir": TARGET_EVOINFRA_DIR,
        "legacy_layout_dirs": [LEGACY_TARGET_EVOINFRA_DIR, OLDEST_TARGET_EVOINFRA_DIR],
        "canonical_repo": repo,
        "instruction_surface": instruction_surface or "SKILL.md",
        "harness_skill_path": TARGET_HARNESS_SKILL,
        "harness_skill_version": HARNESS_SKILL_VERSION,
        "harness_skill_managed": True,
        "managed_files": effective_managed_files,
        "install_links": install_links,
        "dashboard": dashboard if dashboard is not None else build_dashboard_contract(),
        "onboarding": (
            onboarding
            if onboarding is not None
            else build_onboarding_contract(repo=repo, skill_name=repo.split("/")[-1])
        ),
        "hook_registration": {
            "codex": {
                "capability": "repo_maintenance_hook",
                "config_file": CODEX_HOOKS_CONFIG,
                "hook_script": CODEX_START_HOOK_SCRIPT,
                "event": CODEX_START_HOOK_EVENT,
                "matcher": CODEX_START_HOOK_MATCHER,
                "scope": "canonical_repository",
                "covers_skill_invocation": False,
                "installation_status": "installed" if repo_hook_installed else "not_installed",
                "trust_status": "pending_review" if repo_hook_installed else "not_installed",
                "trust_review": "required_by_codex_hooks",
                "latest_version_env": "EVOZEUS_WRAPPER_LATEST_VERSION",
                "enforcement_env": "EVOZEUS_WRAPPER_HOOK_ENFORCEMENT",
            },
        },
        "integration": effective_integration,
        "contributor_branch": {
            "profile": "coevolve_target_skillware_consumer",
            "consumer_path": TARGET_BRANCH_CONSUMER_SCRIPT,
            "contract_path": TARGET_BRANCH_CONTRACT,
            "provenance_path": TARGET_BRANCH_PROVENANCE,
            "planner_path": TARGET_BRANCH_PLANNER,
            "permission_authority": "core_planner_live_github_evidence",
            "runtime_network_fetch": False,
            "ledger_root": "~/.evozeus/coevolve/branch-plans/OWNER/REPO",
        },
    }
    return manifest


def write_wrapper_manifest(target: Path, manifest: dict[str, Any], force: bool = False) -> str:
    path = wrapper_manifest_path(target)
    if path.exists() and not force:
        return f"skip existing {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"write {path}"


TARGET_INFRA_PATH_REPLACEMENTS = (
    (LEGACY_TARGET_WRAPPER_MANIFEST, TARGET_WRAPPER_MANIFEST),
    (OLDEST_TARGET_WRAPPER_MANIFEST, TARGET_WRAPPER_MANIFEST),
    (LEGACY_TARGET_FEEDBACK_POLICY, TARGET_FEEDBACK_POLICY),
    (OLDEST_TARGET_FEEDBACK_POLICY, TARGET_FEEDBACK_POLICY),
    (LEGACY_TARGET_AUDIT_RULE, TARGET_AUDIT_RULE),
    (OLDEST_TARGET_AUDIT_RULE, TARGET_AUDIT_RULE),
    (".codex/hooks/evozeus_wrapper_start_check.py", CODEX_START_HOOK_SCRIPT),
    ("scripts/evozeus_wrapper_preflight.py", TARGET_PREFLIGHT_SCRIPT),
    ("docs/wrapper-migrations", f"{TARGET_EVOINFRA_DIR}/docs/migrations"),
    ("docs/design-doc-template.md", TARGET_DESIGN_TEMPLATE),
    ("docs/designs", f"{TARGET_EVOINFRA_DIR}/docs/designs"),
    ("docs/index.md", TARGET_DASHBOARD_INDEX),
    ("docs/_config.yml", TARGET_DASHBOARD_CONFIG),
    ("WRAPPER.md", TARGET_WRAPPER_GUIDE),
    ("CHANGELOG.md", TARGET_CHANGELOG),
    ("`.evozeus/.projects", "`~/.evozeus/.projects"),
)


def rewrite_target_infra_string(value: str) -> str:
    updated = value
    protected: dict[str, str] = {}
    for index, replacement in enumerate(dict(TARGET_INFRA_PATH_REPLACEMENTS).values()):
        token = f"__EVOZEUS_WRAPPER_PATH_{index}__"
        if replacement in updated:
            updated = updated.replace(replacement, token)
            protected[token] = replacement
    for old, new in TARGET_INFRA_PATH_REPLACEMENTS:
        updated = updated.replace(old, new)
    for token, replacement in protected.items():
        updated = updated.replace(token, replacement)
    return updated


def rewrite_target_infra_json(value: Any) -> Any:
    if isinstance(value, str):
        return rewrite_target_infra_string(value)
    if isinstance(value, list):
        return [rewrite_target_infra_json(item) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_target_infra_json(item) for key, item in value.items()}
    return value


def rewrite_target_infra_json_file(path: Path) -> bool:
    data = _read_manifest_json(path) if path.name == "wrapper.json" else json.loads(path.read_text(encoding="utf-8"))
    updated = rewrite_target_infra_json(data)
    if updated == data:
        return False
    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def target_infra_text_files(target: Path) -> list[Path]:
    files: list[Path] = []
    direct = ["SKILL.md", "WRAPPER.md", "README.md", "CHANGELOG.md"]
    for rel in direct:
        path = target / rel
        if path.is_file():
            files.append(path)
    for dirname, pattern in [
        ("docs", "*.md"),
        ("scripts", "*.py"),
        (".github", "*.yml"),
        (".github", "*.yaml"),
        (".github", "*.md"),
        ("skills", "SKILL.md"),
        ("templates", "*.md"),
    ]:
        root = target / dirname
        if root.is_dir():
            files.extend(path for path in sorted(root.rglob(pattern)) if path.is_file())
    wrapper_root = target / TARGET_EVOINFRA_DIR
    if wrapper_root.is_dir() and not wrapper_root.is_symlink():
        files.extend(
            path
            for path in sorted(wrapper_root.rglob("*"))
            if path.is_file() and path.suffix.lower() in {".json", ".md", ".py", ".yml", ".yaml"}
        )
    codex_hooks = target / CODEX_HOOKS_CONFIG
    if codex_hooks.is_file():
        files.append(codex_hooks)
    return list(dict.fromkeys(files))


def rewrite_target_infra_text_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = rewrite_target_infra_string(text)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def rewrite_dashboard_contact_link(target: Path) -> bool:
    path = target / ".github" / "ISSUE_TEMPLATE" / "config.yml"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    updated = text.replace(
        "/tree/main/docs",
        f"/tree/main/{TARGET_EVOINFRA_DIR}/docs",
    )
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def feedback_policy_path(target: Path) -> Path:
    current = target / TARGET_FEEDBACK_POLICY
    if current.exists():
        return current
    legacy = target / LEGACY_TARGET_FEEDBACK_POLICY
    if legacy.exists():
        return legacy
    return target / OLDEST_TARGET_FEEDBACK_POLICY


def load_feedback_policy(target: Path) -> dict[str, Any]:
    path = feedback_policy_path(target)
    if not path.exists():
        return {
            "management_mode": "manual",
            "strictness": "medium",
            "audit_rule": TARGET_AUDIT_RULE,
            "routing": {},
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def contains_any_term(text: str, terms: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(term.lower() in normalized for term in terms)


def infer_feedback_route(user_input: str) -> str:
    wrapper = contains_any_term(user_input, WRAPPER_ROUTE_TERMS)
    target = contains_any_term(user_input, TARGET_ROUTE_TERMS)
    if wrapper and target:
        return "both"
    if wrapper:
        return "wrapper"
    if target:
        return "target_skill"
    return "target_skill"


def feedback_issue_title(route: str, user_input: str) -> str:
    compact = " ".join(user_input.strip().split())
    if len(compact) > 48:
        compact = compact[:45].rstrip() + "..."
    prefix = {
        "wrapper": "Wrapper feedback",
        "target_skill": "Skill feedback",
        "both": "Skill + wrapper feedback",
    }.get(route, "Skill feedback")
    return f"{prefix}: {compact or 'unspecified issue'}"


def feedback_issue_body(
    *,
    route: str,
    reason: str,
    severity: str,
    user_input: str,
    context: str | None,
) -> str:
    evidence = context.strip() if context else user_input.strip()
    return "\n".join(
        [
            "## Feedback",
            "",
            user_input.strip() or "(empty input)",
            "",
            "## Expected Result",
            "",
            "Capture the reusable rule or wrapper defect so future Skill runs do not repeat it.",
            "",
            "## Reproduction / Scenario",
            "",
            evidence or "(no additional context provided)",
            "",
            "## Evidence Boundary",
            "",
            "Use only this redacted summary. Do not include raw private session text, customer secrets, credentials, or unreleased commercial context.",
            "",
            "## Routing",
            "",
            f"- Route: `{route}`",
            f"- Severity: `{severity}`",
            f"- Reason: {reason}",
            "",
        ]
    )


def plan_feedback_audit(target: Path, user_input: str, context: str | None = None) -> dict[str, Any]:
    target = target.expanduser().resolve()
    policy = load_feedback_policy(target)
    manifest = load_wrapper_manifest(target)
    should_capture = contains_any_term(user_input, FEEDBACK_CAPTURE_TERMS)
    route = infer_feedback_route(user_input)
    severity = "high" if route == "both" else "medium" if route == "wrapper" else "low"
    reason = (
        "user reported a reusable wrapper/Skill behavior gap"
        if should_capture
        else "no reusable correction, dissatisfaction, or mechanism defect detected"
    )
    canonical_repo = (manifest or {}).get("canonical_repo")
    if route == "wrapper":
        issue_repo = WRAPPER_REPO
        secondary_issue_repo = None
    elif route == "both":
        issue_repo = canonical_repo
        secondary_issue_repo = WRAPPER_REPO
    else:
        issue_repo = canonical_repo
        secondary_issue_repo = None
    title = feedback_issue_title(route, user_input)
    body = feedback_issue_body(
        route=route,
        reason=reason,
        severity=severity,
        user_input=user_input,
        context=context,
    )
    signal_seed = "|".join(
        [canonical_repo or "unknown", route, " ".join(user_input.lower().split())]
    )
    signal_id = f"sig_{hashlib.sha256(signal_seed.encode('utf-8')).hexdigest()[:8].upper()}"
    capture_state = "LOCAL_PENDING_CONFIRMATION" if should_capture else None
    user_notice = None
    if should_capture:
        notice_policy_path = target / TARGET_NOTICE_POLICY
        notice_policy = load_notice_policy(notice_policy_path if notice_policy_path.is_file() else None)
        lesson_summary = (context or reason).strip().rstrip("。.!！?")
        user_notice = render_notice(
            kind="lesson",
            state="pending",
            message=f"捕捉到一条可复用 Lesson：{lesson_summary}。",
            action="是否记录到 Skill Feedback Issue？本次授权仅用于记录，不启动修复。",
            signal_id=signal_id,
            policy=notice_policy,
        )
    capture_marker = user_notice["display_text"].splitlines()[0] if user_notice else None

    return {
        "stage": "continuous_evolution_loop",
        "flow": "feedback_audit",
        "writes": False,
        "target": str(target),
        "policy_path": str(feedback_policy_path(target).relative_to(target))
        if feedback_policy_path(target).exists()
        else None,
        "audit_rule_path": policy.get("audit_rule") or TARGET_AUDIT_RULE,
        "management_mode": policy.get("management_mode", "manual"),
        "canonical_repo": canonical_repo,
        "issue_repo": issue_repo,
        "secondary_issue_repo": secondary_issue_repo,
        "should_capture": should_capture,
        "signal_id": signal_id if should_capture else None,
        "capture_state": capture_state,
        "capture_marker": capture_marker,
        "user_notice": user_notice,
        "capture_persisted": False,
        "reason": reason,
        "route": route,
        "severity": severity,
        "evidence_boundary": (
            "redacted summary only; no raw private session, customer secrets, credentials, or unreleased commercial context"
        ),
        "issue_title": title if should_capture else None,
        "issue_body": body if should_capture else None,
        "issue_create_command": None,
        "authorization": {
            "issue_submission": "explicit_confirmation_required",
            "fix_execution": "separate_confirmation_required",
        },
        "next_action": (
            "continue_business_and_await_feedback_submission_confirmation"
            if should_capture
            else "no_capture_needed"
        ),
    }


def _same_file_contents(left: Path, right: Path) -> bool:
    return left.is_file() and right.is_file() and file_sha256(left) == file_sha256(right)


def _pr_template_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text and text.count("\r\n") == text.count("\n") else "\n"


def _canonical_pr_plan_block(official_text: str) -> tuple[str, str]:
    if official_text.count(PR_PLAN_BEGIN) != 1 or official_text.count(PR_PLAN_END) != 1:
        raise ValueError("official Pull Request template must contain one managed Contributor Branch Plan block")
    start = official_text.index(PR_PLAN_BEGIN)
    end_marker = official_text.index(PR_PLAN_END)
    if end_marker <= start:
        raise ValueError("official Pull Request template managed block markers are out of order")
    end = end_marker + len(PR_PLAN_END)
    block = official_text[start:end]
    inner = official_text[start + len(PR_PLAN_BEGIN) : end_marker].strip("\r\n")
    if len(re.findall(rf"(?m)^{re.escape(PR_PLAN_HEADING)}[ \t]*\r?$", inner)) != 1:
        raise ValueError("official Pull Request template managed block has an invalid heading")
    return block, inner


def merge_pull_request_template(current_text: str, official_text: str) -> tuple[str, str]:
    """Refresh only proven wrapper-owned PR-template bytes and preserve target-owned bytes."""
    official_block, official_inner = _canonical_pr_plan_block(official_text)
    digest = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
    if current_text == official_text or digest in PR_TEMPLATE_MANAGED_BASELINE_SHA256:
        return official_text, "refresh_managed_baseline"

    newline = _pr_template_newline(current_text)
    block = official_block.replace("\r\n", "\n").replace("\n", newline)
    begin_count = current_text.count(PR_PLAN_BEGIN)
    end_count = current_text.count(PR_PLAN_END)
    if begin_count or end_count:
        if begin_count != 1 or end_count != 1:
            raise ValueError("target Pull Request template has ambiguous managed block markers")
        start = current_text.index(PR_PLAN_BEGIN)
        end_marker = current_text.index(PR_PLAN_END)
        if end_marker <= start:
            raise ValueError("target Pull Request template managed block markers are out of order")
        end = end_marker + len(PR_PLAN_END)
        return current_text[:start] + block + current_text[end:], "refresh_managed_block"

    headings = list(re.finditer(rf"(?m)^{re.escape(PR_PLAN_HEADING)}[ \t]*\r?$", current_text))
    if len(headings) > 1:
        raise ValueError("target Pull Request template has multiple Contributor Branch Plan headings")
    if headings:
        start = headings[0].start()
        following = re.search(r"(?m)^#{1,2}[ \t]+", current_text[headings[0].end() :])
        end = headings[0].end() + following.start() if following else len(current_text)
        section = current_text[start:end].strip(" \t\r\n").replace("\r\n", "\n")
        if section != official_inner.replace("\r\n", "\n"):
            raise ValueError("target Pull Request template has an unowned Contributor Branch Plan section")
        suffix = current_text[end:].lstrip("\r\n")
        separator = newline * (2 if suffix else 1)
        return current_text[:start] + block + separator + suffix, "adopt_legacy_managed_block"

    anchors = list(re.finditer(r"(?m)^## What Changed[ \t]*\r?$", current_text))
    offset = anchors[0].start() if len(anchors) == 1 else len(current_text)
    prefix = current_text[:offset]
    suffix = current_text[offset:]
    if not prefix:
        before = ""
    elif prefix.endswith(newline * 2):
        before = ""
    elif prefix.endswith(newline):
        before = newline
    else:
        before = newline * 2
    after = newline * (2 if suffix else 1)
    return prefix + before + block + after + suffix, "inject_managed_block"


def plan_pull_request_template_update(
    target: Path,
    wrapper_root: Path,
) -> dict[str, Any]:
    source = wrapper_root / "templates" / "target" / PR_TEMPLATE_PATH
    destination = target / PR_TEMPLATE_PATH
    if not source.is_file():
        raise ValueError(f"wrapper Pull Request template source is missing: {source}")
    if destination.is_symlink():
        raise ValueError(f"migration write path contains a symlink: {PR_TEMPLATE_PATH}")
    official_text = source.read_text(encoding="utf-8")
    if not destination.exists():
        _canonical_pr_plan_block(official_text)
        return {
            "path": PR_TEMPLATE_PATH,
            "mode": "create_managed_template",
            "changed": True,
            "target_owned_bytes_preserved": True,
        }
    if not destination.is_file():
        raise ValueError(f"target Pull Request template is not a regular file: {PR_TEMPLATE_PATH}")
    current_text = _read_text_preserving_newlines(destination)
    merged, mode = merge_pull_request_template(current_text, official_text)
    return {
        "path": PR_TEMPLATE_PATH,
        "mode": mode,
        "changed": merged != current_text,
        "target_owned_bytes_preserved": mode not in {"refresh_managed_baseline"},
    }


def _codex_hook_template_data() -> dict[str, Any]:
    template = Path(__file__).resolve().parents[1] / "templates" / "target" / CODEX_HOOKS_CONFIG
    if not template.is_file():
        raise ValueError(f"wrapper hook registration template is missing: {template}")
    try:
        data = json.loads(template.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid wrapper hook registration template: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("wrapper hook registration template must be a JSON object")
    return data


def _merge_codex_hooks_config(target: Path) -> tuple[dict[str, Any], str]:
    template = _codex_hook_template_data()
    wrapper_entry = template["hooks"][CODEX_START_HOOK_EVENT][0]
    path = target / CODEX_HOOKS_CONFIG
    if not path.exists():
        return template, "create"
    if not path.is_file():
        raise ValueError(f"{CODEX_HOOKS_CONFIG} must be a regular JSON file")
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {CODEX_HOOKS_CONFIG}: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"{CODEX_HOOKS_CONFIG} must contain a JSON object")
    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{CODEX_HOOKS_CONFIG} hooks must be a JSON object")
    session_start = hooks.setdefault(CODEX_START_HOOK_EVENT, [])
    if not isinstance(session_start, list):
        raise ValueError(f"{CODEX_HOOKS_CONFIG} {CODEX_START_HOOK_EVENT} must be a list")

    preserved: list[dict[str, Any]] = []
    wrapper_entry_found = False
    for index, entry in enumerate(session_start):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{CODEX_HOOKS_CONFIG} {CODEX_START_HOOK_EVENT}[{index}] must be an object"
            )
        handlers = entry.get("hooks")
        if not isinstance(handlers, list):
            raise ValueError(
                f"{CODEX_HOOKS_CONFIG} {CODEX_START_HOOK_EVENT}[{index}].hooks must be a list"
            )
        is_wrapper_entry = any(
            isinstance(handler, dict)
            and isinstance(handler.get("command"), str)
            and "evozeus_wrapper_start_check.py" in handler["command"]
            for handler in handlers
        )
        if is_wrapper_entry:
            wrapper_entry_found = True
        else:
            preserved.append(entry)
    hooks[CODEX_START_HOOK_EVENT] = [*preserved, wrapper_entry]
    if existing == json.loads(path.read_text(encoding="utf-8")):
        return existing, "already_registered"
    return existing, "refresh" if wrapper_entry_found else "merge"


def _frontmatter_end(text: str) -> int:
    lines = text.splitlines(keepends=True)
    if not lines or not re.fullmatch(r"---[ \t]*", lines[0].rstrip("\r\n")):
        return 0
    offset = len(lines[0])
    body_lines: list[str] = []
    for line in lines[1:]:
        offset += len(line)
        if re.fullmatch(r"(?:---|\.\.\.)[ \t]*", line.rstrip("\r\n")):
            break
        body_lines.append(line.rstrip("\r\n"))
    else:
        return 0

    def mapping_key_separator(line: str) -> int | None:
        quote: str | None = None
        escaped = False
        for index, character in enumerate(line):
            if escaped:
                escaped = False
                continue
            if quote == '"' and character == "\\":
                escaped = True
                continue
            if quote:
                if character == quote:
                    quote = None
                continue
            if character in {"'", '"'}:
                quote = character
                continue
            if character == ":" and (index + 1 == len(line) or line[index + 1] in " \t"):
                return index if line[:index].strip() else None
        return None

    saw_mapping_key = False
    explicit_key = False
    mapping_tag = False
    allows_indentless_sequence = False
    for line in body_lines:
        stripped = line.strip()
        if not stripped or line.lstrip().startswith("#"):
            continue
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                flow_value = json.loads(stripped)
            except json.JSONDecodeError:
                flow_value = None
            if isinstance(flow_value, dict):
                saw_mapping_key = True
                continue
        if stripped in {"{}", "!!map {}"} and not saw_mapping_key:
            saw_mapping_key = True
            continue
        if stripped == "!!map" and not saw_mapping_key:
            mapping_tag = True
            continue
        if line.startswith((" ", "\t")) and (saw_mapping_key or mapping_tag):
            continue
        if allows_indentless_sequence and (line == "-" or line.startswith("- ")):
            continue
        if line.startswith("? "):
            explicit_key = True
            continue
        if explicit_key and (line == ":" or line.startswith(": ")):
            saw_mapping_key = True
            explicit_key = False
            continue
        separator = mapping_key_separator(line)
        if separator is not None:
            saw_mapping_key = True
            allows_indentless_sequence = not line[separator + 1 :].strip()
            continue
        return 0
    if saw_mapping_key and not explicit_key:
        return offset
    return 0


def _markdown_headings(text: str) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    offset = 0
    frontmatter_end = _frontmatter_end(text)
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if offset < frontmatter_end:
            offset += len(line)
            continue
        if fence:
            fence_char, minimum_length = fence
            if re.match(
                rf"^[ ]{{0,3}}{re.escape(fence_char)}{{{minimum_length},}}[ \t]*$",
                content,
            ):
                fence = None
            offset += len(line)
            continue

        fence_match = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", content)
        if fence_match:
            marker = fence_match.group(1)
            fence = (marker[0], len(marker))
            offset += len(line)
            continue

        heading_match = re.match(r"^[ ]{0,3}(#{1,6})(?:[ \t]+|$)(.*)$", content)
        if heading_match:
            hashes = heading_match.group(1)
            body = heading_match.group(2).rstrip(" \t")
            body = re.sub(r"[ \t]+#+$", "", body).rstrip(" \t")
            canonical = hashes + (f" {body}" if body else "")
            headings.append((offset, len(hashes), canonical))
        offset += len(line)
    return headings


def _markdown_section_span(text: str, heading: str) -> tuple[int, int] | None:
    level_match = re.match(r"^(#{1,6})[ \t]+", heading)
    if not level_match:
        raise ValueError(f"invalid Markdown heading: {heading}")
    level = len(level_match.group(1))
    headings = _markdown_headings(text)
    for index, (start, found_level, canonical) in enumerate(headings):
        if found_level != level or canonical != heading:
            continue
        end = next(
            (
                next_start
                for next_start, next_level, _ in headings[index + 1 :]
                if next_level <= level
            ),
            len(text),
        )
        return start, end
    return None


def _read_text_preserving_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text_preserving_newlines(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def build_harness_activation_block(newline: str = "\n") -> str:
    """Return the entire wrapper-owned target entry contract."""
    return newline.join(
        [
            HARNESS_ENTRY_BEGIN,
            "**CRITICAL — 进入业务主链路前 MUST 使用 Read 工具读取并执行",
            f"[{TARGET_HARNESS_SKILL}]({TARGET_HARNESS_SKILL})。**",
            HARNESS_ENTRY_END,
        ]
    )


def _harness_entry_pattern() -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(HARNESS_ENTRY_BEGIN)}[ \t]*\r?\n"
        rf".*?^{re.escape(HARNESS_ENTRY_END)}[ \t]*(?:\r?\n|$)(?:\r?\n)*",
        re.DOTALL | re.MULTILINE,
    )


def _mask_markdown_fenced_code(text: str) -> str:
    """Mask non-contract Markdown bytes while preserving offsets and newlines."""
    masked: list[str] = []
    fence: tuple[str, int] | None = None
    html_block: tuple[str, re.Pattern[str] | None] | None = None

    def mask_line(line: str) -> str:
        return "".join(character if character in "\r\n" else " " for character in line)

    def is_complete_html_tag(line: str) -> bool:
        match = re.match(r"^[ ]{0,3}</?[A-Za-z][A-Za-z0-9-]*", line)
        if not match:
            return False
        quote: str | None = None
        for index in range(match.end(), len(line)):
            character = line[index]
            if quote:
                if character == quote:
                    quote = None
                continue
            if character in {"'", '"'}:
                quote = character
                continue
            if character == ">":
                return not line[index + 1 :].strip()
        return False

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if fence:
            fence_char, minimum_length = fence
            if re.match(
                rf"^[ ]{{0,3}}{re.escape(fence_char)}{{{minimum_length},}}[ \t]*$",
                content,
            ):
                fence = None
            masked.append(mask_line(line))
            continue
        if html_block:
            mode, end_pattern = html_block
            if (mode == "blank" and not content.strip()) or (
                end_pattern is not None and end_pattern.search(content)
            ):
                html_block = None
            masked.append(mask_line(line))
            continue
        fence_match = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", content)
        if fence_match:
            marker = fence_match.group(1)
            fence = (marker[0], len(marker))
            masked.append(mask_line(line))
        elif re.fullmatch(
            rf"(?:{re.escape(HARNESS_ENTRY_BEGIN)}|{re.escape(HARNESS_ENTRY_END)})[ \t]*",
            content,
        ):
            masked.append(line)
        elif html_match := re.match(
            r"^[ ]{0,3}<(script|pre|style|textarea)(?:[ \t>]|$)",
            content,
            re.IGNORECASE,
        ):
            end_pattern = re.compile(rf"</{html_match.group(1)}[ \t]*>", re.IGNORECASE)
            if not end_pattern.search(content[html_match.end() :]):
                html_block = ("pattern", end_pattern)
            masked.append(mask_line(line))
        elif re.match(r"^[ ]{0,3}<!--", content):
            if "-->" not in content[content.find("<!--") + 4 :]:
                html_block = ("pattern", re.compile(r"-->"))
            masked.append(mask_line(line))
        elif re.match(r"^[ ]{0,3}<\?", content):
            if "?>" not in content[content.find("<?") + 2 :]:
                html_block = ("pattern", re.compile(r"\?>"))
            masked.append(mask_line(line))
        elif re.match(r"^[ ]{0,3}<![A-Z]", content):
            if ">" not in content[content.find("<!") + 2 :]:
                html_block = ("pattern", re.compile(r">"))
            masked.append(mask_line(line))
        elif re.match(r"^[ ]{0,3}<!\[CDATA\[", content):
            if "]]>" not in content[content.find("<![CDATA[") + 9 :]:
                html_block = ("pattern", re.compile(r"\]\]>"))
            masked.append(mask_line(line))
        elif re.match(
            r"^[ ]{0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[ \t/>]|$)",
            content,
            re.IGNORECASE,
        ) or is_complete_html_tag(content):
            html_block = ("blank", None)
            masked.append(mask_line(line))
        elif re.match(r"^(?: {4,}|\t)", content):
            masked.append(mask_line(line))
        else:
            masked.append(line)
    return "".join(masked)


def _harness_entry_markers_well_formed(text: str) -> bool:
    """Accept zero or more complete, non-nested top-level canonical entry blocks."""
    text = _mask_markdown_fenced_code(text)
    marker_pattern = re.compile(
        rf"^(?:(?P<begin>{re.escape(HARNESS_ENTRY_BEGIN)})|"
        rf"(?P<end>{re.escape(HARNESS_ENTRY_END)}))[ \t]*\r?$",
        re.MULTILINE,
    )
    entry_open = False
    for match in marker_pattern.finditer(text):
        if match.group("begin"):
            if entry_open:
                return False
            entry_open = True
        else:
            if not entry_open:
                return False
            entry_open = False
    return not entry_open


def _consume_following_newlines(text: str, offset: int) -> int:
    """Include wrapper-owned blank-line separators after a proven terminal."""
    while offset < len(text) and text[offset] in "\r\n":
        offset += 1
    return offset


def _managed_terminal_match(
    section: str,
    terminal_pattern: str,
    predecessor_sequences: tuple[tuple[str, ...], ...],
) -> re.Match[str] | None:
    """Accept a terminal only after a contiguous suffix from a known managed template."""
    terminator = re.search(terminal_pattern, section)
    if not terminator:
        return None
    preceding_lines = [
        line.strip()
        for line in section[: terminator.start()].splitlines()
        if line.strip()
    ]
    for sequence in predecessor_sequences:
        if len(preceding_lines) < len(sequence):
            continue
        suffix = preceding_lines[-len(sequence) :]
        if all(re.fullmatch(pattern, line) for pattern, line in zip(sequence, suffix)):
            return terminator
    return None


def _wrapper_owned_section_analysis(
    text: str,
) -> tuple[list[tuple[int, int]], list[str]]:
    """Find proven wrapper spans and report owned sections with unproven endings."""
    spans: list[tuple[int, int]] = []
    conflicts: list[str] = []
    signatures = {
        STATUS_SECTION_HEADING: (("Skill 入口 preflight", TARGET_WRAPPER_MANIFEST),),
        LEGACY_STATUS_SECTION_HEADING: (("Skill 入口 preflight", "wrapper"),),
        EVOLUTION_SECTION_HEADING: (
            ("本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环",),
            ("本 Skill 已由 EvoZeus-wrapper 接入自进化闭环",),
        ),
        WRAPPER_SECTION_HEADING: (("本区由 EvoZeus-CoEvolve 追加",),),
        LEGACY_WRAPPER_SECTION_HEADING: (("本区由 EvoZeus-wrapper 追加",),),
    }
    terminal_patterns = {
        STATUS_SECTION_HEADING: r"(?m)^(?:解决顺序|处理顺序|解决方法)：[^\r\n]*(?:\r?\n|$)",
        LEGACY_STATUS_SECTION_HEADING: r"(?m)^(?:解决顺序|处理顺序|解决方法)：[^\r\n]*(?:\r?\n|$)",
        EVOLUTION_SECTION_HEADING: r"(?m)^Wrapper harness version:[^\r\n]*(?:\r?\n|$)",
        WRAPPER_SECTION_HEADING: r"(?m)^- `manual_only`[^\r\n]*(?:\r?\n|$)",
        LEGACY_WRAPPER_SECTION_HEADING: r"(?m)^- `manual_only`[^\r\n]*(?:\r?\n|$)",
    }
    terminal_predecessors = {
        STATUS_SECTION_HEADING: (
            (
                r"- 检查命令：`python3 .*evozeus_wrapper_preflight\.py doctor --repo [^`]+`",
                r"- 如果 `~/.evozeus/\.projects`、git origin 或 runtime install 不一致：先修复为同一个 canonical repo，再继续。",
            ),
            (
                r"- 身份头固定以 `🧙🏻‍♂️` 开始；禁止使用 HTML、自定义图片或 shortcode 替代。",
                r"- 同一次 invocation 的后续 commentary 和 final 不重复；下一次 invocation 再展示一次。",
            ),
            (
                r"- Lesson Notice 的 Tag 为 `EvoZeus · Lesson`、状态为 `待记录`，只询问是否记录到 Skill Feedback Issue。",
                r"- Lesson 记录、Skill 修复、Harness 维护、UAT 与正式发布分别使用配置中的独立 kind；任何 Notice 都不扩张写入授权。",
            ),
        ),
        LEGACY_STATUS_SECTION_HEADING: (
            (
                r"- 检查命令：`python3 .*evozeus_wrapper_preflight\.py doctor --repo [^`]+`",
                r"- 如果 `~/.evozeus/\.projects`、git origin 或 runtime install 不一致：先修复为同一个 canonical repo，再继续。",
            ),
        ),
        EVOLUTION_SECTION_HEADING: (
            (
                r"Visibility: `(public|private)`",
                r"Current Skill version: `v\d+\.\d+\.\d+`",
            ),
        ),
        WRAPPER_SECTION_HEADING: (
            (
                r"- `bootstrap_skill`：[^\r\n]+",
                r"- `prompt_runtime_check`：[^\r\n]+",
            ),
        ),
        LEGACY_WRAPPER_SECTION_HEADING: (
            (
                r"- `bootstrap_skill`：[^\r\n]+",
                r"- `prompt_runtime_check`：[^\r\n]+",
            ),
        ),
    }
    headings = _markdown_headings(text)
    for index, (start, level, heading) in enumerate(headings):
        end = next(
            (
                next_start
                for next_start, next_level, _ in headings[index + 1 :]
                if next_level <= level
            ),
            len(text),
        )
        span = (start, end)
        accepted_signatures = signatures.get(heading)
        if accepted_signatures:
            section = text[start:end]
            if any(
                all(term in section for term in required_terms)
                for required_terms in accepted_signatures
            ):
                terminator = _managed_terminal_match(
                    section,
                    terminal_patterns[heading],
                    terminal_predecessors[heading],
                )
                if not terminator:
                    conflicts.append(
                        f"{heading} has a wrapper ownership signature but no proven managed terminal signature; "
                        "restore the managed section or use an approved manual repair"
                    )
                    continue
                span_end = _consume_following_newlines(
                    text,
                    start + terminator.end(),
                )
                span = (start, span_end)
                spans.append(span)
            continue
        if not (
            heading.startswith("## EvoZeus-CoEvolve Migration Note:")
            or heading.startswith("## EvoZeus-CoEvolve Version Refresh Note:")
            or heading.startswith("## EvoZeus-wrapper Migration Note:")
            or heading.startswith("## EvoZeus-wrapper Version Refresh Note:")
        ):
            continue
        section = text[start:end]
        if "Wrapper harness:" not in section or "- Layout:" not in section:
            continue
        terminator = _managed_terminal_match(
            section,
            r"(?m)^- Target business rules were preserved\.[ \t]*(?:\r?\n|$)",
            (
                (
                    r"- Wrapper harness: `v\d+\.\d+\.\d+ -> v\d+\.\d+\.\d+`",
                    r"- Layout: `[^`]+ -> [^`]+`",
                ),
            ),
        )
        if not terminator:
            conflicts.append(
                f"{heading} has a wrapper ownership signature but no proven managed terminal signature; "
                "restore the managed note or use an approved manual repair"
            )
            continue
        span_end = _consume_following_newlines(
            text,
            start + terminator.end(),
        )
        spans.append((start, span_end))
    return sorted(set(spans)), list(dict.fromkeys(conflicts))


def _wrapper_owned_section_spans(text: str) -> list[tuple[int, int]]:
    return _wrapper_owned_section_analysis(text)[0]


def _has_canonical_harness_entry(text: str) -> bool:
    normalized = text.replace("\r\n", "\n")
    visible = _mask_markdown_fenced_code(text).replace("\r\n", "\n")
    owned_spans, owned_conflicts = _wrapper_owned_section_analysis(text)
    content = normalized[_frontmatter_end(normalized) :].lstrip()
    lines = content.splitlines()
    visible_markers = re.findall(
        rf"^({re.escape(HARNESS_ENTRY_BEGIN)}|{re.escape(HARNESS_ENTRY_END)})[ \t]*$",
        visible,
        re.MULTILINE,
    )
    precedes_business = content.startswith(HARNESS_ENTRY_BEGIN) or bool(
        lines
        and lines[0].startswith("# ")
        and "\n".join(lines[1:]).lstrip().startswith(HARNESS_ENTRY_BEGIN)
    )
    return (
        visible_markers.count(HARNESS_ENTRY_BEGIN) == 1
        and visible_markers.count(HARNESS_ENTRY_END) == 1
        and build_harness_activation_block() in visible
        and precedes_business
        and not owned_spans
        and not owned_conflicts
    )


def _instruction_insert_index(text: str) -> int:
    frontmatter_end = _frontmatter_end(text)
    if frontmatter_end:
        return frontmatter_end
    first_line_end = text.find("\n")
    first_line = text if first_line_end == -1 else text[:first_line_end].rstrip("\r")
    if first_line.startswith("# "):
        return len(text) if first_line_end == -1 else first_line_end + 1
    return 0


def validate_instruction_surface_for_harness_entry(target: Path, surface_rel: str) -> str:
    """Return the read-only surface only when canonical entry migration is provably safe."""
    surface = safe_target_relative_file(target, surface_rel)
    if surface is None:
        raise ValueError(f"instruction surface is missing, unsafe, or symlinked: {surface_rel}")
    text = _read_text_preserving_newlines(surface)
    if not _harness_entry_markers_well_formed(text):
        raise ValueError(
            f"instruction surface has an unbalanced canonical Harness entry or invalid nesting: "
            f"{surface_rel}"
        )
    _, conflicts = _wrapper_owned_section_analysis(text)
    if conflicts:
        raise ValueError(
            f"instruction surface {surface_rel} cannot be migrated safely:\n- "
            + "\n- ".join(conflicts)
        )
    return text


def migrate_instruction_surface_to_harness_entry(target: Path, surface_rel: str) -> bool:
    """Replace proven wrapper-owned legacy blocks with the compact canonical Read block."""
    surface = safe_target_relative_file(target, surface_rel)
    if surface is None:
        raise ValueError(f"instruction surface is missing, unsafe, or symlinked: {surface_rel}")
    original = validate_instruction_surface_for_harness_entry(target, surface_rel)
    if _has_canonical_harness_entry(original):
        return False

    entry_spans = [
        (match.start(), match.end())
        for match in _harness_entry_pattern().finditer(_mask_markdown_fenced_code(original))
    ]
    updated = original
    for start, end in reversed(entry_spans):
        updated = updated[:start] + updated[end:]
    owned_spans, owned_conflicts = _wrapper_owned_section_analysis(updated)
    if owned_conflicts:
        raise ValueError("cannot safely migrate instruction surface:\n- " + "\n- ".join(owned_conflicts))
    for start, end in sorted(owned_spans, reverse=True):
        updated = updated[:start] + updated[end:]

    newline = "\r\n" if "\r\n" in original else "\n"
    insert_at = _instruction_insert_index(updated)
    prefix = updated[:insert_at]
    suffix = updated[insert_at:]
    before = "" if not prefix or prefix.endswith(newline * 2) else newline if prefix.endswith(newline) else newline * 2
    after = "" if suffix.startswith(newline * 2) else newline if suffix.startswith(newline) else newline * 2
    updated = prefix + before + build_harness_activation_block(newline) + after + suffix

    if updated == original:
        return False
    _write_text_preserving_newlines(surface, updated)
    return True


def _replace_markdown_section(text: str, heading: str, replacement: str) -> str:
    span = _markdown_section_span(text, heading)
    if span:
        start, end = span
        suffix = text[end:]
        separator = "\n\n" if suffix else "\n"
        return text[:start] + replacement.strip("\r\n") + separator + suffix

    insert_at = _frontmatter_end(text)
    prefix = text[:insert_at]
    suffix = text[insert_at:]
    separator = "" if not prefix or prefix.endswith("\n\n") else "\n"
    return prefix + separator + replacement.strip("\r\n") + "\n\n" + suffix


def _refresh_owned_markdown_section(
    text: str,
    heading: str,
    latest_wrapper_version: str,
) -> str:
    span = _markdown_section_span(text, heading)
    if not span:
        return text
    start, end = span
    section = text[start:end]
    section = section.replace("--latest-version <wrapper-version> ", "")
    section = re.sub(
        r"(?m)^(Wrapper harness version:[ \t]*)`v\d+\.\d+\.\d+`[ \t]*(?=\r?$)",
        rf"\g<1>`{latest_wrapper_version}`",
        section,
    )
    return text[:start] + section + text[end:]


def _refresh_migration_instruction_surface(
    target: Path,
    manifest: dict[str, Any],
    current_wrapper_version: str | None,
    latest_wrapper_version: str,
    *,
    from_layout: str,
    to_layout: str,
    layout_migration_required: bool,
) -> tuple[str, bool]:
    architecture = detect_target_architecture(target)
    surface_rel = manifest.get("instruction_surface") or architecture.get("root_entry") or "SKILL.md"
    if not isinstance(surface_rel, str):
        raise ValueError("migration instruction_surface must be a relative string")
    return surface_rel, migrate_instruction_surface_to_harness_entry(target, surface_rel)


def _legacy_layout_sources(target: Path) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    manifest_status = wrapper_manifest_status(target)
    if (
        manifest_status["current_manifest_detected"]
        and not manifest_status["legacy_manifest_detected"]
        and _read_manifest_json(wrapper_manifest_path(target)).get("layout_version") == 2
    ):
        return grouped

    def add(source: Path, destination: str) -> None:
        if source.is_file():
            grouped.setdefault(destination, []).append(source)

    for source_rel, destination in LEGACY_LAYOUT_FILE_MAP:
        add(target / source_rel, destination)

    for rel in manifest_status["legacy_manifest_paths"]:
        add(target / rel, TARGET_WRAPPER_MANIFEST)

    for source_dir_rel, destination_dir in LEGACY_LAYOUT_TREE_MAP:
        source_dir = target / source_dir_rel
        if not source_dir.is_dir() or source_dir.is_symlink():
            continue
        for source in sorted(source_dir.rglob("*")):
            if source.is_file():
                rel = source.relative_to(source_dir)
                add(source, str(Path(destination_dir) / rel))
    return grouped


def _harness_contract_needs_migration(
    target: Path,
    manifest: dict[str, Any] | None,
    instruction_surface: object,
) -> bool:
    manifest = manifest or {}
    managed_files = manifest.get("managed_files")
    contributor_branch = manifest.get("contributor_branch")
    expected_branch_contract = {
        "profile": "coevolve_target_skillware_consumer",
        "consumer_path": TARGET_BRANCH_CONSUMER_SCRIPT,
        "contract_path": TARGET_BRANCH_CONTRACT,
        "provenance_path": TARGET_BRANCH_PROVENANCE,
        "planner_path": TARGET_BRANCH_PLANNER,
        "permission_authority": "core_planner_live_github_evidence",
        "runtime_network_fetch": False,
        "ledger_root": "~/.evozeus/coevolve/branch-plans/OWNER/REPO",
    }
    if (
        not _manifest_proves_canonical_harness_ownership(manifest)
        or any(path not in managed_files for path in (
            TARGET_HARNESS_SKILL,
            TARGET_BRANCH_CONSUMER_SCRIPT,
            TARGET_BRANCH_CONTRACT,
            TARGET_BRANCH_PROVENANCE,
            TARGET_BRANCH_PLANNER,
        ))
        or contributor_branch != expected_branch_contract
    ):
        return True
    source_root = Path(__file__).resolve().parents[1]
    branch_asset_sources = {
        TARGET_BRANCH_CONSUMER_SCRIPT: source_root / "scripts" / "evozeus_branch_consumer.py",
        TARGET_BRANCH_CONTRACT: source_root / "templates" / "target" / "contracts" / "v1" / "contributor-branch-contract.json",
        TARGET_BRANCH_PROVENANCE: source_root / "templates" / "target" / "contracts" / "v1" / "contributor-branch-provenance.json",
        TARGET_BRANCH_PLANNER: source_root / "templates" / "target" / "scripts" / "evozeus-branch-preflight.mjs",
    }
    for relative_path, source in branch_asset_sources.items():
        installed = safe_target_relative_file(target, relative_path)
        if installed is None or not source.is_file() or installed.read_bytes() != source.read_bytes():
            return True
    harness = safe_target_relative_file(target, TARGET_HARNESS_SKILL)
    if harness is None:
        return True
    harness_text = _read_text_preserving_newlines(harness)
    if not canonical_harness_skill_text_valid(harness_text):
        return True
    if not isinstance(instruction_surface, str):
        return True
    surface = safe_target_relative_file(target, instruction_surface)
    if surface is None:
        return True
    return not _has_canonical_harness_entry(_read_text_preserving_newlines(surface))


def _manifest_proves_canonical_harness_ownership(manifest: dict[str, Any]) -> bool:
    managed_files = manifest.get("managed_files")
    return (
        manifest.get("harness_skill_path") == TARGET_HARNESS_SKILL
        and manifest.get("harness_skill_version") == HARNESS_SKILL_VERSION
        and manifest.get("harness_skill_managed") is True
        and isinstance(managed_files, list)
        and TARGET_HARNESS_SKILL in managed_files
    )


def _canonical_harness_path_is_proven_owned(
    target: Path,
    manifest: dict[str, Any],
) -> bool:
    if _manifest_proves_canonical_harness_ownership(manifest):
        return True
    harness = safe_target_relative_file(target, TARGET_HARNESS_SKILL)
    if harness is None:
        return False
    template = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "target"
        / LEGACY_TARGET_EVOINFRA_DIR
        / "skills"
        / "using-evozeus-harness"
        / "SKILL.md"
    )
    return template.is_file() and harness.read_bytes() == template.read_bytes()


def canonical_harness_skill_text_valid(harness_text: str) -> bool:
    """Validate the canonical Harness Skill using the same bounded frontmatter contract as preflight."""
    frontmatter = re.match(
        r"\A---\r?\n(?P<body>.*?)\r?\n(?:---|\.\.\.)\r?\n",
        harness_text,
        re.DOTALL,
    )
    if not frontmatter:
        return False
    body = frontmatter.group("body")
    if not re.search(
        r"(?m)^name:[ \t]*[\"']?using-evozeus-harness[\"']?[ \t]*\r?$",
        body,
    ):
        return False
    metadata = re.search(
        r"(?m)^metadata:[ \t]*\r?\n(?P<values>(?:^[ \t]+[^\r\n]*(?:\r?\n|$))*)",
        body,
    )
    if not metadata or not re.search(
        rf"(?m)^[ \t]+version:[ \t]*[\"']?{re.escape(HARNESS_SKILL_VERSION)}[\"']?[ \t]*\r?$",
        metadata.group("values"),
    ):
        return False
    if any(term not in harness_text for term in HARNESS_SKILL_REQUIRED_TERMS):
        return False
    return True


def plan_target_layout_migration(
    target: Path,
    latest_version: str | None = None,
    today: date | None = None,
    *,
    require_clean_git: bool = False,
    wrapper_root: Path | None = None,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    wrapper_root = (
        Path(__file__).resolve().parents[1]
        if wrapper_root is None
        else wrapper_root.expanduser().resolve()
    )
    manifest_status = wrapper_manifest_status(target)
    conflicts: list[str] = []
    if manifest_status["conflict"]:
        conflicts.append("legacy wrapper manifests contain different data")
    try:
        verify_managed_snapshot(wrapper_root / "templates" / "target")
    except BranchConsumerError as exc:
        conflicts.append(f"wrapper contributor branch snapshot is invalid: {exc}")
    git_status = run_command(
        ["git", "-C", str(target), "status", "--porcelain", "--untracked-files=normal"]
    )
    worktree_status_available = git_status["returncode"] == 0
    worktree_clean = worktree_status_available and not git_status.get("stdout", "").strip()
    if worktree_status_available and not worktree_clean:
        conflicts.append("target git worktree is not clean; commit or stash changes before migration")
    elif require_clean_git and not worktree_status_available:
        detail = (git_status.get("stderr") or git_status.get("stdout") or "unknown error").strip()
        conflicts.append(f"target git worktree could not be verified: {detail}")

    moves: list[dict[str, str]] = []
    for destination_rel, sources in sorted(_legacy_layout_sources(target).items()):
        destination = target / destination_rel
        primary = sources[0]
        if destination.is_file():
            for source in sources:
                if _same_file_contents(source, destination):
                    moves.append(
                        {
                            "action": "remove_duplicate",
                            "source": str(source.relative_to(target)),
                            "destination": destination_rel,
                        }
                    )
                else:
                    conflicts.append(
                        f"destination differs from legacy source: {destination_rel} <- {source.relative_to(target)}"
                    )
            continue
        if destination.exists():
            conflicts.append(f"destination is not a regular file: {destination_rel}")
            continue

        moves.append(
            {
                "action": "move",
                "source": str(primary.relative_to(target)),
                "destination": destination_rel,
            }
        )
        for duplicate in sources[1:]:
            if _same_file_contents(duplicate, primary):
                moves.append(
                    {
                        "action": "remove_duplicate",
                        "source": str(duplicate.relative_to(target)),
                        "destination": destination_rel,
                    }
                )
            else:
                conflicts.append(
                    f"multiple legacy sources differ for {destination_rel}: "
                    f"{primary.relative_to(target)} vs {duplicate.relative_to(target)}"
                )

    current_manifest = (
        None if manifest_status["conflict"] else load_wrapper_manifest(target, allow_legacy=True)
    )
    current_version = current_manifest.get("wrapper_version") if current_manifest else None
    layout_migration_required = bool(moves) or manifest_status["migration_required"]
    version_refresh_required = False
    if latest_version and current_version:
        try:
            version_refresh_required = version_key(latest_version) > version_key(current_version)
        except ValueError as exc:
            conflicts.append(str(exc))
    codex_hooks_update = None
    instruction_surface = (
        (current_manifest or {}).get("instruction_surface")
        or detect_target_architecture(target).get("root_entry")
        or "SKILL.md"
    )
    instruction_surface_migration_required = _harness_contract_needs_migration(
        target,
        current_manifest,
        instruction_surface,
    )
    requires_migration = (
        layout_migration_required
        or version_refresh_required
        or instruction_surface_migration_required
    )
    pull_request_template_update = None
    if requires_migration:
        harness_destination = target / TARGET_HARNESS_SKILL
        if (
            (harness_destination.exists() or harness_destination.is_symlink())
            and not _canonical_harness_path_is_proven_owned(target, current_manifest or {})
        ):
            conflicts.append(
                "existing canonical Harness Skill is not proven wrapper-managed; "
                "preserve it and use an approved Harness repair"
            )
        try:
            pull_request_template_update = plan_pull_request_template_update(target, wrapper_root)
        except ValueError as exc:
            conflicts.append(str(exc))
        try:
            _, codex_hooks_update = _merge_codex_hooks_config(target)
        except ValueError as exc:
            conflicts.append(str(exc))
        if not isinstance(instruction_surface, str):
            conflicts.append("migration instruction_surface must be a relative string")
        else:
            surface_file = safe_target_relative_file(target, instruction_surface)
            if surface_file is None:
                conflicts.append(f"migration instruction surface is missing: {instruction_surface}")
            else:
                surface_text = _read_text_preserving_newlines(surface_file)
                if not _harness_entry_markers_well_formed(surface_text):
                    conflicts.append(
                        "instruction surface has an unbalanced canonical Harness entry "
                        f"or invalid nesting: {instruction_surface}"
                    )
                _, section_conflicts = _wrapper_owned_section_analysis(surface_text)
                conflicts.extend(
                    f"instruction surface {instruction_surface}: {conflict}"
                    for conflict in section_conflicts
                )
        harness_identity_fields = {
            "harness_skill_path",
            "harness_skill_version",
            "harness_skill_managed",
        }
        present_identity_fields = harness_identity_fields.intersection(current_manifest or {})
        if present_identity_fields and present_identity_fields != harness_identity_fields:
            conflicts.append("manifest canonical Harness Skill identity is incomplete")
        elif present_identity_fields:
            harness_path = (current_manifest or {}).get("harness_skill_path")
            harness_version = (current_manifest or {}).get("harness_skill_version")
            harness_managed = (current_manifest or {}).get("harness_skill_managed")
            if harness_path != TARGET_HARNESS_SKILL:
                conflicts.append(
                    f"manifest harness_skill_path must use canonical path: {TARGET_HARNESS_SKILL}"
                )
            if harness_version != HARNESS_SKILL_VERSION:
                conflicts.append(
                    "manifest canonical Harness Skill version is incompatible: "
                    f"{harness_version}; expected {HARNESS_SKILL_VERSION}"
                )
            if harness_managed is not True:
                conflicts.append("manifest canonical Harness Skill must remain wrapper managed")
    migration_record = (
        f"{TARGET_EVOINFRA_DIR}/docs/migrations/"
        f"{(today or date.today()).isoformat()}-layout-v1-to-v2.md"
        if layout_migration_required
        else (
            f"{TARGET_EVOINFRA_DIR}/docs/migrations/"
            f"{(today or date.today()).isoformat()}-{current_version}-to-{latest_version}.md"
        )
    )
    if requires_migration and not manifest_status["active_manifest_path"]:
        conflicts.append("legacy wrapper manifest is missing; repair or adopt the harness before migration")
    if requires_migration and (target / migration_record).exists():
        conflicts.append(f"migration record already exists: {migration_record}")

    text_rewrite_candidates = [
        str(path.relative_to(target)) for path in target_infra_text_files(target)
    ]
    generated_cache_candidates = [
        str(path.relative_to(target))
        for pattern in (
            ".codex/hooks/__pycache__/evozeus_wrapper_start_check.*.pyc",
            "scripts/__pycache__/evozeus_wrapper_preflight.*.pyc",
            f"{TARGET_EVOINFRA_DIR}/scripts/__pycache__/evozeus_notice.*.pyc",
            f"{TARGET_EVOINFRA_DIR}/scripts/__pycache__/evozeus_branch_consumer.*.pyc",
        )
        for path in target.glob(pattern)
        if path.is_file()
    ]
    managed_file_refreshes = [
        CODEX_HOOKS_CONFIG,
        TARGET_PREFLIGHT_SCRIPT,
        TARGET_NOTICE_SCRIPT,
        TARGET_BRANCH_CONSUMER_SCRIPT,
        TARGET_BRANCH_CONTRACT,
        TARGET_BRANCH_PROVENANCE,
        TARGET_BRANCH_PLANNER,
        CODEX_START_HOOK_SCRIPT,
        TARGET_ONBOARDING_GUIDE,
        TARGET_FEEDBACK_POLICY,
        TARGET_AUDIT_RULE,
        TARGET_NOTICE_POLICY,
        TARGET_HARNESS_SKILL,
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/pull_request_template.md",
        ".github/workflows/evozeus-wrapper-preflight.yml",
    ]
    if requires_migration:
        write_candidates = {
            instruction_surface,
            TARGET_WRAPPER_MANIFEST,
            migration_record,
            *managed_file_refreshes,
            *text_rewrite_candidates,
            *generated_cache_candidates,
            *(
                item.get(key)
                for item in moves
                for key in ("source", "destination")
                if isinstance(item, dict)
            ),
        }
        for relative in sorted(item for item in write_candidates if isinstance(item, str) and item):
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                conflicts.append(f"migration write path escapes target repository: {relative}")
                continue
            cursor = target
            for index, part in enumerate(relative_path.parts):
                cursor /= part
                if cursor.is_symlink():
                    conflicts.append(
                        "migration write path contains a symlink: "
                        + str(cursor.relative_to(target))
                    )
                    break
                if cursor.exists() and index < len(relative_path.parts) - 1 and not cursor.is_dir():
                    conflicts.append(
                        "migration write path parent is not a directory: "
                        + str(cursor.relative_to(target))
                    )
                    break
                if cursor.exists() and index == len(relative_path.parts) - 1 and not cursor.is_file():
                    conflicts.append(
                        "migration write path is not a regular file: "
                        + str(cursor.relative_to(target))
                    )
                    break

    return {
        "stage": "harness_layout_migration",
        "target": str(target),
        "writes": False,
        "from_layout": "scattered-v1" if layout_migration_required else "consolidated-v2",
        "to_layout": "consolidated-v2",
        "target_wrapper_dir": TARGET_EVOINFRA_DIR,
        "manifest_path": TARGET_WRAPPER_MANIFEST,
        "from_manifest_source": manifest_status["manifest_source"],
        "current_version": current_version,
        "latest_version": latest_version or current_version,
        "migration_required": requires_migration,
        "layout_migration_required": layout_migration_required,
        "version_refresh_required": version_refresh_required,
        "instruction_surface_migration_required": instruction_surface_migration_required,
        "migration_record": migration_record if requires_migration else None,
        "moves": moves,
        "managed_file_refreshes": managed_file_refreshes,
        "pull_request_template_update": pull_request_template_update,
        "codex_hooks_update": codex_hooks_update,
        "instruction_surface": instruction_surface,
        "preserved_host_entrypoints": [
            CODEX_HOOKS_CONFIG,
            ".github/ISSUE_TEMPLATE/",
            ".github/pull_request_template.md",
            ".github/workflows/evozeus-wrapper-preflight.yml",
        ],
        "conflicts": conflicts,
        "worktree_clean": worktree_clean,
        "worktree_status_available": worktree_status_available,
        "worktree_status_error": (
            None
            if worktree_status_available
            else (git_status.get("stderr") or git_status.get("stdout") or "unknown error").strip()
        ),
        "text_rewrite_candidates": text_rewrite_candidates,
        "generated_cache_candidates": generated_cache_candidates,
        "can_apply": requires_migration and not conflicts,
        "rollback": "revert the migration commit; migration must run in a clean target worktree",
    }


def _remove_empty_legacy_dirs(target: Path) -> list[str]:
    candidates = [
        ".codex/hooks/__pycache__",
        ".codex/hooks",
        "scripts/__pycache__",
        "docs/designs",
        "docs/wrapper-migrations",
        "docs",
        "scripts",
        LEGACY_TARGET_EVOINFRA_DIR,
        OLDEST_TARGET_EVOINFRA_DIR,
    ]
    removed: list[str] = []
    for rel in candidates:
        path = target / rel
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue
        removed.append(rel)
    return removed


def _remove_legacy_wrapper_caches(target: Path) -> list[str]:
    patterns = [
        ".codex/hooks/__pycache__/evozeus_wrapper_start_check.*.pyc",
        "scripts/__pycache__/evozeus_wrapper_preflight.*.pyc",
        f"{TARGET_EVOINFRA_DIR}/scripts/__pycache__/evozeus_notice.*.pyc",
        f"{TARGET_EVOINFRA_DIR}/scripts/__pycache__/evozeus_branch_consumer.*.pyc",
    ]
    removed: list[str] = []
    for pattern in patterns:
        for path in target.glob(pattern):
            if path.is_file():
                path.unlink()
                removed.append(str(path.relative_to(target)))
    return removed


def _refresh_migrated_managed_files(
    target: Path,
    wrapper_version: str | None,
    wrapper_root: Path | None = None,
) -> list[str]:
    wrapper_root = (
        Path(__file__).resolve().parents[1]
        if wrapper_root is None
        else wrapper_root.expanduser().resolve()
    )
    refresh_map = [
        (
            wrapper_root / "scripts" / "evozeus_wrapper_preflight.py",
            target / TARGET_PREFLIGHT_SCRIPT,
        ),
        (
            wrapper_root / "scripts" / "evozeus_notice.py",
            target / TARGET_NOTICE_SCRIPT,
        ),
        (
            wrapper_root / "scripts" / "evozeus_branch_consumer.py",
            target / TARGET_BRANCH_CONSUMER_SCRIPT,
        ),
        (
            wrapper_root / "templates" / "target" / "contracts" / "v1" / "contributor-branch-contract.json",
            target / TARGET_BRANCH_CONTRACT,
        ),
        (
            wrapper_root / "templates" / "target" / "contracts" / "v1" / "contributor-branch-provenance.json",
            target / TARGET_BRANCH_PROVENANCE,
        ),
        (
            wrapper_root / "templates" / "target" / "scripts" / "evozeus-branch-preflight.mjs",
            target / TARGET_BRANCH_PLANNER,
        ),
        (
            wrapper_root / "templates" / "target" / ".codex" / "hooks" / "evozeus_wrapper_start_check.py",
            target / CODEX_START_HOOK_SCRIPT,
        ),
        (
            wrapper_root / "templates" / "target" / ".github" / "workflows" / "evozeus-wrapper-preflight.yml",
            target / ".github" / "workflows" / "evozeus-wrapper-preflight.yml",
        ),
        (
            wrapper_root / "templates" / "target" / ".github" / "pull_request_template.md",
            target / ".github" / "pull_request_template.md",
        ),
        (
            wrapper_root / "templates" / "target" / "docs" / "onboarding.md",
            target / TARGET_ONBOARDING_GUIDE,
        ),
        (
            wrapper_root / "templates" / "target" / ".evozeus_evoinfra" / "feedback-policy.json",
            target / TARGET_FEEDBACK_POLICY,
        ),
        (
            wrapper_root / "templates" / "target" / ".evozeus_evoinfra" / "audit-rule.md",
            target / TARGET_AUDIT_RULE,
        ),
        (
            wrapper_root / "templates" / "target" / ".evozeus_evoinfra" / "notice-policy.json",
            target / TARGET_NOTICE_POLICY,
        ),
        (
            wrapper_root
            / "templates"
            / "target"
            / ".evozeus_evoinfra"
            / "skills"
            / "using-evozeus-harness"
            / "SKILL.md",
            target / TARGET_HARNESS_SKILL,
        ),
    ]
    refreshed: list[str] = []
    exact_snapshot_destinations = {
        target / TARGET_BRANCH_CONTRACT,
        target / TARGET_BRANCH_PROVENANCE,
        target / TARGET_BRANCH_PLANNER,
    }
    for source, destination in refresh_map:
        if not source.is_file():
            raise ValueError(f"wrapper migration source is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination in exact_snapshot_destinations:
            shutil.copy2(source, destination)
        else:
            text = source.read_text(encoding="utf-8")
            if source.name == "evozeus_wrapper_start_check.py":
                text = text.replace("{{WRAPPER_VERSION}}", wrapper_version or "")
            if destination == target / PR_TEMPLATE_PATH and destination.exists():
                current = _read_text_preserving_newlines(destination)
                text, _ = merge_pull_request_template(current, text)
                _write_text_preserving_newlines(destination, text)
            else:
                destination.write_text(text, encoding="utf-8")
        if destination.suffix == ".py":
            destination.chmod(0o755)
        refreshed.append(str(destination.relative_to(target)))
    return refreshed


def migrate_target_layout(
    target: Path,
    latest_version: str | None = None,
    today: date | None = None,
    *,
    wrapper_root: Path | None = None,
    require_clean_git: bool = False,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    plan = plan_target_layout_migration(
        target,
        latest_version,
        today,
        require_clean_git=require_clean_git,
        wrapper_root=wrapper_root,
    )
    if plan["conflicts"]:
        raise ValueError("cannot migrate wrapper layout:\n- " + "\n- ".join(plan["conflicts"]))
    if not plan["migration_required"]:
        return {**plan, "writes": False, "actions": [], "changed_files": []}

    actions: list[str] = []
    changed_files: list[str] = []
    for move in plan["moves"]:
        source = target / move["source"]
        destination = target / move["destination"]
        if move["action"] == "move":
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            actions.append(f"move {move['source']} -> {move['destination']}")
            changed_files.extend([move["source"], move["destination"]])
        else:
            source.unlink()
            actions.append(f"remove duplicate {move['source']}")
            changed_files.append(move["source"])

    for path in target_infra_text_files(target):
        if rewrite_target_infra_text_file(path):
            changed_files.append(str(path.relative_to(target)))
    if rewrite_dashboard_contact_link(target):
        changed_files.append(".github/ISSUE_TEMPLATE/config.yml")

    refreshed_files = _refresh_migrated_managed_files(
        target,
        latest_version or plan["current_version"],
        wrapper_root,
    )
    changed_files.extend(refreshed_files)
    for path in refreshed_files:
        if path == PR_TEMPLATE_PATH and plan.get("pull_request_template_update"):
            mode = plan["pull_request_template_update"]["mode"]
            actions.append(f"{mode} {path}")
        else:
            actions.append(f"refresh managed file {path}")

    merged_hooks, hooks_action = _merge_codex_hooks_config(target)
    hooks_path = target / CODEX_HOOKS_CONFIG
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    if hooks_action != "already_registered":
        hooks_path.write_text(json.dumps(merged_hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed_files.append(CODEX_HOOKS_CONFIG)
    actions.append(f"{hooks_action} Codex SessionStart registration in {CODEX_HOOKS_CONFIG}")

    manifest_path = wrapper_manifest_path(target)
    if not manifest_path.is_file():
        raise ValueError(f"migration did not produce {TARGET_WRAPPER_MANIFEST}")
    manifest = _read_manifest_json(manifest_path)
    manifest = rewrite_target_infra_json(manifest)
    installed_version = latest_version or manifest.get("wrapper_version")
    if not installed_version:
        raise ValueError("migration requires a valid wrapper version")
    instruction_surface, surface_changed = _refresh_migration_instruction_surface(
        target,
        manifest,
        plan["current_version"],
        installed_version,
        from_layout=plan["from_layout"],
        to_layout=plan["to_layout"],
        layout_migration_required=plan["layout_migration_required"],
    )
    if surface_changed:
        changed_files.append(instruction_surface)
        actions.append(f"write canonical Harness Skill activation block in {instruction_surface}")
    refreshed_contract = build_wrapper_manifest(
        repo=manifest.get("canonical_repo") or "OWNER/REPO",
        wrapper_version=installed_version,
        managed_files=WRAPPER_MANAGED_FILES,
        install_links=manifest.get("install_links") or [],
        instruction_surface=instruction_surface,
        integration=detect_target_architecture(target)["integration"],
        onboarding=manifest.get("onboarding"),
        dashboard=manifest.get("dashboard"),
    )
    manifest["wrapper_repo"] = refreshed_contract["wrapper_repo"]
    manifest["wrapper_version"] = installed_version
    manifest["applied_at"] = (today or date.today()).isoformat()
    manifest["layout_version"] = 2
    manifest["target_wrapper_dir"] = TARGET_EVOINFRA_DIR
    manifest["target_infra_dir"] = TARGET_EVOINFRA_DIR
    if plan["layout_migration_required"]:
        manifest["migrated_from_layout"] = "scattered-v1"
    manifest["legacy_layout_dirs"] = [LEGACY_TARGET_EVOINFRA_DIR, OLDEST_TARGET_EVOINFRA_DIR]
    manifest["managed_files"] = WRAPPER_MANAGED_FILES
    manifest["hook_registration"] = refreshed_contract["hook_registration"]
    manifest["integration"] = refreshed_contract["integration"]
    manifest["onboarding"] = refreshed_contract["onboarding"]
    manifest["dashboard"] = refreshed_contract["dashboard"]
    manifest["contributor_branch"] = refreshed_contract["contributor_branch"]
    manifest["instruction_surface"] = instruction_surface
    manifest["harness_skill_path"] = refreshed_contract["harness_skill_path"]
    manifest["harness_skill_version"] = refreshed_contract["harness_skill_version"]
    manifest["harness_skill_managed"] = refreshed_contract["harness_skill_managed"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    changed_files.append(TARGET_WRAPPER_MANIFEST)

    migration_record = target / plan["migration_record"]
    migration_record.parent.mkdir(parents=True, exist_ok=True)
    migration_record.write_text(
        "\n".join(
            [
                (
                    "# EvoZeus-CoEvolve 布局迁移：v1 -> v2"
                    if plan["layout_migration_required"]
                    else f"# EvoZeus-CoEvolve Harness Refresh：{plan['current_version']} -> {plan['latest_version']}"
                ),
                "",
                f"- 日期：{(today or date.today()).isoformat()}",
                f"- Wrapper 版本：{plan['current_version'] or 'unknown'} -> {plan['latest_version'] or 'unknown'}",
                f"- 新事实源：`{TARGET_WRAPPER_MANIFEST}`",
                (
                    "- 目标：把 wrapper 产物归拢到 `.evozeus-wrapper/`。"
                    if plan["layout_migration_required"]
                    else "- 目标：修复 consolidated-v2 harness 并刷新 wrapper-managed contract。"
                ),
                "",
                "## 移动记录",
                "",
                *(
                    [f"- `{item['source']}` -> `{item['destination']}`" for item in plan["moves"]]
                    or ["- 无文件移动；刷新 consolidated-v2 managed files。"]
                ),
                "",
                "## 保留的宿主接点",
                "",
                *[f"- `{item}`" for item in plan["preserved_host_entrypoints"]],
                "",
                "## 验证",
                "",
                f"- `python3 {TARGET_PREFLIGHT_SCRIPT} structure`",
                f"- `python3 {TARGET_PREFLIGHT_SCRIPT} runtime`",
                "",
                "## 回滚",
                "",
                "- 回滚包含本次迁移的 Git commit。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    changed_files.append(plan["migration_record"])

    removed_caches = _remove_legacy_wrapper_caches(target)
    actions.extend(f"remove generated cache {path}" for path in removed_caches)
    removed_dirs = _remove_empty_legacy_dirs(target)
    actions.extend(f"remove empty directory {path}/" for path in removed_dirs)
    after = wrapper_manifest_status(target)
    if after["migration_required"] or after["legacy_manifest_detected"]:
        raise ValueError("migration left a legacy wrapper manifest behind")

    structure = run_command(
        [sys.executable, str(target / TARGET_PREFLIGHT_SCRIPT), "structure", "--target", str(target)]
    )
    if structure["returncode"] != 0:
        detail = (structure["stderr"] or structure["stdout"]).strip()
        raise ValueError(f"migration post-validation failed: {detail}")

    return {
        **plan,
        "writes": True,
        "migration_required": False,
        "can_apply": False,
        "actions": actions,
        "changed_files": list(dict.fromkeys(changed_files)),
        "removed_empty_dirs": removed_dirs,
        "removed_generated_caches": removed_caches,
        "manifest_source": after["manifest_source"],
        "validation": {
            "structure": "passed",
            "command": f"python3 {TARGET_PREFLIGHT_SCRIPT} structure --target {target}",
        },
    }


def migrate_target_infra_dir(
    target: Path,
    latest_version: str | None = None,
    remove_duplicate_legacy: bool = False,
) -> dict[str, Any]:
    return migrate_target_layout(target=target, latest_version=latest_version)


def plan_transform_action(harness_state: str, repo_exists: bool | None) -> str:
    if harness_state == "migration_required":
        return "migrate_layout"
    if harness_state == "complete":
        return "verify"
    if harness_state == "partial":
        return "repair"
    if harness_state != "missing":
        raise ValueError(f"unknown harness state: {harness_state}")
    if repo_exists is None:
        return "needs_repo_check"
    return "attach" if repo_exists else "create_repo_first"


def classify_install_action(install_path: Path, canonical_path: Path) -> dict[str, Any]:
    canonical_path = canonical_path.expanduser().resolve()
    kind = path_kind(install_path)
    resolved_path = None
    if install_path.exists() or install_path.is_symlink():
        try:
            resolved_path = str(install_path.resolve())
        except OSError:
            resolved_path = None

    if kind == "missing":
        action = "create_symlink"
        reason = "install path is missing"
    elif kind == "symlink" and resolved_path == str(canonical_path):
        action = "already_linked"
        reason = "install path already points to canonical repo"
    elif kind == "symlink":
        action = "relink_symlink"
        reason = "install symlink points somewhere else"
    elif kind == "directory":
        canonical_hash = file_sha256(canonical_path / "SKILL.md")
        install_hash = file_sha256(install_path / "SKILL.md")
        if canonical_hash and install_hash and canonical_hash == install_hash:
            action = "archive_then_symlink"
            reason = "real directory install has identical SKILL.md"
        else:
            action = "needs_user_confirmation"
            reason = "real directory install differs from canonical repo"
    else:
        action = "needs_user_confirmation"
        reason = "install path is not a directory or symlink"

    return {
        "path": str(install_path),
        "kind": kind,
        "resolved_path": resolved_path,
        "canonical_path": str(canonical_path),
        "action": action,
        "reason": reason,
    }


def plan_reinstall(skill_name: str, canonical_path: Path, home: Path, targets: list[str]) -> dict[str, Any]:
    home = home.expanduser().resolve()
    canonical_path = canonical_path.expanduser().resolve()
    runtime_roots = {
        "codex": home / ".codex" / "skills",
        "agents": home / ".agents" / "skills",
    }
    selected = ["codex", "agents"] if "all" in targets else targets
    actions = []
    seen_paths: set[str] = set()
    for target_name in selected:
        root = runtime_roots[target_name] if target_name in runtime_roots else Path(target_name).expanduser()
        install_path = root / skill_name if target_name in runtime_roots else root
        path_key = str(install_path.absolute())
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        actions.append(classify_install_action(install_path, canonical_path))
    return {
        "stage": "publish_reinstall",
        "skill_name": skill_name,
        "canonical_path": str(canonical_path),
        "status": "planned",
        "writes": False,
        "actions": actions,
        "runtime_skill_installation": {
            "status": "planned",
            "target_count": len(actions),
        },
        "runtime_hook_installation": read_global_hook_status(home),
    }


def apply_reinstall(
    skill_name: str,
    canonical_path: Path,
    home: Path,
    targets: list[str],
    *,
    approve_archive: bool = False,
    archive_root: Path | None = None,
    archive_id: str | None = None,
) -> dict[str, Any]:
    if not skill_name or Path(skill_name).name != skill_name or skill_name in {".", ".."}:
        raise ValueError("skill name must be a single path component")

    canonical_path = canonical_path.expanduser()
    if not canonical_path.is_dir():
        raise ValueError("canonical path must be an existing directory")
    if not (canonical_path / "SKILL.md").is_file():
        raise ValueError("canonical path must contain SKILL.md")
    canonical_path = canonical_path.resolve()
    home = home.expanduser().resolve()
    archive_base = (
        archive_root.expanduser().resolve()
        if archive_root is not None
        else home / GLOBAL_EVOZEUS_HOME / "archives" / "runtime-installs"
    )
    archive_id = archive_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", archive_id):
        raise ValueError("archive id may contain only letters, digits, dot, underscore, and hyphen")

    report = plan_reinstall(skill_name, canonical_path, home, targets)
    report.update(
        {
            "archive_root": str(archive_base),
            "archive_approved": approve_archive,
            "approval_required": False,
        }
    )
    blocked_reasons: list[str] = []

    for action in report["actions"]:
        install_path = Path(action["path"]).absolute()
        action_name = action["action"]
        if action["kind"] == "directory" and install_path.resolve() == canonical_path:
            blocked_reasons.append(f"canonical repo cannot also be a runtime install directory: {install_path}")
            action["result"] = "blocked"
            continue
        if action_name == "needs_user_confirmation" and action["kind"] != "directory":
            blocked_reasons.append(f"unsupported runtime install type requires manual handling: {install_path}")
            action["result"] = "blocked"
            continue
        if action_name in {"archive_then_symlink", "needs_user_confirmation"}:
            report["approval_required"] = True
            if not approve_archive:
                blocked_reasons.append(f"archive approval required before replacing real directory: {install_path}")
                action["result"] = "blocked"
                continue
            path_digest = hashlib.sha256(str(install_path).encode("utf-8")).hexdigest()[:12]
            source_label = install_path.parent.parent.name.lstrip(".") or "runtime"
            archive_path = archive_base / skill_name / archive_id / f"{source_label}-{path_digest}"
            if archive_path.exists() or archive_path.is_symlink():
                blocked_reasons.append(f"archive destination already exists: {archive_path}")
                action["result"] = "blocked"
                continue
            if archive_path.is_relative_to(install_path) or archive_path.is_relative_to(canonical_path):
                blocked_reasons.append(f"archive destination must be outside source and canonical directories: {archive_path}")
                action["result"] = "blocked"
                continue
            action["archive_path"] = str(archive_path)
        action.setdefault("result", "pending")

    if blocked_reasons:
        for action in report["actions"]:
            if action["result"] == "pending":
                action["result"] = "not_applied"
        report["runtime_skill_installation"]["status"] = "blocked"
        report.update({"status": "blocked", "writes": False, "errors": blocked_reasons})
        return report

    undo_log: list[dict[str, Any]] = []
    try:
        for action in report["actions"]:
            install_path = Path(action["path"])
            action_name = action["action"]
            if action_name == "already_linked":
                action["result"] = "already_linked"
                continue
            install_path.parent.mkdir(parents=True, exist_ok=True)
            if action_name == "create_symlink":
                install_path.symlink_to(canonical_path)
                undo_log.append({"operation": "remove_created", "path": install_path})
                action["result"] = "created_symlink"
                continue
            if action_name == "relink_symlink":
                old_target = install_path.readlink()
                install_path.unlink()
                try:
                    install_path.symlink_to(canonical_path)
                except Exception:
                    install_path.symlink_to(old_target)
                    raise
                undo_log.append({"operation": "restore_symlink", "path": install_path, "target": old_target})
                action["result"] = "relinked_symlink"
                continue
            if action_name in {"archive_then_symlink", "needs_user_confirmation"}:
                archive_path = Path(action["archive_path"])
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(install_path), str(archive_path))
                try:
                    install_path.symlink_to(canonical_path)
                except Exception:
                    shutil.move(str(archive_path), str(install_path))
                    raise
                undo_log.append(
                    {"operation": "restore_archive", "path": install_path, "archive_path": archive_path}
                )
                action["result"] = "archived_and_linked"
                continue
            raise RuntimeError(f"unsupported reinstall action: {action_name}")
    except Exception:
        for undo in reversed(undo_log):
            path = undo["path"]
            if path.is_symlink():
                path.unlink()
            if undo["operation"] == "restore_symlink":
                path.symlink_to(undo["target"])
            elif undo["operation"] == "restore_archive":
                shutil.move(str(undo["archive_path"]), str(path))
        raise

    report["runtime_skill_installation"]["status"] = "applied"
    report.update({"status": "applied", "writes": any(item["result"] != "already_linked" for item in report["actions"]), "errors": []})
    return report


def version_key(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        raise ValueError(f"version tag must use vMAJOR.MINOR.PATCH format: {tag}")
    return tuple(int(part) for part in match.groups())


def classify_pr_permission(write: bool, fork: bool) -> str:
    if write:
        return "direct_pr"
    if fork:
        return "fork_pr"
    return "local_patch"


def classify_wrapper_upgrade(current: str, latest: str, managed_dirty: bool) -> str:
    current_key = version_key(current)
    latest_key = version_key(latest)
    if latest_key == current_key:
        return "up_to_date"
    if latest_key < current_key:
        return "local_ahead"
    if latest_key[0] > current_key[0]:
        return "requires_confirmation"
    if managed_dirty:
        return "needs_merge_review"
    return "auto_pr"


def wrapper_migration_doc_path(current: str | None, latest: str | None, today: date | None = None) -> str | None:
    if not latest:
        return None
    day = today or date.today()
    current_label = current or "unknown"
    return f"{TARGET_EVOINFRA_DIR}/docs/migrations/{day.isoformat()}-{current_label}-to-{latest}.md"


def plan_harness_upgrade(
    target: Path,
    latest_version: str | None = None,
    managed_dirty: bool = False,
    today: date | None = None,
    instruction_surface: str | None = None,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    manifest = load_wrapper_manifest(target, allow_legacy=True)
    manifest_status = wrapper_manifest_status(target)
    architecture = detect_target_architecture(target)
    manifest_surface = manifest.get("instruction_surface") if manifest else None
    instruction_surface = instruction_surface or manifest_surface or architecture["root_entry"] or "SKILL.md"
    root_harness_entry = f"{instruction_surface} canonical Harness Skill activation block"
    current = manifest.get("wrapper_version") if manifest else None
    latest_resolution = resolve_latest_wrapper_release(latest_version)
    latest = latest_resolution["version"]

    if not current:
        status = "missing_manifest"
    elif not latest:
        status = "latest_unknown"
    else:
        status = classify_wrapper_upgrade(current, latest, managed_dirty)

    migration_doc = wrapper_migration_doc_path(current, latest, today)
    needs_upgrade = status in {"auto_pr", "needs_merge_review", "requires_confirmation"}
    needs_repair = status in {"missing_manifest", "latest_unknown"}
    layout_migration = plan_target_layout_migration(target, latest, today)

    planned_files: list[str] = []
    if needs_upgrade or needs_repair or layout_migration["migration_required"]:
        planned_files.extend(
            [
                root_harness_entry,
                TARGET_HARNESS_SKILL,
                TARGET_WRAPPER_MANIFEST,
                WRAPPER_MIGRATION_README,
            ]
        )
        if migration_doc:
            planned_files.append(migration_doc)
        planned_files.extend(WRAPPER_MANAGED_FILES)

    deduped_planned_files = []
    for path in planned_files:
        if path not in deduped_planned_files:
            deduped_planned_files.append(path)

    if status == "missing_manifest":
        recommended_action = "repair_or_adopt_before_upgrade"
    elif status == "latest_unknown":
        recommended_action = "provide_latest_wrapper_version"
    elif layout_migration["migration_required"]:
        recommended_action = "migrate_layout"
    elif status == "up_to_date":
        recommended_action = "none"
    elif status == "local_ahead":
        recommended_action = "do_not_downgrade"
    elif status == "needs_merge_review":
        recommended_action = "review_managed_file_diffs_before_upgrade"
    elif status == "requires_confirmation":
        recommended_action = "confirm_major_upgrade_and_migration_plan"
    else:
        recommended_action = "create_harness_upgrade_pr"

    canonical_repo = manifest.get("canonical_repo") if manifest else None
    integration = architecture["integration"]
    validation = [
        f"python3 {TARGET_PREFLIGHT_SCRIPT} structure",
    ]
    if canonical_repo:
        validation.append(f"python3 {TARGET_PREFLIGHT_SCRIPT} doctor --repo {canonical_repo}")
    if latest:
        validation.append(
            "python3 scripts/evozeus_wrapper.py harness upgrade-check "
            f"--target {target} --latest-version {latest} --json"
        )

    return {
        "stage": "harness_upgrade",
        "target": str(target),
        "writes": False,
        "target_infra_dir": TARGET_EVOINFRA_DIR,
        "legacy_infra_dir": LEGACY_TARGET_EVOINFRA_DIR,
        "oldest_infra_dir": OLDEST_TARGET_EVOINFRA_DIR,
        "manifest_path": TARGET_WRAPPER_MANIFEST,
        "legacy_manifest_detected": manifest_status["legacy_manifest_detected"],
        "migration_required": (
            manifest_status["migration_required"] or layout_migration["migration_required"]
        ),
        "manifest_source": manifest_status["manifest_source"],
        "current_version": current,
        "latest_version": latest,
        "latest_source": latest_resolution["source"],
        "latest_release_url": latest_resolution["url"],
        "latest_lookup_error": latest_resolution["error"],
        "checked_at": latest_resolution["checked_at"],
        "managed_dirty": managed_dirty,
        "upgrade_status": status,
        "recommended_action": recommended_action,
        "requires_confirmation": status in {"missing_manifest", "latest_unknown", "needs_merge_review", "requires_confirmation"},
        "status_check_first": True,
        "append_only": False,
        "evolution_surface_policy": (
            f"keep one compact canonical Harness Skill activation block in {instruction_surface}; "
            f"store the full wrapper contract in {TARGET_HARNESS_SKILL}; preserve target business bytes"
        ),
        "integration": integration,
        "integration_policy": (
            "repo_maintenance_hook covers only the canonical repository; global_session_dispatcher checks all "
            "registered wrapped Skills at SessionStart; skill_entry_preflight is prompt-compliance fallback; "
            "none is a native per-Skill invocation hook without a SkillInvoke event; Issue-to-PR must consume "
            "the pinned EvoZeus Core contributor branch contract and live permission evidence before target writes"
        ),
        "skill_md_policy": (
            "single Skill targets use SKILL.md; AGENTS.md-root targets use AGENTS.md; hook-controlled bundles use the hook-loaded control Skill"
        ),
        "migration": {
            "from_wrapper_version": current,
            "to_wrapper_version": latest,
            "doc_path": migration_doc,
            "log_dir": f"{TARGET_EVOINFRA_DIR}/docs/migrations",
            "records_wrapper_version_in": TARGET_WRAPPER_MANIFEST,
        },
        "layout_migration": layout_migration,
        "planned_files": deduped_planned_files,
        "migration_steps": [
            f"Read {TARGET_WRAPPER_MANIFEST} and confirm canonical_repo before touching runtime installs.",
            f"If {LEGACY_TARGET_WRAPPER_MANIFEST} or {OLDEST_TARGET_WRAPPER_MANIFEST} exists, run the one-time layout migration.",
            "Diff wrapper-managed files; if they contain local edits, stop for merge review.",
            "Copy or merge wrapper-managed files only.",
            f"Write {TARGET_HARNESS_SKILL} from the wrapper-managed canonical template.",
            "Refresh the contributor branch consumer, pinned Core contract/planner snapshot, provenance, and PR metadata surface.",
            f"Replace proven wrapper-owned legacy sections in {instruction_surface} with one compact activation block.",
            f"Write a migration record under {TARGET_EVOINFRA_DIR}/docs/migrations/ with from/to wrapper versions, validation, and rollback.",
            f"Update {TARGET_WRAPPER_MANIFEST} wrapper_version after validation passes.",
        ],
        "validation": validation,
    }
