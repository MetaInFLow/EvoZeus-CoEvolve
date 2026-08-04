from __future__ import annotations

import copy
import hashlib
import importlib._bootstrap_external
import importlib.util
import os
import re
import shutil
import stat
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import evozeus_harness_migration as migration_kernel
from scripts import evozeus_wrapper_global_hook as global_hook
from scripts import evozeus_wrapper_lifecycle as lifecycle
import test_harness_migration_protocol as protocol_fixtures
from test_harness_migration_protocol import (
    _make_release_source,
    _prepare_exact_v1_target,
    _source_tag_resolver,
)
from test_supervised_legacy_migration_plan import (
    SURFACE_PATH,
    _git,
    _prepare_reviewed_legacy_target,
)


ROOT = Path(__file__).resolve().parents[1]


def _make_batch_release_source(tmp_path: Path) -> Path:
    source = _make_release_source(tmp_path)
    source.joinpath("CHANGELOG.md").write_text(
        "# Changelog\n\n## [v0.15.0] - 2026-08-03\n",
        encoding="utf-8",
    )
    for relative in global_hook.WRAPPER_UPGRADE_SOURCE_FILES:
        destination = source / relative
        if destination.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    _git(source, "add", ".")
    _git(source, "commit", "-m", "Complete synthetic batch release source")
    _git(source, "tag", "-f", protocol_fixtures.RELEASE_TAG)
    ref_oid = _git(
        source,
        "rev-parse",
        f"refs/tags/{protocol_fixtures.RELEASE_TAG}",
    )
    peeled = _git(
        source,
        "rev-parse",
        f"refs/tags/{protocol_fixtures.RELEASE_TAG}^{{commit}}",
    )
    protocol_fixtures._SOURCE_TAG_ATTESTATIONS[str(source)] = {
        "provider": "github-api",
        "repository": migration_kernel.OFFICIAL_SOURCE_REPOSITORY,
        "tag": protocol_fixtures.RELEASE_TAG,
        "ref_oid": ref_oid,
        "peeled_commit_oid": peeled,
    }
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return source


def _register_batch_target(home: Path, target: Path, repository: str) -> None:
    owner, name = repository.split("/", 1)
    pointer = home / ".evozeus/.projects" / owner / name
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.symlink_to(target)


def _latest_v015() -> dict[str, Any]:
    return {"version": "v0.15.0", "source": "test", "error": None}


def _admin_fact(target: object, repository: object) -> dict[str, Any]:
    return {
        "repo_root": str(Path(str(target)).resolve()),
        "repository": str(repository),
        "viewer_permission": "ADMIN",
        "verified": True,
    }


def _file_tree(target: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in [target, *sorted(target.rglob("*"))]:
        relative = path.relative_to(target)
        if ".git" in relative.parts:
            continue
        metadata = path.lstat()
        key = relative.as_posix() or "."
        if stat.S_ISDIR(metadata.st_mode):
            result[key] = {
                "kind": "directory",
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        elif stat.S_ISREG(metadata.st_mode):
            result[key] = {
                "kind": "file",
                "mode": stat.S_IMODE(metadata.st_mode),
                "bytes": path.read_bytes(),
            }
        elif stat.S_ISLNK(metadata.st_mode):
            result[key] = {
                "kind": "symlink",
                "target": os.readlink(path),
            }
        else:
            result[key] = {"kind": "unsupported"}
    return result


def _release_plan(target: Path, source: Path) -> dict[str, Any]:
    return lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        today=date(2026, 8, 3),
        require_clean_git=True,
        wrapper_root=source,
        remote_tag_resolver=_source_tag_resolver(source),
    )


def _apply(
    target: Path,
    source: Path,
    *,
    approved: str | None,
    snapshot_root: Path,
) -> dict[str, Any]:
    return lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
        date(2026, 8, 3),
        wrapper_root=source,
        require_clean_git=True,
        snapshot_root=snapshot_root,
        approved_plan_sha256=approved,
        remote_tag_resolver=_source_tag_resolver(source),
    )


