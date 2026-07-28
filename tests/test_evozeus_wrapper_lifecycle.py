import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.evozeus_wrapper_bootstrap import (
    build_evolution_section,
    build_status_section,
    build_wrapper_section,
    copy_templates,
    inject_evolution_method,
)
from scripts.evozeus_wrapper_lifecycle import (
    CODEX_START_HOOK_SCRIPT,
    LEGACY_TARGET_WRAPPER_MANIFEST,
    TARGET_CHANGELOG,
    TARGET_FEEDBACK_POLICY,
    TARGET_MIGRATIONS_README,
    TARGET_ONBOARDING_GUIDE,
    TARGET_PREFLIGHT_SCRIPT,
    TARGET_WRAPPER_MANIFEST,
    _refresh_migration_instruction_surface,
    _replace_markdown_section,
    apply_reinstall,
    build_onboarding_contract,
    build_wrapper_manifest,
    classify_pr_permission,
    classify_wrapper_upgrade,
    classify_integration_mode,
    detect_target_architecture,
    diagnose_environment,
    diagnose_skill,
    diagnose_source_contract,
    load_wrapper_manifest,
    migrate_target_layout,
    plan_feedback_audit,
    plan_harness_upgrade,
    plan_target_layout_migration,
    path_kind,
    plan_reinstall,
    plan_transform_action,
    repo_from_remote,
    runtime_pointer_scope,
    skill_name_from_skill_md,
    stage_label,
    write_wrapper_manifest,
    wrapper_manifest_status,
)
from scripts.evozeus_wrapper_global_hook import (
    GLOBAL_DISPATCHER_COMMAND,
    apply_global_hook_install,
    apply_global_hook_uninstall,
    plan_global_hook_install,
    apply_upgrade_all,
    plan_upgrade_all,
    read_global_hook_status,
    record_global_hook_trust,
)
from scripts.evozeus_wrapper_preflight import (
    build_runtime_identity,
    check_notice_policy,
    check_onboarding_contract,
    check_integration_contract,
    classify_runtime_channel,
    load_wrapper_manifest as load_preflight_manifest,
    referenced_runtime_files,
    root_entry_path as preflight_root_entry_path,
    runtime_pointer_scope as preflight_runtime_pointer_scope,
)


