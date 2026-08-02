from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scripts import evozeus_harness_migration as migration_kernel
from scripts import evozeus_wrapper_bootstrap as bootstrap
from scripts import evozeus_wrapper_global_hook as global_hook
from scripts import evozeus_wrapper_lifecycle as lifecycle


ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "44d1fbdefc1e1de47a35c3ca39d2ba083661d569"
RELEASE_TAG = "v9.9.9"
OFFICIAL_URL = "https://github.com/MetaInFLow/EvoZeus-CoEvolve.git"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _replacement_values() -> dict[str, str]:
    return {
        "DATE": "2026-08-02",
        "INITIAL_VERSION": "v0.1.0",
        "CURRENT_VERSION": "v0.1.0",
        "REPO_NAME": "MetaInFLow/migration-target",
        "REPO_URL": "https://github.com/MetaInFLow/migration-target",
        "SKILL_NAME": "migration-target",
        "VISIBILITY": "public",
        "WRAPPER_VERSION": "v0.14.0",
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_release_source(
    tmp_path: Path,
    *,
    publish_tag: bool = True,
) -> Path:
    source = tmp_path / ("source-published" if publish_tag else "source-forged")
    remote = tmp_path / ("remote-published.git" if publish_tag else "remote-forged.git")
    source.mkdir()
    shutil.copytree(ROOT / "contracts", source / "contracts")
    skill_source = (
        source
        / "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md"
    )
    skill_source.parent.mkdir(parents=True)
    shutil.copy2(
        ROOT
        / "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md",
        skill_source,
    )
    preflight_source = source / "scripts/evozeus_wrapper_preflight.py"
    preflight_source.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/evozeus_wrapper_preflight.py", preflight_source)

    manifest_path = source / "contracts/v1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_revision"] = RELEASE_TAG
    _write_json(manifest_path, manifest)

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    _git(source, "config", "user.email", "migration-test@example.invalid")
    _git(source, "config", "user.name", "Migration Test")
    _git(source, "remote", "add", "origin", OFFICIAL_URL)
    _git(
        source,
        "config",
        f"url.file://{remote.resolve()}.insteadOf",
        OFFICIAL_URL,
    )
    _git(source, "add", ".")
    _git(source, "commit", "-m", "Synthetic immutable migration release")
    _git(source, "tag", RELEASE_TAG)
    if publish_tag:
        _git(source, "push", "origin", "HEAD:refs/heads/main", f"refs/tags/{RELEASE_TAG}")
    else:
        _git(source, "push", "origin", "HEAD:refs/heads/main")
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return source


def _old_preflight_bytes() -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{BASE_COMMIT}:scripts/evozeus_wrapper_preflight.py"]
    )


def _prepare_exact_v1_target(tmp_path: Path, name: str = "target") -> Path:
    target = tmp_path / name
    target.mkdir()
    target.joinpath("SKILL.md").write_text(
        '---\nname: "migration-target"\ndescription: Business contract.\n---\n\n'
        "# Migration Target\n\n"
        + lifecycle.build_harness_activation_block()
        + "\n\n## Business Workflow\n\nOWNER-BYTES-DO-NOT-CHANGE  \n",
        encoding="utf-8",
    )
    bootstrap.copy_templates(target, _replacement_values(), force=False)
    frozen_skill = (
        ROOT
        / "contracts/v1/migrations/artifacts/using-evozeus-harness-v1.0.0.md"
    ).read_bytes()
    target.joinpath(lifecycle.TARGET_HARNESS_SKILL).write_bytes(frozen_skill)
    target.joinpath(lifecycle.TARGET_PREFLIGHT_SCRIPT).write_bytes(_old_preflight_bytes())
    target.joinpath(lifecycle.TARGET_PREFLIGHT_SCRIPT).chmod(0o755)
    target.joinpath(lifecycle.TARGET_MIGRATION_CONTRACT).unlink()

    manifest = lifecycle.build_wrapper_manifest(
        "MetaInFLow/migration-target",
        "v0.14.0",
        lifecycle.WRAPPER_MANAGED_FILES,
        [],
        instruction_surface="SKILL.md",
    )
    manifest["harness_skill_version"] = "v1.0.0"
    manifest.pop("migration_contract", None)
    manifest.pop("managed_blocks", None)
    manifest["managed_files"] = [
        item
        for item in manifest["managed_files"]
        if item != lifecycle.TARGET_MIGRATION_CONTRACT
    ]
    _write_json(target / lifecycle.TARGET_WRAPPER_MANIFEST, manifest)

    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    _git(target, "config", "user.email", "target-test@example.invalid")
    _git(target, "config", "user.name", "Target Test")
    _git(target, "add", ".")
    _git(target, "commit", "-m", "Canonical Harness v1.0 fixture")
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return target


