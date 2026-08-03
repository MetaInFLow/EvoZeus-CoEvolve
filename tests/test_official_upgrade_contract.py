from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from scripts import evozeus_official_upgrade_verify as verifier


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "contracts/v1"
PROTOCOL_SHA256 = "688c156bfaebc4ed78508bfc93411b0bb5a53827f3c300e5668a765f8f7c5360"
CONTRACT_MANIFEST_REL = "contracts/v1/manifest.json"
LEGACY_PREFLIGHT_SHA256 = (
    "0ef6e008461dc8e61845ad6deae5fe239122c2415d81550a1e9d6e9838570aa1"
)
LEGACY_PREFLIGHT_SOURCE_COMMIT = "61b8340706db95995f9d31b2928c3363e473748d"
LEGACY_PREFLIGHT_SOURCE_TREE = "91bfe36a427fffa52be9f8f62c6a31a4ca7e8c81"
LEGACY_PREFLIGHT_SOURCE_BLOB = "c772f9bc7f252e36689c9ff6b55442724a97eb00"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate_blob(
    path: str,
    value: bytes | None,
    *,
    status: str = "modified",
    mode: str | None = "100644",
    object_type: str | None = "blob",
) -> verifier.CandidateBlob:
    return verifier.CandidateBlob(
        path=path,
        status=status,
        mode=mode,
        object_type=object_type,
        oid=None,
        loader=(None if value is None else lambda content=value: content),
    )


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _base_store() -> verifier.FilesystemStore:
    return verifier.FilesystemStore(ROOT)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _filesystem_mode(path: Path) -> str:
    return "100755" if path.stat().st_mode & 0o100 else "100644"


def _rebind_filesystem_manifest(
    root: Path,
    *,
    new_roles: dict[str, str] | None = None,
) -> None:
    manifest_path = root / CONTRACT_MANIFEST_REL
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {item["path"]: dict(item) for item in manifest["files"]}
    for relative, item in entries.items():
        source = root / "contracts/v1" / relative
        item["sha256"] = _sha256(source.read_bytes())
        item["mode"] = _filesystem_mode(source)
    for relative, role in (new_roles or {}).items():
        path = root / "contracts/v1" / relative
        entries[relative] = {
            "path": relative,
            "sha256": _sha256(path.read_bytes()),
            "mode": _filesystem_mode(path),
            "role": role,
        }
    manifest["files"] = [entries[path] for path in sorted(entries)]
    _write_json(manifest_path, manifest)


def _protocol_v1_base(tmp_path: Path) -> Path:
    root = tmp_path / "trusted-base"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    return root


def _candidate_contract_role(relative: str) -> str:
    if relative.startswith("migrations/history/"):
        return (
            "immutable-target-closure"
            if relative.endswith("/closure.json")
            else "immutable-target-closure-artifact"
        )
    if relative.startswith("migrations/profiles/"):
        return "official-upgrade-profile"
    raise AssertionError(f"test candidate adds an unknown contract path: {relative}")


def _bind_candidate_manifest(
    root: Path,
    changes: dict[str, verifier.CandidateBlob],
    *,
    new_roles: dict[str, str] | None = None,
    bundle_version: str | None = None,
    source_revision: str | None = None,
) -> None:
    current = changes.get(CONTRACT_MANIFEST_REL)
    if current is not None:
        assert current.loader is not None
        manifest = json.loads(current.loader().decode("utf-8"))
    else:
        manifest = json.loads((root / CONTRACT_MANIFEST_REL).read_text(encoding="utf-8"))
    if bundle_version is not None:
        manifest["bundle_version"] = bundle_version
    if source_revision is not None:
        manifest["source_revision"] = source_revision
    entries = {entry["path"]: dict(entry) for entry in manifest["files"]}
    for relative, entry in list(entries.items()):
        repository_path = "contracts/v1/" + relative
        change = changes.get(repository_path)
        if change is not None and change.status == "deleted":
            del entries[relative]
            continue
        if change is not None:
            assert change.loader is not None
            data = change.loader()
            entry["mode"] = change.mode
        else:
            base_path = root / repository_path
            if not base_path.exists():
                del entries[relative]
                continue
            data = base_path.read_bytes()
            entry["mode"] = _filesystem_mode(base_path)
        entry["sha256"] = _sha256(data)
    for repository_path, change in changes.items():
        if (
            not repository_path.startswith("contracts/v1/")
            or repository_path == CONTRACT_MANIFEST_REL
            or change.status == "deleted"
        ):
            continue
        relative = repository_path.removeprefix("contracts/v1/")
        if relative in entries:
            continue
        assert change.loader is not None
        role = (new_roles or {}).get(relative)
        if role is None:
            role = _candidate_contract_role(relative)
        entries[relative] = {
            "path": relative,
            "sha256": _sha256(change.loader()),
            "mode": change.mode,
            "role": role,
        }
    manifest["files"] = [entries[path] for path in sorted(entries)]
    changes[CONTRACT_MANIFEST_REL] = _candidate_blob(
        CONTRACT_MANIFEST_REL,
        _json_bytes(manifest),
    )


def _candidate_star(
    root: Path,
) -> tuple[
    dict[str, verifier.CandidateBlob],
    verifier.ConstructionRevisionResolver,
    str,
]:
    construction_revision = "a" * 40
    head_sha = "b" * 40
    v11_relative = "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
    v12_relative = "contracts/v1/migrations/history/harness-skill/v1.2.0/closure.json"
    v11 = json.loads((root / v11_relative).read_text(encoding="utf-8"))
    v12 = json.loads(json.dumps(v11))
    v12["closure_version"] = "v1.2.0"
    v12["source"] = {
        "repository": "MetaInFLow/EvoZeus-CoEvolve",
        "construction_revision": construction_revision,
        "release_status": "release_required_for_apply",
        "required_release": "v0.16.0",
    }
    v12["state"] = {
        **v12["state"],
        "target_wrapper_version": "v0.16.0",
        "contract_bundle_version": "v1.3.0",
        "harness_skill_version": "v1.2.0",
    }
    changes: dict[str, verifier.CandidateBlob] = {}
    migration_contract = json.loads(
        (root / verifier.MIGRATION_CONTRACT_REL).read_text(encoding="utf-8")
    )
    migration_contract["current_harness_skill_version"] = "v1.2.0"
    migration_contract_data = _json_bytes(migration_contract)
    migration_contract_sha256 = _sha256(migration_contract_data)
    changes[verifier.MIGRATION_CONTRACT_REL] = _candidate_blob(
        verifier.MIGRATION_CONTRACT_REL,
        migration_contract_data,
    )
    construction_files: dict[str, verifier.ConstructionBlob] = {}
    skill_target = ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md"
    skill_source = "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md"
    skill_before: dict[str, object] | None = None
    skill_after: dict[str, object] | None = None
    for item in v12["files"]:
        if item["target_path"] == ".evozeus-wrapper/wrapper.json":
            item["owned_state"] = {
                **item["owned_state"],
                "wrapper_version": "v0.16.0",
                "harness_skill_version": "v1.2.0",
                "migration_contract": {
                    **item["owned_state"]["migration_contract"],
                    "sha256": f"sha256:{migration_contract_sha256}",
                },
            }
        artifact = item.get("artifact_path")
        if artifact is None:
            continue
        old_artifact = (Path(v11_relative).parent / artifact).as_posix()
        new_artifact = (Path(v12_relative).parent / artifact).as_posix()
        data = (root / old_artifact).read_bytes()
        if item["target_path"] == ".evozeus-wrapper/contracts/harness-migration-contract-v1.json":
            data = migration_contract_data
            item["sha256"] = _sha256(data)
        if item["target_path"] == skill_target:
            skill_before = next(
                entry for entry in v11["files"] if entry["target_path"] == skill_target
            )
            data += b"\n<!-- candidate-harness-v1.2 -->\n"
            item["sha256"] = _sha256(data)
            skill_after = item
            changes[skill_source] = _candidate_blob(skill_source, data)
        artifact_mode = (
            "100644"
            if item.get("materialization", {}).get("mode_policy")
            == "set_declared_executable"
            else item["mode"]
        )
        changes[new_artifact] = _candidate_blob(
            new_artifact,
            data,
            status="added",
            mode=artifact_mode,
        )
        source_path = item.get("source_path")
        if source_path is not None and item.get("source_binding") == "construction_revision":
            source_data = data if source_path == skill_source else (root / source_path).read_bytes()
            source_mode = artifact_mode
            base_mode = (
                "100755" if (root / source_path).stat().st_mode & 0o100 else "100644"
            )
            if source_path in changes or source_mode != base_mode:
                changes[source_path] = _candidate_blob(
                    source_path,
                    source_data,
                    mode=source_mode,
                )
            construction_files[source_path] = verifier.ConstructionBlob(
                path=source_path,
                mode=source_mode,
                data=source_data,
            )
    current_ledger_target = (
        ".evozeus-wrapper/docs/migrations/"
        "harness-skill-v1.1.0-to-v1.2.0.md"
    )
    current_ledger_artifact = (
        "artifacts/generated/harness-skill-v1.1.0-to-v1.2.0.md"
    )
    current_ledger_data = (
        b"# Harness Skill migration v1.1.0 to v1.2.0\n\n"
        b"This deterministic ledger records the exact current Harness hop.\n"
    )
    current_ledger_entry = {
        "target_path": current_ledger_target,
        "kind": "exact",
        "mode": "100644",
        "ownership": "wrapper_managed",
        "artifact_path": current_ledger_artifact,
        "sha256": _sha256(current_ledger_data),
        "materialization": {
            "policy": "copy_exact",
            "generated_release_artifact": True,
        },
        "source_binding": "generated_release_artifact",
    }
    v12["files"].append(current_ledger_entry)
    v12["files"] = sorted(v12["files"], key=lambda item: item["target_path"])
    changes[(Path(v12_relative).parent / current_ledger_artifact).as_posix()] = (
        _candidate_blob(
            (Path(v12_relative).parent / current_ledger_artifact).as_posix(),
            current_ledger_data,
            status="added",
        )
    )
    assert skill_before is not None and skill_after is not None
    migration_contract_target = (
        ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
    )
    migration_contract_before = next(
        entry
        for entry in v11["files"]
        if entry["target_path"] == migration_contract_target
    )
    migration_contract_after = next(
        entry
        for entry in v12["files"]
        if entry["target_path"] == migration_contract_target
    )
    changes[v12_relative] = _candidate_blob(
        v12_relative,
        _json_bytes(v12),
        status="added",
    )
    v12_sha256 = _sha256(_json_bytes(v12))
    protocol_sha256 = _sha256((root / verifier.PROTOCOL_REL).read_bytes())
    old_profile = json.loads(
        (
            root
            / "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
        ).read_text(encoding="utf-8")
    )
    direct_v10 = json.loads(json.dumps(old_profile))
    direct_v10["profile_id"] = "canonical-v1.0-to-v1.2"
    direct_v10["protocol"]["sha256"] = protocol_sha256
    direct_v10["to_closure"] = {
        "path": "migrations/history/harness-skill/v1.2.0/closure.json",
        "sha256": v12_sha256,
    }
    direct_v10["release_axis"]["target_wrapper_to"] = "v0.16.0"
    direct_v10["release_axis"]["artifact_source_to"]["release"] = "v0.16.0"
    for operation in direct_v10["operations"]:
        if operation["target_path"] == skill_target:
            operation["postimage"] = {
                "artifact_path": (
                    "migrations/history/harness-skill/v1.2.0/"
                    + str(skill_after["artifact_path"])
                ),
                "sha256": skill_after["sha256"],
                "mode": skill_after["mode"],
            }
        if operation["target_path"] == migration_contract_target:
            operation["postimage"] = {
                "artifact_path": (
                    "migrations/history/harness-skill/v1.2.0/"
                    + str(migration_contract_after["artifact_path"])
                ),
                "sha256": migration_contract_after["sha256"],
                "mode": migration_contract_after["mode"],
            }
        if operation["target_path"] == ".evozeus-wrapper/wrapper.json":
            for action in operation["patch"]:
                if action["field"] == "wrapper_version":
                    action["value"] = "v0.16.0"
                if action["field"] == "harness_skill_version":
                    action["value"] = "v1.2.0"
                if action["field"] == "migration_contract":
                    action["value"] = next(
                        item["owned_state"]["migration_contract"]
                        for item in v12["files"]
                        if item["target_path"]
                        == ".evozeus-wrapper/wrapper.json"
                    )
    current_ledger_operation = {
        "change_id": "create:" + current_ledger_target,
        "type": "create_exact",
        "target_path": current_ledger_target,
        "preimage": {"state": "absent"},
        "postimage": {
            "artifact_path": (
                "migrations/history/harness-skill/v1.2.0/"
                + current_ledger_artifact
            ),
            "sha256": current_ledger_entry["sha256"],
            "mode": "100644",
        },
    }
    old_ledger_index = next(
        index
        for index, operation in enumerate(direct_v10["operations"])
        if operation["target_path"].endswith(
            "harness-skill-v1.0.0-to-v1.1.0.md"
        )
    )
    direct_v10["operations"].insert(old_ledger_index + 1, current_ledger_operation)
    direct_v11 = {
        "schema_version": "evozeus.coevolve.official-upgrade-profile.v1",
        "profile_id": "canonical-v1.1-to-v1.2",
        "profile_version": "v1.0.0",
        "protocol": {
            "path": "migrations/protocols/official-upgrade-protocol-v1.json",
            "sha256": protocol_sha256,
        },
        "from_closure": {
            "path": "migrations/history/harness-skill/v1.1.0/closure.json",
            "sha256": _sha256((root / v11_relative).read_bytes()),
        },
        "to_closure": {
            "path": "migrations/history/harness-skill/v1.2.0/closure.json",
            "sha256": v12_sha256,
        },
        "release_axis": {
            "target_wrapper_from": "v0.15.0",
            "target_wrapper_to": "v0.16.0",
            "artifact_source_from": {
                "kind": "construction_revision",
                "revision": v11["source"]["construction_revision"],
                "release": "v0.15.0",
            },
            "artifact_source_to": {
                "kind": "required_release",
                "release": "v0.16.0",
                "binding": "contract_bundle.source_revision",
            },
        },
        "automatic": True,
        "operations": [
            {
                "change_id": "replace:" + migration_contract_target,
                "type": "replace_exact",
                "target_path": migration_contract_target,
                "preimage": {
                    "sha256": migration_contract_before["sha256"],
                    "mode": migration_contract_before["mode"],
                },
                "postimage": {
                    "artifact_path": (
                        "migrations/history/harness-skill/v1.2.0/"
                        + str(migration_contract_after["artifact_path"])
                    ),
                    "sha256": migration_contract_after["sha256"],
                    "mode": migration_contract_after["mode"],
                },
            },
            {
                "change_id": "replace:" + skill_target,
                "type": "replace_exact",
                "target_path": skill_target,
                "preimage": {
                    "sha256": skill_before["sha256"],
                    "mode": skill_before["mode"],
                },
                "postimage": {
                    "artifact_path": (
                        "migrations/history/harness-skill/v1.2.0/"
                        + str(skill_after["artifact_path"])
                    ),
                    "sha256": skill_after["sha256"],
                    "mode": skill_after["mode"],
                },
            },
            current_ledger_operation,
            {
                "change_id": "manifest:.evozeus-wrapper/wrapper.json",
                "type": "manifest_patch",
                "target_path": ".evozeus-wrapper/wrapper.json",
                "encoding": "utf-8-json-indent-2-lf",
                "preserve_unlisted_fields": True,
                "preconditions": {
                    "wrapper_version": "v0.15.0",
                    "harness_skill_version": "v1.1.0",
                },
                "patch": [
                    {
                        "action": "replace",
                        "field": "wrapper_version",
                        "value": "v0.16.0",
                    },
                    {
                        "action": "replace",
                        "field": "harness_skill_version",
                        "value": "v1.2.0",
                    },
                    {
                        "action": "replace",
                        "field": "migration_contract",
                        "value": next(
                            item["owned_state"]["migration_contract"]
                            for item in v12["files"]
                            if item["target_path"]
                            == ".evozeus-wrapper/wrapper.json"
                        ),
                    },
                ],
            },
        ],
        "deferred_rendered_surfaces": old_profile["deferred_rendered_surfaces"],
        "protected_business_surfaces": old_profile["protected_business_surfaces"],
        "fallback": old_profile["fallback"],
    }
    profile_entries = []
    for filename, profile in (
        ("canonical-v1.0-to-v1.2-v1.json", direct_v10),
        ("canonical-v1.1-to-v1.2-v1.json", direct_v11),
    ):
        relative = "contracts/v1/migrations/profiles/" + filename
        data = _json_bytes(profile)
        changes[relative] = _candidate_blob(relative, data, status="added")
        profile_entries.append(
            {
                "id": profile["profile_id"],
                "version": profile["profile_version"],
                "path": "migrations/profiles/" + filename,
                "sha256": _sha256(data),
            }
        )
    closure_pointer = {
        "schema_version": "evozeus.coevolve.current-pointer.v1",
        "pointer_id": "using-evozeus-harness-current-closure",
        "entries": [
            {
                "id": "using-evozeus-harness",
                "version": "v1.2.0",
                "path": "migrations/history/harness-skill/v1.2.0/closure.json",
                "sha256": v12_sha256,
            }
        ],
    }
    profile_pointer = {
        "schema_version": "evozeus.coevolve.current-pointer.v1",
        "pointer_id": "official-upgrade-current-profiles",
        "entries": profile_entries,
    }
    changes[verifier.HISTORY_CURRENT_REL] = _candidate_blob(
        verifier.HISTORY_CURRENT_REL,
        _json_bytes(closure_pointer),
    )
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(profile_pointer),
    )
    _bind_candidate_manifest(
        root,
        changes,
        bundle_version="v1.3.0",
        source_revision="v0.16.0",
    )

    def resolver(
        repository: str,
        revision: str,
        resolved_head: str,
        source_paths: frozenset[str],
    ) -> verifier.ConstructionRevisionEvidence:
        if revision == construction_revision:
            resolved_files = {
                path: construction_files[path] for path in source_paths
            }
        else:
            historical_closure = next(
                json.loads(path.read_text(encoding="utf-8"))
                for path in (
                    root / "contracts/v1/migrations/history/harness-skill"
                ).glob("v*/closure.json")
                if json.loads(path.read_text(encoding="utf-8"))["source"]
                ["construction_revision"]
                == revision
            )
            historical_root = (
                root
                / "contracts/v1/migrations/history/harness-skill"
                / historical_closure["closure_version"]
            )
            historical_entries = {
                item["source_path"]: item
                for item in historical_closure["files"]
                if item.get("source_binding") == "construction_revision"
            }
            resolved_files = {
                path: verifier.ConstructionBlob(
                    path=path,
                    mode=(
                        "100755"
                        if (
                            historical_root
                            / historical_entries[path]["artifact_path"]
                        ).stat().st_mode
                        & 0o100
                        else "100644"
                    ),
                    data=(
                        historical_root / historical_entries[path]["artifact_path"]
                    ).read_bytes(),
                )
                for path in source_paths
            }
        return verifier.ConstructionRevisionEvidence(
            repository=repository,
            revision=revision,
            head_sha=resolved_head,
            is_ancestor=True,
            files=resolved_files,
        )

    return changes, resolver, head_sha