def create_complete_legacy_target(target: Path) -> str:
    replacements = {
        "CURRENT_VERSION": "v1.2.3",
        "REPO_NAME": "MetaInFLow/legacy-skill",
        "VISIBILITY": "private",
        "WRAPPER_VERSION": "v0.6.0",
    }
    status = build_status_section(replacements).replace(
        "harness upgrade-check --target <this-skill-repo> --json",
        "harness upgrade-check --target <this-skill-repo> --latest-version <wrapper-version> --json",
    )
    wrapper = build_wrapper_section(replacements).replace(
        "harness upgrade-check --target <this-skill-repo> --json",
        "harness upgrade-check --target <this-skill-repo> --latest-version <wrapper-version> --json",
    )
    business = (
        "## Business Logic\n\n"
        "PRESERVE-BUSINESS-BYTES https://example.test/tree/main/docs\n\n"
    )
    skill_text = (
        '---\nname: "legacy-skill"\n---\n\n'
        + status.rstrip()
        + "\n\n"
        + business
        + build_evolution_section(replacements).rstrip()
        + "\n\n"
        + wrapper.rstrip()
        + "\n"
    )
    (target / "SKILL.md").write_text(skill_text, encoding="utf-8")

    legacy = target / ".evozeus_evoinfra"
    legacy.mkdir(parents=True)
    legacy_manifest = {
        "wrapper_repo": "MetaInFLow/EvoZeus-CoEvolve",
        "wrapper_version": "v0.6.0",
        "canonical_repo": "MetaInFLow/legacy-skill",
        "managed_files": [],
        "install_links": [],
        "integration": {"mode": "prompt_runtime_check", "native_host_hook_installed": False},
    }
    (legacy / "wrapper.json").write_text(
        json.dumps(legacy_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (legacy / "feedback-policy.json").write_text(
        '{"management_mode":"manual","audit_rule":".evozeus_evoinfra/audit-rule.md"}\n',
        encoding="utf-8",
    )
    (legacy / "audit-rule.md").write_text("# Feedback Audit Rule\n\nPreserve evidence.\n", encoding="utf-8")

    fixture_files = {
        "CHANGELOG.md": "# Changelog\n\n## [v1.2.3] - 2026-07-01\n\n- Existing release.\n",
        "WRAPPER.md": "# Legacy Wrapper\n\nRead `.evozeus_evoinfra/wrapper.json`.\n",
        "docs/index.md": "# Legacy Dashboard\n\nRead `../CHANGELOG.md`.\n",
        "docs/_config.yml": 'title: "Legacy Skill"\n',
        "docs/design-doc-template.md": "# Design Doc\n\n## Related issue\n\n## Verification plan\n",
        "docs/designs/README.md": "# Designs\n\nStore design docs here.\n",
        "docs/wrapper-migrations/README.md": "# Wrapper Migrations\n\nRecord migrations here.\n",
        "scripts/evozeus_wrapper_preflight.py": Path("scripts/evozeus_wrapper_preflight.py").read_text(
            encoding="utf-8"
        ),
        ".github/ISSUE_TEMPLATE/config.yml": (
            "blank_issues_enabled: false\ncontact_links:\n"
            "  - name: Read the Skill dashboard\n"
            "    url: \"https://github.com/MetaInFLow/legacy-skill/tree/main/docs\"\n"
            "    about: Check status first.\n"
        ),
        ".github/ISSUE_TEMPLATE/skill-feedback.yml": Path(
            "templates/target/.github/ISSUE_TEMPLATE/skill-feedback.yml"
        ).read_text(encoding="utf-8"),
        ".github/pull_request_template.md": Path(
            "templates/target/.github/pull_request_template.md"
        ).read_text(encoding="utf-8"),
        ".github/workflows/evozeus-wrapper-preflight.yml": Path(
            "templates/target/.github/workflows/evozeus-wrapper-preflight.yml"
        ).read_text(encoding="utf-8"),
    }
    for rel, content in fixture_files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (target / "scripts/evozeus_wrapper_preflight.py").chmod(0o755)
    return business


class LifecycleBasicsTest(unittest.TestCase):
    def test_replace_markdown_section_preserves_target_h1_and_suffix_bytes(self):
        frontmatter = (
            '---\nname: "example"\ndescription: |\n'
            "  ## EvoZeus-CoEvolve 状态检查\n"
            "  Keep this frontmatter text.\n"
            "---\n\n"
        )
        original = (
            frontmatter
            + "## EvoZeus-CoEvolve 状态检查\n\n"
            "old wrapper-owned status\n\n"
            "```text\n"
            "# Not a structural boundary\n"
            "```\n\n"
            "old wrapper-owned status continues\n\n"
            "  # Target Skill Title\n\n"
            "Target-owned intro bytes.  \n\n"
            "## Target Business Section\n\n"
            "Keep this exactly.\n"
        )
        suffix = original[original.index("  # Target Skill Title") :]

        updated = _replace_markdown_section(
            original,
            "## EvoZeus-CoEvolve 状态检查",
            "## EvoZeus-CoEvolve 状态检查\n\nnew wrapper-owned status\n",
        )

        self.assertTrue(updated.endswith(suffix))
        self.assertTrue(updated.startswith(frontmatter))
        self.assertIn("new wrapper-owned status", updated)
        self.assertNotIn("old wrapper-owned status", updated)

    def test_refresh_instruction_surface_preserves_crlf_target_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            skill = target / "SKILL.md"
            original = (
                b'---\r\nname: "example"\r\n---\r\n\r\n'
                b"## EvoZeus-CoEvolve status\r\n\r\n"
                b"old wrapper-owned status\r\n\r\n"
                b"# Target Skill Title\r\n\r\n"
                b"Target-owned CRLF bytes.  \r\n"
            )
            original = original.replace(
                b"## EvoZeus-CoEvolve status",
                "## EvoZeus-CoEvolve 状态检查".encode("utf-8"),
            )
            target_owned = original[original.index(b"# Target Skill Title") :]
            skill.write_bytes(original)

            _refresh_migration_instruction_surface(
                target,
                {
                    "canonical_repo": "MetaInFLow/example",
                    "instruction_surface": "SKILL.md",
                },
                "v0.10.0",
                "v0.10.1",
                from_layout="consolidated-v2",
                to_layout="consolidated-v2",
                layout_migration_required=False,
            )

            self.assertIn(target_owned, skill.read_bytes())

    def test_refresh_instruction_surface_upgrades_legacy_brand_heading_without_duplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            skill = target / "SKILL.md"
            skill.write_text(
                '---\nname: "example"\n---\n\n'
                "## EvoZeus-wrapper 状态检查\n\n"
                "legacy wrapper-owned status\n\n"
                "# Target Skill Title\n\n"
                "Target-owned content.\n",
                encoding="utf-8",
            )

            _refresh_migration_instruction_surface(
                target,
                {
                    "canonical_repo": "MetaInFLow/example",
                    "instruction_surface": "SKILL.md",
                },
                "v0.10.1",
                "v0.11.0",
                from_layout="consolidated-v2",
                to_layout="consolidated-v2",
                layout_migration_required=False,
            )

            updated = skill.read_text(encoding="utf-8")
            self.assertEqual(updated.count("## EvoZeus-CoEvolve 状态检查"), 1)
            self.assertNotIn("## EvoZeus-wrapper 状态检查", updated)
            self.assertIn("普通 Skill 调用不授权 Harness 升级或其他维护写入", updated)
            self.assertNotIn("再进入主链路", updated)
            self.assertIn("# Target Skill Title\n\nTarget-owned content.", updated)

    def test_crlf_instruction_surface_inserts_status_after_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            skill = target / "SKILL.md"
            frontmatter = b'---\r\nname: "example"\r\n---\r\n'
            target_owned = b"# Target Skill Title\r\n\r\nTarget-owned CRLF bytes.\r\n"
            skill.write_bytes(frontmatter + target_owned)

            _refresh_migration_instruction_surface(
                target,
                {
                    "canonical_repo": "MetaInFLow/example",
                    "instruction_surface": "SKILL.md",
                },
                "v0.10.0",
                "v0.10.1",
                from_layout="consolidated-v2",
                to_layout="consolidated-v2",
                layout_migration_required=False,
            )
            updated = skill.read_bytes()
            status = "## EvoZeus-CoEvolve 状态检查".encode("utf-8")

            self.assertTrue(updated.startswith(frontmatter))
            self.assertIn(target_owned, updated)
            self.assertGreater(updated.index(status), len(frontmatter) - 1)
            self.assertGreater(updated.index(target_owned), updated.index(status))

    def test_version_refresh_note_records_actual_consolidated_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            skill = target / "SKILL.md"
            skill.write_text(
                '---\nname: "example"\n---\n\n'
                "## EvoZeus-CoEvolve 状态检查\n\n"
                "old wrapper-owned status\n\n"
                "# Target Skill Title\n\n"
                "Target-owned intro bytes.  \n"
                "Wrapper harness version: `v9.9.9`\n"
                "Keep --latest-version <wrapper-version> in business prose.\n",
                encoding="utf-8",
            )
            target_owned = skill.read_text(encoding="utf-8").split("# Target Skill Title", 1)[1]
            manifest = {
                "canonical_repo": "MetaInFLow/example",
                "instruction_surface": "SKILL.md",
            }

            surface, changed = _refresh_migration_instruction_surface(
                target,
                manifest,
                "v0.10.0",
                "v0.10.1",
                from_layout="consolidated-v2",
                to_layout="consolidated-v2",
                layout_migration_required=False,
            )
            updated = skill.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertEqual(surface, "SKILL.md")
            self.assertIn(
                "## EvoZeus-CoEvolve Version Refresh Note: v0.10.0 -> v0.10.1",
                updated,
            )
            self.assertIn("- Layout: `consolidated-v2 -> consolidated-v2`", updated)
            self.assertNotIn("Migration Note: v0.10.0 -> v0.10.1", updated)
            self.assertIn("# Target Skill Title" + target_owned, updated)
            self.assertIn("Wrapper harness version: `v9.9.9`", updated)
            self.assertIn("Keep --latest-version <wrapper-version> in business prose.", updated)

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

    def test_status_assessment_is_documented_as_a_skill(self):
        skill = Path("skills/status-assessment/SKILL.md")
        text = skill.read_text(encoding="utf-8")
        self.assertIn("name: evozeus-wrapper-status-assessment", text)
        self.assertIn("The CLI scripts provide facts. This Skill provides judgment", text)
        self.assertIn("Do not move user-facing assessment logic into Python scripts.", text)

    def test_evolution_surface_diagnosis_is_documented_as_a_skill(self):
        skill = Path("skills/evolution-surface-diagnosis/SKILL.md")
        text = skill.read_text(encoding="utf-8")
        self.assertIn("name: evozeus-wrapper-evolution-surface-diagnosis", text)
        self.assertIn("The CLI scripts collect facts. This Skill makes the judgment.", text)
        self.assertIn("Do not treat `evolution_surface.candidates` as final placement.", text)

    def test_root_skill_is_thin_and_routes_to_using_skill(self):
        root = Path("SKILL.md")
        text = root.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 60)
        self.assertIn("skills/using-evozeus-wrapper/SKILL.md", text)
        self.assertIn("This root Skill is only the wrapper entrypoint", text)

    def test_using_evozeus_wrapper_is_operating_skill(self):
        skill = Path("skills/using-evozeus-wrapper/SKILL.md")
        text = skill.read_text(encoding="utf-8")
        self.assertIn("name: using-evozeus-wrapper", text)
        self.assertIn("Use this Skill as the operating guide for EvoZeus-CoEvolve.", text)
        self.assertIn("Do not treat script-produced `evolution_surface.candidates` as final placement.", text)
        self.assertNotIn("TODO", text)

    def test_codex_hook_template_uses_official_project_hooks_location(self):
        hooks_path = Path("templates/target/.codex/hooks.json")
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))

        session_start = hooks["hooks"]["SessionStart"][0]
        handler = session_start["hooks"][0]

        self.assertEqual(session_start["matcher"], "startup|resume")
        self.assertEqual(handler["type"], "command")
        self.assertIn(
            "$(git rev-parse --show-toplevel)/.evozeus-wrapper/hooks/evozeus_wrapper_start_check.py",
            handler["command"],
        )
        self.assertEqual(handler["statusMessage"], "Checking EvoZeus wrapper harness")
        self.assertFalse(Path("templates/target/hooks/hooks-codex.json").exists())

    def test_pages_workflow_keeps_validation_active_when_deployment_is_disabled(self):
        workflow = Path("templates/target/.github/workflows/evozeus-wrapper-preflight.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("  validation:\n", workflow)
        self.assertIn("preflight.py maintainer", workflow)
        self.assertIn("Report Pages deployment mode", workflow)
        self.assertIn("needs: validation", workflow)
        self.assertIn("vars.EVOZEUS_PAGES_ENABLED == 'true'", workflow)
        self.assertIn('\".evozeus-wrapper/**\"', workflow)

    def test_copy_templates_consolidates_wrapper_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            replacements = {
                "DATE": "2026-07-18",
                "INITIAL_VERSION": "v0.1.0",
                "CURRENT_VERSION": "v0.1.0",
                "REPO_NAME": "MetaInFLow/skill",
                "REPO_URL": "https://github.com/MetaInFLow/skill",
                "SKILL_NAME": "skill",
                "VISIBILITY": "private",
                "WRAPPER_VERSION": "v0.8.0",
            }

            copy_templates(target, replacements, force=False)

            self.assertTrue((target / ".evozeus-wrapper/CHANGELOG.md").is_file())
            self.assertTrue((target / ".evozeus-wrapper/WRAPPER.md").is_file())
            self.assertTrue((target / TARGET_PREFLIGHT_SCRIPT).is_file())
            self.assertTrue((target / CODEX_START_HOOK_SCRIPT).is_file())
            self.assertTrue((target / ".evozeus-wrapper/docs/index.md").is_file())
            self.assertTrue((target / TARGET_ONBOARDING_GUIDE).is_file())
            self.assertTrue((target / ".codex/hooks.json").is_file())
            self.assertTrue((target / ".github/ISSUE_TEMPLATE/skill-feedback.yml").is_file())
            self.assertFalse((target / "CHANGELOG.md").exists())
            self.assertFalse((target / "WRAPPER.md").exists())
            self.assertFalse((target / "docs").exists())
            self.assertFalse((target / "scripts").exists())

    def test_consolidated_bootstrap_output_passes_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            (target / "SKILL.md").write_text(
                '---\nname: "skill"\n---\n# Skill\n\nRun the business flow.\n',
                encoding="utf-8",
            )
            replacements = {
                "DATE": "2026-07-18",
                "INITIAL_VERSION": "v0.1.0",
                "CURRENT_VERSION": "v0.1.0",
                "REPO_NAME": "MetaInFLow/skill",
                "REPO_URL": "https://github.com/MetaInFLow/skill",
                "SKILL_NAME": "skill",
                "VISIBILITY": "private",
                "WRAPPER_VERSION": "v0.8.0",
            }
            copy_templates(target, replacements, force=False)
            inject_evolution_method(target, replacements)
            write_wrapper_manifest(
                target,
                build_wrapper_manifest(
                    "MetaInFLow/skill",
                    "v0.8.0",
                    [
                        TARGET_CHANGELOG,
                        ".evozeus-wrapper/WRAPPER.md",
                        ".codex/hooks.json",
                        CODEX_START_HOOK_SCRIPT,
                    ],
                    [],
                ),
            )

            result = subprocess.run(
                [sys.executable, str(target / TARGET_PREFLIGHT_SCRIPT), "structure", "--target", str(target)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            skill_text = (target / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("## EvoZeus-CoEvolve 状态检查", skill_text)
            self.assertIn("## 自进化方法", skill_text)
            self.assertIn(TARGET_WRAPPER_MANIFEST, skill_text)

    def test_preflight_rejects_legacy_layout_until_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            legacy = target / LEGACY_TARGET_WRAPPER_MANIFEST
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"canonical_repo":"MetaInFLow/skill"}\n', encoding="utf-8")

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                load_preflight_manifest(target)

    def test_bootstrap_status_language_is_runtime_safe(self):
        checked_files = [
            Path("scripts/evozeus_wrapper_bootstrap.py"),
            Path("templates/target/WRAPPER.md"),
            Path("templates/target/docs/index.md"),
        ]
        blocked_phrases = [
            "Continue to the target Skill's main flow only after all three are OK.",
            "全部 OK 后",
            "只有检查结果为 OK",
            "才继续进入目标 Skill 原本主链路",
            "才继续进入下方原 Skill 流程",
        ]

        for path in checked_files:
            text = path.read_text(encoding="utf-8")
            self.assertIn("runtime-only install", text)
            for phrase in blocked_phrases:
                self.assertNotIn(phrase, text)

    def test_generated_upgrade_guidance_uses_authoritative_latest_lookup(self):
        replacements = {
            "CURRENT_VERSION": "v0.1.0",
            "REPO_NAME": "MetaInFLow/skill",
            "VISIBILITY": "private",
            "WRAPPER_VERSION": "v0.8.0",
        }

        text = build_status_section(replacements) + build_wrapper_section(replacements)

        self.assertIn("harness upgrade-check --target <this-skill-repo> --json", text)
        self.assertNotIn("--latest-version <wrapper-version>", text)
        self.assertIn("Skill 入口 preflight", text)
        self.assertIn("repo_maintenance_hook", text)
        self.assertIn("global_session_dispatcher", text)
        self.assertIn("SkillInvoke", text)

    def test_generated_status_requires_runtime_identity_on_first_visible_line_once_per_invocation(self):
        replacements = {
            "CURRENT_VERSION": "v0.1.0",
            "REPO_NAME": "MetaInFLow/skill",
            "VISIBILITY": "private",
            "WRAPPER_VERSION": "v0.12.1",
        }

        text = build_status_section(replacements)

        self.assertIn(f"python3 {TARGET_PREFLIGHT_SCRIPT} identity --json", text)
        self.assertIn("runtime_identity.display_line", text)
        self.assertIn("第一条用户可见输出的第一行", text)
        self.assertIn("同一次 invocation 的后续 commentary 和 final 不重复", text)
        self.assertIn("下一次 invocation 再展示一次", text)
        self.assertIn("🧙🏻‍♂️", text)
        self.assertIn("evozeus_notice.py render", text)
        self.assertIn("EvoZeus · Lesson", text)
        self.assertIn("先完成当前业务纠正", text)
        self.assertNotIn("<img", text)

    def test_copy_templates_installs_notice_policy_and_cli_in_target_skill(self):
        replacements = {
            "DATE": "2026-07-27",
            "INITIAL_VERSION": "v0.1.0",
            "CURRENT_VERSION": "v0.1.0",
            "REPO_NAME": "MetaInFLow/skill",
            "REPO_URL": "https://github.com/MetaInFLow/skill",
            "SKILL_NAME": "skill",
            "VISIBILITY": "private",
            "WRAPPER_VERSION": "v0.12.1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()

            copy_templates(target, replacements, force=False)

            policy = target / ".evozeus-wrapper/policies/notice-policy.json"
            cli = target / ".evozeus-wrapper/scripts/evozeus_notice.py"
            self.assertTrue(policy.is_file())
            self.assertTrue(cli.is_file())
            self.assertTrue(os.access(cli, os.X_OK))
            self.assertIn("EvoZeus · Lesson", policy.read_text(encoding="utf-8"))

    def test_preflight_validates_notice_policy_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            policy_path = target / ".evozeus-wrapper/policies/notice-policy.json"
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(
                '{"schema_version":"v2","tag_style":"markdown_code","show_signal_id":false,"events":{}}',
                encoding="utf-8",
            )

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_notice_policy(target)

            valid_policy = json.loads(
                Path("templates/target/.evozeus_evoinfra/notice-policy.json").read_text(
                    encoding="utf-8"
                )
            )
            invalid_visual_policy = json.loads(json.dumps(valid_policy, ensure_ascii=False))
            invalid_visual_policy["events"]["skill"]["show_state_label"] = "false"
            policy_path.write_text(
                json.dumps(invalid_visual_policy, ensure_ascii=False), encoding="utf-8"
            )
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_notice_policy(target)

            policy_path.write_text(json.dumps(valid_policy, ensure_ascii=False), encoding="utf-8")
            check_notice_policy(target)

    def test_feedback_surfaces_require_local_pending_state_and_separate_fix_authorization(self):
        checked_files = [
            Path("skills/evolution-loop/SKILL.md"),
            Path("templates/target/.evozeus_evoinfra/audit-rule.md"),
            Path("templates/target/WRAPPER.md"),
            Path("templates/target/docs/index.md"),
        ]

        for path in checked_files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("EvoZeus · Lesson", text)
                self.assertIn("待记录", text)
                self.assertIn("Issue", text)
                self.assertTrue("授权" in text or "authorization" in text.lower())

        policy = json.loads(
            Path("templates/target/.evozeus_evoinfra/feedback-policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(policy["authorization"]["capture"], "no_external_write")
        self.assertEqual(
            policy["authorization"]["issue_submission"],
            "explicit_confirmation_required",
        )
        self.assertEqual(
            policy["authorization"]["fix_execution"],
            "separate_confirmation_required",
        )

    def test_generated_status_keeps_compatible_harness_updates_advisory_and_requires_explicit_authorization(self):
        replacements = {
            "CURRENT_VERSION": "v0.1.0",
            "REPO_NAME": "MetaInFLow/skill",
            "VISIBILITY": "private",
            "WRAPPER_VERSION": "v0.11.4",
        }

        text = build_status_section(replacements)

        self.assertIn("兼容的旧 wrapper 只作为维护提醒，不阻塞业务主链路", text)
        self.assertIn("普通 Skill 调用不授权 Harness 升级或其他维护写入", text)
        self.assertIn("用户明确请求 Harness 维护或升级", text)
        self.assertNotIn("如果 wrapper 落后：先运行", text)
        self.assertNotIn("再进入主链路", text)

    def test_preflight_root_entry_path_uses_manifest_instruction_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "hooked-skill-system"
            target.mkdir()
            (target / ".codex-plugin").mkdir()
            (target / ".codex-plugin" / "plugin.json").write_text(
                '{"skills":"./skills/","hooks":"./hooks/hooks-codex.json"}',
                encoding="utf-8",
            )
            (target / "hooks").mkdir()
            (target / "hooks" / "hooks-codex.json").write_text(
                '{"hooks":{"session-start":"skills/session-bootstrap/SKILL.md"}}',
                encoding="utf-8",
            )
            skill_dir = target / "skills" / "session-bootstrap"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: "session-bootstrap"\n---\n# Session Bootstrap\nUse at session start to load skills.\n',
                encoding="utf-8",
            )
            write_wrapper_manifest(
                target,
                build_wrapper_manifest(
                    "MetaInFLow/hooked-skill-system",
                    "v0.1.0",
                    [],
                    [],
                    instruction_surface="skills/session-bootstrap/SKILL.md",
                ),
            )

            entry = preflight_root_entry_path(target)

            self.assertEqual(str(entry.relative_to(target)), "skills/session-bootstrap/SKILL.md")

    def test_runtime_channel_classification_fails_closed(self):
        stable = classify_runtime_channel(
            branch="main",
            clean=True,
            head="abc123",
            skill_release="v1.2.3",
            latest_release_tag="v1.2.3",
            latest_release_commit="abc123",
        )
        uat = classify_runtime_channel(
            branch="uat/current",
            clean=True,
            head="def456",
            skill_release="v1.2.3",
            latest_release_tag="v1.2.3",
            latest_release_commit="abc123",
        )

        self.assertEqual(stable, "stable")
        self.assertEqual(uat, "uat")

        development_cases = [
            {
                "branch": "main",
                "clean": False,
                "head": "abc123",
                "skill_release": "v1.2.3",
                "latest_release_tag": "v1.2.3",
                "latest_release_commit": "abc123",
            },
            {
                "branch": "codex/feature",
                "clean": True,
                "head": "def456",
                "skill_release": "v1.2.3",
                "latest_release_tag": "v1.2.3",
                "latest_release_commit": "abc123",
            },
            {
                "branch": "main",
                "clean": True,
                "head": "abc123",
                "skill_release": None,
                "latest_release_tag": None,
                "latest_release_commit": None,
            },
            {
                "branch": "main",
                "clean": True,
                "head": "abc123",
                "skill_release": "v1.2.3",
                "latest_release_tag": None,
                "latest_release_commit": None,
            },
        ]
        for case in development_cases:
            with self.subTest(case=case):
                self.assertEqual(classify_runtime_channel(**case), "development")

    def test_runtime_identity_contains_clickable_repo_dual_versions_and_stable_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            manifest = target / TARGET_WRAPPER_MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "canonical_repo": "MetaInFLow/skill",
                        "wrapper_version": "v0.12.1",
                    }
                ),
                encoding="utf-8",
            )
            (target / TARGET_CHANGELOG).write_text(
                "# Changelog\n\n## [v1.2.3] - 2026-07-27\n\n- Stable.\n",
                encoding="utf-8",
            )

            identity = build_runtime_identity(
                target,
                latest_release={"available": True, "tag": "v1.2.3", "error": None},
                git_facts={
                    "branch": "main",
                    "clean": True,
                    "head": "abc123",
                    "release_commit": "abc123",
                    "origin_repo": "MetaInFLow/skill",
                },
            )

            self.assertEqual(identity["schema_version"], "v1")
            self.assertEqual(identity["skill_release"], "v1.2.3")
            self.assertEqual(identity["harness_version"], "v0.12.1")
            self.assertEqual(identity["channel"], "stable")
            self.assertEqual(identity["channel_label"], "正式版")
            self.assertEqual(identity["display_once_scope"], "skill_invocation")
            self.assertEqual(
                identity["display_line"],
                "🧙🏻‍♂️ `EvoZeus · 受管 Skill` [MetaInFLow/skill](https://github.com/MetaInFLow/skill) · Skill v1.2.3 · Harness v0.12.1 · `渠道：正式版`",
            )
            self.assertNotIn("<img", identity["display_line"])

    def test_runtime_identity_uses_uat_and_unverified_development_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            manifest = target / TARGET_WRAPPER_MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "canonical_repo": "MetaInFLow/skill",
                        "wrapper_version": "v0.12.1",
                    }
                ),
                encoding="utf-8",
            )
            (target / TARGET_CHANGELOG).write_text(
                "# Changelog\n\n## [v1.2.3] - 2026-07-27\n\n- Current.\n",
                encoding="utf-8",
            )
            base_facts = {
                "clean": True,
                "head": "def456",
                "release_commit": "abc123",
                "origin_repo": "MetaInFLow/skill",
            }

            uat = build_runtime_identity(
                target,
                latest_release={"available": True, "tag": "v1.2.3", "error": None},
                git_facts={**base_facts, "branch": "uat/current"},
            )
            development = build_runtime_identity(
                target,
                latest_release={"available": False, "tag": None, "error": "offline"},
                git_facts={**base_facts, "branch": "main", "release_commit": None},
            )

            self.assertEqual((uat["channel"], uat["channel_label"]), ("uat", "UAT"))
            self.assertEqual(
                (development["channel"], development["channel_label"]),
                ("development", "开发版"),
            )
            self.assertEqual(development["channel_reason"], "release_unverified")

    def test_runtime_identity_uses_target_notice_policy_visuals(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            manifest = target / TARGET_WRAPPER_MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "canonical_repo": "MetaInFLow/skill",
                        "wrapper_version": "v0.13.0",
                    }
                ),
                encoding="utf-8",
            )
            (target / TARGET_CHANGELOG).write_text(
                "# Changelog\n\n## [v1.2.3] - 2026-07-27\n\n- Stable.\n",
                encoding="utf-8",
            )
            policy_path = target / ".evozeus-wrapper/policies/notice-policy.json"
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy = json.loads(
                Path("templates/target/.evozeus_evoinfra/notice-policy.json").read_text(
                    encoding="utf-8"
                )
            )
            policy["events"]["skill"]["tag"] = "EvoZeus · Custom Skill"
            policy["events"]["skill"]["states"]["active"]["icon"] = "🧠"
            policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

            identity = build_runtime_identity(
                target,
                latest_release={"available": True, "tag": "v1.2.3", "error": None},
                git_facts={
                    "branch": "main",
                    "clean": True,
                    "head": "abc123",
                    "release_commit": "abc123",
                    "origin_repo": "MetaInFLow/skill",
                },
            )

            self.assertTrue(identity["display_line"].startswith("🧠 `EvoZeus · Custom Skill`"))

    def test_runtime_reference_parser_excludes_command_arguments(self):
        text = "Run `scripts/research_search.py --plan path/to/plan.json --out output.jsonl`."

        self.assertEqual(referenced_runtime_files(text), ["scripts/research_search.py"])

    def test_write_wrapper_manifest_uses_target_evoinfra_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()

            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.6.0", [], []),
            )

            self.assertTrue((target / TARGET_WRAPPER_MANIFEST).is_file())
            self.assertFalse((target / LEGACY_TARGET_WRAPPER_MANIFEST).exists())

    def test_load_wrapper_manifest_detects_legacy_manifest_for_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            legacy_path = target / LEGACY_TARGET_WRAPPER_MANIFEST
            legacy_path.parent.mkdir()
            legacy_path.write_text(
                '{"canonical_repo":"MetaInFLow/skill","wrapper_version":"v0.5.0"}\n',
                encoding="utf-8",
            )

            manifest = load_wrapper_manifest(target, allow_legacy=True)
            status = wrapper_manifest_status(target)

            self.assertEqual(manifest["canonical_repo"], "MetaInFLow/skill")
            self.assertTrue(status["legacy_manifest_detected"])
            self.assertTrue(status["migration_required"])
            self.assertEqual(status["manifest_source"], "legacy_evoinfra")
            with self.assertRaises(ValueError):
                load_wrapper_manifest(target)

    def test_load_wrapper_manifest_rejects_conflicting_current_and_legacy_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            current_path = target / TARGET_WRAPPER_MANIFEST
            legacy_path = target / LEGACY_TARGET_WRAPPER_MANIFEST
            current_path.parent.mkdir()
            legacy_path.parent.mkdir()
            current_path.write_text(
                '{"canonical_repo":"MetaInFLow/skill","wrapper_version":"v0.6.0"}\n',
                encoding="utf-8",
            )
            legacy_path.write_text(
                '{"canonical_repo":"MetaInFLow/skill","wrapper_version":"v0.5.0"}\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_wrapper_manifest(target)

    def test_migrate_target_layout_moves_legacy_files_and_rewrites_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            create_complete_legacy_target(target)
            old_legacy_dir = target / ".evozeus_evoinfra"
            legacy_dir = target / ".evozeus"
            old_legacy_dir.rename(legacy_dir)
            legacy_manifest = json.loads((legacy_dir / "wrapper.json").read_text(encoding="utf-8"))
            legacy_manifest["wrapper_version"] = "v0.5.0"
            (legacy_dir / "wrapper.json").write_text(
                json.dumps(legacy_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (legacy_dir / "feedback-policy.json").write_text(
                '{"audit_rule":".evozeus/audit-rule.md"}\n',
                encoding="utf-8",
            )
            skill_path = target / "SKILL.md"
            skill_path.write_text(
                skill_path.read_text(encoding="utf-8").replace(".evozeus-wrapper", ".evozeus"),
                encoding="utf-8",
            )

            report = migrate_target_layout(target, latest_version="v0.9.1", today=date(2026, 7, 18))
            manifest = load_wrapper_manifest(target)
            skill_text = (target / "SKILL.md").read_text(encoding="utf-8")
            policy = (target / TARGET_FEEDBACK_POLICY).read_text(encoding="utf-8")

            self.assertIn(
                "move .evozeus/wrapper.json -> .evozeus-wrapper/wrapper.json",
                report["actions"],
            )
            self.assertFalse((target / ".evozeus").exists())
            self.assertTrue((target / TARGET_WRAPPER_MANIFEST).is_file())
            self.assertEqual(manifest["wrapper_version"], "v0.9.1")
            self.assertEqual(manifest["layout_version"], 2)
            self.assertIn(TARGET_PREFLIGHT_SCRIPT, manifest["managed_files"])
            self.assertIn(TARGET_ONBOARDING_GUIDE, manifest["managed_files"])
            self.assertTrue((target / TARGET_ONBOARDING_GUIDE).is_file())
            self.assertEqual(manifest["onboarding"]["installation"]["mode"], "canonical_repo_symlink")
            self.assertFalse(manifest["onboarding"]["generated_child_skills"]["hooks_inherited"])
            self.assertIn(TARGET_WRAPPER_MANIFEST, skill_text)
            self.assertIn(".evozeus-wrapper/policies/audit-rule.md", policy)
            self.assertTrue(
                (target / ".evozeus-wrapper/docs/migrations/2026-07-18-layout-v1-to-v2.md").is_file()
            )
            self.assertIn(
                'TARGET_EVOINFRA_DIR = ".evozeus-wrapper"',
                (target / TARGET_PREFLIGHT_SCRIPT).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "source: ./.evozeus-wrapper/docs",
                (target / ".github/workflows/evozeus-wrapper-preflight.yml").read_text(encoding="utf-8"),
            )
            self.assertFalse(wrapper_manifest_status(target)["migration_required"])

    def test_migrate_target_layout_seeds_policies_when_oldest_layout_lacks_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            create_complete_legacy_target(target)
            legacy = target / ".evozeus_evoinfra"
            (legacy / "feedback-policy.json").unlink()
            (legacy / "audit-rule.md").unlink()
            legacy.rename(target / ".evozeus")

            report = migrate_target_layout(target, latest_version="v0.11.4")

            self.assertTrue(report["writes"])
            self.assertEqual(report["validation"]["structure"], "passed")
            self.assertTrue((target / TARGET_FEEDBACK_POLICY).is_file())
            self.assertTrue((target / ".evozeus-wrapper/policies/audit-rule.md").is_file())

    def test_migrate_layout_produces_complete_scoped_hook_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "legacy-skill"
            target.mkdir()
            business = create_complete_legacy_target(target)
            before = (target / "SKILL.md").read_text(encoding="utf-8")
            before_business = before.split("## Business Logic", 1)[1].split("## 自进化方法", 1)[0]

            report = migrate_target_layout(
                target,
                latest_version="v0.9.1",
                today=date(2026, 7, 18),
            )
            manifest = load_wrapper_manifest(target)
            skill_text = (target / "SKILL.md").read_text(encoding="utf-8")
            after_business = skill_text.split("## Business Logic", 1)[1].split("## 自进化方法", 1)[0]
            hooks = json.loads((target / ".codex/hooks.json").read_text(encoding="utf-8"))

            self.assertTrue(report["writes"])
            self.assertEqual(before_business, after_business)
            self.assertIn(business.strip(), skill_text)
            self.assertIn("v0.9.1", skill_text.split("## Business Logic", 1)[0])
            self.assertNotIn("--latest-version <wrapper-version>", skill_text)
            self.assertIn("v0.6.0 -> v0.9.1", skill_text)
            self.assertEqual(manifest["integration"]["mode"], "prompt_runtime_check")
            self.assertFalse(manifest["integration"]["native_skill_invocation_hook_installed"])
            self.assertTrue(
                manifest["integration"]["capabilities"]["repo_maintenance_hook"]["installed"]
            )
            self.assertFalse(
                manifest["integration"]["capabilities"]["global_prompt_lesson_watcher"][
                    "installed"
                ]
            )
            self.assertEqual(
                manifest["integration"]["capabilities"]["global_prompt_lesson_watcher"][
                    "event"
                ],
                "UserPromptSubmit",
            )
            self.assertEqual(manifest["hook_registration"]["codex"]["event"], "SessionStart")
            self.assertIn("SessionStart", hooks["hooks"])
            self.assertIn(
                "/tree/main/.evozeus-wrapper/docs",
                (target / ".github/ISSUE_TEMPLATE/config.yml").read_text(encoding="utf-8"),
            )

            for command in ("structure", "maintainer", "runtime"):
                result = subprocess.run(
                    [sys.executable, str(target / TARGET_PREFLIGHT_SCRIPT), command, "--target", str(target)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            hook_env = {**os.environ, "EVOZEUS_WRAPPER_LATEST_VERSION": "v0.9.1"}
            hook_result = subprocess.run(
                [sys.executable, str(target / CODEX_START_HOOK_SCRIPT)],
                input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                text=True,
                capture_output=True,
                cwd=target,
                env=hook_env,
                check=False,
            )
            hook_payload = json.loads(hook_result.stdout)
            self.assertEqual(hook_result.returncode, 0)
            self.assertTrue(hook_payload["continue"])
            self.assertIn("current", hook_payload["systemMessage"])

    def test_migrate_layout_blocks_invalid_codex_hooks_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "legacy-skill"
            target.mkdir()
            create_complete_legacy_target(target)
            hooks_path = target / ".codex/hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text("{not-json\n", encoding="utf-8")

            plan = plan_target_layout_migration(target, latest_version="v0.9.1")

            self.assertFalse(plan["can_apply"])
            self.assertTrue(any("hooks.json" in conflict for conflict in plan["conflicts"]))
            with self.assertRaises(ValueError):
                migrate_target_layout(target, latest_version="v0.9.1")
            self.assertTrue((target / LEGACY_TARGET_WRAPPER_MANIFEST).is_file())
            self.assertFalse((target / TARGET_WRAPPER_MANIFEST).exists())

    def test_migrate_layout_preserves_custom_session_start_hook_when_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "legacy-skill"
            target.mkdir()
            create_complete_legacy_target(target)
            hooks_path = target / ".codex/hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "startup",
                                    "hooks": [{"type": "command", "command": "python3 custom.py"}],
                                }
                            ]
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            migrate_target_layout(target, latest_version="v0.9.1", today=date(2026, 7, 18))
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]["SessionStart"]
            commands = [hook["command"] for entry in hooks for hook in entry.get("hooks", [])]

            self.assertIn("python3 custom.py", commands)
            self.assertTrue(any("evozeus_wrapper_start_check.py" in command for command in commands))

    def test_migrate_layout_repairs_incomplete_existing_v2_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "legacy-skill"
            target.mkdir()
            create_complete_legacy_target(target)
            migrate_target_layout(target, latest_version="v0.9.0", today=date(2026, 7, 17))

            skill_path = target / "SKILL.md"
            skill_path.write_text(
                skill_path.read_text(encoding="utf-8").replace(
                    "## Business Logic",
                    "# Target Skill Title\n\nTarget-owned intro bytes.  \n\n## Business Logic",
                    1,
                ),
                encoding="utf-8",
            )

            (target / ".codex/hooks.json").unlink()
            manifest_path = target / TARGET_WRAPPER_MANIFEST
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["wrapper_version"] = "v0.9.0"
            manifest["integration"] = {
                "mode": "prompt_runtime_check",
                "native_host_hook_installed": False,
            }
            manifest.pop("dashboard", None)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            plan = plan_target_layout_migration(target, latest_version="v0.9.1", today=date(2026, 7, 18))
            report = migrate_target_layout(target, latest_version="v0.9.1", today=date(2026, 7, 18))
            repaired = load_wrapper_manifest(target)
            refreshed_skill = skill_path.read_text(encoding="utf-8")

            self.assertTrue(plan["migration_required"])
            self.assertEqual(plan["from_layout"], "consolidated-v2")
            self.assertTrue(report["writes"])
            self.assertTrue((target / ".codex/hooks.json").is_file())
            self.assertEqual(repaired["wrapper_version"], "v0.9.1")
            self.assertEqual(repaired["integration"]["mode"], "prompt_runtime_check")
            self.assertTrue(
                repaired["integration"]["capabilities"]["repo_maintenance_hook"]["installed"]
            )
            self.assertEqual(repaired["dashboard"]["deployment_mode"], "opt_in_github_pages")
            self.assertIn(
                "# Target Skill Title\n\nTarget-owned intro bytes.  \n\n## Business Logic",
                refreshed_skill,
            )
            self.assertIn(
                "## EvoZeus-CoEvolve Version Refresh Note: v0.9.0 -> v0.9.1",
                refreshed_skill,
            )
            self.assertIn("- Layout: `consolidated-v2 -> consolidated-v2`", refreshed_skill)
            self.assertTrue(
                (target / ".evozeus-wrapper/docs/migrations/2026-07-18-v0.9.0-to-v0.9.1.md").is_file()
            )

    def test_layout_migration_conflict_stops_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            legacy_manifest = target / LEGACY_TARGET_WRAPPER_MANIFEST
            legacy_manifest.parent.mkdir(parents=True)
            legacy_manifest.write_text(
                '{"canonical_repo":"MetaInFLow/skill","wrapper_version":"v0.7.0"}\n',
                encoding="utf-8",
            )
            (target / "WRAPPER.md").write_text("legacy wrapper\n", encoding="utf-8")
            destination = target / ".evozeus-wrapper/WRAPPER.md"
            destination.parent.mkdir(parents=True)
            destination.write_text("different new wrapper\n", encoding="utf-8")

            plan = plan_target_layout_migration(target, latest_version="v0.8.0")

            self.assertFalse(plan["can_apply"])
            self.assertTrue(plan["conflicts"])
            with self.assertRaises(ValueError):
                migrate_target_layout(target, latest_version="v0.8.0")
            self.assertTrue((target / "WRAPPER.md").is_file())
            self.assertEqual(destination.read_text(encoding="utf-8"), "different new wrapper\n")
            self.assertFalse((target / TARGET_WRAPPER_MANIFEST).exists())

    def test_layout_migration_is_idempotent_for_layout_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.8.0", [], []),
            )
            (target / "CHANGELOG.md").write_text(
                "# Business changelog not owned by wrapper\n",
                encoding="utf-8",
            )

            plan = plan_target_layout_migration(target, latest_version="v0.8.0")
            result = migrate_target_layout(target, latest_version="v0.8.0")

            self.assertFalse(plan["migration_required"])
            self.assertEqual(plan["moves"], [])
            self.assertFalse(result["writes"])
            self.assertTrue((target / "CHANGELOG.md").is_file())

    def test_plan_feedback_audit_captures_wrapper_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.6.0", [], []),
            )
            policy_path = target / TARGET_FEEDBACK_POLICY
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(
                '{"management_mode":"semi_managed","audit_rule":".evozeus-wrapper/policies/audit-rule.md"}\n',
                encoding="utf-8",
            )

            report = plan_feedback_audit(
                target=target,
                user_input="这个 wrapper 没有自动 issue 回收，有问题",
            )

            self.assertTrue(report["should_capture"])
            self.assertEqual(report["route"], "wrapper")
            self.assertEqual(report["issue_repo"], "MetaInFLow/EvoZeus-CoEvolve")
            self.assertEqual(report["policy_path"], TARGET_FEEDBACK_POLICY)
            self.assertEqual(report["capture_state"], "LOCAL_PENDING_CONFIRMATION")
            self.assertRegex(report["signal_id"], r"^sig_[0-9A-F]{8}$")
            self.assertEqual(
                report["capture_marker"],
                "💡 `EvoZeus · Lesson` 待记录",
            )
            self.assertEqual(report["user_notice"]["kind"], "lesson")
            self.assertEqual(report["user_notice"]["state"], "pending")
            self.assertFalse(report["user_notice"]["writes"])
            self.assertNotIn(report["signal_id"], report["user_notice"]["display_text"])
            self.assertIn("是否记录", report["user_notice"]["action"])
            self.assertIn("不启动修复", report["user_notice"]["action"])
            self.assertFalse(report["writes"])
            self.assertFalse(report["capture_persisted"])
            self.assertIsNone(report["issue_create_command"])
            self.assertEqual(
                report["next_action"],
                "continue_business_and_await_feedback_submission_confirmation",
            )
            self.assertEqual(report["authorization"]["issue_submission"], "explicit_confirmation_required")
            self.assertEqual(report["authorization"]["fix_execution"], "separate_confirmation_required")

    def test_feedback_audit_uses_redacted_context_as_visible_lesson_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.13.0", [], []),
            )

            report = plan_feedback_audit(
                target=target,
                user_input="我不满意，这个结果漏了关键检查",
                context="项目巡检必须校验未关闭任务负责人和实时在职状态",
            )

            self.assertEqual(
                report["user_notice"]["message"],
                "捕捉到一条可复用 Lesson：项目巡检必须校验未关闭任务负责人和实时在职状态。",
            )

    def test_preflight_rejects_native_hook_mode_without_hook_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "wrapped-skill"
            target.mkdir()
            manifest = {"integration": {"mode": "native_host_hook"}}

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_preflight_rejects_plugin_lifecycle_hook_as_native_skill_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "wrapped-skill"
            target.mkdir()
            (target / ".claude-plugin").mkdir()
            (target / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
            (target / "hooks").mkdir()
            (target / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
            manifest = {"integration": {"mode": "native_host_hook"}}

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_preflight_rejects_native_codex_project_hook_without_invocation_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "wrapped-skill"
            target.mkdir()
            (target / ".codex").mkdir(parents=True)
            (target / ".codex" / "hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
            hook_script = target / CODEX_START_HOOK_SCRIPT
            hook_script.parent.mkdir(parents=True)
            hook_script.write_text(
                "#!/usr/bin/env python3\n",
                encoding="utf-8",
            )
            manifest = {"integration": {"mode": "native_host_hook"}}

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_project_hook_is_repo_maintenance_not_skill_invocation(self):
        integration = classify_integration_mode(
            target_kind="single_skill",
            root_entry="SKILL.md",
            hook_files=[".codex/hooks.json", CODEX_START_HOOK_SCRIPT],
            plugin_manifests=[],
            skill_entries=[],
        )

        self.assertEqual(integration["mode"], "prompt_runtime_check")
        self.assertFalse(integration["native_skill_invocation_hook_installed"])
        self.assertTrue(integration["capabilities"]["repo_maintenance_hook"]["installed"])
        self.assertFalse(
            integration["capabilities"]["repo_maintenance_hook"]["covers_skill_invocation"]
        )
        self.assertEqual(
            integration["capabilities"]["global_prompt_lesson_watcher"]["event"],
            "UserPromptSubmit",
        )
        self.assertEqual(
            integration["capabilities"]["global_prompt_lesson_watcher"]["scope"],
            "all_user_prompts",
        )
        self.assertFalse(
            integration["capabilities"]["global_prompt_lesson_watcher"][
                "covers_skill_invocation"
            ]
        )

    def test_preflight_rejects_prompt_watcher_invocation_or_portable_install_claims(self):
        manifest = build_wrapper_manifest(
            "MetaInFLow/skill",
            "v0.13.1",
            [".codex/hooks.json", CODEX_START_HOOK_SCRIPT],
            [],
        )
        watcher = manifest["integration"]["capabilities"]["global_prompt_lesson_watcher"]
        watcher["installed"] = True
        watcher["native_enforced"] = True
        watcher["covers_skill_invocation"] = True

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_detected_skill_entry_requires_actual_status_prelude(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            skill = target / "SKILL.md"
            skill.write_text("# Business only\n", encoding="utf-8")

            before = detect_target_architecture(target)["integration"]["capabilities"][
                "skill_entry_preflight"
            ]
            skill.write_text(
                "## EvoZeus-CoEvolve 状态检查\n\n检查。\n\n# Business only\n",
                encoding="utf-8",
            )
            after = detect_target_architecture(target)["integration"]["capabilities"][
                "skill_entry_preflight"
            ]

            self.assertFalse(before["installed"])
            self.assertFalse(before["covers_skill_invocation"])
            self.assertTrue(after["installed"])
            self.assertTrue(after["covers_skill_invocation"])

    def test_detected_skill_entry_uses_manifest_selected_non_root_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bundle"
            control = target / "skills/control/SKILL.md"
            control.parent.mkdir(parents=True)
            (target / "AGENTS.md").write_text("# Runtime\n", encoding="utf-8")
            control.write_text(
                "---\nname: control\n---\n\n"
                "## EvoZeus-CoEvolve 状态检查\n\n检查。\n\n# Control\n",
                encoding="utf-8",
            )
            manifest = target / ".evozeus-wrapper/wrapper.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"instruction_surface": "skills/control/SKILL.md"}),
                encoding="utf-8",
            )

            integration = detect_target_architecture(target)["integration"]

            self.assertEqual(integration["instruction_surface"], "skills/control/SKILL.md")
            self.assertTrue(
                integration["capabilities"]["skill_entry_preflight"]["installed"]
            )

    def test_manifest_selected_instruction_surface_cannot_escape_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "bundle"
            target.mkdir()
            (target / "AGENTS.md").write_text("# Runtime\n", encoding="utf-8")
            outside = root / "outside.md"
            outside.write_text(
                "## EvoZeus-CoEvolve 状态检查\n\nExternal.\n",
                encoding="utf-8",
            )
            manifest = target / ".evozeus-wrapper/wrapper.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "instruction_surface": "../outside.md",
                        "integration": {
                            "mode": "prompt_runtime_check",
                            "capabilities": {
                                "skill_entry_preflight": {
                                    "installed": True,
                                    "native_enforced": False,
                                    "covers_skill_invocation": True,
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            integration = detect_target_architecture(target)["integration"]

            self.assertEqual(integration["instruction_surface"], "AGENTS.md")
            self.assertFalse(
                integration["capabilities"]["skill_entry_preflight"]["installed"]
            )
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                preflight_root_entry_path(target)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, json.loads(manifest.read_text(encoding="utf-8")))

    def test_preflight_rejects_native_invocation_claim_backed_only_by_project_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "wrapped-skill"
            target.mkdir()
            (target / ".codex").mkdir()
            (target / ".codex" / "hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
            hook_script = target / CODEX_START_HOOK_SCRIPT
            hook_script.parent.mkdir(parents=True)
            hook_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            manifest = {
                "integration": {
                    "mode": "native_host_hook",
                    "native_host_hook_installed": True,
                }
            }

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_preflight_rejects_project_registration_that_claims_invocation_coverage(self):
        manifest = build_wrapper_manifest(
            "MetaInFLow/skill",
            "v0.10.0",
            [".codex/hooks.json", CODEX_START_HOOK_SCRIPT],
            [],
        )
        manifest["hook_registration"]["codex"]["covers_skill_invocation"] = True

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_preflight_rejects_user_runtime_state_in_portable_manifest(self):
        manifest = {
            "integration": {
                "mode": "prompt_runtime_check",
                "native_skill_invocation_hook_installed": False,
                "capabilities": {
                    "global_session_dispatcher": {
                        "installed": True,
                        "native_enforced": True,
                        "trust_status": "trusted",
                        "covers_skill_invocation": False,
                    }
                },
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)

    def test_preflight_rejects_skill_entry_claim_without_status_prelude(self):
        manifest = {
            "instruction_surface": "SKILL.md",
            "integration": {
                "mode": "prompt_runtime_check",
                "native_skill_invocation_hook_installed": False,
                "capabilities": {
                    "skill_entry_preflight": {
                        "installed": True,
                        "native_enforced": False,
                        "covers_skill_invocation": True,
                    }
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            (target / "SKILL.md").write_text("# Business only\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                check_integration_contract(target, manifest)


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
            self.assertEqual(report["next_action"], "continue_to_target_repo_diagnosis")
            self.assertEqual(report["evozeus_home"]["exists"], True)
            self.assertEqual(report["evozeus_home"]["required_action"], "none")
            self.assertEqual(report["evozeus_home"]["runtime_exists"], True)
            self.assertEqual(report["evozeus_home"]["projects_exists"], True)
            self.assertEqual(report["mother_repo"]["remote"], "MetaInFLow/EvoZeus")
            self.assertEqual(report["dependencies"]["git"], "ok")
            self.assertEqual(report["dependencies"]["gh"], "ok")
            self.assertEqual(report["dependencies"]["gh_auth"], "ok")

    def test_diagnose_environment_reports_missing_home_and_failed_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            def runner(args, cwd=None):
                if args[:3] == ["gh", "auth", "status"]:
                    return {"returncode": 1, "stdout": "", "stderr": "not logged in"}
                return {"returncode": 0, "stdout": "", "stderr": ""}

            report = diagnose_environment(home=home, runner=runner)
            self.assertEqual(report["next_action"], "install_evozeus")
            self.assertEqual(report["evozeus_home"]["exists"], False)
            self.assertEqual(report["evozeus_home"]["required_action"], "install_evozeus")
            self.assertEqual(report["dependencies"]["git"], "ok")
            self.assertEqual(report["dependencies"]["gh"], "ok")
            self.assertEqual(report["dependencies"]["gh_auth"], "failed")


class TargetSkillDiagnosisTest(unittest.TestCase):
    def test_diagnose_skill_reports_target_repo_install_and_missing_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            target = Path(tmp) / "resume-screening"
            target.mkdir()
            skill_text = '---\nname: "resume-screening"\n---\n# Resume Screening\n'
            (target / "SKILL.md").write_text(skill_text, encoding="utf-8")

            codex_install = home / ".codex" / "skills" / "resume-screening"
            codex_install.mkdir(parents=True)
            (codex_install / "SKILL.md").write_text(skill_text, encoding="utf-8")

            projects_pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / "resume-screening"
            projects_pointer.mkdir(parents=True)

            def runner(args, cwd=None):
                return {"returncode": 0, "stdout": "", "stderr": ""}

            report = diagnose_skill(
                target=target,
                repo="MetaInFLow/resume-screening",
                skill_name=None,
                home=home,
                runner=runner,
            )

            self.assertEqual(report["stage"], "target_skill_diagnosis")
            self.assertEqual(report["skill"]["name"], "resume-screening")
            self.assertEqual(report["skill"]["target_kind"], "single_skill")
            self.assertEqual(report["skill"]["root_entry"], "SKILL.md")
            self.assertEqual(report["skill"]["has_skill_md"], True)
            self.assertEqual(report["repo"]["name"], "MetaInFLow/resume-screening")
            self.assertEqual(report["repo"]["exists_on_github"], True)
            self.assertEqual(report["repo"]["projects_pointer"], str(projects_pointer.resolve()))
            self.assertEqual(report["harness"]["state"], "missing")
            self.assertEqual(len(report["installs"]), 1)
            self.assertEqual(report["installs"][0]["path"], str(codex_install.resolve()))
            self.assertEqual(report["installs"][0]["kind"], "directory")
            self.assertEqual(report["installs"][0]["matches_target_skill_md"], True)

    def test_diagnose_skill_marks_scattered_harness_as_migration_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            target = Path(tmp) / "skill"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")
            (target / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

            report = diagnose_skill(target=target, repo=None, skill_name=None, home=home)
            self.assertEqual(report["harness"]["state"], "migration_required")
            self.assertIn("CHANGELOG.md", report["harness"]["legacy_files"])

    def test_diagnose_skill_preserves_existing_repo_latest_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            target = Path(tmp) / "skill"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")

            def runner(args, cwd=None):
                if args[:3] == ["gh", "repo", "view"]:
                    return {"returncode": 0, "stdout": "", "stderr": ""}
                if args[:3] == ["gh", "release", "view"]:
                    return {
                        "returncode": 0,
                        "stdout": '{"tagName":"v0.9.6","url":"https://github.com/o/r/releases/tag/v0.9.6","publishedAt":"2026-06-26T17:57:14Z"}',
                        "stderr": "",
                    }
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_skill(target=target, repo="o/r", skill_name=None, home=home, runner=runner)

            self.assertEqual(report["repo"]["latest_release"]["tag"], "v0.9.6")
            self.assertEqual(report["version"]["status"], "adopt_existing_release")
            self.assertEqual(report["version"]["current_tag"], "v0.9.6")
            self.assertFalse(report["version"]["requires_owner_choice"])

    def test_diagnose_skill_uses_changelog_when_existing_repo_has_no_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            target = Path(tmp) / "skill"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")
            (target / "CHANGELOG.md").write_text("# Changelog\n\n## [v0.3.0] - 2026-06-27\n", encoding="utf-8")

            def runner(args, cwd=None):
                if args[:3] == ["gh", "repo", "view"]:
                    return {"returncode": 0, "stdout": "", "stderr": ""}
                if args[:3] == ["gh", "release", "view"]:
                    return {"returncode": 1, "stdout": "", "stderr": "release not found"}
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_skill(target=target, repo="o/r", skill_name=None, home=home, runner=runner)

            self.assertEqual(report["version"]["status"], "github_release_missing_create_from_changelog")
            self.assertEqual(report["version"]["current_tag"], "v0.3.0")
            self.assertFalse(report["version"]["requires_owner_choice"])

    def test_detect_target_architecture_recognizes_agents_root_runtime_skill_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sales-office-runtime-kit"
            target.mkdir()
            (target / "AGENTS.md").write_text("# Runtime Instructions\n", encoding="utf-8")
            for dirname in ["runtime", "agents", "automation", "skills"]:
                (target / dirname).mkdir()
            for skill_name in ["sales-coach", "sales-crm-sync"]:
                skill_dir = target / "skills" / skill_name
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(
                    f'---\nname: "{skill_name}"\n---\n# {skill_name}\n',
                    encoding="utf-8",
                )

            architecture = detect_target_architecture(target)

            self.assertEqual(architecture["target_kind"], "runtime_skill_bundle")
            self.assertEqual(architecture["root_entry"], "AGENTS.md")
            self.assertEqual(architecture["skill_inventory"]["count"], 2)
            self.assertEqual(architecture["architecture_style"], "managed_runtime_skill_bundle")
            self.assertEqual(architecture["evolution_surface"]["status"], "needs_skill_diagnosis")
            self.assertIsNone(architecture["evolution_surface"]["instruction_placement"])
            self.assertIn(
                "AGENTS.md",
                [candidate["path"] for candidate in architecture["evolution_surface"]["candidates"]],
            )

    def test_detect_target_architecture_recognizes_hook_loaded_control_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "hooked-skill-system"
            target.mkdir()
            (target / ".codex-plugin").mkdir()
            (target / ".codex-plugin" / "plugin.json").write_text(
                '{"skills":"./skills/","hooks":"./hooks/hooks-codex.json"}',
                encoding="utf-8",
            )
            (target / "hooks").mkdir()
            (target / "hooks" / "hooks-codex.json").write_text(
                '{"hooks":{"session-start":"skills/session-bootstrap/SKILL.md"}}',
                encoding="utf-8",
            )
            (target / "hooks" / "session-start-codex").write_text(
                "#!/usr/bin/env bash\n# Load skills/session-bootstrap/SKILL.md at session start.\n",
                encoding="utf-8",
            )
            skill_dir = target / "skills" / "session-bootstrap"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: "session-bootstrap"\n---\n# Session Bootstrap\nUse at session start to load skills and route skill usage.\n',
                encoding="utf-8",
            )

            architecture = detect_target_architecture(target)

            self.assertEqual(architecture["target_kind"], "hooked_skill_bundle")
            self.assertEqual(architecture["architecture_style"], "plugin_hook_controlled_skill_system")
            self.assertEqual(architecture["evolution_surface"]["status"], "needs_skill_diagnosis")
            self.assertIsNone(architecture["evolution_surface"]["instruction_placement"])
            self.assertIn(
                "skills/session-bootstrap/SKILL.md",
                [candidate["path"] for candidate in architecture["evolution_surface"]["candidates"]],
            )
            self.assertIn("hooks/hooks-codex.json", architecture["evolution_surface"]["controller_files"])
            self.assertIn(".codex-plugin/plugin.json", architecture["plugin_manifests"])
            self.assertIn(
                "evolution surface diagnosis result",
                architecture["component_gaps"]["missing_concepts"],
            )
            self.assertEqual(architecture["integration"]["mode"], "bootstrap_skill")
            self.assertFalse(architecture["integration"]["native_skill_invocation_hook_installed"])
            self.assertTrue(
                architecture["integration"]["capabilities"]["plugin_lifecycle_hook"]["installed"]
            )

    def test_detect_target_architecture_marks_single_skill_as_prompt_runtime_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "single-skill"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "single-skill"\n---\n', encoding="utf-8")

            architecture = detect_target_architecture(target)

            self.assertEqual(architecture["target_kind"], "single_skill")
            self.assertEqual(architecture["integration"]["mode"], "prompt_runtime_check")
            self.assertFalse(architecture["integration"]["native_host_hook_installed"])

    def test_codex_plugin_with_empty_hooks_is_not_native_host_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "codex-plugin"
            target.mkdir()
            (target / ".codex-plugin").mkdir()
            (target / ".codex-plugin" / "plugin.json").write_text(
                '{"skills":"./skills/","hooks":{}}',
                encoding="utf-8",
            )
            skill_dir = target / "skills" / "using-example"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: "using-example"\n---\n# Bootstrap\nUse when starting a session.\n',
                encoding="utf-8",
            )

            architecture = detect_target_architecture(target)

            self.assertEqual(architecture["target_kind"], "skill_bundle")
            self.assertEqual(architecture["integration"]["mode"], "bootstrap_skill")
            self.assertFalse(architecture["integration"]["native_host_hook_installed"])

    def test_diagnose_skill_accepts_agents_root_runtime_skill_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            target = Path(tmp) / "sales-office-runtime-kit"
            target.mkdir()
            (target / "AGENTS.md").write_text("# Sales Office Runtime Instructions\n", encoding="utf-8")
            for dirname in ["runtime", "agents", "automation", "skills"]:
                (target / dirname).mkdir()
            skill_dir = target / "skills" / "sales-coach"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text('---\nname: "sales-coach"\n---\n', encoding="utf-8")
            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)

            def runner(args, cwd=None):
                if args[:3] == ["gh", "repo", "view"]:
                    return {
                        "returncode": 0,
                        "stdout": (
                            '{"nameWithOwner":"MetaInFLow/sales-office-runtime-kit",'
                            '"url":"https://github.com/MetaInFLow/sales-office-runtime-kit",'
                            '"visibility":"PRIVATE","viewerPermission":"WRITE",'
                            '"defaultBranchRef":{"name":"main"}}'
                        ),
                        "stderr": "",
                    }
                if args[:3] == ["gh", "release", "view"]:
                    return {"returncode": 1, "stdout": "", "stderr": "release not found"}
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_skill(
                target=target,
                repo="MetaInFLow/sales-office-runtime-kit",
                skill_name=None,
                home=home,
                runner=runner,
            )

            self.assertEqual(report["skill"]["target_kind"], "runtime_skill_bundle")
            self.assertEqual(report["skill"]["root_entry"], "AGENTS.md")
            self.assertFalse(report["skill"]["has_skill_md"])
            self.assertIsNone(report["skill"]["evolution_surface"]["instruction_placement"])
            self.assertEqual(report["skill"]["skill_inventory"]["count"], 1)
            self.assertEqual(report["repo"]["access"]["viewer_permission"], "WRITE")
            self.assertTrue(report["repo"]["access"]["can_write"])
            self.assertEqual(report["publication"]["visibility"], "PRIVATE")
            self.assertEqual(report["version"]["status"], "missing_version_requires_owner_choice")
            global_capability = report["skill"]["integration"]["capabilities"][
                "global_session_dispatcher"
            ]
            self.assertTrue(global_capability["installed"])
            self.assertEqual(global_capability["trust_status"], "pending_review")
            self.assertFalse(global_capability["native_enforced"])

    def test_classify_integration_mode_names_manual_command_as_non_runtime_integration(self):
        integration = classify_integration_mode(
            target_kind="single_skill",
            root_entry="SKILL.md",
            hook_files=[],
            plugin_manifests=[],
            skill_entries=[],
        )

        self.assertEqual(integration["mode"], "prompt_runtime_check")
        self.assertEqual(integration["manual_wrapper_command"], "not_runtime_integration")
        self.assertIn("prompt", integration["description"])


class WrapperManifestTest(unittest.TestCase):
    def test_write_and_load_wrapper_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            manifest = build_wrapper_manifest(
                repo="MetaInFLow/resume-screening",
                wrapper_version="v0.2.0",
                managed_files=["WRAPPER.md", "scripts/evozeus_wrapper_preflight.py"],
                install_links=["/Users/anthonyf/.codex/skills/resume-screening"],
            )

            action = write_wrapper_manifest(target, manifest)
            loaded = load_wrapper_manifest(target)

            self.assertIn("write", action)
            self.assertEqual(loaded["wrapper_repo"], "MetaInFLow/EvoZeus-CoEvolve")
            self.assertEqual(loaded["wrapper_version"], "v0.2.0")
            self.assertEqual(loaded["canonical_repo"], "MetaInFLow/resume-screening")
            self.assertEqual(loaded["managed_files"], ["WRAPPER.md", "scripts/evozeus_wrapper_preflight.py"])
            self.assertEqual(loaded["install_links"], ["/Users/anthonyf/.codex/skills/resume-screening"])
            self.assertEqual(loaded["integration"]["mode"], "prompt_runtime_check")
            self.assertFalse(loaded["integration"]["native_host_hook_installed"])
            self.assertEqual(loaded["hook_registration"]["codex"]["config_file"], ".codex/hooks.json")
            self.assertEqual(
                loaded["hook_registration"]["codex"]["hook_script"],
                CODEX_START_HOOK_SCRIPT,
            )
            self.assertEqual(loaded["layout_version"], 2)
            self.assertEqual(loaded["dashboard"]["deployment_mode"], "opt_in_github_pages")
            self.assertEqual(loaded["dashboard"]["enablement_variable"], "EVOZEUS_PAGES_ENABLED")
            self.assertEqual(loaded["onboarding"]["installation"]["mode"], "canonical_repo_symlink")
            self.assertFalse(loaded["onboarding"]["initialization"]["required"])
            self.assertFalse(loaded["onboarding"]["generated_child_skills"]["hooks_inherited"])

    def test_onboarding_contract_records_target_initialization_and_child_skill_lifecycle(self):
        onboarding = build_onboarding_contract(
            repo="MetaInFLow/factory-skill",
            skill_name="factory-skill",
            init_command="python3 scripts/init_company.py --company <name>",
            init_verification="python3 scripts/verify_company.py --company <name>",
            generates_child_skills=True,
        )
        manifest = build_wrapper_manifest(
            "MetaInFLow/factory-skill",
            "v0.9.0",
            [TARGET_ONBOARDING_GUIDE],
            [],
            onboarding=onboarding,
        )

        self.assertTrue(manifest["onboarding"]["initialization"]["required"])
        self.assertEqual(manifest["onboarding"]["initialization"]["owner"], "target_skill")
        self.assertIn("init_company.py", manifest["onboarding"]["initialization"]["command"])
        invocation = manifest["onboarding"]["invocation"]
        self.assertEqual(invocation["mode"], "host_skill_discovery")
        self.assertEqual(invocation["owner"], "target_skill")
        self.assertIn("factory-skill", invocation["instruction"])
        self.assertIn("consumer-project smoke test", invocation["verification"])
        children = manifest["onboarding"]["generated_child_skills"]
        self.assertTrue(children["supported"])
        self.assertFalse(children["hooks_inherited"])
        self.assertEqual(children["attachment"], "separate_wrapper_lifecycle")
        self.assertEqual(children["trust_review"], "/hooks")
        self.assertIn("consumer-project smoke test", children["verification"])

    def test_onboarding_contract_rejects_incomplete_required_initialization(self):
        with self.assertRaisesRegex(ValueError, "command and verification"):
            build_onboarding_contract(
                repo="MetaInFLow/factory-skill",
                skill_name="factory-skill",
                init_command="python3 scripts/init_company.py",
            )

    def test_preflight_rejects_invalid_onboarding_contract(self):
        manifest = build_wrapper_manifest("MetaInFLow/skill", "v0.9.0", [], [])
        manifest["onboarding"]["initialization"] = {
            "required": True,
            "owner": "target_skill",
            "command": None,
            "verification": None,
        }
        manifest["onboarding"]["generated_child_skills"]["hooks_inherited"] = True

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            check_onboarding_contract(manifest)

    def test_build_wrapper_manifest_records_codex_hook_registration_for_complete_harness(self):
        manifest = build_wrapper_manifest(
            "MetaInFLow/skill",
            "v0.7.0",
            [".evozeus-wrapper/WRAPPER.md", ".codex/hooks.json", CODEX_START_HOOK_SCRIPT],
            [],
        )

        self.assertEqual(manifest["integration"]["mode"], "prompt_runtime_check")
        self.assertFalse(manifest["integration"]["native_skill_invocation_hook_installed"])
        self.assertTrue(manifest["integration"]["codex_project_hook"])
        self.assertTrue(
            manifest["integration"]["capabilities"]["repo_maintenance_hook"]["installed"]
        )
        self.assertEqual(manifest["hook_registration"]["codex"]["event"], "SessionStart")
        self.assertEqual(manifest["hook_registration"]["codex"]["matcher"], "startup|resume")
        self.assertEqual(
            manifest["hook_registration"]["codex"]["capability"],
            "repo_maintenance_hook",
        )
        self.assertEqual(
            manifest["hook_registration"]["codex"]["scope"],
            "canonical_repository",
        )
        self.assertFalse(
            manifest["hook_registration"]["codex"]["covers_skill_invocation"]
        )
        self.assertEqual(
            manifest["hook_registration"]["codex"]["trust_status"],
            "pending_review",
        )

    def test_write_wrapper_manifest_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            first = build_wrapper_manifest("MetaInFLow/a", "v0.1.0", ["WRAPPER.md"], [])
            second = build_wrapper_manifest("MetaInFLow/b", "v0.2.0", ["WRAPPER.md"], [])

            write_wrapper_manifest(target, first)
            action = write_wrapper_manifest(target, second)
            loaded = load_wrapper_manifest(target)

            self.assertIn("skip existing", action)
            self.assertEqual(loaded["canonical_repo"], "MetaInFLow/a")


class SourceContractTest(unittest.TestCase):
    def test_source_contract_passes_for_manifest_pointer_and_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "canonical"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.1.0", ["WRAPPER.md"], []),
            )

            pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / "skill"
            pointer.parent.mkdir(parents=True)
            pointer.symlink_to(target)
            install = home / ".codex" / "skills" / "skill"
            install.parent.mkdir(parents=True)
            install.symlink_to(target)

            def runner(args, cwd=None):
                if len(args) >= 4 and args[0] == "git" and args[3] == "rev-parse":
                    return {"returncode": 0, "stdout": str(target) + "\n", "stderr": ""}
                if len(args) >= 4 and args[0] == "git" and args[3] == "remote":
                    return {"returncode": 0, "stdout": "https://github.com/MetaInFLow/skill.git\n", "stderr": ""}
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_source_contract(
                target=target,
                requested_repo=None,
                skill_name="skill",
                home=home,
                installs=[
                    {
                        "path": str(install),
                        "kind": "symlink",
                        "resolved_path": str(target.resolve()),
                    }
                ],
                runner=runner,
            )

            self.assertTrue(report["managed"])
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["canonical_repo"], "MetaInFLow/skill")
            self.assertEqual(report["projects_pointer"]["resolved_path"], str(target.resolve()))
            self.assertEqual(report["runtime_installs"][0]["source_contract"], "runtime_pointer_ok")

    def test_source_contract_accepts_manifested_plugin_subskill_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "canonical"
            subskill = target / "skills" / "engineering-everything"
            subskill.mkdir(parents=True)
            (subskill / "SKILL.md").write_text(
                '---\nname: "engineering-everything"\n---\n', encoding="utf-8"
            )
            install = home / ".codex" / "skills" / "engineering-everything"
            install.parent.mkdir(parents=True)
            install.symlink_to(subskill)
            write_wrapper_manifest(
                target,
                build_wrapper_manifest(
                    "MetaInFLow/engineering-everything",
                    "v0.11.4",
                    [".evozeus-wrapper/WRAPPER.md"],
                    [str(install)],
                    instruction_surface="skills/engineering-everything/SKILL.md",
                ),
            )
            pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / "engineering-everything"
            pointer.parent.mkdir(parents=True)
            pointer.symlink_to(target)

            def runner(args, cwd=None):
                if len(args) >= 4 and args[0] == "git" and args[3] == "rev-parse":
                    return {"returncode": 0, "stdout": str(target) + "\n", "stderr": ""}
                if len(args) >= 4 and args[0] == "git" and args[3] == "remote":
                    return {
                        "returncode": 0,
                        "stdout": "https://github.com/MetaInFLow/engineering-everything.git\n",
                        "stderr": "",
                    }
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_source_contract(
                target=target,
                requested_repo=None,
                skill_name="engineering-everything",
                home=home,
                installs=[
                    {
                        "path": str(install),
                        "kind": "symlink",
                        "resolved_path": str(subskill.resolve()),
                    }
                ],
                runner=runner,
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(
                report["runtime_installs"][0]["source_contract"],
                "runtime_subskill_pointer_ok",
            )
            self.assertEqual(
                runtime_pointer_scope(target, str(subskill.resolve())),
                "canonical_subskill",
            )
            self.assertEqual(
                preflight_runtime_pointer_scope(target, str(subskill.resolve())),
                "canonical_subskill",
            )

    def test_source_contract_errors_when_project_pointer_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            target = Path(tmp) / "canonical"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.1.0", ["WRAPPER.md"], []),
            )

            def runner(args, cwd=None):
                if len(args) >= 4 and args[0] == "git" and args[3] == "rev-parse":
                    return {"returncode": 0, "stdout": str(target) + "\n", "stderr": ""}
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_source_contract(
                target=target,
                requested_repo=None,
                skill_name="skill",
                home=home,
                installs=[],
                runner=runner,
            )

            self.assertEqual(report["status"], "error")
            self.assertTrue(any("project pointer is missing" in error for error in report["errors"]))

    def test_source_contract_warns_for_runtime_real_directory_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "canonical"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.1.0", ["WRAPPER.md"], []),
            )
            pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / "skill"
            pointer.parent.mkdir(parents=True)
            pointer.symlink_to(target)
            install = home / ".codex" / "skills" / "skill"
            install.mkdir(parents=True)

            def runner(args, cwd=None):
                if len(args) >= 4 and args[0] == "git" and args[3] == "rev-parse":
                    return {"returncode": 0, "stdout": str(target) + "\n", "stderr": ""}
                if len(args) >= 4 and args[0] == "git" and args[3] == "remote":
                    return {"returncode": 0, "stdout": "https://github.com/MetaInFLow/skill.git\n", "stderr": ""}
                return {"returncode": 1, "stdout": "", "stderr": ""}

            report = diagnose_source_contract(
                target=target,
                requested_repo=None,
                skill_name="skill",
                home=home,
                installs=[
                    {
                        "path": str(install),
                        "kind": "directory",
                        "resolved_path": str(install.resolve()),
                    }
                ],
                runner=runner,
            )

            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["runtime_installs"][0]["source_contract"], "runtime_real_directory_warning")
            self.assertTrue(any("real directory" in warning for warning in report["warnings"]))


class TransformPlanningTest(unittest.TestCase):
    def test_plan_transform_action_maps_diagnosis_to_mode(self):
        self.assertEqual(plan_transform_action("migration_required", True), "migrate_layout")
        self.assertEqual(plan_transform_action("missing", False), "bootstrap")
        self.assertEqual(plan_transform_action("missing", True), "adopt")
        self.assertEqual(plan_transform_action("partial", False), "repair")
        self.assertEqual(plan_transform_action("partial", True), "repair")
        self.assertEqual(plan_transform_action("complete", False), "verify")
        self.assertEqual(plan_transform_action("complete", True), "verify")
        self.assertEqual(plan_transform_action("missing", None), "needs_repo_check")


class ReinstallPlanningTest(unittest.TestCase):
    def test_plan_reinstall_creates_symlink_when_install_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("same", encoding="utf-8")

            plan = plan_reinstall("skill", canonical, home, ["codex"])

            self.assertEqual(plan["stage"], "publish_reinstall")
            self.assertEqual(plan["actions"][0]["action"], "create_symlink")
            self.assertEqual(plan["runtime_skill_installation"]["status"], "planned")
            self.assertEqual(plan["runtime_hook_installation"]["status"], "not_installed")
            self.assertEqual(
                plan["runtime_hook_installation"]["scope"],
                "all_registered_wrapped_skills",
            )

    def test_plan_reinstall_detects_already_linked_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("same", encoding="utf-8")
            install = home / ".codex" / "skills" / "skill"
            install.parent.mkdir(parents=True)
            install.symlink_to(canonical)

            plan = plan_reinstall("skill", canonical, home, ["codex"])

            self.assertEqual(plan["actions"][0]["action"], "already_linked")

    def test_plan_reinstall_archives_identical_real_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("same", encoding="utf-8")
            install = home / ".codex" / "skills" / "skill"
            install.mkdir(parents=True)
            (install / "SKILL.md").write_text("same", encoding="utf-8")

            plan = plan_reinstall("skill", canonical, home, ["codex"])

            self.assertEqual(plan["actions"][0]["action"], "archive_then_symlink")

    def test_plan_reinstall_requires_confirmation_for_different_real_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("canonical", encoding="utf-8")
            install = home / ".codex" / "skills" / "skill"
            install.mkdir(parents=True)
            (install / "SKILL.md").write_text("local edits", encoding="utf-8")

            plan = plan_reinstall("skill", canonical, home, ["codex"])

            self.assertEqual(plan["actions"][0]["action"], "needs_user_confirmation")

    def test_apply_reinstall_creates_missing_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("canonical", encoding="utf-8")

            report = apply_reinstall("skill", canonical, home, ["codex"])
            install = home / ".codex" / "skills" / "skill"

            self.assertEqual(report["status"], "applied")
            self.assertTrue(report["writes"])
            self.assertTrue(install.is_symlink())
            self.assertEqual(install.resolve(), canonical.resolve())
            self.assertEqual(report["actions"][0]["result"], "created_symlink")

    def test_apply_reinstall_relinks_wrong_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            old = Path(tmp) / "old"
            canonical.mkdir()
            old.mkdir()
            (canonical / "SKILL.md").write_text("canonical", encoding="utf-8")
            install = home / ".codex" / "skills" / "skill"
            install.parent.mkdir(parents=True)
            install.symlink_to(old)

            report = apply_reinstall("skill", canonical, home, ["codex"])

            self.assertEqual(report["status"], "applied")
            self.assertEqual(report["actions"][0]["result"], "relinked_symlink")
            self.assertEqual(install.resolve(), canonical.resolve())

    def test_apply_reinstall_prevalidates_all_actions_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("canonical", encoding="utf-8")
            agents_install = home / ".agents" / "skills" / "skill"
            agents_install.mkdir(parents=True)
            (agents_install / "SKILL.md").write_text("local edits", encoding="utf-8")

            report = apply_reinstall("skill", canonical, home, ["codex", "agents"])

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertFalse((home / ".codex" / "skills" / "skill").exists())
            self.assertTrue(agents_install.is_dir())
            self.assertFalse((home / ".evozeus" / "archives").exists())

    def test_apply_reinstall_archives_real_directory_after_explicit_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("canonical", encoding="utf-8")
            install = home / ".codex" / "skills" / "skill"
            install.mkdir(parents=True)
            (install / "SKILL.md").write_text("canonical", encoding="utf-8")

            report = apply_reinstall(
                "skill",
                canonical,
                home,
                ["codex"],
                approve_archive=True,
                archive_id="20260718T120000000000Z",
            )
            archived = Path(report["actions"][0]["archive_path"])

            self.assertEqual(report["status"], "applied")
            self.assertTrue(install.is_symlink())
            self.assertEqual(install.resolve(), canonical.resolve())
            self.assertTrue(archived.is_dir())
            self.assertEqual((archived / "SKILL.md").read_text(encoding="utf-8"), "canonical")
            self.assertTrue(
                archived.is_relative_to(home.resolve() / ".evozeus" / "archives" / "runtime-installs")
            )

    def test_apply_reinstall_requires_approval_for_different_real_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            canonical = Path(tmp) / "repo"
            canonical.mkdir()
            (canonical / "SKILL.md").write_text("canonical", encoding="utf-8")
            install = home / ".codex" / "skills" / "skill"
            install.mkdir(parents=True)
            (install / "SKILL.md").write_text("local edits", encoding="utf-8")

            blocked = apply_reinstall("skill", canonical, home, ["codex"])
            applied = apply_reinstall(
                "skill",
                canonical,
                home,
                ["codex"],
                approve_archive=True,
                archive_id="20260718T120000000000Z",
            )

            self.assertEqual(blocked["status"], "blocked")
            self.assertFalse(blocked["writes"])
            self.assertEqual(applied["status"], "applied")
            archived = Path(applied["actions"][0]["archive_path"])
            self.assertEqual((archived / "SKILL.md").read_text(encoding="utf-8"), "local edits")

    def test_apply_reinstall_requires_canonical_skill_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            missing = root / "missing"
            without_skill = root / "repo"
            without_skill.mkdir()

            with self.assertRaisesRegex(ValueError, "canonical path must be an existing directory"):
                apply_reinstall("skill", missing, home, ["codex"])
            with self.assertRaisesRegex(ValueError, "canonical path must contain SKILL.md"):
                apply_reinstall("skill", without_skill, home, ["codex"])

            self.assertFalse(home.exists())


class GlobalHookLifecycleTest(unittest.TestCase):
    def test_global_hook_plan_blocks_invalid_existing_hooks_json_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text("{not-json\n", encoding="utf-8")

            plan = plan_global_hook_install(home=home, wrapper_root=Path.cwd())

            self.assertEqual(plan["status"], "blocked")
            self.assertFalse(plan["writes"])
            self.assertTrue(plan["errors"])
            self.assertEqual(hooks_path.read_text(encoding="utf-8"), "{not-json\n")

    def test_global_hook_install_preserves_unrelated_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            unrelated = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "shell",
                            "hooks": [{"type": "command", "command": "python3 unrelated.py"}],
                        }
                    ]
                }
            }
            hooks_path.write_text(json.dumps(unrelated), encoding="utf-8")

            first = apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            second = apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            merged = json.loads(hooks_path.read_text(encoding="utf-8"))
            session_commands = [
                handler["command"]
                for entry in merged["hooks"]["SessionStart"]
                for handler in entry["hooks"]
            ]

            self.assertEqual(first["status"], "installed")
            self.assertEqual(second["status"], "already_installed")
            self.assertEqual(session_commands.count(GLOBAL_DISPATCHER_COMMAND), 1)
            self.assertIn("PreToolUse", merged["hooks"])
            self.assertEqual(read_global_hook_status(home)["trust_status"], "pending_review")

    def test_global_hook_uninstall_removes_only_evozeus_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            hooks_path = home / ".codex" / "hooks.json"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks["hooks"]["SessionStart"].append(
                {
                    "matcher": "startup",
                    "hooks": [{"type": "command", "command": "python3 keep.py"}],
                }
            )
            hooks_path.write_text(json.dumps(hooks), encoding="utf-8")

            report = apply_global_hook_uninstall(home=home, approve=True)
            remaining = json.loads(hooks_path.read_text(encoding="utf-8"))
            commands = [
                handler["command"]
                for entry in remaining["hooks"]["SessionStart"]
                for handler in entry["hooks"]
            ]

            self.assertEqual(report["status"], "uninstalled")
            self.assertNotIn(GLOBAL_DISPATCHER_COMMAND, commands)
            self.assertIn("python3 keep.py", commands)

    def test_global_hook_preserves_unrelated_handler_in_same_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "startup|resume",
                                    "hooks": [
                                        {"type": "command", "command": GLOBAL_DISPATCHER_COMMAND},
                                        {"type": "command", "command": "python3 keep-same-entry.py"},
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            installed = json.loads(hooks_path.read_text(encoding="utf-8"))
            installed_commands = [
                handler["command"]
                for entry in installed["hooks"]["SessionStart"]
                for handler in entry["hooks"]
            ]
            apply_global_hook_uninstall(home=home, approve=True)
            uninstalled = json.loads(hooks_path.read_text(encoding="utf-8"))
            uninstalled_commands = [
                handler["command"]
                for entry in uninstalled["hooks"]["SessionStart"]
                for handler in entry["hooks"]
            ]

            self.assertEqual(installed_commands.count(GLOBAL_DISPATCHER_COMMAND), 1)
            self.assertIn("python3 keep-same-entry.py", installed_commands)
            self.assertNotIn(GLOBAL_DISPATCHER_COMMAND, uninstalled_commands)
            self.assertIn("python3 keep-same-entry.py", uninstalled_commands)

    def test_global_hook_install_rolls_back_when_a_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            original_hooks = '{"hooks":{"PreToolUse":[]}}\n'
            hooks_path.write_text(original_hooks, encoding="utf-8")

            from scripts import evozeus_wrapper_global_hook as global_hook

            original_write = global_hook._atomic_write
            calls = 0

            def fail_second_write(path, data):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic write failure")
                return original_write(path, data)

            with patch(
                "scripts.evozeus_wrapper_global_hook._atomic_write",
                side_effect=fail_second_write,
            ), self.assertRaisesRegex(OSError, "synthetic write failure"):
                apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)

            self.assertEqual(hooks_path.read_text(encoding="utf-8"), original_hooks)
            self.assertFalse((home / ".evozeus/hooks/evozeus_wrapper_dispatcher.py").exists())
            self.assertFalse((home / ".evozeus/hooks/state.json").exists())

    def test_global_hook_trust_is_recorded_separately_after_explicit_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)

            pending = read_global_hook_status(home)
            recorded = record_global_hook_trust(home, status="trusted", approve=True)
            trusted = read_global_hook_status(home)

            self.assertEqual(pending["trust_status"], "pending_review")
            self.assertEqual(recorded["status"], "trusted")
            self.assertEqual(trusted["trust_status"], "trusted")

    def test_global_hook_cli_plans_and_installs_with_explicit_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            environment = {**os.environ, "HOME": str(home)}

            plan = subprocess.run(
                [
                    sys.executable,
                    "scripts/evozeus_wrapper.py",
                    "hook",
                    "global",
                    "plan",
                    "--json",
                ],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            install = subprocess.run(
                [
                    sys.executable,
                    "scripts/evozeus_wrapper.py",
                    "hook",
                    "global",
                    "install",
                    "--approve",
                    "--json",
                ],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(plan.returncode, 0, plan.stderr)
            self.assertEqual(json.loads(plan.stdout)["status"], "planned")
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertEqual(json.loads(install.stdout)["status"], "installed")
            self.assertTrue((home / ".codex/hooks.json").is_file())


class GlobalDispatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        template = Path("templates/global/evozeus_wrapper_dispatcher.py").resolve()
        spec = importlib.util.spec_from_file_location("evozeus_wrapper_global_dispatcher", template)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(cls.module)

    def create_wrapped_target(self, home: Path, name: str, version: str) -> Path:
        target = home.parent / f"canonical-{name}"
        target.mkdir()
        manifest = target / ".evozeus-wrapper" / "wrapper.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "canonical_repo": f"MetaInFLow/{name}",
                    "wrapper_version": version,
                }
            ),
            encoding="utf-8",
        )
        pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / name
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.symlink_to(target)
        return target

    def test_dispatcher_warns_and_allows_with_aggregate_count_when_targets_are_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            self.create_wrapped_target(home, "private-skill-a", "v0.9.1")
            self.create_wrapped_target(home, "private-skill-b", "v0.8.0")

            payload = self.module.evaluate_session_start(
                home=home,
                latest_resolver=lambda: {
                    "version": "v0.10.0",
                    "source": "test",
                    "error": None,
                },
            )
            serialized = json.dumps(payload, ensure_ascii=False)

            self.assertTrue(payload["continue"])
            self.assertIn("2 个 EvoZeus harness 落后", payload["systemMessage"])
            self.assertIn("正常业务继续", payload["systemMessage"])
            self.assertIn("用户明确请求", payload["systemMessage"])
            self.assertIn("创建分支或 worktree", payload["systemMessage"])
            self.assertIn("若本任务选中了落后 Skill", payload["systemMessage"])
            self.assertIn("当前 Harness 版本", payload["systemMessage"])
            self.assertIn(
                "next_action=continue_business_without_harness_writes",
                payload["hookSpecificOutput"]["additionalContext"],
            )
            self.assertIn(
                "selected_skill_notice=current_to_latest_advisory",
                payload["hookSpecificOutput"]["additionalContext"],
            )
            self.assertIn(
                "latest_harness_version=v0.10.0",
                payload["hookSpecificOutput"]["additionalContext"],
            )
            self.assertNotIn("private-skill", serialized)
            self.assertNotIn(str(home), serialized)

    def test_dispatcher_allows_when_all_targets_are_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            self.create_wrapped_target(home, "current-skill", "v0.10.0")

            payload = self.module.evaluate_session_start(
                home=home,
                latest_resolver=lambda: {
                    "version": "v0.10.0",
                    "source": "test",
                    "error": None,
                },
            )

            self.assertTrue(payload["continue"])
            self.assertIn("current", payload["systemMessage"])

    def test_dispatcher_uses_cached_latest_when_remote_lookup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cache = home / ".evozeus/cache/evozeus-wrapper-latest.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(
                json.dumps({"version": "v0.10.0", "checked_at_epoch": 1000}),
                encoding="utf-8",
            )

            resolution = self.module.resolve_latest_version(
                home=home,
                now_epoch=5000,
                environment={},
                fetcher=lambda: {"version": None, "url": None, "error": "offline"},
            )

            self.assertEqual(resolution["version"], "v0.10.0")
            self.assertEqual(resolution["source"], "stale_cache")

    def test_dispatcher_warns_and_allows_when_latest_is_unknown_without_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            self.create_wrapped_target(home, "offline-skill", "v0.9.1")

            payload = self.module.evaluate_session_start(
                home=home,
                latest_resolver=lambda: {
                    "version": None,
                    "source": "unavailable",
                    "error": "offline",
                },
            )

            self.assertTrue(payload["continue"])
            self.assertIn("unavailable", payload["systemMessage"])

    def test_dispatcher_blocks_deterministic_manifest_mismatch_without_private_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = self.create_wrapped_target(home, "mismatch-skill", "v0.10.0")
            manifest = target / ".evozeus-wrapper/wrapper.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["canonical_repo"] = "MetaInFLow/something-else"
            manifest.write_text(json.dumps(data), encoding="utf-8")

            payload = self.module.evaluate_session_start(
                home=home,
                latest_resolver=lambda: {
                    "version": "v0.10.0",
                    "source": "test",
                    "error": None,
                },
            )
            serialized = json.dumps(payload, ensure_ascii=False)

            self.assertFalse(payload["continue"])
            self.assertIn("本地 harness 注册异常", payload["stopReason"])
            self.assertNotIn("mismatch-skill", serialized)

    def test_dispatcher_rejects_non_symlink_project_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            pointer = home / ".evozeus/.projects/MetaInFLow/not-a-pointer"
            manifest = pointer / ".evozeus-wrapper/wrapper.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "canonical_repo": "MetaInFLow/not-a-pointer",
                        "wrapper_version": "v0.10.0",
                    }
                ),
                encoding="utf-8",
            )

            payload = self.module.evaluate_session_start(
                home=home,
                latest_resolver=lambda: self.fail(
                    "invalid local registration must block before remote lookup"
                ),
            )

            self.assertFalse(payload["continue"])
            self.assertIn("本地 harness 注册异常", payload["stopReason"])

    def test_installed_dispatcher_runs_from_consumer_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            consumer = Path(tmp) / "consumer-workspace"
            consumer.mkdir()
            self.create_wrapped_target(home, "consumer-skill", "v0.9.1")
            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            dispatcher = home / ".evozeus/hooks/evozeus_wrapper_dispatcher.py"

            result = subprocess.run(
                [sys.executable, str(dispatcher)],
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "source": "startup",
                        "cwd": str(consumer),
                    }
                ),
                text=True,
                capture_output=True,
                cwd=consumer,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "EVOZEUS_WRAPPER_LATEST_VERSION": "v0.10.0",
                },
                check=False,
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(payload["continue"])
            self.assertIn("1 个 EvoZeus harness 落后", payload["systemMessage"])
            self.assertIn("正常业务继续", payload["systemMessage"])

    def test_project_and_global_hooks_share_one_remote_lookup(self):
        project_template = Path(
            "templates/target/.codex/hooks/evozeus_wrapper_start_check.py"
        ).resolve()
        spec = importlib.util.spec_from_file_location(
            "evozeus_wrapper_project_hook_for_dedup",
            project_template,
        )
        project_module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(project_module)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            calls = []

            def fetch_latest():
                calls.append("remote")
                return {
                    "version": "v0.10.0",
                    "url": "https://example.test/v0.10.0",
                    "error": None,
                }

            project_resolution = project_module.resolve_latest_version(
                current="v0.9.1",
                environment={},
                fetcher=fetch_latest,
                home=home,
                now_epoch=1200,
            )
            global_resolution = self.module.resolve_latest_version(
                home=home,
                now_epoch=1200,
                environment={},
                fetcher=fetch_latest,
            )

        self.assertEqual(calls, ["remote"])
        self.assertEqual(project_resolution["source"], "github_latest_release")
        self.assertEqual(global_resolution["source"], "fresh_cache")


