#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    from . import evozeus_harness_migration as migration_kernel
    from .evozeus_wrapper_lifecycle import (
        HARNESS_SKILL_VERSION,
        LEGACY_TARGET_WRAPPER_MANIFEST,
        OLDEST_TARGET_WRAPPER_MANIFEST,
        TARGET_HARNESS_SKILL,
        TARGET_MIGRATION_CONTRACT,
        WRAPPER_MANAGED_FILES,
        add_fresh_harness_entry,
        build_onboarding_contract,
        build_status_section,
        build_wrapper_manifest,
        independent_repo_root,
        latest_changelog_tag,
        load_wrapper_manifest,
        require_repo_admin,
        validate_instruction_surface_for_harness_entry,
        version_key,
        write_wrapper_manifest,
        wrapper_manifest_status,
    )
except ImportError:
    import evozeus_harness_migration as migration_kernel
    from evozeus_wrapper_lifecycle import (
        HARNESS_SKILL_VERSION,
        LEGACY_TARGET_WRAPPER_MANIFEST,
        OLDEST_TARGET_WRAPPER_MANIFEST,
        TARGET_HARNESS_SKILL,
        TARGET_MIGRATION_CONTRACT,
        WRAPPER_MANAGED_FILES,
        add_fresh_harness_entry,
        build_onboarding_contract,
        build_status_section,
        build_wrapper_manifest,
        independent_repo_root,
        latest_changelog_tag,
        load_wrapper_manifest,
        require_repo_admin,
        validate_instruction_surface_for_harness_entry,
        version_key,
        write_wrapper_manifest,
        wrapper_manifest_status,
    )


ROOT = Path(__file__).resolve().parents[1]
TARGET_TEMPLATE_DIR = ROOT / "templates" / "target"
PREFLIGHT_SCRIPT = ROOT / "scripts" / "evozeus_wrapper_preflight.py"
NOTICE_SCRIPT = ROOT / "scripts" / "evozeus_notice.py"
MIGRATION_CONTRACT_SOURCE = (
    ROOT / "contracts" / "v1" / "migrations" / "harness-migration-contract-v1.json"
)
TARGET_TEMPLATE_INVENTORY_SOURCE = ROOT / "contracts" / "v1" / "target-template-inventory.json"
EVOLUTION_SECTION_HEADING = "## 自进化方法"
WRAPPER_SECTION_HEADING = "## EvoZeus-CoEvolve"
LOCAL_PROJECTS_DIR = Path.home() / ".evozeus" / ".projects"
WRAPPER_VERSION = "v0.14.0"
TARGET_EVOINFRA_DIR = ".evozeus-wrapper"
TARGET_WRAPPER_MANIFEST = f"{TARGET_EVOINFRA_DIR}/wrapper.json"
TARGET_CHANGELOG = f"{TARGET_EVOINFRA_DIR}/CHANGELOG.md"
TARGET_FEEDBACK_POLICY = f"{TARGET_EVOINFRA_DIR}/policies/feedback-policy.json"
TARGET_AUDIT_RULE = f"{TARGET_EVOINFRA_DIR}/policies/audit-rule.md"
TARGET_NOTICE_POLICY = f"{TARGET_EVOINFRA_DIR}/policies/notice-policy.json"
TARGET_DESIGNS_DIR = f"{TARGET_EVOINFRA_DIR}/docs/designs"
TARGET_MIGRATIONS_DIR = f"{TARGET_EVOINFRA_DIR}/docs/migrations"
TARGET_PREFLIGHT_SCRIPT = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus_wrapper_preflight.py"
TARGET_NOTICE_SCRIPT = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus_notice.py"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def ask_visibility() -> str:
    while True:
        value = input("Choose GitHub repo visibility [public/private]: ").strip().lower()
        if value in {"public", "private"}:
            return value
        print("Please type public or private.")


def infer_skill_name(target: Path) -> str:
    skill = target / "SKILL.md"
    if not skill.exists():
        return target.name
    for line in skill.read_text(encoding="utf-8").splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"')
    return target.name


def render_text(text: str, replacements: dict[str, str]) -> str:
    for key, value in replacements.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def validate_repo(repo: str) -> None:
    parts = repo.split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        fail("--repo must use OWNER/REPO format")


