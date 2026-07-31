#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

try:
    from .evozeus_notice import load_notice_policy, render_notice
except ImportError:
    from evozeus_notice import load_notice_policy, render_notice

try:
    from .evozeus_branch_consumer import (
        ConsumerError as ContributorBranchError,
        PROFILE as CONTRIBUTOR_BRANCH_PROFILE,
        compute_resume_key,
        verify_managed_snapshot,
    )
except ImportError:
    from evozeus_branch_consumer import (
        ConsumerError as ContributorBranchError,
        PROFILE as CONTRIBUTOR_BRANCH_PROFILE,
        compute_resume_key,
        verify_managed_snapshot,
    )


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
CODEX_HOOKS_CONFIG = ".codex/hooks.json"
CODEX_START_HOOK_SCRIPT = f"{TARGET_EVOINFRA_DIR}/hooks/evozeus_wrapper_start_check.py"
TARGET_DASHBOARD_INDEX = f"{TARGET_EVOINFRA_DIR}/docs/index.md"
TARGET_DASHBOARD_CONFIG = f"{TARGET_EVOINFRA_DIR}/docs/_config.yml"
TARGET_DESIGN_TEMPLATE = f"{TARGET_EVOINFRA_DIR}/docs/design-doc-template.md"
TARGET_DESIGNS_DIR = f"{TARGET_EVOINFRA_DIR}/docs/designs"
TARGET_DESIGNS_README = f"{TARGET_DESIGNS_DIR}/README.md"
TARGET_MIGRATIONS_DIR = f"{TARGET_EVOINFRA_DIR}/docs/migrations"
TARGET_MIGRATIONS_README = f"{TARGET_MIGRATIONS_DIR}/README.md"
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

