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
VERIFIER_REL = "scripts/evozeus_official_upgrade_verify.py"
WORKFLOW_REL = ".github/workflows/evozeus-official-upgrade-profile.yml"
BUNDLE_PREFIX = "contracts/v1/"
PROTECTED_MIGRATION_CONSUMER_PATHS = frozenset(
    {
        "scripts/evozeus_harness_migration.py",
        "scripts/evozeus_wrapper.py",
        "scripts/evozeus_wrapper_global_hook.py",
        "scripts/evozeus_wrapper_lifecycle.py",
    }
)
PROTECTED_BASE_PATH_DECLARATIONS = (
    WORKFLOW_REL,
    "contracts/v1/migrations/protocols/",
    "contracts/v1/migrations/schemas/",
    "contracts/v1/migrations/history/*/v*/",
    "contracts/v1/migrations/profiles/*-v*.json",
    VERIFIER_REL,
    *sorted(PROTECTED_MIGRATION_CONSUMER_PATHS),
)
ALLOWED_OPERATION_TYPES = {
    "create_exact",
    "replace_exact",
    "manifest_patch",
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
    return protocol


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
    if any(path.startswith(prefix) for prefix in immutable_history_prefixes):
        return True
    if (
        base.exists(path)
        and path.startswith("contracts/v1/migrations/profiles/")
        and path != PROFILES_CURRENT_REL
    ):
        return True
    return False


def _contract_manifest_files(
    store: BlobStore,
    label: str,
) -> dict[str, dict[str, str]]:
    manifest = _json_file(store, CONTRACT_MANIFEST_REL, label)
    if manifest.get("schema_version") != "evozeus.coevolve.contract-manifest.v1":
        raise VerificationError(f"{label} schema identity is invalid")
    if manifest.get("bundle_id") != "evozeus-coevolve":
        raise VerificationError(f"{label} bundle identity is invalid")
    if manifest.get("source_repository") != "MetaInFLow/EvoZeus-CoEvolve":
        raise VerificationError(f"{label} source repository is invalid")
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


def _verify_candidate_contract_manifest(
    base: FilesystemStore,
    candidate: CandidateStore,
    changes: dict[str, CandidateBlob],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    base_files = _contract_manifest_files(base, "trusted base contract manifest")
    candidate_files = _contract_manifest_files(candidate, "candidate contract manifest")
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
            load_protocol(base),
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
        if item.get("source_binding") == "construction_revision":
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

    report.update(
        {
            "status": "verified_candidate",
            "base_closure_version": base_report["current_closure_version"],
            "candidate_closure_version": candidate_history["version"],
            "candidate_head": head_sha,
            "candidate_files_executed": False,
        }
    )
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
    def resolve(
        repository: str,
        revision: str,
        head_sha: str,
        source_paths: frozenset[str],
    ) -> ConstructionRevisionEvidence:
        if repository != "MetaInFLow/EvoZeus-CoEvolve":
            raise VerificationError("construction revision repository is not canonical")
        comparison = _github_json(
            f"https://api.github.com/repos/{repository}/compare/{revision}...{head_sha}",
            token,
        )
        merge_base = comparison.get("merge_base_commit")
        is_ancestor = (
            comparison.get("status") in {"ahead", "identical"}
            and isinstance(merge_base, dict)
            and merge_base.get("sha") == revision
        )
        tree = _github_tree(repository, revision, token)
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
            files[path] = ConstructionBlob(
                path=path,
                mode=item["mode"],
                data=_github_blob(repository, item["sha"], token),
            )
        return ConstructionRevisionEvidence(
            repository=repository,
            revision=revision,
            head_sha=head_sha,
            is_ancestor=is_ancestor,
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
    if head_repo != base_repo or base_repo != "MetaInFLow/EvoZeus-CoEvolve":
        raise VerificationError("official upgrade profile PR must use the canonical repository")
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
        else:
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise VerificationError("GITHUB_TOKEN is required for pull_request_target verification")
            changes, head_sha, repository = candidate_from_pull_request(
                args.event,
                args.repo_root,
                token,
            )
            report = verify_candidate(
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