def _fault_candidate_release_binding(
    root: Path,
    changes: dict[str, verifier.CandidateBlob],
    fault: str,
) -> None:
    closure_path = (
        "contracts/v1/migrations/history/harness-skill/v1.2.0/closure.json"
    )
    closure_blob = changes[closure_path]
    assert closure_blob.loader is not None
    closure = json.loads(closure_blob.loader().decode("utf-8"))
    if fault == "contract_bundle_version":
        closure["state"]["contract_bundle_version"] = "v9.9.9"
    elif fault == "required_release":
        closure["source"]["required_release"] = "v0.17.0"
    elif fault == "target_wrapper_version":
        closure["state"]["target_wrapper_version"] = "v0.17.0"
    elif fault == "current_harness_skill_version":
        contract_blob = changes[verifier.MIGRATION_CONTRACT_REL]
        assert contract_blob.loader is not None
        contract = json.loads(contract_blob.loader().decode("utf-8"))
        contract["current_harness_skill_version"] = "v9.9.9"
        changes[verifier.MIGRATION_CONTRACT_REL] = _candidate_blob(
            verifier.MIGRATION_CONTRACT_REL,
            _json_bytes(contract),
        )
    else:
        raise AssertionError(f"unknown release binding fault: {fault}")

    closure_data = _json_bytes(closure)
    closure_sha256 = _sha256(closure_data)
    changes[closure_path] = _candidate_blob(
        closure_path,
        closure_data,
        status="added",
    )
    history_pointer_blob = changes[verifier.HISTORY_CURRENT_REL]
    assert history_pointer_blob.loader is not None
    history_pointer = json.loads(history_pointer_blob.loader().decode("utf-8"))
    history_pointer["entries"][0]["sha256"] = closure_sha256
    changes[verifier.HISTORY_CURRENT_REL] = _candidate_blob(
        verifier.HISTORY_CURRENT_REL,
        _json_bytes(history_pointer),
    )
    profile_pointer_blob = changes[verifier.PROFILES_CURRENT_REL]
    assert profile_pointer_blob.loader is not None
    profile_pointer = json.loads(profile_pointer_blob.loader().decode("utf-8"))
    for entry in profile_pointer["entries"]:
        profile_path = "contracts/v1/" + entry["path"]
        profile_blob = changes[profile_path]
        assert profile_blob.loader is not None
        profile = json.loads(profile_blob.loader().decode("utf-8"))
        profile["to_closure"]["sha256"] = closure_sha256
        profile_data = _json_bytes(profile)
        changes[profile_path] = _candidate_blob(
            profile_path,
            profile_data,
            status=profile_blob.status,
            mode=profile_blob.mode,
        )
        entry["sha256"] = _sha256(profile_data)
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(profile_pointer),
    )
    _bind_candidate_manifest(root, changes)


def _bind_candidate_bundle_source(
    root: Path,
    changes: dict[str, verifier.CandidateBlob],
    *,
    role: str = "target-closure-source",
) -> str:
    closure_path = (
        "contracts/v1/migrations/history/harness-skill/v1.2.0/closure.json"
    )
    closure_blob = changes[closure_path]
    assert closure_blob.loader is not None
    closure = json.loads(closure_blob.loader().decode("utf-8"))
    target_path = ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py"
    closure_entry = next(
        item for item in closure["files"] if item["target_path"] == target_path
    )
    artifact_path = (
        Path(closure_path).parent / closure_entry["artifact_path"]
    ).as_posix()
    artifact_blob = changes[artifact_path]
    assert artifact_blob.loader is not None
    source_path = "contracts/v1/core-snapshots/evozeus_wrapper_preflight.py"
    closure_entry["source_path"] = source_path
    changes[source_path] = _candidate_blob(
        source_path,
        artifact_blob.loader(),
        status="added",
        mode=closure_entry["mode"],
    )

    closure_data = _json_bytes(closure)
    closure_sha256 = _sha256(closure_data)
    changes[closure_path] = _candidate_blob(
        closure_path,
        closure_data,
        status="added",
    )
    history_pointer_blob = changes[verifier.HISTORY_CURRENT_REL]
    assert history_pointer_blob.loader is not None
    history_pointer = json.loads(history_pointer_blob.loader().decode("utf-8"))
    history_pointer["entries"][0]["sha256"] = closure_sha256
    changes[verifier.HISTORY_CURRENT_REL] = _candidate_blob(
        verifier.HISTORY_CURRENT_REL,
        _json_bytes(history_pointer),
    )

    profile_pointer_blob = changes[verifier.PROFILES_CURRENT_REL]
    assert profile_pointer_blob.loader is not None
    profile_pointer = json.loads(profile_pointer_blob.loader().decode("utf-8"))
    for pointer_entry in profile_pointer["entries"]:
        profile_path = "contracts/v1/" + pointer_entry["path"]
        profile_blob = changes[profile_path]
        assert profile_blob.loader is not None
        profile = json.loads(profile_blob.loader().decode("utf-8"))
        profile["to_closure"]["sha256"] = closure_sha256
        profile_data = _json_bytes(profile)
        changes[profile_path] = _candidate_blob(
            profile_path,
            profile_data,
            status=profile_blob.status,
            mode=profile_blob.mode,
        )
        pointer_entry["sha256"] = _sha256(profile_data)
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(profile_pointer),
    )
    _bind_candidate_manifest(
        root,
        changes,
        new_roles={source_path.removeprefix("contracts/v1/"): role},
    )
    return source_path


def _attempt_to_bind_protected_consumer_as_skill_source(
    root: Path,
    changes: dict[str, verifier.CandidateBlob],
    protected_path: str,
) -> None:
    closure_path = (
        "contracts/v1/migrations/history/harness-skill/v1.2.0/closure.json"
    )
    closure_blob = changes[closure_path]
    assert closure_blob.loader is not None
    closure = json.loads(closure_blob.loader().decode("utf-8"))
    skill_target = ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md"
    skill_entry = next(
        item for item in closure["files"] if item["target_path"] == skill_target
    )
    artifact_path = (Path(closure_path).parent / skill_entry["artifact_path"]).as_posix()
    artifact_blob = changes[artifact_path]
    assert artifact_blob.loader is not None
    original_source = skill_entry["source_path"]
    skill_entry["source_path"] = protected_path
    changes.pop(original_source)
    changes[protected_path] = _candidate_blob(protected_path, artifact_blob.loader())

    closure_data = _json_bytes(closure)
    closure_sha256 = _sha256(closure_data)
    changes[closure_path] = _candidate_blob(closure_path, closure_data, status="added")
    history_pointer = json.loads(
        changes[verifier.HISTORY_CURRENT_REL].loader().decode("utf-8")
    )
    history_pointer["entries"][0]["sha256"] = closure_sha256
    changes[verifier.HISTORY_CURRENT_REL] = _candidate_blob(
        verifier.HISTORY_CURRENT_REL,
        _json_bytes(history_pointer),
    )
    profile_pointer = json.loads(
        changes[verifier.PROFILES_CURRENT_REL].loader().decode("utf-8")
    )
    for entry in profile_pointer["entries"]:
        profile_path = "contracts/v1/" + entry["path"]
        profile_blob = changes[profile_path]
        assert profile_blob.loader is not None
        profile = json.loads(profile_blob.loader().decode("utf-8"))
        profile["to_closure"]["sha256"] = closure_sha256
        profile_data = _json_bytes(profile)
        changes[profile_path] = _candidate_blob(
            profile_path,
            profile_data,
            status=profile_blob.status,
            mode=profile_blob.mode,
        )
        entry["sha256"] = _sha256(profile_data)
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(profile_pointer),
    )
    _bind_candidate_manifest(root, changes)


def _relocate_candidate_closure(
    root: Path,
    changes: dict[str, verifier.CandidateBlob],
    destination: str,
) -> None:
    source = "contracts/v1/migrations/history/harness-skill/v1.2.0/closure.json"
    source_parent = str(Path(source).parent)
    destination_parent = str(Path(destination).parent)
    new_roles: dict[str, str] = {}
    for path in [item for item in changes if item.startswith(source_parent + "/")]:
        blob = changes.pop(path)
        assert blob.loader is not None
        relocated = destination_parent + path.removeprefix(source_parent)
        changes[relocated] = _candidate_blob(
            relocated,
            blob.loader(),
            status=blob.status,
            mode=blob.mode,
            object_type=blob.object_type,
        )
        new_roles[relocated.removeprefix("contracts/v1/")] = (
            "immutable-target-closure"
            if path == source
            else "immutable-target-closure-artifact"
        )

    destination_relative = destination.removeprefix("contracts/v1/")
    source_relative = source.removeprefix("contracts/v1/")
    history_pointer = json.loads(
        changes[verifier.HISTORY_CURRENT_REL].loader().decode("utf-8")
    )
    history_pointer["entries"][0]["path"] = destination_relative
    changes[verifier.HISTORY_CURRENT_REL] = _candidate_blob(
        verifier.HISTORY_CURRENT_REL,
        _json_bytes(history_pointer),
    )

    profile_pointer = json.loads(
        changes[verifier.PROFILES_CURRENT_REL].loader().decode("utf-8")
    )
    for entry in profile_pointer["entries"]:
        profile_path = "contracts/v1/" + entry["path"]
        profile_blob = changes[profile_path]
        assert profile_blob.loader is not None
        profile = json.loads(profile_blob.loader().decode("utf-8"))
        profile["to_closure"]["path"] = destination_relative
        for operation in profile["operations"]:
            postimage = operation.get("postimage")
            if isinstance(postimage, dict) and isinstance(
                postimage.get("artifact_path"), str
            ):
                postimage["artifact_path"] = postimage["artifact_path"].replace(
                    str(Path(source_relative).parent),
                    str(Path(destination_relative).parent),
                    1,
                )
        profile_data = _json_bytes(profile)
        changes[profile_path] = _candidate_blob(
            profile_path,
            profile_data,
            status=profile_blob.status,
            mode=profile_blob.mode,
        )
        entry["sha256"] = _sha256(profile_data)
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(profile_pointer),
    )
    _bind_candidate_manifest(root, changes, new_roles=new_roles)