def _plan(target: Path, source: Path) -> dict[str, object]:
    return lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.14.0",
        today=date(2026, 8, 2),
        require_clean_git=True,
        wrapper_root=source,
    )


def _refresh_snapshot_receipt(snapshot: Path) -> None:
    descriptor = snapshot.joinpath("snapshot.json").read_bytes()
    value = json.loads(snapshot.joinpath("snapshot.json").read_text(encoding="utf-8"))
    receipt_path = snapshot / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["descriptor_sha256"] = "sha256:" + hashlib.sha256(descriptor).hexdigest()
    receipt["backup_set_sha256"] = (
        "sha256:" + migration_kernel.canonical_json_sha256(value["files"])
    )
    _write_json(receipt_path, receipt)


def test_trusted_remote_tag_exact_profile_apply_and_rollback(tmp_path: Path) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    plan = _plan(target, source)
    protected_before = target.joinpath("SKILL.md").read_bytes()
    planned_before = {
        item["path"]: (
            target.joinpath(item["path"]).read_bytes()
            if target.joinpath(item["path"]).is_file()
            else None
        )
        for item in plan["write_set"]
    }

    assert plan["source_trust"]["status"] == "trusted_release"
    assert plan["source_trust"]["remote_tag_verified"] is True
    assert plan["decision"] == "automatic_migration_available"
    assert plan["can_apply"] is True
    assert plan["profile"]["from_state"]["harness_skill_version"] == "v1.0.0"
    assert plan["profile"]["to_state"]["harness_skill_version"] == "v1.1.0"
    assert all(item.get("postimage_sha256") for item in plan["write_set"])

    approval_required = lifecycle.migrate_target_layout(
        target,
        "v0.14.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
    )
    assert approval_required["status"] == "approval_required"
    assert approval_required["writes"] is False

    snapshot_base = tmp_path / "trusted-snapshots"
    applied = lifecycle.migrate_target_layout(
        target,
        "v0.14.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        snapshot_root=snapshot_base,
        approved_plan_sha256=plan["plan_sha256"],
    )
    assert applied["status"] == "applied"
    assert applied["writes"] is True
    assert target.joinpath("SKILL.md").read_bytes() == protected_before
    record = target.joinpath(plan["migration_record"]).read_text(encoding="utf-8")
    assert "Source release：`v0.14.0 -> v0.14.0`" in record
    assert "Harness Skill：`v1.0.0 -> v1.1.0`" in record
    assert "Migration contract SHA-256" in record

    rolled_back = lifecycle.rollback_target_layout_migration(
        target,
        Path(applied["snapshot"]),
        trusted_snapshot_root=snapshot_base,
    )
    assert rolled_back["status"] == "rolled_back"
    assert target.joinpath("SKILL.md").read_bytes() == protected_before
    for relative, expected in planned_before.items():
        path = target / relative
        assert (path.read_bytes() if path.is_file() else None) == expected


def test_unpublished_or_locally_forged_tag_is_zero_write(tmp_path: Path) -> None:
    source = _make_release_source(tmp_path, publish_tag=False)
    target = _prepare_exact_v1_target(tmp_path)
    before = target.joinpath(lifecycle.TARGET_HARNESS_SKILL).read_bytes()

    plan = _plan(target, source)
    report = lifecycle.migrate_target_layout(
        target,
        "v0.14.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        approved_plan_sha256=plan["plan_sha256"],
    )

    assert plan["source_trust"]["status"] == "source_unreleased"
    assert plan["source_trust"]["remote_tag_verified"] is False
    assert report["status"] == "blocked"
    assert report["writes"] is False
    assert target.joinpath(lifecycle.TARGET_HARNESS_SKILL).read_bytes() == before


