#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MIGRATION_PROTOCOL_VERSION = "v1.0.0"
MIGRATION_CONTRACT_BUNDLE_ROOT = "contracts/v1"
MIGRATION_CONTRACT_REL = "migrations/harness-migration-contract-v1.json"
MIGRATION_CONTRACT_SCHEMA_VERSION = (
    "evozeus.coevolve.harness-migration-contract.v1"
)
MIGRATION_CONTRACT_ID = "evozeus-harness-migration"
MIGRATION_CONTRACT_VERSION = "v1.0.0"
TARGET_MIGRATION_CONTRACT = ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
CANONICAL_HARNESS_SKILL = ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md"
SNAPSHOT_SCHEMA_VERSION = "evozeus.coevolve.harness-migration-snapshot.v1"
SNAPSHOT_RECEIPT_SCHEMA_VERSION = (
    "evozeus.coevolve.harness-migration-snapshot-receipt.v1"
)
OFFICIAL_SOURCE_REPOSITORY = "MetaInFLow/EvoZeus-CoEvolve"
CANONICAL_ACTIVATION_CONTRACT = {
    "block_id": "evozeus-harness-entry",
    "marker_version": "v1",
    "begin_marker": "<!-- evozeus-harness-entry:v1 -->",
    "end_marker": "<!-- /evozeus-harness-entry -->",
    "sha256_lf": "078bb2020284fbd6f91c12e46a2c726e64a4f4bbdef0320f4e40adcef26d3cea",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _is_plain_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}: expected a JSON object: {path}")
    return value


