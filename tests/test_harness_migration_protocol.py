from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import re
import shutil
import stat
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
RELEASE_TAG = "v0.15.0"
OFFICIAL_URL = "https://github.com/MetaInFLow/EvoZeus-CoEvolve.git"
_SOURCE_TAG_ATTESTATIONS: dict[str, dict[str, str] | None] = {}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _current_replacement_values() -> dict[str, str]:
    return {
        "DATE": "2026-08-02",
        "INITIAL_VERSION": "v0.1.0",
        "CURRENT_VERSION": "v0.1.0",
        "REPO_NAME": "MetaInFLow/migration-target",
        "REPO_URL": "https://github.com/MetaInFLow/migration-target",
        "SKILL_NAME": "migration-target",
        "VISIBILITY": "public",
        "WRAPPER_VERSION": bootstrap.WRAPPER_VERSION,
    }


def _legacy_replacement_values() -> dict[str, str]:
    return {**_current_replacement_values(), "WRAPPER_VERSION": "v0.14.0"}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _trusted_development_bundle() -> dict[str, object]:
    bundle = migration_kernel.load_migration_contract(ROOT)
    bundle["source_trust"] = {
        **bundle["source_trust"],
        "status": "trusted_release",
        "reasons": [],
    }
    return bundle


def _make_release_source(
    tmp_path: Path,
    *,
    publish_tag: bool = True,
    released_from: str | None = None,
    contract_current_harness_version: str | None = None,
) -> Path:
    source = tmp_path / ("source-published" if publish_tag else "source-forged")
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
    legacy_adapter_source = (
        source / "scripts/evozeus_harness_legacy_prompt_adapter.py"
    )
    shutil.copy2(
        ROOT / "scripts/evozeus_harness_legacy_prompt_adapter.py",
        legacy_adapter_source,
    )
    shutil.copy2(
        ROOT / "requirements-commonmark.lock",
        source / "requirements-commonmark.lock",
    )

    manifest_path = source / "contracts/v1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_revision"] = RELEASE_TAG
    contract_path = source / "contracts/v1/migrations/harness-migration-contract-v1.json"
    if contract_current_harness_version is not None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["current_harness_skill_version"] = contract_current_harness_version
        _write_json(contract_path, contract)
        next(
            entry
            for entry in manifest["files"]
            if entry["path"] == "migrations/harness-migration-contract-v1.json"
        )["sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    from_closure_path = (
        source
        / "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json"
    )
    if released_from is not None:
        from_closure = json.loads(from_closure_path.read_text(encoding="utf-8"))
        from_closure["source"]["release_status"] = "release_required_for_apply"
        from_closure["source"]["required_release"] = released_from
        _write_json(from_closure_path, from_closure)
    closure_path = (
        source
        / "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
    )
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["source"]["required_release"] = RELEASE_TAG
    closure["state"]["target_wrapper_version"] = RELEASE_TAG
    _write_json(closure_path, closure)
    closure_sha256 = hashlib.sha256(closure_path.read_bytes()).hexdigest()
    history_pointer_path = (
        source / "contracts/v1/migrations/history/harness-skill/current.json"
    )
    history_pointer = json.loads(history_pointer_path.read_text(encoding="utf-8"))
    history_pointer["entries"][0]["sha256"] = closure_sha256
    _write_json(history_pointer_path, history_pointer)

    profile_path = (
        source
        / "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if released_from is not None:
        profile["from_closure"]["sha256"] = hashlib.sha256(
            from_closure_path.read_bytes()
        ).hexdigest()
        profile["release_axis"]["artifact_source_from"]["release"] = released_from
    profile["to_closure"]["sha256"] = closure_sha256
    profile["release_axis"]["target_wrapper_to"] = RELEASE_TAG
    profile["release_axis"]["artifact_source_to"]["release"] = RELEASE_TAG
    _write_json(profile_path, profile)
    profile_sha256 = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    profile_pointer_path = source / "contracts/v1/migrations/profiles/current.json"
    profile_pointer = json.loads(profile_pointer_path.read_text(encoding="utf-8"))
    profile_pointer["entries"][0]["sha256"] = profile_sha256
    _write_json(profile_pointer_path, profile_pointer)
    for path in (
        from_closure_path,
        closure_path,
        history_pointer_path,
        profile_path,
        profile_pointer_path,
    ):
        relative = path.relative_to(source / "contracts/v1").as_posix()
        next(entry for entry in manifest["files"] if entry["path"] == relative)[
            "sha256"
        ] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    _git(source, "config", "user.email", "migration-test@example.invalid")
    _git(source, "config", "user.name", "Migration Test")
    _git(source, "remote", "add", "origin", OFFICIAL_URL)
    _git(source, "add", ".")
    _git(source, "commit", "-m", "Synthetic immutable migration release")
    _git(source, "tag", RELEASE_TAG)
    ref_oid = _git(source, "rev-parse", f"refs/tags/{RELEASE_TAG}")
    peeled = _git(source, "rev-parse", f"refs/tags/{RELEASE_TAG}^{{commit}}")
    _SOURCE_TAG_ATTESTATIONS[str(source)] = (
        {
            "provider": "github-api",
            "repository": migration_kernel.OFFICIAL_SOURCE_REPOSITORY,
            "tag": RELEASE_TAG,
            "ref_oid": ref_oid,
            "peeled_commit_oid": peeled,
        }
        if publish_tag
        else None
    )
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return source


def _source_tag_resolver(source: Path) -> migration_kernel.OfficialTagResolver:
    def resolve(repository: str, tag: str) -> dict[str, str] | None:
        attestation = _SOURCE_TAG_ATTESTATIONS[str(source)]
        if (
            attestation is None
            or repository != migration_kernel.OFFICIAL_SOURCE_REPOSITORY
            or tag != RELEASE_TAG
        ):
            return None
        return dict(attestation)

    return resolve


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
    migration_bundle = _trusted_development_bundle()
    bootstrap.copy_templates(
        target,
        _legacy_replacement_values(),
        force=False,
        _migration_bundle=migration_bundle,
    )
    for profile in migration_bundle["official_upgrade"]["profiles"]:
        for relative in profile.get("migration_records", []):
            target.joinpath(relative).unlink(missing_ok=True)
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
        latest_version="v0.15.0",
        today=date(2026, 8, 2),
        require_clean_git=True,
        wrapper_root=source,
        remote_tag_resolver=_source_tag_resolver(source),
    )


def _rename_active_profile(
    bundle: dict[str, object],
    profile_id: str,
) -> None:
    profile = next(
        item
        for item in bundle["contract"]["profiles"]
        if item.get("automatic") is True
    )
    profile["profile_id"] = profile_id
    profile["adapter_payload"]["official_profile"]["profile_id"] = profile_id
    profile["adapter_sha256"] = migration_kernel.canonical_json_sha256(
        profile["adapter_payload"]
    )
    raw_profile = bundle["official_upgrade"]["profiles"][0]
    raw_profile["profile_id"] = profile_id


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


def _prepare_secure_target(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.mkdir()
    target.joinpath("owned.txt").write_bytes(b"OWNED-PREIMAGE\n")
    target.joinpath("business.txt").write_bytes(b"PROTECTED-BUSINESS\n")
    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    _git(target, "config", "user.email", "target-test@example.invalid")
    _git(target, "config", "user.name", "Target Test")
    _git(target, "add", ".")
    _git(target, "commit", "-m", "Secure migration target fixture")
    return target


def _retirement_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / f"{name}-retirement"
    root.mkdir(mode=0o700)
    return root


def _secure_synthetic_plan(target: Path) -> dict[str, object]:
    owned_preimage = target.joinpath("owned.txt").read_bytes()
    protected = target.joinpath("business.txt").read_bytes()
    owned_postimage = b"OWNED-POSTIMAGE\n"
    created_postimage = b"CREATED-POSTIMAGE\n"
    plan: dict[str, object] = {
        "migration_protocol_version": migration_kernel.MIGRATION_PROTOCOL_VERSION,
        "decision": "automatic_migration_available",
        "can_apply": True,
        "apply_blockers": [],
        "source_trust": {"status": "trusted_release"},
        "profile": {"profile_id": "synthetic-secure-io"},
        "target_git_state": migration_kernel.target_git_state(target),
        "write_set": [
            {
                "path": "owned.txt",
                "preimage_sha256": "sha256:" + hashlib.sha256(owned_preimage).hexdigest(),
                "preimage_mode": 0o644,
                "postimage_sha256": "sha256:" + hashlib.sha256(owned_postimage).hexdigest(),
                "postimage_mode": 0o644,
            },
            {
                "path": "generated/nested/new.txt",
                "preimage_sha256": None,
                "preimage_mode": None,
                "postimage_sha256": "sha256:" + hashlib.sha256(created_postimage).hexdigest(),
                "postimage_mode": 0o644,
            },
        ],
        "delete_set": [],
        "move_set": [],
        "protected_business_surfaces": [
            {
                "path": "business.txt",
                "planned_write": False,
                "rule": "byte_exact",
                "preimage_sha256": "sha256:" + hashlib.sha256(protected).hexdigest(),
            }
        ],
    }
    plan["plan_sha256"] = "sha256:" + migration_kernel.migration_plan_digest(plan)
    return plan


def test_source_trust_uses_structured_official_tag_attestation(tmp_path: Path) -> None:
    source = _make_release_source(tmp_path)
    bundle = migration_kernel.load_migration_contract(
        source,
        remote_tag_resolver=_source_tag_resolver(source),
    )

    assert bundle["source_trust"]["status"] == "trusted_release"
    assert bundle["source_trust"]["remote_tag_attestation"] == _SOURCE_TAG_ATTESTATIONS[
        str(source)
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "contracts/v1/migrations/profiles/legacy-v0.14-three-section-to-canonical-v1.1-v1.json",
        "contracts/v1/migrations/history/harness-skill/current.json",
        "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json",
        "contracts/v1/migrations/adapters/legacy-v0.14-three-section/adapter-v1.json",
        "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/envelope.json",
        "scripts/evozeus_harness_legacy_prompt_adapter.py",
        "requirements-commonmark.lock",
    ],
)
def test_release_source_trust_rejects_working_tree_mode_only_drift(
    tmp_path: Path,
    relative: str,
) -> None:
    source = _make_release_source(tmp_path)
    (source / relative).chmod(0o755)
    manifest = json.loads(
        (source / "contracts/v1/manifest.json").read_text(encoding="utf-8")
    )
    contract_bytes = (
        source / "contracts/v1/migrations/harness-migration-contract-v1.json"
    ).read_bytes()
    contract = json.loads(contract_bytes.decode("utf-8"))

    trust = migration_kernel._release_source_trust(  # noqa: SLF001
        source,
        manifest,
        contract_bytes,
        contract,
        _source_tag_resolver(source),
    )

    assert trust["status"] == "source_unreleased"
    assert any("mode mismatch" in reason for reason in trust["reasons"])


def test_verified_migration_records_are_copied_into_the_runtime_adapter() -> None:
    bundle = migration_kernel.load_migration_contract(ROOT)
    raw_profile = bundle["official_upgrade"]["profiles"][0]
    runtime_profile = next(
        profile
        for profile in bundle["contract"]["profiles"]
        if profile.get("profile_id") == raw_profile["profile_id"]
    )
    payload = runtime_profile["adapter_payload"]

    assert payload["migration_records"] == raw_profile["migration_records"]
    assert payload["migration_records"] is not raw_profile["migration_records"]
    assert payload["current_migration_record"] == raw_profile[
        "current_migration_record"
    ]

    raw_profile["migration_records"].append("owner-mutated-after-verification")
    assert "owner-mutated-after-verification" not in payload["migration_records"]


