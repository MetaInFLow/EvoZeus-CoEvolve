#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evozeus_wrapper_lifecycle import (
    REQUIRED_WRAPPER_FILES,
    TARGET_HARNESS_SKILL,
    apply_reinstall,
    detect_target_architecture,
    diagnose_environment,
    diagnose_skill,
    plan_reinstall,
    plan_harness_upgrade,
    migrate_target_layout,
    rollback_target_layout_migration,
    plan_target_layout_migration,
    plan_feedback_audit,
    classify_pr_permission,
    require_repo_admin,
    resolve_harness_target,
    run_command,
    stage_label,
)
from evozeus_wrapper_global_hook import (
    apply_upgrade_all,
    apply_global_hook_install,
    apply_global_hook_uninstall,
    plan_upgrade_all,
    plan_global_hook_install,
    read_global_hook_status,
    record_global_hook_trust,
)


def print_report(report: dict, as_json: bool, stage: str) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(stage_label(stage))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def repository_target(path: str) -> tuple[Path, dict] | tuple[None, dict]:
    try:
        boundary = resolve_harness_target(Path(path))
    except ValueError as exc:
        return None, {
            "stage": "repository_boundary",
            "status": "blocked",
            "writes": False,
            "errors": [str(exc)],
        }
    if not boundary["eligible"]:
        return None, {
            "stage": "repository_boundary",
            "status": "blocked",
            "writes": False,
            "repository_boundary": boundary,
            "errors": [
                "nested Evolution Harness detected; move or migrate it to the independent Git repository root"
            ],
        }
    return Path(boundary["repo_root"]), boundary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run staged EvoZeus-CoEvolve lifecycle commands.")
    sub = parser.add_subparsers(dest="group", required=True)

    env = sub.add_parser("env", help="Environment lifecycle commands.")
    env_sub = env.add_subparsers(dest="command", required=True)
    env_diag = env_sub.add_parser("diagnose", help="Diagnose EvoZeus environment readiness.")
    env_diag.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")

    skill = sub.add_parser("skill", help="Target Skill lifecycle commands.")
    skill_sub = skill.add_subparsers(dest="command", required=True)
    skill_diag = skill_sub.add_parser("diagnose", help="Diagnose target Skill state.")
    skill_diag.add_argument("--target", required=True, help="Path to target Skill folder.")
    skill_diag.add_argument("--repo", help="GitHub repo in OWNER/REPO format.")
    skill_diag.add_argument("--skill-name", help="Override Skill name.")
    skill_diag.add_argument("--workspace-root", action="append", default=[], help="Additional local workspace root to inspect.")
    skill_diag.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    skill_transform = skill_sub.add_parser("transform", help="Plan or verify target Skill transform.")
    skill_transform.add_argument(
        "--mode",
        required=True,
        choices=["attach", "adopt", "bootstrap", "repair", "verify"],
        help="attach is the preferred first Harness plan; adopt/bootstrap remain compatibility aliases.",
    )
    skill_transform.add_argument("--target", required=True, help="Path to target Skill folder.")
    skill_transform.add_argument("--repo", help="GitHub repo in OWNER/REPO format.")
    skill_transform.add_argument("--visibility", choices=["public", "private"], help="Target repo visibility.")
    skill_transform.add_argument(
        "--instruction-surface",
        help="Instruction surface selected by skills/evolution-surface-diagnosis/SKILL.md.",
    )
    skill_transform.add_argument("--dry-run", action="store_true", help="Print planned transform without writing.")
    skill_transform.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")

    publish = sub.add_parser("publish", help="Publish and reinstall lifecycle commands.")
    publish_sub = publish.add_subparsers(dest="command", required=True)
    reinstall = publish_sub.add_parser("reinstall", help="Plan runtime symlink reinstall.")
    reinstall.add_argument("--skill-name", required=True)
    reinstall.add_argument("--canonical-path", required=True)
    reinstall.add_argument("--target", action="append", required=True, help="codex, agents, all, or an explicit install path.")
    reinstall.add_argument("--dry-run", action="store_true", help="Only print planned reinstall actions.")
    reinstall.add_argument(
        "--approve-archive",
        action="store_true",
        help="Archive real directory installs before replacing them with canonical symlinks.",
    )
    reinstall.add_argument(
        "--archive-root",
        help="Override the default ~/.evozeus/archives/runtime-installs archive root.",
    )
    reinstall.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")

    hook = sub.add_parser("hook", help="Host hook lifecycle commands.")
    hook_scope = hook.add_subparsers(dest="scope", required=True)
    global_hook = hook_scope.add_parser("global", help="Manage the user-level EvoZeus dispatcher.")
    global_hook_sub = global_hook.add_subparsers(dest="command", required=True)
    global_plan = global_hook_sub.add_parser("plan", help="Plan global dispatcher installation.")
    global_plan.add_argument("--json", action="store_true")
    global_install = global_hook_sub.add_parser("install", help="Install the global dispatcher.")
    global_install.add_argument("--approve", action="store_true")
    global_install.add_argument("--json", action="store_true")
    global_status = global_hook_sub.add_parser("status", help="Report global dispatcher state.")
    global_status.add_argument("--json", action="store_true")
    global_trust = global_hook_sub.add_parser("trust", help="Record the result of Codex hook review.")
    global_trust.add_argument(
        "--status",
        required=True,
        choices=["pending_review", "trusted", "rejected"],
    )
    global_trust.add_argument("--approve", action="store_true")
    global_trust.add_argument("--json", action="store_true")
    global_uninstall = global_hook_sub.add_parser("uninstall", help="Uninstall the global dispatcher.")
    global_uninstall.add_argument("--approve", action="store_true")
    global_uninstall.add_argument("--json", action="store_true")

    loop = sub.add_parser("loop", help="Continuous evolution loop commands.")
    loop_sub = loop.add_subparsers(dest="command", required=True)
    lesson = loop_sub.add_parser("lesson", help="Plan lesson candidate intake.")
    lesson.add_argument("--dry-run", action="store_true", help="Only print next action.")
    lesson.add_argument("--json", action="store_true")
    audit = loop_sub.add_parser("audit", help="Audit user feedback and plan feedback Issue capture.")
    audit.add_argument("--target", required=True)
    audit.add_argument("--user-input", required=True)
    audit.add_argument("--context", help="Optional redacted context summary.")
    audit.add_argument("--json", action="store_true")
    issue_to_pr = loop_sub.add_parser("issue-to-pr", help="Plan Issue-to-PR flow.")
    issue_to_pr.add_argument("--write-permission", action="store_true")
    issue_to_pr.add_argument("--fork-permission", action="store_true")
    issue_to_pr.add_argument("--dry-run", action="store_true", help="Only print next action.")
    issue_to_pr.add_argument("--json", action="store_true")

    harness = sub.add_parser("harness", help="Wrapper harness maintenance commands.")
    harness_sub = harness.add_subparsers(dest="command", required=True)
    upgrade_check = harness_sub.add_parser("upgrade-check", help="Check target wrapper harness version.")
    upgrade_check.add_argument("--target", required=True)
    upgrade_check.add_argument("--latest-version", help="Explicit latest wrapper version override, such as v0.15.0.")
    upgrade_check.add_argument("--managed-dirty", action="store_true")
    upgrade_check.add_argument("--json", action="store_true")
    upgrade = harness_sub.add_parser("upgrade", help="Plan wrapper harness upgrade.")
    upgrade.add_argument("--target", required=True)
    upgrade.add_argument("--latest-version", required=True)
    upgrade.add_argument("--managed-dirty", action="store_true")
    upgrade.add_argument("--dry-run", action="store_true")
    upgrade.add_argument(
        "--approve-plan",
        help="Approve one exact sha256:<digest> migration plan for this target.",
    )
    upgrade.add_argument("--json", action="store_true")
    migrate_layout = harness_sub.add_parser(
        "migrate-layout",
        help="Plan or apply the one-time scattered-v1 to consolidated-v2 target layout migration.",
    )
    migrate_layout.add_argument("--target", required=True)
    migrate_layout.add_argument("--latest-version", required=True)
    migrate_layout.add_argument("--dry-run", action="store_true")
    migrate_layout.add_argument(
        "--approve-plan",
        help="Approve one exact sha256:<digest> migration plan for this target.",
    )
    migrate_layout.add_argument("--json", action="store_true")
    rollback_migration = harness_sub.add_parser(
        "rollback-migration",
        help="Restore a complete Harness migration snapshot and verify every preimage.",
    )
    rollback_migration.add_argument("--target", required=True)
    rollback_migration.add_argument("--snapshot", required=True)
    rollback_migration.add_argument(
        "--approve",
        action="store_true",
        help="Explicitly approve restoration from the validated snapshot.",
    )
    rollback_migration.add_argument("--json", action="store_true")
    upgrade_all = harness_sub.add_parser(
        "upgrade-all",
        help="Plan or apply upgrades for every outdated registered wrapped harness.",
    )
    upgrade_all.add_argument("--latest-version", required=True)
    upgrade_all.add_argument(
        "--wrapper-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Canonical EvoZeus-CoEvolve source path.",
    )
    upgrade_all.add_argument("--dry-run", action="store_true")
    upgrade_all.add_argument("--approve", action="store_true")
    upgrade_all.add_argument(
        "--approve-plan",
        help="Approve one exact sha256:<digest> batch plan in addition to --approve.",
    )
    upgrade_all.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.group == "env" and args.command == "diagnose":
        print_report(diagnose_environment(Path.home()), args.json, "environment")
        return 0
    if args.group == "skill" and args.command == "diagnose":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "target_skill")
            return 1
        report = diagnose_skill(
            target=target,
            repo=args.repo,
            skill_name=args.skill_name,
            workspace_roots=[Path(path) for path in args.workspace_root],
        )
        report["repository_boundary"] = boundary
        print_report(report, args.json, "target_skill")
        return 0
    if args.group == "skill" and args.command == "transform":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "transform")
            return 1
        if args.mode == "verify":
            preflight = Path(__file__).resolve().parent / "evozeus_wrapper_preflight.py"
            result = run_command([sys.executable, str(preflight), "structure", "--target", str(target)])
            if args.json:
                print(
                    json.dumps(
                        {
                            "stage": "target_skill_transform",
                            "mode": "verify",
                            "target": str(target),
                            "repository_boundary": boundary,
                            "returncode": result["returncode"],
                            "stdout": result["stdout"],
                            "stderr": result["stderr"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(stage_label("transform"))
                print(result["stdout"], end="")
                print(result["stderr"], end="", file=sys.stderr)
            return int(result["returncode"])

        if not args.dry_run:
            print("write operations are only implemented through dry-run planning for this mode", file=sys.stderr)
            return 1
        architecture = detect_target_architecture(target)
        instruction_surface = args.instruction_surface or architecture["root_entry"]
        requires_surface_diagnosis = not args.instruction_surface and architecture["target_kind"] == "hooked_skill_bundle"
        surface_planned_files = []
        if instruction_surface:
            surface_planned_files = [
                f"{instruction_surface} canonical Harness Skill activation block",
                TARGET_HARNESS_SKILL,
            ]
        planned_files = list(dict.fromkeys(REQUIRED_WRAPPER_FILES + surface_planned_files))
        report = {
            "stage": "target_skill_transform",
            "mode": args.mode,
            "target": str(target),
            "repository_boundary": boundary,
            "repo": args.repo,
            "visibility": args.visibility,
            "writes": False,
            "target_kind": architecture["target_kind"],
            "requires_surface_diagnosis": requires_surface_diagnosis,
            "instruction_surface": instruction_surface,
            "instruction_surface_source": "diagnosis_skill" if args.instruction_surface else "root_entry_fallback",
            "integration": architecture["integration"],
            "evolution_surface": architecture["evolution_surface"],
            "planned_files": planned_files,
            "version_rule": (
                "the independent target repository must already exist; attach/adopt/repair preserve its "
                "GitHub latest release or owner-confirmed CHANGELOG version"
            ),
        }
        print_report(report, args.json, "transform")
        return 0
    if args.group == "publish" and args.command == "reinstall":
        if args.dry_run:
            report = plan_reinstall(args.skill_name, Path(args.canonical_path), Path.home(), args.target)
        else:
            try:
                report = apply_reinstall(
                    args.skill_name,
                    Path(args.canonical_path),
                    Path.home(),
                    args.target,
                    approve_archive=args.approve_archive,
                    archive_root=Path(args.archive_root) if args.archive_root else None,
                )
            except (OSError, ValueError) as exc:
                report = {
                    "stage": "publish_reinstall",
                    "status": "error",
                    "writes": False,
                    "errors": [str(exc)],
                }
        print_report(report, args.json, "publish")
        return 0 if report.get("status") in {"planned", "applied"} else 1
    if args.group == "hook" and args.scope == "global":
        wrapper_root = Path(__file__).resolve().parents[1]
        if args.command == "plan":
            report = plan_global_hook_install(Path.home(), wrapper_root)
        elif args.command == "install":
            report = apply_global_hook_install(Path.home(), wrapper_root, approve=args.approve)
        elif args.command == "status":
            report = read_global_hook_status(Path.home())
        elif args.command == "trust":
            report = record_global_hook_trust(
                Path.home(),
                status=args.status,
                approve=args.approve,
            )
        else:
            report = apply_global_hook_uninstall(Path.home(), approve=args.approve)
        print_report(report, args.json, "publish")
        return 0 if report.get("status") not in {"blocked", "approval_required"} else 1
    if args.group == "loop" and args.command == "lesson":
        if not args.dry_run:
            print("lesson submission requires explicit confirmation and is not implemented in this command yet", file=sys.stderr)
            return 1
        report = {
            "stage": "continuous_evolution_loop",
            "flow": "lesson_intake",
            "writes": False,
            "next_action": "ask_user_whether_to_submit_lesson",
        }
        print_report(report, args.json, "loop")
        return 0
    if args.group == "loop" and args.command == "audit":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "loop")
            return 1
        try:
            report = plan_feedback_audit(
                target=target,
                user_input=args.user_input,
                context=args.context,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        report["repository_boundary"] = boundary
        print_report(report, args.json, "loop")
        return 0
    if args.group == "loop" and args.command == "issue-to-pr":
        if not args.dry_run:
            print("Issue-to-PR writes require explicit confirmation and are not implemented yet", file=sys.stderr)
            return 1
        report = {
            "stage": "continuous_evolution_loop",
            "flow": "issue_to_pr",
            "writes": False,
            "permission_mode": classify_pr_permission(args.write_permission, args.fork_permission),
        }
        print_report(report, args.json, "loop")
        return 0
    if args.group == "harness" and args.command == "upgrade-check":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "loop")
            return 1
        report = plan_harness_upgrade(
            target=target,
            latest_version=args.latest_version,
            managed_dirty=args.managed_dirty,
        )
        report["repository_boundary"] = boundary
        print_report(report, args.json, "loop")
        return 0
    if args.group == "harness" and args.command == "upgrade":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "loop")
            return 1
        if args.dry_run:
            report = plan_harness_upgrade(
                target=target,
                latest_version=args.latest_version,
                managed_dirty=args.managed_dirty,
            )
        else:
            try:
                authority = require_repo_admin(target)
                report = migrate_target_layout(
                    target=target,
                    latest_version=args.latest_version,
                    approved_plan_sha256=args.approve_plan,
                )
                report["administrator_authority"] = authority
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        report["repository_boundary"] = boundary
        print_report(report, args.json, "loop")
        return 1 if report.get("status") in {
            "approval_required",
            "blocked",
            "manual_migration_required",
        } else 0
    if args.group == "harness" and args.command == "migrate-layout":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "loop")
            return 1
        try:
            if args.dry_run:
                report = plan_target_layout_migration(
                    target=target,
                    latest_version=args.latest_version,
                )
            else:
                authority = require_repo_admin(target)
                report = migrate_target_layout(
                    target=target,
                    latest_version=args.latest_version,
                    approved_plan_sha256=args.approve_plan,
                )
                report["administrator_authority"] = authority
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        report["repository_boundary"] = boundary
        print_report(report, args.json, "loop")
        return 1 if report.get("status") in {
            "approval_required",
            "blocked",
            "manual_migration_required",
        } else 0
    if args.group == "harness" and args.command == "rollback-migration":
        target, boundary = repository_target(args.target)
        if target is None:
            print_report(boundary, args.json, "loop")
            return 1
        if not args.approve:
            report = {
                "stage": "harness_migration_rollback",
                "status": "approval_required",
                "writes": False,
                "target": str(target),
                "snapshot": str(Path(args.snapshot).expanduser()),
                "repository_boundary": boundary,
            }
            print_report(report, args.json, "loop")
            return 1
        try:
            authority = require_repo_admin(target)
            report = rollback_target_layout_migration(
                target,
                Path(args.snapshot),
            )
            report["administrator_authority"] = authority
            report["repository_boundary"] = boundary
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print_report(report, args.json, "loop")
        return 0
    if args.group == "harness" and args.command == "upgrade-all":
        if args.dry_run:
            report = plan_upgrade_all(
                Path.home(),
                Path(args.wrapper_root),
                args.latest_version,
            )
        else:
            report = apply_upgrade_all(
                Path.home(),
                Path(args.wrapper_root),
                args.latest_version,
                approve=args.approve,
                approved_plan_sha256=args.approve_plan,
            )
        print_report(report, args.json, "loop")
        return 0 if report.get("status") not in {
            "blocked",
            "approval_required",
            "rolled_back",
            "rollback_failed",
        } else 1

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