@pytest.mark.parametrize("newline", ["lf", "crlf"])
def test_reviewed_v014_exact_operation_approval_apply_verify_and_rollback_golden(
    tmp_path: Path,
    newline: str,
) -> None:
    source = _make_release_source(tmp_path)
    surface = SURFACE_PATH.read_bytes()
    if newline == "crlf":
        surface = surface.replace(b"\n", b"\r\n")
    target = _prepare_reviewed_legacy_target(
        tmp_path,
        surface=surface,
        name=f"reviewed-{newline}",
    )
    before = _file_tree(target)

    plan = _release_plan(target, source)

    assert plan["decision"] == "supervised_migration_available"
    assert plan["can_apply"] is True
    assert plan["writes"] is False
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", plan["operation_sha256"])
    assert plan["operation_sha256"] == plan["plan_sha256"]
    assert plan["write_authorization"]["class"] == "supervised_exact_plan_v1"
    assert _file_tree(target) == before

    approval_required = _apply(
        target,
        source,
        approved=None,
        snapshot_root=tmp_path / f"snapshots-{newline}",
    )
    assert approval_required["status"] == "approval_required"
    assert approval_required["writes"] is False
    assert approval_required["approval"]["expected_operation_sha256"] == plan[
        "operation_sha256"
    ]
    assert _file_tree(target) == before

    applied = _apply(
        target,
        source,
        approved=plan["operation_sha256"],
        snapshot_root=tmp_path / f"snapshots-{newline}",
    )
    assert applied["status"] == "applied"
    assert applied["writes"] is True
    assert applied["approved_operation_sha256"] == plan["operation_sha256"]
    assert applied["validation"] == {
        "adapter_proof": "passed",
        "business_retained_complement": "byte_exact",
        "current_closure": "passed",
        "deleted_spans_remaining": 0,
        "activation_block": "unique_commonmark_ast",
        "manifest": "closure_exact",
        "release_lineage": "closure_exact",
        "migration_ledger": "profile_exact_applied_lineage",
        "structure": "passed",
        "command": "immutable-bytes",
        "preflight_sha256": hashlib.sha256(
            source.joinpath(
                "contracts/v1/migrations/history/harness-skill/v1.1.0/"
                "artifacts/scripts/evozeus_wrapper_preflight.py"
            ).read_bytes()
        ).hexdigest(),
        "notice_sha256": hashlib.sha256(
            source.joinpath(
                "contracts/v1/migrations/history/harness-skill/v1.1.0/"
                "artifacts/scripts/evozeus_notice.py"
            ).read_bytes()
        ).hexdigest(),
    }
    applied_record = target / plan["migration_record"]
    assert applied_record.read_bytes() == source.joinpath(
        "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/artifacts/"
        "generated/reviewed-legacy-v0.14.0-to-harness-skill-v1.1.0.md"
    ).read_bytes()
    skill = (target / "SKILL.md").read_bytes()
    assert skill.count(b"<!-- evozeus-harness-entry:v1 -->") == 1
    assert skill.count(b"<!-- /evozeus-harness-entry -->") == 1
    if newline == "crlf":
        assert b"\r\n" in skill and b"\n" not in skill.replace(b"\r\n", b"")
    else:
        assert b"\r\n" not in skill

    rolled_back = lifecycle.rollback_target_layout_migration(
        target,
        Path(applied["snapshot"]),
        trusted_snapshot_root=tmp_path / f"snapshots-{newline}",
    )
    assert rolled_back["status"] == "rolled_back"
    assert not applied_record.exists()
    assert _file_tree(target) == before