def _profile() -> tuple[str, dict[str, object]]:
    relative = "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
    return relative, json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_current_official_upgrade_catalog_is_hash_closed() -> None:
    report = verifier.verify_catalog(_base_store())

    assert report == {
        "status": "verified",
        "protocol": "evozeus-official-upgrade@v1.0.0",
        "current_closure": (
            "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
        ),
        "current_closure_version": "v1.1.0",
        "profiles": ["canonical-v1.0-to-v1.1@v1.0.0"],
        "supervised_legacy_profiles": [
            {
                "identity": "legacy-v0.14-three-section-to-canonical-v1.1@v1.0.0",
                "active_for_current": True,
                "runtime_apply": "not_implemented",
                "static_write_set": [
                    {
                        "target_path": ".evozeus-wrapper/contracts/harness-migration-contract-v1.json",
                        "type": "create_exact",
                    },
                    {
                        "target_path": ".evozeus-wrapper/docs/migrations/harness-skill-v1.0.0-to-v1.1.0.md",
                        "type": "create_exact",
                    },
                    {
                        "target_path": ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py",
                        "type": "replace_exact",
                    },
                    {
                        "target_path": ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md",
                        "type": "create_exact",
                    },
                    {
                        "target_path": ".evozeus-wrapper/wrapper.json",
                        "type": "manifest_patch",
                    },
                    {
                        "target_path": "SKILL.md",
                        "type": "supervised_transform",
                    },
                ],
            }
        ],
    }
    assert _sha256((ROOT / verifier.PROTOCOL_REL).read_bytes()) == PROTOCOL_SHA256

    profile_path, _ = _profile()
    profile = verifier.load_profile(
        _base_store(),
        profile_path,
        verifier.load_protocol(_base_store()),
    )
    assert profile["migration_records"] == [
        ".evozeus-wrapper/docs/migrations/"
        "harness-skill-v1.0.0-to-v1.1.0.md"
    ]
    assert profile["current_migration_record"] == profile["migration_records"][0]


@pytest.mark.parametrize(
    "path",
    [
        verifier.LEGACY_ENVELOPE_REL,
        verifier.LEGACY_PREFLIGHT_ARTIFACT_REL,
        verifier.LEGACY_ADAPTER_REL,
        verifier.LEGACY_ENVELOPE_SCHEMA_REL,
        verifier.LEGACY_ADAPTER_SCHEMA_REL,
        verifier.LEGACY_PROFILE_SCHEMA_REL,
        (
            "contracts/v1/migrations/profiles/"
            "legacy-v0.14-three-section-to-canonical-v1.1-v1.json"
        ),
        verifier.LEGACY_ADAPTER_IMPLEMENTATION_REL,
        verifier.COMMONMARK_LOCK_REL,
    ],
)
def test_supervised_legacy_trust_assets_are_digest_closed(path: str) -> None:
    store = verifier.CandidateStore(
        _base_store(),
        {path: _candidate_blob(path, b"tampered legacy trust asset\n")},
    )

    with pytest.raises(verifier.VerificationError, match="digest mismatch"):
        verifier.verify_catalog(store)


@pytest.mark.parametrize(
    "path",
    [
        verifier.CONTRACT_MANIFEST_REL,
        (
            "contracts/v1/migrations/profiles/"
            "legacy-v0.14-three-section-to-canonical-v1.1-v1.json"
        ),
        verifier.HISTORY_CURRENT_REL,
        "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json",
        verifier.LEGACY_ADAPTER_REL,
        verifier.LEGACY_ENVELOPE_REL,
        verifier.LEGACY_ADAPTER_IMPLEMENTATION_REL,
        (
            "contracts/v1/migrations/adapters/legacy-v0.14-three-section/"
            "status.md.tpl"
        ),
        verifier.COMMONMARK_LOCK_REL,
    ],
)
def test_supervised_trust_surfaces_reject_candidate_mode_only_drift(path: str) -> None:
    data = (ROOT / path).read_bytes()
    store = verifier.CandidateStore(
        _base_store(),
        {path: _candidate_blob(path, data, mode="100755")},
    )

    with pytest.raises(verifier.VerificationError, match="mode"):
        verifier.verify_catalog(store)


@pytest.mark.parametrize(
    "path",
    [
        verifier.CONTRACT_MANIFEST_REL,
        verifier.LEGACY_ADAPTER_REL,
        verifier.LEGACY_ADAPTER_IMPLEMENTATION_REL,
        verifier.COMMONMARK_LOCK_REL,
    ],
)
def test_supervised_trust_surfaces_reject_filesystem_mode_only_drift(
    tmp_path: Path,
    path: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    (root / path).chmod(0o755)

    with pytest.raises(verifier.VerificationError, match="mode"):
        verifier.verify_catalog(verifier.FilesystemStore(root))


@pytest.mark.parametrize(
    ("path", "role"),
    [
        (
            "forged/artifacts/scripts/evozeus_wrapper_preflight.py",
            "immutable-target-closure-artifact",
        ),
        (
            "migrations/history/harness-skill/v1.2.0/artifacts/scripts/"
            "evozeus_wrapper_preflight.py",
            "candidate-defined-role",
        ),
    ],
)
def test_candidate_cannot_forge_an_executable_preflight_by_suffix_or_role(
    path: str,
    role: str,
) -> None:
    manifest = json.loads((ROOT / CONTRACT_MANIFEST_REL).read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": path,
            "sha256": "0" * 64,
            "mode": "100755",
            "role": role,
        }
    )
    store = verifier.CandidateStore(
        _base_store(),
        {
            CONTRACT_MANIFEST_REL: _candidate_blob(
                CONTRACT_MANIFEST_REL,
                _json_bytes(manifest),
            )
        },
    )

    with pytest.raises(verifier.VerificationError, match="file mode is invalid"):
        verifier._contract_manifest_files(  # noqa: SLF001
            store,
            "candidate contract manifest",
            candidate=True,
        )


def test_legacy_preflight_artifact_is_the_declared_git_blob_and_lf_exact() -> None:
    envelope = json.loads(
        (ROOT / verifier.LEGACY_ENVELOPE_REL).read_text(encoding="utf-8")
    )
    artifact = envelope["frozen_preflight_artifact"]
    artifact_path = ROOT / verifier.LEGACY_PREFLIGHT_ARTIFACT_REL
    data = artifact_path.read_bytes()

    assert artifact == {
        "target_path": ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py",
        "artifact_path": verifier.LEGACY_PREFLIGHT_ARTIFACT_REL,
        "sha256": LEGACY_PREFLIGHT_SHA256,
        "mode": "100755",
        "encoding": "strict-utf-8",
        "newline_style": "lf",
        "normalization": "forbidden_byte_exact",
        "source": {
            "repository": "MetaInFLow/EvoZeus-CoEvolve",
            "commit": LEGACY_PREFLIGHT_SOURCE_COMMIT,
            "tree": LEGACY_PREFLIGHT_SOURCE_TREE,
            "path": "scripts/evozeus_wrapper_preflight.py",
            "blob": LEGACY_PREFLIGHT_SOURCE_BLOB,
            "mode": "100755",
        },
    }
    assert len(data) == 54_422
    assert _sha256(data) == LEGACY_PREFLIGHT_SHA256
    assert artifact_path.stat().st_mode & 0o777 == 0o755
    assert b"\r" not in data
    assert data.endswith(b"\n")
    data.decode("utf-8", errors="strict")
    assert subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "cat-file",
            "blob",
            (
                LEGACY_PREFLIGHT_SOURCE_COMMIT
                + ":scripts/evozeus_wrapper_preflight.py"
            ),
        ]
    ) == data
    assert subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "show",
            "-s",
            "--format=%T",
            LEGACY_PREFLIGHT_SOURCE_COMMIT,
        ],
        text=True,
    ).strip() == LEGACY_PREFLIGHT_SOURCE_TREE
    assert subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-tree",
            LEGACY_PREFLIGHT_SOURCE_COMMIT,
            "scripts/evozeus_wrapper_preflight.py",
        ],
        text=True,
    ).strip() == (
        "100755 blob "
        + LEGACY_PREFLIGHT_SOURCE_BLOB
        + "\tscripts/evozeus_wrapper_preflight.py"
    )


@pytest.mark.parametrize("field", ["commit", "tree", "blob"])
def test_legacy_preflight_artifact_rejects_wrong_git_source_identity(
    field: str,
) -> None:
    store = _base_store()
    envelope = json.loads(
        (ROOT / verifier.LEGACY_ENVELOPE_REL).read_text(encoding="utf-8")
    )
    envelope["frozen_preflight_artifact"]["source"][field] = "0" * 40

    with pytest.raises(
        verifier.VerificationError,
        match="preflight artifact evidence is invalid",
    ):
        verifier._verify_legacy_preflight_artifact(  # noqa: SLF001
            store,
            verifier._contract_manifest_files(store, "contract manifest"),  # noqa: SLF001
            envelope,
            verifier._legacy_envelope_entries(envelope),  # noqa: SLF001
        )