def test_plan_digest_mismatch_preimage_toctou_and_protected_race_are_zero_write(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    plan = _plan(target, source)
    harness = target / lifecycle.TARGET_HARNESS_SKILL
    before = harness.read_bytes()

    mismatch = lifecycle.migrate_target_layout(
        target,
        "v0.14.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        approved_plan_sha256="sha256:" + "0" * 64,
    )
    assert mismatch["status"] == "blocked"
    assert mismatch["writes"] is False
    assert harness.read_bytes() == before

    harness.write_bytes(before + b"\nTOCTOU\n")
    with pytest.raises(ValueError, match="preimage hash changed"):
        migration_kernel.verify_plan_preimages(target, plan)
    harness.write_bytes(before)

    surface = target / "SKILL.md"
    surface_before = surface.read_bytes()
    surface.write_bytes(surface_before + b"\nPROTECTED-RACE\n")
    with pytest.raises(ValueError, match="protected business surface preimage changed"):
        migration_kernel.verify_plan_preimages(target, plan)
    surface.write_bytes(surface_before)

    target.joinpath("OWNER-NOTE.md").write_text("dirty target\n", encoding="utf-8")
    dirty_plan = _plan(target, source)
    dirty_report = lifecycle.migrate_target_layout(
        target,
        "v0.14.0",
        date(2026, 8, 2),
        wrapper_root=source,
        approved_plan_sha256=dirty_plan["plan_sha256"],
    )
    assert dirty_plan["can_apply"] is False
    assert any("worktree is not clean" in item for item in dirty_plan["apply_blockers"])
    assert dirty_report["status"] == "blocked"
    assert dirty_report["writes"] is False
    assert harness.read_bytes() == before


def test_staged_postimage_mismatch_is_detected_before_snapshot_or_target_write(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    bundle = migration_kernel.load_migration_contract(source)
    plan = _plan(target, source)
    bad_plan = copy.deepcopy(plan)
    bad_plan["write_set"][0]["postimage_sha256"] = "sha256:" + "f" * 64
    bad_plan["plan_sha256"] = (
        "sha256:" + migration_kernel.migration_plan_digest(bad_plan)
    )
    before = target.joinpath(bad_plan["write_set"][0]["path"]).read_bytes()
    snapshot_base = tmp_path / "postimage-snapshots"

    with pytest.raises(ValueError, match="staged postimage differs"):
        lifecycle._apply_canonical_v1_upgrade(
            target,
            bad_plan,
            date(2026, 8, 2),
            bundle,
            snapshot_base,
        )

    assert target.joinpath(bad_plan["write_set"][0]["path"]).read_bytes() == before
    assert not snapshot_base.exists()


def test_plan_rejects_missing_postimage_and_overlapping_mutations(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    plan = _plan(target, source)

    missing_postimage = copy.deepcopy(plan)
    missing_postimage["write_set"][0].pop("postimage_sha256")
    missing_postimage["plan_sha256"] = (
        "sha256:" + migration_kernel.migration_plan_digest(missing_postimage)
    )
    with pytest.raises(ValueError, match="write postimage is invalid"):
        migration_kernel.verify_plan_preimages(target, missing_postimage)

    duplicate = copy.deepcopy(plan)
    duplicate["write_set"].append(copy.deepcopy(duplicate["write_set"][0]))
    duplicate["plan_sha256"] = "sha256:" + migration_kernel.migration_plan_digest(
        duplicate
    )
    with pytest.raises(ValueError, match="duplicate or overlapping mutation paths"):
        migration_kernel.verify_plan_preimages(target, duplicate)


def test_snapshot_symlink_tamper_and_incomplete_metadata_are_rejected(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    plan = _plan(target, source)

    symlink_base = tmp_path / "snapshot-link"
    real_base = tmp_path / "real-snapshot-base"
    real_base.mkdir()
    symlink_base.symlink_to(real_base, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        migration_kernel.create_migration_snapshot(
            target,
            plan,
            snapshot_root=symlink_base,
        )

    trusted_base = tmp_path / "trusted-snapshot-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    receipt = snapshot / "receipt.json"
    receipt_bytes = receipt.read_bytes()
    receipt.unlink()
    receipt.symlink_to(tmp_path / "missing-receipt.json")
    with pytest.raises(ValueError, match="symlink"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )
    receipt.unlink()
    receipt.write_bytes(receipt_bytes)

    descriptor_path = snapshot / "snapshot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    file_entry = next(item for item in descriptor["files"] if item["kind"] == "file")
    file_entry.pop("mode")
    _write_json(descriptor_path, descriptor)
    _refresh_snapshot_receipt(snapshot)
    with pytest.raises(ValueError, match="mode is invalid"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )


def test_rollback_validates_all_backups_before_first_restore_mutation(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    plan = _plan(target, source)
    trusted_base = tmp_path / "rollback-prevalidation"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )

    harness_item = next(
        item for item in plan["write_set"] if item["path"] == lifecycle.TARGET_HARNESS_SKILL
    )
    harness = target / lifecycle.TARGET_HARNESS_SKILL
    source_harness = (
        source
        / "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md"
    ).read_bytes()
    assert "sha256:" + hashlib.sha256(source_harness).hexdigest() == harness_item["postimage_sha256"]
    harness.write_bytes(source_harness)
    postimage = harness.read_bytes()

    descriptor = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    victim = next(
        item for item in reversed(descriptor["files"])
        if item["kind"] == "file" and item["path"] != lifecycle.TARGET_HARNESS_SKILL
    )
    (snapshot / "files" / victim["path"]).unlink()

    with pytest.raises(ValueError, match="backup is missing"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )
    assert harness.read_bytes() == postimage


@pytest.mark.parametrize(
    "relative",
    [
        lifecycle.TARGET_HARNESS_SKILL,
        lifecycle.TARGET_MIGRATION_CONTRACT,
        ".github/pull_request_template.md",
    ],
)
def test_bootstrap_force_preserves_unknown_managed_destination(
    tmp_path: Path,
    relative: str,
) -> None:
    target = tmp_path / relative.replace("/", "-").replace(".", "_")
    target.mkdir()
    target.joinpath("SKILL.md").write_text("# Owner Skill\n", encoding="utf-8")
    unknown = target / relative
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_bytes(b"OWNER UNKNOWN BYTES\n")
    forged_manifest = {
        "migration_contract": {
            "path": lifecycle.TARGET_MIGRATION_CONTRACT,
            "sha256": "sha256:" + hashlib.sha256(unknown.read_bytes()).hexdigest(),
        },
        "managed_files": [relative, lifecycle.TARGET_MIGRATION_CONTRACT],
    }
    _write_json(target / lifecycle.TARGET_WRAPPER_MANIFEST, forged_manifest)
    before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="--force cannot authorize replacement"):
        bootstrap.copy_templates(target, _replacement_values(), force=True)

    after = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_prerelease_v11_without_exact_contract_identity_is_manual_zero_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "prerelease-v11"
    target.mkdir()
    target.joinpath("SKILL.md").write_text(
        "# Business\n\n" + lifecycle.build_harness_activation_block() + "\n",
        encoding="utf-8",
    )
    bootstrap.copy_templates(target, _replacement_values(), force=False)
    manifest = lifecycle.build_wrapper_manifest(
        "MetaInFLow/prerelease-v11",
        "v0.14.0",
        lifecycle.WRAPPER_MANAGED_FILES,
        [],
        instruction_surface="SKILL.md",
    )
    manifest["migration_contract"]["sha256"] = "sha256:" + "1" * 64
    _write_json(target / lifecycle.TARGET_WRAPPER_MANIFEST, manifest)
    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    _git(target, "config", "user.email", "target-test@example.invalid")
    _git(target, "config", "user.name", "Target Test")
    _git(target, "add", ".")
    _git(target, "commit", "-m", "PR41-like prerelease fixture")
    before = target.joinpath(lifecycle.TARGET_HARNESS_SKILL).read_bytes()
    before_tree = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(target).parts
    }

    with pytest.raises(ValueError, match="migration_contract"):
        bootstrap.validate_existing_manifest_for_attach(
            target,
            "MetaInFLow/prerelease-v11",
        )
    assert {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(target).parts
    } == before_tree

    plan = lifecycle.plan_target_layout_migration(
        target,
        "v0.14.0",
        date(2026, 8, 2),
    )
    report = lifecycle.migrate_target_layout(
        target,
        "v0.14.0",
        date(2026, 8, 2),
    )

    assert plan["compatibility_state"] == "prerelease_ambiguous"
    assert plan["profile"]["profile_id"] == "prerelease-ambiguous-to-manual-review"
    assert plan["decision"] == "manual_migration_required"
    assert report["writes"] is False
    assert target.joinpath(lifecycle.TARGET_HARNESS_SKILL).read_bytes() == before


def test_batch_digest_mismatch_blocks_before_authority_or_target_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = {
        "stage": "harness_upgrade_all",
        "status": "planned",
        "writes": False,
        "errors": [],
        "latest_version": "v0.14.0",
        "targets": [{"target": str(tmp_path / "target"), "migration": {}}],
    }
    plan["batch_plan_sha256"] = "sha256:" + global_hook._batch_plan_digest(plan)
    authority_called = False

    def admin_resolver(*_args: object) -> dict[str, object]:
        nonlocal authority_called
        authority_called = True
        return {}

    monkeypatch.setattr(global_hook, "plan_upgrade_all", lambda *_args, **_kwargs: copy.deepcopy(plan))
    report = global_hook.apply_upgrade_all(
        tmp_path,
        ROOT,
        "v0.14.0",
        approve=True,
        approved_plan_sha256="sha256:" + "0" * 64,
        admin_resolver=admin_resolver,
    )

    assert report["status"] == "blocked"
    assert report["writes"] is False
    assert authority_called is False


def test_batch_reports_rollback_failure_without_claiming_zero_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    targets = [tmp_path / "first", tmp_path / "second"]
    plan = {
        "stage": "harness_upgrade_all",
        "status": "planned",
        "writes": False,
        "errors": [],
        "latest_version": "v0.14.0",
        "targets": [
            {
                "repo": f"MetaInFLow/{target.name}",
                "target": str(target),
                "migration": {"plan_sha256": "sha256:" + str(index) * 64},
            }
            for index, target in enumerate(targets, start=1)
        ],
    }
    plan["batch_plan_sha256"] = "sha256:" + global_hook._batch_plan_digest(plan)
    apply_calls = 0

    def migrate(target: Path, *_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal apply_calls
        apply_calls += 1
        if apply_calls == 2:
            raise ValueError("second target failed")
        return {
            "status": "applied",
            "writes": True,
            "target": str(target),
            "snapshot": str(tmp_path / "snapshot-first"),
        }

    def fail_rollback(*_args: object, **_kwargs: object) -> None:
        raise ValueError("snapshot validation failed")

    monkeypatch.setattr(
        global_hook,
        "plan_upgrade_all",
        lambda *_args, **_kwargs: copy.deepcopy(plan),
    )
    monkeypatch.setattr(
        global_hook,
        "read_global_hook_status",
        lambda _home: {"status": "not_installed"},
    )
    monkeypatch.setattr(lifecycle, "migrate_target_layout", migrate)
    monkeypatch.setattr(
        lifecycle,
        "rollback_target_layout_migration",
        fail_rollback,
    )

    report = global_hook.apply_upgrade_all(
        tmp_path,
        ROOT,
        "v0.14.0",
        approve=True,
        approved_plan_sha256=plan["batch_plan_sha256"],
        admin_resolver=lambda *_args: {"verified": True},
    )

    assert report["status"] == "rollback_failed"
    assert report["writes"] is True
    assert report["rollback_verified"] is False
    assert len(report["results"]) == 1
    assert any("snapshot validation failed" in error for error in report["errors"])
