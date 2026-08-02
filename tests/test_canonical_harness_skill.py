import contextlib
import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.evozeus_wrapper_bootstrap import (
    build_evolution_section,
    build_status_section,
    build_wrapper_section,
    copy_templates,
    inject_evolution_method,
    validate_existing_manifest_for_attach,
)
from scripts.evozeus_wrapper_lifecycle import (
    HARNESS_ENTRY_BEGIN,
    HARNESS_ENTRY_END,
    HARNESS_SKILL_REQUIRED_TERMS,
    HARNESS_SKILL_VERSION,
    TARGET_HARNESS_SKILL,
    TARGET_PREFLIGHT_SCRIPT,
    TARGET_WRAPPER_MANIFEST,
    build_harness_activation_block,
    build_wrapper_manifest,
    canonical_harness_skill_text_valid,
    detect_target_architecture,
    migrate_instruction_surface_to_harness_entry,
    migrate_target_layout,
    plan_harness_upgrade,
    plan_target_layout_migration,
    validate_instruction_surface_for_harness_entry,
    write_wrapper_manifest,
)
from scripts.evozeus_wrapper_preflight import (
    HARNESS_SKILL_TERMS,
    REQUIRED_FILES,
    check_harness_entry_contract,
    check_harness_skill_contract,
    discover_runtime_bundle,
)


ROOT = Path(__file__).resolve().parents[1]


def replacements() -> dict[str, str]:
    return {
        "DATE": "2026-07-31",
        "INITIAL_VERSION": "v0.1.0",
        "CURRENT_VERSION": "v0.1.0",
        "REPO_NAME": "MetaInFLow/example-skill",
        "REPO_URL": "https://github.com/MetaInFLow/example-skill",
        "SKILL_NAME": "example-skill",
        "VISIBILITY": "public",
        "WRAPPER_VERSION": "v0.14.0",
    }


def write_manifest(target: Path, *, surface: str = "SKILL.md", legacy: bool = False) -> dict:
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface=surface,
    )
    if legacy:
        manifest.pop("harness_skill_path", None)
        manifest.pop("harness_skill_version", None)
        manifest.pop("harness_skill_managed", None)
    write_wrapper_manifest(target, manifest, force=True)
    return manifest


def prepare_fresh_target(target: Path, *, surface: str = "SKILL.md", text: str | None = None) -> dict:
    entry = target / surface
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        text
        or '---\nname: "example-skill"\ndescription: Business Skill.\n---\n\n# Business Skill\n\nRun business flow.\n',
        encoding="utf-8",
    )
    copy_templates(target, replacements(), force=False)
    inject_evolution_method(target, replacements(), instruction_surface=surface)
    return write_manifest(target, surface=surface)


def legacy_skill_text(newline: str = "\n") -> str:
    values = replacements()
    business = (
        "# 企业 AI 场景诊断\n\n"
        "TRIGGER-BYTES: 诊断三个优先场景。  \n\n"
        "## 工作流\n\nKeep workflow bytes.\n\n"
        "## 示例调用\n\nKeep example bytes.\n"
    )
    text = (
        '---\nname: "example-skill"\ndescription: Business trigger.\n---\n\n'
        + build_status_section(values).rstrip()
        + "\n\n"
        + business
        + "\n"
        + build_evolution_section(values).rstrip()
        + "\n\n"
        + build_wrapper_section(values).rstrip()
        + "\n\n## EvoZeus-CoEvolve Version Refresh Note: v0.13.0 -> v0.14.0\n\n"
        + "- Wrapper harness: `v0.13.0 -> v0.14.0`\n"
        + "- Layout: `consolidated-v2 -> consolidated-v2`\n"
        + "- Target business rules were preserved.\n"
    )
    return text.replace("\n", newline)