def _git(wrapper_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(wrapper_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _git_bytes(wrapper_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(wrapper_root), *args],
        capture_output=True,
        check=False,
    )


def _repo_from_remote(remote: str) -> str | None:
    value = remote.strip()
    match = None
    if value.startswith("git@github.com:"):
        match = value.removeprefix("git@github.com:")
    elif value.startswith("https://github.com/"):
        match = value.removeprefix("https://github.com/")
    if match is None:
        return None
    return match.removesuffix(".git").strip("/") or None


def _release_source_trust(
    wrapper_root: Path,
    bundle_manifest: dict[str, Any],
    contract_bytes: bytes,
    contract: dict[str, Any],
) -> dict[str, Any]:
    revision = bundle_manifest.get("source_revision")
    reasons: list[str] = []
    remote = _git(wrapper_root, "config", "--get", "remote.origin.url")
    repository = _repo_from_remote(remote.stdout) if remote.returncode == 0 else None
    if repository != OFFICIAL_SOURCE_REPOSITORY:
        reasons.append(
            "source repository is not the official EvoZeus-CoEvolve origin"
        )
    if not isinstance(revision, str) or not revision.startswith("v"):
        reasons.append("contract bundle source_revision is not an immutable release tag")
        revision = None

    resolved_commit = None
    head_commit = None
    worktree_clean = False
    remote_tag_commit = None
    remote_tag_verified = False
    tagged_bundle_sha256 = None
    source_attestations: list[dict[str, Any]] = []
    head = _git(wrapper_root, "rev-parse", "HEAD")
    if head.returncode == 0 and head.stdout.strip():
        head_commit = head.stdout.strip()
    else:
        reasons.append("source HEAD cannot be resolved")
    status = _git(wrapper_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        reasons.append("source worktree status cannot be verified")
    elif status.stdout:
        reasons.append("source worktree is not clean")
    else:
        worktree_clean = True
    if revision:
        resolved = _git(wrapper_root, "rev-parse", f"refs/tags/{revision}^{{commit}}")
        if resolved.returncode != 0 or not resolved.stdout.strip():
            reasons.append(f"release tag is unavailable: {revision}")
        else:
            resolved_commit = resolved.stdout.strip()
            if head_commit != resolved_commit:
                reasons.append("source HEAD does not equal the declared release tag commit")
            tagged_manifest_result = _git_bytes(
                wrapper_root,
                "show",
                f"{revision}:{MIGRATION_CONTRACT_BUNDLE_ROOT}/manifest.json",
            )
            tagged_contract_result = _git_bytes(
                wrapper_root,
                "show",
                f"{revision}:{MIGRATION_CONTRACT_BUNDLE_ROOT}/{MIGRATION_CONTRACT_REL}",
            )
            if tagged_manifest_result.returncode != 0 or tagged_contract_result.returncode != 0:
                reasons.append(
                    "migration contract is not present in the declared release tag"
                )
            else:
                tagged_manifest_bytes = tagged_manifest_result.stdout
                tagged_contract_bytes = tagged_contract_result.stdout
                tagged_bundle_sha256 = f"sha256:{sha256_bytes(tagged_manifest_bytes)}"
                try:
                    tagged_manifest = json.loads(tagged_manifest_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    tagged_manifest = None
                tagged_entry = next(
                    (
                        item
                        for item in (tagged_manifest or {}).get("files", [])
                        if isinstance(item, dict)
                        and item.get("path") == MIGRATION_CONTRACT_REL
                    ),
                    None,
                )
                tagged_sha256 = sha256_bytes(tagged_contract_bytes)
                if tagged_entry is None or tagged_entry.get("sha256") != tagged_sha256:
                    reasons.append(
                        "release tag does not bind the migration contract digest"
                    )
                if tagged_contract_bytes != contract_bytes:
                    reasons.append(
                        "working migration contract differs from the declared release artifact"
                    )
                declared_sources: list[dict[str, Any]] = []
                for profile in contract.get("profiles", []):
                    payload = profile.get("adapter_payload") if isinstance(profile, dict) else None
                    sources = payload.get("write_sources", []) if isinstance(payload, dict) else []
                    if isinstance(sources, list):
                        declared_sources.extend(
                            item for item in sources if isinstance(item, dict)
                        )
                seen_source_paths: set[str] = set()
                for source in declared_sources:
                    source_path = source.get("source_path")
                    expected_sha256 = source.get("sha256")
                    if source_path in seen_source_paths:
                        continue
                    if not isinstance(source_path, str) or not isinstance(expected_sha256, str):
                        reasons.append("migration write source identity is incomplete")
                        continue
                    seen_source_paths.add(source_path)
                    try:
                        relative = _safe_relative_path(source_path, "write source path")
                        working_path = _safe_file_below(
                            wrapper_root,
                            relative,
                            "migration write source",
                        )
                    except ValueError as exc:
                        reasons.append(str(exc))
                        continue
                    working_bytes = working_path.read_bytes()
                    tagged_source = _git_bytes(
                        wrapper_root,
                        "show",
                        f"{revision}:{relative.as_posix()}",
                    )
                    tagged_sha256 = (
                        sha256_bytes(tagged_source.stdout)
                        if tagged_source.returncode == 0
                        else None
                    )
                    working_sha256 = sha256_bytes(working_bytes)
                    source_attestations.append(
                        {
                            "source_path": relative.as_posix(),
                            "declared_sha256": expected_sha256,
                            "working_sha256": working_sha256,
                            "tagged_sha256": tagged_sha256,
                        }
                    )
                    if working_sha256 != expected_sha256:
                        reasons.append(
                            f"working migration write source digest mismatch: {source_path}"
                        )
                    if tagged_source.returncode != 0:
                        reasons.append(
                            f"migration write source is absent from release tag: {source_path}"
                        )
                    elif tagged_sha256 != expected_sha256 or tagged_source.stdout != working_bytes:
                        reasons.append(
                            f"migration write source is not release-bound: {source_path}"
                        )
            if not reasons:
                remote_tag = _git(
                    wrapper_root,
                    "ls-remote",
                    "--tags",
                    "origin",
                    f"refs/tags/{revision}",
                    f"refs/tags/{revision}^{{}}",
                )
                if remote_tag.returncode != 0:
                    reasons.append("official origin release tag cannot be verified")
                else:
                    direct_commit = None
                    peeled_commit = None
                    for line in remote_tag.stdout.splitlines():
                        fields = line.split()
                        if len(fields) != 2:
                            continue
                        commit, ref = fields
                        if ref == f"refs/tags/{revision}^{{}}":
                            peeled_commit = commit
                        elif ref == f"refs/tags/{revision}":
                            direct_commit = commit
                    remote_tag_commit = peeled_commit or direct_commit
                    if not remote_tag_commit:
                        reasons.append(
                            "declared release tag is absent from the official origin"
                        )
                    elif remote_tag_commit != resolved_commit:
                        reasons.append(
                            "local release tag commit differs from the official origin"
                        )
                    else:
                        remote_tag_verified = True
    return {
        "status": "trusted_release" if not reasons else "source_unreleased",
        "official_repository": OFFICIAL_SOURCE_REPOSITORY,
        "repository": repository,
        "release_tag": revision,
        "resolved_commit": resolved_commit,
        "head_commit": head_commit,
        "worktree_clean": worktree_clean,
        "remote_tag_commit": remote_tag_commit,
        "remote_tag_verified": remote_tag_verified,
        "tagged_bundle_sha256": tagged_bundle_sha256,
        "source_attestations": source_attestations,
        "reasons": reasons,
    }


def load_migration_contract(wrapper_root: Path | None = None) -> dict[str, Any]:
    """Load a release-bound contract and verify every relative identity."""
    wrapper_root = (
        Path(__file__).resolve().parents[1]
        if wrapper_root is None
        else wrapper_root.expanduser().resolve()
    )
    bundle_root = wrapper_root / MIGRATION_CONTRACT_BUNDLE_ROOT
    bundle_manifest_path = _safe_file_below(
        wrapper_root,
        Path(MIGRATION_CONTRACT_BUNDLE_ROOT) / "manifest.json",
        "contract bundle manifest",
    )
    contract_path = _safe_file_below(
        wrapper_root,
        Path(MIGRATION_CONTRACT_BUNDLE_ROOT) / MIGRATION_CONTRACT_REL,
        "Harness migration contract",
    )
    bundle_manifest = _json_object(bundle_manifest_path, "contract bundle manifest")
    if bundle_manifest.get("schema_version") != "evozeus.coevolve.contract-manifest.v1":
        raise ValueError("unsupported contract bundle manifest schema")
    if bundle_manifest.get("source_repository") != OFFICIAL_SOURCE_REPOSITORY:
        raise ValueError("contract bundle source_repository is not official")
    declared = next(
        (
            item
            for item in bundle_manifest.get("files", [])
            if isinstance(item, dict) and item.get("path") == MIGRATION_CONTRACT_REL
        ),
        None,
    )
    if declared is None:
        raise ValueError(
            f"contract bundle does not bind migration contract: {MIGRATION_CONTRACT_REL}"
        )
    if declared.get("role") != "harness-migration-contract":
        raise ValueError("contract bundle migration contract role is invalid")
    contract_bytes = contract_path.read_bytes()
    contract_sha256 = sha256_bytes(contract_bytes)
    if declared.get("sha256") != contract_sha256:
        raise ValueError(
            "migration contract hash does not match contracts/v1/manifest.json"
        )

    contract = _json_object(contract_path, "Harness migration contract")
    expected_contract_identity = {
        "schema_version": MIGRATION_CONTRACT_SCHEMA_VERSION,
        "migration_protocol_version": MIGRATION_PROTOCOL_VERSION,
        "contract_id": MIGRATION_CONTRACT_ID,
        "contract_version": MIGRATION_CONTRACT_VERSION,
        "current_harness_skill_version": "v1.1.0",
        "canonical_harness_skill_path": CANONICAL_HARNESS_SKILL,
        "canonical_activation_block": CANONICAL_ACTIVATION_CONTRACT,
    }
    identity_mismatches = [
        field
        for field, expected in expected_contract_identity.items()
        if contract.get(field) != expected
    ]
    if identity_mismatches:
        raise ValueError(
            "Harness migration contract identity is incompatible: "
            + ", ".join(identity_mismatches)
        )
    if contract.get("migration_protocol_version") != MIGRATION_PROTOCOL_VERSION:
        raise ValueError(
            "unsupported Harness migration protocol: "
            f"{contract.get('migration_protocol_version')}"
        )
    roots = contract.get("path_roots")
    if roots != {
        "artifact_path": MIGRATION_CONTRACT_BUNDLE_ROOT,
        "target_path": "target_repository_root",
    }:
        raise ValueError("migration contract path_roots are missing or ambiguous")

    profiles = contract.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("migration contract must declare profiles")
    seen_profile_ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("migration contract profile must be an object")
        if "from" not in profile or "to" not in profile:
            raise ValueError("migration profile must declare from and to states")
        required = (
            "profile_id",
            "profile_version",
            "adapter_id",
            "adapter_version",
            "adapter_sha256",
            "adapter_payload",
        )
        missing = [field for field in required if not profile.get(field)]
        if missing:
            raise ValueError(
                f"migration profile identity is incomplete: {profile.get('profile_id')}: "
                + ", ".join(missing)
            )
        if any(
            not isinstance(profile.get(field), str)
            for field in (
                "profile_id",
                "profile_version",
                "adapter_id",
                "adapter_version",
                "adapter_sha256",
            )
        ):
            raise ValueError("migration profile identity fields must be strings")
        profile_id = profile["profile_id"]
        if not isinstance(profile_id, str) or profile_id in seen_profile_ids:
            raise ValueError(f"migration profile id is invalid or duplicate: {profile_id}")
        seen_profile_ids.add(profile_id)
        if not isinstance(profile.get("from"), dict) or (
            profile.get("to") is not None and not isinstance(profile.get("to"), dict)
        ):
            raise ValueError(f"migration profile state identity is invalid: {profile_id}")
        if not isinstance(profile.get("automatic"), bool):
            raise ValueError(f"migration profile automatic flag is invalid: {profile_id}")
        payload = profile["adapter_payload"]
        if not isinstance(payload, dict):
            raise ValueError(f"migration adapter payload must be an object: {profile_id}")
        if not _is_plain_sha256(profile["adapter_sha256"]):
            raise ValueError(f"migration adapter digest format is invalid: {profile_id}")
        adapter_sha256 = canonical_json_sha256(payload)
        if profile["adapter_sha256"] != adapter_sha256:
            raise ValueError(
                f"migration adapter digest mismatch: {profile_id}"
            )
        trusted_preimages = payload.get("trusted_preimages", [])
        if not isinstance(trusted_preimages, list):
            raise ValueError(f"trusted preimages must be a list: {profile_id}")
        for artifact in trusted_preimages:
            if not isinstance(artifact, dict):
                raise ValueError(f"invalid trusted preimage: {profile_id}")
            _safe_relative_path(artifact.get("target_path"), "trusted target path")
            if not _is_plain_sha256(artifact.get("sha256")):
                raise ValueError(f"trusted preimage digest is invalid: {profile_id}")
            artifact_path = artifact.get("artifact_path")
            if artifact_path is None:
                continue
            relative = _safe_relative_path(artifact_path, "artifact_path")
            source = _safe_file_below(
                bundle_root,
                relative,
                "trusted migration artifact",
            )
            if sha256_file(source) != artifact.get("sha256"):
                raise ValueError(
                    f"trusted migration artifact hash mismatch: {artifact_path}"
                )
            manifest_path = relative.as_posix()
            artifact_binding = next(
                (
                    item
                    for item in bundle_manifest.get("files", [])
                    if isinstance(item, dict) and item.get("path") == manifest_path
                ),
                None,
            )
            if (
                artifact_binding is None
                or artifact_binding.get("role") != "trusted-migration-artifact"
                or artifact_binding.get("sha256") != artifact.get("sha256")
            ):
                raise ValueError(
                    f"contract bundle does not bind trusted artifact: {artifact_path}"
                )
        write_sources = payload.get("write_sources", [])
        if not isinstance(write_sources, list):
            raise ValueError(f"migration write_sources must be a list: {profile_id}")
        for source in write_sources:
            if not isinstance(source, dict):
                raise ValueError(f"migration write source is invalid: {profile_id}")
            _safe_relative_path(source.get("source_path"), "write source path")
            _safe_relative_path(source.get("target_path"), "write target path")
            if not _is_plain_sha256(source.get("sha256")):
                raise ValueError(f"migration write source digest is invalid: {profile_id}")

    source_trust = _release_source_trust(
        wrapper_root,
        bundle_manifest,
        contract_bytes,
        contract,
    )
    return {
        "contract": contract,
        "identity": {
            "migration_protocol_version": MIGRATION_PROTOCOL_VERSION,
            "contract_id": contract.get("contract_id"),
            "contract_version": contract.get("contract_version"),
            "source_path": f"{MIGRATION_CONTRACT_BUNDLE_ROOT}/{MIGRATION_CONTRACT_REL}",
            "target_path": TARGET_MIGRATION_CONTRACT,
            "sha256": f"sha256:{contract_sha256}",
        },
        "bundle_root": bundle_root,
        "path": contract_path,
        "wrapper_root": wrapper_root,
        "source_trust": source_trust,
    }


def contract_profile(contract: dict[str, Any], profile_id: str) -> dict[str, Any]:
    profile = next(
        (
            item
            for item in contract.get("profiles", [])
            if isinstance(item, dict) and item.get("profile_id") == profile_id
        ),
        None,
    )
    if profile is None:
        raise ValueError(f"migration contract profile is missing: {profile_id}")
    return profile


def profile_identity(profile: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {
        key: str(profile[key])
        for key in (
            "profile_id",
            "profile_version",
            "adapter_id",
            "adapter_version",
            "adapter_sha256",
        )
    }
    identity["from_state"] = profile.get("from")
    identity["to_state"] = profile.get("to")
    return identity


def _safe_relative_path(raw: object, label: str = "migration path") -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"{label} must be a non-empty POSIX relative path: {raw}")
    relative = Path(raw)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or relative.as_posix() != raw
    ):
        raise ValueError(f"{label} escapes its declared root: {raw}")
    return relative


def _safe_file_below(root: Path, relative: Path, label: str) -> Path:
    root = root.expanduser().resolve()
    candidate = root / relative
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError(f"{label} contains a symlink: {relative.as_posix()}")
        cursor = cursor.parent
    if not candidate.is_file():
        raise ValueError(f"{label} is missing: {relative.as_posix()}")
    return candidate


def _target_path(target: Path, raw: object) -> Path:
    relative = _safe_relative_path(raw)
    candidate = target / relative
    cursor = candidate
    while cursor != target:
        if cursor.is_symlink():
            raise ValueError(
                "migration path contains a symlink: "
                + str(cursor.relative_to(target))
            )
        cursor = cursor.parent
    return candidate


def migration_plan_digest(plan: dict[str, Any]) -> str:
    digest_input = {
        key: value
        for key, value in plan.items()
        if key not in {"plan_sha256", "approval", "snapshot"}
    }
    return canonical_json_sha256(digest_input)


def planned_target_paths(plan: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in plan.get("write_set", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            paths.append(item["path"])
    for item in plan.get("delete_set", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            paths.append(item["path"])
    for item in plan.get("move_set", []):
        if not isinstance(item, dict):
            continue
        for field in ("source", "destination"):
            if isinstance(item.get(field), str):
                paths.append(item[field])
    return list(dict.fromkeys(paths))


def verify_plan_preimages(target: Path, plan: dict[str, Any]) -> None:
    target = target.expanduser().resolve()
    if plan.get("decision") != "automatic_migration_available":
        raise ValueError(
            f"migration plan is not writable: decision={plan.get('decision')}"
        )
    if plan.get("can_apply") is not True:
        raise ValueError(
            "migration plan is blocked: "
            + "; ".join(str(item) for item in plan.get("apply_blockers", []))
        )
    if (plan.get("source_trust") or {}).get("status") != "trusted_release":
        raise ValueError("migration apply requires a trusted immutable source release")
    expected_plan_sha256 = plan.get("plan_sha256")
    actual_plan_sha256 = f"sha256:{migration_plan_digest(plan)}"
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(expected_plan_sha256)):
        raise ValueError("migration plan digest format is invalid")
    if expected_plan_sha256 != actual_plan_sha256:
        raise ValueError(
            "migration plan digest mismatch: "
            f"expected={expected_plan_sha256}; actual={actual_plan_sha256}"
        )
    operation_sets: dict[str, list[dict[str, Any]]] = {}
    for field in ("write_set", "delete_set", "move_set"):
        entries = plan.get(field)
        if not isinstance(entries, list) or any(
            not isinstance(item, dict) for item in entries
        ):
            raise ValueError(f"migration {field} must be a list of objects")
        operation_sets[field] = entries
    protected = plan.get("protected_business_surfaces")
    if not isinstance(protected, list) or any(
        not isinstance(item, dict) for item in protected
    ):
        raise ValueError("protected business surfaces must be a list of objects")

    mutation_paths: list[str] = []
    for item in operation_sets["write_set"]:
        relative = _safe_relative_path(item.get("path")).as_posix()
        mutation_paths.append(relative)
        preimage = item.get("preimage_sha256")
        postimage = item.get("postimage_sha256")
        if preimage is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(preimage)
        ):
            raise ValueError(f"migration write preimage is invalid: {relative}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(postimage)):
            raise ValueError(f"migration write postimage is invalid: {relative}")
    for item in operation_sets["delete_set"]:
        relative = _safe_relative_path(item.get("path")).as_posix()
        mutation_paths.append(relative)
        if not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(item.get("preimage_sha256"))
        ):
            raise ValueError(f"migration delete preimage is invalid: {relative}")
    for item in operation_sets["move_set"]:
        source = _safe_relative_path(item.get("source")).as_posix()
        destination = _safe_relative_path(item.get("destination")).as_posix()
        mutation_paths.extend((source, destination))
        if not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(item.get("source_preimage_sha256"))
        ):
            raise ValueError(f"migration move source preimage is invalid: {source}")
        destination_preimage = item.get("destination_preimage_sha256")
        if destination_preimage is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(destination_preimage)
        ):
            raise ValueError(
                f"migration move destination preimage is invalid: {destination}"
            )
        destination_postimage = item.get("destination_postimage_sha256") or item.get(
            "source_preimage_sha256"
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(destination_postimage)):
            raise ValueError(
                f"migration move destination postimage is invalid: {destination}"
            )
    if len(mutation_paths) != len(set(mutation_paths)):
        raise ValueError("migration plan contains duplicate or overlapping mutation paths")
    mutation_path_set = set(mutation_paths)
    for item in protected:
        relative = _safe_relative_path(item.get("path")).as_posix()
        if relative in mutation_path_set or item.get("planned_write") is not False:
            raise ValueError(
                f"protected business surface overlaps the mutation set: {relative}"
            )
        if item.get("rule") != "byte_exact" or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(item.get("preimage_sha256"))
        ):
            raise ValueError(f"protected business surface contract is invalid: {relative}")

    for item in operation_sets["write_set"]:
        path = _target_path(target, item.get("path"))
        expected = item.get("preimage_sha256")
        if expected is None:
            if path.exists() or path.is_symlink():
                raise ValueError(
                    f"migration create path appeared after planning: {item.get('path')}"
                )
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(
                f"migration preimage is missing or unsafe: {item.get('path')}"
            )
        actual = f"sha256:{sha256_file(path)}"
        if actual != expected:
            raise ValueError(
                f"migration preimage hash changed: {item.get('path')}: "
                f"expected={expected}; actual={actual}"
            )
    for item in operation_sets["delete_set"]:
        path = _target_path(target, item.get("path"))
        expected = item.get("preimage_sha256")
        if not path.is_file() or path.is_symlink() or f"sha256:{sha256_file(path)}" != expected:
            raise ValueError(
                f"migration delete preimage hash changed: {item.get('path')}"
            )
    for item in operation_sets["move_set"]:
        source = _target_path(target, item.get("source"))
        destination = _target_path(target, item.get("destination"))
        expected_source = item.get("source_preimage_sha256")
        expected_destination = item.get("destination_preimage_sha256")
        if (
            not source.is_file()
            or source.is_symlink()
            or f"sha256:{sha256_file(source)}" != expected_source
        ):
            raise ValueError(
                f"migration move source preimage hash changed: {item.get('source')}"
            )
        if expected_destination is None:
            if destination.exists() or destination.is_symlink():
                raise ValueError(
                    f"migration move destination appeared after planning: {item.get('destination')}"
                )
        elif (
            not destination.is_file()
            or destination.is_symlink()
            or f"sha256:{sha256_file(destination)}" != expected_destination
        ):
            raise ValueError(
                f"migration move destination preimage hash changed: {item.get('destination')}"
            )
    for item in protected:
        path = _target_path(target, item.get("path"))
        expected = item.get("preimage_sha256")
        if (
            not isinstance(expected, str)
            or not path.is_file()
            or path.is_symlink()
            or f"sha256:{sha256_file(path)}" != expected
        ):
            raise ValueError(
                f"protected business surface preimage changed: {item.get('path')}"
            )