@pytest.mark.parametrize(
    ("mutate", "mode", "message"),
    [
        (lambda data: data + b"# drift\n", "100755", "digest mismatch"),
        (lambda data: data.replace(b"\n", b"\r\n"), "100755", "digest mismatch"),
        (lambda data: data, "100644", "mode mismatch"),
    ],
    ids=["replacement", "crlf-normalization", "mode-drift"],
)
def test_legacy_preflight_artifact_rejects_any_byte_or_mode_normalization(
    mutate,
    mode: str,
    message: str,
) -> None:
    original = (ROOT / verifier.LEGACY_PREFLIGHT_ARTIFACT_REL).read_bytes()
    store = verifier.CandidateStore(
        _base_store(),
        {
            verifier.LEGACY_PREFLIGHT_ARTIFACT_REL: _candidate_blob(
                verifier.LEGACY_PREFLIGHT_ARTIFACT_REL,
                mutate(original),
                mode=mode,
            )
        },
    )

    with pytest.raises(verifier.VerificationError, match=message):
        verifier.verify_catalog(store)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "migrations/history/legacy-wrapper/v0.14.0/artifacts/wrong.py"),
        ("sha256", "0" * 64),
        ("mode", "100644"),
        ("normalization", "allow_crlf"),
    ],
)
def test_supervised_profile_cannot_rebind_the_frozen_preflight_artifact(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    profile_path = (
        root
        / "contracts/v1/migrations/profiles/"
        "legacy-v0.14-three-section-to-canonical-v1.1-v1.json"
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    operation = next(
        item
        for item in profile["operations"]
        if item["target_path"]
        == ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py"
    )
    operation["preimage"]["artifact"][field] = value
    _write_json(profile_path, profile)
    _rebind_filesystem_manifest(root)

    with pytest.raises(
        verifier.VerificationError,
        match="replace_exact preimage disagrees with closure",
    ):
        verifier.verify_catalog(verifier.FilesystemStore(root))


@pytest.mark.parametrize(
    "path",
    [
        verifier.LEGACY_ENVELOPE_REL,
        verifier.LEGACY_ADAPTER_REL,
        verifier.LEGACY_ENVELOPE_SCHEMA_REL,
        verifier.LEGACY_ADAPTER_SCHEMA_REL,
        verifier.LEGACY_PROFILE_SCHEMA_REL,
        verifier.LEGACY_ADAPTER_IMPLEMENTATION_REL,
        verifier.COMMONMARK_LOCK_REL,
    ],
)
def test_legacy_trust_rotation_requires_a_protected_source_pr(path: str) -> None:
    changes = {path: _candidate_blob(path, b"candidate rotation\n")}
    protocol = verifier.load_protocol(_base_store())

    assert verifier.classify_candidate_changes(protocol, changes) == "rotation_required"
    with pytest.raises(
        verifier.VerificationError,
        match="trusted base authority|migration consumer",
    ):
        verifier.verify_candidate(_base_store(), changes, head_sha="6" * 40)


def test_candidate_protocol_cannot_reclassify_a_legacy_trust_rotation_as_data() -> None:
    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("rotation classification must not resolve candidate history")

    candidate_protocol = json.loads(
        (ROOT / verifier.PROTOCOL_REL).read_text(encoding="utf-8")
    )
    candidate_protocol["candidate_policy"]["protected_legacy_data_prefixes"] = []
    changes = {
        verifier.PROTOCOL_REL: _candidate_blob(
            verifier.PROTOCOL_REL,
            _json_bytes(candidate_protocol),
        ),
        verifier.LEGACY_ENVELOPE_REL: _candidate_blob(
            verifier.LEGACY_ENVELOPE_REL,
            b"candidate trust-anchor rotation\n",
        ),
    }

    report = verifier.verify_classified_pull_request(
        _base_store(),
        changes,
        head_sha="8" * 40,
        repository="MetaInFLow/EvoZeus-CoEvolve",
        construction_resolver=must_not_resolve,
    )

    assert report["status"] == "rotation_required"
    assert report["candidate_files_executed"] is False


def test_candidate_data_cannot_rotate_the_bound_legacy_trust_anchor() -> None:
    contract = json.loads(
        (ROOT / verifier.MIGRATION_CONTRACT_REL).read_text(encoding="utf-8")
    )
    contract["reviewed_legacy_migrations"][0]["adapter"]["sha256"] = "0" * 64
    changes = {
        verifier.MIGRATION_CONTRACT_REL: _candidate_blob(
            verifier.MIGRATION_CONTRACT_REL,
            _json_bytes(contract),
        )
    }

    with pytest.raises(
        verifier.VerificationError,
        match="cannot rotate a trusted legacy envelope or adapter",
    ):
        verifier.verify_candidate(_base_store(), changes, head_sha="7" * 40)


def test_broad_scattered_legacy_discovery_remains_manual_and_zero_authority() -> None:
    contract = json.loads(
        (ROOT / verifier.MIGRATION_CONTRACT_REL).read_text(encoding="utf-8")
    )
    profile = next(
        item
        for item in contract["discovery_profiles"]
        if item["profile_id"] == "legacy-scattered-to-canonical-v1.0"
    )

    assert profile["automatic"] is False
    assert profile["default_decision"] == "manual_migration_required"
    assert profile["adapter_payload"] == {
        "type": "manual-review-gate",
        "discovery_candidates_are_authority": False,
        "destructive_authority": False,
        "trusted_preimages": [],
    }


def test_protocol_declares_the_exact_protected_code_and_cumulative_ledger_policy() -> None:
    protocol = verifier.load_protocol(_base_store())

    assert set(protocol["candidate_policy"]["protected_base_paths"]) == set(
        verifier.PROTECTED_BASE_PATH_DECLARATIONS
    )
    assert (
        protocol["target_policy"]["ledger_history"]
        == "one_current_hop_plus_zero_or_more_prior_records"
    )
    assert protocol["candidate_policy"]["construction_source_allowlist"] == {
        "prefixes": list(verifier.CONSTRUCTION_SOURCE_PREFIXES),
        "paths": list(verifier.CONSTRUCTION_SOURCE_PATHS),
    }
    assert protocol["candidate_policy"]["pull_request_classification"] == {
        "authority_rotation_prefixes": list(verifier.AUTHORITY_ROTATION_PREFIXES),
        "authority_rotation_paths": list(verifier.AUTHORITY_ROTATION_PATHS),
        "data_candidate_prefixes": list(verifier.DATA_CANDIDATE_PREFIXES),
        "data_candidate_paths": list(verifier.DATA_CANDIDATE_PATHS),
    }


def test_migration_ledger_uses_closure_axis_and_sorts_operations_deterministically() -> None:
    prior_record = (
        ".evozeus-wrapper/docs/migrations/"
        "harness-skill-v1.0.0-to-v1.1.0.md"
    )
    current_record = (
        ".evozeus-wrapper/docs/migrations/"
        "harness-skill-v1.1.0-to-v1.2.0.md"
    )
    operations = [
        {"type": "create_exact", "target_path": current_record},
        {"type": "create_exact", "target_path": prior_record},
    ]
    generated_entry = {
        "kind": "exact",
        "materialization": {"generated_release_artifact": True},
        "source_binding": "generated_release_artifact",
    }

    records = verifier._verified_profile_migration_records(
        operations,
        {
            "closure_version": "v1.0.0",
            "state": {"harness_skill_version": "v9.0.0"},
        },
        {
            "closure_version": "v1.2.0",
            "state": {"harness_skill_version": "v9.0.0"},
        },
        {
            prior_record: generated_entry,
            current_record: generated_entry,
        },
    )

    assert records == [prior_record, current_record]


@pytest.mark.parametrize("version", ["v1.0.0", "v1.1.0"])
def test_frozen_closure_artifacts_equal_the_declared_construction_revision(
    version: str,
) -> None:
    closure_path = (
        ROOT
        / "contracts/v1/migrations/history/harness-skill"
        / version
        / "closure.json"
    )
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    revision = closure["source"]["construction_revision"]

    for item in closure["files"]:
        source_path = item.get("source_path")
        artifact_path = item.get("artifact_path")
        if source_path is None or item.get("source_binding") != "construction_revision":
            continue
        historical = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{revision}:{source_path}"],
            capture_output=True,
            check=False,
        )
        assert historical.returncode == 0, (version, source_path, historical.stderr)
        assert historical.stdout == (closure_path.parent / artifact_path).read_bytes()


def test_repository_history_gate_verifies_every_immutable_closure() -> None:
    head_sha = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    report = verifier.verify_repository_history(
        _base_store(),
        head_sha=head_sha,
        construction_resolver=verifier._local_construction_revision_resolver(ROOT),
    )

    assert report["head"] == head_sha
    assert report["immutable_closures"] == 2
    assert report["construction_revisions"] == [
        "44d1fbdefc1e1de47a35c3ca39d2ba083661d569",
        "ee199b5d50bd12b26d8150538a85b1e959cadf0a",
    ]


def test_local_history_resolver_distinguishes_two_phase_merge_squash_and_side_branch(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "history-graph"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "EvoZeus Test")
    git("config", "user.email", "evozeus-test@example.invalid")
    source = repo / "source.txt"
    source.write_text("base\n", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "base")

    source.write_text("two-phase source\n", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "source first")
    two_phase_revision = git("rev-parse", "HEAD")
    (repo / "closure.txt").write_text("data only\n", encoding="utf-8")
    git("add", "closure.txt")
    git("commit", "-m", "data only")
    two_phase_head = git("rev-parse", "HEAD")
    resolver = verifier._local_construction_revision_resolver(repo)
    assert resolver(
        "MetaInFLow/EvoZeus-CoEvolve",
        two_phase_revision,
        two_phase_head,
        frozenset({"source.txt"}),
    ).is_ancestor

    git("switch", "-c", "merge-source")
    source.write_text("merge source\n", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "merge source")
    merge_revision = git("rev-parse", "HEAD")
    git("switch", "main")
    git("merge", "--no-ff", "merge-source", "-m", "merge source history")
    merge_head = git("rev-parse", "HEAD")
    assert resolver(
        "MetaInFLow/EvoZeus-CoEvolve",
        merge_revision,
        merge_head,
        frozenset({"source.txt"}),
    ).is_ancestor

    git("switch", "-c", "squash-source")
    source.write_text("squash source\n", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "squash source")
    squash_revision = git("rev-parse", "HEAD")
    git("switch", "main")
    git("merge", "--squash", "squash-source")
    git("commit", "-m", "squashed source")
    squash_head = git("rev-parse", "HEAD")
    assert not resolver(
        "MetaInFLow/EvoZeus-CoEvolve",
        squash_revision,
        squash_head,
        frozenset({"source.txt"}),
    ).is_ancestor

    git("switch", "-c", "unmerged-source")
    source.write_text("side branch\n", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "unmerged source")
    side_revision = git("rev-parse", "HEAD")
    git("switch", "main")
    side_head = git("rev-parse", "HEAD")
    assert not resolver(
        "MetaInFLow/EvoZeus-CoEvolve",
        side_revision,
        side_head,
        frozenset({"source.txt"}),
    ).is_ancestor


def test_closure_release_status_does_not_claim_checkpoint_is_a_release() -> None:
    v10 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.0.0/closure.json").read_text()
    )
    v11 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.1.0/closure.json").read_text()
    )

    assert v10["source"] == {
        "repository": "MetaInFLow/EvoZeus-CoEvolve",
        "construction_revision": "44d1fbdefc1e1de47a35c3ca39d2ba083661d569",
        "release_status": "unreleased_exact_snapshot",
        "required_release": None,
    }
    assert v11["source"] == {
        "repository": "MetaInFLow/EvoZeus-CoEvolve",
        "construction_revision": "ee199b5d50bd12b26d8150538a85b1e959cadf0a",
        "release_status": "release_required_for_apply",
        "required_release": "v0.15.0",
    }

    _, profile = _profile()
    assert profile["release_axis"] == {
        "target_wrapper_from": "v0.14.0",
        "target_wrapper_to": "v0.15.0",
        "artifact_source_from": {
            "kind": "construction_revision",
            "revision": "44d1fbdefc1e1de47a35c3ca39d2ba083661d569",
            "release": None,
        },
        "artifact_source_to": {
            "kind": "required_release",
            "release": "v0.15.0",
            "binding": "contract_bundle.source_revision",
        },
    }


@pytest.mark.parametrize(
    ("axis", "field", "value", "message"),
    [
        (
            "artifact_source_from",
            "revision",
            "1" * 40,
            "from-artifact provenance",
        ),
        (
            "artifact_source_to",
            "release",
            "v9.9.9",
            "to-artifact provenance",
        ),
    ],
)
def test_profile_artifact_provenance_must_equal_bound_closure_sources(
    axis: str,
    field: str,
    value: str,
    message: str,
) -> None:
    relative, profile = _profile()
    profile["release_axis"][axis][field] = value
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match=message):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


def test_profile_operations_are_a_strict_bijection_with_closure_diff() -> None:
    store = _base_store()
    protocol = verifier.load_protocol(store)
    relative, raw_profile = _profile()
    profile = verifier.load_profile(store, relative, protocol)
    from_path = profile["_verified_from_path"]
    to_path = profile["_verified_to_path"]
    _, before = verifier.load_closure(store, from_path)
    _, after = verifier.load_closure(store, to_path)
    changes = verifier.closure_diff(before, after)

    assert {path: operation for path, (operation, _, _) in changes.items()} == {
        ".evozeus-wrapper/contracts/harness-migration-contract-v1.json": "create_exact",
        (
            ".evozeus-wrapper/docs/migrations/"
            "harness-skill-v1.0.0-to-v1.1.0.md"
        ): "create_exact",
        ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py": "replace_exact",
        ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md": "replace_exact",
        ".evozeus-wrapper/wrapper.json": "manifest_patch",
    }
    assert {item["target_path"] for item in raw_profile["operations"]} == set(changes)


def test_migration_contract_postimage_hash_is_locked_across_profile_and_closure() -> None:
    contract_sha256 = _sha256(
        (BUNDLE / "migrations/harness-migration-contract-v1.json").read_bytes()
    )
    _, profile = _profile()
    closure = json.loads(
        (BUNDLE / profile["to_closure"]["path"]).read_text(encoding="utf-8")
    )
    operation = next(
        item
        for item in profile["operations"]
        if item["target_path"]
        == ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
    )
    manifest_operation = next(
        item
        for item in profile["operations"]
        if item["target_path"] == ".evozeus-wrapper/wrapper.json"
    )
    manifest_contract = next(
        item["value"]
        for item in manifest_operation["patch"]
        if item["field"] == "migration_contract"
    )
    contract_file = next(
        item
        for item in closure["files"]
        if item["target_path"]
        == ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
    )
    wrapper_state = next(
        item["owned_state"]
        for item in closure["files"]
        if item["target_path"] == ".evozeus-wrapper/wrapper.json"
    )

    assert operation["postimage"]["sha256"] == contract_sha256
    assert contract_file["sha256"] == contract_sha256
    assert manifest_contract["sha256"] == f"sha256:{contract_sha256}"
    assert wrapper_state["migration_contract"]["sha256"] == f"sha256:{contract_sha256}"


def test_rendered_surfaces_are_explicitly_excluded_from_automatic_upgrade() -> None:
    _, profile = _profile()
    v10 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.0.0/closure.json").read_text()
    )
    v11 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.1.0/closure.json").read_text()
    )
    rendered = {
        item["target_path"]
        for item in v10["files"]
        if item["kind"] == "rendered_template"
    }

    assert rendered == {
        ".evozeus-wrapper/CHANGELOG.md",
        ".evozeus-wrapper/WRAPPER.md",
        ".evozeus-wrapper/docs/_config.yml",
        ".evozeus-wrapper/docs/index.md",
        ".github/ISSUE_TEMPLATE/config.yml",
    }
    assert {
        item["target_path"]
        for item in profile["deferred_rendered_surfaces"]
    } == rendered
    assert {
        item["target_path"]
        for item in v11["files"]
        if item["kind"] == "rendered_template"
    } == rendered
    for closure in (v10, v11):
        for item in closure["files"]:
            if item["target_path"] in rendered:
                assert item["materialization"] == {
                    "policy": "render_at_fresh_attach",
                    "without_receipt": "preserve_byte_exact",
                    "migration_policy": "preserve_byte_exact_no_auto_upgrade",
                }
    for closure in (v10, v11):
        workflow = next(
            item
            for item in closure["files"]
            if item["target_path"]
            == ".github/workflows/evozeus-wrapper-preflight.yml"
        )
        assert workflow["kind"] == "exact"
        assert workflow["materialization"] == {"policy": "copy_exact"}


@pytest.mark.parametrize("operation_type", ["delete", "rename", "shell", "copy"])
def test_unknown_or_destructive_profile_operation_is_rejected(operation_type: str) -> None:
    relative, profile = _profile()
    profile["operations"][0]["type"] = operation_type
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match="unknown operation"):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


def test_profile_cannot_target_business_instruction_bytes() -> None:
    relative, profile = _profile()
    profile["operations"][0]["target_path"] = "SKILL.md"
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match="no closure diff"):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


def test_missing_operation_breaks_closure_diff_bijection() -> None:
    relative, profile = _profile()
    profile["operations"].pop()
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match="lacks profile operations"):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


@pytest.mark.parametrize(
    "path",
    [
        verifier.VERIFIER_REL,
        verifier.WORKFLOW_REL,
        verifier.PROTOCOL_REL,
        "contracts/v1/migrations/schemas/target-closure-v1.schema.json",
        "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json",
    ],
)
def test_candidate_cannot_modify_trusted_base_authority_or_history(path: str) -> None:
    changes = {path: _candidate_blob(path, b"candidate bytes\n")}

    with pytest.raises(
        verifier.VerificationError,
        match="modifies trusted base authority or history",
    ):
        verifier.verify_candidate(_base_store(), changes, head_sha="1" * 40)


def test_candidate_cannot_add_to_an_existing_immutable_version_directory() -> None:
    path = (
        "contracts/v1/migrations/history/harness-skill/"
        "v1.1.0/artifacts/generated/late-addition.md"
    )

    with pytest.raises(
        verifier.VerificationError,
        match="modifies trusted base authority or history",
    ):
        verifier.verify_candidate(
            _base_store(),
            {path: _candidate_blob(path, b"late mutation\n", status="added")},
            head_sha="2" * 40,
        )


@pytest.mark.parametrize(
    "mode,object_type",
    [
        ("120000", "blob"),
        ("160000", "commit"),
        ("040000", "tree"),
    ],
)
def test_candidate_symlink_submodule_or_tree_is_rejected(
    mode: str,
    object_type: str,
) -> None:
    path = "candidate-object"
    changes = {
        path: _candidate_blob(
            path,
            b"object\n",
            status="added",
            mode=mode,
            object_type=object_type,
        )
    }

    with pytest.raises(verifier.VerificationError, match="symlink, submodule"):
        verifier.verify_candidate(_base_store(), changes, head_sha="3" * 40)