def test_forged_effective_origin_rewrite_is_untrusted_even_with_valid_tag_attestation(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    forged = tmp_path / "forged.git"
    subprocess.run(["git", "init", "--bare", str(forged)], check=True, capture_output=True)
    _git(source, "config", f"url.file://{forged.resolve()}.insteadOf", OFFICIAL_URL)

    bundle = migration_kernel.load_migration_contract(
        source,
        remote_tag_resolver=_source_tag_resolver(source),
    )

    assert bundle["source_trust"]["status"] == "source_unreleased"
    assert bundle["source_trust"]["remote_transport"]["verified"] is False
    assert any("rewritten away" in reason for reason in bundle["source_trust"]["reasons"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "forged-provider"),
        ("repository", "attacker/repo"),
        ("tag", "v1.2.3"),
        ("ref_oid", "0" * 40),
        ("peeled_commit_oid", "f" * 40),
        ("ref_oid", "not-an-oid"),
    ],
)
def test_malformed_or_mismatched_tag_attestation_is_untrusted(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    source = _make_release_source(tmp_path)
    valid = dict(_SOURCE_TAG_ATTESTATIONS[str(source)] or {})
    valid[field] = value

    bundle = migration_kernel.load_migration_contract(
        source,
        remote_tag_resolver=lambda _repository, _tag: valid,
    )

    assert bundle["source_trust"]["status"] == "source_unreleased"
    assert bundle["source_trust"]["remote_tag_verified"] is False


def test_fresh_attach_rejects_unreleased_development_source(tmp_path: Path) -> None:
    target = tmp_path / "untrusted-attach"
    target.mkdir()
    target.joinpath("SKILL.md").write_text("# Owner Skill\n", encoding="utf-8")

    with pytest.raises(ValueError, match="immutable trusted source release"):
        bootstrap.copy_templates(target, _current_replacement_values(), force=False)

    assert sorted(path.name for path in target.iterdir()) == ["SKILL.md"]


def test_attach_success_consumes_staging_and_keeps_capability_evidence_external(
    tmp_path: Path,
) -> None:
    target = tmp_path / "attach-success"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER SKILL\n")

    bootstrap.copy_templates(
        target,
        _current_replacement_values(),
        force=False,
        _migration_bundle=_trusted_development_bundle(),
    )

    quarantines = list(tmp_path.glob(".attach-success.evozeus-attachment-*"))
    assert len(quarantines) == 1
    assert quarantines[0].parent == target.parent
    assert stat.S_IMODE(quarantines[0].stat().st_mode) == 0o700
    assert quarantines[0].stat().st_dev == target.stat().st_dev
    capability_evidence = list(
        quarantines[0].glob(".evozeus-atomic-capability-*")
    )
    assert len(capability_evidence) == 1
    assert capability_evidence[0].is_dir()
    assert not any(
        path.name.startswith(".evozeus-tmp-") for path in target.rglob("*")
    )


@pytest.mark.parametrize("failure", ["parent-unwritable", "cross-device"])
def test_attach_quarantine_capability_failure_is_zero_target_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    target = tmp_path / f"attach-quarantine-{failure}"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER SKILL\n")

    if failure == "parent-unwritable":
        def reject_transaction_directory(*_args: object, **_kwargs: object) -> str:
            raise PermissionError("simulated unwritable target parent")

        monkeypatch.setattr(
            migration_kernel,
            "create_secure_retirement_root",
            reject_transaction_directory,
        )
        expected_error = "unwritable target parent"
    else:
        original_configure = migration_kernel.SecureTargetFS._configure_retirement_root

        def reject_cross_device(
            secure_target: migration_kernel.SecureTargetFS,
            retirement_root: Path,
            *,
            allow_inside_target: bool = False,
        ) -> None:
            if secure_target.target == target.resolve():
                raise ValueError(
                    "secure retirement root must share the target filesystem"
                )
            original_configure(
                secure_target,
                retirement_root,
                allow_inside_target=allow_inside_target,
            )

        monkeypatch.setattr(
            migration_kernel.SecureTargetFS,
            "_configure_retirement_root",
            reject_cross_device,
        )
        expected_error = "share the target filesystem"

    with pytest.raises((PermissionError, ValueError), match=expected_error):
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=False,
            _migration_bundle=_trusted_development_bundle(),
        )

    assert {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    } == {"SKILL.md"}


def test_attach_create_cas_preserves_a_racing_unknown_leaf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "attach-leaf-race"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER SKILL\n")
    original_write = migration_kernel.SecureTargetFS.write_exact
    raced_path: Path | None = None

    def race_leaf(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        nonlocal raced_path
        if raced_path is None:
            raced_path = secure_target.target / str(raw)
            raced_path.parent.mkdir(parents=True, exist_ok=True)
            raced_path.write_bytes(b"OWNER-RACE\n")
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            mode=mode,
            expected_mode=expected_mode,
        )

    monkeypatch.setattr(migration_kernel.SecureTargetFS, "write_exact", race_leaf)
    with pytest.raises(ValueError, match="rollback_failed"):
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=False,
            _migration_bundle=_trusted_development_bundle(),
        )

    assert raced_path is not None
    assert not raced_path.exists()
    quarantines = list(tmp_path.glob(".attach-leaf-race.evozeus-attachment-*"))
    preserved = [
        path
        for path in quarantines[0].rglob("*")
        if path.is_file() and path.read_bytes() == b"OWNER-RACE\n"
    ]
    assert len(preserved) == 1
    assert {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    } == {"SKILL.md"}


def test_attach_parent_swap_never_writes_through_the_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "attach-parent-race"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER SKILL\n")
    outside = tmp_path / "outside-parent-race"
    outside.mkdir()
    detached = tmp_path / "detached-parent-race"
    original_verify = migration_kernel.SecureTargetFS._verify_parent_binding
    swapped = False

    def swap_parent(
        secure_target: migration_kernel.SecureTargetFS,
        relative: Path,
        parent_fd: int,
    ) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            top = target / relative.parts[0]
            top.rename(detached)
            top.symlink_to(outside, target_is_directory=True)
        original_verify(secure_target, relative, parent_fd)

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "_verify_parent_binding",
        swap_parent,
    )
    with pytest.raises(ValueError):
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=False,
            _migration_bundle=_trusted_development_bundle(),
        )

    assert swapped is True
    assert list(outside.rglob("*")) == []
    assert [path for path in detached.rglob("*") if path.is_file()] == []
    assert target.joinpath("SKILL.md").read_bytes() == b"OWNER SKILL\n"


def test_attach_rolls_back_a_complete_create_left_by_a_commit_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "attach-complete-create-error"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER SKILL\n")
    original_write = migration_kernel.SecureTargetFS.write_exact

    def fail_after_complete_create(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            mode=mode,
            expected_mode=expected_mode,
        )
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "write_exact",
        fail_after_complete_create,
    )
    with pytest.raises(ValueError, match="rolled back"):
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=False,
            _migration_bundle=_trusted_development_bundle(),
        )

    assert {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    } == {"SKILL.md"}


def test_attach_reports_rollback_failed_for_an_unknown_commit_error_residue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "attach-unknown-create-error"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER SKILL\n")
    original_write = migration_kernel.SecureTargetFS.write_exact
    residue: Path | None = None

    def replace_after_complete_create(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        nonlocal residue
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            mode=mode,
            expected_mode=expected_mode,
        )
        residue = secure_target.target / str(raw)
        residue.write_bytes(b"UNKNOWN-AFTER-COMMIT\n")
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "write_exact",
        replace_after_complete_create,
    )
    with pytest.raises(ValueError, match="rollback_failed"):
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=False,
            _migration_bundle=_trusted_development_bundle(),
    )

    assert residue is not None
    assert not residue.exists()
    quarantines = list(
        tmp_path.glob(".attach-unknown-create-error.evozeus-attachment-*")
    )
    assert len(quarantines) == 1
    retained_unknown = [
        path
        for path in quarantines[0].rglob("*")
        if path.is_file() and path.read_bytes() == b"UNKNOWN-AFTER-COMMIT\n"
    ]
    assert len(retained_unknown) == 1


def test_snapshot_external_anchor_rejects_a_forged_plan_descriptor_and_receipt(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "snapshot-plan-anchor")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "snapshot-plan-anchor-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    owner_before = target.joinpath("owned.txt").read_bytes()

    approved_plan_path = snapshot / "approved-plan.json"
    approved_plan = json.loads(approved_plan_path.read_text(encoding="utf-8"))
    approved_plan["source_trust"]["forged"] = True
    forged_plan_bytes = migration_kernel.canonical_plan_bytes(approved_plan)
    forged_plan_sha256 = "sha256:" + hashlib.sha256(forged_plan_bytes).hexdigest()
    approved_plan_path.write_bytes(forged_plan_bytes)

    descriptor_path = snapshot / "snapshot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["plan_sha256"] = forged_plan_sha256
    _write_json(descriptor_path, descriptor)
    receipt_path = snapshot / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["plan_sha256"] = forged_plan_sha256
    receipt["approved_plan_sha256"] = forged_plan_sha256
    receipt["descriptor_sha256"] = "sha256:" + hashlib.sha256(
        descriptor_path.read_bytes()
    ).hexdigest()
    _write_json(receipt_path, receipt)

    with pytest.raises(ValueError, match="external anchor"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )
    assert target.joinpath("owned.txt").read_bytes() == owner_before


def test_snapshot_artifacts_and_transaction_use_secure_exclusive_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "snapshot-secure-writes")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "snapshot-secure-writes-base"
    calls: list[tuple[str, str | None]] = []
    original_write = migration_kernel.SecureTargetFS.write_exact

    def record_write(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        if secure_target.target == trusted_base.resolve():
            calls.append((str(raw), expected_preimage))
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            mode=mode,
            expected_mode=expected_mode,
        )

    monkeypatch.setattr(migration_kernel.SecureTargetFS, "write_exact", record_write)
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    migration_kernel.mark_migration_transaction(snapshot, state="in_progress")
    transaction_id = snapshot.name
    expected_creates = {
        f"{transaction_id}/approved-plan.json",
        f"{transaction_id}/files/owned.txt",
        f"{transaction_id}/snapshot.json",
        f"{transaction_id}/receipt.json",
        f".anchors/{transaction_id}.json",
        f"{transaction_id}/transaction.json",
    }

    assert expected_creates <= {relative for relative, _preimage in calls}
    assert all(
        preimage is None
        for relative, preimage in calls
        if relative in expected_creates
    )


def test_snapshot_rejects_cross_device_quarantine_before_first_target_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "snapshot-cross-device")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "snapshot-cross-device-base"
    before = migration_kernel._target_inventory(target)
    original_configure = migration_kernel.SecureTargetFS._configure_retirement_root

    def reject_cross_device(
        secure_target: migration_kernel.SecureTargetFS,
        retirement_root: Path,
        *,
        allow_inside_target: bool = False,
    ) -> None:
        if secure_target.target == target.resolve():
            raise ValueError(
                "secure retirement root must share the target filesystem for atomic moves"
            )
        original_configure(
            secure_target,
            retirement_root,
            allow_inside_target=allow_inside_target,
        )

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "_configure_retirement_root",
        reject_cross_device,
    )

    with pytest.raises(ValueError, match="share the target filesystem"):
        migration_kernel.create_migration_snapshot(
            target,
            plan,
            snapshot_root=trusted_base,
        )

    assert migration_kernel._target_inventory(target) == before
    assert not target.joinpath("generated/nested/new.txt").exists()


@pytest.mark.parametrize("attack_surface", ["approved-plan", "anchor"])
def test_snapshot_leaf_race_is_fail_closed_and_preserves_unknown_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    attack_surface: str,
) -> None:
    target = _prepare_secure_target(tmp_path, f"snapshot-leaf-{attack_surface}")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / f"snapshot-leaf-{attack_surface}-base"
    original_write = migration_kernel.SecureTargetFS.write_exact
    residue: Path | None = None

    def race_leaf(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        nonlocal residue
        relative = str(raw)
        selected = (
            attack_surface == "approved-plan" and relative.endswith("/approved-plan.json")
        ) or (attack_surface == "anchor" and relative.startswith(".anchors/"))
        if secure_target.target == trusted_base.resolve() and selected and residue is None:
            residue = trusted_base / relative
            residue.parent.mkdir(parents=True, exist_ok=True)
            residue.write_bytes(b"UNKNOWN-SNAPSHOT-RACE\n")
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            mode=mode,
            expected_mode=expected_mode,
        )

    monkeypatch.setattr(migration_kernel.SecureTargetFS, "write_exact", race_leaf)
    with pytest.raises(ValueError, match="CAS"):
        migration_kernel.create_migration_snapshot(
            target,
            plan,
            snapshot_root=trusted_base,
        )

    assert residue is not None
    assert residue.read_bytes() == b"UNKNOWN-SNAPSHOT-RACE\n"
    assert target.joinpath("owned.txt").read_bytes() == b"OWNED-PREIMAGE\n"


def test_snapshot_parent_swap_never_writes_through_the_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "snapshot-parent-swap")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "snapshot-parent-swap-base"
    outside = tmp_path / "snapshot-parent-outside"
    outside.mkdir()
    detached = tmp_path / "snapshot-parent-detached"
    original_verify = migration_kernel.SecureTargetFS._verify_parent_binding
    swapped = False

    def swap_parent(
        secure_target: migration_kernel.SecureTargetFS,
        relative: Path,
        parent_fd: int,
    ) -> None:
        nonlocal swapped
        if secure_target.target == trusted_base.resolve() and not swapped:
            swapped = True
            trusted_base.rename(detached)
            trusted_base.symlink_to(outside, target_is_directory=True)
        original_verify(secure_target, relative, parent_fd)

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "_verify_parent_binding",
        swap_parent,
    )
    with pytest.raises(ValueError):
        migration_kernel.create_migration_snapshot(
            target,
            plan,
            snapshot_root=trusted_base,
        )

    assert swapped is True
    assert list(outside.rglob("*")) == []


