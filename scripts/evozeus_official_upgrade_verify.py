#!/usr/bin/env python3
"""Verify data-only official Harness upgrade profiles from trusted base code."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol


PROTOCOL_REL = "contracts/v1/migrations/protocols/official-upgrade-protocol-v1.json"
HISTORY_CURRENT_REL = "contracts/v1/migrations/history/harness-skill/current.json"
PROFILES_CURRENT_REL = "contracts/v1/migrations/profiles/current.json"
CONTRACT_MANIFEST_REL = "contracts/v1/manifest.json"
MIGRATION_CONTRACT_REL = "contracts/v1/migrations/harness-migration-contract-v1.json"
LEGACY_ADAPTER_REL = (
    "contracts/v1/migrations/adapters/legacy-v0.14-three-section/adapter-v1.json"
)
LEGACY_ENVELOPE_REL = (
    "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/envelope.json"
)
LEGACY_PROFILE_SCHEMA_REL = (
    "contracts/v1/migrations/schemas/supervised-legacy-profile-v1.schema.json"
)
LEGACY_ADAPTER_SCHEMA_REL = (
    "contracts/v1/migrations/schemas/legacy-prompt-adapter-v1.schema.json"
)
LEGACY_ENVELOPE_SCHEMA_REL = (
    "contracts/v1/migrations/schemas/legacy-source-envelope-v1.schema.json"
)
LEGACY_ADAPTER_IMPLEMENTATION_REL = (
    "scripts/evozeus_harness_legacy_prompt_adapter.py"
)
VERIFIER_REL = "scripts/evozeus_official_upgrade_verify.py"
WORKFLOW_REL = ".github/workflows/evozeus-official-upgrade-profile.yml"
BUNDLE_PREFIX = "contracts/v1/"
PROTECTED_MIGRATION_CONSUMER_PATHS = frozenset(
    {
        LEGACY_ADAPTER_IMPLEMENTATION_REL,
        "scripts/evozeus_harness_migration.py",
        "scripts/evozeus_wrapper.py",
        "scripts/evozeus_wrapper_global_hook.py",
        "scripts/evozeus_wrapper_lifecycle.py",
    }
)
PROTECTED_LEGACY_DATA_PREFIXES = (
    "contracts/v1/migrations/adapters/legacy-v0.14-three-section/",
    "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/",
)
PROTECTED_BASE_PATH_DECLARATIONS = (
    WORKFLOW_REL,
    "contracts/v1/migrations/protocols/",
    "contracts/v1/migrations/schemas/",
    "contracts/v1/migrations/history/*/v*/",
    "contracts/v1/migrations/profiles/*-v*.json",
    *PROTECTED_LEGACY_DATA_PREFIXES,
    VERIFIER_REL,
    *sorted(PROTECTED_MIGRATION_CONSUMER_PATHS),
)
CONSTRUCTION_SOURCE_PREFIXES = ("templates/target/",)
CONSTRUCTION_SOURCE_PATHS = (
    "scripts/evozeus_notice.py",
    "scripts/evozeus_wrapper_preflight.py",
)
AUTHORITY_ROTATION_PREFIXES = (
    ".github/workflows/",
    "contracts/v1/migrations/protocols/",
    "contracts/v1/migrations/schemas/",
)
AUTHORITY_ROTATION_PATHS = (
    VERIFIER_REL,
    *sorted(PROTECTED_MIGRATION_CONSUMER_PATHS),
)
DATA_CANDIDATE_PREFIXES = (
    "contracts/v1/migrations/",
)
DATA_CANDIDATE_PATHS = (MIGRATION_CONTRACT_REL,)
ALLOWED_OPERATION_TYPES = {
    "create_exact",
    "replace_exact",
    "manifest_patch",
    "supervised_transform",
}
ALLOWED_BLOB_MODES = {"100644", "100755"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
GIT_OID_PATTERN = re.compile(r"[0-9a-f]{40}")
SEMVER_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
LEDGER_TARGET_PATTERN = re.compile(
    r"\.evozeus-wrapper/docs/migrations/harness-skill-"
    r"(v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))-to-"
    r"(v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\.md"
)
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_BLOB_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BLOB_BYTES = 32 * 1024 * 1024


class VerificationError(ValueError):
    """A candidate or installed contract cannot be verified safely."""


class BlobStore(Protocol):
    def read_bytes(self, relative: str) -> bytes: ...

    def exists(self, relative: str) -> bool: ...

    def mode(self, relative: str) -> str | None: ...


def _safe_relative(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise VerificationError(f"{label} must be a non-empty POSIX relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != raw:
        raise VerificationError(f"{label} escapes its trusted root: {raw}")
    if any(part in {"", "."} for part in path.parts):
        raise VerificationError(f"{label} is not canonical: {raw}")
    return raw


def _bundle_relative(raw: object, label: str) -> str:
    return BUNDLE_PREFIX + _safe_relative(raw, label)


def _relative_to_document(document: str, raw: object, label: str) -> str:
    relative = _safe_relative(raw, label)
    parent = PurePosixPath(document).parent
    return (parent / relative).as_posix()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise VerificationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _semver(value: object, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise VerificationError(f"{label} must be semantic version text")
    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise VerificationError(f"{label} must use vMAJOR.MINOR.PATCH")
    return tuple(int(item) for item in match.groups())


def _allowed_target_path(relative: str) -> bool:
    """Keep automatic migration operations off target business instruction bytes."""
    if relative.startswith(".evozeus-wrapper/"):
        return True
    if relative == ".codex/hooks.json":
        return True
    if relative.startswith(".github/ISSUE_TEMPLATE/"):
        return True
    if relative == ".github/pull_request_template.md":
        return True
    return (
        relative.startswith(".github/workflows/evozeus-wrapper-")
        and relative.endswith((".yml", ".yaml"))
    )


def _strict_json(value: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise VerificationError(f"{label} contains duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_non_finite(constant: str) -> None:
        raise VerificationError(
            f"{label} contains a non-finite JSON constant: {constant}"
        )

    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_non_finite,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label} is invalid UTF-8 JSON: {exc}") from exc

    def reject_non_finite_numbers(item: object) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise VerificationError(f"{label} contains a non-finite JSON number")
        if isinstance(item, dict):
            for nested in item.values():
                reject_non_finite_numbers(nested)
        elif isinstance(item, list):
            for nested in item:
                reject_non_finite_numbers(nested)

    reject_non_finite_numbers(parsed)
    if not isinstance(parsed, dict):
        raise VerificationError(f"{label} must be a JSON object")
    return parsed


class FilesystemStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    def _file(self, relative: str) -> Path:
        relative = _safe_relative(relative, "repository file")
        cursor = self.root
        for part in PurePosixPath(relative).parts:
            cursor /= part
            try:
                metadata = cursor.lstat()
            except FileNotFoundError as exc:
                raise VerificationError(f"repository file is missing: {relative}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise VerificationError(f"repository file traverses a symlink: {relative}")
        try:
            cursor.relative_to(self.root)
        except ValueError as exc:
            raise VerificationError(f"repository file escapes trusted root: {relative}") from exc
        if not cursor.is_file():
            raise VerificationError(f"repository path is not a regular file: {relative}")
        return cursor

    def read_bytes(self, relative: str) -> bytes:
        return self._file(relative).read_bytes()

    def exists(self, relative: str) -> bool:
        try:
            self._file(relative)
        except VerificationError:
            return False
        return True

    def mode(self, relative: str) -> str | None:
        try:
            metadata = self._file(relative).stat()
        except VerificationError:
            return None
        return "100755" if metadata.st_mode & stat.S_IXUSR else "100644"


@dataclass(frozen=True)
class CandidateBlob:
    path: str
    status: str
    mode: str | None
    object_type: str | None
    oid: str | None
    loader: Callable[[], bytes] | None


@dataclass(frozen=True)
class ConstructionBlob:
    path: str
    mode: str
    data: bytes


@dataclass(frozen=True)
class ConstructionRevisionEvidence:
    repository: str
    revision: str
    head_sha: str
    is_ancestor: bool
    files: dict[str, ConstructionBlob]


ConstructionRevisionResolver = Callable[
    [str, str, str, frozenset[str]], ConstructionRevisionEvidence
]


class CandidateStore:
    """Overlay candidate blobs on a trusted base without checking out candidate code."""

    def __init__(self, base: FilesystemStore, changes: dict[str, CandidateBlob]):
        self.base = base
        self.changes = changes
        self._cache: dict[str, bytes] = {}

    def _candidate_bytes(self, blob: CandidateBlob) -> bytes:
        if blob.status == "deleted" or blob.loader is None:
            raise VerificationError(f"candidate file is absent: {blob.path}")
        if blob.object_type != "blob" or blob.mode not in ALLOWED_BLOB_MODES:
            raise VerificationError(
                f"candidate path is not a regular file: {blob.path} "
                f"({blob.mode}/{blob.object_type})"
            )
        if blob.path not in self._cache:
            data = blob.loader()
            if len(data) > MAX_BLOB_BYTES:
                raise VerificationError(f"candidate blob exceeds size limit: {blob.path}")
            if blob.oid and GIT_OID_PATTERN.fullmatch(blob.oid):
                header = f"blob {len(data)}\0".encode("ascii")
                if hashlib.sha1(header + data).hexdigest() != blob.oid:
                    raise VerificationError(f"candidate Git blob identity mismatch: {blob.path}")
            self._cache[blob.path] = data
            if sum(len(item) for item in self._cache.values()) > MAX_TOTAL_BLOB_BYTES:
                raise VerificationError("candidate contract data exceeds the total size limit")
        return self._cache[blob.path]

    def read_bytes(self, relative: str) -> bytes:
        relative = _safe_relative(relative, "candidate repository file")
        blob = self.changes.get(relative)
        return self._candidate_bytes(blob) if blob is not None else self.base.read_bytes(relative)

    def exists(self, relative: str) -> bool:
        relative = _safe_relative(relative, "candidate repository file")
        blob = self.changes.get(relative)
        if blob is not None:
            return blob.status != "deleted"
        return self.base.exists(relative)

    def mode(self, relative: str) -> str | None:
        relative = _safe_relative(relative, "candidate repository file")
        blob = self.changes.get(relative)
        return blob.mode if blob is not None and blob.status != "deleted" else self.base.mode(relative)


def _json_file(store: BlobStore, relative: str, label: str) -> dict[str, Any]:
    return _strict_json(store.read_bytes(relative), label)


def _binding_path(binding: object, label: str) -> tuple[str, str]:
    if not isinstance(binding, dict):
        raise VerificationError(f"{label} binding must be an object")
    path = _bundle_relative(binding.get("path"), f"{label} path")
    digest = _require_sha256(binding.get("sha256"), f"{label} digest")
    return path, digest


def _verify_binding(store: BlobStore, binding: object, label: str) -> str:
    path, expected = _binding_path(binding, label)
    actual = _sha256(store.read_bytes(path))
    if actual != expected:
        raise VerificationError(
            f"{label} digest mismatch: {path}: expected={expected}; actual={actual}"
        )
    return path


def load_protocol(store: BlobStore) -> dict[str, Any]:
    protocol = _json_file(store, PROTOCOL_REL, "official upgrade protocol")
    if protocol.get("schema_version") != "evozeus.coevolve.official-upgrade-protocol.v1":
        raise VerificationError("official upgrade protocol schema identity is invalid")
    if protocol.get("protocol_id") != "evozeus-official-upgrade":
        raise VerificationError("official upgrade protocol id is invalid")
    _semver(protocol.get("protocol_version"), "official upgrade protocol version")
    operations = protocol.get("allowed_operation_types")
    if not isinstance(operations, list) or set(operations) != ALLOWED_OPERATION_TYPES:
        raise VerificationError("official upgrade protocol operation allowlist is invalid")
    authority = protocol.get("authority")
    if authority != {
        "discovery_is_authority": False,
        "profile_is_data": True,
        "exact_closure_required": True,
        "closure_diff_operation_bijection": True,
    }:
        raise VerificationError("official upgrade protocol authority contract is invalid")
    candidate = protocol.get("candidate_policy")
    if not isinstance(candidate, dict):
        raise VerificationError("official upgrade candidate policy is missing")
    if candidate.get("execution_model") != "trusted_base_verifier_candidate_blobs_as_data":
        raise VerificationError("candidate execution model is unsafe")
    if candidate.get("protected_base_paths") != list(PROTECTED_BASE_PATH_DECLARATIONS):
        raise VerificationError(
            "candidate protected base path declaration disagrees with the verifier"
        )
    if candidate.get("construction_source_allowlist") != {
        "prefixes": list(CONSTRUCTION_SOURCE_PREFIXES),
        "paths": list(CONSTRUCTION_SOURCE_PATHS),
    }:
        raise VerificationError(
            "candidate construction source allowlist disagrees with the verifier"
        )
    if candidate.get("protected_legacy_data_prefixes") != list(
        PROTECTED_LEGACY_DATA_PREFIXES
    ):
        raise VerificationError(
            "candidate protected legacy data declaration disagrees with the verifier"
        )
    if candidate.get("pull_request_classification") != {
        "authority_rotation_prefixes": list(AUTHORITY_ROTATION_PREFIXES),
        "authority_rotation_paths": list(AUTHORITY_ROTATION_PATHS),
        "data_candidate_prefixes": list(DATA_CANDIDATE_PREFIXES),
        "data_candidate_paths": list(DATA_CANDIDATE_PATHS),
    }:
        raise VerificationError(
            "candidate pull request classification disagrees with the verifier"
        )
    if any(candidate.get(field) is not True for field in (
        "reject_symlinks",
        "reject_submodules",
        "reject_unknown_operations",
    )):
        raise VerificationError("candidate fail-closed policy is incomplete")
    target = protocol.get("target_policy")
    if not isinstance(target, dict) or target.get("protected_business_surfaces") != "byte_exact":
        raise VerificationError("target business surface policy is invalid")
    if target.get("rendered_without_receipt") != "preserve_byte_exact":
        raise VerificationError("rendered template preservation policy is invalid")
    if target.get("ledger") != "deterministic_no_date_no_self_reference":
        raise VerificationError("migration ledger determinism policy is invalid")
    if (
        target.get("ledger_history")
        != "one_current_hop_plus_zero_or_more_prior_records"
    ):
        raise VerificationError("migration ledger history policy is invalid")
    if target.get("unknown_or_scattered") != "manual_migration_required_zero_write":
        raise VerificationError("unknown/scattered fallback policy is invalid")
    if (
        target.get("supervised_legacy")
        != "exact_envelope_exact_plan_one_time_approval_no_runtime_apply"
    ):
        raise VerificationError("supervised legacy target policy is invalid")
    return protocol


def _construction_source_allowed(path: str) -> bool:
    return path in CONSTRUCTION_SOURCE_PATHS or path.startswith(
        CONSTRUCTION_SOURCE_PREFIXES
    )


def classify_candidate_changes(
    protocol: dict[str, Any],
    changes: dict[str, CandidateBlob],
) -> str:
    """Classify a full PR diff using policy already trusted on the base branch."""
    candidate_policy = protocol.get("candidate_policy")
    if not isinstance(candidate_policy, dict):
        raise VerificationError("official upgrade candidate policy is missing")
    # load_protocol has already required exact agreement with these constants.
    protected_legacy_prefixes = tuple(
        candidate_policy["protected_legacy_data_prefixes"]
    )
    has_authority = False
    has_data = False
    for path in changes:
        _safe_relative(path, "candidate changed path")
        is_authority = (
            path in AUTHORITY_ROTATION_PATHS
            or path.startswith(AUTHORITY_ROTATION_PREFIXES)
            or path.startswith(protected_legacy_prefixes)
        )
        has_authority = has_authority or is_authority
        # Protocols and schemas live below the migration root but are authority,
        # never candidate data. Every other migration path fails into the strict
        # data verifier, including paths unknown to the current protocol.
        is_data = not is_authority and (
            path in DATA_CANDIDATE_PATHS
            or path.startswith(DATA_CANDIDATE_PREFIXES)
        )
        has_data = has_data or is_data
    if has_authority and has_data:
        raise VerificationError(
            "rotation_required: authority/consumer changes and migration data "
            "must be split into source-first and data-only pull requests"
        )
    if has_authority:
        return "rotation_required"
    if has_data:
        return "data_candidate"
    return "not_applicable"


def load_pointer(store: BlobStore, relative: str, pointer_id: str) -> list[dict[str, str]]:
    pointer = _json_file(store, relative, pointer_id)
    if pointer.get("schema_version") != "evozeus.coevolve.current-pointer.v1":
        raise VerificationError(f"{pointer_id} schema identity is invalid")
    if pointer.get("pointer_id") != pointer_id:
        raise VerificationError(f"{pointer_id} pointer identity is invalid")
    raw_entries = pointer.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise VerificationError(f"{pointer_id} entries are missing")
    entries: list[dict[str, str]] = []
    identities: set[str] = set()
    paths: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != {"id", "version", "path", "sha256"}:
            raise VerificationError(f"{pointer_id} contains an invalid entry")
        identity = raw.get("id")
        if not isinstance(identity, str) or not identity:
            raise VerificationError(f"{pointer_id} entry id is invalid")
        _semver(raw.get("version"), f"{pointer_id} entry version")
        path = _safe_relative(raw.get("path"), f"{pointer_id} entry path")
        digest = _require_sha256(raw.get("sha256"), f"{pointer_id} entry digest")
        if identity in identities or path in paths:
            raise VerificationError(f"{pointer_id} contains duplicate ids or paths")
        identities.add(identity)
        paths.add(path)
        entries.append({"id": identity, "version": raw["version"], "path": path, "sha256": digest})
    return entries


def load_closure(
    store: BlobStore,
    relative: str,
    *,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    relative = _safe_relative(relative, "target closure path")
    if expected_sha256 is not None:
        expected_sha256 = _require_sha256(expected_sha256, "target closure binding")
        actual = _sha256(store.read_bytes(relative))
        if actual != expected_sha256:
            raise VerificationError(
                f"target closure digest mismatch: {relative}: "
                f"expected={expected_sha256}; actual={actual}"
            )
    closure = _json_file(store, relative, f"target closure {relative}")
    if closure.get("schema_version") != "evozeus.coevolve.target-closure.v1":
        raise VerificationError(f"target closure schema identity is invalid: {relative}")
    if closure.get("closure_id") != "using-evozeus-harness":
        raise VerificationError(f"target closure id is invalid: {relative}")
    closure_version = closure.get("closure_version")
    _semver(closure_version, "target closure version")
    source = closure.get("source")
    if not isinstance(source, dict):
        raise VerificationError(f"target closure source identity is missing: {relative}")
    if source.get("repository") != "MetaInFLow/EvoZeus-CoEvolve":
        raise VerificationError(f"target closure source repository is invalid: {relative}")
    revision = source.get("construction_revision")
    if not isinstance(revision, str) or GIT_OID_PATTERN.fullmatch(revision) is None:
        raise VerificationError(f"target closure source revision is invalid: {relative}")
    release_status = source.get("release_status")
    if release_status not in {"unreleased_exact_snapshot", "release_required_for_apply"}:
        raise VerificationError(f"target closure release status is invalid: {relative}")
    required_release = source.get("required_release")
    if required_release is not None:
        _semver(required_release, "target closure required release")
    if release_status == "unreleased_exact_snapshot" and required_release is not None:
        raise VerificationError(
            f"unreleased target closure cannot claim a release: {relative}"
        )
    if release_status == "release_required_for_apply" and required_release is None:
        raise VerificationError(
            f"release-bound target closure lacks its required release: {relative}"
        )
    state = closure.get("state")
    if not isinstance(state, dict):
        raise VerificationError(f"target closure state is missing: {relative}")
    if state.get("layout") != "consolidated-v2":
        raise VerificationError(f"target closure layout is not automatic: {relative}")
    for field in ("target_wrapper_version", "contract_bundle_version", "harness_skill_version"):
        _semver(state.get(field), f"target closure {field}")
    files = closure.get("files")
    if not isinstance(files, list) or not files:
        raise VerificationError(f"target closure files are missing: {relative}")
    by_path: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise VerificationError(f"target closure file entry is invalid: {relative}")
        target_path = _safe_relative(item.get("target_path"), "target closure target path")
        if not _allowed_target_path(target_path):
            raise VerificationError(
                f"target closure attempts to own business or unknown bytes: {target_path}"
            )
        if target_path in by_path:
            raise VerificationError(f"target closure contains duplicate path: {target_path}")
        if item.get("ownership") != "wrapper_managed":
            raise VerificationError(f"target closure path is not wrapper-owned: {target_path}")
        kind = item.get("kind")
        mode = item.get("mode")
        materialization = item.get("materialization")
        if not isinstance(materialization, dict):
            raise VerificationError(f"target closure materialization is missing: {target_path}")
        if kind == "absent":
            if mode != "absent" or materialization != {"policy": "must_be_absent"}:
                raise VerificationError(f"absent target closure state is invalid: {target_path}")
            if any(key in item for key in ("artifact_path", "source_path", "sha256", "owned_state")):
                raise VerificationError(f"absent closure path contains byte state: {target_path}")
        elif kind == "manifest_state":
            if mode != "virtual" or materialization.get("policy") != "manifest_owned_state":
                raise VerificationError(f"target manifest closure is invalid: {target_path}")
            if target_path != ".evozeus-wrapper/wrapper.json":
                raise VerificationError(f"manifest state uses an unauthorized path: {target_path}")
            if not isinstance(item.get("owned_state"), dict):
                raise VerificationError(f"manifest owned state is missing: {target_path}")
            if "artifact_path" in item or "sha256" in item:
                raise VerificationError(f"manifest state cannot contain byte artifacts: {target_path}")
        elif kind in {"exact", "rendered_template"}:
            if mode not in ALLOWED_BLOB_MODES:
                raise VerificationError(f"target closure file mode is invalid: {target_path}")
            artifact_path = _relative_to_document(
                relative,
                item.get("artifact_path"),
                "target closure artifact path",
            )
            expected = _require_sha256(item.get("sha256"), "target closure artifact digest")
            actual = _sha256(store.read_bytes(artifact_path))
            if actual != expected:
                raise VerificationError(
                    f"target closure artifact digest mismatch: {target_path}: "
                    f"expected={expected}; actual={actual}"
                )
            if kind == "exact" and materialization.get("policy") != "copy_exact":
                raise VerificationError(f"exact closure path lacks copy_exact policy: {target_path}")
            if kind == "rendered_template" and (
                materialization.get("policy") != "render_at_fresh_attach"
                or materialization.get("without_receipt") != "preserve_byte_exact"
                or materialization.get("migration_policy")
                != "preserve_byte_exact_no_auto_upgrade"
            ):
                raise VerificationError(
                    f"rendered closure path lacks explicit no-auto-upgrade policy: {target_path}"
                )
            source_path = item.get("source_path")
            source_binding = item.get("source_binding")
            if source_path is not None:
                _safe_relative(source_path, "target closure source path")
                if source_binding not in {"construction_revision", "required_release"}:
                    raise VerificationError(
                        f"closure source binding is invalid: {target_path}"
                    )
            elif materialization.get("generated_release_artifact") is not True:
                raise VerificationError(f"closure artifact lacks source or generated identity: {target_path}")
            if materialization.get("generated_release_artifact") is True:
                if source_binding != "generated_release_artifact":
                    raise VerificationError(
                        f"generated closure artifact binding is invalid: {target_path}"
                    )
                ledger_match = LEDGER_TARGET_PATTERN.fullmatch(target_path)
                if ledger_match is None:
                    raise VerificationError(
                        f"generated migration ledger path is not canonical: {target_path}"
                    )
                record_from, record_to = ledger_match.groups()
                if _semver(record_to, "migration ledger to version") <= _semver(
                    record_from,
                    "migration ledger from version",
                ):
                    raise VerificationError(
                        f"generated migration ledger does not advance: {target_path}"
                    )
                expected_artifact_path = (
                    PurePosixPath(relative).parent
                    / "artifacts/generated"
                    / PurePosixPath(target_path).name
                ).as_posix()
                if artifact_path != expected_artifact_path:
                    raise VerificationError(
                        "generated migration ledger artifact path is not canonical: "
                        f"expected={expected_artifact_path}; actual={artifact_path}"
                    )
                artifact_bytes = store.read_bytes(artifact_path)
                if re.search(rb"\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b", artifact_bytes):
                    raise VerificationError(f"generated migration ledger contains a date: {target_path}")
                if b"plan_sha256" in artifact_bytes or b"closure_sha256" in artifact_bytes:
                    raise VerificationError(f"generated migration ledger is self-referential: {target_path}")
            elif LEDGER_TARGET_PATTERN.fullmatch(target_path) is not None:
                raise VerificationError(
                    f"migration ledger lacks generated release identity: {target_path}"
                )
        else:
            raise VerificationError(f"target closure file kind is invalid: {target_path}")
        by_path[target_path] = item
    if list(by_path) != sorted(by_path):
        raise VerificationError(f"target closure entries are not deterministic: {relative}")
    protected = closure.get("protected_business_surfaces")
    if protected != [{"selector": "manifest.instruction_surface", "rule": "byte_exact"}]:
        raise VerificationError(f"target closure business surface protection is invalid: {relative}")
    return closure, by_path


def _entry_effect(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if key not in {"artifact_path", "source_path"}
    }


def closure_diff(
    from_entries: dict[str, dict[str, Any]],
    to_entries: dict[str, dict[str, Any]],
) -> dict[str, tuple[str, dict[str, Any] | None, dict[str, Any]]]:
    removed = sorted(set(from_entries) - set(to_entries))
    if removed:
        raise VerificationError("automatic target closure cannot delete paths: " + ", ".join(removed))
    changes: dict[str, tuple[str, dict[str, Any] | None, dict[str, Any]]] = {}
    for path in sorted(to_entries):
        before = from_entries.get(path)
        after = to_entries[path]
        if before is not None and _entry_effect(before) == _entry_effect(after):
            continue
        if before is None or before.get("kind") == "absent":
            if after.get("kind") != "exact":
                raise VerificationError(f"automatic closure can only create exact files: {path}")
            operation = "create_exact"
        elif before.get("kind") == after.get("kind") == "exact":
            operation = "replace_exact"
        elif before.get("kind") == after.get("kind") == "rendered_template":
            raise VerificationError(
                f"automatic closure cannot change a rendered surface under protocol v1: {path}"
            )
        elif before.get("kind") == after.get("kind") == "manifest_state":
            operation = "manifest_patch"
        else:
            raise VerificationError(f"automatic closure kind transition is unsupported: {path}")
        changes[path] = (operation, before, after)
    return changes


def _operation_artifact_path(profile_path: str, value: object, label: str) -> str:
    # Profile artifact paths are contract-bundle relative, unlike closure-local paths.
    return _bundle_relative(value, label)


def _verify_exact_operation(
    store: BlobStore,
    operation: dict[str, Any],
    expected_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    if expected_type == "create_exact":
        if operation.get("preimage") != {"state": "absent"}:
            raise VerificationError(f"create_exact lacks absent preimage: {operation['target_path']}")
    else:
        preimage = operation.get("preimage")
        if not isinstance(preimage, dict) or before is None:
            raise VerificationError(f"replace_exact preimage is missing: {operation['target_path']}")
        if preimage != {"sha256": before.get("sha256"), "mode": before.get("mode")}:
            raise VerificationError(f"replace_exact preimage disagrees with closure: {operation['target_path']}")
    postimage = operation.get("postimage")
    if not isinstance(postimage, dict):
        raise VerificationError(f"exact operation postimage is missing: {operation['target_path']}")
    if postimage.get("sha256") != after.get("sha256") or postimage.get("mode") != after.get("mode"):
        raise VerificationError(f"exact operation postimage disagrees with closure: {operation['target_path']}")
    artifact_path = _operation_artifact_path(
        "",
        postimage.get("artifact_path"),
        "profile postimage artifact path",
    )
    actual = _sha256(store.read_bytes(artifact_path))
    if actual != postimage.get("sha256"):
        raise VerificationError(f"profile postimage artifact digest mismatch: {operation['target_path']}")


def _state_preconditions_match(state: dict[str, Any], preconditions: object) -> bool:
    if not isinstance(preconditions, dict):
        return False
    for field, expected in preconditions.items():
        if expected == {"state": "absent"}:
            if field in state:
                return False
        elif state.get(field) != expected:
            return False
    return True


def _verify_manifest_patch(
    operation: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    if operation.get("encoding") != "utf-8-json-indent-2-lf":
        raise VerificationError("manifest_patch encoding is not deterministic")
    if operation.get("preserve_unlisted_fields") is not True:
        raise VerificationError("manifest_patch must preserve unlisted fields")
    before_state = before.get("owned_state")
    after_state = after.get("owned_state")
    if not isinstance(before_state, dict) or not isinstance(after_state, dict):
        raise VerificationError("manifest closure state is missing")
    if not _state_preconditions_match(before_state, operation.get("preconditions")):
        raise VerificationError("manifest_patch preconditions disagree with the from closure")
    result = json.loads(json.dumps(before_state))
    patch = operation.get("patch")
    if not isinstance(patch, list) or not patch:
        raise VerificationError("manifest_patch patch list is missing")
    touched: set[str] = set()
    for item in patch:
        if not isinstance(item, dict):
            raise VerificationError("manifest_patch action is invalid")
        action = item.get("action")
        field = item.get("field")
        if not isinstance(field, str) or not field or field in touched:
            raise VerificationError("manifest_patch fields must be unique")
        touched.add(field)
        if action == "replace":
            if field not in result:
                raise VerificationError(f"manifest_patch replace field is absent: {field}")
            result[field] = item.get("value")
        elif action == "add":
            if field in result:
                raise VerificationError(f"manifest_patch add field already exists: {field}")
            result[field] = item.get("value")
        elif action == "add_managed_block":
            if field in result or item.get("path_from") != "instruction_surface":
                raise VerificationError("manifest managed block patch is invalid")
            value = item.get("value")
            if not isinstance(value, dict):
                raise VerificationError("manifest managed block value is invalid")
            result[field] = [{**value, "path_selector": "manifest.instruction_surface"}]
        elif action == "append_unique":
            if field != "managed_files":
                raise VerificationError("append_unique is only allowed for managed_files")
            values = item.get("values")
            if not isinstance(values, list) or not values or any(
                not isinstance(value, str) for value in values
            ) or len(values) != len(set(values)):
                raise VerificationError("managed_files append set is invalid")
            result["managed_files_require"] = values
        else:
            raise VerificationError(f"manifest_patch contains unknown action: {action}")
    if result != after_state:
        raise VerificationError("manifest_patch effect does not equal the target closure state")


def _verified_profile_migration_records(
    operations: list[dict[str, Any]],
    from_closure: dict[str, Any],
    to_closure: dict[str, Any],
    to_entries: dict[str, dict[str, Any]],
) -> list[str]:
    from_version = from_closure.get("closure_version")
    current_version = to_closure.get("closure_version")
    _semver(from_version, "profile migration record from closure version")
    _semver(current_version, "profile migration record current closure version")

    parsed_records: list[tuple[tuple[int, int, int], tuple[int, int, int], str, str, str]] = []
    for operation in operations:
        target_path = operation["target_path"]
        match = LEDGER_TARGET_PATTERN.fullmatch(target_path)
        if match is None:
            if "/docs/migrations/harness-skill-" in target_path:
                raise VerificationError(
                    f"profile migration ledger path is not canonical: {target_path}"
                )
            continue
        if operation.get("type") != "create_exact":
            raise VerificationError(
                f"profile migration ledger must use create_exact: {target_path}"
            )
        closure_entry = to_entries.get(target_path)
        materialization = (
            closure_entry.get("materialization")
            if isinstance(closure_entry, dict)
            else None
        )
        if (
            not isinstance(closure_entry, dict)
            or closure_entry.get("kind") != "exact"
            or not isinstance(materialization, dict)
            or materialization.get("generated_release_artifact") is not True
            or closure_entry.get("source_binding") != "generated_release_artifact"
        ):
            raise VerificationError(
                f"profile migration ledger is not closure-bound generated data: {target_path}"
            )
        record_from, record_to = match.groups()
        from_semver = _semver(
            record_from,
            "profile migration record from version",
        )
        to_semver = _semver(record_to, "profile migration record to version")
        if to_semver <= from_semver:
            raise VerificationError(
                f"profile migration record does not advance: {target_path}"
            )
        parsed_records.append(
            (from_semver, to_semver, target_path, record_from, record_to)
        )
    if not parsed_records:
        raise VerificationError(
            "official upgrade profile must contain at least one generated migration record"
        )
    parsed_records.sort(key=lambda item: (item[0], item[1], item[2]))
    records = [item[2] for item in parsed_records]
    if len(records) != len(set(records)):
        raise VerificationError("official upgrade profile migration records are duplicated")
    expected_from = from_version
    for _, _, _, record_from, record_to in parsed_records:
        if record_from != expected_from:
            raise VerificationError(
                "profile migration records are not one contiguous cumulative chain: "
                f"expected_from={expected_from}; actual_from={record_from}"
            )
        expected_from = record_to
    if expected_from != current_version:
        raise VerificationError(
            "profile migration record chain does not end at the current closure version: "
            f"expected={current_version}; actual={expected_from}"
        )
    return records


def load_profile(
    store: BlobStore,
    relative: str,
    protocol: dict[str, Any],
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    relative = _safe_relative(relative, "official upgrade profile path")
    if expected_sha256 is not None:
        actual = _sha256(store.read_bytes(relative))
        if actual != _require_sha256(expected_sha256, "official upgrade profile binding"):
            raise VerificationError(f"official upgrade profile digest mismatch: {relative}")
    profile = _json_file(store, relative, f"official upgrade profile {relative}")
    if profile.get("schema_version") != "evozeus.coevolve.official-upgrade-profile.v1":
        raise VerificationError(f"official upgrade profile schema is invalid: {relative}")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise VerificationError(f"official upgrade profile id is invalid: {relative}")
    _semver(profile.get("profile_version"), "official upgrade profile version")
    protocol_path = _verify_binding(store, profile.get("protocol"), "profile protocol")
    if protocol_path != PROTOCOL_REL:
        raise VerificationError("official upgrade profile selects an unknown protocol")
    from_path = _verify_binding(store, profile.get("from_closure"), "from closure")
    to_path = _verify_binding(store, profile.get("to_closure"), "to closure")
    from_closure, from_entries = load_closure(
        store,
        from_path,
        expected_sha256=profile["from_closure"]["sha256"],
    )
    to_closure, to_entries = load_closure(
        store,
        to_path,
        expected_sha256=profile["to_closure"]["sha256"],
    )
    release_axis = profile.get("release_axis")
    if not isinstance(release_axis, dict):
        raise VerificationError("official upgrade profile release axis is missing")
    for field in ("target_wrapper_from", "target_wrapper_to"):
        _semver(release_axis.get(field), f"official upgrade profile {field}")
    if release_axis.get("target_wrapper_from") != from_closure["state"].get(
        "target_wrapper_version"
    ) or release_axis.get("target_wrapper_to") != to_closure["state"].get(
        "target_wrapper_version"
    ):
        raise VerificationError("official upgrade profile target release axes disagree")
    from_source = from_closure["source"]
    to_source = to_closure["source"]
    expected_artifact_source_from = {
        "kind": "construction_revision",
        "revision": from_source.get("construction_revision"),
        "release": from_source.get("required_release"),
    }
    expected_artifact_source_to = {
        "kind": "required_release",
        "release": to_source.get("required_release"),
        "binding": "contract_bundle.source_revision",
    }
    if release_axis.get("artifact_source_from") != expected_artifact_source_from:
        raise VerificationError(
            "official upgrade profile from-artifact provenance is not closure-bound"
        )
    if release_axis.get("artifact_source_to") != expected_artifact_source_to:
        raise VerificationError(
            "official upgrade profile to-artifact provenance is not closure-bound"
        )
    if _semver(to_closure.get("closure_version"), "to closure version") <= _semver(
        from_closure.get("closure_version"), "from closure version"
    ):
        raise VerificationError("official upgrade profile does not advance the closure version")
    if profile.get("automatic") is not True:
        raise VerificationError("current official upgrade profile must be automatic")
    changes = closure_diff(from_entries, to_entries)
    operations = profile.get("operations")
    if not isinstance(operations, list) or not operations:
        raise VerificationError("official upgrade profile operations are missing")
    operation_paths: set[str] = set()
    operation_ids: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise VerificationError("official upgrade profile operation is invalid")
        operation_type = operation.get("type")
        if operation_type not in ALLOWED_OPERATION_TYPES:
            raise VerificationError(f"official upgrade profile contains unknown operation: {operation_type}")
        target_path = _safe_relative(operation.get("target_path"), "profile operation target")
        change_id = operation.get("change_id")
        if not isinstance(change_id, str) or not change_id:
            raise VerificationError("official upgrade operation change_id is invalid")
        if target_path in operation_paths or change_id in operation_ids:
            raise VerificationError("official upgrade profile operations are not one-to-one")
        operation_paths.add(target_path)
        operation_ids.add(change_id)
        if target_path not in changes:
            raise VerificationError(f"profile operation has no closure diff: {target_path}")
        expected_type, before, after = changes[target_path]
        if operation_type != expected_type:
            raise VerificationError(
                f"profile operation disagrees with closure diff: {target_path}: "
                f"expected={expected_type}; actual={operation_type}"
            )
        expected_change_id = (
            ("create" if expected_type == "create_exact" else "replace") + ":" + target_path
            if expected_type in {"create_exact", "replace_exact"}
            else "manifest:" + target_path
        )
        if change_id != expected_change_id:
            raise VerificationError(f"profile change_id is not canonical: {target_path}")
        if expected_type in {"create_exact", "replace_exact"}:
            _verify_exact_operation(store, operation, expected_type, before, after)
        elif expected_type == "manifest_patch":
            if before is None:
                raise VerificationError("manifest_patch cannot create a manifest")
            _verify_manifest_patch(operation, before, after)
    if operation_paths != set(changes):
        missing = sorted(set(changes) - operation_paths)
        raise VerificationError("closure diff lacks profile operations: " + ", ".join(missing))
    migration_records = _verified_profile_migration_records(
        operations,
        from_closure,
        to_closure,
        to_entries,
    )
    rendered_unchanged = {
        path
        for path, item in from_entries.items()
        if item.get("kind") == "rendered_template"
        and path in to_entries
        and _entry_effect(item) == _entry_effect(to_entries[path])
    }
    deferred = profile.get("deferred_rendered_surfaces")
    if not isinstance(deferred, list):
        raise VerificationError("profile deferred rendered surfaces are missing")
    deferred_paths: set[str] = set()
    for item in deferred:
        if not isinstance(item, dict):
            raise VerificationError("deferred rendered surface is invalid")
        path = _safe_relative(item.get("target_path"), "deferred rendered surface")
        if item.get("policy") != "preserve_byte_exact_no_auto_upgrade":
            raise VerificationError(f"rendered surface policy is unsafe: {path}")
        if item.get("version_fact_source") != ".evozeus-wrapper/wrapper.json":
            raise VerificationError(f"rendered surface version source is invalid: {path}")
        if path in deferred_paths:
            raise VerificationError(f"deferred rendered surface is duplicated: {path}")
        deferred_paths.add(path)
    if deferred_paths != rendered_unchanged:
        raise VerificationError(
            "profile must enumerate every unchanged rendered surface excluded from auto-upgrade"
        )
    if profile.get("protected_business_surfaces") != [
        {"selector": "manifest.instruction_surface", "rule": "byte_exact"}
    ]:
        raise VerificationError("profile business surface protection is invalid")
    if profile.get("fallback") != {
        "unknown_layout": "manual_migration_required_zero_write",
        "scattered_layout": "manual_migration_required_zero_write",
        "missing_evidence": "manual_migration_required_zero_write",
    }:
        raise VerificationError("profile manual zero-write fallback is invalid")
    profile["_verified_from_path"] = from_path
    profile["_verified_to_path"] = to_path
    profile["_verified_from_closure"] = from_closure
    profile["_verified_to_closure"] = to_closure
    profile["migration_records"] = migration_records
    profile["current_migration_record"] = migration_records[-1]
    return profile


def _manifest_bound_bundle_binding(
    store: BlobStore,
    manifest_files: dict[str, dict[str, str]],
    binding: object,
    label: str,
    role: str,
) -> str:
    path, digest = _binding_path(binding, label)
    relative = path.removeprefix(BUNDLE_PREFIX)
    manifest_item = manifest_files.get(relative)
    if manifest_item != {"sha256": digest, "role": role}:
        raise VerificationError(
            f"{label} is not exactly bound by the contract manifest: {relative}"
        )
    actual = _sha256(store.read_bytes(path))
    if actual != digest:
        raise VerificationError(
            f"{label} digest mismatch: {path}: expected={digest}; actual={actual}"
        )
    return path


def _manifest_bound_repository_file(
    store: BlobStore,
    repository_files: dict[str, dict[str, str]],
    binding: object,
    label: str,
    role: str,
) -> str:
    if not isinstance(binding, dict) or set(binding) != {
        "root",
        "path",
        "sha256",
        "entrypoint",
    }:
        raise VerificationError(f"{label} binding is invalid")
    if binding.get("root") != "repository_path":
        raise VerificationError(f"{label} root is invalid")
    path = _safe_relative(binding.get("path"), f"{label} path")
    digest = _require_sha256(binding.get("sha256"), f"{label} digest")
    manifest_item = repository_files.get(path)
    if manifest_item != {"sha256": digest, "role": role}:
        raise VerificationError(
            f"{label} is not exactly bound by the contract manifest: {path}"
        )
    actual = _sha256(store.read_bytes(path))
    if actual != digest:
        raise VerificationError(
            f"{label} digest mismatch: {path}: expected={digest}; actual={actual}"
        )
    return path


def _verify_bound_schema(
    store: BlobStore,
    manifest_files: dict[str, dict[str, str]],
    binding: object,
    *,
    expected_path: str,
    expected_schema_version: str,
    label: str,
) -> str:
    path = _manifest_bound_bundle_binding(
        store,
        manifest_files,
        binding,
        label,
        "official-upgrade-json-schema",
    )
    if path != expected_path:
        raise VerificationError(f"{label} path is not canonical")
    schema = _json_file(store, path, label)
    schema_identity = (
        schema.get("properties", {}).get("schema_version", {}).get("const")
        if isinstance(schema.get("properties"), dict)
        else None
    )
    if (
        schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
        or schema_identity != expected_schema_version
        or schema.get("additionalProperties") is not False
    ):
        raise VerificationError(f"{label} published contract is invalid")
    return path


def _legacy_envelope_entries(
    envelope: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_files = envelope.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise VerificationError("reviewed legacy source envelope files are missing")
    entries: dict[str, dict[str, Any]] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            raise VerificationError("reviewed legacy source envelope file is invalid")
        path = _safe_relative(item.get("path"), "reviewed legacy envelope path")
        if path in entries:
            raise VerificationError(
                f"reviewed legacy source envelope repeats a path: {path}"
            )
        kind = item.get("kind")
        if kind == "exact":
            _require_sha256(item.get("sha256"), f"legacy exact file digest: {path}")
            if item.get("mode") not in ALLOWED_BLOB_MODES:
                raise VerificationError(f"legacy exact file mode is invalid: {path}")
        elif kind == "rendered_preserve":
            if item.get("mode") not in ALLOWED_BLOB_MODES or "sha256" in item:
                raise VerificationError(
                    f"legacy rendered-preserve file is invalid: {path}"
                )
        elif kind == "absent":
            if "sha256" in item or "mode" in item:
                raise VerificationError(f"legacy absent file has byte state: {path}")
        else:
            raise VerificationError(f"legacy source envelope kind is invalid: {path}")
        entries[path] = item
    return entries


def _derive_legacy_static_write_set(
    envelope: dict[str, Any],
    legacy_entries: dict[str, dict[str, Any]],
    target_entries: dict[str, dict[str, Any]],
) -> tuple[dict[str, tuple[str, dict[str, Any] | None, dict[str, Any] | None]], set[str]]:
    changes: dict[
        str,
        tuple[str, dict[str, Any] | None, dict[str, Any] | None],
    ] = {}
    rendered_preserve: set[str] = set()
    for path, after in target_entries.items():
        kind = after.get("kind")
        before = legacy_entries.get(path)
        if kind == "manifest_state":
            projection = envelope.get("manifest_projection")
            if not isinstance(projection, dict) or projection.get("path") != path:
                raise VerificationError(
                    "legacy manifest projection does not select the target closure manifest"
                )
            changes[path] = ("manifest_patch", None, after)
            continue
        if kind == "rendered_template":
            if before is None or before.get("kind") != "rendered_preserve":
                raise VerificationError(
                    f"legacy rendered surface lacks preserve evidence: {path}"
                )
            if before.get("mode") != after.get("mode"):
                raise VerificationError(
                    f"legacy rendered surface mode differs from target closure: {path}"
                )
            rendered_preserve.add(path)
            continue
        if kind != "exact" or before is None:
            raise VerificationError(
                f"legacy envelope cannot derive target closure path: {path}"
            )
        before_kind = before.get("kind")
        if before_kind == "absent":
            changes[path] = ("create_exact", None, after)
        elif before_kind == "exact":
            if before.get("mode") == after.get("mode") and before.get(
                "sha256"
            ) == after.get("sha256"):
                continue
            changes[path] = ("replace_exact", before, after)
        else:
            raise VerificationError(
                f"legacy envelope target transition is unsupported: {path}"
            )
    for path, before in legacy_entries.items():
        if before.get("kind") in {"exact", "rendered_preserve"} and path not in target_entries:
            raise VerificationError(
                f"current closure drops a reviewed legacy file: {path}"
            )
    instruction = envelope.get("instruction_surface")
    if not isinstance(instruction, dict):
        raise VerificationError("legacy instruction surface contract is missing")
    instruction_path = _safe_relative(
        instruction.get("path"), "legacy instruction surface path"
    )
    if instruction_path in changes or instruction_path in target_entries:
        raise VerificationError("legacy instruction surface collides with closure ownership")
    changes[instruction_path] = ("supervised_transform", None, None)
    return changes, rendered_preserve


def _verify_legacy_manifest_operation(
    operation: dict[str, Any],
    envelope_binding: dict[str, Any],
    envelope: dict[str, Any],
    target_closure_path: str,
    target_manifest: dict[str, Any],
) -> None:
    if (
        operation.get("encoding") != "utf-8-json-indent-2-lf"
        or operation.get("preserve_unlisted_fields") is not True
    ):
        raise VerificationError("legacy manifest patch encoding/preservation is invalid")
    projection = envelope.get("manifest_projection")
    instruction = envelope.get("instruction_surface")
    if not isinstance(projection, dict) or not isinstance(instruction, dict):
        raise VerificationError("legacy manifest/instruction projection is missing")
    expected_source = {
        "envelope_path": envelope_binding["path"],
        "manifest_path": projection["path"],
        "projection": "frozen-template-byte-exact",
    }
    if operation.get("source_projection") != expected_source:
        raise VerificationError("legacy manifest source projection is not envelope-bound")
    owned_state = target_manifest.get("owned_state")
    if not isinstance(owned_state, dict):
        raise VerificationError("target closure manifest state is missing")
    managed_blocks = owned_state.get("managed_blocks")
    if not isinstance(managed_blocks, list) or len(managed_blocks) != 1:
        raise VerificationError("target closure managed block state is invalid")
    block = dict(managed_blocks[0])
    if block.pop("path_selector", None) != "manifest.instruction_surface":
        raise VerificationError("target closure managed block selector is invalid")
    expected_patch = [
        {
            "action": "replace",
            "field": "wrapper_version",
            "value": owned_state["wrapper_version"],
        },
        {
            "action": "add",
            "field": "instruction_surface",
            "value": instruction["path"],
        },
        {
            "action": "add",
            "field": "harness_skill_path",
            "value": owned_state["harness_skill_path"],
        },
        {
            "action": "add",
            "field": "harness_skill_version",
            "value": owned_state["harness_skill_version"],
        },
        {
            "action": "add",
            "field": "harness_skill_managed",
            "value": owned_state["harness_skill_managed"],
        },
        {
            "action": "add",
            "field": "migration_contract",
            "value": owned_state["migration_contract"],
        },
        {
            "action": "add_managed_block",
            "field": "managed_blocks",
            "path_from": "instruction_surface",
            "value": block,
        },
        {
            "action": "append_unique",
            "field": "managed_files",
            "values": owned_state["managed_files_require"],
        },
    ]
    if operation.get("patch") != expected_patch:
        raise VerificationError("legacy manifest patch is not the exact target projection")
    expected_target = {
        "closure_path": target_closure_path.removeprefix(BUNDLE_PREFIX),
        "closure_target_path": ".evozeus-wrapper/wrapper.json",
        "additional_exact": {"instruction_surface": instruction["path"]},
    }
    if operation.get("target_projection") != expected_target:
        raise VerificationError("legacy manifest target projection is not closure-bound")


def load_supervised_legacy_profile(
    store: BlobStore,
    contract_binding: dict[str, Any],
    protocol: dict[str, Any],
    manifest_files: dict[str, dict[str, str]],
    repository_files: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(contract_binding, dict):
        raise VerificationError("reviewed legacy migration binding is invalid")
    expected_contract_keys = {
        "profile_id",
        "profile_version",
        "profile_path",
        "profile_schema",
        "source_envelope",
        "adapter",
        "target_closure_pointer",
        "release_lineage",
        "execution",
    }
    if set(contract_binding) != expected_contract_keys:
        raise VerificationError("reviewed legacy migration binding fields are invalid")
    profile_id = contract_binding.get("profile_id")
    profile_version = contract_binding.get("profile_version")
    if not isinstance(profile_id, str) or not profile_id:
        raise VerificationError("reviewed legacy profile id is invalid")
    major, _, _ = _semver(profile_version, "reviewed legacy profile version")
    profile_relative = _safe_relative(
        contract_binding.get("profile_path"), "reviewed legacy profile path"
    )
    expected_profile_relative = f"migrations/profiles/{profile_id}-v{major}.json"
    if profile_relative != expected_profile_relative:
        raise VerificationError("reviewed legacy profile path is not canonical")
    profile_manifest_item = manifest_files.get(profile_relative)
    if (
        profile_manifest_item is None
        or profile_manifest_item.get("role") != "supervised-legacy-profile"
    ):
        raise VerificationError("reviewed legacy profile is not manifest-bound")
    profile_path = BUNDLE_PREFIX + profile_relative
    profile_bytes = store.read_bytes(profile_path)
    if _sha256(profile_bytes) != profile_manifest_item["sha256"]:
        raise VerificationError("reviewed legacy profile manifest digest mismatch")
    profile = _strict_json(profile_bytes, f"reviewed legacy profile {profile_path}")
    if (
        profile.get("schema_version")
        != "evozeus.coevolve.supervised-legacy-profile.v1"
        or profile.get("profile_id") != profile_id
        or profile.get("profile_version") != profile_version
    ):
        raise VerificationError("reviewed legacy profile identity is invalid")

    profile_schema_path = _verify_bound_schema(
        store,
        manifest_files,
        contract_binding.get("profile_schema"),
        expected_path=LEGACY_PROFILE_SCHEMA_REL,
        expected_schema_version="evozeus.coevolve.supervised-legacy-profile.v1",
        label="supervised legacy profile schema",
    )
    if profile.get("profile_schema") != contract_binding.get("profile_schema"):
        raise VerificationError("reviewed legacy profile schema binding differs from contract")
    if _verify_binding(store, profile.get("profile_schema"), "legacy profile schema") != profile_schema_path:
        raise VerificationError("reviewed legacy profile schema path is invalid")
    protocol_path = _verify_binding(store, profile.get("protocol"), "legacy profile protocol")
    if protocol_path != PROTOCOL_REL or profile["protocol"]["sha256"] != _sha256(
        store.read_bytes(PROTOCOL_REL)
    ):
        raise VerificationError("reviewed legacy profile protocol binding is invalid")

    source_binding = contract_binding.get("source_envelope")
    if not isinstance(source_binding, dict) or set(source_binding) != {
        "path",
        "sha256",
        "schema",
    }:
        raise VerificationError("reviewed legacy source binding is invalid")
    envelope_path = _manifest_bound_bundle_binding(
        store,
        manifest_files,
        {"path": source_binding["path"], "sha256": source_binding["sha256"]},
        "reviewed legacy source envelope",
        "trusted-legacy-source-envelope",
    )
    if envelope_path != LEGACY_ENVELOPE_REL:
        raise VerificationError("reviewed legacy source envelope path is not canonical")
    _verify_bound_schema(
        store,
        manifest_files,
        source_binding["schema"],
        expected_path=LEGACY_ENVELOPE_SCHEMA_REL,
        expected_schema_version="evozeus.coevolve.legacy-source-envelope.v1",
        label="reviewed legacy source envelope schema",
    )
    envelope = _json_file(store, envelope_path, "reviewed legacy source envelope")
    if (
        envelope.get("schema_version")
        != "evozeus.coevolve.legacy-source-envelope.v1"
        or envelope.get("envelope_id") != "legacy-wrapper-v0.14-three-section"
        or envelope.get("envelope_version") != "v1.0.0"
    ):
        raise VerificationError("reviewed legacy source envelope identity is invalid")
    evidence = envelope.get("source_evidence")
    lineage = contract_binding.get("release_lineage")
    if not isinstance(evidence, dict) or not isinstance(lineage, dict):
        raise VerificationError("reviewed legacy source lineage is missing")
    if (
        evidence.get("repository") != lineage.get("source_repository")
        or evidence.get("commit") != lineage.get("source_commit")
        or evidence.get("tree") != lineage.get("source_tree")
    ):
        raise VerificationError("reviewed legacy source evidence differs from release lineage")
    if (
        evidence.get("repository_url")
        != "https://github.com/MetaInFLow/diagnose-enterprise-ai-scenarios"
        or evidence.get("instruction_surface_sha256")
        != "22b519a18fa4ec9b5ed1a892cd1895c1b68b84366c1286af8f8a403f35d79a04"
        or evidence.get("manifest_sha256")
        != "c05dbb63db5deb391a13e7093948324e6018f7c6bcb318c537b936dd1e173b52"
        or evidence.get("legacy_preflight_sha256")
        != "0ef6e008461dc8e61845ad6deae5fe239122c2415d81550a1e9d6e9838570aa1"
    ):
        raise VerificationError("reviewed legacy source evidence is not the audited fixture")
    legacy_entries = _legacy_envelope_entries(envelope)
    host_entrypoints = envelope.get("host_entrypoints_must_match")
    if not isinstance(host_entrypoints, list) or not host_entrypoints:
        raise VerificationError("reviewed legacy host entrypoint evidence is missing")
    if any(
        path not in legacy_entries or legacy_entries[path].get("kind") != "exact"
        for path in host_entrypoints
    ):
        raise VerificationError("reviewed legacy host entrypoint is not exact")
    projection = envelope.get("manifest_projection")
    if not isinstance(projection, dict):
        raise VerificationError("reviewed legacy manifest projection is missing")
    manifest_template_path = _safe_relative(
        projection.get("template_path"), "reviewed legacy manifest template"
    )
    template_manifest_item = manifest_files.get(
        manifest_template_path.removeprefix(BUNDLE_PREFIX)
    )
    if (
        not manifest_template_path.startswith(BUNDLE_PREFIX)
        or template_manifest_item
        != {
            "sha256": projection.get("template_sha256"),
            "role": "trusted-legacy-source-artifact",
        }
        or _sha256(store.read_bytes(manifest_template_path))
        != projection.get("template_sha256")
    ):
        raise VerificationError("reviewed legacy manifest template is not exactly bound")

    adapter_binding = contract_binding.get("adapter")
    if not isinstance(adapter_binding, dict) or set(adapter_binding) != {
        "path",
        "sha256",
        "schema",
        "implementation",
    }:
        raise VerificationError("reviewed legacy adapter binding is invalid")
    adapter_path = _manifest_bound_bundle_binding(
        store,
        manifest_files,
        {"path": adapter_binding["path"], "sha256": adapter_binding["sha256"]},
        "reviewed legacy adapter",
        "trusted-legacy-adapter",
    )
    if adapter_path != LEGACY_ADAPTER_REL:
        raise VerificationError("reviewed legacy adapter path is not canonical")
    _verify_bound_schema(
        store,
        manifest_files,
        adapter_binding["schema"],
        expected_path=LEGACY_ADAPTER_SCHEMA_REL,
        expected_schema_version="evozeus.coevolve.legacy-prompt-adapter.v1",
        label="reviewed legacy adapter schema",
    )
    implementation_path = _manifest_bound_repository_file(
        store,
        repository_files,
        adapter_binding["implementation"],
        "reviewed legacy adapter implementation",
        "trusted-legacy-adapter-implementation",
    )
    if implementation_path != LEGACY_ADAPTER_IMPLEMENTATION_REL:
        raise VerificationError("reviewed legacy adapter implementation path is not canonical")
    adapter_document = _json_file(store, adapter_path, "reviewed legacy adapter")
    if (
        adapter_document.get("schema_version")
        != "evozeus.coevolve.legacy-prompt-adapter.v1"
        or adapter_document.get("adapter_id")
        != "legacy-v0.14-three-section-exact-transform"
        or adapter_document.get("adapter_version") != "v1.0.0"
    ):
        raise VerificationError("reviewed legacy adapter identity is invalid")
    internal_envelope = adapter_document.get("source_envelope")
    if internal_envelope != {
        "path": envelope_path,
        "sha256": source_binding["sha256"],
    }:
        raise VerificationError("reviewed legacy adapter source binding is invalid")
    implementation = adapter_document.get("implementation")
    expected_implementation = dict(adapter_binding["implementation"])
    expected_implementation.pop("root")
    if implementation != expected_implementation:
        raise VerificationError("reviewed legacy adapter implementation binding differs")
    template_kinds: list[str] = []
    for template in adapter_document.get("templates", []):
        if not isinstance(template, dict):
            raise VerificationError("reviewed legacy adapter template is invalid")
        kind = template.get("kind")
        path = _safe_relative(template.get("path"), "legacy adapter template path")
        digest = _require_sha256(template.get("sha256"), "legacy adapter template digest")
        manifest_item = manifest_files.get(path.removeprefix(BUNDLE_PREFIX))
        if (
            not path.startswith(BUNDLE_PREFIX)
            or manifest_item
            != {"sha256": digest, "role": "trusted-legacy-adapter-template"}
            or _sha256(store.read_bytes(path)) != digest
        ):
            raise VerificationError(f"reviewed legacy adapter template is unbound: {path}")
        template_kinds.append(kind)
    if template_kinds != ["status", "evolution", "wrapper"]:
        raise VerificationError("reviewed legacy adapter template order is invalid")
    activation = adapter_document.get("canonical_activation_block_lf")
    if (
        not isinstance(activation, str)
        or _sha256(activation.encode("utf-8"))
        != adapter_document.get("canonical_activation_sha256_lf")
    ):
        raise VerificationError("reviewed legacy adapter activation block is invalid")
    if profile.get("source_envelope") != {
        "path": source_binding["path"],
        "sha256": source_binding["sha256"],
    } or profile.get("adapter") != {
        "path": adapter_binding["path"],
        "sha256": adapter_binding["sha256"],
    }:
        raise VerificationError("reviewed legacy profile data bindings differ from contract")

    target_pointer = contract_binding.get("target_closure_pointer")
    profile_pointer = profile.get("target_closure_pointer")
    if not isinstance(target_pointer, dict) or not isinstance(profile_pointer, dict):
        raise VerificationError("reviewed legacy target closure pointer is missing")
    expected_profile_pointer = {
        "path": target_pointer.get("path"),
        "pointer_id": target_pointer.get("pointer_id"),
        "selected": {
            "id": target_pointer.get("selected_id"),
            "version": target_pointer.get("selected_version"),
            "path": target_pointer.get("selected_path"),
        },
    }
    if profile_pointer != expected_profile_pointer:
        raise VerificationError("reviewed legacy profile target pointer differs from contract")
    pointer_path = _bundle_relative(target_pointer.get("path"), "legacy target pointer path")
    if pointer_path != HISTORY_CURRENT_REL:
        raise VerificationError("reviewed legacy target pointer path is not canonical")
    pointer_manifest_item = manifest_files.get(target_pointer["path"])
    if (
        pointer_manifest_item is None
        or pointer_manifest_item.get("role") != "current-target-closure-pointer"
        or _sha256(store.read_bytes(pointer_path)) != pointer_manifest_item["sha256"]
    ):
        raise VerificationError("reviewed legacy target pointer is not manifest-bound")
    pointer_entries = load_pointer(store, pointer_path, target_pointer["pointer_id"])
    selected = expected_profile_pointer["selected"]
    active_for_current = any(
        entry["id"] == selected["id"]
        and entry["version"] == selected["version"]
        and entry["path"] == selected["path"]
        for entry in pointer_entries
    )
    target_relative = _safe_relative(selected["path"], "legacy selected closure path")
    target_manifest_item = manifest_files.get(target_relative)
    if (
        target_manifest_item is None
        or target_manifest_item.get("role") != "immutable-target-closure"
    ):
        raise VerificationError("reviewed legacy target closure is not manifest-bound")
    target_path = BUNDLE_PREFIX + target_relative
    target_closure, target_entries = load_closure(
        store,
        target_path,
        expected_sha256=target_manifest_item["sha256"],
    )
    if (
        target_closure.get("closure_id") != selected["id"]
        or target_closure.get("closure_version") != selected["version"]
    ):
        raise VerificationError("reviewed legacy target closure identity differs")
    release_axis = profile.get("release_axis")
    expected_release_axis = {
        "target_wrapper_from": envelope["state"]["target_wrapper_version"],
        "target_wrapper_to": target_closure["state"]["target_wrapper_version"],
        "artifact_source_from": {
            "kind": "reviewed_source_commit",
            "repository": evidence["repository"],
            "revision": evidence["commit"],
            "tree": evidence["tree"],
            "release": None,
        },
        "artifact_source_to": {
            "kind": "required_release",
            "release": target_closure["source"]["required_release"],
            "binding": "contract_bundle.source_revision",
        },
    }
    if release_axis != expected_release_axis or lineage.get(
        "target_release"
    ) != target_closure["source"]["required_release"]:
        raise VerificationError("reviewed legacy profile release lineage is invalid")
    expected_execution = {
        "mode": "supervised_exact_plan",
        "discovery_authority": False,
        "adapter_write_authority": False,
        "approval": "one_time_exact_plan_digest",
        "approval_scope": "one_target_one_preimage_one_postimage",
        "compare_and_swap": "full_file_preimage",
        "snapshot": "required_before_any_write",
        "post_verify": "adapter_proof_and_current_closure",
        "rollback": "restore_snapshot_on_any_failure",
        "runtime_apply": "not_implemented",
    }
    if profile.get("execution") != expected_execution or profile.get("automatic") is not False:
        raise VerificationError("reviewed legacy profile execution authority is invalid")
    if contract_binding.get("execution") != {
        key: expected_execution[key]
        for key in (
            "mode",
            "discovery_authority",
            "adapter_write_authority",
            "approval",
            "runtime_apply",
        )
    }:
        raise VerificationError("reviewed legacy contract execution authority is invalid")

    changes, rendered_preserve = _derive_legacy_static_write_set(
        envelope,
        legacy_entries,
        target_entries,
    )
    operations = profile.get("operations")
    if not isinstance(operations, list) or not operations:
        raise VerificationError("reviewed legacy profile operations are missing")
    if [item.get("target_path") for item in operations if isinstance(item, dict)] != sorted(
        changes
    ):
        raise VerificationError("reviewed legacy profile write set is not deterministic")
    operation_ids: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise VerificationError("reviewed legacy profile operation is invalid")
        target = _safe_relative(operation.get("target_path"), "legacy operation target")
        operation_type = operation.get("type")
        if target not in changes or operation_type != changes[target][0]:
            raise VerificationError(
                f"reviewed legacy operation differs from derived write set: {target}"
            )
        expected_change_id = {
            "create_exact": "create:",
            "replace_exact": "replace:",
            "manifest_patch": "manifest:",
            "supervised_transform": "supervised-transform:",
        }[operation_type] + target
        change_id = operation.get("change_id")
        if change_id != expected_change_id or change_id in operation_ids:
            raise VerificationError(f"reviewed legacy change_id is invalid: {target}")
        operation_ids.add(change_id)
        _, before, after = changes[target]
        if operation_type in {"create_exact", "replace_exact"}:
            assert after is not None
            _verify_exact_operation(store, operation, operation_type, before, after)
        elif operation_type == "manifest_patch":
            assert after is not None
            _verify_legacy_manifest_operation(
                operation,
                {"path": source_binding["path"], "sha256": source_binding["sha256"]},
                envelope,
                target_path,
                after,
            )
        else:
            if operation != {
                "change_id": "supervised-transform:" + target,
                "type": "supervised_transform",
                "target_path": envelope["instruction_surface"]["path"],
                "source_envelope": {
                    "path": source_binding["path"],
                    "sha256": source_binding["sha256"],
                },
                "adapter": {
                    "path": adapter_binding["path"],
                    "sha256": adapter_binding["sha256"],
                },
                "plan_contract": {
                    "decision": "supervised_migration_available",
                    "writes": False,
                    "approval": "one_time_exact_proof_sha256",
                    "compare_and_swap": "full_file_preimage",
                    "runtime_apply": "not_implemented",
                },
            }:
                raise VerificationError("reviewed legacy supervised transform is invalid")
    if set(item["target_path"] for item in operations) != set(changes):
        raise VerificationError("reviewed legacy write set is incomplete")
    deferred = profile.get("deferred_rendered_surfaces")
    expected_deferred = [
        {
            "target_path": path,
            "policy": "preserve_byte_exact_no_write",
            "version_fact_source": ".evozeus-wrapper/wrapper.json",
        }
        for path in sorted(rendered_preserve)
    ]
    if deferred != expected_deferred:
        raise VerificationError("reviewed legacy preserve set differs from envelope")
    if profile.get("protected_business_surfaces") != [
        {
            "selector": "manifest.instruction_surface",
            "rule": "adapter_proven_complement_bytes_exact",
        }
    ]:
        raise VerificationError("reviewed legacy business preservation is invalid")
    if profile.get("fallback") != {
        "unknown_layout": "manual_migration_required_zero_write",
        "scattered_layout": "manual_migration_required_zero_write",
        "missing_evidence": "manual_migration_required_zero_write",
        "ambiguous_prompt": "manual_migration_required_zero_write",
    }:
        raise VerificationError("reviewed legacy fallback is invalid")
    profile["_verified_target_path"] = target_path
    profile["_active_for_current"] = active_for_current
    profile["static_write_set"] = [
        {"target_path": path, "type": changes[path][0]} for path in sorted(changes)
    ]
    return profile


def load_reviewed_legacy_profiles(
    store: BlobStore,
    migration_contract: dict[str, Any],
    protocol: dict[str, Any],
) -> list[dict[str, Any]]:
    path_roots = migration_contract.get("path_roots")
    if path_roots != {
        "artifact_path": "contracts/v1",
        "repository_path": "repository_root",
        "target_path": "target_repository_root",
    }:
        raise VerificationError("Harness migration contract path roots are invalid")
    raw_bindings = migration_contract.get("reviewed_legacy_migrations")
    if not isinstance(raw_bindings, list) or len(raw_bindings) != 1:
        raise VerificationError(
            "Harness migration contract must bind one reviewed legacy profile"
        )
    manifest_files = _contract_manifest_files(store, "contract manifest")
    repository_files = _contract_manifest_repository_files(store, "contract manifest")
    profiles = [
        load_supervised_legacy_profile(
            store,
            raw_bindings[0],
            protocol,
            manifest_files,
            repository_files,
        )
    ]
    if len({profile["profile_id"] for profile in profiles}) != len(profiles):
        raise VerificationError("reviewed legacy profile identities are duplicated")
    return profiles


def verify_catalog(store: BlobStore) -> dict[str, Any]:
    protocol = load_protocol(store)
    history_entries = load_pointer(
        store,
        HISTORY_CURRENT_REL,
        "using-evozeus-harness-current-closure",
    )
    if len(history_entries) != 1 or history_entries[0]["id"] != "using-evozeus-harness":
        raise VerificationError("Harness closure current pointer must select exactly one closure")
    current = history_entries[0]
    current_path = _bundle_relative(current["path"], "current closure path")
    current_closure, _ = load_closure(
        store,
        current_path,
        expected_sha256=current["sha256"],
    )
    if current_closure.get("closure_version") != current["version"]:
        raise VerificationError("Harness closure current pointer version disagrees with closure")
    migration_contract = _verify_current_release_bindings(store, current_closure)
    legacy_profiles = load_reviewed_legacy_profiles(
        store,
        migration_contract,
        protocol,
    )
    profile_entries = load_pointer(
        store,
        PROFILES_CURRENT_REL,
        "official-upgrade-current-profiles",
    )
    profiles: list[dict[str, Any]] = []
    for entry in profile_entries:
        profile_path = _bundle_relative(entry["path"], "current profile path")
        profile = load_profile(
            store,
            profile_path,
            protocol,
            expected_sha256=entry["sha256"],
        )
        if profile.get("profile_id") != entry["id"] or profile.get("profile_version") != entry["version"]:
            raise VerificationError("official upgrade current pointer disagrees with profile identity")
        profiles.append(profile)
    from_paths: set[str] = set()
    historical_from_versions: list[tuple[tuple[int, int, int], str]] = []
    for profile in profiles:
        if profile["_verified_to_path"] != current_path:
            raise VerificationError(
                "every active official upgrade profile must point directly to the current closure"
            )
        from_path = profile["_verified_from_path"]
        if from_path in from_paths:
            raise VerificationError(
                "active official upgrade profiles contain a duplicate from closure"
            )
        from_paths.add(from_path)
        from_closure = profile["_verified_from_closure"]
        historical_from_versions.append(
            (
                _semver(from_closure["closure_version"], "historical from closure version"),
                from_closure["closure_version"],
            )
        )
    current_version = current_closure["closure_version"]
    highest_historical_version = max(historical_from_versions)[1]
    expected_current_record = (
        ".evozeus-wrapper/docs/migrations/"
        f"harness-skill-{highest_historical_version}-to-{current_version}.md"
    )
    common_records = set(profiles[0]["migration_records"])
    for profile in profiles:
        records = profile.get("migration_records")
        current_record = profile.get("current_migration_record")
        if (
            not isinstance(records, list)
            or records.count(expected_current_record) != 1
            or current_record != expected_current_record
        ):
            raise VerificationError(
                "every direct-to-current profile must contain exactly one shared current-hop record"
            )
        common_records.intersection_update(records)
    if common_records != {expected_current_record}:
        raise VerificationError(
            "direct-to-current profiles must share only the current-hop migration record"
        )
    return {
        "status": "verified",
        "protocol": f"{protocol['protocol_id']}@{protocol['protocol_version']}",
        "current_closure": current_path,
        "current_closure_version": current["version"],
        "profiles": [
            f"{profile['profile_id']}@{profile['profile_version']}" for profile in profiles
        ],
        "supervised_legacy_profiles": [
            {
                "identity": f"{profile['profile_id']}@{profile['profile_version']}",
                "active_for_current": profile["_active_for_current"],
                "runtime_apply": profile["execution"]["runtime_apply"],
                "static_write_set": profile["static_write_set"],
            }
            for profile in legacy_profiles
        ],
    }


def _immutable_history_prefixes(base: FilesystemStore) -> set[str]:
    history_root = base.root / "contracts/v1/migrations/history"
    prefixes: set[str] = set()
    if not history_root.is_dir():
        return prefixes
    for closure_path in history_root.glob("**/closure.json"):
        if closure_path.is_symlink() or not closure_path.is_file():
            raise VerificationError("trusted base history contains an unsafe closure path")
        relative = closure_path.relative_to(base.root).as_posix()
        prefixes.add(PurePosixPath(relative).parent.as_posix() + "/")
    return prefixes


def _protected_candidate_change(
    base: FilesystemStore,
    path: str,
    immutable_history_prefixes: set[str],
) -> bool:
    if path in PROTECTED_MIGRATION_CONSUMER_PATHS:
        return True
    if path in {VERIFIER_REL, WORKFLOW_REL}:
        return True
    if path.startswith("contracts/v1/migrations/protocols/"):
        return True
    if path.startswith("contracts/v1/migrations/schemas/"):
        return True
    if base.exists(path) and path.startswith(PROTECTED_LEGACY_DATA_PREFIXES):
        return True
    if any(path.startswith(prefix) for prefix in immutable_history_prefixes):
        return True
    if (
        base.exists(path)
        and path.startswith("contracts/v1/migrations/profiles/")
        and path != PROFILES_CURRENT_REL
    ):
        return True
    return False


def _contract_manifest_document(
    store: BlobStore,
    label: str,
) -> dict[str, Any]:
    manifest = _json_file(store, CONTRACT_MANIFEST_REL, label)
    if manifest.get("schema_version") != "evozeus.coevolve.contract-manifest.v1":
        raise VerificationError(f"{label} schema identity is invalid")
    if manifest.get("bundle_id") != "evozeus-coevolve":
        raise VerificationError(f"{label} bundle identity is invalid")
    if manifest.get("source_repository") != "MetaInFLow/EvoZeus-CoEvolve":
        raise VerificationError(f"{label} source repository is invalid")
    if set(manifest) != {
        "schema_version",
        "bundle_id",
        "bundle_version",
        "source_repository",
        "source_revision",
        "runtime_compatibility",
        "files",
        "trusted_repository_files",
    }:
        raise VerificationError(f"{label} top-level contract is invalid")
    _semver(manifest.get("bundle_version"), f"{label} bundle version")
    _semver(manifest.get("source_revision"), f"{label} source revision")
    return manifest


def _contract_manifest_files(
    store: BlobStore,
    label: str,
) -> dict[str, dict[str, str]]:
    manifest = _contract_manifest_document(store, label)
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise VerificationError(f"{label} files are missing")
    files: dict[str, dict[str, str]] = {}
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "role"}:
            raise VerificationError(f"{label} contains an invalid file entry")
        path = _safe_relative(item.get("path"), f"{label} file path")
        if path == "manifest.json" or path in files:
            raise VerificationError(f"{label} contains a duplicate or recursive path: {path}")
        role = item.get("role")
        if not isinstance(role, str) or not role:
            raise VerificationError(f"{label} file role is invalid: {path}")
        files[path] = {
            "sha256": _require_sha256(item.get("sha256"), f"{label} file digest"),
            "role": role,
        }
    return files


def _contract_manifest_repository_files(
    store: BlobStore,
    label: str,
) -> dict[str, dict[str, str]]:
    manifest = _contract_manifest_document(store, label)
    raw_files = manifest.get("trusted_repository_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise VerificationError(f"{label} trusted repository files are missing")
    files: dict[str, dict[str, str]] = {}
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "role"}:
            raise VerificationError(
                f"{label} contains an invalid trusted repository file entry"
            )
        path = _safe_relative(item.get("path"), f"{label} repository file path")
        if path.startswith(BUNDLE_PREFIX) or path in files:
            raise VerificationError(
                f"{label} contains a duplicate or bundle-relative repository path: {path}"
            )
        role = item.get("role")
        if not isinstance(role, str) or not role:
            raise VerificationError(f"{label} repository file role is invalid: {path}")
        files[path] = {
            "sha256": _require_sha256(
                item.get("sha256"), f"{label} repository file digest"
            ),
            "role": role,
        }
    return files


def _verify_contract_manifest_digests(
    store: BlobStore,
    files: dict[str, dict[str, str]],
    label: str,
) -> None:
    for relative, item in files.items():
        actual = _sha256(store.read_bytes(BUNDLE_PREFIX + relative))
        if actual != item["sha256"]:
            raise VerificationError(
                f"{label} digest mismatch: {relative}: "
                f"expected={item['sha256']}; actual={actual}"
            )


def _verify_contract_manifest_repository_digests(
    store: BlobStore,
    files: dict[str, dict[str, str]],
    label: str,
) -> None:
    for relative, item in files.items():
        actual = _sha256(store.read_bytes(relative))
        if actual != item["sha256"]:
            raise VerificationError(
                f"{label} trusted repository digest mismatch: {relative}: "
                f"expected={item['sha256']}; actual={actual}"
            )


def _verify_current_release_bindings(
    store: BlobStore,
    current_closure: dict[str, Any],
) -> dict[str, Any]:
    manifest = _contract_manifest_document(store, "contract manifest")
    manifest_files = _contract_manifest_files(store, "contract manifest")
    _verify_contract_manifest_digests(store, manifest_files, "contract manifest")
    repository_files = _contract_manifest_repository_files(store, "contract manifest")
    _verify_contract_manifest_repository_digests(
        store,
        repository_files,
        "contract manifest",
    )
    migration_relative = MIGRATION_CONTRACT_REL.removeprefix(BUNDLE_PREFIX)
    migration_entry = manifest_files.get(migration_relative)
    if migration_entry is None or migration_entry.get("role") != "harness-migration-contract":
        raise VerificationError(
            "contract manifest does not bind the Harness migration contract"
        )
    migration_digest = _sha256(store.read_bytes(MIGRATION_CONTRACT_REL))
    if migration_digest != migration_entry["sha256"]:
        raise VerificationError(
            "contract manifest Harness migration contract digest mismatch"
        )
    migration_contract = _json_file(
        store,
        MIGRATION_CONTRACT_REL,
        "Harness migration contract",
    )
    if (
        migration_contract.get("schema_version")
        != "evozeus.coevolve.harness-migration-contract.v1"
        or migration_contract.get("contract_id") != "evozeus-harness-migration"
    ):
        raise VerificationError("Harness migration contract identity is invalid")
    state = current_closure.get("state")
    source = current_closure.get("source")
    if not isinstance(state, dict) or not isinstance(source, dict):
        raise VerificationError("current closure release state is missing")
    if state.get("contract_bundle_version") != manifest.get("bundle_version"):
        raise VerificationError(
            "current closure contract_bundle_version disagrees with contract manifest"
        )
    if source.get("required_release") != manifest.get("source_revision"):
        raise VerificationError(
            "current closure required_release disagrees with contract manifest source_revision"
        )
    if state.get("target_wrapper_version") != source.get("required_release"):
        raise VerificationError(
            "current closure target_wrapper_version disagrees with required_release"
        )
    if (
        migration_contract.get("current_harness_skill_version")
        != state.get("harness_skill_version")
    ):
        raise VerificationError(
            "Harness migration current_harness_skill_version disagrees with current closure"
        )
    closure_contract_entries = [
        item
        for item in current_closure.get("files", [])
        if isinstance(item, dict) and item.get("target_path") == (
            ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
        )
    ]
    if (
        len(closure_contract_entries) != 1
        or closure_contract_entries[0].get("sha256") != migration_digest
        or closure_contract_entries[0].get("source_path") != MIGRATION_CONTRACT_REL
    ):
        raise VerificationError(
            "current closure does not exactly bind the Harness migration contract"
        )
    manifest_states = [
        item
        for item in current_closure.get("files", [])
        if isinstance(item, dict) and item.get("kind") == "manifest_state"
    ]
    manifest_contract = (
        manifest_states[0].get("owned_state", {}).get("migration_contract")
        if len(manifest_states) == 1
        and isinstance(manifest_states[0].get("owned_state"), dict)
        else None
    )
    if not isinstance(manifest_contract, dict) or manifest_contract.get(
        "sha256"
    ) != "sha256:" + migration_digest:
        raise VerificationError(
            "current closure manifest state does not bind the migration contract"
        )
    return migration_contract


def verify_repository_history(
    store: BlobStore,
    *,
    head_sha: str,
    construction_resolver: ConstructionRevisionResolver,
) -> dict[str, Any]:
    """Verify every manifest-bound immutable closure against repository ancestry."""
    if GIT_OID_PATTERN.fullmatch(head_sha) is None:
        raise VerificationError("repository history head SHA is invalid")
    manifest_files = _contract_manifest_files(store, "repository history manifest")
    _verify_contract_manifest_digests(
        store,
        manifest_files,
        "repository history manifest",
    )
    repository_files = _contract_manifest_repository_files(
        store, "repository history manifest"
    )
    _verify_contract_manifest_repository_digests(
        store,
        repository_files,
        "repository history manifest",
    )
    closure_paths = sorted(
        BUNDLE_PREFIX + relative
        for relative, item in manifest_files.items()
        if item.get("role") == "immutable-target-closure"
    )
    if not closure_paths:
        raise VerificationError("repository history contains no immutable closures")
    verified_revisions: list[str] = []
    for closure_path in closure_paths:
        closure, entries = load_closure(store, closure_path)
        source = closure["source"]
        expected_sources: dict[str, ConstructionBlob] = {}
        for target_path, item in entries.items():
            if item.get("source_binding") != "construction_revision":
                continue
            source_path = _safe_relative(
                item.get("source_path"),
                f"construction source for {target_path}",
            )
            artifact_path = _relative_to_document(
                closure_path,
                item.get("artifact_path"),
                f"construction artifact for {target_path}",
            )
            artifact_mode = store.mode(artifact_path)
            if artifact_mode not in ALLOWED_BLOB_MODES:
                raise VerificationError(
                    f"immutable closure construction artifact mode is unsafe: {artifact_path}"
                )
            expected = ConstructionBlob(
                path=source_path,
                mode=artifact_mode,
                data=store.read_bytes(artifact_path),
            )
            prior = expected_sources.get(source_path)
            if prior is not None and prior != expected:
                raise VerificationError(
                    f"immutable closure binds conflicting construction source: {source_path}"
                )
            expected_sources[source_path] = expected
        revision = source["construction_revision"]
        evidence = construction_resolver(
            source["repository"],
            revision,
            head_sha,
            frozenset(expected_sources),
        )
        if (
            evidence.repository != source["repository"]
            or evidence.revision != revision
            or evidence.head_sha != head_sha
            or evidence.is_ancestor is not True
        ):
            raise VerificationError(
                "immutable closure construction_revision is not an ancestor of repository HEAD: "
                f"{closure_path}@{revision}"
            )
        if set(evidence.files) != set(expected_sources):
            raise VerificationError(
                f"immutable closure construction evidence is incomplete: {closure_path}"
            )
        for source_path, expected in expected_sources.items():
            historical = evidence.files[source_path]
            if historical.path != source_path:
                raise VerificationError(
                    f"immutable closure construction source path is invalid: {source_path}"
                )
            if historical.mode != expected.mode:
                raise VerificationError(
                    f"immutable closure construction source mode differs: {source_path}"
                )
            if historical.data != expected.data:
                raise VerificationError(
                    f"immutable closure construction source bytes differ: {source_path}"
                )
        verified_revisions.append(revision)
    return {
        "head": head_sha,
        "immutable_closures": len(closure_paths),
        "construction_revisions": verified_revisions,
    }


def _local_construction_revision_resolver(
    repo_root: Path,
) -> ConstructionRevisionResolver:
    root = repo_root.expanduser().resolve()

    def resolve(
        repository: str,
        revision: str,
        head_sha: str,
        source_paths: frozenset[str],
    ) -> ConstructionRevisionEvidence:
        if repository != "MetaInFLow/EvoZeus-CoEvolve":
            raise VerificationError("construction revision repository is not canonical")
        ancestry = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", revision, head_sha],
            capture_output=True,
            check=False,
        )
        if ancestry.returncode not in {0, 1}:
            raise VerificationError(
                "repository ancestry cannot be verified: "
                + ancestry.stderr.decode("utf-8", errors="replace").strip()
            )
        files: dict[str, ConstructionBlob] = {}
        for path in sorted(source_paths):
            listing = subprocess.run(
                ["git", "-C", str(root), "ls-tree", "-z", revision, "--", path],
                capture_output=True,
                check=False,
            )
            if listing.returncode != 0 or not listing.stdout.endswith(b"\0"):
                raise VerificationError(
                    f"construction revision source is missing or unsafe: {path}"
                )
            metadata, separator, listed_path = listing.stdout[:-1].partition(b"\t")
            fields = metadata.split()
            if (
                separator != b"\t"
                or listed_path.decode("utf-8", errors="strict") != path
                or len(fields) != 3
                or fields[0].decode("ascii") not in ALLOWED_BLOB_MODES
                or fields[1] != b"blob"
            ):
                raise VerificationError(
                    f"construction revision source is missing or unsafe: {path}"
                )
            content = subprocess.run(
                ["git", "-C", str(root), "show", f"{revision}:{path}"],
                capture_output=True,
                check=False,
            )
            if content.returncode != 0:
                raise VerificationError(
                    f"construction revision source cannot be read: {path}"
                )
            files[path] = ConstructionBlob(
                path=path,
                mode=fields[0].decode("ascii"),
                data=content.stdout,
            )
        return ConstructionRevisionEvidence(
            repository=repository,
            revision=revision,
            head_sha=head_sha,
            is_ancestor=ancestry.returncode == 0,
            files=files,
        )

    return resolve


def _verify_candidate_contract_manifest(
    base: FilesystemStore,
    candidate: CandidateStore,
    changes: dict[str, CandidateBlob],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    base_files = _contract_manifest_files(base, "trusted base contract manifest")
    candidate_files = _contract_manifest_files(candidate, "candidate contract manifest")
    base_repository_files = _contract_manifest_repository_files(
        base, "trusted base contract manifest"
    )
    candidate_repository_files = _contract_manifest_repository_files(
        candidate, "candidate contract manifest"
    )
    if candidate_repository_files != base_repository_files:
        raise VerificationError(
            "candidate cannot rotate trusted repository files as migration data"
        )
    _verify_contract_manifest_repository_digests(
        candidate,
        candidate_repository_files,
        "candidate contract manifest",
    )
    expected_paths = set(base_files)
    for path, change in changes.items():
        if not path.startswith(BUNDLE_PREFIX) or path == CONTRACT_MANIFEST_REL:
            continue
        relative = path.removeprefix(BUNDLE_PREFIX)
        if change.status == "deleted":
            if relative in base_files:
                raise VerificationError(
                    "candidate cannot delete trusted base contract manifest path: "
                    f"{relative}"
                )
            continue
        expected_paths.add(relative)
    if set(candidate_files) != expected_paths:
        raise VerificationError(
            "candidate contract manifest does not exactly enumerate the candidate bundle"
        )
    for relative, item in candidate_files.items():
        base_item = base_files.get(relative)
        if base_item is not None and item["role"] != base_item["role"]:
            raise VerificationError(
                f"candidate contract manifest role mismatch: {relative}: "
                f"expected={base_item['role']}; actual={item['role']}"
            )
        actual = _sha256(candidate.read_bytes(BUNDLE_PREFIX + relative))
        if actual != item["sha256"]:
            raise VerificationError(
                f"candidate contract manifest digest mismatch: {relative}: "
                f"expected={item['sha256']}; actual={actual}"
            )
    return base_files, candidate_files


def _require_candidate_manifest_role(
    manifest_files: dict[str, dict[str, str]],
    repository_path: str,
    expected_role: str,
) -> None:
    if not repository_path.startswith(BUNDLE_PREFIX):
        raise VerificationError(
            f"candidate contract path is outside its manifest: {repository_path}"
        )
    relative = repository_path.removeprefix(BUNDLE_PREFIX)
    item = manifest_files.get(relative)
    if item is None or item["role"] != expected_role:
        actual_role = None if item is None else item["role"]
        raise VerificationError(
            f"candidate contract manifest role mismatch: {relative}: "
            f"expected={expected_role}; actual={actual_role}"
        )


def _bind_expected_new_manifest_role(
    expected_roles: dict[str, str],
    base_files: dict[str, dict[str, str]],
    repository_path: str,
    role: str,
) -> None:
    if not repository_path.startswith(BUNDLE_PREFIX):
        raise VerificationError(
            f"candidate contract path is outside its manifest: {repository_path}"
        )
    relative = repository_path.removeprefix(BUNDLE_PREFIX)
    if relative in base_files:
        return
    prior = expected_roles.get(relative)
    if prior is not None and prior != role:
        raise VerificationError(
            f"candidate contract path has conflicting manifest roles: {relative}: "
            f"expected={prior}; conflicting={role}"
        )
    expected_roles[relative] = role


def _verify_expected_new_manifest_roles(
    base_files: dict[str, dict[str, str]],
    candidate_files: dict[str, dict[str, str]],
    expected_roles: dict[str, str],
) -> None:
    actual_new_paths = set(candidate_files) - set(base_files)
    expected_new_paths = set(expected_roles)
    unexpected = sorted(actual_new_paths - expected_new_paths)
    if unexpected:
        if any(
            path.startswith(("migrations/history/", "migrations/profiles/"))
            for path in unexpected
        ):
            raise VerificationError(
                "candidate migration file is not bound by current closure/profile pointers: "
                + ", ".join(unexpected)
            )
        raise VerificationError(
            "candidate new bundle path is not authorized by the candidate current closure: "
            + ", ".join(unexpected)
        )
    missing = sorted(expected_new_paths - actual_new_paths)
    if missing:
        raise VerificationError(
            "candidate contract manifest omits an authorized new bundle path: "
            + ", ".join(missing)
        )
    for relative, role in expected_roles.items():
        actual_role = candidate_files[relative]["role"]
        if actual_role != role:
            raise VerificationError(
                f"candidate contract manifest role mismatch: {relative}: "
                f"expected={role}; actual={actual_role}"
            )


def _canonical_active_profile_path(profile_id: object, profile_version: object) -> str:
    if (
        not isinstance(profile_id, str)
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?", profile_id)
        is None
    ):
        raise VerificationError("candidate active profile id is not filename-safe")
    major, _, _ = _semver(profile_version, "candidate active profile version")
    return f"{BUNDLE_PREFIX}migrations/profiles/{profile_id}-v{major}.json"


def _legacy_trust_anchor_projection(contract: dict[str, Any]) -> list[dict[str, Any]]:
    bindings = contract.get("reviewed_legacy_migrations")
    if not isinstance(bindings, list):
        raise VerificationError("Harness migration contract legacy bindings are missing")
    projection: list[dict[str, Any]] = []
    for item in bindings:
        if not isinstance(item, dict):
            raise VerificationError("Harness migration contract legacy binding is invalid")
        adapter = item.get("adapter")
        lineage = item.get("release_lineage")
        if not isinstance(adapter, dict) or not isinstance(lineage, dict):
            raise VerificationError("Harness migration contract legacy trust anchor is invalid")
        projection.append(
            {
                "source_envelope": item.get("source_envelope"),
                "adapter": {
                    key: adapter.get(key)
                    for key in ("path", "sha256", "schema", "implementation")
                },
                "source_repository": lineage.get("source_repository"),
                "source_commit": lineage.get("source_commit"),
                "source_tree": lineage.get("source_tree"),
            }
        )
    return projection


def verify_candidate(
    base: FilesystemStore,
    changes: dict[str, CandidateBlob],
    *,
    head_sha: str,
    repository: str = "MetaInFLow/EvoZeus-CoEvolve",
    construction_resolver: ConstructionRevisionResolver | None = None,
) -> dict[str, Any]:
    if GIT_OID_PATTERN.fullmatch(head_sha) is None:
        raise VerificationError("candidate head SHA is invalid")
    if repository != "MetaInFLow/EvoZeus-CoEvolve":
        raise VerificationError("official upgrade candidate must use the canonical repository")
    base_protocol = load_protocol(base)
    base_report = verify_catalog(base)
    immutable_history_prefixes = _immutable_history_prefixes(base)
    for path, change in changes.items():
        _safe_relative(path, "candidate changed path")
        if path in PROTECTED_MIGRATION_CONSUMER_PATHS:
            raise VerificationError(
                f"candidate modifies trusted base authority or migration consumer: {path}"
            )
        if _protected_candidate_change(base, path, immutable_history_prefixes):
            raise VerificationError(f"candidate modifies trusted base authority or history: {path}")
        if change.status != "deleted" and (
            change.mode not in ALLOWED_BLOB_MODES or change.object_type != "blob"
        ):
            raise VerificationError(
                f"candidate adds a symlink, submodule or unsupported object: {path}"
            )
    candidate = CandidateStore(base, changes)
    base_contract = _json_file(
        base,
        MIGRATION_CONTRACT_REL,
        "trusted base Harness migration contract",
    )
    candidate_contract = _json_file(
        candidate,
        MIGRATION_CONTRACT_REL,
        "candidate Harness migration contract",
    )
    if _legacy_trust_anchor_projection(candidate_contract) != (
        _legacy_trust_anchor_projection(base_contract)
    ):
        raise VerificationError(
            "candidate data cannot rotate a trusted legacy envelope or adapter"
        )
    base_manifest_files, candidate_manifest_files = _verify_candidate_contract_manifest(
        base,
        candidate,
        changes,
    )
    expected_new_manifest_roles: dict[str, str] = {}
    report = verify_catalog(candidate)

    base_history = load_pointer(
        base,
        HISTORY_CURRENT_REL,
        "using-evozeus-harness-current-closure",
    )[0]
    candidate_history = load_pointer(
        candidate,
        HISTORY_CURRENT_REL,
        "using-evozeus-harness-current-closure",
    )[0]
    if _semver(candidate_history["version"], "candidate closure version") <= _semver(
        base_history["version"], "base closure version"
    ):
        raise VerificationError("candidate current closure does not advance history")
    candidate_closure_path = _bundle_relative(
        candidate_history["path"],
        "candidate current closure path",
    )
    expected_closure_path = (
        "contracts/v1/migrations/history/harness-skill/"
        f"{candidate_history['version']}/closure.json"
    )
    if candidate_closure_path != expected_closure_path:
        raise VerificationError(
            "candidate current closure path is not canonical for its version: "
            f"expected={expected_closure_path}; actual={candidate_closure_path}"
        )
    if base.exists(candidate_closure_path):
        raise VerificationError("candidate current closure must use a new immutable version path")
    candidate_closure, candidate_entries = load_closure(
        candidate,
        candidate_closure_path,
        expected_sha256=candidate_history["sha256"],
    )
    expected_candidate_changes = {
        CONTRACT_MANIFEST_REL,
        HISTORY_CURRENT_REL,
        PROFILES_CURRENT_REL,
        candidate_closure_path,
    }
    _require_candidate_manifest_role(
        candidate_manifest_files,
        HISTORY_CURRENT_REL,
        "current-target-closure-pointer",
    )
    _require_candidate_manifest_role(
        candidate_manifest_files,
        candidate_closure_path,
        "immutable-target-closure",
    )
    _bind_expected_new_manifest_role(
        expected_new_manifest_roles,
        base_manifest_files,
        candidate_closure_path,
        "immutable-target-closure",
    )
    for item in candidate_entries.values():
        artifact_relative = item.get("artifact_path")
        if artifact_relative is not None:
            artifact_path = _relative_to_document(
                candidate_closure_path,
                artifact_relative,
                "candidate closure artifact path",
            )
            artifact_root = PurePosixPath(candidate_closure_path).parent / "artifacts"
            if not PurePosixPath(artifact_path).is_relative_to(artifact_root):
                raise VerificationError(
                    "candidate closure artifact is outside its immutable version: "
                    f"{artifact_path}"
                )
            artifact_change = changes.get(artifact_path)
            if artifact_change is None or artifact_change.status != "added":
                raise VerificationError(
                    "candidate closure artifact must be a newly added immutable blob: "
                    f"{artifact_path}"
                )
            expected_candidate_changes.add(artifact_path)
            _require_candidate_manifest_role(
                candidate_manifest_files,
                artifact_path,
                "immutable-target-closure-artifact",
            )
            _bind_expected_new_manifest_role(
                expected_new_manifest_roles,
                base_manifest_files,
                artifact_path,
                "immutable-target-closure-artifact",
            )
    base_profiles = load_pointer(
        base,
        PROFILES_CURRENT_REL,
        "official-upgrade-current-profiles",
    )
    candidate_profiles = load_pointer(
        candidate,
        PROFILES_CURRENT_REL,
        "official-upgrade-current-profiles",
    )
    _require_candidate_manifest_role(
        candidate_manifest_files,
        PROFILES_CURRENT_REL,
        "current-official-upgrade-profile-pointer",
    )
    base_closure_path = _bundle_relative(base_history["path"], "base current closure path")
    base_from_paths: set[str] = set()
    for entry in base_profiles:
        base_profile_path = _bundle_relative(entry["path"], "base profile path")
        base_profile = load_profile(
            base,
            base_profile_path,
            base_protocol,
            expected_sha256=entry["sha256"],
        )
        base_from_paths.add(base_profile["_verified_from_path"])
    required_from_paths = {base_closure_path, *base_from_paths}
    candidate_from_paths: set[str] = set()
    for entry in candidate_profiles:
        profile_path = _bundle_relative(entry["path"], "candidate profile path")
        if base.exists(profile_path):
            raise VerificationError(
                f"candidate active profile must use a new immutable path: {profile_path}"
            )
        profile_change = changes.get(profile_path)
        if profile_change is None or profile_change.status != "added":
            raise VerificationError(
                f"candidate active profile must be a newly added blob: {profile_path}"
            )
        expected_candidate_changes.add(profile_path)
        profile = load_profile(
            candidate,
            profile_path,
            load_protocol(base),
            expected_sha256=entry["sha256"],
        )
        if (
            profile.get("profile_id") != entry["id"]
            or profile.get("profile_version") != entry["version"]
        ):
            raise VerificationError(
                "candidate profile pointer disagrees with profile identity"
            )
        expected_profile_path = _canonical_active_profile_path(
            profile["profile_id"],
            profile["profile_version"],
        )
        if profile_path != expected_profile_path:
            raise VerificationError(
                "candidate active profile path is not canonical for its identity: "
                f"expected={expected_profile_path}; actual={profile_path}"
            )
        _require_candidate_manifest_role(
            candidate_manifest_files,
            profile_path,
            "official-upgrade-profile",
        )
        _bind_expected_new_manifest_role(
            expected_new_manifest_roles,
            base_manifest_files,
            profile_path,
            "official-upgrade-profile",
        )
        if profile["_verified_to_path"] != candidate_closure_path:
            raise VerificationError("candidate profile does not end at the candidate current closure")
        from_path = profile["_verified_from_path"]
        if not base.exists(from_path):
            raise VerificationError(
                "candidate direct-to-current profile must start at immutable base history"
            )
        candidate_from_paths.add(from_path)
    missing_history = sorted(required_from_paths - candidate_from_paths)
    if missing_history:
        raise VerificationError(
            "candidate direct-to-current profiles do not preserve historical coverage: "
            + ", ".join(missing_history)
        )

    bound_sources: set[str] = set()
    construction_source_modes: dict[str, str] = {}
    for target_path, item in candidate_entries.items():
        source_path = item.get("source_path")
        artifact_relative = item.get("artifact_path")
        if source_path is None or artifact_relative is None:
            continue
        source_path = _safe_relative(source_path, "candidate closure source path")
        source_binding = item.get("source_binding")
        if (
            source_binding == "construction_revision"
            and not _construction_source_allowed(source_path)
        ):
            raise VerificationError(
                "candidate construction source is outside the trusted protocol allowlist: "
                f"{source_path}"
            )
        if (
            source_binding == "required_release"
            and not source_path.startswith(BUNDLE_PREFIX)
            and not _construction_source_allowed(source_path)
        ):
            raise VerificationError(
                "candidate required-release source is outside the trusted protocol allowlist: "
                f"{source_path}"
            )
        artifact_path = _relative_to_document(
            candidate_closure_path,
            artifact_relative,
            "candidate closure artifact path",
        )
        if candidate.read_bytes(source_path) != candidate.read_bytes(artifact_path):
            raise VerificationError(
                f"candidate closure artifact differs from reviewed source: {target_path}"
            )
        if candidate.mode(artifact_path) != item.get("mode"):
            raise VerificationError(
                f"candidate closure artifact mode differs from target mode: {target_path}"
            )
        bound_sources.add(source_path)
        if source_path in changes:
            expected_candidate_changes.add(source_path)
        if source_path.startswith(BUNDLE_PREFIX):
            relative_source = source_path.removeprefix(BUNDLE_PREFIX)
            if relative_source not in base_manifest_files:
                _bind_expected_new_manifest_role(
                    expected_new_manifest_roles,
                    base_manifest_files,
                    source_path,
                    "target-closure-source",
                )
                _require_candidate_manifest_role(
                    candidate_manifest_files,
                    source_path,
                    "target-closure-source",
                )
        if source_binding == "construction_revision":
            expected_mode = item["mode"]
            prior_mode = construction_source_modes.get(source_path)
            if prior_mode is not None and prior_mode != expected_mode:
                raise VerificationError(
                    f"candidate construction source has conflicting closure modes: {source_path}"
                )
            construction_source_modes[source_path] = expected_mode
    _verify_expected_new_manifest_roles(
        base_manifest_files,
        candidate_manifest_files,
        expected_new_manifest_roles,
    )
    actual_candidate_changes = set(changes)
    missing_candidate_changes = sorted(
        expected_candidate_changes - actual_candidate_changes
    )
    if missing_candidate_changes:
        raise VerificationError(
            "candidate omits paths required by the derived official-upgrade closure: "
            + ", ".join(missing_candidate_changes)
        )
    unexpected_candidate_changes = sorted(
        actual_candidate_changes - expected_candidate_changes
    )
    if unexpected_candidate_changes:
        raise VerificationError(
            "candidate changed path is outside the derived official-upgrade closure: "
            + ", ".join(unexpected_candidate_changes)
        )
    revision = candidate_closure["source"]["construction_revision"]
    if construction_resolver is None:
        raise VerificationError("candidate construction revision evidence is unavailable")
    evidence = construction_resolver(
        repository,
        revision,
        head_sha,
        frozenset(construction_source_modes),
    )
    if (
        evidence.repository != repository
        or evidence.revision != revision
        or evidence.head_sha != head_sha
        or evidence.is_ancestor is not True
    ):
        raise VerificationError(
            "candidate construction revision is not a verified same-repository ancestor"
        )
    if set(evidence.files) != set(construction_source_modes):
        raise VerificationError("candidate construction revision source evidence is incomplete")
    total_construction_bytes = 0
    for source_path in sorted(construction_source_modes):
        historical = evidence.files[source_path]
        if historical.path != source_path:
            raise VerificationError("candidate construction source path identity is invalid")
        if historical.mode not in ALLOWED_BLOB_MODES:
            raise VerificationError(
                f"candidate construction source mode is invalid: {source_path}"
            )
        if historical.mode != construction_source_modes[source_path]:
            raise VerificationError(
                f"candidate construction source mode differs from target closure: {source_path}"
            )
        total_construction_bytes += len(historical.data)
        if len(historical.data) > MAX_BLOB_BYTES or total_construction_bytes > MAX_TOTAL_BLOB_BYTES:
            raise VerificationError("candidate construction source evidence exceeds size limit")
        if candidate.read_bytes(source_path) != historical.data:
            raise VerificationError(
                f"candidate source differs from construction revision: {source_path}"
            )
        if candidate.mode(source_path) != historical.mode:
            raise VerificationError(
                f"candidate source mode differs from construction revision: {source_path}"
            )
    for path, change in changes.items():
        if change.status == "deleted":
            continue
        if path.startswith("templates/target/") and path not in bound_sources:
            raise VerificationError(f"candidate target template is absent from target closure: {path}")

    history_report = verify_repository_history(
        candidate,
        head_sha=head_sha,
        construction_resolver=construction_resolver,
    )

    report.update(
        {
            "status": "verified_candidate",
            "base_closure_version": base_report["current_closure_version"],
            "candidate_closure_version": candidate_history["version"],
            "candidate_head": head_sha,
            "candidate_files_executed": False,
            "immutable_closures_verified": history_report["immutable_closures"],
        }
    )
    return report


def verify_classified_pull_request(
    base: FilesystemStore,
    changes: dict[str, CandidateBlob],
    *,
    head_sha: str,
    repository: str,
    construction_resolver: ConstructionRevisionResolver,
) -> dict[str, Any]:
    classification = classify_candidate_changes(load_protocol(base), changes)
    if classification == "not_applicable":
        return {
            "status": "not_applicable",
            "classification": classification,
            "candidate_head": head_sha,
            "candidate_files_executed": False,
        }
    if classification == "rotation_required":
        return {
            "status": "rotation_required",
            "classification": classification,
            "candidate_head": head_sha,
            "candidate_files_executed": False,
            "next_step": "land trusted source first, then open a data-only migration PR",
        }
    report = verify_candidate(
        base,
        changes,
        head_sha=head_sha,
        repository=repository,
        construction_resolver=construction_resolver,
    )
    report["classification"] = classification
    return report


def _github_json(url: str, token: str) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "api.github.com" or parsed.port not in {None, 443}:
        raise VerificationError("GitHub API URL is not fixed to api.github.com")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "EvoZeus-official-upgrade-verifier",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != "api.github.com" or final.port not in {None, 443}:
                raise VerificationError("GitHub API redirected away from api.github.com")
            data = response.read(MAX_BLOB_BYTES * 2 + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise VerificationError(f"GitHub API request failed: {exc}") from exc
    if len(data) > MAX_BLOB_BYTES * 2:
        raise VerificationError("GitHub API response exceeds verifier limit")
    return _strict_json(data, "GitHub API response")


def _github_tree(repository: str, oid: str, token: str) -> dict[str, dict[str, str]]:
    if REPOSITORY_PATTERN.fullmatch(repository) is None or GIT_OID_PATTERN.fullmatch(oid) is None:
        raise VerificationError("GitHub repository or tree identity is invalid")
    value = _github_json(
        f"https://api.github.com/repos/{repository}/git/trees/{oid}?recursive=1",
        token,
    )
    if value.get("truncated") is True:
        raise VerificationError("GitHub candidate tree is truncated")
    tree = value.get("tree")
    if not isinstance(tree, list):
        raise VerificationError("GitHub candidate tree is missing")
    result: dict[str, dict[str, str]] = {}
    for item in tree:
        if not isinstance(item, dict) or item.get("type") == "tree":
            continue
        path = _safe_relative(item.get("path"), "GitHub tree path")
        mode = item.get("mode")
        object_type = item.get("type")
        object_oid = item.get("sha")
        if not all(isinstance(value, str) for value in (mode, object_type, object_oid)):
            raise VerificationError(f"GitHub tree entry is incomplete: {path}")
        result[path] = {"mode": mode, "type": object_type, "sha": object_oid}
    return result


def _github_blob(repository: str, oid: str, token: str) -> bytes:
    value = _github_json(f"https://api.github.com/repos/{repository}/git/blobs/{oid}", token)
    if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        raise VerificationError("GitHub blob encoding is invalid")
    try:
        data = base64.b64decode(value["content"], validate=True)
    except ValueError as exc:
        raise VerificationError("GitHub blob base64 is invalid") from exc
    if len(data) > MAX_BLOB_BYTES:
        raise VerificationError("GitHub candidate blob exceeds verifier limit")
    return data


def _github_construction_revision_resolver(
    token: str,
) -> ConstructionRevisionResolver:
    ancestry_cache: dict[tuple[str, str, str], bool] = {}
    tree_cache: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
    blob_cache: dict[tuple[str, str], bytes] = {}

    def resolve(
        repository: str,
        revision: str,
        head_sha: str,
        source_paths: frozenset[str],
    ) -> ConstructionRevisionEvidence:
        if repository != "MetaInFLow/EvoZeus-CoEvolve":
            raise VerificationError("construction revision repository is not canonical")
        ancestry_key = (repository, revision, head_sha)
        if ancestry_key not in ancestry_cache:
            comparison = _github_json(
                f"https://api.github.com/repos/{repository}/compare/{revision}...{head_sha}",
                token,
            )
            merge_base = comparison.get("merge_base_commit")
            ancestry_cache[ancestry_key] = (
                comparison.get("status") in {"ahead", "identical"}
                and isinstance(merge_base, dict)
                and merge_base.get("sha") == revision
            )
        tree_key = (repository, revision)
        if tree_key not in tree_cache:
            tree_cache[tree_key] = _github_tree(repository, revision, token)
        tree = tree_cache[tree_key]
        files: dict[str, ConstructionBlob] = {}
        for path in sorted(source_paths):
            item = tree.get(path)
            if (
                not isinstance(item, dict)
                or item.get("type") != "blob"
                or item.get("mode") not in ALLOWED_BLOB_MODES
                or not isinstance(item.get("sha"), str)
            ):
                raise VerificationError(
                    f"construction revision source is missing or unsafe: {path}"
                )
            blob_key = (repository, item["sha"])
            if blob_key not in blob_cache:
                blob_cache[blob_key] = _github_blob(repository, item["sha"], token)
            files[path] = ConstructionBlob(
                path=path,
                mode=item["mode"],
                data=blob_cache[blob_key],
            )
        return ConstructionRevisionEvidence(
            repository=repository,
            revision=revision,
            head_sha=head_sha,
            is_ancestor=ancestry_cache[ancestry_key],
            files=files,
        )

    return resolve


def candidate_from_pull_request(
    event_path: Path,
    repo_root: Path,
    token: str,
) -> tuple[dict[str, CandidateBlob], str, str]:
    event = _strict_json(event_path.read_bytes(), "pull_request_target event")
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        raise VerificationError("event is not a pull request")
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise VerificationError("pull request refs are missing")
    base_sha = base.get("sha")
    head_sha = head.get("sha")
    base_repo = (base.get("repo") or {}).get("full_name") if isinstance(base.get("repo"), dict) else None
    head_repo = (head.get("repo") or {}).get("full_name") if isinstance(head.get("repo"), dict) else None
    if not isinstance(base_sha, str) or GIT_OID_PATTERN.fullmatch(base_sha) is None:
        raise VerificationError("pull request base SHA is invalid")
    if not isinstance(head_sha, str) or GIT_OID_PATTERN.fullmatch(head_sha) is None:
        raise VerificationError("pull request head SHA is invalid")
    if not isinstance(base_repo, str) or REPOSITORY_PATTERN.fullmatch(base_repo) is None:
        raise VerificationError("pull request base repository is invalid")
    if not isinstance(head_repo, str) or REPOSITORY_PATTERN.fullmatch(head_repo) is None:
        raise VerificationError("pull request head repository is invalid")
    if base_repo != "MetaInFLow/EvoZeus-CoEvolve":
        raise VerificationError("pull request base repository is not canonical")
    local_head = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if local_head.returncode != 0 or local_head.stdout.strip() != base_sha:
        raise VerificationError("trusted verifier checkout does not equal the PR base SHA")
    base_tree = _github_tree(base_repo, base_sha, token)
    head_tree = _github_tree(head_repo, head_sha, token)
    changes: dict[str, CandidateBlob] = {}
    for path in sorted(set(base_tree) | set(head_tree)):
        before = base_tree.get(path)
        after = head_tree.get(path)
        if before == after:
            continue
        if after is None:
            changes[path] = CandidateBlob(path, "deleted", None, None, None, None)
            continue
        status = "added" if before is None else "modified"
        oid = after["sha"]
        changes[path] = CandidateBlob(
            path,
            status,
            after["mode"],
            after["type"],
            oid,
            lambda repository=head_repo, blob_oid=oid: _github_blob(repository, blob_oid, token),
        )
    return changes, head_sha, head_repo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_base = subparsers.add_parser("verify-base")
    verify_base.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify_pr = subparsers.add_parser("verify-pull-request")
    verify_pr.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify_pr.add_argument("--event", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        base = FilesystemStore(args.repo_root)
        if args.command == "verify-base":
            report = verify_catalog(base)
            head = subprocess.run(
                ["git", "-C", str(args.repo_root), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=False,
            )
            head_sha = head.stdout.strip()
            if head.returncode != 0 or GIT_OID_PATTERN.fullmatch(head_sha) is None:
                raise VerificationError("repository HEAD cannot be resolved")
            history = verify_repository_history(
                base,
                head_sha=head_sha,
                construction_resolver=_local_construction_revision_resolver(
                    args.repo_root
                ),
            )
            report["repository_history"] = history
        else:
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise VerificationError("GITHUB_TOKEN is required for pull_request_target verification")
            changes, head_sha, repository = candidate_from_pull_request(
                args.event,
                args.repo_root,
                token,
            )
            report = verify_classified_pull_request(
                base,
                changes,
                head_sha=head_sha,
                repository=repository,
                construction_resolver=_github_construction_revision_resolver(token),
            )
    except (OSError, VerificationError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