def test_generated_migration_ledger_rejects_dates_and_self_reference() -> None:
    closure_relative = (
        "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
    )
    closure = json.loads((ROOT / closure_relative).read_text(encoding="utf-8"))
    ledger = next(
        item
        for item in closure["files"]
        if item["target_path"].endswith("harness-skill-v1.0.0-to-v1.1.0.md")
    )
    ledger_relative = (
        str(Path(closure_relative).parent / ledger["artifact_path"])
        .replace("\\", "/")
    )
    bad_ledger = b"migration date 2026-08-02 and plan_sha256=self\n"
    ledger["sha256"] = _sha256(bad_ledger)
    store = verifier.CandidateStore(
        _base_store(),
        {
            closure_relative: _candidate_blob(
                closure_relative,
                _json_bytes(closure),
            ),
            ledger_relative: _candidate_blob(ledger_relative, bad_ledger),
        },
    )

    with pytest.raises(verifier.VerificationError, match="contains a date"):
        verifier.load_closure(store, closure_relative)


def test_github_request_is_get_without_candidate_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve"

        def read(self, _limit: int) -> bytes:
            return b"{}"

    def open_request(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 30
        captured.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    assert verifier._github_json(
        "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve",
        "token",
    ) == {}
    assert captured[0].data is None
    assert captured[0].get_method() == "GET"


def test_github_construction_resolver_binds_compare_tree_and_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40
    head_sha = "b" * 40
    blob_oid = "c" * 40
    calls: list[str] = []

    def github_json(url: str, _token: str) -> dict[str, object]:
        calls.append(url)
        if "/compare/" in url:
            return {
                "status": "ahead",
                "merge_base_commit": {"sha": revision},
            }
        return {
            "truncated": False,
            "tree": [
                {
                    "path": "templates/target/example.txt",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_oid,
                }
            ],
        }

    monkeypatch.setattr(verifier, "_github_json", github_json)
    monkeypatch.setattr(verifier, "_github_blob", lambda *_args: b"trusted\n")
    resolver = verifier._github_construction_revision_resolver("token")

    evidence = resolver(
        "MetaInFLow/EvoZeus-CoEvolve",
        revision,
        head_sha,
        frozenset({"templates/target/example.txt"}),
    )

    assert evidence.is_ancestor is True
    assert evidence.files["templates/target/example.txt"].mode == "100644"
    assert evidence.files["templates/target/example.txt"].data == b"trusted\n"
    assert calls[0].endswith(f"/compare/{revision}...{head_sha}")
    assert calls[1].endswith(f"/git/trees/{revision}?recursive=1")


def test_pull_request_target_workflow_executes_only_trusted_base_code() -> None:
    workflow = (ROOT / verifier.WORKFLOW_REL).read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "    paths:" not in workflow
    assert "classify-and-verify:" in workflow
    assert "Classify full diff and verify data-only candidates" in workflow
    assert "permissions:\n  contents: read\n" in workflow
    assert "pull-requests:" not in workflow
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in workflow
    )
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.pull_request.head" not in workflow
    assert "verify-pull-request" in workflow
    assert "pip install" not in workflow


def test_ordinary_pull_request_is_a_successful_official_upgrade_noop() -> None:
    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("ordinary PR classification must not resolve candidate history")

    report = verifier.verify_classified_pull_request(
        _base_store(),
        {"README.md": _candidate_blob("README.md", b"ordinary docs change\n")},
        head_sha="1" * 40,
        repository="someone/fork",
        construction_resolver=must_not_resolve,
    )

    assert report == {
        "status": "not_applicable",
        "classification": "not_applicable",
        "candidate_head": "1" * 40,
        "candidate_files_executed": False,
    }


@pytest.mark.parametrize(
    "path",
    [
        verifier.VERIFIER_REL,
        "scripts/evozeus_wrapper_lifecycle.py",
        ".github/workflows/ci.yml",
        verifier.PROTOCOL_REL,
    ],
)
def test_authority_or_consumer_pull_request_requires_source_rotation(
    path: str,
) -> None:
    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("authority rotation must not execute candidate history")

    report = verifier.verify_classified_pull_request(
        _base_store(),
        {path: _candidate_blob(path, b"trusted source rotation\n")},
        head_sha="2" * 40,
        repository="MetaInFLow/EvoZeus-CoEvolve",
        construction_resolver=must_not_resolve,
    )

    assert report["status"] == "rotation_required"
    assert report["classification"] == "rotation_required"
    assert report["candidate_files_executed"] is False
    assert "data-only migration PR" in report["next_step"]


@pytest.mark.parametrize(
    "data_path",
    [
        verifier.HISTORY_CURRENT_REL,
        "contracts/v1/migrations/profiles/future-v1.json",
    ],
)
def test_authority_and_migration_data_must_be_split_into_two_pull_requests(
    data_path: str,
) -> None:
    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("mixed authority/data PR must stop before candidate history")

    changes = {
        verifier.VERIFIER_REL: _candidate_blob(
            verifier.VERIFIER_REL,
            b"trusted source rotation\n",
        ),
        data_path: _candidate_blob(data_path, b"{}\n"),
    }

    with pytest.raises(
        verifier.VerificationError,
        match="rotation_required.*source-first and data-only",
    ):
        verifier.verify_classified_pull_request(
            _base_store(),
            changes,
            head_sha="3" * 40,
            repository="MetaInFLow/EvoZeus-CoEvolve",
            construction_resolver=must_not_resolve,
        )