REQUIRED_FILES = [
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
MAINTAINER_REQUIRED_FILES = REQUIRED_FILES

ISSUE_TERMS = [
    ["不满意", "unsatisfactory", "bad result"],
    ["期望", "expected"],
    ["复现", "reproduction", "scenario", "场景"],
    ["证据边界", "evidence boundary"],
    ["影响", "impact"],
]

DESIGN_TERMS = [
    ["related issue", "修复", "issue"],
    ["optimization goal", "优化目标"],
    ["direction", "优化方向"],
    ["implementation plan", "怎么优化", "实现"],
    ["verification plan", "验证"],
    ["release plan", "release", "发布"],
]

HARNESS_SKILL_TERMS = [
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
    "evozeus/harness-vX-to-vY",
    "隔离 worktree",
]

PLACEHOLDER_PATTERNS = [
    r"\{\{[A-Z_]+\}\}",
    r"<short title>",
    r"<path>",
    r"<design-doc>",
    r"\bTBD\b",
    r"待填写",
]

NOTICE_REQUIRED_STATES = {
    "skill": {"active"},
    "lesson": {"pending", "recorded"},
    "evolution": {"authorized", "running", "verified"},
    "maintenance": {"pending", "running", "completed"},
    "advisory": {"continue"},
    "blocked": {"blocked"},
    "uat": {"replaced", "passed", "failed"},
    "release": {"published"},
}

VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RESUME_KEY_RE = re.compile(r"^branch_v1_[0-9a-f]{24}$")
COEVOLVE_SOURCE_REPO = "MetaInFLow/EvoZeus-CoEvolve"
TRUSTED_CONTROL_SOURCES = {
    CODEX_HOOKS_CONFIG: "templates/target/.codex/hooks.json",
    TARGET_WRAPPER_GUIDE: "templates/target/WRAPPER.md",
    TARGET_DASHBOARD_INDEX: "templates/target/docs/index.md",
    TARGET_DASHBOARD_CONFIG: "templates/target/docs/_config.yml",
    TARGET_DESIGN_TEMPLATE: "templates/target/docs/design-doc-template.md",
    TARGET_DESIGNS_README: "templates/target/docs/designs/README.md",
    TARGET_MIGRATIONS_README: "templates/target/docs/wrapper-migrations/README.md",
    ".github/ISSUE_TEMPLATE/config.yml": "templates/target/.github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/skill-feedback.yml": "templates/target/.github/ISSUE_TEMPLATE/skill-feedback.yml",
    TARGET_PREFLIGHT_SCRIPT: "scripts/evozeus_wrapper_preflight.py",
    TARGET_NOTICE_SCRIPT: "scripts/evozeus_notice.py",
    TARGET_BRANCH_CONSUMER_SCRIPT: "scripts/evozeus_branch_consumer.py",
    TARGET_BRANCH_CONTRACT: "templates/target/contracts/v1/contributor-branch-contract.json",
    TARGET_BRANCH_PROVENANCE: "templates/target/contracts/v1/contributor-branch-provenance.json",
    TARGET_BRANCH_PLANNER: "templates/target/scripts/evozeus-branch-preflight.mjs",
    CODEX_START_HOOK_SCRIPT: "templates/target/.codex/hooks/evozeus_wrapper_start_check.py",
    ".github/workflows/evozeus-wrapper-preflight.yml": "templates/target/.github/workflows/evozeus-wrapper-preflight.yml",
    ".github/pull_request_template.md": "templates/target/.github/pull_request_template.md",
    TARGET_ONBOARDING_GUIDE: "templates/target/docs/onboarding.md",
    TARGET_FEEDBACK_POLICY: "templates/target/.evozeus_evoinfra/feedback-policy.json",
    TARGET_AUDIT_RULE: "templates/target/.evozeus_evoinfra/audit-rule.md",
    TARGET_NOTICE_POLICY: "templates/target/.evozeus_evoinfra/notice-policy.json",
    TARGET_HARNESS_SKILL: "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md",
}
EXPECTED_CONTRIBUTOR_BRANCH = {
    "profile": "coevolve_target_skillware_consumer",
    "consumer_path": TARGET_BRANCH_CONSUMER_SCRIPT,
    "contract_path": TARGET_BRANCH_CONTRACT,
    "provenance_path": TARGET_BRANCH_PROVENANCE,
    "planner_path": TARGET_BRANCH_PLANNER,
    "permission_authority": "core_planner_live_github_evidence",
    "runtime_network_fetch": False,
    "ledger_root": "~/.evozeus/coevolve/branch-plans/OWNER/REPO",
}
RUNTIME_REFERENCE_RE = re.compile(
    r"(?P<path>(?:references|scripts|assets|templates|agents)/[A-Za-z0-9_.@()/+=,~-]+)",
)
PLUGIN_MANIFEST_CANDIDATES = [
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    ".kimi-plugin/plugin.json",
    ".opencode/INSTALL.md",
    "gemini-extension.json",
    "package.json",
]
WRAPPER_RUNTIME_SECTION_HEADINGS = {
    "## EvoZeus-CoEvolve 状态检查",
    "## EvoZeus-wrapper 状态检查",
    "## 自进化方法",
    "## EvoZeus-CoEvolve",
    "## EvoZeus-wrapper",
}
STATUS_PRELUDE_HEADINGS = (
    "## EvoZeus-CoEvolve 状态检查",
    "## EvoZeus-wrapper 状态检查",
)
BLOCKING_STATUS_PHRASES = [
    "Continue to the target Skill's main flow only after all three are OK.",
    "全部 OK 后",
    "只有检查结果为 OK",
    "才继续进入目标 Skill 原本主链路",
    "才继续进入下方原 Skill 流程",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"OK: {message}")


def warn(message: str) -> None:
    print(f"WARN: {message}")


def read_text(path: Path) -> str:
    if not path.exists():
        fail(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def has_any(text: str, terms: list[str]) -> bool:
    low = normalize(text)
    return any(term.lower() in low for term in terms)


def has_real_content(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    return not any(re.search(pattern, stripped, re.IGNORECASE) for pattern in PLACEHOLDER_PATTERNS)


def version_key(tag: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(tag)
    if not match:
        fail(f"release tag must use vMAJOR.MINOR.PATCH format: {tag}")
    return tuple(int(part) for part in match.groups())


def run_command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        fail(f"missing required dependency: {command}")


def path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "missing"


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


def wrapper_manifest_path(target: Path) -> Path:
    return target / TARGET_EVOINFRA_DIR / "wrapper.json"


def legacy_wrapper_manifest_path(target: Path) -> Path:
    return target / LEGACY_TARGET_EVOINFRA_DIR / "wrapper.json"


def oldest_wrapper_manifest_path(target: Path) -> Path:
    return target / OLDEST_TARGET_EVOINFRA_DIR / "wrapper.json"


def read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid wrapper manifest JSON: {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"wrapper manifest must be a JSON object: {path}")
    return data


def load_wrapper_manifest(target: Path) -> dict | None:
    current_path = wrapper_manifest_path(target)
    legacy_paths = [legacy_wrapper_manifest_path(target), oldest_wrapper_manifest_path(target)]
    existing_legacy = [path for path in legacy_paths if path.exists()]
    if existing_legacy:
        fail(
            "legacy wrapper layout requires an upgrade migration before preflight: "
            + ", ".join(str(path.relative_to(target)) for path in existing_legacy)
        )
    if not current_path.exists():
        return None
    return read_json_object(current_path)


def project_pointer_path(repo: str) -> Path:
    owner, name = repo.split("/", 1)
    return Path.home() / GLOBAL_EVOZEUS_HOME / GLOBAL_EVOZEUS_PROJECTS_DIR / owner / name


def detected_hook_files(target: Path) -> list[str]:
    hooks = [
        path
        for path in [
            CODEX_HOOKS_CONFIG,
            ".codex/config.toml",
            CODEX_START_HOOK_SCRIPT,
        ]
        if (target / path).is_file()
    ]
    hooks_dir = target / "hooks"
    if hooks_dir.is_dir():
        hooks.extend(
            str(path.relative_to(target))
            for path in sorted(hooks_dir.iterdir())
            if path.is_file()
        )
    return list(dict.fromkeys(hooks))


def detected_plugin_manifests(target: Path) -> list[str]:
    return [path for path in PLUGIN_MANIFEST_CANDIDATES if (target / path).is_file()]


def _manifest_relative_file(target: Path, raw: object, label: str) -> Path:
    repair_hint = (
        "; run an approved Harness repair or migrate-layout"
        if label == "canonical Harness Skill"
        else ""
    )
    if not isinstance(raw, str) or not raw:
        fail(f"{label} must be a non-empty relative path{repair_hint}")
    if re.match(r"^[A-Za-z]:[\\/]", raw) or "\\" in raw:
        fail(f"{label} must stay inside target and use POSIX relative syntax{repair_hint}")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"{label} must stay inside target{repair_hint}")
    target = target.expanduser().resolve()
    candidate = target / relative
    cursor = candidate
    while cursor != target:
        if cursor.is_symlink():
            fail(f"{label} cannot contain a symlink: {cursor.relative_to(target)}{repair_hint}")
        cursor = cursor.parent
    if not candidate.exists():
        fail(f"missing {label}: {raw}{repair_hint}")
    if not candidate.is_file():
        fail(f"{label} must resolve to a regular file: {raw}{repair_hint}")
    try:
        candidate.resolve(strict=True).relative_to(target.resolve())
    except (OSError, ValueError):
        fail(f"{label} resolves outside target: {raw}{repair_hint}")
    return candidate


def check_harness_skill_contract(
    target: Path,
    manifest: dict,
    *,
    allow_legacy: bool,
) -> str:
    fields = ("harness_skill_path", "harness_skill_version", "harness_skill_managed")
    present = [field for field in fields if field in manifest]
    if not present:
        if allow_legacy:
            surface_rel = manifest.get("instruction_surface")
            if not isinstance(surface_rel, str) or not surface_rel:
                surface_rel = "SKILL.md" if (target / "SKILL.md").is_file() else "AGENTS.md"
            surface = _manifest_relative_file(target, surface_rel, "legacy instruction_surface")
            content = content_after_frontmatter(read_text(surface)).lstrip()
            lines = content.splitlines()
            has_legacy_prelude = content.startswith(STATUS_PRELUDE_HEADINGS) or bool(
                lines
                and lines[0].startswith("# ")
                and "\n".join(lines[1:]).lstrip().startswith(STATUS_PRELUDE_HEADINGS)
            )
            if not has_legacy_prelude:
                fail(
                    "legacy wrapper manifest has no compatible status prelude; "
                    "run an approved Harness repair or migrate-layout"
                )
            warn(
                "legacy wrapper manifest has no canonical Harness Skill identity; "
                "run migrate-layout before the next Harness write"
            )
            return "legacy_compatible"
        fail(f"{TARGET_WRAPPER_MANIFEST} missing canonical Harness Skill identity")
    if len(present) != len(fields):
        fail(f"{TARGET_WRAPPER_MANIFEST} has an incomplete canonical Harness Skill identity")

    path_value = manifest.get("harness_skill_path")
    if path_value != TARGET_HARNESS_SKILL:
        fail(f"harness_skill_path must use canonical path {TARGET_HARNESS_SKILL}")
    if manifest.get("harness_skill_version") != HARNESS_SKILL_VERSION:
        fail(
            "incompatible canonical Harness Skill version: "
            f"{manifest.get('harness_skill_version')}; expected {HARNESS_SKILL_VERSION}; "
            "run an approved compatible Harness migration"
        )
    if manifest.get("harness_skill_managed") is not True:
        fail("canonical Harness Skill must remain wrapper managed")
    managed_files = manifest.get("managed_files")
    if not isinstance(managed_files, list) or TARGET_HARNESS_SKILL not in managed_files:
        fail("canonical Harness Skill must be listed in managed_files")

    harness_path = _manifest_relative_file(target, path_value, "canonical Harness Skill")
    text = read_text(harness_path)
    frontmatter = re.match(r"\A---\r?\n(?P<body>.*?)\r?\n(?:---|\.\.\.)\r?\n", text, re.DOTALL)
    if not frontmatter:
        fail(
            "canonical Harness Skill frontmatter is missing or malformed; "
            "run an approved Harness repair or migrate-layout"
        )
    body = frontmatter.group("body")
    if not re.search(r"(?m)^name:[ \t]*[\"']?using-evozeus-harness[\"']?[ \t]*\r?$", body):
        fail(
            "canonical Harness Skill frontmatter name must be using-evozeus-harness; "
            "run an approved Harness repair"
        )
    metadata = re.search(
        r"(?m)^metadata:[ \t]*\r?\n(?P<values>(?:^[ \t]+[^\r\n]*(?:\r?\n|$))*)",
        body,
    )
    if not metadata or not re.search(
        rf"(?m)^[ \t]+version:[ \t]*[\"']?{re.escape(HARNESS_SKILL_VERSION)}[\"']?[ \t]*\r?$",
        metadata.group("values"),
    ):
        fail(
            f"canonical Harness Skill frontmatter version must be {HARNESS_SKILL_VERSION}; "
            "run an approved Harness repair"
        )
    missing_terms = [term for term in HARNESS_SKILL_TERMS if term not in text]
    if missing_terms:
        fail(
            "canonical Harness Skill missing required concepts: "
            + ", ".join(missing_terms)
            + "; run an approved Harness repair"
        )
    ok(f"canonical Harness Skill contract is valid: {TARGET_HARNESS_SKILL}@{HARNESS_SKILL_VERSION}")
    return "canonical"


def _canonical_harness_entry_block() -> str:
    return "\n".join(
        [
            HARNESS_ENTRY_BEGIN,
            "**CRITICAL — 进入业务主链路前 MUST 使用 Read 工具读取并执行",
            f"[{TARGET_HARNESS_SKILL}]({TARGET_HARNESS_SKILL})。**",
            HARNESS_ENTRY_END,
        ]
    )


def check_harness_entry_contract(target: Path, manifest: dict) -> None:
    if manifest.get("harness_skill_path") != TARGET_HARNESS_SKILL:
        fail(f"Harness entry manifest does not match canonical path {TARGET_HARNESS_SKILL}")
    surface_rel = manifest.get("instruction_surface")
    surface = _manifest_relative_file(target, surface_rel, "instruction_surface")
    text = read_text(surface).replace("\r\n", "\n")
    if text.count(HARNESS_ENTRY_BEGIN) != 1 or text.count(HARNESS_ENTRY_END) != 1:
        fail("instruction surface must contain exactly one canonical Harness Skill activation block")
    start = text.index(HARNESS_ENTRY_BEGIN)
    end = text.index(HARNESS_ENTRY_END, start) + len(HARNESS_ENTRY_END)
    block = text[start:end]
    if block != _canonical_harness_entry_block():
        fail("instruction surface Harness Skill link does not match the canonical manifest path")
    if len(block.splitlines()) > 8:
        fail("instruction surface Harness Skill activation block exceeds 8 lines")

    content = content_after_frontmatter(text).lstrip()
    lines = content.splitlines()
    at_entry = content.startswith(HARNESS_ENTRY_BEGIN)
    after_title = bool(
        lines
        and lines[0].startswith("# ")
        and "\n".join(lines[1:]).lstrip().startswith(HARNESS_ENTRY_BEGIN)
    )
    if not (at_entry or after_title):
        fail("canonical Harness Skill activation block must precede the business main flow")
    ok(f"instruction surface activates canonical Harness Skill: {surface_rel}")


def check_integration_contract(target: Path, manifest: dict | None) -> None:
    integration = (manifest or {}).get("integration") or {}
    registration = ((manifest or {}).get("hook_registration") or {}).get("codex") or {}
    mode = integration.get("mode")
    capabilities = integration.get("capabilities") or {}
    repo_hook = capabilities.get("repo_maintenance_hook") or {}
    global_dispatcher = capabilities.get("global_session_dispatcher") or {}
    skill_entry = capabilities.get("skill_entry_preflight") or {}
    invocation_hook = capabilities.get("skill_invocation_hook") or {}

    if repo_hook.get("covers_skill_invocation"):
        fail("repo_maintenance_hook cannot claim Skill-invocation coverage")
    if global_dispatcher.get("covers_skill_invocation"):
        fail("global_session_dispatcher cannot claim per-Skill invocation coverage")
    if global_dispatcher.get("installed") or global_dispatcher.get("native_enforced"):
        fail("portable manifest cannot persist user-level global dispatcher installation state")
    if global_dispatcher.get("trust_status") not in {None, "not_installed"}:
        fail("portable manifest cannot persist user-level global dispatcher trust state")
    if skill_entry.get("native_enforced"):
        fail("skill_entry_preflight is prompt-compliance based, not native enforcement")
    if skill_entry.get("installed"):
        surface_rel = (manifest or {}).get("instruction_surface") or integration.get("root_entry")
        if not isinstance(surface_rel, str) or not surface_rel:
            fail("skill_entry_preflight installation requires an instruction_surface")
        relative_surface = Path(surface_rel)
        if relative_surface.is_absolute() or ".." in relative_surface.parts:
            fail("skill_entry_preflight instruction_surface must stay inside target")
        surface = target / relative_surface
        cursor = surface
        while cursor != target:
            if cursor.is_symlink():
                fail("skill_entry_preflight instruction_surface cannot use symlink components")
            cursor = cursor.parent
        try:
            surface.resolve(strict=True).relative_to(target.resolve())
        except (OSError, ValueError):
            fail("skill_entry_preflight instruction_surface is missing or outside target")
        if not surface.is_file():
            fail("skill_entry_preflight instruction_surface is missing")
        if (manifest or {}).get("harness_skill_path") is not None:
            check_harness_entry_contract(target, manifest or {})
        else:
            content = content_after_frontmatter(surface.read_text(encoding="utf-8")).lstrip()
            lines = content.splitlines()
            if content.startswith(STATUS_PRELUDE_HEADINGS):
                has_status_prelude = True
            else:
                has_status_prelude = bool(
                    lines
                    and lines[0].startswith("# ")
                    and "\n".join(lines[1:]).lstrip().startswith(STATUS_PRELUDE_HEADINGS)
                )
            if not has_status_prelude:
                fail("legacy skill_entry_preflight installation requires the status prelude")
    if registration.get("capability") == "repo_maintenance_hook":
        if registration.get("scope") != "canonical_repository":
            fail("repo_maintenance_hook registration scope must be canonical_repository")
        if registration.get("covers_skill_invocation"):
            fail("repo_maintenance_hook registration cannot claim Skill-invocation coverage")

    if not mode:
        warn("wrapper manifest has no integration.mode; treating runtime checks as prompt/manual fallback")
        return

    if mode == "native_host_hook":
        if not integration.get("native_skill_invocation_hook_installed"):
            fail("native Skill-invocation coverage requires explicit invocation-hook evidence")
        if not invocation_hook.get("supported") or not invocation_hook.get("installed"):
            fail("native Skill-invocation coverage requires a supported installed SkillInvoke hook")
        ok("integration contract has native Skill-invocation hook evidence")
        return

    if mode in {"bootstrap_skill", "prompt_runtime_check", "manual_only"}:
        if integration.get("native_skill_invocation_hook_installed"):
            fail(f"integration.mode={mode} conflicts with native Skill-invocation hook installation")
        if integration.get("native_host_hook_installed"):
            fail("deprecated native_host_hook_installed cannot overstate Skill-invocation coverage")
        ok(f"integration contract declares non-native mode: {mode}")
        return

    fail(f"unknown integration.mode: {mode}")


def check_onboarding_contract(manifest: dict | None) -> None:
    def nonempty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    onboarding = (manifest or {}).get("onboarding")
    if not isinstance(onboarding, dict):
        fail("wrapper manifest must contain an onboarding contract")

    installation = onboarding.get("installation")
    if not isinstance(installation, dict):
        fail("onboarding.installation must be an object")
    if installation.get("mode") != "canonical_repo_symlink":
        fail("onboarding.installation.mode must be canonical_repo_symlink")
    if not nonempty_string(installation.get("command")) or not nonempty_string(
        installation.get("verification")
    ):
        fail("onboarding.installation must provide command and verification")

    invocation = onboarding.get("invocation")
    if not isinstance(invocation, dict):
        fail("onboarding.invocation must be an object")
    if invocation.get("mode") != "host_skill_discovery" or invocation.get("owner") != "target_skill":
        fail("onboarding.invocation must use host_skill_discovery owned by target_skill")
    invocation_verification = invocation.get("verification")
    if not nonempty_string(invocation.get("instruction")) or not (
        isinstance(invocation_verification, str)
        and "consumer-project smoke test" in invocation_verification
    ):
        fail("onboarding.invocation must provide instructions and a consumer-project smoke test")

    initialization = onboarding.get("initialization")
    if not isinstance(initialization, dict):
        fail("onboarding.initialization must be an object")
    if initialization.get("owner") != "target_skill":
        fail("onboarding.initialization.owner must be target_skill")
    if not isinstance(initialization.get("required"), bool):
        fail("onboarding.initialization.required must be boolean")
    if initialization.get("required") and not (
        nonempty_string(initialization.get("command"))
        and nonempty_string(initialization.get("verification"))
    ):
        fail("required onboarding initialization must provide command and verification")

    children = onboarding.get("generated_child_skills")
    if not isinstance(children, dict):
        fail("onboarding.generated_child_skills must be an object")
    if children.get("hooks_inherited") is not False:
        fail("generated child Skills must explicitly declare hooks_inherited=false")
    if not isinstance(children.get("supported"), bool):
        fail("onboarding.generated_child_skills.supported must be boolean")
    if children.get("supported"):
        if children.get("repo_harness_inherited") is not True:
            fail("generated child Skills must inherit the parent repository Harness")
        if children.get("attachment") != "inherited_repo_harness":
            fail("generated child Skills must use inherited_repo_harness attachment")
        if children.get("separate_harness_boundary") != "independent_git_repository":
            fail("a generated child may own a separate Harness only as an independent Git repository")
        verification = children.get("verification") or ""
        required_terms = ["repository structure preflight", "consumer-project smoke test"]
        if not all(term in verification for term in required_terms):
            fail("generated child Skill verification must include parent repository preflight and consumer-project smoke test")
    ok("onboarding contract is complete")


def check_dashboard_contract(manifest: dict | None) -> None:
    dashboard = (manifest or {}).get("dashboard")
    if not isinstance(dashboard, dict):
        fail("wrapper manifest must contain a dashboard deployment contract")
    if dashboard.get("deployment_mode") != "opt_in_github_pages":
        fail("dashboard.deployment_mode must be opt_in_github_pages")
    if dashboard.get("enablement_variable") != "EVOZEUS_PAGES_ENABLED":
        fail("dashboard.enablement_variable must be EVOZEUS_PAGES_ENABLED")
    if dashboard.get("fallback") != "repository_only":
        fail("dashboard.fallback must be repository_only")
    ok("dashboard deployment contract is complete")


def check_contributor_branch_contract(
    target: Path,
    manifest: dict | None,
    *,
    snapshot_verified_from_official_release: bool = False,
) -> None:
    if (manifest or {}).get("contributor_branch") != EXPECTED_CONTRIBUTOR_BRANCH:
        fail("wrapper manifest contributor_branch contract is missing or incompatible")
    managed_files = (manifest or {}).get("managed_files")
    managed_branch_files = {
        TARGET_BRANCH_CONSUMER_SCRIPT,
        TARGET_BRANCH_CONTRACT,
        TARGET_BRANCH_PROVENANCE,
        TARGET_BRANCH_PLANNER,
    }
    if not isinstance(managed_files, list) or not managed_branch_files.issubset(managed_files):
        fail("wrapper manifest must keep every contributor branch asset wrapper-managed")
    for relative_path in managed_branch_files:
        _manifest_relative_file(target, relative_path, "contributor branch asset")
    if not snapshot_verified_from_official_release:
        try:
            verify_managed_snapshot(target / TARGET_EVOINFRA_DIR)
        except ContributorBranchError:
            fail("contributor branch snapshot verification failed")
    ok("contributor branch contract and offline snapshot are verified")


def skill_name_from_skill_md(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def target_canonical_path(target: Path) -> str:
    git_root_result = run_command(["git", "-C", str(target), "rev-parse", "--show-toplevel"])
    if git_root_result.returncode == 0 and git_root_result.stdout.strip():
        return str(Path(git_root_result.stdout.strip()).expanduser().resolve())
    return str(target.expanduser().resolve())


def git_origin_repo(path: Path) -> str | None:
    remote_result = run_command(["git", "-C", str(path), "remote", "get-url", "origin"])
    if remote_result.returncode != 0:
        return None
    return repo_from_remote(remote_result.stdout)


def repo_from_remote(remote_url: str) -> str | None:
    remote_url = remote_url.strip()
    match = re.match(r"^https://github\.com/([^/]+/[^/.]+)(?:\.git)?$", remote_url)
    if match:
        return match.group(1)
    match = re.match(r"^git@github\.com:([^/]+/[^/.]+)(?:\.git)?$", remote_url)
    if match:
        return match.group(1)
    return None


def gh_current_login() -> str | None:
    result = run_command(["gh", "api", "user", "--jq", ".login"])
    return result.stdout.strip() if result.returncode == 0 else None


def gh_orgs() -> list[str]:
    result = run_command(["gh", "api", "user/orgs", "--jq", ".[].login"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def gh_search_repos(query: str) -> list[str]:
    result = run_command(["gh", "search", "repos", query, "--json", "fullName", "--limit", "10"])
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [row["fullName"] for row in rows if row.get("fullName")]


def discover_repo_candidates(skill_name: str) -> list[str]:
    candidates: list[str] = []
    login = gh_current_login()
    if login:
        candidates.extend(gh_search_repos(f"{skill_name} user:{login}"))
    for org in gh_orgs():
        for repo in gh_search_repos(f"{skill_name} org:{org}"):
            if repo not in candidates:
                candidates.append(repo)
    if candidates:
        return candidates
    for repo in gh_search_repos(skill_name):
        if repo not in candidates:
            candidates.append(repo)
    return candidates


def is_repo_not_found(output: str) -> bool:
    markers = [
        "Could not resolve to a Repository",
        "Not Found",
        "HTTP 404",
        "repository not found",
    ]
    return any(marker.lower() in output.lower() for marker in markers)


def check_terms(text: str, term_groups: list[list[str]], label: str) -> None:
    missing = []
    for group in term_groups:
        if not has_any(text, group):
            missing.append("/".join(group))
    if missing:
        fail(f"{label} missing required concepts: {', '.join(missing)}")


def content_after_frontmatter(text: str) -> str:
    if not re.match(r"\A---[ \t]*\r?\n", text):
        return text
    match = re.match(r"\A---[ \t]*\r?\n.*?\r?\n(?:---|\.\.\.)[ \t]*\r?\n", text, re.DOTALL)
    if not match:
        return text
    return text[match.end() :]


def check_status_prelude(skill_text: str, label: str = "SKILL.md") -> None:
    content = content_after_frontmatter(skill_text).lstrip()
    if not content.startswith(STATUS_PRELUDE_HEADINGS):
        fail(f"{label} must start with the EvoZeus-CoEvolve status check after frontmatter")
    check_runtime_safe_status_prelude(skill_text, label)


def root_entry_path(target: Path) -> Path:
    manifest = load_wrapper_manifest(target)
    if manifest and manifest.get("instruction_surface"):
        raw_surface = manifest["instruction_surface"]
        relative = Path(raw_surface) if isinstance(raw_surface, str) else Path("")
        if (
            not isinstance(raw_surface, str)
            or re.match(r"^[A-Za-z]:[\\/]", raw_surface)
            or "\\" in raw_surface
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            fail(f"manifest instruction_surface must stay inside target: {raw_surface}")
        manifest_surface = target / relative
        cursor = manifest_surface
        while cursor != target:
            if cursor.is_symlink():
                fail(
                    "manifest instruction_surface cannot use symlink components: "
                    f"{raw_surface}"
                )
            cursor = cursor.parent
        try:
            manifest_surface.resolve(strict=True).relative_to(target.resolve())
        except (OSError, ValueError):
            fail(f"manifest instruction_surface is missing or outside target: {raw_surface}")
        if manifest_surface.is_file():
            return manifest_surface
        fail(f"manifest instruction_surface is missing: {manifest['instruction_surface']}")
    skill = target / "SKILL.md"
    if skill.exists():
        return skill
    agents = target / "AGENTS.md"
    if agents.exists():
        return agents
    fail(
        "target must contain a detectable evolution instruction surface: "
        f"root SKILL.md, root AGENTS.md, or {TARGET_WRAPPER_MANIFEST} instruction_surface selected by diagnosis"
    )


def check_agents_status_prelude(agents_text: str) -> None:
    content = content_after_frontmatter(agents_text).lstrip()
    if content.startswith(STATUS_PRELUDE_HEADINGS):
        check_runtime_safe_status_prelude(agents_text, "AGENTS.md")
        return
    lines = content.splitlines()
    if lines and lines[0].startswith("# "):
        rest = "\n".join(lines[1:]).lstrip()
        if rest.startswith(STATUS_PRELUDE_HEADINGS):
            check_runtime_safe_status_prelude(agents_text, "AGENTS.md")
            return
    fail("AGENTS.md must put the EvoZeus-CoEvolve status check before the main runtime instructions")


def normalize_relative_path(raw: str) -> str:
    cleaned = raw.strip().strip("`'\"").strip()
    cleaned = cleaned.rstrip(".,;:)")
    return cleaned.replace("\\", "/")


def referenced_runtime_files(text: str) -> list[str]:
    files: list[str] = []
    for match in RUNTIME_REFERENCE_RE.finditer(text):
        rel = normalize_relative_path(match.group("path"))
        if rel and rel not in files:
            files.append(rel)
    return files


def strip_wrapper_runtime_sections(text: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in WRAPPER_RUNTIME_SECTION_HEADINGS:
            skipping = True
            continue
        if skipping and re.match(r"^#{1,6}\s+", stripped) and stripped not in WRAPPER_RUNTIME_SECTION_HEADINGS:
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def add_tree_files(target: Path, dirname: str, files: list[str]) -> None:
    root = target / dirname
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(target))
            if rel not in files:
                files.append(rel)


def discover_runtime_bundle(target: Path) -> dict:
    manifest = load_wrapper_manifest(target)
    runtime_bundle = manifest.get("runtime_bundle") if manifest else None
    if isinstance(runtime_bundle, dict):
        instruction_surface = str(runtime_bundle.get("instruction_surface") or "SKILL.md")
        required = [
            normalize_relative_path(path)
            for path in runtime_bundle.get("required_files", [])
            if isinstance(path, str) and path.strip()
        ]
        if instruction_surface not in required:
            required.insert(0, instruction_surface)
        optional = [
            normalize_relative_path(path)
            for path in runtime_bundle.get("optional_files", [])
            if isinstance(path, str) and path.strip()
        ]
        return {
            "instruction_surface": instruction_surface,
            "required_files": list(dict.fromkeys(required)),
            "optional_files": list(dict.fromkeys(optional)),
            "external_tools": runtime_bundle.get("external_tools", []),
            "source": f"{TARGET_WRAPPER_MANIFEST} runtime_bundle",
        }

    entry = root_entry_path(target)
    instruction_surface = str(entry.relative_to(target))
    required_files = [instruction_surface]
    text = entry.read_text(encoding="utf-8", errors="ignore")
    business_text = strip_wrapper_runtime_sections(text)
    for rel in referenced_runtime_files(business_text):
        if rel not in required_files:
            required_files.append(rel)
    for dirname in ["references", "assets", "templates"]:
        if f"{dirname}/" in business_text:
            add_tree_files(target, dirname, required_files)
    if "scripts/" in business_text:
        add_tree_files(target, "scripts", required_files)
    for metadata in ["agents/openai.yaml"]:
        if (target / metadata).is_file() and metadata not in required_files:
            required_files.append(metadata)
    return {
        "instruction_surface": instruction_surface,
        "required_files": required_files,
        "optional_files": [],
        "external_tools": [],
        "source": "discovered_from_instruction_surface",
    }


def check_runtime_safe_status_prelude(text: str, label: str) -> None:
    if not any(heading.removeprefix("## ") in text for heading in STATUS_PRELUDE_HEADINGS):
        return
    if "runtime-only install" not in text:
        fail(f"{label} wrapper status prelude must include runtime-only install fallback language")
    lowered = text.lower()
    for phrase in BLOCKING_STATUS_PHRASES:
        if phrase.lower() in lowered:
            fail(
                f"{label} wrapper status prelude contains blocking runtime language; "
                f"remove it and keep the runtime-only install fallback: {phrase}"
            )


def check_runtime(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    bundle = discover_runtime_bundle(target)
    missing = [path for path in bundle["required_files"] if not (target / path).is_file()]
    if missing:
        fail("missing required runtime files:\n" + "\n".join(f"- {path}" for path in missing))
    entry = target / bundle["instruction_surface"]
    check_runtime_safe_status_prelude(read_text(entry), bundle["instruction_surface"])
    ok("runtime bundle is complete")


def check_notice_policy(target: Path) -> None:
    path = target / TARGET_NOTICE_POLICY
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid notice policy {TARGET_NOTICE_POLICY}: {exc}")
    if not isinstance(policy, dict) or policy.get("schema_version") != "v1":
        fail(f"{TARGET_NOTICE_POLICY} schema_version must be v1")
    if policy.get("tag_style") != "markdown_code":
        fail(f"{TARGET_NOTICE_POLICY} tag_style must be markdown_code")
    if not isinstance(policy.get("show_signal_id"), bool):
        fail(f"{TARGET_NOTICE_POLICY} show_signal_id must be boolean")
    events = policy.get("events")
    if not isinstance(events, dict):
        fail(f"{TARGET_NOTICE_POLICY} events must be an object")
    for kind, required_states in NOTICE_REQUIRED_STATES.items():
        event = events.get(kind)
        if not isinstance(event, dict) or not isinstance(event.get("tag"), str):
            fail(f"{TARGET_NOTICE_POLICY} missing notice event: {kind}")
        if "show_state_label" in event and not isinstance(event["show_state_label"], bool):
            fail(f"{TARGET_NOTICE_POLICY} {kind} show_state_label must be boolean")
        if "details_separator" in event and not isinstance(event["details_separator"], str):
            fail(f"{TARGET_NOTICE_POLICY} {kind} details_separator must be a string")
        states = event.get("states")
        if not isinstance(states, dict) or not required_states.issubset(states):
            missing = sorted(required_states - set(states or {}))
            fail(f"{TARGET_NOTICE_POLICY} {kind} missing states: {', '.join(missing)}")
        for state in required_states:
            visual = states[state]
            if not isinstance(visual, dict) or not all(
                isinstance(visual.get(field), str) and visual[field].strip()
                for field in ("icon", "label")
            ):
                fail(f"{TARGET_NOTICE_POLICY} {kind}/{state} must define icon and label")
    ok("notice policy satisfies the target-Skill visual contract")


def check_maintainer(
    args: argparse.Namespace,
    *,
    snapshot_verified_from_official_release: bool = False,
) -> None:
    target = Path(args.target).resolve()
    missing = [path for path in MAINTAINER_REQUIRED_FILES if not (target / path).exists()]
    if missing:
        fail("missing required maintainer wrapper files:\n" + "\n".join(f"- {path}" for path in missing))
    manifest = load_wrapper_manifest(target)
    if manifest is None:
        fail(f"missing wrapper manifest: {TARGET_WRAPPER_MANIFEST}")
    check_harness_skill_contract(target, manifest, allow_legacy=False)
    check_harness_entry_contract(target, manifest)
    check_notice_policy(target)
    check_onboarding_contract(manifest)
    check_dashboard_contract(manifest)
    check_contributor_branch_contract(
        target,
        manifest,
        snapshot_verified_from_official_release=snapshot_verified_from_official_release,
    )
    check_integration_contract(target, manifest)
    check_runtime(args)
    ok("maintainer bundle contains required wrapper files")


def check_doctor(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    require_command("git")
    require_command("gh")

    git_root_result = run_command(["git", "-C", str(target), "rev-parse", "--show-toplevel"])
    if git_root_result.returncode != 0 or not git_root_result.stdout.strip():
        fail("Evolution Harness requires an independent Git repository")
    git_root = Path(git_root_result.stdout.strip()).resolve()
    if git_root != target:
        fail(f"Harness doctor must run at the Git repository root: {git_root}")

    auth = run_command(["gh", "auth", "status"])
    if auth.returncode != 0:
        fail("gh is installed but not authenticated; run gh auth login")
    ok("gh authenticated")

    repo = args.repo
    if repo and not GITHUB_REPO_RE.match(repo):
        fail(f"--repo must use OWNER/REPO format: {repo}")

    manifest = load_wrapper_manifest(target)
    if manifest:
        check_harness_skill_contract(target, manifest, allow_legacy=True)
        if manifest.get("harness_skill_path") is not None:
            check_harness_entry_contract(target, manifest)
        check_wrapper_managed_doctor(target, repo, manifest, args.allow_missing_repo)
        return

    remote_result = run_command(["git", "-C", str(git_root), "remote", "get-url", "origin"])
    if remote_result.returncode == 0:
        remote_repo = repo_from_remote(remote_result.stdout)
        if remote_repo:
            repo = repo or remote_repo
            ok(f"origin GitHub repo detected: {remote_repo}")
        else:
            fail(f"origin remote is not a GitHub repo: {remote_result.stdout.strip()}")
    else:
        fail("target independent Git repository has no GitHub origin")

    if repo:
        view = run_command(["gh", "repo", "view", repo, "--json", "nameWithOwner,url,visibility"])
        if view.returncode != 0:
            detail = (view.stderr or view.stdout or "").strip()
            fail(f"cannot access GitHub repo {repo}: {detail}")
        ok(f"GitHub repo accessible: {repo}")


def check_wrapper_managed_doctor(
    target: Path,
    requested_repo: str | None,
    manifest: dict,
    allow_missing_repo: bool,
) -> None:
    manifest_repo = manifest.get("canonical_repo")
    if not manifest_repo or not GITHUB_REPO_RE.match(manifest_repo):
        fail(f"{TARGET_WRAPPER_MANIFEST} must contain canonical_repo in OWNER/REPO format")
    if requested_repo and requested_repo != manifest_repo:
        fail(f"--repo {requested_repo} does not match wrapper canonical_repo {manifest_repo}")
    ok(f"wrapper manifest detected: canonical_repo={manifest_repo}")

    canonical_path = target_canonical_path(target)
    pointer = project_pointer_path(manifest_repo)
    if not pointer.exists() and not pointer.is_symlink():
        fail(f"project pointer is missing: {pointer}")
    if not pointer.is_symlink():
        fail(f"project pointer must be a symlink to the canonical repo: {pointer}")
    pointer_resolved = resolve_path(pointer)
    if pointer_resolved != canonical_path:
        fail(f"project pointer mismatch: {pointer} -> {pointer_resolved}; expected {canonical_path}")
    ok(f"project pointer resolves to canonical repo: {pointer} -> {pointer_resolved}")

    origin_repo = git_origin_repo(Path(canonical_path))
    if origin_repo:
        if origin_repo != manifest_repo:
            fail(f"canonical repo origin {origin_repo} does not match wrapper canonical_repo {manifest_repo}")
        ok(f"canonical repo origin matches wrapper manifest: {origin_repo}")
    else:
        fail("canonical independent Git repository has no GitHub origin")

    view = run_command(["gh", "repo", "view", manifest_repo, "--json", "nameWithOwner,url,visibility"])
    if view.returncode != 0:
        detail = (view.stderr or view.stdout or "").strip()
        fail(f"cannot access GitHub repo {manifest_repo}: {detail}")
    else:
        ok(f"GitHub repo accessible: {manifest_repo}")

    skill_name = skill_name_from_skill_md(target / "SKILL.md") or target.name
    manifest_install_paths = [
        Path(item).expanduser()
        for item in manifest.get("install_links", [])
        if isinstance(item, str) and item.strip()
    ]
    install_paths = manifest_install_paths or [
        Path.home() / ".codex" / "skills" / skill_name,
        Path.home() / ".agents" / "skills" / skill_name,
    ]
    install_paths = list(dict.fromkeys(install_paths))
    found_install = False
    for install_path in install_paths:
        if not install_path.exists() and not install_path.is_symlink():
            continue
        found_install = True
        kind = path_kind(install_path)
        resolved = resolve_path(install_path)
        pointer_scope = runtime_pointer_scope(Path(canonical_path), resolved)
        if kind == "symlink" and pointer_scope == "canonical_repo":
            ok(f"runtime install points to canonical repo: {install_path} -> {resolved}")
        elif kind == "symlink" and pointer_scope == "canonical_subskill":
            ok(f"runtime install points to canonical sub-Skill: {install_path} -> {resolved}")
        elif kind == "symlink":
            fail(
                f"runtime install symlink mismatch: {install_path} -> {resolved}; "
                f"expected {canonical_path} or a direct canonical skills/<name> entry"
            )
        elif kind == "directory":
            warn(f"runtime install is a real directory copy, not a source of truth: {install_path}")
        else:
            warn(f"runtime install is not a usable pointer: {install_path} ({kind})")
    if not found_install:
        ok("no runtime install pointers found; canonical repo remains the only discovered source")


def check_structure(args: argparse.Namespace) -> None:
    check_maintainer(args)


def check_issue(args: argparse.Namespace) -> None:
    body = read_text(Path(args.file))
    check_terms(body, ISSUE_TERMS, "issue")
    if not has_real_content(body):
        fail("issue body looks empty or placeholder-only")
    ok("issue body satisfies feedback template concepts")


def find_design_doc(target: Path) -> Path:
    docs_dir = target / TARGET_DESIGNS_DIR
    candidates = [
        path
        for path in docs_dir.glob("*.md")
        if path.name.lower() != "readme.md" and "template" not in path.name.lower()
    ]
    if not candidates:
        fail(f"no design doc found under {TARGET_DESIGNS_DIR}/*.md")
    return sorted(candidates)[-1]


def changelog_has_unreleased_entry(changelog: str) -> bool:
    match = re.search(r"^## \[?Unreleased\]?.*?(?=^## |\Z)", changelog, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if not match:
        return False
    section = match.group(0)
    lines = [
        line.strip()
        for line in section.splitlines()
        if line.strip().startswith("-") and "none yet" not in line.lower()
    ]
    return bool(lines)


def check_design_doc(path: Path) -> None:
    text = read_text(path)
    check_terms(text, DESIGN_TERMS, f"design doc {path}")
    if not has_real_content(text):
        fail(f"design doc looks placeholder-only: {path}")
    ok(f"design doc has required concepts: {path}")


def collect_live_github_issue(
    repo: str,
    issue_number: int,
    *,
    runner=subprocess.run,
) -> dict[str, object]:
    try:
        result = runner(
            ["gh", "api", f"repos/{repo}/issues/{issue_number}", "--hostname", "github.com"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        fail("PR Issue could not be verified through the trusted GitHub API path")
    if result.returncode != 0:
        fail("PR Issue could not be verified through the trusted GitHub API path")
    try:
        issue = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("trusted GitHub Issue evidence is invalid")
    if not isinstance(issue, dict):
        fail("trusted GitHub Issue evidence must be an object")
    return issue


def check_live_feedback_issue(
    contract: dict,
    *,
    repo: str,
    issue_number: int,
    runner=subprocess.run,
) -> None:
    issue = collect_live_github_issue(repo, issue_number, runner=runner)
    labels = issue.get("labels")
    label_names = [
        label if isinstance(label, str) else label.get("name")
        for label in labels
        if isinstance(label, (str, dict))
    ] if isinstance(labels, list) else []
    if (
        issue.get("number") != issue_number
        or str(issue.get("state", "")).upper() != contract.get("issue_resolution", {}).get("required_state")
        or "pull_request" in issue
        or not isinstance(issue.get("title"), str)
        or not all(isinstance(label, str) for label in label_names)
    ):
        fail("PR must reference the matching live OPEN GitHub Issue, not a Pull Request")

    classification = contract.get("profiles", {}).get(CONTRIBUTOR_BRANCH_PROFILE, {}).get("issue_classification", {})
    normalized_labels = {label.lower() for label in label_names}
    label_match = any(
        isinstance(label, str) and label.lower() in normalized_labels
        for label in classification.get("labels_any", [])
    )
    title_match = any(
        isinstance(prefix, str) and issue["title"].startswith(prefix)
        for prefix in classification.get("title_prefixes", [])
    )
    if classification.get("match") != "label_or_title_prefix" or not (label_match or title_match):
        fail("PR Issue is not classified as Skill feedback")
    ok("PR Issue identity, state, type, and Skill-feedback classification are live-verified")


def revalidate_linked_pull_request_runs(
    repo: str,
    issue_number: int,
    *,
    runner=subprocess.run,
) -> dict[str, object]:
    if not GITHUB_REPO_RE.fullmatch(repo) or issue_number < 1:
        fail("Issue-triggered PR revalidation requires OWNER/REPO and a positive Issue number")

    def request_json(endpoint: str, label: str) -> object:
        try:
            result = runner(
                ["gh", "api", endpoint, "--hostname", "github.com"],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            fail(f"{label} is unavailable")
        if result.returncode != 0:
            fail(f"{label} is unavailable")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            fail(f"{label} is invalid")

    issue_pattern = re.compile(
        rf"(?mi)^-[ \t]+Issue:[ \t]*`?{re.escape(repo)}#{issue_number}`?[ \t]*$",
    )
    linked_prs: dict[int, dict] = {}
    for page in range(1, 11):
        pull_requests = request_json(
            f"repos/{repo}/pulls?state=open&per_page=100&page={page}",
            "open Pull Request evidence",
        )
        if not isinstance(pull_requests, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("number"), int)
            or item.get("body") is not None and not isinstance(item.get("body"), str)
            or not isinstance(item.get("head"), dict)
            or not re.fullmatch(r"[0-9a-f]{40}", str(item["head"].get("sha", "")))
            for item in pull_requests
        ):
            fail("open Pull Request evidence is invalid")
        for pull_request in pull_requests:
            if issue_pattern.search(pull_request.get("body") or ""):
                linked_prs[pull_request["number"]] = pull_request
        if len(pull_requests) < 100:
            break
    else:
        fail("open Pull Request inventory exceeds the trusted revalidation limit")

    if not linked_prs:
        return {
            "schema_version": "evozeus.coevolve.issue-pr-revalidation.v1",
            "repository": repo,
            "issue_number": issue_number,
            "linked_pull_requests": [],
            "rerun_requested": [],
            "already_pending": [],
            "writes": False,
        }

    latest_runs: dict[int, dict] = {}
    for page in range(1, 11):
        workflow_runs = request_json(
            f"repos/{repo}/actions/workflows/evozeus-wrapper-preflight.yml/runs"
            f"?event=pull_request_target&exclude_pull_requests=false&per_page=100&page={page}",
            "trusted Pull Request workflow-run evidence",
        )
        runs = workflow_runs.get("workflow_runs") if isinstance(workflow_runs, dict) else None
        if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
            fail("trusted Pull Request workflow-run evidence is invalid")
        for run in runs:
            pull_requests = run.get("pull_requests")
            if not isinstance(pull_requests, list):
                fail("trusted Pull Request workflow-run association is invalid")
            for pull_request in pull_requests:
                number = pull_request.get("number") if isinstance(pull_request, dict) else None
                run_head = pull_request.get("head") if isinstance(pull_request, dict) else None
                current_head = linked_prs.get(number, {}).get("head")
                if (
                    number in linked_prs
                    and isinstance(run_head, dict)
                    and isinstance(current_head, dict)
                    and run_head.get("sha") == current_head.get("sha")
                ):
                    run_number = run.get("run_number")
                    previous_number = latest_runs.get(number, {}).get("run_number", -1)
                    if not isinstance(run_number, int):
                        fail("trusted Pull Request workflow-run identity is invalid")
                    if run_number > previous_number:
                        latest_runs[number] = run
        if len(runs) < 100:
            break
    else:
        fail("trusted Pull Request workflow-run inventory exceeds the revalidation limit")

    missing_runs = sorted(set(linked_prs) - set(latest_runs))
    if missing_runs:
        fail(
            "linked Pull Requests have no trusted pull_request_target run for the current head; edit or reopen them: "
            + ", ".join(f"#{number}" for number in missing_runs)
        )

    rerun_ids: list[tuple[int, int]] = []
    already_pending: list[int] = []
    for number, run in latest_runs.items():
        run_id = run.get("id")
        status = run.get("status")
        event = run.get("event")
        if (
            not isinstance(run_id, int)
            or event != "pull_request_target"
            or status not in {"completed", "queued", "in_progress", "requested", "waiting", "pending"}
        ):
            fail("trusted Pull Request workflow-run identity is invalid")
        if status == "completed":
            rerun_ids.append((number, run_id))
        else:
            already_pending.append(number)

    rerun_requested: list[int] = []
    for number, run_id in rerun_ids:
        try:
            result = runner(
                [
                    "gh", "api", "--method", "POST",
                    f"repos/{repo}/actions/runs/{run_id}/rerun",
                    "--hostname", "github.com",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            fail(f"Pull Request #{number} trusted workflow re-run request is unavailable")
        if result.returncode != 0:
            fail(f"Pull Request #{number} trusted workflow re-run request failed")
        rerun_requested.append(number)

    return {
        "schema_version": "evozeus.coevolve.issue-pr-revalidation.v1",
        "repository": repo,
        "issue_number": issue_number,
        "linked_pull_requests": sorted(linked_prs),
        "rerun_requested": sorted(rerun_requested),
        "already_pending": sorted(already_pending),
        "writes": bool(rerun_requested),
    }


def check_trusted_pr_checkouts(
    candidate: Path,
    trusted_root: Path,
    *,
    github_head_sha: str,
    github_base_sha: str,
    github_repository: str,
    github_head_repo: str,
    github_actor: str,
    github_head_ref: str,
    github_pr_number: int,
    github_api_runner=subprocess.run,
) -> str:
    for path, expected, label in (
        (candidate, github_head_sha, "candidate"),
        (trusted_root, github_base_sha, "trusted base"),
    ):
        result = run_command(["git", "-C", str(path), "rev-parse", "HEAD"])
        if result.returncode != 0 or result.stdout.strip() != expected:
            fail(f"{label} checkout does not match the trusted GitHub event SHA")

    candidate_manifest_file = _manifest_relative_file(
        candidate,
        TARGET_WRAPPER_MANIFEST,
        "candidate wrapper manifest",
    )
    trusted_manifest_file = _manifest_relative_file(
        trusted_root,
        TARGET_WRAPPER_MANIFEST,
        "trusted wrapper manifest",
    )
    candidate_manifest = read_json_object(candidate_manifest_file)
    trusted_manifest = read_json_object(trusted_manifest_file)

    expected_managed_files = [
        path for path in MAINTAINER_REQUIRED_FILES if path != TARGET_WRAPPER_MANIFEST
    ]
    official_managed_files = set(expected_managed_files) - {TARGET_CHANGELOG}
    if set(TRUSTED_CONTROL_SOURCES) != official_managed_files:
        fail("trusted Harness managed-file source map is incomplete")
    changed: list[str] = []
    for relative_path in expected_managed_files:
        candidate_file = _manifest_relative_file(candidate, relative_path, "candidate PR control file")
        trusted_file = _manifest_relative_file(trusted_root, relative_path, "trusted base control file")
        if relative_path == TARGET_CHANGELOG:
            continue
        if candidate_file.read_bytes() != trusted_file.read_bytes():
            changed.append(relative_path)
    if candidate_manifest_file.read_bytes() != trusted_manifest_file.read_bytes():
        changed.append(TARGET_WRAPPER_MANIFEST)
    if not changed:
        ok("business PR control files and manifest are byte-bound to the trusted base")
        return "business"

    candidate_version = candidate_manifest.get("wrapper_version")
    trusted_version = trusted_manifest.get("wrapper_version")
    if not isinstance(candidate_version, str) or not isinstance(trusted_version, str):
        fail("Harness upgrade requires semantic base and candidate wrapper versions")
    if version_key(candidate_version) <= version_key(trusted_version):
        fail("business PR cannot change trusted Harness control files")
    if (
        trusted_manifest.get("canonical_repo") != github_repository
        or candidate_manifest.get("canonical_repo") != github_repository
        or candidate_manifest.get("wrapper_repo") != COEVOLVE_SOURCE_REPO
    ):
        fail("Harness upgrade manifest does not match the trusted target and source repositories")
    preserved_manifest_fields = (
        "canonical_repo",
        "instruction_surface",
        "install_links",
        "integration",
        "onboarding",
        "dashboard",
        "runtime_bundle",
    )
    if any(candidate_manifest.get(field) != trusted_manifest.get(field) for field in preserved_manifest_fields):
        fail("Harness upgrade changes target-owned manifest bindings")
    canonical_identity = {
        "wrapper_repo": COEVOLVE_SOURCE_REPO,
        "layout_version": 2,
        "target_wrapper_dir": TARGET_EVOINFRA_DIR,
        "target_infra_dir": TARGET_EVOINFRA_DIR,
        "legacy_layout_dirs": [LEGACY_TARGET_EVOINFRA_DIR, OLDEST_TARGET_EVOINFRA_DIR],
        "harness_skill_path": TARGET_HARNESS_SKILL,
        "harness_skill_version": HARNESS_SKILL_VERSION,
        "harness_skill_managed": True,
        "managed_files": expected_managed_files,
        "contributor_branch": EXPECTED_CONTRIBUTOR_BRANCH,
    }
    if any(candidate_manifest.get(field) != value for field, value in canonical_identity.items()):
        fail("Harness upgrade manifest wrapper-owned identity is not canonical")
    if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", str(candidate_manifest.get("applied_at", ""))):
        fail("Harness upgrade manifest applied_at is invalid")

    integration = candidate_manifest.get("integration", {})
    repo_hook_installed = bool(
        (integration.get("capabilities") or {})
        .get("repo_maintenance_hook", {})
        .get("installed")
    ) if isinstance(integration, dict) else False
    expected_hook_registration = {
        "codex": {
            "capability": "repo_maintenance_hook",
            "config_file": CODEX_HOOKS_CONFIG,
            "hook_script": CODEX_START_HOOK_SCRIPT,
            "event": "SessionStart",
            "matcher": "startup|resume",
            "scope": "canonical_repository",
            "covers_skill_invocation": False,
            "installation_status": "installed" if repo_hook_installed else "not_installed",
            "trust_status": "pending_review" if repo_hook_installed else "not_installed",
            "trust_review": "required_by_codex_hooks",
            "latest_version_env": "EVOZEUS_WRAPPER_LATEST_VERSION",
            "enforcement_env": "EVOZEUS_WRAPPER_HOOK_ENFORCEMENT",
        },
    }
    if candidate_manifest.get("hook_registration") != expected_hook_registration:
        fail("Harness upgrade manifest hook registration is not canonical")
    wrapper_owned_mutable_keys = {
        *canonical_identity,
        "wrapper_version",
        "applied_at",
        "hook_registration",
    }
    preserved_keys = (set(candidate_manifest) | set(trusted_manifest)) - wrapper_owned_mutable_keys
    if any(
        (key in candidate_manifest) != (key in trusted_manifest)
        or candidate_manifest.get(key) != trusted_manifest.get(key)
        for key in preserved_keys
    ):
        fail("Harness upgrade adds or changes a noncanonical manifest field")
    expected_upgrade_branch = f"evozeus/harness-{trusted_version}-to-{candidate_version}"
    if github_head_ref != expected_upgrade_branch:
        fail("Harness upgrade branch does not match the trusted from/to versions")
    if github_head_repo.lower() != github_repository.lower():
        fail("Harness upgrade must use a direct branch in the canonical repository")

    try:
        permission_result = github_api_runner(
            [
                "gh", "api",
                f"repos/{github_repository}/collaborators/{github_actor}/permission",
                "--hostname", "github.com",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        fail("Harness upgrade author permission could not be live-verified")
    if permission_result.returncode != 0:
        fail("Harness upgrade author permission could not be live-verified")
    try:
        permission = json.loads(permission_result.stdout)
    except json.JSONDecodeError:
        fail("Harness upgrade author permission evidence is invalid")
    if (
        not isinstance(permission, dict)
        or str(permission.get("permission", "")).lower() != "admin"
        or permission.get("user", {}).get("permissions", {}).get("admin") is not True
    ):
        fail("Harness upgrade requires live ADMIN permission for the PR author")

    try:
        release_result = github_api_runner(
            [
                "gh", "api",
                f"repos/{COEVOLVE_SOURCE_REPO}/releases/tags/{candidate_version}",
                "--hostname", "github.com",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        fail("Harness upgrade release provenance is unavailable")
    if release_result.returncode != 0:
        fail("Harness upgrade release provenance is unavailable")
    try:
        release = json.loads(release_result.stdout)
    except json.JSONDecodeError:
        fail("Harness upgrade release provenance is invalid")
    if (
        not isinstance(release, dict)
        or release.get("tag_name") != candidate_version
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(release.get("published_at"), str)
    ):
        fail("Harness upgrade requires an official published CoEvolve Release")

    changed_entries: dict[str, dict] = {}
    for page in range(1, 11):
        try:
            files_result = github_api_runner(
                [
                    "gh", "api",
                    f"repos/{github_repository}/pulls/{github_pr_number}/files?per_page=100&page={page}",
                    "--hostname", "github.com",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            fail("Harness upgrade file diff could not be live-verified")
        if files_result.returncode != 0:
            fail("Harness upgrade file diff could not be live-verified")
        try:
            page_files = json.loads(files_result.stdout)
        except json.JSONDecodeError:
            fail("Harness upgrade file diff evidence is invalid")
        if not isinstance(page_files, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("filename"), str)
            for item in page_files
        ):
            fail("Harness upgrade file diff evidence is invalid")
        for item in page_files:
            changed_entries[item["filename"]] = item
        if len(page_files) < 100:
            break
    else:
        fail("Harness upgrade file diff exceeds the trusted verification limit")

    trusted_surface = trusted_manifest.get("instruction_surface")
    candidate_surface = candidate_manifest.get("instruction_surface")
    if (
        not isinstance(trusted_surface, str)
        or candidate_surface != trusted_surface
        or Path(trusted_surface).is_absolute()
        or ".." in Path(trusted_surface).parts
    ):
        fail("Harness upgrade must preserve the trusted instruction surface")
    trusted_surface_file = _manifest_relative_file(
        trusted_root,
        trusted_surface,
        "trusted instruction surface",
    )
    candidate_surface_file = _manifest_relative_file(
        candidate,
        candidate_surface,
        "candidate instruction surface",
    )

    def business_surface(text: str) -> str:
        if text.count(HARNESS_ENTRY_BEGIN) != 1 or text.count(HARNESS_ENTRY_END) != 1:
            fail("Harness upgrade instruction surface requires one owned activation block")
        start = text.index(HARNESS_ENTRY_BEGIN)
        end = text.index(HARNESS_ENTRY_END, start) + len(HARNESS_ENTRY_END)
        block = text[start:end].replace("\r\n", "\n")
        if block != _canonical_harness_entry_block():
            fail("Harness upgrade instruction surface activation block is not canonical")
        return text[:start] + text[end:]

    trusted_surface_text = trusted_surface_file.read_text(encoding="utf-8")
    candidate_surface_text = candidate_surface_file.read_text(encoding="utf-8")
    if business_surface(candidate_surface_text) != business_surface(trusted_surface_text):
        fail("Harness upgrade changes target-owned instruction-surface bytes")
    fixed_outputs = {
        *TRUSTED_CONTROL_SOURCES,
        TARGET_WRAPPER_MANIFEST,
    }
    if candidate_surface_text != trusted_surface_text:
        fixed_outputs.add(trusted_surface)
    migration_pattern = re.compile(
        rf"^{re.escape(TARGET_MIGRATIONS_DIR)}/20[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}-"
        rf"{re.escape(trusted_version)}-to-{re.escape(candidate_version)}\.md$"
    )
    changed_paths = set(changed_entries)
    unexpected = sorted(
        path for path in changed_paths
        if path not in fixed_outputs and not migration_pattern.fullmatch(path)
    )
    if not set(changed).issubset(changed_paths) or TARGET_WRAPPER_MANIFEST not in changed_paths:
        fail("Harness upgrade API diff does not contain the verified control-plane changes")
    if unexpected:
        fail("Harness upgrade diff contains files outside official managed and migration outputs")

    trusted_changelog = read_text(trusted_root / TARGET_CHANGELOG)
    version_matches = list(re.finditer(r"^##\s+\[?(v\d+\.\d+\.\d+)\]?", trusted_changelog, re.MULTILINE))
    current_skill_version = version_matches[0].group(1) if version_matches else "v0.1.0"
    initial_skill_version = version_matches[-1].group(1) if version_matches else current_skill_version
    initial_date_match = re.search(
        rf"^##\s+\[?{re.escape(initial_skill_version)}\]?\s+-\s+(20[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}})",
        trusted_changelog,
        re.MULTILINE,
    )
    trusted_wrapper = read_text(trusted_root / TARGET_WRAPPER_GUIDE)
    visibility_match = re.search(r"(?m)^- Selected visibility:\s*([^\r\n]+)$", trusted_wrapper)
    replacements = {
        "SKILL_NAME": skill_name_from_skill_md(trusted_surface_file) or github_repository.split("/", 1)[1],
        "REPO_NAME": github_repository,
        "REPO_URL": f"https://github.com/{github_repository}",
        "CURRENT_VERSION": current_skill_version,
        "INITIAL_VERSION": initial_skill_version,
        "DATE": initial_date_match.group(1) if initial_date_match else candidate_manifest["applied_at"],
        "VISIBILITY": visibility_match.group(1).strip() if visibility_match else "public",
        "WRAPPER_VERSION": candidate_version,
    }
    for relative_path in sorted(TRUSTED_CONTROL_SOURCES):
        source_path = TRUSTED_CONTROL_SOURCES[relative_path]
        try:
            source_result = github_api_runner(
                [
                    "gh", "api",
                    f"repos/{COEVOLVE_SOURCE_REPO}/contents/{source_path}?ref={candidate_version}",
                    "--hostname", "github.com",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            fail("Harness upgrade source provenance is unavailable")
        if source_result.returncode != 0:
            fail("Harness upgrade source provenance is unavailable")
        try:
            source_data = json.loads(source_result.stdout)
            official_text = base64.b64decode(source_data["content"]).decode("utf-8")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, UnicodeDecodeError):
            fail("Harness upgrade source provenance is invalid")
        for key, value in replacements.items():
            official_text = official_text.replace(f"{{{{{key}}}}}", value)
        if re.search(r"\{\{[A-Z_]+\}\}", official_text):
            fail("Harness upgrade official template has unresolved target placeholders")
        candidate_file = _manifest_relative_file(candidate, relative_path, "candidate Harness upgrade file")
        if relative_path == CODEX_HOOKS_CONFIG:
            trusted_hooks_file = _manifest_relative_file(
                trusted_root,
                relative_path,
                "trusted Codex hooks config",
            )
            try:
                official_hooks = json.loads(official_text)
                trusted_hooks = json.loads(trusted_hooks_file.read_text(encoding="utf-8"))
                candidate_hooks = json.loads(candidate_file.read_text(encoding="utf-8"))
                wrapper_entry = official_hooks["hooks"]["SessionStart"][0]
                expected_hooks = copy.deepcopy(trusted_hooks)
                session_start = expected_hooks.setdefault("hooks", {}).setdefault("SessionStart", [])
                if not isinstance(session_start, list):
                    raise TypeError("trusted SessionStart hooks must be a list")
                preserved = []
                for entry in session_start:
                    handlers = entry.get("hooks") if isinstance(entry, dict) else None
                    if not isinstance(handlers, list):
                        raise TypeError("trusted SessionStart entry must contain hooks")
                    wrapper_owned = any(
                        isinstance(handler, dict)
                        and isinstance(handler.get("command"), str)
                        and "evozeus_wrapper_start_check.py" in handler["command"]
                        for handler in handlers
                    )
                    if not wrapper_owned:
                        preserved.append(entry)
                expected_hooks["hooks"]["SessionStart"] = [*preserved, wrapper_entry]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
                fail("Harness upgrade Codex hooks provenance is invalid")
            if candidate_hooks != expected_hooks:
                fail("Harness upgrade changes target-owned Codex hook entries")
            continue
        if candidate_file.read_bytes() != official_text.encode("utf-8"):
            fail(f"candidate Harness control file does not match official Release {candidate_version}: {relative_path}")
    for path, item in changed_entries.items():
        status = item.get("status")
        if migration_pattern.fullmatch(path):
            if status != "added":
                fail("Harness upgrade migration record must be a newly added file")
            migration_file = _manifest_relative_file(
                candidate,
                path,
                "Harness upgrade migration record",
            )
            migration_text = migration_file.read_text(encoding="utf-8")
            if not all(
                term in migration_text
                for term in (trusted_version, candidate_version, "## 验证", "## 回滚")
            ):
                fail("Harness upgrade migration record is incomplete")
        elif status != "modified":
            fail("Harness upgrade cannot add, remove, or rename governed existing files")
        if item.get("previous_filename") is not None:
            fail("Harness upgrade cannot hide a source path through a rename")

    ok(f"Harness upgrade control files match official CoEvolve Release {candidate_version}")
    return "official_harness_upgrade"


def check_branch_pr_metadata(
    target: Path,
    body: str,
    *,
    github_repository: str | None,
    github_head_ref: str | None,
    github_head_repo: str | None,
    github_actor: str | None,
    github_base_ref: str | None,
    github_base_sha: str | None,
    plan_asset_root: Path | None = None,
    issue_runner=subprocess.run,
) -> None:
    labels = (
        "Resume key",
        "Core source revision",
        "Contract SHA-256",
        "Profile",
        "Purpose type / component / summary",
        "Canonical repo",
        "Base ref / commit",
        "Target branch",
        "Issue",
        "Verified actor",
        "Permission path",
    )
    values: dict[str, str] = {}
    for label in labels:
        match = re.search(rf"(?m)^-[ \t]+{re.escape(label)}:[ \t]*(?P<value>[^\r\n]+)$", body)
        if not match or not match.group("value").strip():
            fail(f"PR body must contain a populated Contributor Branch Plan field: {label}")
        value = match.group("value").strip().strip("`").strip()
        if value.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            fail(f"PR Contributor Branch Plan cannot expose a local path: {label}")
        values[label] = value

    try:
        assets = verify_managed_snapshot(plan_asset_root or target / TARGET_EVOINFRA_DIR)
    except ContributorBranchError:
        fail("Contributor Branch Plan snapshot does not match the trusted base verifier")
    provenance = assets["provenance"]
    contract = assets["contract"]
    if values["Core source revision"] != provenance.get("source_revision"):
        fail("PR Core source revision must match the managed contributor branch provenance")
    if values["Contract SHA-256"] != provenance.get("contract", {}).get("sha256"):
        fail("PR contract digest must match the managed contributor branch provenance")
    repo = values["Canonical repo"]
    if (
        not GITHUB_REPO_RE.fullmatch(repo)
        or any(component in {".", ".."} for component in repo.split("/", 1))
    ):
        fail("PR canonical repo must use OWNER/REPO")
    if not RESUME_KEY_RE.fullmatch(values["Resume key"]):
        fail("PR resume key must use the contributor branch v1 format")
    if values["Profile"] != CONTRIBUTOR_BRANCH_PROFILE:
        fail("PR profile must use the CoEvolve target Skillware consumer")
    purpose_match = re.fullmatch(
        r"(dev|bug|refactor|docs|test|chore)[ \t]*/[ \t]*([a-z0-9]+(?:-[a-z0-9]+)*)[ \t]*/[ \t]*([a-z0-9]+(?:-[a-z0-9]+){0,6})",
        values["Purpose type / component / summary"],
    )
    if not purpose_match:
        fail("PR purpose must include valid type, component, and summary fields")
    purpose_type, component, summary = purpose_match.groups()
    profile = contract.get("profiles", {}).get(CONTRIBUTOR_BRANCH_PROFILE, {})
    if purpose_type not in profile.get("allowed_types", []):
        fail("PR purpose type is not allowed by the trusted contributor branch contract")
    base_match = re.fullmatch(r"origin/(main|master)[ \t]*/[ \t]*([0-9a-f]{40})", values["Base ref / commit"])
    if not base_match:
        fail("PR base must include canonical origin/main or origin/master and a full commit")
    branch_match = re.fullmatch(
        rf"codex/{re.escape(purpose_type)}/(20[0-9]{{6}})-{re.escape(component)}-{re.escape(summary)}",
        values["Target branch"],
    )
    if not branch_match:
        fail("PR target branch does not match the contributor branch namespace")
    issue_match = re.fullmatch(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]*)", values["Issue"])
    if not issue_match or issue_match.group(1).lower() != repo.lower():
        fail("PR Issue must belong to the canonical repo")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", values["Verified actor"]):
        fail("PR verified actor must use a GitHub login")
    if values["Permission path"] not in {"direct", "fork"}:
        fail("PR permission path must allow a remote Pull Request")
    if not all((github_repository, github_head_ref, github_head_repo, github_actor, github_base_ref, github_base_sha)):
        fail("PR metadata validation requires trusted GitHub repository, actor, head, and base context")
    if repo != github_repository:
        fail("PR canonical repo does not match github.repository")
    if values["Target branch"] != github_head_ref:
        fail("PR target branch does not match github.head_ref")
    if values["Verified actor"].lower() != github_actor.lower():
        fail("PR verified actor does not match the pull request author")
    if base_match.group(1) != github_base_ref:
        fail("PR base ref does not match github.base_ref")
    if base_match.group(2) != github_base_sha:
        fail("PR base commit does not match github.event.pull_request.base.sha")
    direct_head = github_head_repo.lower() == repo.lower()
    expected_fork = f"{github_actor}/{repo.split('/', 1)[1]}"
    fork_head = github_head_repo.lower() == expected_fork.lower()
    event_permission = "direct" if direct_head else ("fork" if fork_head else None)
    if event_permission is None or values["Permission path"] != event_permission:
        fail("PR permission path does not match the trusted head repository topology")

    expected_resume_key = compute_resume_key(
        profile=values["Profile"],
        repo=repo,
        base_ref=f"origin/{github_base_ref}",
        issue=values["Issue"],
        actor=github_actor,
        permission=event_permission,
        purpose_type=purpose_type,
        component=component,
        summary=summary,
    )
    if values["Resume key"] != expected_resume_key:
        fail("PR resume key does not match the trusted branch-plan identity")

    check_live_feedback_issue(
        contract,
        repo=repo,
        issue_number=int(issue_match.group(2)),
        runner=issue_runner,
    )
    ok("PR body plan identity is recomputed from trusted GitHub context")


def check_pr(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    trusted_root: Path | None = None
    if args.pr_body:
        trusted_values = (
            args.trusted_root,
            args.github_head_sha,
            args.github_base_sha,
            args.github_repository,
            args.github_head_repo,
            args.github_actor,
            args.github_head_ref,
            args.github_pr_number,
        )
        if not all(trusted_values):
            fail("PR workflow validation requires complete trusted GitHub event context")
        trusted_root = Path(args.trusted_root).resolve()
        trust_mode = check_trusted_pr_checkouts(
            target,
            trusted_root,
            github_head_sha=args.github_head_sha,
            github_base_sha=args.github_base_sha,
            github_repository=args.github_repository,
            github_head_repo=args.github_head_repo,
            github_actor=args.github_actor,
            github_head_ref=args.github_head_ref,
            github_pr_number=args.github_pr_number,
        )
        if trust_mode == "business":
            check_maintainer(args)
        else:
            check_maintainer(args, snapshot_verified_from_official_release=True)
            ok("official Harness upgrade uses its dedicated ADMIN and Release provenance gate")
            return
    design_doc = Path(args.design_doc).resolve() if args.design_doc else find_design_doc(target)
    check_design_doc(design_doc)

    changelog = read_text(target / TARGET_CHANGELOG)
    if not changelog_has_unreleased_entry(changelog):
        fail(f"{TARGET_CHANGELOG} must contain a non-empty Unreleased entry for the PR")
    ok(f"{TARGET_CHANGELOG} has an Unreleased entry")

    if args.pr_body:
        body = read_text(Path(args.pr_body))
        if "design doc" not in normalize(body) and "设计" not in body:
            fail("PR body should reference the design doc")
        if TARGET_CHANGELOG not in body:
            fail(f"PR body should confirm {TARGET_CHANGELOG} was updated")
        check_branch_pr_metadata(
            target,
            body,
            github_repository=args.github_repository,
            github_head_ref=args.github_head_ref,
            github_head_repo=args.github_head_repo,
            github_actor=args.github_actor,
            github_base_ref=args.github_base_ref,
            github_base_sha=args.github_base_sha,
            plan_asset_root=trusted_root / TARGET_EVOINFRA_DIR,
        )
        ok("PR body references design doc and changelog")


def changelog_has_tag(changelog: str, tag: str) -> bool:
    escaped = re.escape(tag)
    return bool(re.search(rf"^##\s+\[?{escaped}\]?\b", changelog, re.MULTILINE))


def latest_changelog_tag(changelog: str) -> str | None:
    for match in re.finditer(r"^##\s+\[?(v\d+\.\d+\.\d+)\]?\b", changelog, re.MULTILINE):
        return match.group(1)
    return None


CHANNEL_LABELS = {
    "development": "开发版",
    "uat": "UAT",
    "stable": "正式版",
}


def classify_runtime_channel(
    *,
    branch: str,
    clean: bool,
    head: str | None,
    skill_release: str | None,
    latest_release_tag: str | None,
    latest_release_commit: str | None,
    declared_channel: str | None = None,
) -> str:
    if not clean:
        return "development"
    if declared_channel == "uat" or branch.startswith("uat/"):
        return "uat"
    if (
        branch in {"", "main", "master"}
        and head
        and skill_release
        and latest_release_tag == skill_release
        and latest_release_commit == head
    ):
        return "stable"
    return "development"


def latest_release_for_identity(repo: str) -> dict[str, object]:
    result = run_command(["gh", "release", "view", "--repo", repo, "--json", "tagName,url,publishedAt"])
    if result.returncode != 0:
        return {
            "available": False,
            "tag": None,
            "url": None,
            "error": (result.stderr or result.stdout).strip() or "latest release unavailable",
        }
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"available": False, "tag": None, "url": None, "error": "invalid release response"}
    tag = data.get("tagName")
    if not isinstance(tag, str) or not tag:
        return {"available": False, "tag": None, "url": data.get("url"), "error": "release tag missing"}
    return {"available": True, "tag": tag, "url": data.get("url"), "error": None}


def collect_runtime_git_facts(target: Path, release_tag: str | None) -> dict[str, object]:
    def output(args: list[str]) -> str | None:
        result = run_command(["git", "-C", str(target), *args])
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    status = output(["status", "--porcelain"])
    return {
        "branch": output(["branch", "--show-current"]) or "",
        "clean": status == "" if status is not None else False,
        "head": output(["rev-parse", "HEAD"]),
        "release_commit": output(["rev-list", "-n", "1", release_tag]) if release_tag else None,
        "origin_repo": git_origin_repo(target),
    }


def runtime_channel_reason(
    *,
    channel: str,
    branch: str,
    clean: bool,
    skill_release: str | None,
    latest_release_tag: str | None,
    head: str | None,
    latest_release_commit: str | None,
) -> str:
    if not clean:
        return "dirty_worktree"
    if channel == "uat":
        return "declared_uat_channel"
    if channel == "stable":
        return "exact_release_commit"
    if not skill_release:
        return "skill_unpublished"
    if not latest_release_tag:
        return "release_unverified"
    if branch not in {"", "main", "master"}:
        return "development_branch"
    if not head or not latest_release_commit or head != latest_release_commit:
        return "head_not_release_commit"
    return "development_fallback"


def build_runtime_identity(
    target: Path,
    *,
    latest_release: dict[str, object] | None = None,
    git_facts: dict[str, object] | None = None,
) -> dict[str, object]:
    target = target.expanduser().resolve()
    manifest = load_wrapper_manifest(target)
    if not manifest:
        fail(f"missing wrapper manifest: {TARGET_WRAPPER_MANIFEST}")

    canonical_repo = manifest.get("canonical_repo")
    if not isinstance(canonical_repo, str) or not GITHUB_REPO_RE.fullmatch(canonical_repo):
        fail(f"{TARGET_WRAPPER_MANIFEST} must contain canonical_repo in OWNER/REPO format")
    harness_version = manifest.get("wrapper_version")
    if not isinstance(harness_version, str) or not VERSION_RE.fullmatch(harness_version):
        fail(f"{TARGET_WRAPPER_MANIFEST} must contain wrapper_version in vMAJOR.MINOR.PATCH format")

    changelog_path = target / TARGET_CHANGELOG
    skill_release = (
        latest_changelog_tag(changelog_path.read_text(encoding="utf-8"))
        if changelog_path.is_file()
        else None
    )
    release = latest_release if latest_release is not None else latest_release_for_identity(canonical_repo)
    latest_release_tag = release.get("tag") if release.get("available") else None
    if not isinstance(latest_release_tag, str):
        latest_release_tag = None
    facts = git_facts if git_facts is not None else collect_runtime_git_facts(target, latest_release_tag)
    origin_repo = facts.get("origin_repo")
    if origin_repo != canonical_repo:
        fail(f"canonical repo origin {origin_repo or 'missing'} does not match wrapper canonical_repo {canonical_repo}")

    branch = facts.get("branch") if isinstance(facts.get("branch"), str) else ""
    clean = facts.get("clean") is True
    head = facts.get("head") if isinstance(facts.get("head"), str) else None
    release_commit = (
        facts.get("release_commit") if isinstance(facts.get("release_commit"), str) else None
    )
    declared_channel = manifest.get("runtime_channel")
    channel = classify_runtime_channel(
        branch=branch,
        clean=clean,
        head=head,
        skill_release=skill_release,
        latest_release_tag=latest_release_tag,
        latest_release_commit=release_commit,
        declared_channel=declared_channel if isinstance(declared_channel, str) else None,
    )
    channel_label = CHANNEL_LABELS[channel]
    skill_display = skill_release or "未发布"
    canonical_url = f"https://github.com/{canonical_repo}"
    notice_policy_path = target / TARGET_NOTICE_POLICY
    notice_policy = load_notice_policy(notice_policy_path if notice_policy_path.is_file() else None)
    identity_notice = render_notice(
        kind="skill",
        state="active",
        details=(
            f"[{canonical_repo}]({canonical_url}) · Skill {skill_display} · "
            f"Harness {harness_version} · `渠道：{channel_label}`"
        ),
        policy=notice_policy,
    )
    display_line = identity_notice["display_text"]
    return {
        "schema_version": "v1",
        "managed_by": "EvoZeus-CoEvolve",
        "icon": "🧙🏻‍♂️",
        "canonical_repo": canonical_repo,
        "canonical_url": canonical_url,
        "skill_release": skill_display,
        "harness_version": harness_version,
        "channel": channel,
        "channel_label": channel_label,
        "channel_reason": runtime_channel_reason(
            channel=channel,
            branch=branch,
            clean=clean,
            skill_release=skill_release,
            latest_release_tag=latest_release_tag,
            head=head,
            latest_release_commit=release_commit,
        ),
        "display_once_scope": "skill_invocation",
        "display_line": display_line,
        "notice": identity_notice,
    }


def release_body_from_gh(tag: str, repo: str | None) -> str | None:
    cmd = ["gh", "release", "view", tag, "--json", "body", "-q", ".body"]
    if repo:
        cmd.extend(["--repo", repo])
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout


def latest_release_from_gh(repo: str) -> dict[str, str]:
    cmd = ["gh", "release", "view", "--repo", repo, "--json", "tagName,url,publishedAt"]
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    except FileNotFoundError:
        fail("gh CLI is required to check the latest GitHub release")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        fail(f"could not read latest GitHub release for {repo}: {detail}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"could not parse gh release output for {repo}")
    if not data.get("tagName"):
        fail(f"latest GitHub release for {repo} has no tagName")
    return data


def check_release(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    version_key(args.tag)
    changelog = read_text(target / TARGET_CHANGELOG)
    if not changelog_has_tag(changelog, args.tag):
        fail(f"{TARGET_CHANGELOG} must contain a release entry for {args.tag}")
    ok(f"{TARGET_CHANGELOG} contains {args.tag}")

    body = ""
    if args.release_notes:
        body = read_text(Path(args.release_notes))
    elif not args.skip_gh:
        body = release_body_from_gh(args.tag, args.repo) or ""

    if not has_real_content(body):
        fail("release description is missing, too short, or placeholder-only")
    ok("release description is present")


def check_version(args: argparse.Namespace) -> None:
    target = Path(args.target).resolve()
    changelog = read_text(target / TARGET_CHANGELOG)
    current_tag = args.current_tag or latest_changelog_tag(changelog)
    if not current_tag:
        fail(f"could not infer current version from {TARGET_CHANGELOG}; pass --current-tag vMAJOR.MINOR.PATCH")
    current_key = version_key(current_tag)

    latest = latest_release_from_gh(args.repo)
    latest_tag = latest["tagName"]
    latest_key = version_key(latest_tag)
    if latest_key > current_key:
        fail(f"newer Skill release available: {latest_tag} > local {current_tag}. Update before running.")
    if latest_key < current_key:
        if args.no_release_needed:
            ok(
                f"local changelog version {current_tag} is ahead of latest GitHub release {latest_tag}; "
                "--no-release-needed explicitly bypassed release creation"
            )
            return
        fail(
            f"local changelog version {current_tag} is ahead of latest GitHub release {latest_tag}. "
            "Create the GitHub release or rerun with --no-release-needed only for changes that do not affect the installable artifact."
        )
        return
    ok(f"local Skill version matches latest GitHub release: {current_tag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for an EvoZeus-CoEvolve Skill repo.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check local git/gh dependencies and source repo access.")
    doctor.add_argument("--target", default=".", help="Target wrapped Skill repo path.")
    doctor.add_argument("--repo", help="GitHub repo in OWNER/REPO format. Defaults to origin remote or discovered candidate.")
    doctor.add_argument("--allow-missing-repo", action="store_true", help=argparse.SUPPRESS)

    runtime = sub.add_parser("runtime", help="Check runtime-copy runnable Skill files.")
    runtime.add_argument("--target", default=".", help="Target Skill runtime or wrapped repo path.")

    maintainer = sub.add_parser("maintainer", help="Check maintainer wrapper governance files.")
    maintainer.add_argument("--target", default=".", help="Target wrapped Skill repo path.")

    structure = sub.add_parser("structure", help="Check required wrapper files.")
    structure.add_argument("--target", default=".", help="Target wrapped Skill repo path.")

    issue = sub.add_parser("issue", help="Check a Skill feedback issue body.")
    issue.add_argument("--target", default=".", help="Target wrapped Skill repo path.")
    issue.add_argument("--file", required=True, help="Markdown file containing the issue body.")

    issue_prs = sub.add_parser(
        "issue-pr-revalidate",
        help="Re-run trusted PR gates linked to a changed Feedback Issue.",
    )
    issue_prs.add_argument("--github-repository", required=True, help="Trusted github.repository value.")
    issue_prs.add_argument("--github-issue-number", required=True, type=int, help="Trusted Issue event number.")
    issue_prs.add_argument("--json", action="store_true", help="Return the revalidation report as JSON.")

    pr = sub.add_parser("pr", help="Check Skill evolution PR readiness.")
    pr.add_argument("--target", default=".", help="Target wrapped Skill repo path.")
    pr.add_argument("--design-doc", help="Path to the design doc for this PR.")
    pr.add_argument("--pr-body", help="Optional PR body markdown file.")
    pr.add_argument("--trusted-root", help="Trusted base-SHA checkout used to execute PR validation.")
    pr.add_argument("--github-repository", help="Trusted github.repository value for PR metadata binding.")
    pr.add_argument("--github-head-ref", help="Trusted github.head_ref value for PR metadata binding.")
    pr.add_argument("--github-head-repo", help="Trusted pull_request.head.repo.full_name value.")
    pr.add_argument("--github-head-sha", help="Trusted pull_request.head.sha value.")
    pr.add_argument("--github-actor", help="Trusted pull_request.user.login value.")
    pr.add_argument("--github-pr-number", type=int, help="Trusted pull_request.number value.")
    pr.add_argument("--github-base-ref", help="Trusted github.base_ref value for PR metadata binding.")
    pr.add_argument("--github-base-sha", help="Trusted pull_request.base.sha value for PR metadata binding.")

    release = sub.add_parser("release", help="Check release readiness.")
    release.add_argument("--target", default=".", help="Target wrapped Skill repo path.")
    release.add_argument("--tag", required=True, help="Release tag, such as v0.1.0.")
    release.add_argument("--release-notes", help="Markdown file containing release notes.")
    release.add_argument("--repo", help="GitHub repo in OWNER/REPO format for gh release lookup.")
    release.add_argument("--skip-gh", action="store_true", help="Do not call gh release view when release notes are omitted.")

    version = sub.add_parser("version", help="Check whether GitHub has a newer Skill release.")
    version.add_argument("--target", default=".", help="Target wrapped Skill repo path.")
    version.add_argument("--repo", required=True, help="GitHub repo in OWNER/REPO format.")
    version.add_argument(
        "--current-tag",
        help=f"Current local Skill version. Defaults to latest release tag in {TARGET_CHANGELOG}.",
    )
    version.add_argument(
        "--no-release-needed",
        action="store_true",
        help="Explicitly allow local changelog to be ahead when the change does not affect the installable artifact.",
    )

    identity = sub.add_parser("identity", help="Render the EvoZeus runtime identity for this Skill invocation.")
    identity.add_argument("--target", default=".", help="Target wrapped Skill repo path.")
    identity.add_argument("--json", action="store_true", help="Return the versioned runtime_identity JSON object.")

    args = parser.parse_args()
    if args.command == "doctor":
        check_doctor(args)
    elif args.command == "runtime":
        check_runtime(args)
    elif args.command == "maintainer":
        check_maintainer(args)
    elif args.command == "structure":
        check_structure(args)
    elif args.command == "issue":
        check_issue(args)
    elif args.command == "issue-pr-revalidate":
        report = revalidate_linked_pull_request_runs(
            args.github_repository,
            args.github_issue_number,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            ok(
                f"Issue {args.github_repository}#{args.github_issue_number} revalidation queued for "
                f"{len(report['rerun_requested'])} linked Pull Request(s); "
                f"{len(report['already_pending'])} already pending"
            )
    elif args.command == "pr":
        check_pr(args)
    elif args.command == "release":
        check_release(args)
    elif args.command == "version":
        check_version(args)
    elif args.command == "identity":
        runtime_identity = build_runtime_identity(Path(args.target))
        if args.json:
            print(json.dumps({"runtime_identity": runtime_identity}, ensure_ascii=False))
        else:
            print(runtime_identity["display_line"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