class UpgradeAllHarnessTest(unittest.TestCase):
    @staticmethod
    def latest_v010():
        return {"version": "v0.10.0", "source": "test", "error": None}

    @staticmethod
    def latest_v0114():
        return {"version": "v0.11.4", "source": "test", "error": None}

    @staticmethod
    def latest_v0121():
        return {"version": "v0.12.1", "source": "test", "error": None}

    @staticmethod
    def latest_v013():
        return {"version": "v0.13.1", "source": "test", "error": None}

    def create_wrapper_source(self, root: Path, version: str = "v0.10.0") -> Path:
        wrapper_root = root / "wrapper-source"
        wrapper_root.mkdir()
        (wrapper_root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [{version}] - 2026-07-20\n",
            encoding="utf-8",
        )
        for relative in (
            "scripts/evozeus_wrapper_preflight.py",
            "templates/global/evozeus_wrapper_dispatcher.py",
            "templates/target/.codex/hooks/evozeus_wrapper_start_check.py",
            "templates/target/.github/workflows/evozeus-wrapper-preflight.yml",
            "templates/target/docs/onboarding.md",
        ):
            source = Path(relative)
            destination = wrapper_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        return wrapper_root

    def create_upgrade_target(
        self,
        home: Path,
        name: str,
        version: str = "v0.9.1",
        *,
        initialize_git: bool = False,
    ) -> Path:
        target = home.parent / f"canonical-{name}"
        target.mkdir()
        (target / "SKILL.md").write_text(f"original {name}\n", encoding="utf-8")
        manifest = target / ".evozeus-wrapper/wrapper.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "canonical_repo": f"MetaInFLow/{name}",
                    "wrapper_version": version,
                    "layout_version": 2,
                    "instruction_surface": "SKILL.md",
                }
            ),
            encoding="utf-8",
        )
        pointer = home / ".evozeus/.projects/MetaInFLow" / name
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.symlink_to(target)
        if initialize_git:
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.name=EvoZeus Test",
                    "-c",
                    "user.email=evozeus@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
        return target.resolve()

    def test_upgrade_all_prevalidates_every_target_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            clean = self.create_upgrade_target(home, "clean")
            dirty = self.create_upgrade_target(home, "dirty")

            def fake_plan(target, latest_version, **kwargs):
                conflicts = ["target git worktree is not clean"] if target == dirty else []
                return {
                    "target": str(target),
                    "migration_required": True,
                    "can_apply": not conflicts,
                    "conflicts": conflicts,
                    "instruction_surface": "SKILL.md",
                    "migration_record": ".evozeus-wrapper/docs/migrations/refresh.md",
                    "moves": [],
                    "managed_file_refreshes": [],
                }

            with patch(
                "scripts.evozeus_wrapper_lifecycle.plan_target_layout_migration",
                side_effect=fake_plan,
            ):
                report = plan_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    latest_resolver=self.latest_v010,
                )

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertIn("original clean", (clean / "SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("original dirty", (dirty / "SKILL.md").read_text(encoding="utf-8"))

    def test_upgrade_all_blocks_incomplete_wrapper_source_before_target_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            self.create_upgrade_target(home, "source-contract")
            (wrapper_root / "scripts/evozeus_wrapper_preflight.py").unlink()

            report = plan_upgrade_all(
                home,
                wrapper_root,
                "v0.10.0",
                latest_resolver=self.latest_v010,
            )

            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("source is missing" in error for error in report["errors"]))

    def test_upgrade_all_rolls_back_every_modified_target_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            first = self.create_upgrade_target(home, "first")
            second = self.create_upgrade_target(home, "second")
            (first / "README.md").write_text("business first\n", encoding="utf-8")
            (second / "README.md").write_text("business second\n", encoding="utf-8")
            (first / "empty-business-dir").mkdir()

            def fake_plan(target, latest_version, **kwargs):
                return {
                    "target": str(target),
                    "migration_required": True,
                    "can_apply": True,
                    "conflicts": [],
                    "instruction_surface": "SKILL.md",
                    "migration_record": ".evozeus-wrapper/docs/migrations/refresh.md",
                    "moves": [],
                    "managed_file_refreshes": [],
                    "text_rewrite_candidates": ["README.md"],
                    "generated_cache_candidates": [],
                }

            def fake_migrate(target, latest_version, **kwargs):
                (target / "SKILL.md").write_text("modified\n", encoding="utf-8")
                (target / "README.md").write_text("modified business\n", encoding="utf-8")
                if target == second:
                    raise ValueError("synthetic migration failure")
                return {"writes": True, "changed_files": ["SKILL.md"]}

            with patch(
                "scripts.evozeus_wrapper_lifecycle.plan_target_layout_migration",
                side_effect=fake_plan,
            ), patch(
                "scripts.evozeus_wrapper_lifecycle.migrate_target_layout",
                side_effect=fake_migrate,
            ):
                report = apply_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    approve=True,
                    latest_resolver=self.latest_v010,
                )

            self.assertEqual(report["status"], "rolled_back")
            self.assertFalse(report["writes"])
            self.assertEqual((first / "SKILL.md").read_text(encoding="utf-8"), "original first\n")
            self.assertEqual((second / "SKILL.md").read_text(encoding="utf-8"), "original second\n")
            self.assertEqual((first / "README.md").read_text(encoding="utf-8"), "business first\n")
            self.assertEqual((second / "README.md").read_text(encoding="utf-8"), "business second\n")
            self.assertTrue((first / "empty-business-dir").is_dir())

    def test_upgrade_all_real_migration_restores_broad_rewrites_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            target = root / "canonical-legacy"
            target.mkdir()
            create_complete_legacy_target(target)
            readme = target / "README.md"
            readme.write_text("Use .evozeus_evoinfra for wrapper docs.\n", encoding="utf-8")
            original_readme = readme.read_bytes()
            pointer = home / ".evozeus/.projects/MetaInFLow/legacy-skill"
            pointer.parent.mkdir(parents=True)
            pointer.symlink_to(target)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.name=EvoZeus Test",
                    "-c",
                    "user.email=evozeus@example.invalid",
                    "commit",
                    "-qm",
                    "legacy fixture",
                ],
                check=True,
            )

            from scripts import evozeus_wrapper_lifecycle as lifecycle

            original_run_command = lifecycle.run_command

            def fail_post_validation(args, cwd=None):
                if len(args) > 2 and args[1].endswith("evozeus_wrapper_preflight.py"):
                    return {"returncode": 1, "stdout": "", "stderr": "synthetic validation failure"}
                return original_run_command(args, cwd)

            with patch(
                "scripts.evozeus_wrapper_lifecycle.run_command",
                side_effect=fail_post_validation,
            ):
                report = apply_upgrade_all(
                    home,
                    Path.cwd(),
                    "v0.13.1",
                    approve=True,
                    latest_resolver=self.latest_v013,
                )

            self.assertEqual(report["status"], "rolled_back")
            self.assertEqual(readme.read_bytes(), original_readme)
            self.assertTrue((target / ".evozeus_evoinfra/wrapper.json").is_file())
            self.assertFalse((target / ".evozeus-wrapper/wrapper.json").exists())
            git_status = subprocess.run(
                ["git", "-C", str(target), "status", "--porcelain"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(git_status.stdout, "")

    def test_upgrade_all_preserves_h1_instruction_surface_on_version_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            target = root / "canonical-h1"
            target.mkdir()
            create_complete_legacy_target(target)
            migrate_target_layout(
                target,
                latest_version="v0.10.0",
                today=date(2026, 7, 19),
                wrapper_root=Path.cwd(),
            )
            skill = target / "SKILL.md"
            business_block = (
                "# Target Skill Title\n\n"
                "Target-owned intro bytes.  \n\n"
                "## Business Logic\n\n"
                "PRESERVE-BUSINESS-BYTES https://example.test/tree/main/docs\n\n"
            )
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "## Business Logic\n\n"
                    "PRESERVE-BUSINESS-BYTES https://example.test/tree/main/docs\n\n",
                    business_block,
                    1,
                ),
                encoding="utf-8",
            )
            pointer = home / ".evozeus/.projects/MetaInFLow/legacy-skill"
            pointer.parent.mkdir(parents=True)
            pointer.symlink_to(target)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.name=EvoZeus Test",
                    "-c",
                    "user.email=evozeus@example.invalid",
                    "commit",
                    "-qm",
                    "h1 fixture",
                ],
                check=True,
            )

            report = apply_upgrade_all(
                home,
                Path.cwd(),
                "v0.13.1",
                approve=True,
                latest_resolver=self.latest_v013,
            )
            updated = skill.read_text(encoding="utf-8")

            self.assertEqual(report["status"], "applied")
            self.assertIn(business_block, updated)
            self.assertIn(
                "## EvoZeus-CoEvolve Version Refresh Note: v0.10.0 -> v0.13.1",
                updated,
            )
            self.assertIn("- Layout: `consolidated-v2 -> consolidated-v2`", updated)

    def test_upgrade_all_is_idempotent_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            target = self.create_upgrade_target(home, "current-after-upgrade")

            def fake_plan(target, latest_version, **kwargs):
                return {
                    "target": str(target),
                    "migration_required": True,
                    "can_apply": True,
                    "conflicts": [],
                    "instruction_surface": "SKILL.md",
                    "migration_record": ".evozeus-wrapper/docs/migrations/refresh.md",
                    "moves": [],
                    "managed_file_refreshes": [],
                }

            def fake_migrate(target, latest_version, **kwargs):
                manifest = target / ".evozeus-wrapper/wrapper.json"
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["wrapper_version"] = latest_version
                manifest.write_text(json.dumps(data), encoding="utf-8")
                return {"writes": True, "changed_files": [".evozeus-wrapper/wrapper.json"]}

            with patch(
                "scripts.evozeus_wrapper_lifecycle.plan_target_layout_migration",
                side_effect=fake_plan,
            ), patch(
                "scripts.evozeus_wrapper_lifecycle.migrate_target_layout",
                side_effect=fake_migrate,
            ):
                first = apply_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    approve=True,
                    latest_resolver=self.latest_v010,
                )
                second = apply_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    approve=True,
                    latest_resolver=self.latest_v010,
                )

            self.assertEqual(first["status"], "applied")
            self.assertEqual(second["status"], "up_to_date")
            manifest = json.loads(
                (target / ".evozeus-wrapper/wrapper.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["wrapper_version"], "v0.10.0")

    def test_upgrade_all_refreshes_an_installed_global_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            target = self.create_upgrade_target(home, "refresh-global")
            apply_global_hook_install(home, wrapper_root, approve=True)
            installed_dispatcher = home / ".evozeus/hooks/evozeus_wrapper_dispatcher.py"
            installed_dispatcher.write_text("# outdated dispatcher\n", encoding="utf-8")

            def fake_plan(target_path, latest_version, **kwargs):
                return {
                    "target": str(target_path),
                    "migration_required": True,
                    "can_apply": True,
                    "conflicts": [],
                    "instruction_surface": "SKILL.md",
                    "migration_record": ".evozeus-wrapper/docs/migrations/refresh.md",
                    "moves": [],
                    "managed_file_refreshes": [],
                }

            migration_sources = []

            def fake_migrate(target_path, latest_version, **kwargs):
                migration_sources.append(kwargs.get("wrapper_root"))
                manifest = target_path / ".evozeus-wrapper/wrapper.json"
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["wrapper_version"] = latest_version
                manifest.write_text(json.dumps(data), encoding="utf-8")
                return {"writes": True, "changed_files": [str(manifest)]}

            with patch(
                "scripts.evozeus_wrapper_lifecycle.plan_target_layout_migration",
                side_effect=fake_plan,
            ), patch(
                "scripts.evozeus_wrapper_lifecycle.migrate_target_layout",
                side_effect=fake_migrate,
            ):
                report = apply_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    approve=True,
                    latest_resolver=self.latest_v010,
                )

            self.assertEqual(report["status"], "applied")
            self.assertEqual(report["global_hook_refresh"]["status"], "installed")
            self.assertEqual(
                installed_dispatcher.read_bytes(),
                (wrapper_root / "templates/global/evozeus_wrapper_dispatcher.py").read_bytes(),
            )
            self.assertEqual(read_global_hook_status(home)["trust_status"], "pending_review")
            self.assertEqual(migration_sources, [wrapper_root.resolve()])
            self.assertEqual(
                json.loads(
                    (target / ".evozeus-wrapper/wrapper.json").read_text(encoding="utf-8")
                )["wrapper_version"],
                "v0.10.0",
            )

    def test_upgrade_all_cli_reports_up_to_date_for_empty_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/evozeus_wrapper.py",
                    "harness",
                    "upgrade-all",
                    "--latest-version",
                    "v0.10.0",
                    "--wrapper-root",
                    str(wrapper_root),
                    "--dry-run",
                    "--json",
                ],
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "EVOZEUS_WRAPPER_LATEST_VERSION": "v0.10.0",
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "up_to_date")

    def test_upgrade_all_cli_serializes_populated_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            self.create_upgrade_target(home, "planned-cli", initialize_git=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/evozeus_wrapper.py",
                    "harness",
                    "upgrade-all",
                    "--latest-version",
                    "v0.10.0",
                    "--wrapper-root",
                    str(wrapper_root),
                    "--dry-run",
                    "--json",
                ],
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "EVOZEUS_WRAPPER_LATEST_VERSION": "v0.10.0",
                },
                check=False,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["target_count"], 1)
            self.assertIsInstance(payload["targets"][0]["target"], str)

    def test_upgrade_all_requires_verifiable_clean_git_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            self.create_upgrade_target(home, "not-a-git-repo")

            report = plan_upgrade_all(
                home,
                wrapper_root,
                "v0.10.0",
                latest_resolver=self.latest_v010,
            )

            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("git worktree" in error for error in report["errors"]))

    def test_upgrade_all_rejects_non_authoritative_latest_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            self.create_upgrade_target(home, "stale-latest")

            report = plan_upgrade_all(
                home,
                wrapper_root,
                "v0.10.0",
                latest_resolver=lambda: {
                    "version": "v0.9.1",
                    "source": "test",
                    "error": None,
                },
            )

            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("authoritative" in error for error in report["errors"]))

    def test_upgrade_all_checks_authority_before_up_to_date_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root, version="v0.9.1")
            self.create_upgrade_target(home, "appears-current", version="v0.9.1")
            calls = []

            def resolve_latest():
                calls.append("resolved")
                return {"version": "v0.10.0", "source": "test", "error": None}

            report = plan_upgrade_all(
                home,
                wrapper_root,
                "v0.9.1",
                latest_resolver=resolve_latest,
            )

            self.assertEqual(calls, ["resolved"])
            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("authoritative" in error for error in report["errors"]))

    def test_upgrade_all_rejects_symlinked_write_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            target = self.create_upgrade_target(home, "symlink-write")
            external = root / "external-readme.md"
            external.write_text("outside\n", encoding="utf-8")
            (target / "README.md").symlink_to(external)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.name=EvoZeus Test",
                    "-c",
                    "user.email=evozeus@example.invalid",
                    "commit",
                    "-qm",
                    "symlink fixture",
                ],
                check=True,
            )

            report = plan_upgrade_all(
                home,
                wrapper_root,
                "v0.10.0",
                latest_resolver=self.latest_v010,
            )

            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("symlink" in error for error in report["errors"]))
            self.assertEqual(external.read_text(encoding="utf-8"), "outside\n")

    def test_direct_migrate_layout_rejects_symlinked_write_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            target = self.create_upgrade_target(home, "direct-symlink")
            external = root / "outside-direct.md"
            external.write_text("Use .evozeus_evoinfra outside.\n", encoding="utf-8")
            (target / "README.md").symlink_to(external)

            plan = plan_target_layout_migration(target, "v0.10.0")

            self.assertFalse(plan["can_apply"])
            self.assertTrue(any("symlink" in error for error in plan["conflicts"]))
            with self.assertRaisesRegex(ValueError, "symlink"):
                migrate_target_layout(target, "v0.10.0")
            self.assertEqual(
                external.read_text(encoding="utf-8"),
                "Use .evozeus_evoinfra outside.\n",
            )

    def test_upgrade_all_rejects_unwritable_target_before_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            target = self.create_upgrade_target(home, "unwritable", initialize_git=True)

            real_access = os.access

            def access(path, mode):
                return False if Path(path) == target else real_access(path, mode)

            with patch("scripts.evozeus_wrapper_global_hook.os.access", side_effect=access):
                report = plan_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    latest_resolver=self.latest_v010,
                )

            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("not writable" in error for error in report["errors"]))

    def test_upgrade_all_rejects_unwritable_destination_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            wrapper_root = self.create_wrapper_source(root)
            target = self.create_upgrade_target(home, "unwritable-parent", initialize_git=True)
            github = target / ".github"
            github.mkdir()

            real_access = os.access

            def access(path, mode):
                return False if Path(path) == github else real_access(path, mode)

            with patch("scripts.evozeus_wrapper_global_hook.os.access", side_effect=access):
                report = plan_upgrade_all(
                    home,
                    wrapper_root,
                    "v0.10.0",
                    latest_resolver=self.latest_v010,
                )

            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("parent is not writable" in error for error in report["errors"]))


