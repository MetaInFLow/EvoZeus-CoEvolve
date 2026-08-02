#!/usr/bin/env python3
"""Verify data-only official Harness upgrade profiles from trusted base code."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
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
VERIFIER_REL = "scripts/evozeus_official_upgrade_verify.py"
WORKFLOW_REL = ".github/workflows/evozeus-official-upgrade-profile.yml"
BUNDLE_PREFIX = "contracts/v1/"
ALLOWED_OPERATION_TYPES = {
    "create_exact",
    "replace_exact",
    "managed_block_merge",
    "manifest_patch",
}
ALLOWED_BLOB_MODES = {"100644", "100755"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
GIT_OID_PATTERN = re.compile(r"[0-9a-f]{40}")
SEMVER_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
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

    try:
        parsed = json.loads(value.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label} is invalid UTF-8 JSON: {exc}") from exc
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
    source_release = source.get("required_release")
    if source_release is not None:
        _semver(source_release, "target closure required release")
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
                materialization.get("policy") != "render_with_install_receipt"
                or materialization.get("without_receipt") != "preserve_byte_exact"
                or materialization.get("migration_policy") != "receipt_gated_preserve_exact"
            ):
                raise VerificationError(
                    f"rendered closure path lacks receipt-gated preservation: {target_path}"
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
                artifact_bytes = store.read_bytes(artifact_path)
                if re.search(rb"\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b", artifact_bytes):
                    raise VerificationError(f"generated migration ledger contains a date: {target_path}")
                if b"plan_sha256" in artifact_bytes or b"closure_sha256" in artifact_bytes:
                    raise VerificationError(f"generated migration ledger is self-referential: {target_path}")
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
            operation = "managed_block_merge"
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
    for field in (
        "target_wrapper_from",
        "target_wrapper_to",
        "artifact_release_from",
        "artifact_release_to",
    ):
        _semver(release_axis.get(field), f"official upgrade profile {field}")
    if release_axis.get("artifact_to_binding") != "contract_bundle.source_revision":
        raise VerificationError("official upgrade profile release binding is invalid")
    if release_axis.get("target_wrapper_from") != from_closure["state"].get(
        "target_wrapper_version"
    ) or release_axis.get("target_wrapper_to") != to_closure["state"].get(
        "target_wrapper_version"
    ):
        raise VerificationError("official upgrade profile target release axes disagree")
    if to_closure["source"].get("required_release") != release_axis.get(
        "artifact_release_to"
    ):
        raise VerificationError("official upgrade profile artifact release is not closure-bound")
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
            else ("manifest:" if expected_type == "manifest_patch" else "merge:") + target_path
        )
        if change_id != expected_change_id:
            raise VerificationError(f"profile change_id is not canonical: {target_path}")
        if expected_type in {"create_exact", "replace_exact"}:
            _verify_exact_operation(store, operation, expected_type, before, after)
        elif expected_type == "manifest_patch":
            if before is None:
                raise VerificationError("manifest_patch cannot create a manifest")
            _verify_manifest_patch(operation, before, after)
        else:
            raise VerificationError(
                "managed_block_merge requires a versioned block receipt profile; "
                "this generic profile does not declare one"
            )
    if operation_paths != set(changes):
        missing = sorted(set(changes) - operation_paths)
        raise VerificationError("closure diff lacks profile operations: " + ", ".join(missing))
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
        if item.get("policy") != "receipt_gated_preserve_exact":
            raise VerificationError(f"rendered surface policy is unsafe: {path}")
        if item.get("version_fact_source") != ".evozeus-wrapper/wrapper.json":
            raise VerificationError(f"rendered surface version source is invalid: {path}")
        if path in deferred_paths:
            raise VerificationError(f"deferred rendered surface is duplicated: {path}")
        deferred_paths.add(path)
    if deferred_paths != rendered_unchanged:
        raise VerificationError(
            "profile must enumerate every receipt-gated unchanged rendered surface"
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
    if not any(profile["_verified_to_path"] == current_path for profile in profiles):
        raise VerificationError("current Harness closure has no verified incoming profile")
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
    prefixes: set[str] = set()
    current = load_pointer(
        base,
        HISTORY_CURRENT_REL,
        "using-evozeus-harness-current-closure",
    )
    profiles = load_pointer(
        base,
        PROFILES_CURRENT_REL,
        "official-upgrade-current-profiles",
    )
    closure_paths = {
        _bundle_relative(entry["path"], "base closure history path")
        for entry in current
    }
    for entry in profiles:
        profile_path = _bundle_relative(entry["path"], "base profile path")
        profile = _json_file(base, profile_path, "base official upgrade profile")
        for field in ("from_closure", "to_closure"):
            closure_path, _ = _binding_path(profile.get(field), f"base profile {field}")
            closure_paths.add(closure_path)
    for closure_path in closure_paths:
        prefixes.add(PurePosixPath(closure_path).parent.as_posix() + "/")
    return prefixes


def _protected_candidate_change(
    base: FilesystemStore,
    path: str,
    immutable_history_prefixes: set[str],
) -> bool:
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


def verify_candidate(
    base: FilesystemStore,
    changes: dict[str, CandidateBlob],
    *,
    head_sha: str,
) -> dict[str, Any]:
    if GIT_OID_PATTERN.fullmatch(head_sha) is None:
        raise VerificationError("candidate head SHA is invalid")
    base_report = verify_catalog(base)
    immutable_history_prefixes = _immutable_history_prefixes(base)
    for path, change in changes.items():
        _safe_relative(path, "candidate changed path")
        if _protected_candidate_change(base, path, immutable_history_prefixes):
            raise VerificationError(f"candidate modifies trusted base authority or history: {path}")
        if change.status != "deleted" and (
            change.mode not in ALLOWED_BLOB_MODES or change.object_type != "blob"
        ):
            raise VerificationError(
                f"candidate adds a symlink, submodule or unsupported object: {path}"
            )
    candidate = CandidateStore(base, changes)
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
    if base.exists(candidate_closure_path):
        raise VerificationError("candidate current closure must use a new immutable version path")
    candidate_closure, candidate_entries = load_closure(
        candidate,
        candidate_closure_path,
        expected_sha256=candidate_history["sha256"],
    )
    if candidate_closure["source"]["construction_revision"] != head_sha:
        raise VerificationError("candidate closure source revision does not equal PR head")

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
    if candidate_profiles[: len(base_profiles)] != base_profiles:
        raise VerificationError("candidate profile pointer rewrites existing profile history")
    new_profiles = candidate_profiles[len(base_profiles) :]
    if not new_profiles:
        raise VerificationError("candidate current closure lacks a new official upgrade profile")
    base_closure_path = _bundle_relative(base_history["path"], "base current closure path")
    for entry in new_profiles:
        profile_path = _bundle_relative(entry["path"], "candidate profile path")
        if base.exists(profile_path):
            raise VerificationError(f"candidate profile must use a new immutable path: {profile_path}")
        profile = load_profile(
            candidate,
            profile_path,
            load_protocol(base),
            expected_sha256=entry["sha256"],
        )
        if profile["_verified_from_path"] != base_closure_path:
            raise VerificationError("candidate profile does not start at the base current closure")
        if profile["_verified_to_path"] != candidate_closure_path:
            raise VerificationError("candidate profile does not end at the candidate current closure")

    bound_sources: set[str] = set()
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
        bound_sources.add(source_path)
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


def candidate_from_pull_request(
    event_path: Path,
    repo_root: Path,
    token: str,
) -> tuple[dict[str, CandidateBlob], str]:
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
    return changes, head_sha


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
            changes, head_sha = candidate_from_pull_request(
                args.event,
                args.repo_root,
                token,
            )
            report = verify_candidate(base, changes, head_sha=head_sha)
    except (OSError, VerificationError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