def _lexical_absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _reject_symlink_chain(path: Path, label: str) -> None:
    absolute = _lexical_absolute(path)
    candidates = [absolute, *absolute.parents]
    for candidate in reversed(candidates[:-1]):
        if candidate.is_symlink():
            raise ValueError(f"{label} contains a symlink: {candidate}")


def _trusted_snapshot_base(target: Path, override: Path | None) -> Path:
    raw = (
        Path.home() / ".evozeus" / "backups" / "harness-migrations"
        if override is None
        else override
    )
    lexical = _lexical_absolute(raw)
    _reject_symlink_chain(lexical, "trusted snapshot root")
    base = lexical.resolve(strict=False)
    if base == target or target in base.parents:
        raise ValueError("trusted snapshot root must be outside the target repository")
    return base


def _snapshot_path(base: Path, snapshot: Path) -> Path:
    lexical = _lexical_absolute(snapshot)
    _reject_symlink_chain(lexical, "migration snapshot")
    resolved = lexical.resolve(strict=False)
    if resolved.parent != base:
        raise ValueError(
            "migration snapshot must be a direct transaction child of the trusted root"
        )
    if not re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{12}", resolved.name):
        raise ValueError("migration snapshot transaction id is invalid")
    return resolved


def _rollback_allowed_states(plan: dict[str, Any], relative_text: str, preimage: str) -> list[str]:
    states = [preimage]
    for item in plan.get("write_set", []):
        if item.get("path") == relative_text:
            postimage = item.get("postimage_sha256")
            if not isinstance(postimage, str):
                raise ValueError(
                    f"migration write set has no postimage: {relative_text}"
                )
            states.append(postimage)
    for item in plan.get("delete_set", []):
        if item.get("path") == relative_text:
            states.append("absent")
    for item in plan.get("move_set", []):
        if item.get("source") == relative_text:
            states.append("absent")
        if item.get("destination") == relative_text:
            postimage = item.get("destination_postimage_sha256") or item.get(
                "source_preimage_sha256"
            )
            if not isinstance(postimage, str):
                raise ValueError(
                    f"migration move set has no destination postimage: {relative_text}"
                )
            states.append(postimage)
    return list(dict.fromkeys(states))


