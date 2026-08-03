from __future__ import annotations

import copy
import hashlib
import json
import stat
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts import evozeus_harness_legacy_prompt_adapter as legacy_adapter
from scripts import evozeus_harness_migration as migration_kernel
from scripts import evozeus_wrapper_lifecycle as lifecycle


ROOT = Path(__file__).resolve().parents[1]
ENVELOPE_PATH = (
    ROOT
    / "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/envelope.json"
)
V1_CLOSURE_PATH = (
    ROOT
    / "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json"
)
MANIFEST_TEMPLATE_PATH = (
    ROOT
    / "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/artifacts/wrapper.json.tpl"
)
FROZEN_PREFLIGHT_PATH = (
    ROOT
    / "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/artifacts/scripts/evozeus_wrapper_preflight.py"
)
SURFACE_PATH = ROOT / "tests/fixtures/diagnose-enterprise-ai-scenarios/SKILL.md"
TARGET_REPOSITORY = "MetaInFLow/diagnose-enterprise-ai-scenarios"
TARGET_SKILL = "diagnose-enterprise-ai-scenarios"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _declared_mode(value: str) -> int:
    return int(value, 8) & 0o7777


def _write_file(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _render_manifest() -> bytes:
    return (
        MANIFEST_TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("{{APPLIED_AT}}", "2026-07-30")
        .replace("{{REPO_NAME}}", TARGET_REPOSITORY)
        .replace("{{SKILL_NAME}}", TARGET_SKILL)
        .encode("utf-8")
    )


def _trusted_development_bundle() -> dict[str, Any]:
    bundle = migration_kernel.load_migration_contract(ROOT)
    bundle["source_trust"] = {
        **bundle["source_trust"],
        "status": "trusted_release",
        "reasons": [],
    }
    return bundle


def _prepare_reviewed_legacy_target(
    tmp_path: Path,
    *,
    surface: bytes | None = None,
    name: str = "reviewed-legacy-target",
) -> Path:
    target = tmp_path / name
    target.mkdir()
    envelope = json.loads(ENVELOPE_PATH.read_text(encoding="utf-8"))
    v1_closure = json.loads(V1_CLOSURE_PATH.read_text(encoding="utf-8"))
    v1_entries = {
        item["target_path"]: item
        for item in v1_closure["files"]
        if item.get("kind") == "exact"
    }

    for item in envelope["files"]:
        relative = item["path"]
        kind = item["kind"]
        if kind == "absent":
            continue
        mode = _declared_mode(item["mode"])
        if kind == "rendered_preserve":
            data = f"REVIEWED-PRESERVE:{relative}\n".encode("utf-8")
        elif relative == lifecycle.TARGET_PREFLIGHT_SCRIPT:
            data = FROZEN_PREFLIGHT_PATH.read_bytes()
        else:
            closure_entry = v1_entries[relative]
            source = V1_CLOSURE_PATH.parent / closure_entry["artifact_path"]
            data = source.read_bytes()
        assert _sha256(data) == item.get("sha256", _sha256(data))
        _write_file(target / relative, data, mode)

    _write_file(
        target / lifecycle.TARGET_WRAPPER_MANIFEST,
        _render_manifest(),
        0o644,
    )
    _write_file(target / "SKILL.md", surface or SURFACE_PATH.read_bytes(), 0o644)

    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    _git(target, "config", "user.email", "legacy-plan-test@example.invalid")
    _git(target, "config", "user.name", "Legacy Plan Test")
    _git(target, "add", ".")
    _git(target, "commit", "-m", "Reviewed v0.14 legacy fixture")
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return target


def _target_snapshot(target: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in [target, *sorted(target.rglob("*"))]:
        relative = path.relative_to(target)
        if ".git" in relative.parts:
            continue
        metadata = path.lstat()
        key = relative.as_posix() or "."
        common = {
            "mode": stat.S_IMODE(metadata.st_mode),
            "st_dev": metadata.st_dev,
            "st_ino": metadata.st_ino,
        }
        if stat.S_ISDIR(metadata.st_mode):
            snapshot[key] = {"kind": "directory", **common}
        elif stat.S_ISREG(metadata.st_mode):
            data = path.read_bytes()
            snapshot[key] = {
                "kind": "file",
                **common,
                "sha256": _sha256(data),
                "length": len(data),
            }
        else:
            snapshot[key] = {"kind": "other", **common}
    return snapshot


def _plan(
    target: Path,
    *,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        today=date(2026, 8, 3),
        require_clean_git=True,
        _migration_bundle=bundle or _trusted_development_bundle(),
    )


def _supervised_profile(bundle: dict[str, Any]) -> dict[str, Any]:
    profiles = bundle["official_upgrade"]["supervised_legacy_profiles"]
    assert len(profiles) == 1
    return profiles[0]


def _assert_plan_only(plan: dict[str, Any]) -> None:
    assert plan["writes"] is False
    assert plan["can_apply"] is False
    assert plan["delete_set"] == []
    assert plan["move_set"] == []


def test_reviewed_lf_fixture_produces_contract_derived_zero_write_golden_plan(
    tmp_path: Path,
) -> None:
    target = _prepare_reviewed_legacy_target(tmp_path)
    bundle = _trusted_development_bundle()
    profile = _supervised_profile(bundle)
    before = _target_snapshot(target)

    first = _plan(target, bundle=bundle)
    second = _plan(target, bundle=bundle)

    _assert_plan_only(first)
    assert first["decision"] == "supervised_migration_available"
    assert first["compatibility_state"] == "reviewed_legacy"
    assert first["supervised_profile_authorized"] is True
    assert first["supervised_runtime_apply"] == "not_implemented"
    assert first["apply_blockers"] == [
        "supervised legacy runtime apply is not enabled by the verified profile"
    ]
    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["write_set"] == second["write_set"]
    assert _target_snapshot(target) == before

    expected_projection = profile["static_write_set"]
    assert [
        {"target_path": item["path"], "type": item["operation"]}
        for item in first["write_set"]
    ] == expected_projection
    assert len(first["write_set"]) == len(profile["operations"])
    assert first["supervised_profile_candidates"] == [first["profile"]]
    assert first["supervised_profile_matches"] == [first["profile"]]
    assert len(first["supervised_candidate_evidence"]) == 1
    assert first["supervised_candidate_evidence"][0]["decision"] == (
        "supervised_migration_available"
    )

    creates = [
        item for item in first["write_set"] if item["operation"] == "create_exact"
    ]
    assert creates
    assert all(item["preimage_sha256"] is None for item in creates)
    assert all(item["preimage_identity"] is None for item in creates)
    assert all(item["source_root"] == "repository_root" for item in creates)
    assert all(item["ownership"] == "wrapper_managed" for item in creates)

    by_path = {item["path"]: item for item in first["write_set"]}
    preflight = by_path[lifecycle.TARGET_PREFLIGHT_SCRIPT]
    preflight_operation = next(
        item
        for item in profile["operations"]
        if item["target_path"] == lifecycle.TARGET_PREFLIGHT_SCRIPT
    )
    assert preflight["preimage_sha256"] == (
        "sha256:0ef6e008461dc8e61845ad6deae5fe239122c2415d81550a1e9d6e9838570aa1"
    )
    assert preflight["preimage_mode"] == 0o755
    assert preflight["preimage_artifact"] == preflight_operation["preimage"][
        "artifact"
    ]
    assert preflight["preimage_identity"] == {
        "st_dev": (target / lifecycle.TARGET_PREFLIGHT_SCRIPT).stat().st_dev,
        "st_ino": (target / lifecycle.TARGET_PREFLIGHT_SCRIPT).stat().st_ino,
    }

    skill_write = by_path["SKILL.md"]
    assert skill_write["operation"] == "supervised_transform"
    assert skill_write["ownership"] == "adapter_proven_complement_bytes_exact"
    assert skill_write["postimage_sha256"] == (
        "sha256:0d44ec65677c0dad94491362541fc1fbaf67fecc89f2a917f4f6bb5a8e52bc29"
    )
    assert skill_write["retained_target_bytes"]["concatenated_sha256"] == (
        "sha256:3822b34e173d290cd2a93ccd083f706546dcc90c2069c77d4d9b3bcf74db8b2e"
    )
    assert len(skill_write["deleted_spans"]) == 3
    assert skill_write["zero_context_diff"]["text"].startswith("--- ")
    assert skill_write["preimage_identity"] == {
        "st_dev": (target / "SKILL.md").stat().st_dev,
        "st_ino": (target / "SKILL.md").stat().st_ino,
    }

    proof = first["supervised_adapter_proof"]
    assert proof["writes"] is False
    assert proof["destructive_authority"] is False
    assert proof["instruction_surface_transform"]["newline_style"] == "lf"
    assert proof["proof_sha256"] == skill_write["adapter_proof_sha256"]
    protected = first["protected_business_surfaces"]
    assert protected[0]["path"] == "SKILL.md"
    assert protected[0]["planned_write"] is True
    assert all(item["planned_write"] is False for item in protected[1:])


def test_reviewed_crlf_fixture_preserves_style_in_zero_write_plan(
    tmp_path: Path,
) -> None:
    crlf_surface = SURFACE_PATH.read_bytes().replace(b"\n", b"\r\n")
    target = _prepare_reviewed_legacy_target(
        tmp_path,
        surface=crlf_surface,
        name="reviewed-crlf-target",
    )
    before = _target_snapshot(target)

    plan = _plan(target)

    _assert_plan_only(plan)
    assert plan["decision"] == "supervised_migration_available"
    transform = plan["supervised_adapter_proof"]["instruction_surface_transform"]
    assert transform["newline_style"] == "crlf"
    assert transform["postimage_sha256"] == (
        "sha256:b01832ace3aa1de1c10fcf37771420c55a47164f73300cea9cb2878a1f465daa"
    )
    assert _target_snapshot(target) == before


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda value: value.replace(b"\n", b"\r\n", 1),
            id="mixed-lf-crlf",
        ),
        pytest.param(
            lambda value: value.replace(
                "# 企业 AI 场景诊断\n".encode(),
                (
                    "# 企业 AI 场景诊断\n\n"
                    "## EvoZeus-CoEvolve 状态检查\n\n"
                    "业务同名标题。\n"
                ).encode(),
                1,
            ),
            id="duplicate-visible-heading",
        ),
    ],
)
def test_ambiguous_reviewed_surface_fails_closed_without_a_write_plan(
    tmp_path: Path,
    mutate: Callable[[bytes], bytes],
) -> None:
    target = _prepare_reviewed_legacy_target(
        tmp_path,
        surface=mutate(SURFACE_PATH.read_bytes()),
    )
    before = _target_snapshot(target)

    plan = _plan(target)

    _assert_plan_only(plan)
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []
    assert plan["supervised_profile_matches"] == []
    evidence = plan["supervised_candidate_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["decision"] == "manual_migration_required"
    assert evidence[0]["proof"]["writes"] is False
    assert evidence[0]["proof"]["destructive_authority"] is False
    assert evidence[0]["proof"]["reasons"]
    assert _target_snapshot(target) == before


def test_dirty_reviewed_target_retains_exact_plan_but_blocks_apply(
    tmp_path: Path,
) -> None:
    target = _prepare_reviewed_legacy_target(tmp_path)
    preserve_path = target / ".evozeus-wrapper/CHANGELOG.md"
    preserve_path.write_bytes(preserve_path.read_bytes() + b"DIRTY\n")
    before = _target_snapshot(target)

    plan = _plan(target)

    _assert_plan_only(plan)
    assert plan["decision"] == "supervised_migration_available"
    assert plan["worktree_clean"] is False
    assert "target Git worktree is not clean" in plan["apply_blockers"]
    assert any(
        "commit or stash changes" in conflict for conflict in plan["conflicts"]
    )
    assert plan["write_set"]
    assert _target_snapshot(target) == before


def test_unbound_executing_adapter_fails_closed_before_write_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _prepare_reviewed_legacy_target(tmp_path)
    fake_implementation = tmp_path / "unbound_adapter.py"
    fake_implementation.write_bytes(b"def untrusted():\n    pass\n")
    monkeypatch.setattr(legacy_adapter, "__file__", str(fake_implementation))
    before = _target_snapshot(target)

    plan = _plan(target)

    _assert_plan_only(plan)
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []
    evidence = plan["supervised_candidate_evidence"]
    assert evidence[0]["proof"]["reasons"] == [
        "executing supervised legacy adapter is not release-bound"
    ]
    assert _target_snapshot(target) == before


@pytest.mark.parametrize("drift", ["profile-operation", "target-closure"])
def test_in_memory_verified_contract_drift_downgrades_to_manual_zero_write(
    tmp_path: Path,
    drift: str,
) -> None:
    target = _prepare_reviewed_legacy_target(tmp_path)
    bundle = copy.deepcopy(_trusted_development_bundle())
    profile = _supervised_profile(bundle)
    if drift == "profile-operation":
        profile["operations"][0]["postimage"]["sha256"] = "0" * 64
    else:
        closure_entry = next(
            item
            for item in bundle["current_closure"]["closure"]["files"]
            if item.get("kind") == "exact"
        )
        closure_entry["sha256"] = "0" * 64
    before = _target_snapshot(target)

    plan = _plan(target, bundle=bundle)

    _assert_plan_only(plan)
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []
    assert plan["conflicts"]
    assert any("supervised legacy" in item for item in plan["conflicts"])
    assert plan["protected_business_surfaces"] == [
        {
            "path": "SKILL.md",
            "rule": "byte_exact",
            "preimage_sha256": (
                "sha256:" + _sha256((target / "SKILL.md").read_bytes())
            ),
            "planned_write": False,
        }
    ]
    assert _target_snapshot(target) == before