def test_wrong_and_replayed_supervised_operation_digest_are_zero_write(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    before = _file_tree(target)
    plan = _release_plan(target, source)
    snapshot_root = tmp_path / "replay-snapshots"

    wrong = _apply(
        target,
        source,
        approved="sha256:" + "0" * 64,
        snapshot_root=snapshot_root,
    )
    assert wrong["status"] == "blocked"
    assert wrong["writes"] is False
    assert wrong["approval"]["matched"] is False
    assert _file_tree(target) == before

    applied = _apply(
        target,
        source,
        approved=plan["operation_sha256"],
        snapshot_root=snapshot_root,
    )
    assert applied["status"] == "applied"
    applied_tree = _file_tree(target)

    replay = _apply(
        target,
        source,
        approved=plan["operation_sha256"],
        snapshot_root=snapshot_root,
    )
    assert replay["writes"] is False
    assert replay["decision"] != "supervised_migration_available"
    assert _file_tree(target) == applied_tree


@pytest.mark.parametrize(
    "drift",
    [
        "target-mode",
        "target-inode",
        "target-root",
        "target-index",
        "source-index",
        "source-asset",
    ],
)
def test_supervised_apply_replans_and_rejects_every_authority_drift_before_write(
    tmp_path: Path,
    drift: str,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    plan = _release_plan(target, source)

    if drift == "target-mode":
        path = target / lifecycle.TARGET_PREFLIGHT_SCRIPT
        path.chmod(0o644)
    elif drift == "target-inode":
        path = target / "SKILL.md"
        replacement = target / "SKILL.replacement"
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o644)
        replacement.replace(path)
    elif drift == "target-root":
        detached = tmp_path / "detached-reviewed-root"
        target.rename(detached)
        shutil.copytree(detached, target, symlinks=True)
    elif drift in {"target-index", "source-index"}:
        repository = target if drift == "target-index" else source
        index = Path(_git(repository, "rev-parse", "--git-path", "index"))
        if not index.is_absolute():
            index = repository / index
        replacement = index.with_name("index.replacement")
        replacement.write_bytes(index.read_bytes())
        replacement.chmod(stat.S_IMODE(index.stat().st_mode))
        replacement.replace(index)
    else:
        source.joinpath(
            "scripts/evozeus_harness_legacy_prompt_adapter.py"
        ).write_bytes(
            source.joinpath(
                "scripts/evozeus_harness_legacy_prompt_adapter.py"
            ).read_bytes()
            + b"\n# drift\n"
        )
    after_drift = _file_tree(target)

    try:
        report = _apply(
            target,
            source,
            approved=plan["operation_sha256"],
            snapshot_root=tmp_path / f"drift-snapshots-{drift}",
        )
    except ValueError:
        report = None

    if report is not None:
        assert report["writes"] is False
        assert report.get("status") in {
            "blocked",
            "manual_migration_required",
        }
    assert _file_tree(target) == after_drift


@pytest.mark.parametrize("operation_index", range(7))
def test_each_supervised_operation_failure_rolls_back_every_byte_and_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_index: int,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    plan = _release_plan(target, source)
    before = _file_tree(target)
    fail_path = sorted(item["path"] for item in plan["write_set"])[operation_index]
    original = migration_kernel.SecureTargetFS.write_exact
    failed = False

    def fail_one(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        **kwargs: Any,
    ) -> None:
        nonlocal failed
        if (
            not failed
            and secure_target.target == target.resolve()
            and str(raw) == fail_path
        ):
            failed = True
            raise OSError(f"simulated supervised operation failure: {fail_path}")
        original(secure_target, raw, data, **kwargs)

    monkeypatch.setattr(migration_kernel.SecureTargetFS, "write_exact", fail_one)

    with pytest.raises(ValueError, match="snapshot rollback passed"):
        _apply(
            target,
            source,
            approved=plan["operation_sha256"],
            snapshot_root=tmp_path / f"operation-failure-{operation_index}",
        )

    assert failed is True
    assert _file_tree(target) == before


def test_supervised_postcondition_failure_rolls_back_every_byte_and_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    plan = _release_plan(target, source)
    before = _file_tree(target)

    def fail_postconditions(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError("simulated supervised postcondition failure")

    monkeypatch.setattr(
        lifecycle,
        "_verify_supervised_legacy_postconditions",
        fail_postconditions,
        raising=False,
    )

    with pytest.raises(ValueError, match="snapshot rollback passed"):
        _apply(
            target,
            source,
            approved=plan["operation_sha256"],
            snapshot_root=tmp_path / "postcondition-failure",
        )

    assert _file_tree(target) == before


def test_target_owned_pyc_cannot_execute_during_final_structure_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    plan = _release_plan(target, source)
    before = _file_tree(target)
    marker = tmp_path / "target-pyc-executed"
    original = lifecycle._run_harness_structure_check
    planted = False

    def plant_target_pyc(
        checked_target: Path,
        *,
        trusted_preflight: Path,
    ) -> dict[str, Any]:
        nonlocal planted
        notice = checked_target / lifecycle.TARGET_NOTICE_SCRIPT
        cache = Path(importlib.util.cache_from_source(str(notice)))
        payload = (
            f"open({str(marker)!r}, 'w').write('executed')\n"
            f"open({str(checked_target / 'SKILL.md')!r}, 'wb').write(b'CORRUPTED')\n"
        )
        code = compile(payload, str(notice), "exec")
        metadata = notice.stat()
        pyc = importlib._bootstrap_external._code_to_timestamp_pyc(  # type: ignore[attr-defined]
            code,
            int(metadata.st_mtime),
            metadata.st_size,
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pyc)
        planted = True
        return original(
            checked_target,
            trusted_preflight=trusted_preflight,
        )

    monkeypatch.setattr(
        lifecycle,
        "_run_harness_structure_check",
        plant_target_pyc,
    )

    applied = _apply(
        target,
        source,
        approved=plan["operation_sha256"],
        snapshot_root=tmp_path / "target-pyc-snapshots",
    )

    assert planted is True
    assert applied["status"] == "applied"
    assert not marker.exists()
    skill_bytes = target.joinpath("SKILL.md").read_bytes()
    skill_operation = next(
        item for item in plan["write_set"] if item["path"] == "SKILL.md"
    )
    assert "sha256:" + hashlib.sha256(skill_bytes).hexdigest() == skill_operation[
        "postimage_sha256"
    ]
    assert skill_bytes != b"CORRUPTED"
    assert before["SKILL.md"]["bytes"] != skill_bytes


@pytest.mark.parametrize(
    "artifact_name",
    ["evozeus_wrapper_preflight.py", "evozeus_notice.py"],
)
def test_final_structure_validation_uses_immutable_bytes_after_source_inode_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    plan = _release_plan(target, source)
    original_run = lifecycle._run_harness_structure_check
    artifact = (
        source
        / "contracts/v1/migrations/history/harness-skill/v1.1.0/artifacts/scripts"
        / artifact_name
    )
    writer = os.open(artifact, os.O_WRONLY)
    forged_staging = (
        tmp_path
        / "evozeus-structure-preflight-forged/scripts"
        / artifact_name
    )
    source_mutated = False

    def mutate_source_and_forge_old_staging_path(
        checked_target: Path,
        *,
        trusted_preflight: Any,
    ) -> dict[str, Any]:
        nonlocal source_mutated
        payload = b"raise SystemExit(91)\n"
        os.lseek(writer, 0, os.SEEK_SET)
        os.write(writer, payload)
        os.ftruncate(writer, len(payload))
        source_mutated = True
        forged_staging.parent.mkdir(parents=True)
        forged_staging.write_bytes(payload)
        return original_run(
            checked_target,
            trusted_preflight=trusted_preflight,
        )

    monkeypatch.setattr(
        lifecycle,
        "_run_harness_structure_check",
        mutate_source_and_forge_old_staging_path,
    )

    try:
        applied = _apply(
            target,
            source,
            approved=plan["operation_sha256"],
            snapshot_root=tmp_path / "trusted-structure-snapshots",
        )
    finally:
        os.close(writer)
        shutil.rmtree(forged_staging.parents[1], ignore_errors=True)

    assert applied["status"] == "applied"
    assert source_mutated is True
    assert artifact.read_bytes() == b"raise SystemExit(91)\n"
    assert not forged_staging.exists()


def test_supervised_rollback_preserves_unknown_concurrent_object_and_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    plan = _release_plan(target, source)
    unknown = target / ".evozeus-wrapper/contracts/concurrent-owner.txt"

    def race_postconditions(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        unknown.write_bytes(b"UNKNOWN-CONCURRENT-OWNER\n")
        raise ValueError("simulated postcondition race")

    monkeypatch.setattr(
        lifecycle,
        "_verify_supervised_legacy_postconditions",
        race_postconditions,
        raising=False,
    )

    report = _apply(
        target,
        source,
        approved=plan["operation_sha256"],
        snapshot_root=tmp_path / "unknown-object-snapshots",
    )

    assert report["status"] == "rollback_failed"
    assert report["writes"] is True
    assert report["rollback_verified"] is False
    retained_unknown = [
        path
        for path in Path(report["snapshot"]).joinpath("quarantine").rglob("*")
        if path.is_file()
        and path.read_bytes() == b"UNKNOWN-CONCURRENT-OWNER\n"
    ]
    assert len(retained_unknown) == 1


@pytest.mark.parametrize(
    "tamper",
    [
        "authorization-class",
        "profile-identity",
        "adapter-proof",
        "write-set",
        "protected-path",
        "protected-ownership",
        "protected-proof-policy",
    ],
)
def test_kernel_rejects_forged_supervised_authorization_even_with_resigned_digest(
    tmp_path: Path,
    tamper: str,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    forged = copy.deepcopy(_release_plan(target, source))
    authorization = forged["write_authorization"]
    protected = next(
        item
        for item in forged["protected_business_surfaces"]
        if item["planned_write"] is True
    )
    if tamper == "authorization-class":
        authorization["class"] = "automatic_migration_available"
    elif tamper == "profile-identity":
        authorization["profile_identity"]["profile_sha256"] = "0" * 64
    elif tamper == "adapter-proof":
        authorization["adapter_proof_sha256"] = "sha256:" + "0" * 64
    elif tamper == "write-set":
        authorization["write_set_sha256"] = "sha256:" + "0" * 64
    elif tamper == "protected-path":
        protected["path"] = "README.md"
    elif tamper == "protected-ownership":
        protected["ownership"] = "wrapper_managed"
    else:
        protected["proof_policy"] = "byte_exact"
    digest = "sha256:" + migration_kernel.migration_plan_digest(forged)
    forged["operation_sha256"] = digest
    forged["plan_sha256"] = digest

    with pytest.raises(ValueError, match="supervised|authorization|protected"):
        migration_kernel.verify_plan_preimages(target, forged)


def test_secure_write_exact_binds_the_planned_preimage_inode(
    tmp_path: Path,
) -> None:
    target = tmp_path / "identity-cas-target"
    target.mkdir()
    destination = target / "managed.txt"
    destination.write_bytes(b"PREIMAGE\n")
    destination.chmod(0o644)
    planned = destination.stat()
    expected_sha256 = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = migration_kernel.create_secure_retirement_root(
        target,
        prefix="identity-cas-quarantine",
    )
    replacement = target / "replacement.txt"
    replacement.write_bytes(b"PREIMAGE\n")
    replacement.chmod(0o644)
    replacement.replace(destination)

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        secure_target.prepare_mutation_batch(["managed.txt"])
        with pytest.raises(ValueError, match="identity CAS changed"):
            secure_target.write_exact(
                "managed.txt",
                b"POSTIMAGE\n",
                expected_preimage=expected_sha256,
                expected_mode=0o644,
                expected_identity=(planned.st_dev, planned.st_ino),
                mode=0o644,
            )

    assert destination.read_bytes() == b"PREIMAGE\n"


def test_real_supervised_batch_plan_and_apply_happy_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_batch_release_source(tmp_path)
    target = _prepare_reviewed_legacy_target(tmp_path)
    home = tmp_path / "home"
    _register_batch_target(
        home,
        target,
        "MetaInFLow/diagnose-enterprise-ai-scenarios",
    )
    monkeypatch.setattr(
        migration_kernel,
        "_resolve_official_remote_tag",
        _source_tag_resolver(source),
    )

    plan = global_hook.plan_upgrade_all(
        home,
        source,
        "v0.15.0",
        latest_resolver=_latest_v015,
    )

    assert plan["status"] == "planned"
    assert plan["target_count"] == 1
    migration = plan["targets"][0]["migration"]
    assert migration["decision"] == "supervised_migration_available"
    assert migration["plan_sha256"] == migration["operation_sha256"]

    report = global_hook.apply_upgrade_all(
        home,
        source,
        "v0.15.0",
        approve=True,
        approved_plan_sha256=plan["batch_plan_sha256"],
        latest_resolver=_latest_v015,
        admin_resolver=_admin_fact,
    )

    assert report["status"] == "applied"
    assert report["writes"] is True
    assert len(report["results"]) == 1
    result = report["results"][0]
    assert result["status"] == "applied"
    assert result["approved_operation_sha256"] == migration["operation_sha256"]
    assert result["validation"]["activation_block"] == "unique_commonmark_ast"


def test_real_batch_rolls_back_supervised_target_when_later_target_replans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_batch_release_source(tmp_path)
    supervised = _prepare_reviewed_legacy_target(tmp_path)
    later = _prepare_exact_v1_target(tmp_path)
    home = tmp_path / "home"
    _register_batch_target(
        home,
        supervised,
        "MetaInFLow/diagnose-enterprise-ai-scenarios",
    )
    _register_batch_target(home, later, "MetaInFLow/migration-target")
    monkeypatch.setattr(
        migration_kernel,
        "_resolve_official_remote_tag",
        _source_tag_resolver(source),
    )
    supervised_before = _file_tree(supervised)

    plan = global_hook.plan_upgrade_all(
        home,
        source,
        "v0.15.0",
        latest_resolver=_latest_v015,
    )
    assert plan["status"] == "planned"
    assert [
        item["migration"]["decision"] for item in plan["targets"]
    ] == ["supervised_migration_available", "automatic_migration_available"]

    def admin_with_later_drift(
        target: object,
        repository: object,
    ) -> dict[str, Any]:
        if repository == "MetaInFLow/migration-target":
            path = Path(str(target)) / "SKILL.md"
            path.write_bytes(path.read_bytes() + b"\nLATER-BATCH-DRIFT\n")
        return _admin_fact(target, repository)

    report = global_hook.apply_upgrade_all(
        home,
        source,
        "v0.15.0",
        approve=True,
        approved_plan_sha256=plan["batch_plan_sha256"],
        latest_resolver=_latest_v015,
        admin_resolver=admin_with_later_drift,
    )

    assert report["status"] == "rolled_back"
    assert report["writes"] is False
    assert report["rollback_verified"] is True
    assert len(report["backup"]) == 1
    assert _file_tree(supervised) == supervised_before
    assert later.joinpath("SKILL.md").read_bytes().endswith(
        b"LATER-BATCH-DRIFT\n"
    )