def test_secure_nested_directory_creation_fsyncs_each_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "secure-directory-fsync"
    target.mkdir()
    retirement = _retirement_root(tmp_path, "secure-directory-fsync")
    created_under: list[tuple[int, int]] = []
    fsynced_directories: list[tuple[int, int]] = []
    original_mkdir = os.mkdir
    original_fsync = os.fsync

    def record_mkdir(
        path: object,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if dir_fd is not None:
            metadata = os.fstat(dir_fd)
            created_under.append((metadata.st_dev, metadata.st_ino))
        original_mkdir(path, mode, dir_fd=dir_fd)

    def record_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            fsynced_directories.append((metadata.st_dev, metadata.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(os, "mkdir", record_mkdir)
    monkeypatch.setattr(os, "fsync", record_fsync)
    with migration_kernel.SecureTargetFS(
        target,
        directory_mode=0o700,
        retirement_root=retirement,
    ) as secure_target:
        secure_target.write_exact(
            "one/two/artifact.json",
            b"{}\n",
            expected_preimage=None,
            mode=0o600,
        )

    assert set(created_under) <= set(fsynced_directories)


def test_secure_replace_mode_cas_preserves_a_changed_preimage(tmp_path: Path) -> None:
    target = tmp_path / "secure-mode-cas"
    target.mkdir()
    managed = target / "managed.txt"
    managed.write_bytes(b"PREIMAGE\n")
    managed.chmod(0o644)
    expected = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = _retirement_root(tmp_path, "secure-mode-cas")

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(ValueError, match="mode CAS changed"):
            secure_target.write_exact(
                "managed.txt",
                b"POSTIMAGE\n",
                expected_preimage=expected,
                expected_mode=0o755,
                mode=0o644,
            )

    assert managed.read_bytes() == b"PREIMAGE\n"
    assert stat.S_IMODE(managed.stat().st_mode) == 0o644


@pytest.mark.parametrize("failure_point", ["write", "fchmod"])
def test_secure_replace_prepare_failure_preserves_preimage_and_allows_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    target = _prepare_secure_target(tmp_path, f"secure-replace-{failure_point}")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / f"secure-replace-{failure_point}-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    item = next(entry for entry in plan["write_set"] if entry["path"] == "owned.txt")
    original_write = os.write
    write_calls = 0

    def fail_during_write(descriptor: int, value: object) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            view = memoryview(value)
            return original_write(descriptor, view[: max(1, len(view) // 2)])
        raise OSError("simulated staged write failure")

    def fail_fchmod(_descriptor: int, _mode: int) -> None:
        raise OSError("simulated staged fchmod failure")

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        with monkeypatch.context() as failure_patch:
            if failure_point == "write":
                failure_patch.setattr(os, "write", fail_during_write)
            else:
                failure_patch.setattr(os, "fchmod", fail_fchmod)
            with pytest.raises(OSError, match="simulated staged"):
                secure_target.write_exact(
                    "owned.txt",
                    b"OWNED-POSTIMAGE\n",
                    expected_preimage=item["preimage_sha256"],
                    expected_mode=item["preimage_mode"],
                    mode=item["postimage_mode"],
                )

    assert target.joinpath("owned.txt").read_bytes() == b"OWNED-PREIMAGE\n"
    assert stat.S_IMODE(target.joinpath("owned.txt").stat().st_mode) == 0o644
    assert not any(path.name.startswith(".evozeus-tmp-") for path in target.iterdir())
    result = migration_kernel.rollback_migration_snapshot(
        target,
        snapshot,
        trusted_snapshot_root=trusted_base,
    )
    assert result["status"] == "rolled_back"


@pytest.mark.parametrize("failure_point", ["write", "fchmod"])
def test_secure_create_prepare_failure_leaves_no_file_directory_or_temp_residue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    target = tmp_path / f"secure-create-{failure_point}"
    target.mkdir()
    if failure_point == "fchmod":
        target.joinpath("generated/nested").mkdir(parents=True)
    original_write = os.write
    write_calls = 0

    def fail_during_write(descriptor: int, value: object) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            view = memoryview(value)
            return original_write(descriptor, view[: max(1, len(view) // 2)])
        raise OSError("simulated staged write failure")

    def fail_fchmod(_descriptor: int, _mode: int) -> None:
        raise OSError("simulated staged fchmod failure")

    retirement = _retirement_root(tmp_path, f"secure-create-{failure_point}")
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with monkeypatch.context() as failure_patch:
            if failure_point == "write":
                failure_patch.setattr(os, "write", fail_during_write)
            else:
                failure_patch.setattr(os, "fchmod", fail_fchmod)
            with pytest.raises(OSError, match="simulated staged"):
                secure_target.write_exact(
                    "generated/nested/new.txt",
                    b"CREATED-POSTIMAGE\n",
                    expected_preimage=None,
                    mode=0o644,
                )

    assert not target.joinpath("generated/nested/new.txt").exists()
    assert not any(
        path.name.startswith(".evozeus-tmp-") for path in target.rglob("*")
    )
    if failure_point == "write":
        assert list(target.rglob("*")) == []
    else:
        assert {
            path.relative_to(target).as_posix() for path in target.rglob("*")
        } == {"generated", "generated/nested"}


def test_secure_replace_breaks_external_hardlink_without_mutating_approved_inode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "secure-hardlink"
    target.mkdir()
    managed = target / "managed.txt"
    managed.write_bytes(b"PREIMAGE\n")
    outside = tmp_path / "external-hardlink.txt"
    os.link(managed, outside)
    expected = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = _retirement_root(tmp_path, "secure-hardlink")

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        secure_target.write_exact(
            "managed.txt",
            b"POSTIMAGE\n",
            expected_preimage=expected,
            expected_mode=0o644,
            mode=0o644,
        )

    assert managed.read_bytes() == b"POSTIMAGE\n"
    assert outside.read_bytes() == b"PREIMAGE\n"
    assert managed.stat().st_ino != outside.stat().st_ino

    failed = target / "failed.txt"
    failed.write_bytes(b"PREIMAGE\n")
    outside_failed = tmp_path / "external-hardlink-failed.txt"
    os.link(failed, outside_failed)
    original_write = os.write
    write_calls = 0

    def fail_during_write(descriptor: int, value: object) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            view = memoryview(value)
            return original_write(descriptor, view[: max(1, len(view) // 2)])
        raise OSError("simulated staged write failure")

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(os, "write", fail_during_write)
            with pytest.raises(OSError, match="simulated staged"):
                secure_target.write_exact(
                    "failed.txt",
                    b"POSTIMAGE\n",
                    expected_preimage=expected,
                    expected_mode=0o644,
                    mode=0o644,
                )

    assert failed.read_bytes() == b"PREIMAGE\n"
    assert outside_failed.read_bytes() == b"PREIMAGE\n"
    assert failed.stat().st_ino == outside_failed.stat().st_ino
    assert not any(path.name.startswith(".evozeus-tmp-") for path in target.iterdir())


@pytest.mark.parametrize("operation", ["create", "replace"])
def test_secure_publish_rejects_a_wrong_staging_inode_after_the_atomic_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    target = tmp_path / f"secure-published-identity-{operation}"
    target.mkdir()
    destination = target / "managed.txt"
    expected_preimage: str | None = None
    if operation == "replace":
        destination.write_bytes(b"PREIMAGE\n")
        expected_preimage = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    target.joinpath("attacker.txt").write_bytes(b"ATTACKER\n")
    retirement = _retirement_root(
        tmp_path,
        f"secure-published-identity-{operation}",
    )

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with monkeypatch.context() as publication_patch:
            if operation == "create":
                original_noreplace = (
                    migration_kernel._atomic_rename_noreplace_between_directories
                )

                def publish_wrong_create(
                    _source_parent_fd: int,
                    _source: str,
                    destination_parent_fd: int,
                    destination_name: str,
                ) -> None:
                    original_noreplace(
                        destination_parent_fd,
                        "attacker.txt",
                        destination_parent_fd,
                        destination_name,
                    )

                publication_patch.setattr(
                    migration_kernel,
                    "_atomic_rename_noreplace_between_directories",
                    publish_wrong_create,
                )
            else:
                original_exchange = (
                    migration_kernel._atomic_exchange_between_directories
                )

                def publish_wrong_exchange(
                    _source_parent_fd: int,
                    _source: str,
                    destination_parent_fd: int,
                    destination_name: str,
                ) -> None:
                    original_exchange(
                        destination_parent_fd,
                        "attacker.txt",
                        destination_parent_fd,
                        destination_name,
                    )

                publication_patch.setattr(
                    migration_kernel,
                    "_atomic_exchange_between_directories",
                    publish_wrong_exchange,
                )

            with pytest.raises(
                ValueError,
                match=(
                    "published identity changed|replace CAS changed|cleanup_required"
                ),
            ):
                secure_target.write_exact(
                    "managed.txt",
                    b"POSTIMAGE\n",
                    expected_preimage=expected_preimage,
                    expected_mode=0o644 if expected_preimage is not None else None,
                    mode=0o644,
                )

    preserved = [
        path
        for path in [destination, *retirement.iterdir()]
        if path.is_file() and path.read_bytes() == b"ATTACKER\n"
    ]
    assert len(preserved) == 1
    assert not any(path.name.startswith(".evozeus-tmp-") for path in target.iterdir())


def test_secure_replace_quarantines_a_destination_changed_at_atomic_exchange(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "secure-replace-final-cas"
    target.mkdir()
    destination = target / "managed.txt"
    destination.write_bytes(b"PREIMAGE\n")
    expected = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = _retirement_root(tmp_path, "secure-replace-final-cas")
    original_exchange = migration_kernel._atomic_exchange_between_directories
    raced = False

    def race_before_exchange(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal raced
        if not raced:
            raced = True
            os.unlink(destination_name, dir_fd=destination_parent_fd)
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
                dir_fd=destination_parent_fd,
            )
            try:
                os.write(descriptor, b"CONCURRENT-OWNER-CHANGE\n")
            finally:
                os.close(descriptor)
        original_exchange(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination_name,
        )

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_exchange_between_directories",
        race_before_exchange,
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(ValueError, match="replace identity CAS changed"):
            secure_target.write_exact(
                "managed.txt",
                b"POSTIMAGE\n",
                expected_preimage=expected,
                expected_mode=0o644,
                mode=0o644,
            )

    assert raced is True
    assert destination.read_bytes() == b"POSTIMAGE\n"
    assert any(
        path.is_file() and path.read_bytes() == b"CONCURRENT-OWNER-CHANGE\n"
        for path in retirement.iterdir()
    )
    assert not any(
        path.name.startswith((".evozeus-tmp-", ".evozeus-quarantine-"))
        for path in target.iterdir()
    )


def test_secure_replace_preserves_in_place_postpublication_race_and_blocks_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "secure-replace-published-race")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "secure-replace-published-race-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    item = next(entry for entry in plan["write_set"] if entry["path"] == "owned.txt")
    original_exchange = migration_kernel._atomic_exchange_between_directories
    raced = False

    def modify_published_inode(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal raced
        original_exchange(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination_name,
        )
        if not raced:
            raced = True
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_TRUNC,
                dir_fd=destination_parent_fd,
            )
            try:
                os.write(descriptor, b"CONCURRENT-PUBLISHED-INODE\n")
            finally:
                os.close(descriptor)

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_exchange_between_directories",
        modify_published_inode,
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        with pytest.raises(ValueError, match="published bytes changed"):
            secure_target.write_exact(
                "owned.txt",
                b"OWNED-POSTIMAGE\n",
                expected_preimage=item["preimage_sha256"],
                expected_mode=item["preimage_mode"],
                mode=item["postimage_mode"],
            )

    assert raced is True
    assert target.joinpath("owned.txt").read_bytes() == b"CONCURRENT-PUBLISHED-INODE\n"
    quarantined = [
        path
        for path in snapshot.joinpath("quarantine").iterdir()
        if path.is_file() and path.read_bytes() == b"OWNED-PREIMAGE\n"
    ]
    assert len(quarantined) == 1
    with pytest.raises(ValueError, match="rollback target changed outside"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )
    assert quarantined[0].read_bytes() == b"OWNED-PREIMAGE\n"


def test_migration_fails_closed_when_published_bytes_change_concurrently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    plan = _plan(target, source)
    harness_path = target / lifecycle.TARGET_HARNESS_SKILL
    harness_before = harness_path.read_bytes()
    original_exchange = migration_kernel._atomic_exchange_between_directories
    raced = False

    def modify_published_harness(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal raced
        original_exchange(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
        )
        if not raced and destination_name == "SKILL.md":
            raced = True
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_TRUNC,
                dir_fd=destination_parent_fd,
            )
            try:
                os.write(descriptor, b"CONCURRENT-HARNESS-CONTENT\n")
            finally:
                os.close(descriptor)

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_exchange_between_directories",
        modify_published_harness,
    )
    snapshot_base = tmp_path / "concurrent-quarantine-snapshots"
    result = lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        snapshot_root=snapshot_base,
        approved_plan_sha256=plan["plan_sha256"],
        remote_tag_resolver=_source_tag_resolver(source),
    )

    assert raced is True
    assert result["status"] == "rollback_failed"
    assert result["rollback_verified"] is False
    assert harness_path.read_bytes() == b"CONCURRENT-HARNESS-CONTENT\n"
    transactions = [
        path
        for path in snapshot_base.iterdir()
        if path.is_dir() and re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{12}", path.name)
    ]
    assert len(transactions) == 1
    quarantined = [
        path
        for path in transactions[0].joinpath("quarantine").rglob("*")
        if path.is_file() and path.read_bytes() == harness_before
    ]
    assert len(quarantined) == 1
    transaction = json.loads(
        transactions[0].joinpath("transaction.json").read_text(encoding="utf-8")
    )
    assert transaction["state"] == "rollback_failed"
    assert transaction["retained_quarantine"]


def test_rollback_remove_race_retains_unknown_destination_in_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "secure-rollback-remove-race")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "secure-rollback-remove-race-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    created_item = next(
        entry
        for entry in plan["write_set"]
        if entry["path"] == "generated/nested/new.txt"
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            created_item["path"],
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=created_item["postimage_mode"],
        )
    original_noreplace = migration_kernel._atomic_rename_noreplace_between_directories
    raced = False

    def race_before_quarantine(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        destination: str,
    ) -> None:
        nonlocal raced
        if not raced and source == "new.txt":
            raced = True
            descriptor = os.open(
                source,
                os.O_WRONLY | os.O_TRUNC,
                dir_fd=source_parent_fd,
            )
            try:
                os.write(descriptor, b"CONCURRENT-ROLLBACK-CONTENT\n")
            finally:
                os.close(descriptor)
        original_noreplace(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination,
        )

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_rename_noreplace_between_directories",
        race_before_quarantine,
    )
    with pytest.raises(ValueError, match="CAS changed during atomic retirement"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )

    destination = target / created_item["path"]
    assert raced is True
    assert not destination.exists()
    assert any(
        path.is_file() and path.read_bytes() == b"CONCURRENT-ROLLBACK-CONTENT\n"
        for path in snapshot.joinpath("quarantine").rglob("*")
    )
    assert not any(
        path.name.startswith((".evozeus-tmp-", ".evozeus-quarantine-"))
        for path in destination.parent.iterdir()
    )


def test_secure_replace_and_remove_reject_missing_atomic_name_primitives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "secure-missing-atomic-rename"
    target.mkdir()
    destination = target / "managed.txt"
    destination.write_bytes(b"PREIMAGE\n")
    expected = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = _retirement_root(tmp_path, "secure-missing-atomic-rename")

    def unsupported(*_args: object, **_kwargs: object) -> None:
        raise ValueError("atomic rename unavailable")

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_exchange_between_directories",
        unsupported,
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(ValueError, match="atomic exchange"):
            secure_target.write_exact(
                "managed.txt",
                b"POSTIMAGE\n",
                expected_preimage=expected,
                expected_mode=0o644,
                mode=0o644,
            )
    assert destination.read_bytes() == b"PREIMAGE\n"
    assert not any(path.name.startswith(".evozeus-tmp-") for path in target.iterdir())

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        monkeypatch.setattr(
            migration_kernel,
            "_atomic_rename_noreplace_between_directories",
            unsupported,
        )
        with pytest.raises(ValueError, match="cleanup_required"):
            secure_target.remove_exact(
                "managed.txt",
                expected,
                expected_mode=0o644,
            )
    assert destination.read_bytes() == b"PREIMAGE\n"
    assert not any(
        path.name.startswith(".evozeus-quarantine-") for path in target.iterdir()
    )


def test_secure_create_rejects_missing_atomic_publish_without_copy_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "secure-create-missing-atomic-publish"
    target.mkdir()
    retirement = _retirement_root(tmp_path, "secure-create-missing-atomic-publish")

    def unsupported(*_args: object, **_kwargs: object) -> None:
        raise ValueError("atomic rename unavailable")

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        monkeypatch.setattr(
            migration_kernel,
            "_atomic_rename_noreplace_between_directories",
            unsupported,
        )
        with pytest.raises(ValueError, match="atomic create"):
            secure_target.write_exact(
                "managed.txt",
                b"POSTIMAGE\n",
                expected_preimage=None,
                mode=0o644,
            )

    assert not target.joinpath("managed.txt").exists()
    assert not any(
        path.name.startswith(".evozeus-tmp-") for path in target.iterdir()
    )
    assert any(
        path.read_bytes() == b"POSTIMAGE\n"
        for path in retirement.rglob("*")
        if path.is_file()
    )


def test_secure_create_consumes_staging_without_any_unlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "secure-create-unlink-failure")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "secure-create-unlink-failure-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    item = next(
        entry
        for entry in plan["write_set"]
        if entry["path"] == "generated/nested/new.txt"
    )
    unlink_called = False

    def forbid_unlink(
        _path: object,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal unlink_called
        del dir_fd
        unlink_called = True
        raise AssertionError("secure create must not unlink a named candidate")

    with monkeypatch.context() as unlink_patch:
        unlink_patch.setattr(os, "unlink", forbid_unlink)
        with migration_kernel.SecureTargetFS(
            target,
            retirement_root=snapshot / "quarantine",
        ) as secure_target:
            secure_target.write_exact(
                "generated/nested/new.txt",
                b"CREATED-POSTIMAGE\n",
                expected_preimage=None,
                mode=item["postimage_mode"],
            )

    assert unlink_called is False
    assert target.joinpath("generated/nested/new.txt").read_bytes() == (
        b"CREATED-POSTIMAGE\n"
    )
    assert not any(
        path.name.startswith(".evozeus-tmp-") for path in target.rglob("*")
    )
    result = migration_kernel.rollback_migration_snapshot(
        target,
        snapshot,
        trusted_snapshot_root=trusted_base,
    )
    assert result["status"] == "rolled_back"
    assert not target.joinpath("generated").exists()


@pytest.mark.parametrize("swap", ["leaf", "parent"])
def test_secure_apply_swap_never_writes_outside_the_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    swap: str,
) -> None:
    target = tmp_path / f"secure-apply-{swap}"
    target.mkdir()
    managed_parent = target / "managed"
    managed_parent.mkdir()
    managed = managed_parent / "artifact.txt"
    managed.write_bytes(b"PREIMAGE\n")
    outside = tmp_path / f"secure-apply-{swap}-outside"
    outside.mkdir()
    outside_file = outside / "artifact.txt"
    outside_file.write_bytes(b"OUTSIDE\n")
    detached = tmp_path / f"secure-apply-{swap}-detached"
    expected = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = _retirement_root(tmp_path, f"secure-apply-{swap}")
    original_verify = migration_kernel.SecureTargetFS._verify_parent_binding
    swapped = False

    def inject_swap(
        secure_target: migration_kernel.SecureTargetFS,
        relative: Path,
        parent_fd: int,
    ) -> None:
        nonlocal swapped
        original_verify(secure_target, relative, parent_fd)
        if swapped:
            return
        swapped = True
        if swap == "leaf":
            managed.unlink()
            managed.symlink_to(outside_file)
        else:
            managed_parent.rename(detached)
            managed_parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "_verify_parent_binding",
        inject_swap,
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(ValueError):
            secure_target.write_exact(
                "managed/artifact.txt",
                b"POSTIMAGE\n",
                expected_preimage=expected,
                mode=0o644,
            )

    assert swapped is True
    assert outside_file.read_bytes() == b"OUTSIDE\n"


def test_rollback_removes_transaction_created_directories_that_were_absent(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-directory-cleanup")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-directory-cleanup-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    write_items = {item["path"]: item for item in plan["write_set"]}
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            "owned.txt",
            b"OWNED-POSTIMAGE\n",
            expected_preimage=write_items["owned.txt"]["preimage_sha256"],
            mode=0o644,
        )
        secure_target.write_exact(
            "generated/nested/new.txt",
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=0o644,
        )

    result = migration_kernel.rollback_migration_snapshot(
        target,
        snapshot,
        trusted_snapshot_root=trusted_base,
    )

    assert result["status"] == "rolled_back"
    assert target.joinpath("owned.txt").read_bytes() == b"OWNED-PREIMAGE\n"
    assert not target.joinpath("generated").exists()


def test_rollback_upgrades_an_authenticated_pre_quarantine_v1_snapshot(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-legacy-quarantine")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-legacy-quarantine-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    created_item = next(
        item
        for item in plan["write_set"]
        if item["path"] == "generated/nested/new.txt"
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            created_item["path"],
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=created_item["postimage_mode"],
        )

    descriptor_path = snapshot / "snapshot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor.pop("quarantine_directory")
    _write_json(descriptor_path, descriptor)
    receipt_path = snapshot / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["descriptor_sha256"] = "sha256:" + hashlib.sha256(
        descriptor_path.read_bytes()
    ).hexdigest()
    _write_json(receipt_path, receipt)
    anchor_path = trusted_base / ".anchors" / f"{snapshot.name}.json"
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["descriptor_sha256"] = receipt["descriptor_sha256"]
    anchor["receipt_sha256"] = "sha256:" + hashlib.sha256(
        receipt_path.read_bytes()
    ).hexdigest()
    anchor_path.chmod(0o600)
    _write_json(anchor_path, anchor)
    anchor_path.chmod(0o400)
    shutil.rmtree(snapshot / "quarantine")

    result = migration_kernel.rollback_migration_snapshot(
        target,
        snapshot,
        trusted_snapshot_root=trusted_base,
    )

    assert result["status"] == "rolled_back"
    assert (snapshot / "quarantine").is_dir()
    assert stat.S_IMODE((snapshot / "quarantine").stat().st_mode) == 0o700
    assert not target.joinpath("generated").exists()


def test_rollback_rejects_a_hash_matching_file_with_an_unapproved_mode(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-mode-state")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-mode-state-base"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    owned = target / "owned.txt"
    item = next(entry for entry in plan["write_set"] if entry["path"] == "owned.txt")
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            "owned.txt",
            b"OWNED-POSTIMAGE\n",
            expected_preimage=item["preimage_sha256"],
            expected_mode=item["preimage_mode"],
            mode=item["postimage_mode"],
        )
    owned.chmod(0o600)

    with pytest.raises(ValueError, match="changed outside the migration transaction"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )

    assert owned.read_bytes() == b"OWNED-POSTIMAGE\n"
    assert stat.S_IMODE(owned.stat().st_mode) == 0o600


def test_post_apply_state_accepts_only_the_approved_git_and_inventory_delta(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "post-apply-state")
    plan = _secure_synthetic_plan(target)
    plan["post_apply_baseline"] = migration_kernel.capture_post_apply_baseline(
        target,
        plan,
    )
    plan["plan_sha256"] = "sha256:" + migration_kernel.migration_plan_digest(plan)
    write_items = {item["path"]: item for item in plan["write_set"]}
    retirement = _retirement_root(tmp_path, "post-apply-state")
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        secure_target.write_exact(
            "owned.txt",
            b"OWNED-POSTIMAGE\n",
            expected_preimage=write_items["owned.txt"]["preimage_sha256"],
            mode=0o644,
        )
        secure_target.write_exact(
            "generated/nested/new.txt",
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=0o644,
        )

    migration_kernel.verify_post_apply_target_state(target, plan)
    target.joinpath("OWNER-RACE.md").write_bytes(b"UNPLANNED\n")
    with pytest.raises(ValueError, match="unplanned|changed set"):
        migration_kernel.verify_post_apply_target_state(target, plan)


def test_post_apply_state_rejects_head_index_and_protected_changes(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "post-apply-git-state")
    plan = _secure_synthetic_plan(target)
    plan["post_apply_baseline"] = migration_kernel.capture_post_apply_baseline(
        target,
        plan,
    )
    plan["plan_sha256"] = "sha256:" + migration_kernel.migration_plan_digest(plan)
    target.joinpath("business.txt").write_bytes(b"PROTECTED-CHANGED\n")

    with pytest.raises(ValueError, match="protected|unplanned"):
        migration_kernel.verify_post_apply_target_state(target, plan)

    target.joinpath("business.txt").write_bytes(b"PROTECTED-BUSINESS\n")
    target.joinpath("owned.txt").write_bytes(b"OWNED-POSTIMAGE\n")
    target.joinpath("generated/nested").mkdir(parents=True)
    target.joinpath("generated/nested/new.txt").write_bytes(b"CREATED-POSTIMAGE\n")
    _git(target, "add", "owned.txt")
    with pytest.raises(ValueError, match="index"):
        migration_kernel.verify_post_apply_target_state(target, plan)


def test_snapshot_then_unplanned_race_blocks_before_the_first_target_write(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "snapshot-prewrite-state")
    plan = _secure_synthetic_plan(target)
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=tmp_path / "snapshot-prewrite-state-base",
    )
    target.joinpath("OWNER-RACE.md").write_bytes(b"UNPLANNED\n")

    with pytest.raises(ValueError, match="target Git tree/status/inventory changed"):
        migration_kernel.verify_plan_preimages(target, plan)

    assert snapshot.joinpath("receipt.json").is_file()
    assert target.joinpath("owned.txt").read_bytes() == b"OWNED-PREIMAGE\n"
    assert not target.joinpath("generated/nested/new.txt").exists()


def test_structure_validation_disables_python_bytecode_writes(tmp_path: Path) -> None:
    target = tmp_path / "structure-no-pyc"
    script = target / lifecycle.TARGET_PREFLIGHT_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text(
        "import helper_probe\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    script.parent.joinpath("helper_probe.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = lifecycle._run_harness_structure_check(target)

    assert result["returncode"] == 0
    assert not script.parent.joinpath("__pycache__").exists()


def test_current_harness_version_is_derived_from_the_verified_closure(
    tmp_path: Path,
) -> None:
    source = _make_release_source(
        tmp_path,
        contract_current_harness_version="v9.9.9",
    )

    with pytest.raises(
        ValueError,
        match="current_harness_skill_version disagrees with current closure",
    ):
        migration_kernel.load_migration_contract(source)


def test_released_from_closure_is_a_valid_automatic_profile_source(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path, released_from="v0.14.0")
    target = _prepare_exact_v1_target(tmp_path)

    plan = _plan(target, source)

    assert plan["decision"] == "automatic_migration_available"
    assert plan["upgrade_axis_evidence"]["artifact_source_from"]["release"] == (
        "v0.14.0"
    )
    assert plan["upgrade_axis_evidence"]["matched"] is True


def test_dynamic_profile_selection_and_apply_binding_do_not_depend_on_fixed_id(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    bundle = _trusted_development_bundle()
    dynamic_id = "canonical-v1.0-to-current"
    _rename_active_profile(bundle, dynamic_id)

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        today=date(2026, 8, 2),
        _migration_bundle=bundle,
    )

    assert plan["decision"] == "automatic_migration_available"
    assert plan["profile"]["profile_id"] == dynamic_id
    assert re.fullmatch(r"contracts/v1/.+\.json", plan["profile"]["profile_path"])
    assert re.fullmatch(r"[0-9a-f]{64}", plan["profile"]["profile_sha256"])
    assert lifecycle._approved_automatic_profile(bundle, plan["profile"])[
        "profile_id"
    ] == dynamic_id

    tampered = copy.deepcopy(plan["profile"])
    tampered["profile_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="approved profile identity differs"):
        lifecycle._approved_automatic_profile(bundle, tampered)


def test_zero_or_ambiguous_dynamic_profile_match_is_manual_zero_write(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    no_match_bundle = _trusted_development_bundle()
    only_profile = next(
        item
        for item in no_match_bundle["contract"]["profiles"]
        if item.get("automatic") is True
    )
    only_profile["from_closure_state"]["target_wrapper_version"] = "v0.13.0"
    no_match = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=no_match_bundle,
    )
    assert no_match["decision"] == "manual_migration_required"
    assert no_match["write_set"] == []
    assert no_match["automatic_profile_candidates"] == []
    assert no_match["ownership_evidence"]["from_closure_files"] == []
    assert no_match["ownership_evidence"]["manifest_patch_preconditions"] == []

    ambiguous_bundle = _trusted_development_bundle()
    original = next(
        item
        for item in ambiguous_bundle["contract"]["profiles"]
        if item.get("automatic") is True
    )
    duplicate = copy.deepcopy(original)
    duplicate["profile_id"] = "canonical-v1.0-to-current-duplicate"
    duplicate["adapter_payload"]["official_profile"]["profile_id"] = duplicate[
        "profile_id"
    ]
    duplicate["adapter_sha256"] = migration_kernel.canonical_json_sha256(
        duplicate["adapter_payload"]
    )
    ambiguous_bundle["contract"]["profiles"].append(duplicate)
    ambiguous = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=ambiguous_bundle,
    )
    assert ambiguous["decision"] == "manual_migration_required"
    assert ambiguous["write_set"] == []
    assert len(ambiguous["automatic_profile_candidates"]) == 2
    assert any(
        "multiple verified automatic profiles" in blocker
        for blocker in ambiguous["ownership_evidence"]["blockers"]
    )


def test_dynamic_selection_uses_exact_closure_preimages_before_profile_id(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    bundle = _trusted_development_bundle()
    original = next(
        item
        for item in bundle["contract"]["profiles"]
        if item.get("automatic") is True
    )
    mismatched = copy.deepcopy(original)
    mismatched["profile_id"] = "canonical-same-version-wrong-preimage"
    mismatched["adapter_payload"]["official_profile"]["profile_id"] = mismatched[
        "profile_id"
    ]
    mismatched["adapter_payload"]["trusted_preimages"][0]["sha256"] = "0" * 64
    mismatched["adapter_sha256"] = migration_kernel.canonical_json_sha256(
        mismatched["adapter_payload"]
    )
    bundle["contract"]["profiles"].append(mismatched)

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=bundle,
    )

    assert plan["decision"] == "automatic_migration_available"
    assert plan["profile"]["profile_id"] == original["profile_id"]
    assert [
        item["profile_id"] for item in plan["automatic_profile_candidates"]
    ] == [original["profile_id"]]


def test_unmodified_exact_from_closure_file_drift_is_manual_zero_write(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    onboarding = target / ".evozeus-wrapper/docs/onboarding.md"
    onboarding.write_bytes(onboarding.read_bytes() + b"\nOWNER-DRIFT\n")
    _git(target, "add", onboarding.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Drift an unchanged closure artifact")

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=_trusted_development_bundle(),
    )

    evidence = next(
        item
        for item in plan["ownership_evidence"]["from_closure_files"]
        if item["target_path"] == ".evozeus-wrapper/docs/onboarding.md"
    )
    assert evidence["kind"] == "exact"
    assert evidence["matched"] is False
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []


def test_rendered_from_closure_surface_is_preserved_without_automatic_write(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    rendered = target / ".evozeus-wrapper/CHANGELOG.md"
    rendered.write_bytes(b"OWNER-RENDERED-HISTORY\n")
    _git(target, "add", rendered.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Customize a preserved rendered surface")
    before = rendered.read_bytes()

    plan = _plan(target, source)

    evidence = next(
        item
        for item in plan["ownership_evidence"]["from_closure_files"]
        if item["target_path"] == ".evozeus-wrapper/CHANGELOG.md"
    )
    assert evidence == {
        "target_path": ".evozeus-wrapper/CHANGELOG.md",
        "kind": "rendered_template",
        "policy": "preserve_byte_exact_no_auto_upgrade",
        "planned_write": False,
    }
    assert plan["decision"] == "automatic_migration_available"
    assert rendered.relative_to(target).as_posix() not in {
        item["path"] for item in plan["write_set"]
    }

    applied = lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        snapshot_root=tmp_path / "rendered-preserve-snapshots",
        approved_plan_sha256=plan["plan_sha256"],
        remote_tag_resolver=_source_tag_resolver(source),
    )

    assert applied["status"] == "applied"
    assert rendered.read_bytes() == before


def test_present_from_closure_absent_file_is_manual_zero_write(tmp_path: Path) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    unexpected = target / lifecycle.TARGET_MIGRATION_CONTRACT
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("{}\n", encoding="utf-8")
    _git(target, "add", unexpected.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Add a file excluded by the from closure")

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=_trusted_development_bundle(),
    )

    evidence = next(
        item
        for item in plan["ownership_evidence"]["from_closure_files"]
        if item["target_path"] == lifecycle.TARGET_MIGRATION_CONTRACT
    )
    assert evidence["kind"] == "absent"
    assert evidence["matched"] is False
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []


def test_from_closure_manifest_owned_field_drift_is_manual_zero_write(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    manifest_path = target / lifecycle.TARGET_WRAPPER_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["target_infra_dir"] = ".owner-controlled"
    _write_json(manifest_path, manifest)
    _git(target, "add", manifest_path.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Drift a closure-owned manifest field")

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=_trusted_development_bundle(),
    )

    manifest_evidence = next(
        item
        for item in plan["ownership_evidence"]["from_closure_files"]
        if item["kind"] == "manifest_state"
    )
    field = next(
        item for item in manifest_evidence["fields"] if item["field"] == "target_infra_dir"
    )
    assert field["matched"] is False
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []


def test_manifest_patch_precondition_drift_is_evidence_backed_manual_zero_write(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    manifest_path = target / lifecycle.TARGET_WRAPPER_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["migration_contract"] = {"owner": "unexpected"}
    _write_json(manifest_path, manifest)
    _git(target, "add", manifest_path.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Drift a manifest patch precondition")

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=_trusted_development_bundle(),
    )

    precondition = next(
        item
        for item in plan["ownership_evidence"]["manifest_patch_preconditions"]
        if item["field"] == "migration_contract"
    )
    assert precondition["expected"] == {"state": "absent"}
    assert precondition["matched"] is False
    assert plan["decision"] == "manual_migration_required"
    assert plan["write_set"] == []


def test_from_closure_manifest_requirements_use_subset_and_normalized_block_path(
    tmp_path: Path,
) -> None:
    target = _prepare_exact_v1_target(tmp_path)
    bundle = _trusted_development_bundle()
    profile = next(
        item
        for item in bundle["contract"]["profiles"]
        if item.get("automatic") is True
    )
    manifest_entry = next(
        item
        for item in profile["adapter_payload"]["from_closure_files"]
        if item["kind"] == "manifest_state"
    )
    activation = bundle["contract"]["canonical_activation_block"]
    required_managed_file = ".codex/hooks.json"
    manifest_entry["owned_state"]["managed_files_require"] = [required_managed_file]
    manifest_entry["owned_state"]["managed_blocks"] = [
        {**copy.deepcopy(activation), "path_selector": "manifest.instruction_surface"}
    ]
    profile["adapter_sha256"] = migration_kernel.canonical_json_sha256(
        profile["adapter_payload"]
    )

    manifest_path = target / lifecycle.TARGET_WRAPPER_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert required_managed_file in manifest["managed_files"]
    manifest["managed_blocks"] = [
        {**copy.deepcopy(activation), "path": manifest["instruction_surface"]}
    ]
    _write_json(manifest_path, manifest)
    _git(target, "add", manifest_path.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Record normalized closure block evidence")

    matching = lifecycle._canonical_v1_upgrade_evidence(
        target,
        manifest,
        manifest["instruction_surface"],
        profile,
        activation,
    )
    managed_files_evidence = next(
        item
        for item in matching["from_closure_files"]
        if item["kind"] == "manifest_state"
    )
    assert next(
        item
        for item in managed_files_evidence["fields"]
        if item["field"] == "managed_files_require"
    )["matched"] is True
    assert next(
        item
        for item in managed_files_evidence["fields"]
        if item["field"] == "managed_blocks"
    )["matched"] is True

    manifest["managed_files"].remove(required_managed_file)
    manifest["managed_blocks"][0]["path"] = "OWNER.md"
    _write_json(manifest_path, manifest)
    _git(target, "add", manifest_path.relative_to(target).as_posix())
    _git(target, "commit", "-m", "Drift closure manifest semantics")
    drifted = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        _migration_bundle=bundle,
    )

    assert any(
        blocker == "from closure manifest state mismatch: managed_files_require"
        for blocker in drifted["ownership_evidence"]["blockers"]
    )
    assert any(
        blocker == "from closure manifest state mismatch: managed_blocks"
        for blocker in drifted["ownership_evidence"]["blockers"]
    )
    assert drifted["decision"] == "manual_migration_required"
    assert drifted["write_set"] == []


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
    planned_modes_before = {
        item["path"]: (
            stat.S_IMODE(target.joinpath(item["path"]).stat().st_mode)
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
    assert all(isinstance(item.get("postimage_mode"), int) for item in plan["write_set"])
    manifest_item = next(
        item
        for item in plan["write_set"]
        if item["path"] == lifecycle.TARGET_WRAPPER_MANIFEST
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_item["operation_sha256"])
    assert manifest_item["manifest_preconditions"]["wrapper_version"] == "v0.14.0"
    assert {
        item["value"]
        for item in manifest_item["manifest_patch"]
        if item["field"] == "wrapper_version"
    } == {"v0.15.0"}

    approval_required = lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        remote_tag_resolver=_source_tag_resolver(source),
    )
    assert approval_required["status"] == "approval_required"
    assert approval_required["writes"] is False

    snapshot_base = tmp_path / "trusted-snapshots"
    applied = lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        snapshot_root=snapshot_base,
        approved_plan_sha256=plan["plan_sha256"],
        remote_tag_resolver=_source_tag_resolver(source),
    )
    assert applied["status"] == "applied"
    assert applied["writes"] is True
    assert target.joinpath("SKILL.md").read_bytes() == protected_before
    record = target.joinpath(plan["migration_record"]).read_bytes()
    assert record == source.joinpath(
        "contracts/v1/migrations/history/harness-skill/v1.1.0/artifacts/"
        "generated/harness-skill-v1.0.0-to-v1.1.0.md"
    ).read_bytes()
    assert b"v0.14.0 -> v0.15.0" in record
    assert b"wrapper.json" in record
    assert b"2026-08-02" not in record
    for item in plan["write_set"]:
        assert stat.S_IMODE(target.joinpath(item["path"]).stat().st_mode) == item[
            "postimage_mode"
        ]

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
        assert (
            stat.S_IMODE(path.stat().st_mode) if path.is_file() else None
        ) == planned_modes_before[relative]


def test_exact_profile_rejects_a_matching_preimage_with_the_wrong_mode(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    preflight = target / lifecycle.TARGET_PREFLIGHT_SCRIPT
    preflight.chmod(0o644)

    plan = lifecycle.plan_target_layout_migration(
        target,
        latest_version="v0.15.0",
        today=date(2026, 8, 2),
        require_clean_git=False,
        wrapper_root=source,
        remote_tag_resolver=_source_tag_resolver(source),
    )

    evidence = next(
        item
        for item in plan["ownership_evidence"]["trusted_preimages"]
        if item["target_path"] == lifecycle.TARGET_PREFLIGHT_SCRIPT
    )
    assert evidence["expected_sha256"] == evidence["actual_sha256"]
    assert evidence["expected_mode"] == 0o755
    assert evidence["actual_mode"] == 0o644
    assert evidence["matched"] is False
    assert plan["decision"] == "manual_migration_required"
    assert plan["writes"] is False


def test_unpublished_or_locally_forged_tag_is_zero_write(tmp_path: Path) -> None:
    source = _make_release_source(tmp_path, publish_tag=False)
    target = _prepare_exact_v1_target(tmp_path)
    before = target.joinpath(lifecycle.TARGET_HARNESS_SKILL).read_bytes()

    plan = _plan(target, source)
    report = lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
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
        "v0.15.0",
        date(2026, 8, 2),
        wrapper_root=source,
        require_clean_git=True,
        approved_plan_sha256="sha256:" + "0" * 64,
    )
    assert mismatch["status"] == "blocked"
    assert mismatch["writes"] is False
    assert harness.read_bytes() == before

    harness.write_bytes(before + b"\nTOCTOU\n")
    with pytest.raises(ValueError, match="target Git tree/status/inventory changed"):
        migration_kernel.verify_plan_preimages(target, plan)
    harness.write_bytes(before)

    surface = target / "SKILL.md"
    surface_before = surface.read_bytes()
    surface.write_bytes(surface_before + b"\nPROTECTED-RACE\n")
    with pytest.raises(ValueError, match="target Git tree/status/inventory changed"):
        migration_kernel.verify_plan_preimages(target, plan)
    surface.write_bytes(surface_before)

    target.joinpath("OWNER-NOTE.md").write_text("dirty target\n", encoding="utf-8")
    dirty_plan = _plan(target, source)
    dirty_report = lifecycle.migrate_target_layout(
        target,
        "v0.15.0",
        date(2026, 8, 2),
        wrapper_root=source,
        approved_plan_sha256=dirty_plan["plan_sha256"],
    )
    assert dirty_plan["can_apply"] is False
    assert any("worktree is not clean" in item for item in dirty_plan["apply_blockers"])
    assert dirty_report["status"] == "blocked"
    assert dirty_report["writes"] is False
    assert harness.read_bytes() == before


def test_profile_postimage_mismatch_is_detected_before_snapshot_or_target_write(
    tmp_path: Path,
) -> None:
    source = _make_release_source(tmp_path)
    target = _prepare_exact_v1_target(tmp_path)
    bundle = migration_kernel.load_migration_contract(source)
    plan = _plan(target, source)
    bad_plan = copy.deepcopy(plan)
    replace_item = next(
        item for item in bad_plan["write_set"] if item["operation"] == "replace_exact"
    )
    replace_item["postimage_sha256"] = "sha256:" + "f" * 64
    bad_plan["plan_sha256"] = (
        "sha256:" + migration_kernel.migration_plan_digest(bad_plan)
    )
    replaced_path = target / replace_item["path"]
    before = replaced_path.read_bytes()
    snapshot_base = tmp_path / "postimage-snapshots"

    with pytest.raises(ValueError, match="migration operation differs from verified profile"):
        lifecycle._apply_canonical_v1_upgrade(
            target,
            bad_plan,
            date(2026, 8, 2),
            bundle,
            snapshot_base,
        )

    assert replaced_path.read_bytes() == before
    assert not snapshot_base.exists()

    bad_manifest_plan = copy.deepcopy(plan)
    manifest_item = next(
        item
        for item in bad_manifest_plan["write_set"]
        if item["path"] == lifecycle.TARGET_WRAPPER_MANIFEST
    )
    manifest_item["manifest_patch"][0]["value"] = "v9.9.9"
    bad_manifest_plan["plan_sha256"] = (
        "sha256:" + migration_kernel.migration_plan_digest(bad_manifest_plan)
    )
    with pytest.raises(ValueError, match="migration operation differs from verified profile"):
        lifecycle._apply_canonical_v1_upgrade(
            target,
            bad_manifest_plan,
            date(2026, 8, 2),
            bundle,
            snapshot_base,
        )

    assert replaced_path.read_bytes() == before
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
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=True,
            _migration_bundle=_trusted_development_bundle(),
        )

    after = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("force", [False, True])
def test_bootstrap_rejects_exact_bytes_with_the_wrong_mode_before_writing(
    tmp_path: Path,
    force: bool,
) -> None:
    target = tmp_path / f"bootstrap-wrong-mode-{force}"
    target.mkdir()
    target.joinpath("SKILL.md").write_text("# Owner Skill\n", encoding="utf-8")
    preflight = target / lifecycle.TARGET_PREFLIGHT_SCRIPT
    preflight.parent.mkdir(parents=True)
    preflight.write_bytes((ROOT / "scripts/evozeus_wrapper_preflight.py").read_bytes())
    preflight.chmod(0o644)

    with pytest.raises(ValueError, match="no exact trusted preimage"):
        bootstrap.copy_templates(
            target,
            _current_replacement_values(),
            force=force,
            _migration_bundle=_trusted_development_bundle(),
        )

    assert preflight.read_bytes() == (
        ROOT / "scripts/evozeus_wrapper_preflight.py"
    ).read_bytes()
    assert stat.S_IMODE(preflight.stat().st_mode) == 0o644
    assert {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    } == {"SKILL.md", lifecycle.TARGET_PREFLIGHT_SCRIPT}


def test_prerelease_v11_without_exact_contract_identity_is_manual_zero_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "prerelease-v11"
    target.mkdir()
    target.joinpath("SKILL.md").write_text(
        "# Business\n\n" + lifecycle.build_harness_activation_block() + "\n",
        encoding="utf-8",
    )
    bootstrap.copy_templates(
        target,
        _legacy_replacement_values(),
        force=False,
        _migration_bundle=_trusted_development_bundle(),
    )
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
        lambda _home: {
            "status": "not_installed",
            "any_registration_installed": False,
        },
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


def test_batch_propagates_the_current_targets_structured_rollback_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "current-rollback-failed"
    plan = {
        "stage": "harness_upgrade_all",
        "status": "planned",
        "writes": False,
        "errors": [],
        "latest_version": "v0.14.0",
        "targets": [
            {
                "repo": "MetaInFLow/current-rollback-failed",
                "target": str(target),
                "migration": {"plan_sha256": "sha256:" + "1" * 64},
            }
        ],
    }
    plan["batch_plan_sha256"] = "sha256:" + global_hook._batch_plan_digest(plan)
    failed_result = {
        "status": "rollback_failed",
        "writes": True,
        "rollback_verified": False,
        "target": str(target),
        "snapshot": str(tmp_path / "snapshot-current"),
        "error": "apply failed",
        "rollback_error": "snapshot validation failed",
    }

    monkeypatch.setattr(
        global_hook,
        "plan_upgrade_all",
        lambda *_args, **_kwargs: copy.deepcopy(plan),
    )
    monkeypatch.setattr(
        global_hook,
        "read_global_hook_status",
        lambda _home: {
            "status": "not_installed",
            "any_registration_installed": False,
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "migrate_target_layout",
        lambda *_args, **_kwargs: copy.deepcopy(failed_result),
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
    assert report["results"] == [failed_result]


@pytest.mark.parametrize("operation", ["create", "replace", "remove"])
def test_cleanup_identity_race_preserves_later_content_and_requires_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    target = tmp_path / f"cleanup-identity-race-{operation}"
    target.mkdir()
    destination = target / "managed.txt"
    preimage = b"PREIMAGE\n"
    expected = "sha256:" + hashlib.sha256(preimage).hexdigest()
    if operation in {"replace", "remove"}:
        destination.write_bytes(preimage)
    retirement = _retirement_root(tmp_path, f"cleanup-identity-race-{operation}")

    original_rename = migration_kernel._atomic_rename_noreplace_between_directories
    original_exchange = migration_kernel._atomic_exchange_between_directories
    raced = False

    def replace_named(parent_fd: int, name: str) -> None:
        os.unlink(name, dir_fd=parent_fd)
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            os.write(descriptor, b"LATER-CONCURRENT-CONTENT\n")
        finally:
            os.close(descriptor)

    def replace_before_noreplace(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        retired_name: str,
    ) -> None:
        nonlocal raced
        should_race = (
            operation == "create" and source.startswith("candidate-file-")
        ) or (
            operation == "remove" and source == "managed.txt"
        )
        if should_race and not raced:
            raced = True
            replace_named(source_parent_fd, source)
        original_rename(
            source_parent_fd,
            source,
            destination_parent_fd,
            retired_name,
        )

    def replace_before_exchange(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal raced
        if operation == "replace" and not raced:
            raced = True
            replace_named(destination_parent_fd, destination_name)
        original_exchange(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination_name,
        )

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_rename_noreplace_between_directories",
        replace_before_noreplace,
    )
    monkeypatch.setattr(
        migration_kernel,
        "_atomic_exchange_between_directories",
        replace_before_exchange,
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(
            ValueError,
            match="identity|CAS changed|cleanup_required",
        ):
            if operation == "create":
                secure_target.write_exact(
                    "managed.txt",
                    b"POSTIMAGE\n",
                    expected_preimage=None,
                    mode=0o644,
                )
            elif operation == "replace":
                secure_target.write_exact(
                    "managed.txt",
                    b"POSTIMAGE\n",
                    expected_preimage=expected,
                    expected_mode=0o644,
                    mode=0o644,
                )
            else:
                secure_target.remove_exact(
                    "managed.txt",
                    expected,
                    expected_mode=0o644,
                )

    preserved = [
        path
        for path in [destination, *retirement.iterdir()]
        if path.is_file() and path.read_bytes() == b"LATER-CONCURRENT-CONTENT\n"
    ]
    assert raced is True
    assert len(preserved) == 1
    if operation == "create":
        assert destination.read_bytes() == b"LATER-CONCURRENT-CONTENT\n"
    elif operation == "replace":
        assert destination.read_bytes() == b"POSTIMAGE\n"
    else:
        assert not destination.exists()


@pytest.mark.parametrize("operation", ["create", "replace", "remove", "rollback"])
def test_post_retirement_stat_replacement_is_never_unlinked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    target = tmp_path / f"post-retirement-stat-{operation}"
    target.mkdir()
    destination = target / "managed.txt"
    preimage = b"PREIMAGE\n"
    expected = "sha256:" + hashlib.sha256(preimage).hexdigest()
    retirement = _retirement_root(tmp_path, f"post-retirement-stat-{operation}")
    snapshot: Path | None = None
    if operation in {"replace", "remove"}:
        destination.write_bytes(preimage)
    elif operation == "rollback":
        target.joinpath("owned.txt").write_bytes(b"OWNED-PREIMAGE\n")
        target.joinpath("business.txt").write_bytes(b"PROTECTED-BUSINESS\n")
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
        _git(target, "config", "user.email", "target-test@example.invalid")
        _git(target, "config", "user.name", "Target Test")
        _git(target, "add", ".")
        _git(target, "commit", "-m", "retirement race fixture")
        plan = _secure_synthetic_plan(target)
        trusted_base = tmp_path / "post-retirement-stat-rollback-snapshots"
        snapshot = migration_kernel.create_migration_snapshot(
            target,
            plan,
            snapshot_root=trusted_base,
        )
        retirement = snapshot / "quarantine"
        created_item = next(
            item
            for item in plan["write_set"]
            if item["path"] == "generated/nested/new.txt"
        )
        with migration_kernel.SecureTargetFS(
            target,
            retirement_root=retirement,
        ) as secure_target:
            secure_target.write_exact(
                created_item["path"],
                b"CREATED-POSTIMAGE\n",
                expected_preimage=None,
                mode=created_item["postimage_mode"],
            )
        destination = target / created_item["path"]

    retirement_metadata = retirement.stat()
    retirement_identity = (
        retirement_metadata.st_dev,
        retirement_metadata.st_ino,
    )
    original_named_entry_identity = (
        migration_kernel.SecureTargetFS._named_entry_identity
    )
    original_unlink = os.unlink
    raced = False
    production_unlinks: list[str] = []

    def replace_after_retirement_stat(
        parent_fd: int,
        name: str,
    ) -> tuple[int, int, int]:
        nonlocal raced
        identity = original_named_entry_identity(parent_fd, name)
        parent_metadata = os.fstat(parent_fd)
        if (
            not raced
            and (parent_metadata.st_dev, parent_metadata.st_ino)
            == retirement_identity
            and stat.S_ISREG(identity[2])
            and not name.startswith("probe-")
        ):
            raced = True
            original_unlink(name, dir_fd=parent_fd)
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.write(descriptor, b"POST-STAT-CONCURRENT-CONTENT\n")
            finally:
                os.close(descriptor)
        return identity

    def forbid_production_unlink(
        path: object,
        *,
        dir_fd: int | None = None,
    ) -> None:
        del dir_fd
        production_unlinks.append(str(path))
        raise AssertionError("migration code must not unlink a named candidate")

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "_named_entry_identity",
        staticmethod(replace_after_retirement_stat),
    )
    monkeypatch.setattr(os, "unlink", forbid_production_unlink)

    if operation == "create":
        original_verify = migration_kernel.SecureTargetFS._verify_published_postimage

        def fail_after_publication(
            secure_target: migration_kernel.SecureTargetFS,
            *args: object,
            **kwargs: object,
        ) -> None:
            original_verify(secure_target, *args, **kwargs)
            raise ValueError("simulated post-publication failure")

        monkeypatch.setattr(
            migration_kernel.SecureTargetFS,
            "_verify_published_postimage",
            fail_after_publication,
        )

    if operation == "rollback":
        assert snapshot is not None
        with pytest.raises(ValueError, match="rollback_failed"):
            migration_kernel.rollback_migration_snapshot(
                target,
                snapshot,
                trusted_snapshot_root=snapshot.parent,
            )
    else:
        with migration_kernel.SecureTargetFS(
            target,
            retirement_root=retirement,
        ) as secure_target:
            if operation == "create":
                with pytest.raises(ValueError, match="published identity changed"):
                    secure_target.write_exact(
                        "managed.txt",
                        b"POSTIMAGE\n",
                        expected_preimage=None,
                        mode=0o644,
                    )
            elif operation == "replace":
                with pytest.raises(ValueError, match="published identity changed"):
                    secure_target.write_exact(
                        "managed.txt",
                        b"POSTIMAGE\n",
                        expected_preimage=expected,
                        expected_mode=0o644,
                        mode=0o644,
                    )
            else:
                with pytest.raises(ValueError, match="atomic retirement"):
                    secure_target.remove_exact(
                        "managed.txt",
                        expected,
                        expected_mode=0o644,
                    )

    preserved = [
        path
        for path in [destination, *retirement.rglob("*")]
        if path.is_file()
        and path.read_bytes() == b"POST-STAT-CONCURRENT-CONTENT\n"
    ]
    assert raced is True
    assert production_unlinks == []
    assert len(preserved) == 1


@pytest.mark.parametrize("replacement", ["symlink-root", "new-root-inode", "new-leaf-inode"])
def test_approved_target_binding_rejects_post_plan_identity_replacement_zero_write(
    tmp_path: Path,
    replacement: str,
) -> None:
    target = _prepare_secure_target(tmp_path, f"target-binding-{replacement}")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / f"target-binding-{replacement}-snapshots"
    original_owned = target.joinpath("owned.txt").read_bytes()

    if replacement == "new-leaf-inode":
        detached_leaf = tmp_path / "detached-owned.txt"
        target.joinpath("owned.txt").rename(detached_leaf)
        target.joinpath("owned.txt").write_bytes(original_owned)
        assert target.joinpath("owned.txt").stat().st_ino != detached_leaf.stat().st_ino
    else:
        replacement_root = tmp_path / f"replacement-{replacement}"
        shutil.copytree(target, replacement_root, symlinks=True)
        detached_root = tmp_path / f"detached-{replacement}"
        target.rename(detached_root)
        if replacement == "symlink-root":
            target.symlink_to(replacement_root, target_is_directory=True)
        else:
            replacement_root.rename(target)

    with pytest.raises(ValueError, match="(binding changed|Git tree/status/inventory changed)"):
        migration_kernel.create_migration_snapshot(
            target,
            plan,
            snapshot_root=trusted_base,
        )

    assert target.joinpath("owned.txt").read_bytes() == original_owned
    assert not target.joinpath("generated/nested/new.txt").exists()
    assert not trusted_base.exists()


def test_rollback_persists_in_progress_before_write_and_rolled_back_after_verify(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-state-order")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-state-order-snapshots"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    write_items = {item["path"]: item for item in plan["write_set"]}
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            "owned.txt",
            b"OWNED-POSTIMAGE\n",
            expected_preimage=write_items["owned.txt"]["preimage_sha256"],
            expected_mode=write_items["owned.txt"]["preimage_mode"],
            mode=0o644,
        )
        secure_target.write_exact(
            "generated/nested/new.txt",
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=0o644,
        )

    observed_states: list[str] = []
    original_write = migration_kernel.SecureTargetFS.write_exact

    def observe_first_target_write(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        if secure_target.target == target.resolve() and not observed_states:
            transaction = json.loads(
                snapshot.joinpath("transaction.json").read_text(encoding="utf-8")
            )
            observed_states.append(transaction["state"])
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            expected_mode=expected_mode,
            mode=mode,
        )

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "write_exact",
        observe_first_target_write,
    )
    result = migration_kernel.rollback_migration_snapshot(
        target,
        snapshot,
        trusted_snapshot_root=trusted_base,
    )

    transaction = json.loads(
        snapshot.joinpath("transaction.json").read_text(encoding="utf-8")
    )
    assert observed_states == ["rollback_in_progress"]
    assert transaction["state"] == "rolled_back"
    assert transaction["retained_quarantine"]
    assert all(
        item["kind"] in {"file", "directory"}
        for item in transaction["retained_quarantine"]
    )
    assert result["status"] == "rolled_back"
    assert not target.joinpath("generated").exists()


def test_partial_rollback_failure_is_persisted_as_rollback_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-partial-failure")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-partial-failure-snapshots"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    write_items = {item["path"]: item for item in plan["write_set"]}
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            "owned.txt",
            b"OWNED-POSTIMAGE\n",
            expected_preimage=write_items["owned.txt"]["preimage_sha256"],
            expected_mode=write_items["owned.txt"]["preimage_mode"],
            mode=0o644,
        )
        secure_target.write_exact(
            "generated/nested/new.txt",
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=0o644,
        )

    original_remove = migration_kernel.SecureTargetFS.remove_exact

    def fail_created_path_remove(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        expected_sha256: str,
        *,
        expected_mode: int | None = None,
    ) -> None:
        if str(raw) == "generated/nested/new.txt":
            raise OSError("simulated partial rollback failure")
        original_remove(
            secure_target,
            raw,
            expected_sha256,
            expected_mode=expected_mode,
        )

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "remove_exact",
        fail_created_path_remove,
    )
    with pytest.raises(ValueError, match="rollback_failed"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )

    transaction = json.loads(
        snapshot.joinpath("transaction.json").read_text(encoding="utf-8")
    )
    assert transaction["state"] == "rollback_failed"
    assert "simulated partial rollback failure" in transaction["error"]
    assert target.joinpath("owned.txt").read_bytes() == b"OWNED-PREIMAGE\n"
    assert target.joinpath("generated/nested/new.txt").read_bytes() == (
        b"CREATED-POSTIMAGE\n"
    )


def test_rollback_preserves_concurrent_file_in_created_directory_and_fails_state(
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-created-directory-race")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-created-directory-race-snapshots"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    write_items = {item["path"]: item for item in plan["write_set"]}
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            "generated/nested/new.txt",
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=0o644,
        )
    concurrent = target / "generated/nested/concurrent-owner.txt"
    concurrent.write_bytes(b"CONCURRENT-OWNER\n")

    with pytest.raises(
        ValueError,
        match="rollback_failed.*non-empty transaction-created directory",
    ):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )

    transaction = json.loads(
        snapshot.joinpath("transaction.json").read_text(encoding="utf-8")
    )
    assert transaction["state"] == "rollback_failed"
    preserved = [
        path
        for path in snapshot.joinpath("quarantine").rglob("*")
        if path.is_file() and path.read_bytes() == b"CONCURRENT-OWNER\n"
    ]
    assert len(preserved) == 1
    assert not target.joinpath("generated/nested").exists()
    assert not target.joinpath("generated/nested/new.txt").exists()


def test_rollback_rejects_an_empty_created_directory_inode_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = _prepare_secure_target(tmp_path, "rollback-empty-directory-race")
    plan = _secure_synthetic_plan(target)
    trusted_base = tmp_path / "rollback-empty-directory-race-snapshots"
    snapshot = migration_kernel.create_migration_snapshot(
        target,
        plan,
        snapshot_root=trusted_base,
    )
    created_item = next(
        item
        for item in plan["write_set"]
        if item["path"] == "generated/nested/new.txt"
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=snapshot / "quarantine",
    ) as secure_target:
        secure_target.write_exact(
            created_item["path"],
            b"CREATED-POSTIMAGE\n",
            expected_preimage=None,
            mode=created_item["postimage_mode"],
        )

    original_cleanup = migration_kernel.SecureTargetFS.cleanup_created_directories
    detached = tmp_path / "detached-created-directory"
    replacement_identity: tuple[int, int] | None = None

    def replace_empty_directory_before_cleanup(
        secure_target: migration_kernel.SecureTargetFS,
    ) -> list[str]:
        nonlocal replacement_identity
        nested = target / "generated/nested"
        nested.rename(detached)
        nested.mkdir()
        metadata = nested.stat()
        replacement_identity = (metadata.st_dev, metadata.st_ino)
        return original_cleanup(secure_target)

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "cleanup_created_directories",
        replace_empty_directory_before_cleanup,
    )

    with pytest.raises(ValueError, match="rollback_failed"):
        migration_kernel.rollback_migration_snapshot(
            target,
            snapshot,
            trusted_snapshot_root=trusted_base,
        )

    assert replacement_identity is not None
    retained_identities = {
        (path.stat().st_dev, path.stat().st_ino)
        for path in snapshot.joinpath("quarantine").iterdir()
        if path.is_dir()
    }
    assert replacement_identity in retained_identities
    assert detached.is_dir()
    assert not target.joinpath("generated/nested").exists()
    transaction = json.loads(
        snapshot.joinpath("transaction.json").read_text(encoding="utf-8")
    )
    assert transaction["state"] == "rollback_failed"


@pytest.mark.parametrize(
    "raw",
    [
        '{"key": 1, "key": 2}',
        '{"value": NaN}',
        '{"value": Infinity}',
        '{"value": -Infinity}',
        '{"value": 1e400}',
    ],
)
def test_migration_json_parser_rejects_ambiguous_or_nonfinite_values(
    raw: str,
) -> None:
    with pytest.raises(ValueError):
        migration_kernel._json_bytes_object(raw.encode("utf-8"), "audit fixture")


def test_migration_json_serializers_reject_nonfinite_values() -> None:
    with pytest.raises(ValueError):
        migration_kernel.canonical_json_sha256({"value": float("inf")})
    with pytest.raises(ValueError):
        migration_kernel.canonical_plan_bytes({"value": float("nan")})


def test_mutation_batch_rejects_an_inner_mount_before_any_target_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "inner-mount-zero-write"
    target.mkdir()
    mounted_parent = target / "mounted"
    mounted_parent.mkdir()
    retirement = _retirement_root(tmp_path, "inner-mount-zero-write")
    original_fstat = os.fstat
    mounted_identity = mounted_parent.stat()
    target_before = list(target.rglob("*"))

    def report_inner_mount(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        if (
            metadata.st_dev == mounted_identity.st_dev
            and metadata.st_ino == mounted_identity.st_ino
        ):
            fields = list(metadata)
            fields[2] = retirement.stat().st_dev + 1
            return os.stat_result(fields)
        return metadata

    monkeypatch.setattr(os, "fstat", report_inner_mount)
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(ValueError, match="mutation parent.*filesystem"):
            secure_target.prepare_mutation_batch(["mounted/artifact.txt"])

    assert list(target.rglob("*")) == target_before


def test_directory_publication_never_adopts_a_concurrent_named_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "directory-publication-race"
    target.mkdir()
    retirement = _retirement_root(tmp_path, "directory-publication-race")
    original_publish = migration_kernel._atomic_rename_noreplace_between_directories
    injected = False

    def publish_with_competitor(
        source_parent_fd: int,
        source: str,
        destination_parent_fd: int,
        destination: str,
    ) -> None:
        nonlocal injected
        if destination == "raced" and not injected:
            injected = True
            os.mkdir(destination, mode=0o755, dir_fd=destination_parent_fd)
        original_publish(
            source_parent_fd,
            source,
            destination_parent_fd,
            destination,
        )

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        monkeypatch.setattr(
            migration_kernel,
            "_atomic_rename_noreplace_between_directories",
            publish_with_competitor,
        )
        with pytest.raises(ValueError, match="directory create CAS changed"):
            secure_target.prepare_mutation_batch(["raced/artifact.txt"])

    assert injected is True
    assert list((target / "raced").iterdir()) == []
    assert not target.joinpath("raced/artifact.txt").exists()


def test_atomic_capability_failure_is_detected_before_target_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "atomic-capability-zero-write"
    target.mkdir()
    target.joinpath("SKILL.md").write_bytes(b"OWNER\n")
    retirement = _retirement_root(tmp_path, "atomic-capability-zero-write")
    target_before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }

    def reject_atomic_rename(*_args: object, **_kwargs: object) -> None:
        raise ValueError("simulated unsupported atomic rename filesystem")

    monkeypatch.setattr(
        migration_kernel,
        "_atomic_rename_between_directories",
        reject_atomic_rename,
    )
    with pytest.raises(ValueError, match="atomic rename capability"):
        migration_kernel.SecureTargetFS(
            target,
            retirement_root=retirement,
        )

    assert {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    } == target_before
    assert not any(path.name.startswith(".evozeus-") for path in target.rglob("*"))


def test_same_device_distinct_mount_identity_is_rejected_before_target_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "same-device-bind-mount"
    target.mkdir()
    mounted_parent = target / "mounted"
    mounted_parent.mkdir()
    retirement = _retirement_root(tmp_path, "same-device-bind-mount")
    original_fstat = os.fstat
    mounted_identity = mounted_parent.stat()
    retirement_identity = retirement.stat()
    target_before = list(target.rglob("*"))

    def simulated_mount_identity(descriptor: int) -> int | None:
        metadata = original_fstat(descriptor)
        if (
            metadata.st_dev == mounted_identity.st_dev
            and metadata.st_ino == mounted_identity.st_ino
        ):
            return 200
        if (
            metadata.st_dev == retirement_identity.st_dev
            and metadata.st_ino == retirement_identity.st_ino
        ):
            return 100
        return 100

    monkeypatch.setattr(
        migration_kernel,
        "_mount_identity_for_fd",
        simulated_mount_identity,
        raising=False,
    )
    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        with pytest.raises(ValueError, match="mount"):
            secure_target.prepare_mutation_batch(["mounted/artifact.txt"])

    assert list(target.rglob("*")) == target_before


def test_cross_mount_replace_failure_is_zero_change_without_target_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "cross-mount-replace"
    target.mkdir()
    destination = target / "managed.txt"
    destination.write_bytes(b"PREIMAGE\n")
    expected = "sha256:" + hashlib.sha256(b"PREIMAGE\n").hexdigest()
    retirement = _retirement_root(tmp_path, "cross-mount-replace")
    original_atomic = migration_kernel._atomic_rename_between_directories

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        calls: list[tuple[tuple[int, int], tuple[int, int], bool]] = []

        def reject_cross_mount(
            source_parent_fd: int,
            source: str,
            destination_parent_fd: int,
            destination_name: str,
            *,
            exchange: bool,
        ) -> None:
            source_parent = original_fstat(source_parent_fd)
            destination_parent = original_fstat(destination_parent_fd)
            source_identity = (source_parent.st_dev, source_parent.st_ino)
            destination_identity = (
                destination_parent.st_dev,
                destination_parent.st_ino,
            )
            calls.append((source_identity, destination_identity, exchange))
            if source_identity != destination_identity:
                raise OSError(errno.EXDEV, "simulated cross-mount rename")
            original_atomic(
                source_parent_fd,
                source,
                destination_parent_fd,
                destination_name,
                exchange=exchange,
            )

        original_fstat = os.fstat
        monkeypatch.setattr(
            migration_kernel,
            "_atomic_rename_between_directories",
            reject_cross_mount,
        )
        with pytest.raises(
            (OSError, ValueError),
            match="mount|atomic exchange|cleanup_required",
        ):
            secure_target.write_exact(
                "managed.txt",
                b"POSTIMAGE\n",
                expected_preimage=expected,
                expected_mode=0o644,
                mode=0o644,
            )

    assert calls
    assert calls[0][0] != calls[0][1]
    assert calls[0][2] is True
    assert destination.read_bytes() == b"PREIMAGE\n"
    assert not any(path.name.startswith(".evozeus-") for path in target.iterdir())


@pytest.mark.parametrize("candidate_kind", ["file", "directory"])
def test_failed_publish_never_creates_a_candidate_inside_the_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candidate_kind: str,
) -> None:
    target = tmp_path / f"external-candidate-{candidate_kind}"
    target.mkdir()
    retirement = _retirement_root(tmp_path, f"external-candidate-{candidate_kind}")
    target_identity = target.stat()
    retirement_identity = retirement.stat()
    observed: list[dict[str, object]] = []

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=retirement,
    ) as secure_target:
        def fail_final_publish(
            source_parent_fd: int,
            _source: str,
            destination_parent_fd: int,
            _destination: str,
            *,
            exchange: bool,
        ) -> None:
            source_parent = os.fstat(source_parent_fd)
            destination_parent = os.fstat(destination_parent_fd)
            observed.append(
                {
                    "source": (source_parent.st_dev, source_parent.st_ino),
                    "destination": (
                        destination_parent.st_dev,
                        destination_parent.st_ino,
                    ),
                    "exchange": exchange,
                    "target_entries": sorted(path.name for path in target.iterdir()),
                }
            )
            raise OSError(errno.EIO, "simulated final atomic publish failure")

        monkeypatch.setattr(
            migration_kernel,
            "_atomic_rename_between_directories",
            fail_final_publish,
        )
        with pytest.raises(
            (OSError, ValueError),
            match="atomic (publish|create)|cleanup_required",
        ):
            if candidate_kind == "file":
                secure_target.write_exact(
                    "artifact.txt",
                    b"POSTIMAGE\n",
                    expected_preimage=None,
                    mode=0o644,
                )
            else:
                secure_target.prepare_mutation_batch(["nested/artifact.txt"])

    assert observed
    assert observed[0]["source"] == (
        retirement_identity.st_dev,
        retirement_identity.st_ino,
    )
    assert observed[0]["destination"] == (
        target_identity.st_dev,
        target_identity.st_ino,
    )
    assert observed[0]["target_entries"] == []
    assert list(target.iterdir()) == []


@pytest.mark.skipif(
    not Path("/var").is_symlink(),
    reason="requires the macOS /var -> /private/var system alias",
)
def test_external_retirement_root_accepts_the_macos_var_system_alias(
    tmp_path: Path,
) -> None:
    target = tmp_path / "system-alias-target"
    target.mkdir()
    retirement = _retirement_root(tmp_path, "system-alias-retirement")
    canonical = retirement.resolve(strict=True)
    private_var = Path("/private/var")
    if not canonical.is_relative_to(private_var):
        pytest.skip("temporary directory is not under /private/var")
    aliased = Path("/var") / canonical.relative_to(private_var)

    with migration_kernel.SecureTargetFS(
        target,
        retirement_root=aliased,
    ) as secure_target:
        secure_target.write_exact(
            "artifact.txt",
            b"POSTIMAGE\n",
            expected_preimage=None,
            mode=0o644,
        )

    assert target.joinpath("artifact.txt").read_bytes() == b"POSTIMAGE\n"


def test_external_retirement_root_rejects_a_controlled_leaf_symlink(
    tmp_path: Path,
) -> None:
    target = tmp_path / "controlled-symlink-target"
    target.mkdir()
    retirement = _retirement_root(tmp_path, "controlled-symlink-retirement")
    alias = tmp_path / "controlled-retirement-link"
    alias.symlink_to(retirement, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        migration_kernel.SecureTargetFS(target, retirement_root=alias)


def test_force_never_replaces_a_mismatched_project_pointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    monkeypatch.setattr(bootstrap, "LOCAL_PROJECTS_DIR", projects)
    old_target = tmp_path / "old-target"
    old_target.mkdir()
    target = tmp_path / "new-target"
    target.mkdir()
    pointer = projects / "MetaInFLow" / "pointer-target"
    pointer.parent.mkdir(parents=True)
    pointer.symlink_to(old_target, target_is_directory=True)
    original_link = os.readlink(pointer)

    actions = bootstrap.ensure_project_pointer(
        target,
        "MetaInFLow/pointer-target",
        force=True,
    )

    assert os.readlink(pointer) == original_link
    assert pointer.resolve() == old_target
    assert any("manual" in action for action in actions)


def test_fresh_attach_rolls_back_templates_lineage_skill_and_manifest_as_one_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "fresh-attach-single-batch"
    target.mkdir()
    skill = target / "SKILL.md"
    skill.write_text(
        '---\nname: "fresh-attach"\ndescription: Owner bytes.\n---\n\n'
        "# Fresh Attach\n\nOWNER-BUSINESS-BYTES\n",
        encoding="utf-8",
    )
    skill.chmod(0o640)
    before_bytes = skill.read_bytes()
    before_mode = stat.S_IMODE(skill.stat().st_mode)
    manifest = {
        "wrapper_repo": lifecycle.WRAPPER_REPO,
        "wrapper_version": bootstrap.WRAPPER_VERSION,
        "canonical_repo": "MetaInFLow/fresh-attach",
        "instruction_surface": "SKILL.md",
    }
    original_write = migration_kernel.SecureTargetFS.write_exact

    def fail_after_manifest_publication(
        secure_target: migration_kernel.SecureTargetFS,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        mode: int,
        expected_mode: int | None = None,
    ) -> None:
        original_write(
            secure_target,
            raw,
            data,
            expected_preimage=expected_preimage,
            mode=mode,
            expected_mode=expected_mode,
        )
        if str(raw) == lifecycle.TARGET_WRAPPER_MANIFEST:
            raise OSError("simulated manifest directory fsync failure")

    monkeypatch.setattr(
        migration_kernel.SecureTargetFS,
        "write_exact",
        fail_after_manifest_publication,
    )
    with pytest.raises(ValueError, match="rolled back"):
        bootstrap.attach_harness_transaction(
            target,
            _current_replacement_values(),
            force=False,
            manifest=manifest,
            _migration_bundle=_trusted_development_bundle(),
        )

    assert skill.read_bytes() == before_bytes
    assert stat.S_IMODE(skill.stat().st_mode) == before_mode
    assert {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    } == {"SKILL.md"}


def test_fresh_attach_commits_the_complete_hash_and_mode_bound_artifact_set(
    tmp_path: Path,
) -> None:
    target = tmp_path / "fresh-attach-complete-batch"
    target.mkdir()
    skill = target / "SKILL.md"
    skill.write_text(
        '---\nname: "fresh-attach"\ndescription: Owner bytes.\n---\n\n'
        "# Fresh Attach\n\nOWNER-BUSINESS-BYTES\n",
        encoding="utf-8",
    )
    skill.chmod(0o640)
    bundle = _trusted_development_bundle()
    manifest = {
        "wrapper_repo": lifecycle.WRAPPER_REPO,
        "wrapper_version": bootstrap.WRAPPER_VERSION,
        "canonical_repo": "MetaInFLow/fresh-attach",
        "instruction_surface": "SKILL.md",
    }
    lineage = bootstrap.current_release_lineage_artifacts(bundle, target)

    bootstrap.attach_harness_transaction(
        target,
        _current_replacement_values(),
        force=False,
        manifest=manifest,
        _migration_bundle=bundle,
    )

    skill_bytes = skill.read_bytes()
    assert b"OWNER-BUSINESS-BYTES" in skill_bytes
    assert lifecycle.HARNESS_ENTRY_BEGIN.encode("utf-8") in skill_bytes
    assert stat.S_IMODE(skill.stat().st_mode) == 0o640
    manifest_path = target / lifecycle.TARGET_WRAPPER_MANIFEST
    assert manifest_path.read_bytes() == lifecycle.wrapper_manifest_bytes(manifest)
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o644
    for destination, expected, executable in lineage:
        assert destination.read_bytes() == expected
        assert stat.S_IMODE(destination.stat().st_mode) == (
            0o755 if executable else 0o644
        )