def run_structure(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(target / TARGET_PREFLIGHT_SCRIPT),
            "structure",
            "--target",
            str(target),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_fresh_attach_writes_one_canonical_harness_skill_and_compact_entry(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)

    entry = (target / "SKILL.md").read_text(encoding="utf-8")
    harness = (target / TARGET_HARNESS_SKILL).read_text(encoding="utf-8")
    block = entry[entry.index(HARNESS_ENTRY_BEGIN) : entry.index(HARNESS_ENTRY_END) + len(HARNESS_ENTRY_END)]

    assert len(block.splitlines()) <= 8
    assert entry.count(HARNESS_ENTRY_BEGIN) == 1
    assert "## EvoZeus-CoEvolve 状态检查" not in entry
    assert "## 自进化方法" not in entry
    assert "# Business Skill\n\nRun business flow." in entry
    assert manifest["harness_skill_path"] == TARGET_HARNESS_SKILL
    assert manifest["harness_skill_version"] == HARNESS_SKILL_VERSION
    assert manifest["harness_skill_managed"] is True
    assert "MetaInFLow/example-skill" not in harness
    assert "prompt_runtime_check" in harness
    assert "SkillInvoke" in harness
    assert run_structure(target).returncode == 0


def test_attach_rejects_an_existing_incompatible_harness_skill_before_any_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    original_skill = '# Business Skill\n\nOwner content.\n'
    skill.write_text(original_skill, encoding="utf-8")
    harness = target / TARGET_HARNESS_SKILL
    harness.parent.mkdir(parents=True)
    original_harness = "# Owner file at the reserved Harness path\n"
    harness.write_text(original_harness, encoding="utf-8")

    with pytest.raises(ValueError, match="existing canonical Harness Skill is incompatible"):
        copy_templates(target, replacements(), force=False)

    assert skill.read_text(encoding="utf-8") == original_skill
    assert harness.read_text(encoding="utf-8") == original_harness
    assert not (target / ".evozeus-wrapper/wrapper.json").exists()
    assert not (target / ".github").exists()

    copy_templates(target, replacements(), force=True)
    assert "name: using-evozeus-harness" in harness.read_text(encoding="utf-8")


def test_force_repair_rejects_a_harness_symlink_without_touching_its_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text("# Business Skill\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("OWNER BYTES\n", encoding="utf-8")
    harness = target / TARGET_HARNESS_SKILL
    harness.parent.mkdir(parents=True)
    harness.symlink_to(outside)

    with pytest.raises(ValueError, match="template destination contains a symlink component"):
        copy_templates(target, replacements(), force=True)

    assert harness.is_symlink()
    assert outside.read_text(encoding="utf-8") == "OWNER BYTES\n"
    assert not (target / ".evozeus-wrapper/wrapper.json").exists()
    assert not (target / ".github").exists()


def test_attach_rejects_a_symlinked_harness_parent_without_writing_outside_repo(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text("# Business Skill\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / ".evozeus-wrapper").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="template destination contains a symlink component"):
        copy_templates(target, replacements(), force=True)

    assert list(outside.iterdir()) == []
    assert not (target / ".github").exists()


def test_attach_preflights_every_template_destination_before_any_write(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text("# Business Skill\n", encoding="utf-8")
    outside = tmp_path / "outside-github"
    outside.mkdir()
    (target / ".github").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="template destination contains a symlink component"):
        copy_templates(target, replacements(), force=False)

    assert list(outside.iterdir()) == []
    assert not (target / ".evozeus-wrapper").exists()
    assert not (target / ".codex").exists()


def test_attach_rejects_a_non_directory_template_parent_before_any_write(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text("# Business Skill\n", encoding="utf-8")
    wrapper = target / ".evozeus-wrapper"
    wrapper.mkdir()
    (wrapper / "skills").write_text("OWNER FILE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="template destination parent is not a directory"):
        copy_templates(target, replacements(), force=False)

    assert (wrapper / "skills").read_text(encoding="utf-8") == "OWNER FILE\n"
    assert not (target / ".github").exists()
    assert not (target / ".codex").exists()


@pytest.mark.parametrize(
    "damaged_entry",
    [
        f"{HARNESS_ENTRY_BEGIN}\n{HARNESS_ENTRY_BEGIN}\n{HARNESS_ENTRY_END}\n{HARNESS_ENTRY_END}",
        f"{HARNESS_ENTRY_BEGIN}\n{HARNESS_ENTRY_END}\n{HARNESS_ENTRY_END}\n{HARNESS_ENTRY_BEGIN}",
    ],
)
def test_attach_preflight_rejects_nested_or_interleaved_harness_markers(
    tmp_path: Path,
    damaged_entry: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    original = damaged_entry + "\n\n# Business Skill\n"
    skill.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="unbalanced canonical Harness entry"):
        validate_instruction_surface_for_harness_entry(target, "SKILL.md")

    assert skill.read_text(encoding="utf-8") == original
    assert not (target / ".evozeus-wrapper").exists()
    assert not (target / ".github").exists()


def test_attach_preflight_rejects_a_truncated_owned_surface_before_template_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    original = (
        "## 自进化方法\n\n"
        "本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环。\n\n"
        "Owner business text must survive.\n"
    )
    skill.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be migrated safely"):
        validate_instruction_surface_for_harness_entry(target, "SKILL.md")

    assert skill.read_text(encoding="utf-8") == original
    assert not (target / ".evozeus-wrapper").exists()
    assert not (target / ".github").exists()


def test_attach_preflight_routes_an_existing_legacy_manifest_to_migration(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    original_skill = legacy_skill_text()
    skill.write_text(original_skill, encoding="utf-8")
    manifest = write_manifest(target, legacy=True)
    manifest_path = target / TARGET_WRAPPER_MANIFEST
    original_manifest = manifest_path.read_bytes()

    with pytest.raises(ValueError, match="requires migrate-layout before attach"):
        validate_existing_manifest_for_attach(target, manifest["canonical_repo"])

    assert skill.read_text(encoding="utf-8") == original_skill
    assert manifest_path.read_bytes() == original_manifest
    assert not (target / TARGET_HARNESS_SKILL).exists()
    assert not (target / ".github").exists()


def test_attach_preflight_allows_an_idempotent_canonical_manifest(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)

    validate_existing_manifest_for_attach(target, manifest["canonical_repo"])


def test_attach_preflight_requires_harness_in_managed_files(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)
    manifest["managed_files"].remove(TARGET_HARNESS_SKILL)
    (target / TARGET_WRAPPER_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="managed_files"):
        validate_existing_manifest_for_attach(target, manifest["canonical_repo"])


def test_harness_skill_routes_low_frequency_intents_without_expanding_authority() -> None:
    harness = (
        ROOT
        / "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md"
    ).read_text(encoding="utf-8")

    for term in ["Feedback Issue", "Issue-to-PR", "Harness 维护", "UAT", "Release", "rollback"]:
        assert term in harness
    assert "普通 Skill 调用不授权" in harness
    assert "runtime-only install" in harness
    assert "{{REPO_NAME}}" not in harness
    assert "{{WRAPPER_VERSION}}" not in harness
    assert "当前能力为 `prompt_runtime_check`" not in harness
    assert "`prompt_runtime_check`" in harness
    assert "`bootstrap_skill`" in harness
    assert "integration.capabilities" in harness
    assert "禁止把它当作 `--target` 路径" in harness
    assert tuple(HARNESS_SKILL_TERMS) == HARNESS_SKILL_REQUIRED_TERMS


@pytest.mark.parametrize(
    ("surface", "source"),
    [
        ("SKILL.md", '---\nname: "single"\n---\n\n# Single\n\nBUSINESS\n'),
        ("AGENTS.md", "# Runtime Agents\n\nBUSINESS\n"),
        (
            "skills/session-bootstrap/SKILL.md",
            '---\nname: "session-bootstrap"\n---\n\n# Session Bootstrap\n\nBUSINESS\n',
        ),
    ],
)
def test_single_agents_and_hooked_surfaces_use_the_same_activation_pattern(
    tmp_path: Path,
    surface: str,
    source: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    manifest = prepare_fresh_target(target, surface=surface, text=source)
    updated = (target / surface).read_text(encoding="utf-8")

    assert updated.count(HARNESS_ENTRY_BEGIN) == 1
    assert "BUSINESS" in updated
    check_harness_skill_contract(target, manifest, allow_legacy=False)
    check_harness_entry_contract(target, manifest)


def test_prompt_and_plugin_bootstrap_targets_keep_integration_facts_in_manifest(
    tmp_path: Path,
) -> None:
    prompt_target = tmp_path / "prompt-target"
    prompt_target.mkdir()
    prompt_manifest = prepare_fresh_target(prompt_target)

    plugin_target = tmp_path / "plugin-target"
    plugin_target.mkdir()
    surface = "skills/session-bootstrap/SKILL.md"
    entry = plugin_target / surface
    entry.parent.mkdir(parents=True)
    entry.write_text(
        '---\nname: "session-bootstrap"\n---\n\n# Session Bootstrap\n\nRoute Skills.\n',
        encoding="utf-8",
    )
    plugin = plugin_target / ".codex-plugin/plugin.json"
    plugin.parent.mkdir()
    plugin.write_text('{"skills":"./skills/","hooks":"./hooks/hooks-codex.json"}\n', encoding="utf-8")
    hooks = plugin_target / "hooks/hooks-codex.json"
    hooks.parent.mkdir()
    hooks.write_text(
        '{"hooks":{"session-start":"skills/session-bootstrap/SKILL.md"}}\n',
        encoding="utf-8",
    )
    copy_templates(plugin_target, replacements(), force=False)
    inject_evolution_method(plugin_target, replacements(), instruction_surface=surface)
    architecture = detect_target_architecture(plugin_target)
    plugin_manifest = build_wrapper_manifest(
        "MetaInFLow/example-plugin",
        "v0.14.0",
        [],
        [],
        instruction_surface=surface,
        integration=architecture["integration"],
    )
    write_wrapper_manifest(plugin_target, plugin_manifest, force=True)

    harness = (plugin_target / TARGET_HARNESS_SKILL).read_text(encoding="utf-8")
    assert prompt_manifest["integration"]["mode"] == "prompt_runtime_check"
    assert plugin_manifest["integration"]["mode"] == "bootstrap_skill"
    assert "按 manifest 解释 integration 事实" in harness
    assert run_structure(prompt_target).returncode == 0
    assert run_structure(plugin_target).returncode == 0


def test_fresh_harness_commands_execute_from_repo_root_and_legacy_doctor_is_advisory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "remote",
            "add",
            "origin",
            "https://github.com/MetaInFLow/example-skill.git",
        ],
        check=True,
    )

    home = tmp_path / "home"
    pointer = home / ".evozeus/.projects/MetaInFLow/example-skill"
    pointer.parent.mkdir(parents=True)
    pointer.symlink_to(target)
    runtime_link = home / ".codex/skills/example-skill"
    runtime_link.parent.mkdir(parents=True)
    runtime_link.symlink_to(target)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
if args[:2] == ["repo", "view"]:
    print(json.dumps({"nameWithOwner": "MetaInFLow/example-skill", "url": "https://github.com/MetaInFLow/example-skill", "visibility": "PUBLIC"}))
    raise SystemExit(0)
if args[:2] == ["release", "view"]:
    print(json.dumps({"tagName": "v0.1.0", "url": "https://github.com/MetaInFLow/example-skill/releases/tag/v0.1.0"}))
    raise SystemExit(0)
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
    }
    preflight = target / TARGET_PREFLIGHT_SCRIPT
    commands = [
        [sys.executable, str(preflight), "structure", "--target", "."],
        [
            sys.executable,
            str(preflight),
            "doctor",
            "--target",
            ".",
        ],
        [sys.executable, str(preflight), "identity", "--target", ".", "--json"],
    ]
    results = [
        subprocess.run(
            command,
            cwd=target,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        for command in commands
    ]

    assert [result.returncode for result in results] == [0, 0, 0], [
        result.stderr for result in results
    ]
    identity = json.loads(results[2].stdout)
    assert identity["runtime_identity"]["display_line"].startswith("🧙🏻‍♂️")

    runtime_results = [
        subprocess.run(
            command,
            cwd=runtime_link,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        for command in commands
    ]
    assert [result.returncode for result in runtime_results] == [0, 0, 0], [
        result.stderr for result in runtime_results
    ]

    for field in ("harness_skill_path", "harness_skill_version", "harness_skill_managed"):
        manifest.pop(field)
    write_wrapper_manifest(target, manifest, force=True)
    incompatible_legacy = subprocess.run(
        commands[1],
        cwd=target,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert incompatible_legacy.returncode == 1
    assert "no compatible status prelude" in incompatible_legacy.stderr

    (target / "SKILL.md").write_text(legacy_skill_text(), encoding="utf-8")
    legacy_doctor = subprocess.run(
        commands[1],
        cwd=target,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert legacy_doctor.returncode == 0, legacy_doctor.stderr
    assert "migrate-layout" in legacy_doctor.stdout


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_legacy_three_block_migration_preserves_business_bytes_and_stops_note_growth(
    tmp_path: Path,
    newline: str,
) -> None:
    target = tmp_path / "diagnose-enterprise-ai-scenarios"
    target.mkdir()
    copy_templates(target, replacements(), force=False)
    original = legacy_skill_text(newline).encode("utf-8")
    business = (
        "# 企业 AI 场景诊断\n\n"
        "TRIGGER-BYTES: 诊断三个优先场景。  \n\n"
        "## 工作流\n\nKeep workflow bytes.\n\n"
        "## 示例调用\n\nKeep example bytes.\n"
    ).replace("\n", newline).encode("utf-8")
    (target / "SKILL.md").write_bytes(original)
    write_manifest(target, legacy=True)

    plan = plan_target_layout_migration(target, latest_version="v0.14.0", today=date(2026, 7, 31))
    report = migrate_target_layout(target, latest_version="v0.14.0", today=date(2026, 7, 31))
    updated = (target / "SKILL.md").read_bytes()

    assert plan["instruction_surface_migration_required"] is True
    assert report["writes"] is True
    assert business in updated
    assert updated.count(HARNESS_ENTRY_BEGIN.encode()) == 1
    assert "## EvoZeus-CoEvolve 状态检查".encode() not in updated
    assert "## 自进化方法".encode() not in updated
    assert b"Version Refresh Note" not in updated
    second = plan_target_layout_migration(target, latest_version="v0.14.0", today=date(2026, 8, 1))
    assert second["migration_required"] is False


def test_public_232_line_golden_fixture_preserves_business_contract_byte_for_byte(
    tmp_path: Path,
) -> None:
    fixture_dir = ROOT / "tests/fixtures/diagnose-enterprise-ai-scenarios"
    provenance = json.loads((fixture_dir / "source.json").read_text(encoding="utf-8"))
    original = (fixture_dir / "SKILL.md").read_bytes()
    assert provenance == {
        "schema_version": "evozeus.coevolve.public-golden-fixture.v1",
        "source_repo": "MetaInFLow/diagnose-enterprise-ai-scenarios",
        "source_url": "https://github.com/MetaInFLow/diagnose-enterprise-ai-scenarios",
        "source_commit": "ee2bd6e8232d14ad9070b4ee7875595e9e8e4a4f",
        "source_path": "SKILL.md",
        "sha256": "22b519a18fa4ec9b5ed1a892cd1895c1b68b84366c1286af8f8a403f35d79a04",
        "line_count": 232,
        "captured_at": "2026-07-31",
        "purpose": "Issue #38 canonical Harness Skill migration golden fixture",
    }
    assert hashlib.sha256(original).hexdigest() == provenance["sha256"]
    assert len(original.splitlines()) == provenance["line_count"]

    business_start = original.index("# 企业 AI 场景诊断".encode())
    business_end = original.index("## 自进化方法".encode(), business_start)
    business_contract = original[business_start:business_end]
    target = tmp_path / "diagnose-enterprise-ai-scenarios"
    target.mkdir()
    (target / "SKILL.md").write_bytes(original)
    values = {
        **replacements(),
        "REPO_NAME": "MetaInFLow/diagnose-enterprise-ai-scenarios",
        "REPO_URL": "https://github.com/MetaInFLow/diagnose-enterprise-ai-scenarios",
        "SKILL_NAME": "diagnose-enterprise-ai-scenarios",
    }
    copy_templates(target, values, force=False)
    manifest = build_wrapper_manifest(
        values["REPO_NAME"],
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )
    for field in ("harness_skill_path", "harness_skill_version", "harness_skill_managed"):
        manifest.pop(field)
    write_wrapper_manifest(target, manifest, force=True)

    report = migrate_target_layout(
        target,
        latest_version="v0.14.0",
        today=date(2026, 7, 31),
    )
    updated = (target / "SKILL.md").read_bytes()

    assert report["validation"]["structure"] == "passed"
    assert updated.count(business_contract) == 1
    for contract_heading in ["## 输入", "## 工作流", "## 输出合同", "## 质量门禁", "## 示例调用"]:
        assert contract_heading.encode() in business_contract
        assert contract_heading.encode() in updated
    assert updated.count(HARNESS_ENTRY_BEGIN.encode()) == 1
    assert "## EvoZeus-CoEvolve 状态检查".encode() not in updated
    assert "## 自进化方法".encode() not in updated
    assert "## EvoZeus-CoEvolve\n".encode() not in updated
    assert run_structure(target).returncode == 0


def test_target_owned_self_evolution_heading_is_not_deleted(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    source = (
        '---\nname: "example"\n---\n\n'
        "## 自进化方法\n\n"
        "TARGET-OWNED: improve examples after human review.\n"
    )
    (target / "SKILL.md").write_text(source, encoding="utf-8")

    changed = migrate_instruction_surface_to_harness_entry(target, "SKILL.md")
    updated = (target / "SKILL.md").read_text(encoding="utf-8")

    assert changed is True
    assert "## 自进化方法" in updated
    assert "TARGET-OWNED" in updated


def test_target_owned_heading_survives_when_a_later_wrapper_section_uses_same_heading(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    source = (
        '---\nname: "example"\n---\n\n'
        "## 自进化方法\n\n"
        "TARGET-OWNED: improve examples after human review.\n\n"
        + build_evolution_section(replacements()).rstrip()
        + "\n"
    )
    (target / "SKILL.md").write_text(source, encoding="utf-8")

    migrate_instruction_surface_to_harness_entry(target, "SKILL.md")
    updated = (target / "SKILL.md").read_text(encoding="utf-8")

    assert updated.count("## 自进化方法") == 1
    assert "TARGET-OWNED" in updated
    assert "本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环" not in updated


def test_legacy_status_migration_preserves_business_without_h1_or_h2_boundary(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    business = (
        "Business introduction must survive byte-for-byte.  \n\n"
        "### Workflow\n\n"
        "1. Keep this lower-level business section.\n"
    )
    source = (
        '---\nname: "example"\n---\n\n'
        + build_status_section(replacements()).rstrip()
        + "\n\n"
        + business
    )
    (target / "SKILL.md").write_text(source, encoding="utf-8")

    migrate_instruction_surface_to_harness_entry(target, "SKILL.md")
    updated = (target / "SKILL.md").read_text(encoding="utf-8")

    assert business in updated
    assert "## EvoZeus-CoEvolve 状态检查" not in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 1


@pytest.mark.parametrize(
    ("owned_section", "owned_marker"),
    [
        (
            build_evolution_section(replacements()),
            "本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环",
        ),
        (
            build_wrapper_section(replacements()),
            "本区由 EvoZeus-CoEvolve 追加",
        ),
        (
            "## EvoZeus-CoEvolve Version Refresh Note: v0.13.0 -> v0.14.0\n\n"
            "- Wrapper harness: `v0.13.0 -> v0.14.0`\n"
            "- Layout: `consolidated-v2 -> consolidated-v2`\n"
            "- Target business rules were preserved.\n",
            "Version Refresh Note",
        ),
    ],
    ids=["evolution", "wrapper", "migration-note"],
)
def test_wrapper_sections_stop_at_terminal_before_plain_or_h3_business(
    tmp_path: Path,
    owned_section: str,
    owned_marker: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    business = "Owner paragraph after managed section.  \n\n### Owner Details\n\nKeep owner bytes.\n"
    (target / "SKILL.md").write_text(
        '---\nname: "example"\n---\n\n' + owned_section.rstrip() + "\n\n" + business,
        encoding="utf-8",
    )

    migrate_instruction_surface_to_harness_entry(target, "SKILL.md")
    updated = (target / "SKILL.md").read_text(encoding="utf-8")

    assert business in updated
    assert owned_marker not in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 1


@pytest.mark.parametrize(
    "truncated_section",
    [
        "## 自进化方法\n\n本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环。\n",
        "## EvoZeus-CoEvolve\n\n本区由 EvoZeus-CoEvolve 追加，用来说明 wrapper harness。\n",
        (
            "## EvoZeus-CoEvolve Version Refresh Note: v0.13.0 -> v0.14.0\n\n"
            "- Wrapper harness: `v0.13.0 -> v0.14.0`\n"
            "- Layout: `consolidated-v2 -> consolidated-v2`\n"
        ),
    ],
    ids=["evolution", "wrapper", "migration-note"],
)
def test_owned_section_without_terminal_blocks_before_writing(
    tmp_path: Path,
    truncated_section: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    source = (
        '---\nname: "example"\n---\n\n'
        + truncated_section
        + "\nOwner paragraph must not be guessed into the managed span.\n"
    )
    (target / "SKILL.md").write_text(source, encoding="utf-8")
    copy_templates(target, replacements(), force=False)
    write_manifest(target, legacy=True)
    before = (target / "SKILL.md").read_bytes()

    plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    with pytest.raises(ValueError, match="terminal signature"):
        migrate_target_layout(target, latest_version="v0.14.0")

    assert any("terminal signature" in conflict for conflict in plan["conflicts"])
    assert plan["can_apply"] is False
    assert (target / "SKILL.md").read_bytes() == before


@pytest.mark.parametrize(
    "ambiguous_section",
    [
        (
            "## EvoZeus-CoEvolve 状态检查\n\n"
            "本段是 Skill 入口 preflight，事实源为 "
            ".evozeus-wrapper/wrapper.json。\n\n"
            "Owner business guidance must survive.\n"
            "解决方法：按客户规则处理。\n"
        ),
        (
            "## 自进化方法\n\n"
            "本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环。\n\n"
            "Owner business version example must survive.\n"
            "Wrapper harness version: `v0.14.0`\n"
        ),
        (
            "## EvoZeus-CoEvolve\n\n"
            "本区由 EvoZeus-CoEvolve 追加，用来说明 wrapper harness。\n\n"
            "Owner business mode example must survive.\n"
            "- `manual_only`：客户流程只能人工执行。\n"
        ),
        (
            "## EvoZeus-CoEvolve Version Refresh Note: v0.13.0 -> v0.14.0\n\n"
            "- Wrapper harness: `v0.13.0 -> v0.14.0`\n"
            "- Layout: `consolidated-v2 -> consolidated-v2`\n"
            "Owner business migration note must survive.\n"
            "- Target business rules were preserved.\n"
        ),
    ],
    ids=["status", "evolution", "wrapper", "migration-note"],
)
def test_business_terminal_text_cannot_complete_a_truncated_managed_section(
    tmp_path: Path,
    ambiguous_section: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text(
        '---\nname: "example"\n---\n\n' + ambiguous_section,
        encoding="utf-8",
    )
    copy_templates(target, replacements(), force=False)
    write_manifest(target, legacy=True)
    before = (target / "SKILL.md").read_bytes()

    plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    with pytest.raises(ValueError, match="terminal signature"):
        migrate_target_layout(target, latest_version="v0.14.0")

    assert any("terminal signature" in conflict for conflict in plan["conflicts"])
    assert plan["can_apply"] is False
    assert (target / "SKILL.md").read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("harness_skill_path", "/tmp/evil/SKILL.md", "canonical"),
        ("harness_skill_path", "../evil/SKILL.md", "canonical"),
        ("harness_skill_path", "C:\\evil\\SKILL.md", "canonical"),
        ("harness_skill_version", "v2.0.0", "incompatible"),
        ("harness_skill_managed", False, "managed"),
    ],
)
def test_manifest_rejects_unsafe_or_incompatible_harness_identity(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)
    manifest[field] = value

    with contextlib.redirect_stderr(io.StringIO()) as stderr, pytest.raises(SystemExit):
        check_harness_skill_contract(target, manifest, allow_legacy=False)

    assert message in stderr.getvalue()


def test_missing_damaged_mismatch_and_symlink_escape_are_deterministic(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)
    harness = target / TARGET_HARNESS_SKILL

    harness.unlink()
    with contextlib.redirect_stderr(io.StringIO()) as stderr, pytest.raises(SystemExit):
        check_harness_skill_contract(target, manifest, allow_legacy=False)
    assert "missing canonical Harness Skill" in stderr.getvalue()

    harness.parent.mkdir(parents=True, exist_ok=True)
    harness.write_text("# damaged\n", encoding="utf-8")
    damaged_plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    with contextlib.redirect_stderr(io.StringIO()) as stderr, pytest.raises(SystemExit):
        check_harness_skill_contract(target, manifest, allow_legacy=False)
    assert "frontmatter" in stderr.getvalue()
    assert damaged_plan["instruction_surface_migration_required"] is True

    harness.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    harness.symlink_to(outside)
    with contextlib.redirect_stderr(io.StringIO()) as stderr, pytest.raises(SystemExit):
        check_harness_skill_contract(target, manifest, allow_legacy=False)
    assert "symlink" in stderr.getvalue()

    harness.unlink()
    copy_templates(target, replacements(), force=True)
    entry = target / "SKILL.md"
    entry.write_text(
        entry.read_text(encoding="utf-8").replace(TARGET_HARNESS_SKILL, ".evozeus-wrapper/skills/other/SKILL.md"),
        encoding="utf-8",
    )
    with contextlib.redirect_stderr(io.StringIO()) as stderr, pytest.raises(SystemExit):
        check_harness_entry_contract(target, manifest)
    assert "does not match" in stderr.getvalue()


def test_missing_harness_frontmatter_boundary_routes_to_migration_and_repair(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    prepare_fresh_target(target)
    harness = target / TARGET_HARNESS_SKILL
    valid = harness.read_text(encoding="utf-8")
    assert valid.startswith("---\n")
    harness.write_text(valid.removeprefix("---\n"), encoding="utf-8")

    plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    assert plan["instruction_surface_migration_required"] is True
    assert plan["migration_required"] is True

    report = migrate_target_layout(target, latest_version="v0.14.0")
    assert report["writes"] is True
    assert harness.read_text(encoding="utf-8").startswith("---\n")
    assert run_structure(target).returncode == 0


def test_migration_preserves_an_unowned_canonical_harness_path(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text(legacy_skill_text(), encoding="utf-8")
    write_manifest(target, legacy=True)
    harness = target / TARGET_HARNESS_SKILL
    harness.parent.mkdir(parents=True, exist_ok=True)
    template = (
        ROOT
        / "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md"
    ).read_bytes()
    owner_bytes = template + b"\n# Owner customization at reserved path\n"
    harness.write_bytes(owner_bytes)
    assert canonical_harness_skill_text_valid(harness.read_text(encoding="utf-8"))
    skill_bytes = (target / "SKILL.md").read_bytes()

    plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    with pytest.raises(ValueError, match="not proven wrapper-managed"):
        migrate_target_layout(target, latest_version="v0.14.0")

    assert plan["can_apply"] is False
    assert any("not proven wrapper-managed" in item for item in plan["conflicts"])
    assert harness.read_bytes() == owner_bytes
    assert (target / "SKILL.md").read_bytes() == skill_bytes


def test_migration_rejects_a_non_directory_write_parent_before_any_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    skill.write_text(legacy_skill_text(), encoding="utf-8")
    write_manifest(target, legacy=True)
    blocker = target / ".evozeus-wrapper/skills"
    blocker.write_text("OWNER FILE\n", encoding="utf-8")
    skill_bytes = skill.read_bytes()
    manifest_bytes = (target / TARGET_WRAPPER_MANIFEST).read_bytes()

    plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    with pytest.raises(ValueError, match="parent is not a directory"):
        migrate_target_layout(target, latest_version="v0.14.0")

    assert plan["can_apply"] is False
    assert any("parent is not a directory" in item for item in plan["conflicts"])
    assert blocker.read_text(encoding="utf-8") == "OWNER FILE\n"
    assert skill.read_bytes() == skill_bytes
    assert (target / TARGET_WRAPPER_MANIFEST).read_bytes() == manifest_bytes


def test_preflight_rejects_reversed_harness_entry_markers_without_traceback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)
    entry = target / "SKILL.md"
    text = entry.read_text(encoding="utf-8")
    block = build_harness_activation_block()
    reversed_block = block.replace(HARNESS_ENTRY_BEGIN, "__BEGIN__").replace(
        HARNESS_ENTRY_END,
        HARNESS_ENTRY_BEGIN,
    ).replace("__BEGIN__", HARNESS_ENTRY_END)
    entry.write_text(text.replace(block, reversed_block), encoding="utf-8")

    with contextlib.redirect_stderr(io.StringIO()) as stderr, pytest.raises(SystemExit):
        check_harness_entry_contract(target, manifest)

    assert "exactly one canonical Harness Skill activation block" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_migration_preserves_harness_entry_examples_inside_fenced_code(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    fenced_example = (
        "```markdown\n"
        f"{HARNESS_ENTRY_BEGIN}\n"
        "Owner example content must survive.\n"
        f"{HARNESS_ENTRY_END}\n"
        "```\n"
    )
    skill = target / "SKILL.md"
    skill.write_text(
        '---\nname: "example"\n---\n\n# Business Skill\n\n' + fenced_example,
        encoding="utf-8",
    )

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert fenced_example in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert updated.count(HARNESS_ENTRY_END) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_migration_preserves_harness_entry_examples_inside_indented_code(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    indented_example = (
        f"    {HARNESS_ENTRY_BEGIN}\n"
        "    EXAMPLE BUSINESS BYTES\n"
        f"    {HARNESS_ENTRY_END}\n"
    )
    skill = target / "SKILL.md"
    skill.write_text(
        '---\nname: "example"\n---\n\n# Business Skill\n\n' + indented_example,
        encoding="utf-8",
    )

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert indented_example in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_migration_preserves_harness_entry_examples_inside_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    frontmatter = (
        "---\n"
        'name: "example"\n'
        "description: |\n"
        f"  {HARNESS_ENTRY_BEGIN}\n"
        "  EXAMPLE BUSINESS BYTES\n"
        f"  {HARNESS_ENTRY_END}\n"
        "---\n"
    )
    skill = target / "SKILL.md"
    skill.write_text(frontmatter + "\n# Business Skill\n", encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.startswith(frontmatter)
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_thematic_breaks_do_not_hide_or_duplicate_a_real_harness_entry(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    business_prefix = "---\n# Business introduction\n\n"
    business_suffix = "---\n\n# Business continuation\n"
    skill = target / "SKILL.md"
    skill.write_text(
        business_prefix + build_harness_activation_block() + "\n" + business_suffix,
        encoding="utf-8",
    )

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.count(HARNESS_ENTRY_BEGIN) == 1
    assert business_prefix + business_suffix in updated
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_invalid_flow_markdown_does_not_own_a_harness_entry_example(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    invalid_flow_example = (
        "---\n"
        "{key: [broken\n"
        f"{HARNESS_ENTRY_BEGIN}\n"
        "EXAMPLE BUSINESS BYTES\n"
        f"{HARNESS_ENTRY_END}\n"
        "}\n"
        "---\n"
        "\n# Business Skill\n"
    )
    skill = target / "SKILL.md"
    skill.write_text(invalid_flow_example, encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert invalid_flow_example in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_invalid_nested_flow_is_not_treated_as_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    business = "---\n{key: [broken,,value]}\n---\n\n# Business main flow\n"
    skill = target / "SKILL.md"
    skill.write_text(business, encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.startswith(build_harness_activation_block())
    assert business in updated
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_invalid_sequence_mapping_pair_is_not_treated_as_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    business = "---\n{key: [a: b: c]}\n---\n\n# Business main flow\n"
    skill = target / "SKILL.md"
    skill.write_text(business, encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.startswith(build_harness_activation_block())
    assert business in updated
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_adjacent_plain_and_collection_nodes_are_not_treated_as_frontmatter(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    business = (
        "---\n"
        "{key: {broken [value] ...}}\n"
        "# Business main flow\n"
        "---\n"
    )
    skill = target / "SKILL.md"
    skill.write_text(business, encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.startswith(build_harness_activation_block())
    assert business in updated
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_flow_frontmatter_preserves_an_exact_harness_entry_example(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    frontmatter = (
        "---\n"
        '{"key":[foo#bar],"description":"\n'
        + build_harness_activation_block()
        + '\n"}\n'
        + "---\n"
    )
    skill = target / "SKILL.md"
    skill.write_text(frontmatter + "\n# Business Skill\n", encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.startswith(frontmatter)
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


@pytest.mark.parametrize(
    "mapping_body",
    [
        "display name: Demo",
        "123: Demo",
        "显示名称: Demo",
        "{}",
        '{"name":"Demo"}',
        "{name:'Demo'}",
        '{\n"name":"Demo"\n}',
        "{a: b,# comment: ignored\nc: d}",
        "{key: [http://example.com: 80]}",
        '{urn:"foo": value}',
        "tags:\n- alpha",
        "!!map\n? complex key\n: Demo",
    ],
)
def test_migration_preserves_frontmatter_with_general_yaml_mapping_keys(
    tmp_path: Path,
    mapping_body: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    frontmatter = f"---\n{mapping_body}\n---\n"
    skill = target / "SKILL.md"
    skill.write_text(frontmatter + "\n# Business Skill\n", encoding="utf-8")

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert updated.startswith(frontmatter)
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


@pytest.mark.parametrize(
    "container_example",
    [
        (
            f"> {HARNESS_ENTRY_BEGIN}\n"
            "> EXAMPLE BUSINESS BYTES\n"
            f"> {HARNESS_ENTRY_END}\n"
        ),
        (
            "- Example entry:\n"
            f"  {HARNESS_ENTRY_BEGIN}\n"
            "  EXAMPLE BUSINESS BYTES\n"
            f"  {HARNESS_ENTRY_END}\n"
        ),
    ],
)
def test_migration_preserves_harness_entry_examples_inside_markdown_containers(
    tmp_path: Path,
    container_example: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    skill.write_text(
        '---\nname: "example"\n---\n\n# Business Skill\n\n' + container_example,
        encoding="utf-8",
    )

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert container_example in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


@pytest.mark.parametrize(
    "html_example",
    [
        (
            "<pre>\n"
            f"{HARNESS_ENTRY_BEGIN}\n"
            "EXAMPLE BUSINESS BYTES\n"
            f"{HARNESS_ENTRY_END}\n"
            "</pre>\n\n"
        ),
        (
            "<div>\n"
            f"{HARNESS_ENTRY_BEGIN}\n"
            "EXAMPLE BUSINESS BYTES\n"
            f"{HARNESS_ENTRY_END}\n"
            "</div>\n\n"
        ),
        (
            '<widget title="a > b">\n'
            f"{HARNESS_ENTRY_BEGIN}\n"
            "EXAMPLE BUSINESS BYTES\n"
            f"{HARNESS_ENTRY_END}\n"
            "</widget>\n\n"
        ),
    ],
)
def test_migration_preserves_harness_entry_examples_inside_raw_html_blocks(
    tmp_path: Path,
    html_example: str,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    skill = target / "SKILL.md"
    skill.write_text(
        '---\nname: "example"\n---\n\n# Business Skill\n\n' + html_example,
        encoding="utf-8",
    )

    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is True
    updated = skill.read_text(encoding="utf-8")
    manifest = build_wrapper_manifest(
        "MetaInFLow/example-skill",
        "v0.14.0",
        [],
        [],
        instruction_surface="SKILL.md",
    )

    assert html_example in updated
    assert updated.count(HARNESS_ENTRY_BEGIN) == 2
    assert migrate_instruction_surface_to_harness_entry(target, "SKILL.md") is False
    check_harness_entry_contract(target, manifest)


def test_runtime_bundle_requires_the_canonical_harness_skill_from_manifest_or_entry(
    tmp_path: Path,
) -> None:
    wrapped = tmp_path / "wrapped"
    wrapped.mkdir()
    prepare_fresh_target(wrapped)
    bundle = discover_runtime_bundle(wrapped)
    assert set(REQUIRED_FILES).issubset(bundle["required_files"])

    harness = wrapped / TARGET_HARNESS_SKILL
    harness.unlink()
    runtime = subprocess.run(
        [
            sys.executable,
            str(wrapped / TARGET_PREFLIGHT_SCRIPT),
            "runtime",
            "--target",
            str(wrapped),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert runtime.returncode == 1
    assert TARGET_HARNESS_SKILL in runtime.stderr

    standalone = tmp_path / "standalone-runtime"
    standalone.mkdir()
    (standalone / "SKILL.md").write_text(
        build_harness_activation_block() + "\n\n# Business\n",
        encoding="utf-8",
    )
    assert set(REQUIRED_FILES).issubset(discover_runtime_bundle(standalone)["required_files"])


def test_runtime_bundle_rejects_a_damaged_canonical_harness_contract(tmp_path: Path) -> None:
    wrapped = tmp_path / "wrapped"
    wrapped.mkdir()
    prepare_fresh_target(wrapped)
    (wrapped / TARGET_HARNESS_SKILL).write_text("BROKEN\n", encoding="utf-8")

    runtime = subprocess.run(
        [
            sys.executable,
            str(wrapped / TARGET_PREFLIGHT_SCRIPT),
            "runtime",
            "--target",
            str(wrapped),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert runtime.returncode == 1
    assert "frontmatter is missing or malformed" in runtime.stderr


@pytest.mark.parametrize(
    "dependency",
    [
        TARGET_WRAPPER_MANIFEST,
        TARGET_PREFLIGHT_SCRIPT,
        ".evozeus-wrapper/scripts/evozeus_notice.py",
        ".evozeus-wrapper/policies/notice-policy.json",
        ".github/workflows/evozeus-wrapper-preflight.yml",
    ],
)
def test_runtime_bundle_rejects_a_missing_harness_transitive_dependency(
    tmp_path: Path,
    dependency: str,
) -> None:
    wrapped = tmp_path / "wrapped"
    wrapped.mkdir()
    prepare_fresh_target(wrapped)
    (wrapped / dependency).unlink()

    runtime = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evozeus_wrapper_preflight.py"),
            "runtime",
            "--target",
            str(wrapped),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert runtime.returncode == 1
    assert dependency in runtime.stderr


def test_compatible_legacy_manifest_remains_advisory_for_doctor_contract(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text(legacy_skill_text(), encoding="utf-8")
    manifest = write_manifest(target, legacy=True)

    with contextlib.redirect_stdout(io.StringIO()) as stdout:
        state = check_harness_skill_contract(target, manifest, allow_legacy=True)

    assert state == "legacy_compatible"
    assert "migrate-layout" in stdout.getvalue()


def test_unbalanced_managed_entry_blocks_migration_before_any_write(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    prepare_fresh_target(target)
    entry = target / "SKILL.md"
    entry.write_text(
        entry.read_text(encoding="utf-8").replace(HARNESS_ENTRY_END, ""),
        encoding="utf-8",
    )
    before = entry.read_bytes()

    plan = plan_target_layout_migration(target, latest_version="v0.14.0")
    with pytest.raises(ValueError, match="unbalanced canonical Harness entry"):
        migrate_target_layout(target, latest_version="v0.14.0")

    assert any("unbalanced canonical Harness entry" in item for item in plan["conflicts"])
    assert entry.read_bytes() == before


def test_upgrade_plan_reports_one_entry_and_canonical_harness_skill_write(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text(legacy_skill_text(), encoding="utf-8")
    write_manifest(target, legacy=True)

    plan = plan_harness_upgrade(target, latest_version="v0.14.0", today=date(2026, 7, 31))

    assert plan["recommended_action"] == "migrate_layout"
    assert TARGET_HARNESS_SKILL in plan["planned_files"]
    assert "SKILL.md canonical Harness Skill activation block" in plan["planned_files"]
    assert all("migration note" not in item.lower() for item in plan["planned_files"])
    assert plan["append_only"] is False
    assert "#36" in plan["integration_policy"]


def test_transform_dry_run_reports_only_the_canonical_instruction_write_set(
    tmp_path: Path,
) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text('---\nname: "example"\n---\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(target)], check=True)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evozeus_wrapper.py"),
            "skill",
            "transform",
            "--mode",
            "attach",
            "--target",
            str(target),
            "--repo",
            "MetaInFLow/example-skill",
            "--visibility",
            "public",
            "--dry-run",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 0, result.stderr
    assert "SKILL.md canonical Harness Skill activation block" in report["planned_files"]
    assert report["planned_files"].count(TARGET_HARNESS_SKILL) == 1
    assert all("self-evolution section" not in item for item in report["planned_files"])
    assert all("status check section" not in item for item in report["planned_files"])


def test_consumer_smoke_follows_entry_to_read_only_harness_then_business(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    manifest = prepare_fresh_target(target)
    entry = (target / "SKILL.md").read_text(encoding="utf-8")
    harness = (target / manifest["harness_skill_path"]).read_text(encoding="utf-8")

    assert build_harness_activation_block().strip() in entry
    assert "structure --target ." in harness
    assert "doctor --target ." in harness
    assert "identity --target . --json" in harness
    assert "--target <canonical-repo>" not in harness
    assert "进入业务主链路" in harness
    assert "Run business flow." in entry
    assert run_structure(target).returncode == 0