def require_github_cli() -> None:
    if shutil.which("git") is None:
        fail("git CLI is required before bootstrapping a GitHub-backed Skill wrapper")
    if shutil.which("gh") is None:
        fail("gh CLI is required before bootstrapping a GitHub-backed Skill wrapper")
    result = subprocess.run(["gh", "auth", "status"], text=True, capture_output=True)
    if result.returncode != 0:
        fail("gh is installed but not authenticated; run gh auth login")


def resolve_current_skillware_version(target: Path, repo: str, explicit: str | None) -> str:
    if explicit:
        try:
            version_key(explicit)
        except ValueError as exc:
            fail(str(exc))
        return explicit
    release = subprocess.run(
        ["gh", "release", "view", "--repo", repo, "--json", "tagName"],
        text=True,
        capture_output=True,
    )
    if release.returncode == 0:
        try:
            tag = json.loads(release.stdout).get("tagName")
        except json.JSONDecodeError:
            tag = None
        if isinstance(tag, str) and tag.strip():
            try:
                version_key(tag.strip())
            except ValueError:
                fail(f"target GitHub latest release is not semantic: {tag.strip()}")
            return tag.strip()
    changelog_version = latest_changelog_tag(target)
    if changelog_version:
        return changelog_version
    fail(
        "target Repo has no GitHub release or versioned CHANGELOG; "
        "pass --current-version vMAJOR.MINOR.PATCH after the Owner confirms its current version"
    )


def copy_template_file(src: Path, dst: Path, replacements: dict[str, str], force: bool) -> str:
    expected = render_text(src.read_text(encoding="utf-8"), replacements).encode("utf-8")
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink() or not dst.is_file() or dst.read_bytes() != expected:
            raise ValueError(
                f"existing managed destination differs from the trusted source: {dst}; "
                "target was not modified"
            )
        return f"skip exact existing {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(expected)
    return f"write {dst}"


def target_template_path(rel: Path) -> Path:
    rel_text = rel.as_posix()
    if rel_text.startswith(".github/") or rel_text == ".codex/hooks.json":
        return rel
    if rel_text.startswith(".codex/hooks/"):
        return Path(TARGET_EVOINFRA_DIR) / "hooks" / rel.name
    if rel_text.startswith(".evozeus_evoinfra/skills/"):
        return Path(TARGET_EVOINFRA_DIR) / rel.relative_to(".evozeus_evoinfra")
    if rel_text.startswith(".evozeus_evoinfra/"):
        return Path(TARGET_EVOINFRA_DIR) / "policies" / rel.name
    if rel_text.startswith("docs/wrapper-migrations/"):
        return Path(TARGET_MIGRATIONS_DIR) / rel.relative_to("docs/wrapper-migrations")
    return Path(TARGET_EVOINFRA_DIR) / rel


def validate_template_destination(target: Path, destination: Path) -> None:
    """Reject template writes that would traverse a symlink inside the target Repo."""
    try:
        relative = destination.relative_to(target)
    except ValueError as exc:
        raise ValueError(f"template destination escapes target repository: {destination}") from exc
    cursor = target
    for index, part in enumerate(relative.parts):
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(
                "template destination contains a symlink component: "
                + str(cursor.relative_to(target))
            )
        if cursor.exists() and index < len(relative.parts) - 1 and not cursor.is_dir():
            raise ValueError(
                "template destination parent is not a directory: "
                + str(cursor.relative_to(target))
            )
        if cursor.exists() and index == len(relative.parts) - 1 and not cursor.is_file():
            raise ValueError(
                "template destination is not a regular file: "
                + str(cursor.relative_to(target))
            )