class EvolutionAndUpgradePlanningTest(unittest.TestCase):
    def test_classify_pr_permission(self):
        self.assertEqual(classify_pr_permission(write=True, fork=True), "direct_pr")
        self.assertEqual(classify_pr_permission(write=False, fork=True), "fork_pr")
        self.assertEqual(classify_pr_permission(write=False, fork=False), "local_patch")

    def test_classify_wrapper_upgrade(self):
        self.assertEqual(classify_wrapper_upgrade("v0.1.0", "v0.1.0", managed_dirty=False), "up_to_date")
        self.assertEqual(classify_wrapper_upgrade("v0.1.0", "v0.1.1", managed_dirty=False), "auto_pr")
        self.assertEqual(classify_wrapper_upgrade("v0.1.0", "v0.2.0", managed_dirty=True), "needs_merge_review")
        self.assertEqual(classify_wrapper_upgrade("v0.1.0", "v1.0.0", managed_dirty=False), "requires_confirmation")
        self.assertEqual(classify_wrapper_upgrade("v0.2.0", "v0.1.0", managed_dirty=False), "local_ahead")

    def test_plan_harness_upgrade_reports_latest_unknown_instead_of_self_comparing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.6.0", [], []),
            )

            with patch(
                "scripts.evozeus_wrapper_lifecycle.read_latest_release",
                return_value={"exists": False, "tag": None, "error": "offline"},
            ):
                plan = plan_harness_upgrade(target=target)

            self.assertEqual(plan["current_version"], "v0.6.0")
            self.assertIsNone(plan["latest_version"])
            self.assertEqual(plan["latest_source"], "unavailable")
            self.assertEqual(plan["upgrade_status"], "latest_unknown")

    def test_plan_harness_upgrade_resolves_authoritative_github_latest_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.6.0", [], []),
            )

            with patch(
                "scripts.evozeus_wrapper_lifecycle.read_latest_release",
                return_value={"exists": True, "tag": "v0.8.0", "url": "https://example.test/v0.8.0"},
            ):
                plan = plan_harness_upgrade(target=target)

            self.assertEqual(plan["latest_version"], "v0.8.0")
            self.assertEqual(plan["latest_source"], "github_latest_release")
            self.assertIsNone(plan["latest_lookup_error"])
            self.assertEqual(plan["upgrade_status"], "auto_pr")

    def test_plan_harness_upgrade_reports_lookup_error_when_github_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.8.0", [], []),
            )

            with patch(
                "scripts.evozeus_wrapper_lifecycle.read_latest_release",
                return_value={"exists": False, "tag": None, "error": "network unavailable"},
            ):
                plan = plan_harness_upgrade(target=target)

            self.assertIsNone(plan["latest_version"])
            self.assertEqual(plan["latest_source"], "unavailable")
            self.assertEqual(plan["latest_lookup_error"], "network unavailable")
            self.assertEqual(plan["upgrade_status"], "latest_unknown")

    def test_plan_harness_upgrade_reports_up_to_date_only_from_authoritative_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.8.0", [], []),
            )

            with patch(
                "scripts.evozeus_wrapper_lifecycle.read_latest_release",
                return_value={"exists": True, "tag": "v0.8.0", "url": "https://example.test/v0.8.0"},
            ):
                plan = plan_harness_upgrade(target=target)

            self.assertEqual(plan["latest_source"], "github_latest_release")
            self.assertEqual(plan["upgrade_status"], "up_to_date")

    def test_plan_harness_upgrade_reports_local_ahead_of_authoritative_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.9.0", [], []),
            )

            with patch(
                "scripts.evozeus_wrapper_lifecycle.read_latest_release",
                return_value={"exists": True, "tag": "v0.8.0", "url": "https://example.test/v0.8.0"},
            ):
                plan = plan_harness_upgrade(target=target)

            self.assertEqual(plan["latest_source"], "github_latest_release")
            self.assertEqual(plan["upgrade_status"], "local_ahead")

    def test_plan_harness_upgrade_prioritizes_required_layout_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            legacy_manifest = target / LEGACY_TARGET_WRAPPER_MANIFEST
            legacy_manifest.parent.mkdir(parents=True)
            legacy_manifest.write_text(
                '{"canonical_repo":"MetaInFLow/skill","wrapper_version":"v0.7.0"}\n',
                encoding="utf-8",
            )

            plan = plan_harness_upgrade(target=target, latest_version="v0.7.0")

            self.assertTrue(plan["migration_required"])
            self.assertEqual(plan["upgrade_status"], "up_to_date")
            self.assertEqual(plan["recommended_action"], "migrate_layout")

    def test_plan_harness_upgrade_returns_append_only_migration_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            (target / "SKILL.md").write_text('---\nname: "skill"\n---\n', encoding="utf-8")
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.1.1", ["WRAPPER.md"], []),
            )

            plan = plan_harness_upgrade(
                target=target,
                latest_version="v0.2.0",
                managed_dirty=False,
                today=date(2026, 6, 27),
            )

            self.assertEqual(plan["upgrade_status"], "auto_pr")
            self.assertEqual(plan["current_version"], "v0.1.1")
            self.assertEqual(plan["latest_version"], "v0.2.0")
            self.assertEqual(plan["target_infra_dir"], ".evozeus-wrapper")
            self.assertEqual(plan["legacy_infra_dir"], ".evozeus_evoinfra")
            self.assertEqual(plan["oldest_infra_dir"], ".evozeus")
            self.assertEqual(plan["manifest_path"], TARGET_WRAPPER_MANIFEST)
            self.assertFalse(plan["legacy_manifest_detected"])
            self.assertFalse(plan["migration_required"])
            self.assertTrue(plan["append_only"])
            self.assertTrue(plan["status_check_first"])
            self.assertFalse(plan["requires_confirmation"])
            self.assertEqual(
                plan["migration"]["doc_path"],
                ".evozeus-wrapper/docs/migrations/2026-06-27-v0.1.1-to-v0.2.0.md",
            )
            self.assertEqual(plan["integration"]["mode"], "prompt_runtime_check")
            self.assertFalse(plan["integration"]["native_host_hook_installed"])
            self.assertIn("SKILL.md EvoZeus-CoEvolve status check section (front matter prelude)", plan["planned_files"])
            self.assertIn("SKILL.md EvoZeus-CoEvolve section or migration note (append only)", plan["planned_files"])
            self.assertIn(TARGET_MIGRATIONS_README, plan["planned_files"])
            self.assertIn(TARGET_WRAPPER_MANIFEST, plan["planned_files"])
            self.assertIn(".codex/hooks.json", plan["planned_files"])
            self.assertIn(CODEX_START_HOOK_SCRIPT, plan["planned_files"])

    def test_plan_harness_upgrade_requires_repair_when_manifest_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()

            plan = plan_harness_upgrade(
                target=target,
                latest_version="v0.2.0",
                today=date(2026, 6, 27),
            )

            self.assertEqual(plan["upgrade_status"], "missing_manifest")
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(plan["recommended_action"], "repair_or_adopt_before_upgrade")
            self.assertEqual(plan["migration"]["from_wrapper_version"], None)
            self.assertEqual(
                plan["migration"]["doc_path"],
                ".evozeus-wrapper/docs/migrations/2026-06-27-unknown-to-v0.2.0.md",
            )

    def test_plan_harness_upgrade_uses_agents_root_entry_for_runtime_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "kit"
            target.mkdir()
            (target / "AGENTS.md").write_text("# Runtime\n", encoding="utf-8")
            (target / "skills").mkdir()
            skill_dir = target / "skills" / "sales-coach"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text('---\nname: "sales-coach"\n---\n', encoding="utf-8")

            plan = plan_harness_upgrade(
                target=target,
                latest_version="v0.2.0",
                today=date(2026, 6, 27),
            )

            self.assertIn(
                "AGENTS.md EvoZeus-CoEvolve status check section (instruction surface prelude)",
                plan["planned_files"],
            )
            self.assertIn("AGENTS.md", plan["evolution_surface_policy"])
            self.assertNotIn("SKILL.md EvoZeus-CoEvolve status check section (front matter prelude)", plan["planned_files"])

    def test_plan_harness_upgrade_uses_hook_loaded_control_skill_for_plugin_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "hooked-skill-system"
            target.mkdir()
            (target / ".codex-plugin").mkdir()
            (target / ".codex-plugin" / "plugin.json").write_text(
                '{"skills":"./skills/","hooks":"./hooks/hooks-codex.json"}',
                encoding="utf-8",
            )
            (target / "hooks").mkdir()
            (target / "hooks" / "hooks-codex.json").write_text(
                '{"hooks":{"session-start":"skills/session-bootstrap/SKILL.md"}}',
                encoding="utf-8",
            )
            skill_dir = target / "skills" / "session-bootstrap"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: "session-bootstrap"\n---\n# Session Bootstrap\nUse at session start to load skills and route skill usage.\n',
                encoding="utf-8",
            )

            plan = plan_harness_upgrade(
                target=target,
                latest_version="v0.2.0",
                today=date(2026, 6, 27),
                instruction_surface="skills/session-bootstrap/SKILL.md",
            )

            self.assertIn(
                "skills/session-bootstrap/SKILL.md EvoZeus-CoEvolve status check section (instruction surface prelude)",
                plan["planned_files"],
            )
            self.assertIn("skills/session-bootstrap/SKILL.md", plan["evolution_surface_policy"])

    def test_codex_session_start_hook_adapter_reads_target_evoinfra_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            target.mkdir()
            write_wrapper_manifest(
                target,
                build_wrapper_manifest("MetaInFLow/skill", "v0.6.0", ["WRAPPER.md"], []),
            )
            hook_dir = (target / CODEX_START_HOOK_SCRIPT).parent
            hook_dir.mkdir(parents=True)
            template = Path("templates/target/.codex/hooks/evozeus_wrapper_start_check.py").read_text(
                encoding="utf-8"
            )
            adapter = target / CODEX_START_HOOK_SCRIPT
            adapter.write_text(template.replace("{{WRAPPER_VERSION}}", "v0.7.0"), encoding="utf-8")

            advisory = subprocess.run(
                [sys.executable, str(adapter)],
                input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                text=True,
                capture_output=True,
                cwd=target,
                env={**os.environ, "EVOZEUS_WRAPPER_LATEST_VERSION": "v0.7.0"},
                check=False,
            )
            advisory_payload = json.loads(advisory.stdout)

            self.assertEqual(advisory.returncode, 0)
            self.assertTrue(advisory_payload["continue"])
            self.assertIn("non-breaking upgrade", advisory_payload["systemMessage"])
            self.assertIn(
                "capability=repo_maintenance_hook",
                advisory_payload["hookSpecificOutput"]["additionalContext"],
            )
            self.assertIn(
                "scope=canonical_repository",
                advisory_payload["hookSpecificOutput"]["additionalContext"],
            )

            strict_env = {
                **os.environ,
                "EVOZEUS_WRAPPER_HOOK_ENFORCEMENT": "strict",
                "EVOZEUS_WRAPPER_LATEST_VERSION": "v0.7.0",
            }
            strict = subprocess.run(
                [sys.executable, str(adapter)],
                input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                text=True,
                capture_output=True,
                cwd=target,
                env=strict_env,
                check=False,
            )
            strict_payload = json.loads(strict.stdout)

            self.assertEqual(strict.returncode, 0)
            self.assertFalse(strict_payload["continue"])
            self.assertIn("wrapper harness", strict_payload["stopReason"])

    def test_codex_hook_resolves_latest_release_after_install(self):
        template_path = Path("templates/target/.codex/hooks/evozeus_wrapper_start_check.py").resolve()
        spec = importlib.util.spec_from_file_location("evozeus_wrapper_hook_template", template_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            result = module.resolve_latest_version(
                current="v0.7.0",
                environment={},
                fetcher=lambda: {
                    "version": "v0.8.0",
                    "url": "https://example.test/v0.8.0",
                    "error": None,
                },
                home=Path(tmp) / "home",
            )

        self.assertEqual(result["version"], "v0.8.0")
        self.assertEqual(result["source"], "github_latest_release")
        self.assertIsNone(result["error"])

    def test_codex_hook_reports_latest_unknown_when_remote_lookup_fails(self):
        template_path = Path("templates/target/.codex/hooks/evozeus_wrapper_start_check.py").resolve()
        spec = importlib.util.spec_from_file_location("evozeus_wrapper_hook_template_offline", template_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            result = module.resolve_latest_version(
                current="v0.8.0",
                environment={},
                fetcher=lambda: {"version": None, "url": None, "error": "offline"},
                home=Path(tmp) / "home",
            )

        self.assertIsNone(result["version"])
        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["error"], "offline")

    def test_codex_project_hook_reuses_fresh_global_dispatcher_cache(self):
        template_path = Path("templates/target/.codex/hooks/evozeus_wrapper_start_check.py").resolve()
        spec = importlib.util.spec_from_file_location("evozeus_wrapper_hook_template_cache", template_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cache = home / ".evozeus/cache/evozeus-wrapper-latest.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(
                json.dumps({"version": "v0.10.0", "checked_at_epoch": 1000}),
                encoding="utf-8",
            )

            result = module.resolve_latest_version(
                current="v0.9.1",
                environment={},
                fetcher=lambda: self.fail("fresh global cache should avoid another remote lookup"),
                home=home,
                now_epoch=1100,
            )

        self.assertEqual(result["version"], "v0.10.0")
        self.assertEqual(result["source"], "global_dispatcher_cache")

    def test_codex_project_hook_populates_shared_cache_after_remote_lookup(self):
        template_path = Path("templates/target/.codex/hooks/evozeus_wrapper_start_check.py").resolve()
        spec = importlib.util.spec_from_file_location("evozeus_wrapper_hook_template_write_cache", template_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            result = module.resolve_latest_version(
                current="v0.9.1",
                environment={},
                fetcher=lambda: {
                    "version": "v0.10.0",
                    "url": "https://example.test/v0.10.0",
                    "error": None,
                },
                home=home,
                now_epoch=1200,
            )
            cache = json.loads(
                (home / module.GLOBAL_CACHE_PATH).read_text(encoding="utf-8")
            )

        self.assertEqual(result["source"], "github_latest_release")
        self.assertEqual(cache, {"version": "v0.10.0", "checked_at_epoch": 1200})


if __name__ == "__main__":
    unittest.main()