def create_migration_snapshot(
    target: Path,
    plan: dict[str, Any],
    snapshot_root: Path | None = None,
) -> Path:
    """Persist the complete declared write set before the first target write."""
    target = target.expanduser().resolve()
    verify_plan_preimages(target, plan)
    base = _trusted_snapshot_base(target, snapshot_root)
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    _reject_symlink_chain(base, "trusted snapshot root")
    transaction_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + uuid.uuid4().hex[:12]
    )
    destination = base / transaction_id
    if destination.exists() or destination.is_symlink():
        raise ValueError("migration snapshot transaction already exists")
    destination.mkdir(mode=0o700)
    files_root = destination / "files"
    files_root.mkdir(mode=0o700)
    files: list[dict[str, Any]] = []
    existing_directories: set[str] = set()
    for relative_text in planned_target_paths(plan):
        path = _target_path(target, relative_text)
        parent = path.parent
        while parent != target:
            if parent.is_dir() and not parent.is_symlink():
                existing_directories.add(parent.relative_to(target).as_posix())
            parent = parent.parent
        item: dict[str, Any] = {
            "path": relative_text,
            "kind": "absent",
            "mode": None,
            "sha256": None,
        }
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            item.update(
                {
                    "kind": "file",
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "sha256": f"sha256:{sha256_bytes(data)}",
                }
            )
            backup = destination / "files" / relative_text
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(data)
            backup.chmod(0o600)
        elif path.exists() or path.is_symlink():
            raise ValueError(f"migration snapshot path is not a regular file: {relative_text}")
        preimage_state = item["sha256"] if item["kind"] == "file" else "absent"
        item["allowed_rollback_states"] = _rollback_allowed_states(
            plan,
            relative_text,
            preimage_state,
        )
        files.append(item)

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "target": str(target),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "migration_protocol_version": plan.get("migration_protocol_version"),
        "profile": plan.get("profile"),
        "plan_sha256": f"sha256:{migration_plan_digest(plan)}",
        "planned_paths": planned_target_paths(plan),
        "files": files,
        "existing_directories": sorted(existing_directories),
    }
    descriptor_bytes = (
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    receipt = {
        "schema_version": SNAPSHOT_RECEIPT_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "target": str(target),
        "plan_sha256": snapshot["plan_sha256"],
        "descriptor_sha256": f"sha256:{sha256_bytes(descriptor_bytes)}",
        "backup_set_sha256": f"sha256:{canonical_json_sha256(files)}",
    }
    descriptor_path = destination / "snapshot.json"
    receipt_path = destination / "receipt.json"
    descriptor_path.write_bytes(descriptor_bytes)
    descriptor_path.chmod(0o600)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt_path.chmod(0o600)
    return destination


def rollback_migration_snapshot(
    target: Path,
    snapshot_root: Path,
    *,
    trusted_snapshot_root: Path | None = None,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    trusted_base = _trusted_snapshot_base(target, trusted_snapshot_root)
    snapshot_root = _snapshot_path(trusted_base, snapshot_root)
    descriptor_path = snapshot_root / "snapshot.json"
    receipt_path = snapshot_root / "receipt.json"
    files_root = snapshot_root / "files"
    for path, label in (
        (descriptor_path, "migration snapshot descriptor"),
        (receipt_path, "migration snapshot receipt"),
        (files_root, "migration snapshot files root"),
    ):
        _reject_symlink_chain(path, label)
        if path.is_symlink():
            raise ValueError(f"{label} must not be a symlink")
    if not descriptor_path.is_file() or not receipt_path.is_file() or not files_root.is_dir():
        raise ValueError("migration snapshot descriptor, receipt, or files root is missing")
    descriptor_bytes = descriptor_path.read_bytes()
    snapshot = _json_object(descriptor_path, "migration snapshot")
    receipt = _json_object(receipt_path, "migration snapshot receipt")
    if receipt.get("schema_version") != SNAPSHOT_RECEIPT_SCHEMA_VERSION:
        raise ValueError("unsupported migration snapshot receipt schema")
    if receipt.get("descriptor_sha256") != f"sha256:{sha256_bytes(descriptor_bytes)}":
        raise ValueError("migration snapshot descriptor digest mismatch")
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported migration snapshot schema")
    if snapshot.get("target") != str(target):
        raise ValueError("migration snapshot target does not match requested target")
    if snapshot.get("transaction_id") != snapshot_root.name:
        raise ValueError("migration snapshot transaction identity mismatch")
    if any(
        receipt.get(field) != snapshot.get(field)
        for field in ("transaction_id", "target", "plan_sha256")
    ):
        raise ValueError("migration snapshot receipt identity mismatch")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(snapshot.get("plan_sha256"))):
        raise ValueError("migration snapshot plan digest is invalid")

    files = snapshot.get("files")
    if not isinstance(files, list):
        raise ValueError("migration snapshot files must be a list")
    if receipt.get("backup_set_sha256") != f"sha256:{canonical_json_sha256(files)}":
        raise ValueError("migration snapshot backup-set digest mismatch")
    planned_paths = snapshot.get("planned_paths")
    if (
        not isinstance(planned_paths, list)
        or planned_paths != [item.get("path") for item in files if isinstance(item, dict)]
        or len(planned_paths) != len(set(planned_paths))
    ):
        raise ValueError("migration snapshot planned path set is invalid")
    validated: list[tuple[dict[str, Any], Path, bytes | None]] = []
    seen_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("migration snapshot file entry must be an object")
        relative_text = item.get("path")
        if relative_text in seen_paths:
            raise ValueError(f"migration snapshot contains duplicate path: {relative_text}")
        seen_paths.add(relative_text)
        path = _target_path(target, item.get("path"))
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(
                f"rollback path is no longer a regular file: {item.get('path')}"
            )
        current_state = (
            f"sha256:{sha256_file(path)}" if path.is_file() else "absent"
        )
        allowed_states = item.get("allowed_rollback_states")
        if (
            not isinstance(allowed_states, list)
            or not allowed_states
            or any(
                not isinstance(value, str)
                or value != "absent" and not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                for value in allowed_states
            )
            or current_state not in allowed_states
        ):
            raise ValueError(
                f"rollback target changed outside the migration transaction: {item.get('path')}"
            )
        if item.get("kind") == "absent":
            if item.get("mode") is not None or item.get("sha256") is not None:
                raise ValueError(
                    f"absent snapshot metadata is invalid: {item.get('path')}"
                )
            validated.append((item, path, None))
            continue
        if item.get("kind") != "file":
            raise ValueError(f"unsupported snapshot file kind: {item.get('kind')}")
        if (
            not isinstance(item.get("mode"), int)
            or isinstance(item.get("mode"), bool)
            or not 0 <= item["mode"] <= 0o7777
        ):
            raise ValueError(f"migration snapshot mode is invalid: {item.get('path')}")
        backup = files_root / _safe_relative_path(item.get("path"))
        _reject_symlink_chain(backup, "migration snapshot backup")
        if not backup.is_file() or backup.is_symlink():
            raise ValueError(f"migration snapshot backup is missing: {item.get('path')}")
        data = backup.read_bytes()
        if f"sha256:{sha256_bytes(data)}" != item.get("sha256"):
            raise ValueError(f"migration snapshot backup hash mismatch: {item.get('path')}")
        validated.append((item, path, data))

    existing_directories_raw = snapshot.get("existing_directories")
    if not isinstance(existing_directories_raw, list):
        raise ValueError("migration snapshot existing_directories must be a list")
    existing_directories: set[str] = set()
    for relative_text in existing_directories_raw:
        relative = _safe_relative_path(relative_text, "snapshot directory")
        normalized = relative.as_posix()
        if normalized in existing_directories:
            raise ValueError(f"migration snapshot contains duplicate directory: {normalized}")
        existing_directories.add(normalized)

    for item, path, data in validated:
        if path.is_file() or path.is_symlink():
            path.unlink()
        if item.get("kind") == "absent":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data or b"")
        path.chmod(int(item["mode"]))

    candidate_directories: set[Path] = set()
    for item in files:
        path = target / _safe_relative_path(item.get("path"))
        parent = path.parent
        while parent != target:
            candidate_directories.add(parent)
            parent = parent.parent
    for directory in sorted(candidate_directories, key=lambda value: len(value.parts), reverse=True):
        if directory.relative_to(target).as_posix() in existing_directories:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass

    for item in files:
        path = _target_path(target, item.get("path"))
        if item.get("kind") == "absent":
            if path.exists() or path.is_symlink():
                raise ValueError(f"rollback failed to remove created path: {item.get('path')}")
            continue
        if not path.is_file() or f"sha256:{sha256_file(path)}" != item.get("sha256"):
            raise ValueError(f"rollback verification failed: {item.get('path')}")
    return {
        "stage": "harness_migration_rollback",
        "status": "rolled_back",
        "writes": True,
        "target": str(target),
        "snapshot": str(snapshot_root),
        "plan_sha256": snapshot["plan_sha256"],
        "restored_files": [item["path"] for item in files],
        "verification": "passed",
    }