def validate_existing_manifest_for_attach(
    target: Path,
    repo: str,
    instruction_surface: str = "SKILL.md",
) -> None:
    """Allow an idempotent attach only for an already canonical wrapper manifest."""
    try:
        for relative in (
            TARGET_WRAPPER_MANIFEST,
            LEGACY_TARGET_WRAPPER_MANIFEST,
            OLDEST_TARGET_WRAPPER_MANIFEST,
        ):
            validate_template_destination(target, target / relative)
        status = wrapper_manifest_status(target)
        if not status["active_manifest_path"]:
            return
        manifest = load_wrapper_manifest(target)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(
            "existing wrapper manifest cannot be reused safely; run migrate-layout or an "
            "approved Harness repair before attach"
        ) from exc
    migration_bundle = validate_migration_contract_source()
    migration_identity = migration_bundle["identity"]
    activation_contract = migration_bundle["contract"]["canonical_activation_block"]
    expected = {
        "layout_version": 2,
        "canonical_repo": repo,
        "instruction_surface": instruction_surface,
        "harness_skill_path": TARGET_HARNESS_SKILL,
        "harness_skill_version": HARNESS_SKILL_VERSION,
        "harness_skill_managed": True,
    }
    mismatches = [
        field
        for field, value in expected.items()
        if not isinstance(manifest, dict) or manifest.get(field) != value
    ]
    managed_files = manifest.get("managed_files") if isinstance(manifest, dict) else None
    if (
        not isinstance(managed_files, list)
        or TARGET_HARNESS_SKILL not in managed_files
        or TARGET_MIGRATION_CONTRACT not in managed_files
    ):
        mismatches.append("managed_files")
    expected_contract = {
        "migration_protocol_version": migration_identity["migration_protocol_version"],
        "contract_id": migration_identity["contract_id"],
        "contract_version": migration_identity["contract_version"],
        "path": migration_identity["target_path"],
        "sha256": migration_identity["sha256"],
    }
    if not isinstance(manifest, dict) or manifest.get("migration_contract") != expected_contract:
        mismatches.append("migration_contract")
    expected_blocks = [
        {
            "block_id": activation_contract["block_id"],
            "path": instruction_surface,
            "marker_version": activation_contract["marker_version"],
            "begin_marker": activation_contract["begin_marker"],
            "end_marker": activation_contract["end_marker"],
            "sha256_lf": activation_contract["sha256_lf"],
        }
    ]
    if not isinstance(manifest, dict) or manifest.get("managed_blocks") != expected_blocks:
        mismatches.append("managed_blocks")
    if mismatches:
        raise ValueError(
            "existing wrapper manifest requires migrate-layout before attach; incompatible fields: "
            + ", ".join(mismatches)
        )