def test_mixed_authority_and_data_cli_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changes = {
        "scripts/evozeus_wrapper_lifecycle.py": _candidate_blob(
            "scripts/evozeus_wrapper_lifecycle.py",
            b"trusted consumer rotation\n",
        ),
        verifier.HISTORY_CURRENT_REL: _candidate_blob(
            verifier.HISTORY_CURRENT_REL,
            b"{}\n",
        ),
    }
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(
        verifier,
        "candidate_from_pull_request",
        lambda *_args: (
            changes,
            "4" * 40,
            "MetaInFLow/EvoZeus-CoEvolve",
        ),
    )

    exit_code = verifier.main(
        [
            "verify-pull-request",
            "--repo-root",
            str(ROOT),
            "--event",
            str(tmp_path / "unused-event.json"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["status"] == "rejected"
    assert "rotation_required" in output["error"]


def test_unknown_migration_data_cannot_fall_through_as_an_ordinary_pr() -> None:
    path = "contracts/v1/migrations/unknown-candidate-data.json"
    changes = {path: _candidate_blob(path, b"{}\n", status="added")}
    protocol = verifier.load_protocol(_base_store())

    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("unknown data must fail before history resolution")

    assert verifier.classify_candidate_changes(protocol, changes) == "data_candidate"
    with pytest.raises(
        verifier.VerificationError,
        match="manifest does not exactly enumerate",
    ):
        verifier.verify_classified_pull_request(
            _base_store(),
            changes,
            head_sha="5" * 40,
            repository="MetaInFLow/EvoZeus-CoEvolve",
            construction_resolver=must_not_resolve,
        )


def test_main_uat_and_release_workflows_explicitly_run_history_gate() -> None:
    ci_command = (
        "python scripts/evozeus_official_upgrade_verify.py "
        "verify-base --repo-root ."
    )
    release_command = (
        "python trusted/scripts/evozeus_official_upgrade_verify.py "
        "verify-release"
    )
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'branches: [main, "uat/current"]' in ci
    assert ci_command in ci
    assert release_command in release
    assert ci_command not in release


def test_catalog_requires_a_unique_direct_profile_from_each_historical_closure(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    profile_relative = "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
    duplicate_relative = "contracts/v1/migrations/profiles/duplicate-v1.0-to-v1.1-v1.json"
    duplicate = json.loads((root / profile_relative).read_text(encoding="utf-8"))
    duplicate["profile_id"] = "duplicate-v1.0-to-v1.1"
    _write_json(root / duplicate_relative, duplicate)
    pointer_path = root / verifier.PROFILES_CURRENT_REL
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["entries"].append(
        {
            "id": duplicate["profile_id"],
            "version": duplicate["profile_version"],
            "path": "migrations/profiles/" + Path(duplicate_relative).name,
            "sha256": _sha256((root / duplicate_relative).read_bytes()),
        }
    )
    _write_json(pointer_path, pointer)
    _rebind_filesystem_manifest(
        root,
        new_roles={
            duplicate_relative.removeprefix("contracts/v1/"): (
                "official-upgrade-profile"
            )
        },
    )

    with pytest.raises(verifier.VerificationError, match="duplicate from closure"):
        verifier.verify_catalog(verifier.FilesystemStore(root))


def test_catalog_rejects_an_active_profile_that_does_not_end_at_current(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    current_path = root / verifier.HISTORY_CURRENT_REL
    current = json.loads(current_path.read_text(encoding="utf-8"))
    v10_relative = "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json"
    current["entries"][0] = {
        "id": "using-evozeus-harness",
        "version": "v1.0.0",
        "path": "migrations/history/harness-skill/v1.0.0/closure.json",
        "sha256": _sha256((root / v10_relative).read_bytes()),
    }
    _write_json(current_path, current)
    _rebind_filesystem_manifest(root)

    with pytest.raises(
        verifier.VerificationError,
        match="contract_bundle_version|point directly to the current",
    ):
        verifier.verify_catalog(verifier.FilesystemStore(root))


def test_candidate_rotates_to_a_direct_to_current_profile_star(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)

    report = verifier.verify_classified_pull_request(
        verifier.FilesystemStore(root),
        changes,
        head_sha=head_sha,
        repository="MetaInFLow/EvoZeus-CoEvolve",
        construction_resolver=resolver,
    )

    assert report["status"] == "verified_candidate"
    assert report["classification"] == "data_candidate"
    assert report["base_closure_version"] == "v1.1.0"
    assert report["candidate_closure_version"] == "v1.2.0"

    candidate = verifier.CandidateStore(verifier.FilesystemStore(root), changes)
    profiles = {}
    for entry in verifier.load_pointer(
        candidate,
        verifier.PROFILES_CURRENT_REL,
        "official-upgrade-current-profiles",
    ):
        path = "contracts/v1/" + entry["path"]
        profile = verifier.load_profile(
            candidate,
            path,
            verifier.load_protocol(candidate),
            expected_sha256=entry["sha256"],
        )
        profiles[profile["profile_id"]] = profile
    old_record = (
        ".evozeus-wrapper/docs/migrations/"
        "harness-skill-v1.0.0-to-v1.1.0.md"
    )
    current_record = (
        ".evozeus-wrapper/docs/migrations/"
        "harness-skill-v1.1.0-to-v1.2.0.md"
    )
    assert profiles["canonical-v1.0-to-v1.2"]["migration_records"] == [
        old_record,
        current_record,
    ]
    assert profiles["canonical-v1.1-to-v1.2"]["migration_records"] == [
        current_record
    ]
    assert {
        profile["current_migration_record"] for profile in profiles.values()
    } == {current_record}


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("contract_bundle_version", "contract_bundle_version"),
        ("required_release", "required_release"),
        ("target_wrapper_version", "target_wrapper_version"),
        ("current_harness_skill_version", "current_harness_skill_version"),
    ],
)
def test_candidate_current_closure_is_cross_bound_to_release_contract_axes(
    tmp_path: Path,
    fault: str,
    message: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    _fault_candidate_release_binding(root, changes, fault)

    with pytest.raises(verifier.VerificationError, match=message):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_rejects_an_unbound_non_migration_change(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    changes["docs/unbound-runtime-note.md"] = _candidate_blob(
        "docs/unbound-runtime-note.md",
        b"unbound\n",
        status="added",
    )

    with pytest.raises(verifier.VerificationError, match="derived official-upgrade closure"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize("bound", [False, True])
def test_candidate_cannot_change_the_migration_consumer_even_when_closure_bound(
    tmp_path: Path,
    bound: bool,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    path = "scripts/evozeus_wrapper_lifecycle.py"
    if bound:
        _attempt_to_bind_protected_consumer_as_skill_source(root, changes, path)
    else:
        changes[path] = _candidate_blob(path, b"malicious consumer change\n")

    with pytest.raises(
        verifier.VerificationError,
        match="trusted base authority or migration consumer",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize(
    "path",
    [
        "scripts/unreviewed_upgrade_source.py",
        "docs/unreviewed-governance.md",
        ".github/workflows/unreviewed-governance.yml",
    ],
)
def test_candidate_construction_sources_are_protocol_allowlisted(
    tmp_path: Path,
    path: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    _attempt_to_bind_protected_consumer_as_skill_source(root, changes, path)

    with pytest.raises(
        verifier.VerificationError,
        match="outside the trusted protocol allowlist",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_construction_source_mode_must_equal_closure_artifact_mode(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, valid_resolver, head_sha = _candidate_star(root)
    source_path = (
        "templates/target/.evozeus_evoinfra/skills/"
        "using-evozeus-harness/SKILL.md"
    )
    source = changes[source_path]
    assert source.loader is not None
    changes[source_path] = _candidate_blob(
        source_path,
        source.loader(),
        mode="100755",
    )

    def mismatched_mode_resolver(
        repository: str,
        revision: str,
        resolved_head: str,
        source_paths: frozenset[str],
    ) -> verifier.ConstructionRevisionEvidence:
        evidence = valid_resolver(repository, revision, resolved_head, source_paths)
        files = dict(evidence.files)
        historical = files[source_path]
        files[source_path] = verifier.ConstructionBlob(
            path=source_path,
            mode="100755",
            data=historical.data,
        )
        return verifier.ConstructionRevisionEvidence(
            repository=evidence.repository,
            revision=evidence.revision,
            head_sha=evidence.head_sha,
            is_ancestor=evidence.is_ancestor,
            files=files,
        )

    with pytest.raises(
        verifier.VerificationError,
        match="construction source mode differs from target closure",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=mismatched_mode_resolver,
        )


@pytest.mark.parametrize(
    "path",
    [
        "contracts/v1/migrations/history/harness-skill/v1.2.0/artifacts/unbound.json",
        "contracts/v1/migrations/profiles/unbound-v1.json",
    ],
)
def test_candidate_rejects_manifest_bound_but_inactive_migration_files(
    tmp_path: Path,
    path: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    changes[path] = _candidate_blob(path, b"{}\n", status="added")
    _bind_candidate_manifest(root, changes)

    with pytest.raises(verifier.VerificationError, match="migration file is not bound"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize("fault", ["incomplete", "wrong_hash"])
def test_candidate_contract_manifest_is_complete_and_hash_bound(
    tmp_path: Path,
    fault: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    if fault == "incomplete":
        changes[CONTRACT_MANIFEST_REL] = _candidate_blob(
            CONTRACT_MANIFEST_REL,
            (root / CONTRACT_MANIFEST_REL).read_bytes(),
        )
    else:
        manifest_blob = changes[CONTRACT_MANIFEST_REL]
        assert manifest_blob.loader is not None
        manifest = json.loads(manifest_blob.loader().decode("utf-8"))
        entry = next(
            item
            for item in manifest["files"]
            if item["path"]
            == "migrations/history/harness-skill/v1.2.0/closure.json"
        )
        entry["sha256"] = "0" * 64
        changes[CONTRACT_MANIFEST_REL] = _candidate_blob(
            CONTRACT_MANIFEST_REL,
            _json_bytes(manifest),
        )

    with pytest.raises(
        verifier.VerificationError,
        match="manifest (does not exactly enumerate|digest mismatch)",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_candidate_contract_json_rejects_non_finite_constants(
    tmp_path: Path,
    constant: bytes,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    manifest_blob = changes[CONTRACT_MANIFEST_REL]
    assert manifest_blob.loader is not None
    manifest_data = manifest_blob.loader()
    identity = b'"bundle_id": "evozeus-coevolve"'
    assert identity in manifest_data
    changes[CONTRACT_MANIFEST_REL] = _candidate_blob(
        CONTRACT_MANIFEST_REL,
        manifest_data.replace(identity, b'"bundle_id": ' + constant, 1),
    )

    with pytest.raises(verifier.VerificationError, match="non-finite JSON constant"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_contract_json_rejects_exponent_overflow(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    manifest_blob = changes[CONTRACT_MANIFEST_REL]
    assert manifest_blob.loader is not None
    manifest_data = manifest_blob.loader()
    identity = b'"bundle_id": "evozeus-coevolve"'
    assert identity in manifest_data
    changes[CONTRACT_MANIFEST_REL] = _candidate_blob(
        CONTRACT_MANIFEST_REL,
        manifest_data.replace(identity, b'"bundle_id": 1e400', 1),
    )

    with pytest.raises(verifier.VerificationError, match="non-finite JSON number"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_contract_manifest_cannot_delete_a_trusted_base_path(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    deleted_path = "contracts/v1/user-prompt-lesson-runtime-lifecycle.json"
    changes[deleted_path] = _candidate_blob(
        deleted_path,
        None,
        status="deleted",
        mode=None,
        object_type=None,
    )
    _bind_candidate_manifest(root, changes)

    with pytest.raises(
        verifier.VerificationError,
        match="cannot delete trusted base contract manifest path",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_rejects_an_unbound_new_contract_bundle_path(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    path = "contracts/v1/misc/unbound.bin"
    changes[path] = _candidate_blob(path, b"unbound\n", status="added")
    _bind_candidate_manifest(
        root,
        changes,
        new_roles={"misc/unbound.bin": "target-closure-source"},
    )

    with pytest.raises(
        verifier.VerificationError,
        match="new bundle path is not authorized by the candidate current closure",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_bundle_local_source_role_is_fixed(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    _bind_candidate_bundle_source(root, changes, role="candidate-defined-role")

    with pytest.raises(
        verifier.VerificationError,
        match="manifest role mismatch|file mode is invalid",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_accepts_a_manifest_bound_bundle_local_source(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    source_path = _bind_candidate_bundle_source(root, changes)

    report = verifier.verify_candidate(
        verifier.FilesystemStore(root),
        changes,
        head_sha=head_sha,
        construction_resolver=resolver,
    )

    manifest_blob = changes[CONTRACT_MANIFEST_REL]
    assert manifest_blob.loader is not None
    manifest = json.loads(manifest_blob.loader().decode("utf-8"))
    source_entry = next(
        entry
        for entry in manifest["files"]
        if entry["path"] == source_path.removeprefix("contracts/v1/")
    )
    assert report["status"] == "verified_candidate"
    assert source_entry["role"] == "target-closure-source"


@pytest.mark.parametrize(
    "relative",
    [
        "migrations/history/harness-skill/current.json",
        "migrations/history/harness-skill/v1.2.0/closure.json",
        (
            "migrations/history/harness-skill/v1.2.0/"
            "artifacts/scripts/evozeus_notice.py"
        ),
        "migrations/profiles/canonical-v1.1-to-v1.2-v1.json",
    ],
)
def test_candidate_contract_manifest_roles_are_not_candidate_defined(
    tmp_path: Path,
    relative: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    manifest_blob = changes[CONTRACT_MANIFEST_REL]
    assert manifest_blob.loader is not None
    manifest = json.loads(manifest_blob.loader().decode("utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == relative)
    entry["role"] = "candidate-defined-role"
    changes[CONTRACT_MANIFEST_REL] = _candidate_blob(
        CONTRACT_MANIFEST_REL,
        _json_bytes(manifest),
    )

    with pytest.raises(verifier.VerificationError, match="manifest role mismatch"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize(
    "destination",
    [
        "contracts/v1/relocated/closure.json",
        "contracts/v1/migrations/history/harness-skill/v9.9.9/closure.json",
    ],
)
def test_candidate_current_closure_path_is_version_canonical(
    tmp_path: Path,
    destination: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    _relocate_candidate_closure(root, changes, destination)

    with pytest.raises(
        verifier.VerificationError,
        match="closure path is not canonical|file mode is invalid",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_active_profile_must_remain_in_profiles_directory(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    pointer = json.loads(changes[verifier.PROFILES_CURRENT_REL].loader().decode("utf-8"))
    entry = pointer["entries"][0]
    source = "contracts/v1/" + entry["path"]
    destination = "contracts/v1/relocated/active-profile.json"
    profile_blob = changes.pop(source)
    assert profile_blob.loader is not None
    changes[destination] = _candidate_blob(
        destination,
        profile_blob.loader(),
        status=profile_blob.status,
        mode=profile_blob.mode,
    )
    entry["path"] = destination.removeprefix("contracts/v1/")
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(pointer),
    )
    _bind_candidate_manifest(
        root,
        changes,
        new_roles={
            destination.removeprefix("contracts/v1/"): "official-upgrade-profile"
        },
    )

    with pytest.raises(verifier.VerificationError, match="profile path is not canonical"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_active_profile_rejects_a_noncanonical_suffix(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    pointer_blob = changes[verifier.PROFILES_CURRENT_REL]
    assert pointer_blob.loader is not None
    pointer = json.loads(pointer_blob.loader().decode("utf-8"))
    entry = pointer["entries"][0]
    source = "contracts/v1/" + entry["path"]
    destination = source.removesuffix(".json") + ".data"
    profile_blob = changes.pop(source)
    assert profile_blob.loader is not None
    changes[destination] = _candidate_blob(
        destination,
        profile_blob.loader(),
        status=profile_blob.status,
        mode=profile_blob.mode,
    )
    entry["path"] = destination.removeprefix("contracts/v1/")
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(pointer),
    )
    _bind_candidate_manifest(
        root,
        changes,
        new_roles={
            destination.removeprefix("contracts/v1/"): "official-upgrade-profile"
        },
    )

    with pytest.raises(verifier.VerificationError, match="profile path is not canonical"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_active_profile_filename_matches_its_identity(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    pointer_blob = changes[verifier.PROFILES_CURRENT_REL]
    assert pointer_blob.loader is not None
    pointer = json.loads(pointer_blob.loader().decode("utf-8"))
    entry = pointer["entries"][0]
    profile_path = "contracts/v1/" + entry["path"]
    profile_blob = changes[profile_path]
    assert profile_blob.loader is not None
    profile = json.loads(profile_blob.loader().decode("utf-8"))
    profile["profile_id"] += "-renamed"
    profile_data = _json_bytes(profile)
    changes[profile_path] = _candidate_blob(
        profile_path,
        profile_data,
        status=profile_blob.status,
        mode=profile_blob.mode,
    )
    entry["id"] = profile["profile_id"]
    entry["sha256"] = _sha256(profile_data)
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(pointer),
    )
    _bind_candidate_manifest(root, changes)

    with pytest.raises(verifier.VerificationError, match="profile path is not canonical"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


def test_candidate_star_must_cover_base_current_and_prior_active_from_closures(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    pointer = json.loads(changes[verifier.PROFILES_CURRENT_REL].loader().decode("utf-8"))
    removed_profile = pointer["entries"][0]
    pointer["entries"] = pointer["entries"][1:]
    changes.pop("contracts/v1/" + removed_profile["path"])
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(pointer),
    )
    _bind_candidate_manifest(root, changes)

    with pytest.raises(verifier.VerificationError, match="historical coverage"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize("fault", ["not_ancestor", "wrong_bytes", "wrong_mode"])
def test_candidate_construction_revision_requires_ancestor_tree_bytes_and_mode(
    tmp_path: Path,
    fault: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, valid_resolver, head_sha = _candidate_star(root)

    def faulty_resolver(
        repository: str,
        revision: str,
        resolved_head: str,
        source_paths: frozenset[str],
    ) -> verifier.ConstructionRevisionEvidence:
        evidence = valid_resolver(repository, revision, resolved_head, source_paths)
        files = dict(evidence.files)
        if fault in {"wrong_bytes", "wrong_mode"}:
            path = sorted(files)[0]
            original = files[path]
            files[path] = verifier.ConstructionBlob(
                path=path,
                mode="100755" if fault == "wrong_mode" and original.mode == "100644" else (
                    "100644" if fault == "wrong_mode" else original.mode
                ),
                data=(original.data + b"drift") if fault == "wrong_bytes" else original.data,
            )
        return verifier.ConstructionRevisionEvidence(
            repository=evidence.repository,
            revision=evidence.revision,
            head_sha=evidence.head_sha,
            is_ancestor=fault != "not_ancestor",
            files=files,
        )

    with pytest.raises(
        verifier.VerificationError,
        match="ancestor|differs from (construction revision|target closure)",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=faulty_resolver,
        )


def test_inactive_existing_history_and_profiles_remain_immutable(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    base = verifier.FilesystemStore(root)
    inactive_closure = (
        root
        / "contracts/v1/migrations/history/harness-skill/v0.9.0/closure.json"
    )
    inactive_closure.parent.mkdir(parents=True)
    inactive_closure.write_text("{}\n", encoding="utf-8")
    prefixes = verifier._immutable_history_prefixes(base)

    assert verifier._protected_candidate_change(
        base,
        "contracts/v1/migrations/history/harness-skill/v0.9.0/artifacts/late.md",
        prefixes,
    )
    assert verifier._protected_candidate_change(
        base,
        "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json",
        prefixes,
    )


def test_protocol_v1_rejects_rendered_surface_changes() -> None:
    before = {
        "kind": "rendered_template",
        "mode": "100644",
        "ownership": "wrapper_managed",
        "sha256": "1" * 64,
        "materialization": {"policy": "preserve_byte_exact_no_auto_upgrade"},
    }
    after = {**before, "sha256": "2" * 64}

    with pytest.raises(verifier.VerificationError, match="cannot change a rendered surface"):
        verifier.closure_diff({"rendered.md": before}, {"rendered.md": after})


def _manifest_without_v100_closure(root: Path) -> bytes:
    manifest = json.loads((root / CONTRACT_MANIFEST_REL).read_text(encoding="utf-8"))
    manifest["files"] = [
        item
        for item in manifest["files"]
        if item["path"]
        != "migrations/history/harness-skill/v1.0.0/closure.json"
    ]
    return _json_bytes(manifest)


def test_history_gate_enumerates_closures_from_disk_not_only_manifest() -> None:
    changes = {
        CONTRACT_MANIFEST_REL: _candidate_blob(
            CONTRACT_MANIFEST_REL,
            _manifest_without_v100_closure(ROOT),
        )
    }
    candidate = verifier.CandidateStore(_base_store(), changes)

    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("closure set mismatch must fail before history resolution")

    with pytest.raises(
        verifier.VerificationError,
        match="immutable closure set disagrees with canonical history files",
    ):
        verifier.verify_repository_history(
            candidate,
            head_sha="6" * 40,
            construction_resolver=must_not_resolve,
        )


def test_manifest_only_history_removal_is_strict_candidate_data() -> None:
    changes = {
        CONTRACT_MANIFEST_REL: _candidate_blob(
            CONTRACT_MANIFEST_REL,
            _manifest_without_v100_closure(ROOT),
        )
    }
    protocol = verifier.load_protocol(_base_store())

    def must_not_resolve(*_args: object) -> verifier.ConstructionRevisionEvidence:
        raise AssertionError("invalid manifest must fail before history resolution")

    assert verifier.classify_candidate_changes(protocol, changes) == "data_candidate"
    with pytest.raises(
        verifier.VerificationError,
        match="manifest does not exactly enumerate|immutable closure set disagrees",
    ):
        verifier.verify_classified_pull_request(
            _base_store(),
            changes,
            head_sha="7" * 40,
            repository="MetaInFLow/EvoZeus-CoEvolve",
            construction_resolver=must_not_resolve,
        )


def test_verify_base_rejects_manifest_hidden_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _protocol_v1_base(tmp_path)
    (root / CONTRACT_MANIFEST_REL).write_bytes(_manifest_without_v100_closure(root))
    subprocess.run(["git", "-C", str(root), "init", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "EvoZeus Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "config",
            "user.email",
            "evozeus-test@example.invalid",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "test fixture"],
        check=True,
        capture_output=True,
    )

    assert verifier.main(["verify-base", "--repo-root", str(root)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert "immutable closure set disagrees with canonical history files" in output["error"]


@pytest.mark.parametrize(
    "mode_policy,error",
    [
        (None, "mode differs from artifact mode"),
        ("set_declared_executable", "mode transformation is unauthorized"),
    ],
)
def test_skill_cannot_reuse_notice_executable_mode_exception(
    mode_policy: str | None,
    error: str,
) -> None:
    closure_path = (
        "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json"
    )
    closure = json.loads((ROOT / closure_path).read_text(encoding="utf-8"))
    skill = next(
        item
        for item in closure["files"]
        if item["target_path"]
        == ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md"
    )
    skill["mode"] = "100755"
    if mode_policy is not None:
        skill["materialization"]["mode_policy"] = mode_policy
    store = verifier.CandidateStore(
        _base_store(),
        {closure_path: _candidate_blob(closure_path, _json_bytes(closure))},
    )

    with pytest.raises(verifier.VerificationError, match=error):
        verifier.load_closure(store, closure_path)


def _release_test_repo(tmp_path: Path) -> tuple[Path, Callable[..., str]]:
    repo = tmp_path / "release-history"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "EvoZeus Test")
    git("config", "user.email", "evozeus-test@example.invalid")
    (repo / "state.txt").write_text("base\n", encoding="utf-8")
    git("add", "state.txt")
    git("commit", "-m", "base")
    return repo, git


def test_release_git_gate_binds_tag_manifest_head_and_trusted_main(
    tmp_path: Path,
) -> None:
    repo, git = _release_test_repo(tmp_path)
    tag_commit = git("rev-parse", "HEAD")
    git("tag", "v0.15.0")
    git("update-ref", "refs/remotes/origin/main", tag_commit)

    report = verifier._verify_release_git_state(
        repo,
        tag="v0.15.0",
        manifest_source_revision="v0.15.0",
        main_ref="refs/remotes/origin/main",
    )

    assert report["tag_commit"] == tag_commit
    assert report["trusted_main_commit"] == tag_commit


def test_release_git_gate_rejects_manifest_tag_mismatch(tmp_path: Path) -> None:
    repo, _git = _release_test_repo(tmp_path)

    with pytest.raises(verifier.VerificationError, match="source_revision"):
        verifier._verify_release_git_state(
            repo,
            tag="v0.15.0",
            manifest_source_revision="v0.16.0",
            main_ref="refs/remotes/origin/main",
        )


def test_release_git_gate_rejects_checkout_after_tag(tmp_path: Path) -> None:
    repo, git = _release_test_repo(tmp_path)
    git("tag", "v0.15.0")
    (repo / "state.txt").write_text("after tag\n", encoding="utf-8")
    git("add", "state.txt")
    git("commit", "-m", "after tag")
    git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))

    with pytest.raises(verifier.VerificationError, match="HEAD does not equal"):
        verifier._verify_release_git_state(
            repo,
            tag="v0.15.0",
            manifest_source_revision="v0.15.0",
            main_ref="refs/remotes/origin/main",
        )


def test_release_git_gate_rejects_tag_outside_trusted_main(tmp_path: Path) -> None:
    repo, git = _release_test_repo(tmp_path)
    git("switch", "-c", "release-side")
    (repo / "state.txt").write_text("side\n", encoding="utf-8")
    git("add", "state.txt")
    git("commit", "-m", "side release")
    git("tag", "v0.15.0")
    git("switch", "main")
    (repo / "state.txt").write_text("main\n", encoding="utf-8")
    git("add", "state.txt")
    git("commit", "-m", "trusted main")
    git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))
    git("switch", "--detach", "v0.15.0")

    with pytest.raises(verifier.VerificationError, match="not an ancestor"):
        verifier._verify_release_git_state(
            repo,
            tag="v0.15.0",
            manifest_source_revision="v0.15.0",
            main_ref="refs/remotes/origin/main",
        )


def _release_workflow() -> tuple[str, dict[str, object]]:
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    document = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return text, document


def _job_commands(job: dict[str, object]) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def _checkout_steps(job: dict[str, object]) -> list[dict[str, object]]:
    return [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    ]


def _assert_release_security_invariants(workflow: dict[str, object]) -> None:
    jobs = workflow["jobs"]
    governance = jobs["governance"]
    verify = jobs["verify"]
    test_job = jobs["test"]
    package = jobs["package"]
    publish = jobs["publish"]

    verify_commands = _job_commands(verify)
    assert (
        "python trusted/scripts/evozeus_official_upgrade_verify.py "
        "verify-release"
    ) in verify_commands
    assert "python candidate/scripts/evozeus_official_upgrade_verify.py" not in verify_commands
    assert _checkout_steps(test_job)[0]["with"]["ref"] == (
        "${{ needs.verify.outputs.tag_commit }}"
    )
    assert _checkout_steps(package)[0]["with"]["ref"] == (
        "${{ needs.verify.outputs.tag_commit }}"
    )
    assert set(publish["needs"]) == {"governance", "verify", "test", "package"}
    assert "needs.governance.outputs.approved == 'true'" in publish["if"]

    governance_gate = governance["steps"][0]
    assert publish["steps"][0] == governance_gate
    create_index = next(
        index
        for index, step in enumerate(publish["steps"])
        if "gh release create" in step.get("run", "")
    )
    assert publish["steps"][create_index - 1] == governance_gate
    assert governance_gate["env"]["REQUIRED_CI_CONTEXT"] == "CI / test"
    assert governance_gate["env"]["REQUIRED_OFFICIAL_CONTEXT"] == (
        "EvoZeus Official Upgrade Profile / classify-and-verify"
    )
    assert governance_gate["env"]["EXPECTED_GITHUB_ACTIONS_APP_ID"] == "15368"
    assert governance_gate["run"].count(
        "((.bypass_actors // []) | length == 0)"
    ) == 2
    assert "index($ci) != null and index($official) != null" in governance_gate["run"]
    assert "strict_required_status_checks_policy == true" in governance_gate["run"]
    assert ".integration_id == $actions_app_id" in governance_gate["run"]
    assert ".required_status_checks.strict == true" in governance_gate["run"]
    assert ".app_id == $actions_app_id" in governance_gate["run"]
    artifact_step = next(
        step
        for step in publish["steps"]
        if step["name"] == "Verify artifact API provenance before download"
    )
    assert artifact_step["env"]["EXPECTED_ARTIFACT_DIGEST"] == (
        "${{ needs.package.outputs.artifact_digest }}"
    )
    publish_commands = _job_commands(publish)
    assert 'test "${REMOTE_RAW_TAG_OID}" = "${EXPECTED_RAW_TAG_OID}"' in publish_commands
    assert 'test "${OBJECT_SHA}" = "${EXPECTED_TAG_COMMIT}"' in publish_commands
    assert "gh release create" in publish_commands
    assert "--verify-tag" in publish_commands
    assert "--clobber" not in publish_commands
    assert "gh release upload" not in publish_commands


def test_release_workflow_is_manual_main_only_and_read_by_default() -> None:
    text, workflow = _release_workflow()
    trigger = workflow["on"]
    jobs = workflow["jobs"]
    assert isinstance(trigger, dict)
    assert isinstance(jobs, dict)

    assert set(trigger) == {"workflow_dispatch"}
    dispatch = trigger["workflow_dispatch"]
    assert dispatch["inputs"]["tag"] == {
        "description": "Existing SemVer tag to verify and publish (vMAJOR.MINOR.PATCH)",
        "required": "true",
        "type": "string",
    }
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    assert workflow["concurrency"] == {
        "group": "release-${{ github.repository }}-${{ inputs.tag }}",
        "cancel-in-progress": "false",
    }
    assert set(jobs) == {"governance", "verify", "test", "package", "publish"}
    assert jobs["test"]["needs"] == ["governance", "verify"]
    assert jobs["package"]["needs"] == ["governance", "verify", "test"]
    assert jobs["publish"]["needs"] == ["governance", "verify", "test", "package"]
    for job_name in ("governance", "verify", "test", "package"):
        assert jobs[job_name]["permissions"] == {
            "actions": "read",
            "contents": "read",
        }
    assert jobs["publish"]["permissions"] == {
        "actions": "read",
        "contents": "write",
    }
    assert jobs["publish"]["environment"] == "release"
    for job_name in jobs:
        condition = jobs[job_name]["if"]
        assert "github.ref == 'refs/heads/main'" in condition
        assert "endsWith(github.workflow_ref, '@refs/heads/main')" in condition
    for job_name in ("test", "package", "publish"):
        condition = jobs[job_name]["if"]
        assert "needs.governance.result == 'success'" in condition
        assert "needs.governance.outputs.approved == 'true'" in condition
    dispatch_gate = jobs["verify"]["steps"][0]["run"]
    assert 'test "${GITHUB_REF}" = "refs/heads/main"' in dispatch_gate
    assert 'test "${GITHUB_WORKFLOW_REF}" = "${EXPECTED_WORKFLOW_REF}"' in dispatch_gate
    assert "release tag must use vMAJOR.MINOR.PATCH" in dispatch_gate
    assert "remains fail-closed until repository administrators configure" in text
    assert "only\n# reads those controls; it never creates or changes them" in text
    assert "--method" not in _job_commands(jobs["governance"])


def test_release_governance_job_proves_external_admin_controls_read_only() -> None:
    _text, workflow = _release_workflow()
    jobs = workflow["jobs"]
    governance = jobs["governance"]
    assert _checkout_steps(governance) == []
    assert governance["outputs"] == {
        "approved": "${{ steps.approval.outputs.approved }}"
    }
    step = governance["steps"][0]
    assert step["env"]["ATTESTATION"] == (
        "${{ vars.EVOZEUS_RELEASE_GOVERNANCE_ATTESTATION }}"
    )
    assert step["env"]["GH_TOKEN"] == "${{ secrets.EVOZEUS_GOVERNANCE_TOKEN }}"
    assert step["env"]["EXPECTED_GITHUB_ACTIONS_APP_ID"] == "15368"
    assert step["env"]["REQUIRED_CI_CONTEXT"] == "CI / test"
    assert step["env"]["REQUIRED_OFFICIAL_CONTEXT"] == (
        "EvoZeus Official Upgrade Profile / classify-and-verify"
    )
    run = step["run"]
    for required in (
        'test -n "${GH_TOKEN}"',
        'test "${ATTESTATION}" = "${EXPECTED_ATTESTATION}"',
        "repos/${GH_REPO}/rulesets?per_page=100",
        "repos/${GH_REPO}/rulesets/${RULESET_ID}",
        'index("pull_request")',
        'index("required_status_checks")',
        "strict_required_status_checks_policy == true",
        ".integration_id == $actions_app_id",
        'index("update")',
        "((.bypass_actors // []) | length == 0)",
        "index($ci) != null and index($official) != null",
        "repos/${GH_REPO}/branches/main/protection",
        ".required_status_checks.strict == true",
        ".app_id == $actions_app_id",
        ".required_pull_request_reviews.required_approving_review_count >= 1",
        ".enforce_admins.enabled == true",
        "repos/${GH_REPO}/environments/${RELEASE_ENVIRONMENT}",
        '.type == "required_reviewers"',
        ".prevent_self_review == true",
        "deployment-branch-policies?per_page=100",
        "repos/${GH_REPO}/immutable-releases",
        ".enabled == true",
    ):
        assert required in run
    assert run.count("((.bypass_actors // []) | length == 0)") == 2
    approval = governance["steps"][1]
    assert approval["id"] == "approval"
    assert "approved=true" in approval["run"]
    assert "approved=true" not in run
    assert "gh api --method" not in run
    publish_gate = jobs["publish"]["steps"][0]
    assert publish_gate == step
    assert publish_gate["env"]["GH_TOKEN"] == (
        "${{ secrets.EVOZEUS_GOVERNANCE_TOKEN }}"
    )


def _valid_release_governance_responses() -> dict[str, object]:
    required_contexts = [
        {"context": "CI / test", "integration_id": 15368},
        {
            "context": "EvoZeus Official Upgrade Profile / classify-and-verify",
            "integration_id": 15368,
        },
    ]
    return {
        "ruleset_list": [
            {"id": 1, "target": "branch", "enforcement": "active"},
            {"id": 2, "target": "tag", "enforcement": "active"},
        ],
        "branch_ruleset": {
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {
                "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
            },
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "pull_request"},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": required_contexts,
                        "strict_required_status_checks_policy": True,
                    },
                },
            ],
        },
        "tag_ruleset": {
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {
                "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
            },
            "rules": [{"type": "deletion"}, {"type": "update"}],
        },
        "branch_protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": [
                    "CI / test",
                    "EvoZeus Official Upgrade Profile / classify-and-verify",
                ],
                "checks": [
                    {"context": "CI / test", "app_id": 15368},
                    {
                        "context": (
                            "EvoZeus Official Upgrade Profile / "
                            "classify-and-verify"
                        ),
                        "app_id": 15368,
                    }
                ],
            },
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        "environment": {
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [{"type": "Team", "reviewer": {"id": 7}}],
                }
            ],
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        },
        "deployment_policies": {
            "branch_policies": [{"type": "branch", "name": "main"}]
        },
        "immutable_releases": {"enabled": True, "enforced_by_owner": False},
    }


def _run_release_governance_gate(
    tmp_path: Path,
    responses: dict[str, object],
    *,
    attestation: str = "evozeus-release-governance-v1",
) -> subprocess.CompletedProcess[str]:
    _text, workflow = _release_workflow()
    gate = workflow["jobs"]["governance"]["steps"][0]["run"]
    fake_gh = r'''
gh() {
  local endpoint="${!#}"
  if test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/rulesets?per_page=100"; then
    printf '[%s]\n' "${RULESET_LIST}"
  elif test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/rulesets/1"; then
    printf '%s\n' "${BRANCH_RULESET}"
  elif test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/rulesets/2"; then
    printf '%s\n' "${TAG_RULESET}"
  elif test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/branches/main/protection"; then
    printf '%s\n' "${BRANCH_PROTECTION}"
  elif test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/environments/release"; then
    printf '%s\n' "${ENVIRONMENT_RESPONSE}"
  elif test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/environments/release/deployment-branch-policies?per_page=100"; then
    printf '%s\n' "${DEPLOYMENT_POLICIES}"
  elif test "${endpoint}" = "repos/MetaInFLow/EvoZeus-CoEvolve/immutable-releases"; then
    printf '%s\n' "${IMMUTABLE_RELEASES}"
  else
    printf 'unexpected endpoint: %s\n' "${endpoint}" >&2
    return 90
  fi
}
'''
    environment = {
        **os.environ,
        "ATTESTATION": attestation,
        "EXPECTED_ATTESTATION": "evozeus-release-governance-v1",
        "GH_REPO": "MetaInFLow/EvoZeus-CoEvolve",
        "GH_TOKEN": "admin-read-token",
        "EXPECTED_GITHUB_ACTIONS_APP_ID": "15368",
        "RELEASE_ENVIRONMENT": "release",
        "REQUIRED_CI_CONTEXT": "CI / test",
        "REQUIRED_OFFICIAL_CONTEXT": (
            "EvoZeus Official Upgrade Profile / classify-and-verify"
        ),
        "RUNNER_TEMP": str(tmp_path),
        "RULESET_LIST": json.dumps(responses["ruleset_list"]),
        "BRANCH_RULESET": json.dumps(responses["branch_ruleset"]),
        "TAG_RULESET": json.dumps(responses["tag_ruleset"]),
        "BRANCH_PROTECTION": json.dumps(responses["branch_protection"]),
        "ENVIRONMENT_RESPONSE": json.dumps(responses["environment"]),
        "DEPLOYMENT_POLICIES": json.dumps(responses["deployment_policies"]),
        "IMMUTABLE_RELEASES": json.dumps(responses["immutable_releases"]),
    }
    return subprocess.run(
        ["bash"],
        input=fake_gh + gate,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def test_release_governance_gate_accepts_only_the_complete_external_contract(
    tmp_path: Path,
) -> None:
    result = _run_release_governance_gate(
        tmp_path,
        _valid_release_governance_responses(),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "downgrade",
    [
        "branch_bypass_actor",
        "tag_bypass_actor",
        "missing_ci_context",
        "missing_official_context",
        "missing_integration_id",
        "null_integration_id",
        "wrong_integration_id",
        "missing_app_id",
        "wrong_app_id",
        "ruleset_strict_false",
        "branch_protection_strict_false",
        "contexts_only",
        "unprotected_environment",
        "mutable_future_releases",
        "invalid_attestation",
    ],
)
def test_release_governance_gate_rejects_external_control_downgrades(
    tmp_path: Path,
    downgrade: str,
) -> None:
    responses = copy.deepcopy(_valid_release_governance_responses())
    attestation = "evozeus-release-governance-v1"
    if downgrade == "branch_bypass_actor":
        responses["branch_ruleset"]["bypass_actors"] = [{"actor_id": 1}]
    elif downgrade == "tag_bypass_actor":
        responses["tag_ruleset"]["bypass_actors"] = [{"actor_id": 1}]
    elif downgrade == "missing_ci_context":
        checks = responses["branch_ruleset"]["rules"][3]["parameters"][
            "required_status_checks"
        ]
        checks[:] = [item for item in checks if item["context"] != "CI / test"]
    elif downgrade == "missing_official_context":
        checks = responses["branch_protection"]["required_status_checks"]["checks"]
        checks[:] = [
            item
            for item in checks
            if item["context"]
            != "EvoZeus Official Upgrade Profile / classify-and-verify"
        ]
    elif downgrade == "missing_integration_id":
        del responses["branch_ruleset"]["rules"][3]["parameters"][
            "required_status_checks"
        ][0]["integration_id"]
    elif downgrade == "null_integration_id":
        responses["branch_ruleset"]["rules"][3]["parameters"][
            "required_status_checks"
        ][0]["integration_id"] = None
    elif downgrade == "wrong_integration_id":
        responses["branch_ruleset"]["rules"][3]["parameters"][
            "required_status_checks"
        ][0]["integration_id"] = 1
    elif downgrade == "missing_app_id":
        del responses["branch_protection"]["required_status_checks"]["checks"][0][
            "app_id"
        ]
    elif downgrade == "wrong_app_id":
        responses["branch_protection"]["required_status_checks"]["checks"][0][
            "app_id"
        ] = 1
    elif downgrade == "ruleset_strict_false":
        responses["branch_ruleset"]["rules"][3]["parameters"][
            "strict_required_status_checks_policy"
        ] = False
    elif downgrade == "branch_protection_strict_false":
        responses["branch_protection"]["required_status_checks"]["strict"] = False
    elif downgrade == "contexts_only":
        responses["branch_protection"]["required_status_checks"]["checks"] = []
    elif downgrade == "unprotected_environment":
        responses["environment"]["protection_rules"] = []
    elif downgrade == "mutable_future_releases":
        responses["immutable_releases"]["enabled"] = False
    elif downgrade == "invalid_attestation":
        attestation = "unreviewed"
    else:
        raise AssertionError(f"unknown governance downgrade: {downgrade}")
    result = _run_release_governance_gate(
        tmp_path,
        responses,
        attestation=attestation,
    )
    assert result.returncode != 0


def test_release_trusted_verifier_and_candidate_tests_are_runner_isolated() -> None:
    _text, workflow = _release_workflow()
    jobs = workflow["jobs"]
    verify = jobs["verify"]
    test_job = jobs["test"]
    package = jobs["package"]
    publish = jobs["publish"]
    all_steps = [
        step
        for job in jobs.values()
        for step in job["steps"]
    ]
    action_uses = [step["uses"] for step in all_steps if "uses" in step]

    assert set(action_uses) == {
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
    }
    assert len(action_uses) == 6
    assert all(
        re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action) is not None
        for action in action_uses
    )
    trusted_checkout, candidate_checkout = _checkout_steps(verify)
    assert trusted_checkout["with"] == {
        "ref": "${{ github.workflow_sha }}",
        "path": "trusted",
        "fetch-depth": "1",
        "persist-credentials": "false",
    }
    assert candidate_checkout["with"] == {
        "ref": "refs/tags/${{ inputs.tag }}",
        "path": "candidate",
        "fetch-depth": "0",
        "persist-credentials": "false",
    }
    identity_gate = next(step for step in verify["steps"] if step.get("id") == "identities")[
        "run"
    ]
    assert 'refs/tags/${REQUESTED_TAG}^{object}' in identity_gate
    assert 'refs/tags/${REQUESTED_TAG}^{commit}' in identity_gate
    assert 'test "${TRUSTED_WORKFLOW_SHA}" = "${EXPECTED_HEAD_SHA}"' in identity_gate
    release_gate = verify["steps"][-1]["run"]
    assert "python trusted/scripts/evozeus_official_upgrade_verify.py" in release_gate
    assert "--repo-root candidate" in release_gate
    assert "--main-ref refs/remotes/origin/main" in release_gate
    assert "candidate/scripts/evozeus_official_upgrade_verify.py" not in release_gate
    assert verify["outputs"] == {
        "head_sha": "${{ steps.identities.outputs.head_sha }}",
        "raw_tag_oid": "${{ steps.identities.outputs.raw_tag_oid }}",
        "run_id": "${{ steps.identities.outputs.run_id }}",
        "tag_commit": "${{ steps.identities.outputs.tag_commit }}",
        "workflow_sha": "${{ steps.identities.outputs.workflow_sha }}",
    }
    test_checkout = _checkout_steps(test_job)[0]
    package_checkout = _checkout_steps(package)[0]
    for checkout in (test_checkout, package_checkout):
        assert checkout["with"]["ref"] == "${{ needs.verify.outputs.tag_commit }}"
        assert checkout["with"]["fetch-depth"] == "1"
        assert checkout["with"]["persist-credentials"] == "false"
    test_commands = _job_commands(test_job)
    assert "python -m pip install --require-hashes -r requirements-commonmark.lock" in test_commands
    assert test_commands.index("--require-hashes -r requirements-commonmark.lock") < test_commands.index(
        "python -m pytest -q"
    )
    assert "python -m pytest -q" in test_commands
    assert "verify-base" not in test_commands
    assert "verify-release" not in test_commands
    assert all("actions/checkout@" not in step.get("uses", "") for step in publish["steps"])


def test_release_package_uses_only_peeled_commit_and_binds_three_files() -> None:
    _text, workflow = _release_workflow()
    package = workflow["jobs"]["package"]
    package_step = next(step for step in package["steps"] if step.get("id") == "package")
    run = package_step["run"]
    assert 'git -C candidate ls-tree "${TAG_COMMIT}" -- "${NOTES_SOURCE}"' in run
    assert 'git -C candidate show "${TAG_COMMIT}:${NOTES_SOURCE}"' in run
    assert "git -C candidate archive" in run
    assert '"${TAG_COMMIT}"' in run
    assert 'git -C candidate ls-tree "${REQUESTED_TAG}"' not in run
    assert 'git -C candidate show "${REQUESTED_TAG}' not in run
    assert 'git -C candidate archive "${REQUESTED_TAG}"' not in run
    for digest in ("ARCHIVE_SHA256", "CHECKSUM_SHA256", "NOTES_SHA256"):
        assert digest in run
    assert (
        'ARTIFACT_NAME="evozeus-release-${REQUESTED_TAG}-${GITHUB_RUN_ID}-'
        '${GITHUB_RUN_ATTEMPT}-${TAG_COMMIT}"'
    ) in run
    assert package["outputs"] == {
        "archive_sha256": "${{ steps.package.outputs.archive_sha256 }}",
        "artifact_digest": "${{ steps.upload.outputs.artifact-digest }}",
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
        "artifact_name": "${{ steps.package.outputs.artifact_name }}",
        "checksum_sha256": "${{ steps.package.outputs.checksum_sha256 }}",
        "notes_sha256": "${{ steps.package.outputs.notes_sha256 }}",
    }
    upload = next(step for step in package["steps"] if step.get("id") == "upload")
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["compression-level"] == "0"
    assert len(upload["with"]["path"].strip().splitlines()) == 3


def test_release_publish_job_only_publishes_verified_exact_payload() -> None:
    _text, workflow = _release_workflow()
    publish = workflow["jobs"]["publish"]
    steps = publish["steps"]

    assert all("actions/checkout@" not in step.get("uses", "") for step in steps)
    publish_commands = "\n".join(step.get("run", "") for step in steps)
    assert "git archive" not in publish_commands
    assert "python" not in publish_commands
    assert "pytest" not in publish_commands
    assert "pip install" not in publish_commands
    assert "candidate" not in publish_commands
    artifact_index = next(
        index
        for index, step in enumerate(steps)
        if step["name"] == "Verify artifact API provenance before download"
    )
    download_index = next(
        index
        for index, step in enumerate(steps)
        if "actions/download-artifact@" in step.get("uses", "")
    )
    payload_index = next(
        index
        for index, step in enumerate(steps)
        if step["name"] == "Verify exact downloaded payload and all file digests"
    )
    publish_index = next(
        index
        for index, step in enumerate(steps)
        if "gh release create" in step.get("run", "")
    )
    assert artifact_index == 1
    assert artifact_index < download_index < payload_index < publish_index
    assert steps[0] == workflow["jobs"]["governance"]["steps"][0]
    assert steps[publish_index - 1] == workflow["jobs"]["governance"]["steps"][0]
    artifact_gate = steps[artifact_index]
    assert artifact_gate["env"]["EXPECTED_ARTIFACT_DIGEST"] == (
        "${{ needs.package.outputs.artifact_digest }}"
    )
    for field in (
        ".id == $artifact_id",
        ".name == $name",
        ".expired == false",
        ".digest == $digest",
        ".workflow_run.id == $run_id",
        ".workflow_run.head_sha == $head_sha",
    ):
        assert field in artifact_gate["run"]
    assert "repos/${GH_REPO}/actions/artifacts/${EXPECTED_ARTIFACT_ID}" in artifact_gate["run"]

    download = steps[download_index]
    assert download["uses"] == (
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    )
    assert download["with"]["artifact-ids"] == "${{ needs.package.outputs.artifact_id }}"
    payload_gate = steps[payload_index]["run"]
    assert "EXPECTED_ARCHIVE_SHA256" in payload_gate
    assert "EXPECTED_CHECKSUM_SHA256" in payload_gate
    assert "EXPECTED_NOTES_SHA256" in payload_gate
    assert "EXPECTED_FILES" in payload_gate
    assert "ACTUAL_ENTRY_COUNT" in payload_gate
    assert payload_gate.count("sha256sum") == 3
    assert '| cmp -s - "${PAYLOAD_DIR}/${CHECKSUM}"' in payload_gate

    publish_run = steps[publish_index]["run"]
    assert steps[publish_index]["env"]["GH_REPO"] == "${{ github.repository }}"
    assert "repos/${GH_REPO}/git/ref/tags/${REQUESTED_TAG}" in publish_run
    assert "repos/${GH_REPO}/git/tags/${OBJECT_SHA}" in publish_run
    assert 'test "${REMOTE_RAW_TAG_OID}" = "${EXPECTED_RAW_TAG_OID}"' in publish_run
    assert 'test "${OBJECT_SHA}" = "${EXPECTED_TAG_COMMIT}"' in publish_run
    assert 'test "${TAG_DEPTH}" -le 16' in publish_run
    assert "repos/${GH_REPO}/releases/tags/${REQUESTED_TAG}" in publish_run
    assert 'test "${RELEASE_STATUS}" -eq 0' in publish_run
    assert '"HTTP 404"' in publish_run
    assert "gh release create" in publish_run
    assert publish_commands.count("gh release create") == 1
    assert "--verify-tag" in publish_run
    assert '"${PAYLOAD_DIR}/${ARCHIVE}"' in publish_run
    assert '"${PAYLOAD_DIR}/${CHECKSUM}"' in publish_run
    assert "gh release upload" not in publish_run
    assert "--clobber" not in publish_run
    assert "gh release view" not in publish_run
    _assert_release_security_invariants(workflow)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "python trusted/scripts/evozeus_official_upgrade_verify.py verify-release",
            "python candidate/scripts/evozeus_official_upgrade_verify.py verify-release",
        ),
        (
            "ref: ${{ needs.verify.outputs.tag_commit }}",
            "ref: refs/tags/${{ inputs.tag }}",
        ),
        (
            "needs: [governance, verify, test, package]",
            "needs: [verify, test, package]",
        ),
        (
            "EXPECTED_ARTIFACT_DIGEST: ${{ needs.package.outputs.artifact_digest }}",
            "EXPECTED_ARTIFACT_DIGEST: unbound",
        ),
        (
            "((.bypass_actors // []) | length == 0)",
            "true # ruleset bypass actors were not rejected",
        ),
        (
            "REQUIRED_CI_CONTEXT: CI / test",
            "REQUIRED_CI_CONTEXT: unbound",
        ),
        (
            "- *release_governance_gate",
            "- name: Governance recheck was removed\n        run: 'true'",
        ),
        (
            'test "${REMOTE_RAW_TAG_OID}" = "${EXPECTED_RAW_TAG_OID}"',
            "true # remote raw tag identity was not checked",
        ),
        ("--verify-tag", "--clobber"),
    ],
)
def test_release_security_contract_rejects_unsafe_workflow_mutations(
    old: str,
    new: str,
) -> None:
    text, _workflow = _release_workflow()
    assert old in text
    mutated = text.replace(old, new, 1)
    document = yaml.load(mutated, Loader=yaml.BaseLoader)
    with pytest.raises(AssertionError):
        _assert_release_security_invariants(document)
