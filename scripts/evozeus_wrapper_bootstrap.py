#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    from .evozeus_branch_consumer import ConsumerError as BranchConsumerError
    from .evozeus_branch_consumer import verify_managed_snapshot
    from .evozeus_wrapper_lifecycle import (
        TARGET_HARNESS_SKILL,
        WRAPPER_MANAGED_FILES,
        build_onboarding_contract,
        build_status_section,
        build_wrapper_manifest,
        canonical_harness_skill_text_valid,
        independent_repo_root,
        latest_changelog_tag,
        migrate_instruction_surface_to_harness_entry,
        require_repo_admin,
        version_key,
        write_wrapper_manifest,
    )
except ImportError:
    from evozeus_branch_consumer import ConsumerError as BranchConsumerError
    from evozeus_branch_consumer import verify_managed_snapshot
    from evozeus_wrapper_lifecycle import (
        TARGET_HARNESS_SKILL,
        WRAPPER_MANAGED_FILES,
        build_onboarding_contract,
        build_status_section,
        build_wrapper_manifest,
        canonical_harness_skill_text_valid,
        independent_repo_root,
        latest_changelog_tag,
        migrate_instruction_surface_to_harness_entry,
        require_repo_admin,
        version_key,
        write_wrapper_manifest,
    )


ROOT = Path(__file__).resolve().parents[1]
TARGET_TEMPLATE_DIR = ROOT / "templates" / "target"
PREFLIGHT_SCRIPT = ROOT / "scripts" / "evozeus_wrapper_preflight.py"
NOTICE_SCRIPT = ROOT / "scripts" / "evozeus_notice.py"
BRANCH_CONSUMER_SCRIPT = ROOT / "scripts" / "evozeus_branch_consumer.py"
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
TARGET_BRANCH_CONSUMER_SCRIPT = f"{TARGET_EVOINFRA_DIR}/scripts/evozeus_branch_consumer.py"
EXACT_SNAPSHOT_TEMPLATE_PATHS = {
    Path("contracts/v1/contributor-branch-contract.json"),
    Path("contracts/v1/contributor-branch-provenance.json"),
    Path("scripts/evozeus-branch-preflight.mjs"),
}


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
    if dst.exists() and not force:
        return f"skip existing {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_text(encoding="utf-8")
    dst.write_text(render_text(data, replacements), encoding="utf-8")
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


def checked_target_write_path(target: Path, relative_path: Path) -> Path:
    target = target.expanduser().resolve()
    if relative_path.is_absolute() or ".." in relative_path.parts:
        fail(f"wrapper target path escapes the repository: {relative_path}")
    destination = target / relative_path
    cursor = destination
    while cursor != target:
        if cursor.is_symlink():
            fail(f"wrapper target path cannot contain symlinks: {cursor.relative_to(target)}")
        cursor = cursor.parent
    return destination


def copy_templates(target: Path, replacements: dict[str, str], force: bool) -> list[str]:
    existing_harness = target / TARGET_HARNESS_SKILL
    if (existing_harness.exists() or existing_harness.is_symlink()) and not force:
        if existing_harness.is_symlink() or not existing_harness.is_file():
            raise ValueError(
                f"existing canonical Harness Skill path is unsafe: {existing_harness}; "
                "use an approved repair with --force"
            )
        try:
            existing_text = existing_harness.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(
                f"existing canonical Harness Skill cannot be verified: {existing_harness}; "
                "use an approved repair with --force"
            ) from exc
        if not canonical_harness_skill_text_valid(existing_text):
            raise ValueError(
                f"existing canonical Harness Skill is incompatible: {existing_harness}; "
                "preserve it and use an approved repair with --force"
            )

    actions: list[str] = []
    for src in sorted(TARGET_TEMPLATE_DIR.rglob("*")):
        if src.is_dir() or "__pycache__" in src.parts or src.suffix in {".pyc", ".pyo"}:
            continue
        rel = src.relative_to(TARGET_TEMPLATE_DIR)
        destination = checked_target_write_path(target, target_template_path(rel))
        if rel in EXACT_SNAPSHOT_TEMPLATE_PATHS:
            if destination.exists() and not force:
                actions.append(f"skip existing {destination}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, destination)
                actions.append(f"write {destination}")
        else:
            actions.append(copy_template_file(src, destination, replacements, force))

    script_dst = checked_target_write_path(target, Path(TARGET_PREFLIGHT_SCRIPT))
    if script_dst.exists() and not force:
        actions.append(f"skip existing {script_dst}")
    else:
        script_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PREFLIGHT_SCRIPT, script_dst)
        script_dst.chmod(0o755)
        actions.append(f"write {script_dst}")
    notice_dst = checked_target_write_path(target, Path(TARGET_NOTICE_SCRIPT))
    if notice_dst.exists() and not force:
        actions.append(f"skip existing {notice_dst}")
    else:
        notice_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(NOTICE_SCRIPT, notice_dst)
        notice_dst.chmod(0o755)
        actions.append(f"write {notice_dst}")
    consumer_dst = checked_target_write_path(target, Path(TARGET_BRANCH_CONSUMER_SCRIPT))
    if consumer_dst.exists() and not force:
        actions.append(f"skip existing {consumer_dst}")
    else:
        consumer_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BRANCH_CONSUMER_SCRIPT, consumer_dst)
        consumer_dst.chmod(0o755)
        actions.append(f"write {consumer_dst}")
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
    changed = migrate_instruction_surface_to_harness_entry(target, instruction_surface)
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
    validate_repo(args.repo)
    if not TARGET_TEMPLATE_DIR.exists():
        fail(f"template folder missing: {TARGET_TEMPLATE_DIR}")
    if not PREFLIGHT_SCRIPT.exists():
        fail(f"preflight script missing: {PREFLIGHT_SCRIPT}")
    if not NOTICE_SCRIPT.exists():
        fail(f"notice script missing: {NOTICE_SCRIPT}")
    if not BRANCH_CONSUMER_SCRIPT.exists():
        fail(f"contributor branch consumer missing: {BRANCH_CONSUMER_SCRIPT}")
    try:
        verify_managed_snapshot(TARGET_TEMPLATE_DIR)
    except BranchConsumerError as exc:
        fail(f"contributor branch snapshot is invalid: {exc}")
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