def _tree_sha256(root: Path, relative_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative_text in sorted(relative_paths):
        path = root / relative_text
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"target template source is missing or unsafe: {relative_text}")
        digest.update(relative_text.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_source_file(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"{label} must be a non-empty POSIX relative path")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != raw:
        raise ValueError(f"{label} escapes its source root: {raw}")
    candidate = root / relative
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError(f"{label} contains a symlink: {raw}")
        cursor = cursor.parent
    if not candidate.is_file():
        raise ValueError(f"{label} is missing: {raw}")
    return candidate


def validate_target_source_inventory(bundle: dict[str, object]) -> dict[str, object]:
    inventory = json.loads(TARGET_TEMPLATE_INVENTORY_SOURCE.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise ValueError("target template inventory must be a JSON object")
    governed = (inventory.get("modes") or {}).get("governed-sidecar")
    if not isinstance(governed, dict) or governed.get("target_writes") is not True:
        raise ValueError("governed target template inventory is unavailable")
    declared = governed.get("files")
    if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared):
        raise ValueError("target template inventory files must be a string list")
    actual = [
        path.relative_to(TARGET_TEMPLATE_DIR).as_posix()
        for path in TARGET_TEMPLATE_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    if sorted(declared) != sorted(actual):
        raise ValueError("target template inventory does not cover the complete source tree")
    tree_digest = f"sha256:{_tree_sha256(TARGET_TEMPLATE_DIR, declared)}"
    if governed.get("source_tree_sha256") != tree_digest:
        raise ValueError("target template inventory source tree digest mismatch")

    external_sources = governed.get("external_sources")
    if not isinstance(external_sources, list) or not external_sources:
        raise ValueError("target template inventory must bind external source files")
    for entry in external_sources:
        if not isinstance(entry, dict):
            raise ValueError("target template external source entry must be an object")
        source = _safe_source_file(ROOT, entry.get("source"), "external source")
        if entry.get("sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError(f"target template external source digest mismatch: {entry.get('source')}")

    contract_files = governed.get("contract_files")
    if not isinstance(contract_files, list) or not contract_files:
        raise ValueError("target template inventory must bind contract files")
    bundle_root = Path(bundle["bundle_root"])
    for entry in contract_files:
        if not isinstance(entry, dict):
            raise ValueError("target template contract file entry must be an object")
        source = _safe_source_file(bundle_root, entry.get("source"), "contract source")
        if entry.get("sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError(f"target template contract source digest mismatch: {entry.get('source')}")
    return governed


def validate_migration_contract_source(
    *,
    remote_tag_resolver: migration_kernel.OfficialTagResolver | None = None,
    require_trusted_release: bool = False,
) -> dict[str, object]:
    """Verify the contracts/v1 manifest binding before the first target write."""
    bundle = migration_kernel.load_migration_contract(
        ROOT,
        remote_tag_resolver=remote_tag_resolver,
    )
    if Path(bundle["path"]).resolve() != MIGRATION_CONTRACT_SOURCE.resolve():
        raise ValueError(
            "migration contract source path is not the canonical contracts/v1 artifact"
        )
    validate_target_source_inventory(bundle)
    if require_trusted_release and bundle["source_trust"]["status"] != "trusted_release":
        reasons = "; ".join(bundle["source_trust"].get("reasons", []))
        raise ValueError(
            "Harness attachment requires an immutable trusted source release"
            + (f": {reasons}" if reasons else "")
        )
    return bundle


def copy_templates(
    target: Path,
    replacements: dict[str, str],
    force: bool,
    *,
    _migration_bundle: dict[str, object] | None = None,
) -> list[str]:
    bundle = _migration_bundle or validate_migration_contract_source(
        require_trusted_release=True
    )
    if bundle["source_trust"]["status"] != "trusted_release":
        raise ValueError("Harness attachment source is not a trusted immutable release")
    governed = validate_target_source_inventory(bundle)
    template_files = [
        src
        for src in sorted(TARGET_TEMPLATE_DIR.rglob("*"))
        if not src.is_dir()
        and "__pycache__" not in src.parts
        and src.suffix not in {".pyc", ".pyo"}
    ]
    template_destinations = [
        (src, target / target_template_path(src.relative_to(TARGET_TEMPLATE_DIR)))
        for src in template_files
    ]
    expected_artifacts: list[tuple[Path, bytes, bool]] = [
        (
            destination,
            render_text(source.read_text(encoding="utf-8"), replacements).encode("utf-8"),
            False,
        )
        for source, destination in template_destinations
    ]
    for entry in governed["external_sources"]:
        source = _safe_source_file(ROOT, entry["source"], "external source")
        expected_artifacts.append(
            (target / entry["target"], source.read_bytes(), entry.get("executable") is True)
        )
    bundle_root = Path(bundle["bundle_root"])
    for entry in governed["contract_files"]:
        source = _safe_source_file(bundle_root, entry["source"], "contract source")
        expected_artifacts.append((target / entry["target"], source.read_bytes(), False))
    for destination, expected, _ in expected_artifacts:
        validate_template_destination(target, destination)
        if destination.exists() or destination.is_symlink():
            if (
                destination.is_symlink()
                or not destination.is_file()
                or destination.read_bytes() != expected
            ):
                relative = destination.relative_to(target)
                raise ValueError(
                    "existing managed destination has no exact trusted preimage: "
                    f"{relative}; --force cannot authorize replacement; target was not modified"
                )

    actions: list[str] = []
    for destination, expected, executable in expected_artifacts:
        if destination.is_file():
            actions.append(f"skip exact existing {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(expected)
        if executable:
            destination.chmod(0o755)
        actions.append(f"write {destination}")
    return actions


def local_project_dir(repo: str) -> Path:
    owner, name = repo.split("/")
    return LOCAL_PROJECTS_DIR / owner / name


def ensure_project_pointer(target: Path, repo: str, force: bool) -> list[str]:
    project_dir = local_project_dir(repo)
    actions: list[str] = []

    if project_dir.is_symlink():
        if project_dir.resolve() == target:
            actions.append(f"skip existing pointer {project_dir}")
        elif force:
            project_dir.unlink()
            project_dir.symlink_to(target, target_is_directory=True)
            actions.append(f"update pointer {project_dir} -> {target}")
        else:
            actions.append(f"skip existing pointer {project_dir}; points to {project_dir.resolve()}")
        return actions

    if project_dir.exists():
        actions.append(f"skip existing non-symlink {project_dir}; choose canonical repo before replacing")
        return actions

    project_dir.parent.mkdir(parents=True, exist_ok=True)
    project_dir.symlink_to(target, target_is_directory=True)
    actions.append(f"write pointer {project_dir} -> {target}")

    return actions


def build_evolution_section(replacements: dict[str, str]) -> str:
    return f"""\
{EVOLUTION_SECTION_HEADING}

本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环。后续任何行为改动都必须先留下可追踪证据，再进入实现。

源头发现顺序：

1. 先读取本 repo 的 `{TARGET_WRAPPER_MANIFEST}`，以 `canonical_repo` 作为目标 repo。
2. 再检查 `~/.evozeus/.projects/{replacements["REPO_NAME"]}` 是否存在并指向 canonical repo。
3. 验证 canonical repo 的 git origin / GitHub repo 可访问。
4. 再检查 runtime install：`~/.codex/skills/<skill-name>`、`~/.agents/skills/<skill-name>`；它们只能是指向 canonical repo 的安装指针。
5. 只有 wrapper manifest 和 project pointer 都无法确认时，才进入 GitHub user/org/public search。

进化流程：

1. 使用中出现不满意、纠错、漏检或可复用机制缺陷时，先完成当前业务纠正，再运行 feedback audit，并通过 `--context` 提供一句脱敏 Lesson 摘要；原样显示返回的 `user_notice.display_text`，询问是否只记录到 Skill Feedback Issue。只有明确确认后才创建 Issue，修复继续要求单独授权。
2. 每次运行本 Skill 前，先执行 `python3 {TARGET_PREFLIGHT_SCRIPT} doctor --repo {replacements["REPO_NAME"]}`，确认 wrapper source contract 成立。
3. 再执行 `python3 {TARGET_PREFLIGHT_SCRIPT} version --repo {replacements["REPO_NAME"]}`，确认 GitHub latest release 没有新版本。
4. 开始修改前，在 `{TARGET_DESIGNS_DIR}/` 新建设计文档，明确 Related issue、优化目标、实现计划、验证计划和 release plan。
5. PR 必须同步更新 `SKILL.md` 与 `{TARGET_CHANGELOG}`，并通过 `python3 {TARGET_PREFLIGHT_SCRIPT} structure` 和 PR 检查。
6. 合并后用 `vMAJOR.MINOR.PATCH` release tag 和 release notes 固化本次进化，保留可回滚记录。

边界：不要把 raw private session、客户资料、secret、未脱敏商业上下文写入公开 Issue、docs 或 release notes；`~/.evozeus/.projects/{replacements["REPO_NAME"]}/` 应指向 canonical repo，runtime-only install 只能是指针，不能作为 copied install 或第二事实源直接修改。

Target repo: `{replacements["REPO_NAME"]}`
Visibility: `{replacements["VISIBILITY"]}`
Current Skill version: `{replacements["CURRENT_VERSION"]}`
Wrapper harness version: `{replacements["WRAPPER_VERSION"]}`
"""


def build_wrapper_section(replacements: dict[str, str]) -> str:
    return f"""\
{WRAPPER_SECTION_HEADING}

本区由 EvoZeus-CoEvolve 追加，用来说明本 Skill 的 wrapper harness 路由、版本记录和迁移规则。它不覆盖原 Skill 的业务规则；涉及业务行为变化时，仍必须走 Issue、design doc、PR、CHANGELOG 和 release。

调用 wrapper 的场景：

1. 本 Skillware Repo 需要 attach/adopt/repair Harness，或确认 canonical source。
2. `{TARGET_WRAPPER_MANIFEST}` 中的 wrapper harness version 落后于 EvoZeus-CoEvolve 最新版本。
3. `~/.evozeus/.projects/{replacements["REPO_NAME"]}`、`.codex` 或 `.agents` runtime install 疑似不是同一个 source of truth。
4. 使用反馈先进入当前 invocation 的本地待确认状态；用户明确确认后才提交 Skill Feedback Issue；另获修复授权后才能进入 design doc、PR、CHANGELOG、release 的自进化闭环。
5. 目标 GitHub repo、release tag、GitHub Pages 或 preflight check 需要创建、诊断或修复。

路由规则：

- 目标 Skill 行为问题：先捕获为本地待确认信号；用户确认提交后才创建 Skill Feedback Issue，修复和 PR 继续要求单独授权。
- 源头/安装问题：先运行 `python3 {TARGET_PREFLIGHT_SCRIPT} doctor --repo {replacements["REPO_NAME"]}`。
- 结构问题：运行 `python3 {TARGET_PREFLIGHT_SCRIPT} structure`。
- Skill release 问题：运行 `python3 {TARGET_PREFLIGHT_SCRIPT} version --repo {replacements["REPO_NAME"]}`。
- wrapper harness 升级：回到 EvoZeus-CoEvolve repo，运行 `python3 scripts/evozeus_wrapper.py harness upgrade-check --target <this-skill-repo> --json`，再用检查结果中的最新版本运行 `harness upgrade --dry-run` 生成迁移方案。

Append-only 迁移规则：

- wrapper 升级必须保留 frontmatter 后的状态检查；其他 `SKILL.md` wrapper 内容只能追加本区缺失内容或 migration note，不要重写原 Skill 业务段落。
- 如果本区已存在，升级时追加 migration note，不改写旧文本。
- 每次 wrapper 升级必须记录 from/to wrapper version、planned files、验证命令、回滚方案和是否需要人工 merge review。
- wrapper version 事实源是 `{TARGET_WRAPPER_MANIFEST}` 的 `wrapper_version`；Skill release 仍以 GitHub release / `{TARGET_CHANGELOG}` 为准。

Wrapper harness version: `{replacements["WRAPPER_VERSION"]}`
Wrapper manifest: `{TARGET_WRAPPER_MANIFEST}`
Feedback audit policy: `{TARGET_FEEDBACK_POLICY}`
Feedback audit rule: `{TARGET_AUDIT_RULE}`
Notice policy: `{TARGET_NOTICE_POLICY}`
Notice CLI: `{TARGET_NOTICE_SCRIPT}`
Wrapper migration log: `{TARGET_MIGRATIONS_DIR}/`

Runtime integration modes:

- `repo_maintenance_hook`：project-local `SessionStart` hook，仅覆盖 canonical repository 维护。
- `global_session_dispatcher`：user-level `SessionStart` 聚合检查全部 wrapped Skills，不是 per-Skill invocation hook。
- `bootstrap_skill`：Plugin lifecycle 可以稳定加载控制 Skill，但当前没有 `SkillInvoke` 事件。
- `prompt_runtime_check`：Skill 入口 preflight，基本绑定被选中的 Skill，但依赖 prompt compliance。
- `manual_only`：只能手动运行 wrapper 命令。
"""


def inject_evolution_method(
    target: Path,
    replacements: dict[str, str],
    instruction_surface: str = "SKILL.md",
) -> list[str]:
    changed = add_fresh_harness_entry(target, instruction_surface)
    action = "write" if changed else "keep"
    return [f"{action} canonical Harness Skill activation block in {target / instruction_surface}"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach EvoZeus-CoEvolve to an independent Git repository root.")
    parser.add_argument("target", help="Path to the independent target Git repository root.")
    parser.add_argument("--skill-name", help="Display name for the Skill.")
    parser.add_argument("--repo", required=True, help="Target GitHub repo in OWNER/REPO format.")
    parser.add_argument("--visibility", choices=["public", "private"], help="GitHub repo visibility.")
    parser.add_argument(
        "--current-version",
        help="Owner-confirmed current Skillware version when the target Repo has no release or versioned CHANGELOG.",
    )
    parser.add_argument(
        "--init-command",
        help="Target Skill-owned initialization command; requires --init-verification.",
    )
    parser.add_argument(
        "--init-verification",
        help="Target Skill-owned initialization verification; requires --init-command.",
    )
    parser.add_argument(
        "--generates-child-skills",
        action="store_true",
        help="Declare generated child Skills; they inherit this Repo Harness unless published as independent Repos.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing wrapper files.")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        fail(f"target folder does not exist: {target}")
    if not (target / "SKILL.md").exists():
        fail(f"target folder must contain SKILL.md: {target}")
    try:
        repo_root = independent_repo_root(target)
    except ValueError as exc:
        fail(str(exc))
    if repo_root != target:
        fail(
            "Harness can only be attached at the independent Git repository root: "
            f"requested={target}; repo_root={repo_root}"
        )
    try:
        validate_instruction_surface_for_harness_entry(target, "SKILL.md")
    except ValueError as exc:
        fail(str(exc))
    validate_repo(args.repo)
    try:
        validate_existing_manifest_for_attach(target, args.repo)
    except ValueError as exc:
        fail(str(exc))
    if not TARGET_TEMPLATE_DIR.exists():
        fail(f"template folder missing: {TARGET_TEMPLATE_DIR}")
    if not PREFLIGHT_SCRIPT.exists():
        fail(f"preflight script missing: {PREFLIGHT_SCRIPT}")
    if not NOTICE_SCRIPT.exists():
        fail(f"notice script missing: {NOTICE_SCRIPT}")
    require_github_cli()
    try:
        authority = require_repo_admin(target, args.repo)
    except ValueError as exc:
        fail(str(exc))

    detected_visibility = str(authority.get("visibility") or "").lower()
    visibility = args.visibility or detected_visibility or ask_visibility()
    if detected_visibility and visibility != detected_visibility:
        fail(
            "requested visibility does not match the existing GitHub repository: "
            f"requested={visibility}; actual={detected_visibility}"
        )
    skill_name = args.skill_name or infer_skill_name(target)
    current_version = resolve_current_skillware_version(target, args.repo, args.current_version)
    try:
        onboarding = build_onboarding_contract(
            repo=args.repo,
            skill_name=skill_name,
            init_command=args.init_command,
            init_verification=args.init_verification,
            generates_child_skills=args.generates_child_skills,
        )
    except ValueError as exc:
        fail(str(exc))
    replacements = {
        "DATE": date.today().isoformat(),
        "INITIAL_VERSION": current_version,
        "CURRENT_VERSION": current_version,
        "REPO_NAME": args.repo,
        "REPO_URL": f"https://github.com/{args.repo}",
        "SKILL_NAME": skill_name,
        "VISIBILITY": visibility,
        "WRAPPER_VERSION": WRAPPER_VERSION,
    }

    actions = [
        f"verified independent Git repository root: {target}",
        f"verified GitHub ADMIN authority: {authority['repository']}",
    ]
    try:
        actions.extend(copy_templates(target, replacements, args.force))
    except ValueError as exc:
        fail(str(exc))
    actions.extend(ensure_project_pointer(target, args.repo, args.force))
    actions.extend(inject_evolution_method(target, replacements, instruction_surface="SKILL.md"))
    actions.append(
        write_wrapper_manifest(
            target,
            build_wrapper_manifest(
                args.repo,
                WRAPPER_VERSION,
                WRAPPER_MANAGED_FILES,
                [],
                instruction_surface="SKILL.md",
                onboarding=onboarding,
            ),
            args.force,
        )
    )
    print("EvoZeus-CoEvolve repository Harness attachment complete.")
    print(f"Target: {target}")
    print(f"Repo: {args.repo}")
    print(f"Visibility: {visibility}")
    print(f"Preserved Skillware version: {current_version}")
    print(f"EvoZeus project pointer: {local_project_dir(args.repo)}")
    for action in actions:
        print(f"- {action}")

    print("\nNext commands from the target folder:")
    print(f"python3 {TARGET_PREFLIGHT_SCRIPT} doctor --repo {args.repo}")
    print(f"python3 {TARGET_PREFLIGHT_SCRIPT} structure")
    print("git add .")
    print('git commit -m "Attach EvoZeus CoEvolve Harness"')
    print("git push")
    print("Preserve the target Repo release version; attaching a Harness does not create or reset a Skillware release.")
    if visibility == "public":
        print(f"gh api --method POST repos/{args.repo}/pages -f build_type=workflow")
        print(f"gh variable set EVOZEUS_PAGES_ENABLED --body true --repo {args.repo}")
    else:
        print("Private repo: keep Pages in repository-only mode unless plan support is confirmed.")
    print(f"gh workflow run evozeus-wrapper-preflight.yml --repo {args.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
