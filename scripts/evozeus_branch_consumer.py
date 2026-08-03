#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE = "coevolve_target_skillware_consumer"
PROVENANCE_SCHEMA = "evozeus.coevolve.contributor-branch-snapshot-provenance.v1"
CONTRACT_ID = "evozeus.contributor_branch"
CONTRACT_VERSION = "1.3.1"
CORE_REVISION = "11ef28eed715a46d0dfc35bf443b64701a970a16"
CONTRACT_SHA256 = "208035f76ba3adbb774a5381cfec898b6501cccd91166856acbae2600b796f07"
PLANNER_SHA256 = "b6b14ffaae910d5346bd35f2ae1d9e672a10f83e92629286b8c7e35d922dcb1e"
CONTRACT_RELATIVE_PATH = Path("contracts/v1/contributor-branch-contract.json")
PROVENANCE_RELATIVE_PATH = Path("contracts/v1/contributor-branch-provenance.json")
PLANNER_RELATIVE_PATH = Path("scripts/evozeus-branch-preflight.mjs")
CONTRACT_CANONICAL_URL = "https://github.com/MetaInFLow/EvoZeus/blob/main/contracts/v1/contributor-branch-contract.json"
CONTRACT_IMMUTABLE_URL = f"https://github.com/MetaInFLow/EvoZeus/blob/{CORE_REVISION}/contracts/v1/contributor-branch-contract.json"
PLANNER_CANONICAL_URL = "https://github.com/MetaInFLow/EvoZeus/blob/main/scripts/evozeus-branch-preflight.mjs"
PLANNER_IMMUTABLE_URL = f"https://github.com/MetaInFLow/EvoZeus/blob/{CORE_REVISION}/scripts/evozeus-branch-preflight.mjs"
DEFAULT_LEDGER_RELATIVE_PATH = Path(".evozeus/coevolve/branch-plans")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RESUME_KEY_RE = re.compile(r"^branch_v1_[0-9a-f]{24}$")


class ConsumerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


MAX_MANAGED_ASSET_BYTES = 8 * 1024 * 1024


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsumerError("invalid_json", f"invalid managed JSON: {path.name}") from exc
    if not isinstance(data, dict):
        raise ConsumerError("invalid_json", f"managed JSON must be an object: {path.name}")
    return data


def read_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConsumerError("invalid_json", f"invalid managed JSON: {label}") from exc
    if not isinstance(data, dict):
        raise ConsumerError("invalid_json", f"managed JSON must be an object: {label}")
    return data


def managed_asset_root() -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    if (repository_root / CONTRACT_RELATIVE_PATH).is_file():
        return repository_root
    template_root = repository_root / "templates" / "target"
    if (template_root / CONTRACT_RELATIVE_PATH).is_file():
        return template_root
    raise ConsumerError("snapshot_missing", "managed contributor branch snapshot is missing")


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if nofollow == 0 or directory == 0:
        raise ConsumerError(
            "snapshot_unsafe",
            "managed snapshot loading requires O_NOFOLLOW and O_DIRECTORY",
        )
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | directory


def _file_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise ConsumerError(
            "snapshot_unsafe",
            "managed snapshot loading requires O_NOFOLLOW",
        )
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_child_directory(parent_fd: int, name: str, label: str) -> int:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise ConsumerError("snapshot_missing", f"managed snapshot directory is missing: {label}") from exc
    if stat.S_ISLNK(named.st_mode):
        raise ConsumerError("snapshot_symlink", "managed snapshot paths cannot contain symlinks")
    if not stat.S_ISDIR(named.st_mode):
        raise ConsumerError("snapshot_missing", f"managed snapshot path is not a directory: {label}")
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise ConsumerError("snapshot_symlink", "managed snapshot directory changed while opening") from exc
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(named):
        os.close(descriptor)
        raise ConsumerError("snapshot_changed", "managed snapshot directory changed while opening")
    return descriptor


def _open_absolute_directory(path: Path) -> tuple[int, tuple[int, int]]:
    absolute = absolute_path(path)
    anchor = absolute.anchor or os.sep
    try:
        descriptor = os.open(anchor, _directory_flags())
    except OSError as exc:
        raise ConsumerError("snapshot_missing", "managed snapshot asset root is unavailable") from exc
    try:
        for index, part in enumerate(absolute.parts[1:], start=1):
            child = _open_child_directory(
                descriptor,
                part,
                "/".join(absolute.parts[1:index + 1]),
            )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        return descriptor, _identity(metadata)
    except Exception:
        os.close(descriptor)
        raise


def _open_snapshot_parent(
    root_fd: int,
    relative_path: Path,
    directory_identities: dict[tuple[str, ...], tuple[int, int]],
) -> int:
    descriptor = os.dup(root_fd)
    traversed: list[str] = []
    try:
        for part in relative_path.parts[:-1]:
            if part in {"", ".", ".."}:
                raise ConsumerError("snapshot_escape", "managed snapshot path is invalid")
            traversed.append(part)
            child = _open_child_directory(descriptor, part, "/".join(traversed))
            identity = _identity(os.fstat(child))
            key = tuple(traversed)
            previous = directory_identities.setdefault(key, identity)
            if previous != identity:
                os.close(child)
                raise ConsumerError("snapshot_changed", "managed snapshot directory identity changed")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _capture_managed_file(
    root_fd: int,
    relative_path: Path,
    directory_identities: dict[tuple[str, ...], tuple[int, int]],
) -> bytes:
    if relative_path.is_absolute() or not relative_path.parts:
        raise ConsumerError("snapshot_escape", "managed snapshot path is invalid")
    parent_fd = _open_snapshot_parent(root_fd, relative_path, directory_identities)
    label = relative_path.as_posix()
    try:
        filename = relative_path.parts[-1]
        try:
            named = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise ConsumerError("snapshot_missing", f"managed snapshot file is missing: {label}") from exc
        if stat.S_ISLNK(named.st_mode):
            raise ConsumerError("snapshot_symlink", "managed snapshot paths cannot contain symlinks")
        if not stat.S_ISREG(named.st_mode):
            raise ConsumerError("snapshot_missing", f"managed snapshot must be a regular file: {label}")
        try:
            descriptor = os.open(filename, _file_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise ConsumerError("snapshot_symlink", f"managed snapshot file changed while opening: {label}") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or _identity(metadata) != _identity(named)
                or metadata.st_size > MAX_MANAGED_ASSET_BYTES
            ):
                raise ConsumerError("snapshot_changed", f"managed snapshot file identity is invalid: {label}")
            payload = bytearray()
            while len(payload) < metadata.st_size:
                chunk = os.read(descriptor, min(1024 * 1024, metadata.st_size - len(payload)))
                if not chunk:
                    raise ConsumerError("snapshot_changed", f"managed snapshot file changed while reading: {label}")
                payload.extend(chunk)
            if os.read(descriptor, 1):
                raise ConsumerError("snapshot_changed", f"managed snapshot file grew while reading: {label}")
            final_metadata = os.fstat(descriptor)
            if _stable_file_identity(metadata) != _stable_file_identity(final_metadata):
                raise ConsumerError("snapshot_changed", f"managed snapshot file changed while reading: {label}")
            return bytes(payload)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _verify_snapshot_directory_identities(
    root_fd: int,
    root_path: Path,
    root_identity: tuple[int, int],
    directory_identities: dict[tuple[str, ...], tuple[int, int]],
) -> None:
    for parts, expected in sorted(directory_identities.items(), key=lambda item: len(item[0])):
        descriptor = _open_snapshot_parent(
            root_fd,
            Path(*parts, "_identity_probe"),
            directory_identities,
        )
        try:
            actual = _identity(os.fstat(descriptor))
        finally:
            os.close(descriptor)
        if actual != expected:
            raise ConsumerError("snapshot_changed", "managed snapshot directory identity changed")
    named_descriptor, named_identity = _open_absolute_directory(root_path)
    os.close(named_descriptor)
    if named_identity != root_identity:
        raise ConsumerError("snapshot_changed", "managed snapshot asset root identity changed")


def verify_managed_snapshot(asset_root: Path | None = None) -> dict[str, Any]:
    root = absolute_path(asset_root or managed_asset_root())
    root_fd, root_identity = _open_absolute_directory(root)
    directory_identities: dict[tuple[str, ...], tuple[int, int]] = {}
    try:
        contract_bytes = _capture_managed_file(
            root_fd,
            CONTRACT_RELATIVE_PATH,
            directory_identities,
        )
        provenance_bytes = _capture_managed_file(
            root_fd,
            PROVENANCE_RELATIVE_PATH,
            directory_identities,
        )
        planner_bytes = _capture_managed_file(
            root_fd,
            PLANNER_RELATIVE_PATH,
            directory_identities,
        )
        _verify_snapshot_directory_identities(
            root_fd,
            root,
            root_identity,
            directory_identities,
        )
    finally:
        os.close(root_fd)

    provenance = read_json_bytes(
        provenance_bytes,
        PROVENANCE_RELATIVE_PATH.name,
    )
    contract = read_json_bytes(contract_bytes, CONTRACT_RELATIVE_PATH.name)
    if provenance.get("schema_version") != PROVENANCE_SCHEMA:
        raise ConsumerError("provenance_incompatible", "unsupported snapshot provenance schema")
    if provenance.get("source_revision") != CORE_REVISION:
        raise ConsumerError("provenance_incompatible", "Core snapshot revision does not match the pinned consumer")
    if provenance.get("runtime_network_fetch") is not False:
        raise ConsumerError("provenance_incompatible", "offline snapshot must disable runtime contract download")
    if (
        provenance.get("snapshot_kind") != "generated_offline_copy"
        or provenance.get("source_repository") != "MetaInFLow/EvoZeus"
        or provenance.get("source_pull_request") != "https://github.com/MetaInFLow/EvoZeus/pull/47"
        or provenance.get("contract", {}).get("canonical_id_url") != CONTRACT_CANONICAL_URL
        or provenance.get("contract", {}).get("immutable_source_url") != CONTRACT_IMMUTABLE_URL
        or provenance.get("contract", {}).get("snapshot_path")
        != ".evozeus-wrapper/contracts/v1/contributor-branch-contract.json"
        or provenance.get("planner", {}).get("canonical_url") != PLANNER_CANONICAL_URL
        or provenance.get("planner", {}).get("immutable_source_url") != PLANNER_IMMUTABLE_URL
        or provenance.get("planner", {}).get("snapshot_path")
        != ".evozeus-wrapper/scripts/evozeus-branch-preflight.mjs"
    ):
        raise ConsumerError("provenance_incompatible", "Core snapshot provenance does not match the pinned source")
    if contract.get("contract") != CONTRACT_ID or contract.get("version") != CONTRACT_VERSION:
        raise ConsumerError("contract_incompatible", "unsupported contributor branch contract identity or version")
    if contract.get("schema_version") != "v1" or contract.get("$id") != provenance.get("contract", {}).get("canonical_id_url"):
        raise ConsumerError("contract_incompatible", "contract schema or canonical id does not match provenance")

    contract_digest = hashlib.sha256(contract_bytes).hexdigest()
    planner_digest = hashlib.sha256(planner_bytes).hexdigest()
    if contract_digest != CONTRACT_SHA256 or contract_digest != provenance.get("contract", {}).get("sha256"):
        raise ConsumerError("contract_digest_mismatch", "Core contributor branch contract digest mismatch")
    if planner_digest != PLANNER_SHA256 or planner_digest != provenance.get("planner", {}).get("sha256"):
        raise ConsumerError("planner_digest_mismatch", "Core contributor branch planner digest mismatch")
    return {
        "asset_root": root,
        "contract_path": root / CONTRACT_RELATIVE_PATH,
        "planner_path": root / PLANNER_RELATIVE_PATH,
        "provenance_path": root / PROVENANCE_RELATIVE_PATH,
        "contract_bytes": contract_bytes,
        "planner_bytes": planner_bytes,
        "provenance_bytes": provenance_bytes,
        "provenance": provenance,
        "contract": contract,
    }


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def canonical_candidate_path(path: Path) -> Path:
    try:
        return absolute_path(path).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConsumerError("ledger_path_invalid", "ledger path cannot be resolved safely") from exc


def validate_ledger_root_location(plan: dict[str, Any], ledger_root: Path) -> None:
    root = canonical_candidate_path(ledger_root)
    worktree = plan.get("worktree", {})
    protected_paths = (
        plan.get("repo", {}).get("path"),
        worktree.get("current_repo_path"),
        worktree.get("canonical_checkout_path"),
        worktree.get("path"),
    )
    for raw_path in protected_paths:
        if not isinstance(raw_path, str) or not raw_path:
            raise ConsumerError("planner_output_invalid", "Core planner omitted a protected local path")
        protected = canonical_candidate_path(Path(raw_path))
        try:
            root.relative_to(protected)
        except ValueError:
            continue
        raise ConsumerError(
            "ledger_location_unsafe",
            "branch ledger must stay outside repository and contribution worktree paths",
        )


def valid_repo_slug(repo: object) -> bool:
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        return False
    owner, name = repo.split("/", 1)
    return owner not in {".", ".."} and name not in {".", ".."}


def reject_symlink_components(path: Path) -> None:
    path = absolute_path(path)
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ConsumerError("ledger_symlink", "ledger paths cannot contain symlinks")
        if cursor.exists() and not cursor.is_dir() and cursor != path:
            raise ConsumerError("ledger_path_invalid", "ledger parent must be a directory")


def require_private_directory(path: Path, create: bool) -> None:
    path = absolute_path(path)
    reject_symlink_components(path)
    if not path.exists():
        if not create:
            raise ConsumerError("ledger_missing", "ledger directory does not exist")
        missing: list[Path] = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        reject_symlink_components(cursor)
        for directory in reversed(missing):
            os.mkdir(directory, 0o700)
    reject_symlink_components(path)
    if not path.is_dir():
        raise ConsumerError("ledger_path_invalid", "ledger path must be a directory")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConsumerError("ledger_permissions", "ledger directories must use owner-only permissions")


def ledger_path_for(plan: dict[str, Any], ledger_root: Path) -> Path:
    repo = plan.get("repo", {}).get("canonical")
    resume_key = plan.get("resume", {}).get("key")
    if not valid_repo_slug(repo):
        raise ConsumerError("ledger_repo_invalid", "ledger repo must use a strict OWNER/REPO slug")
    if not isinstance(resume_key, str) or not RESUME_KEY_RE.fullmatch(resume_key):
        raise ConsumerError("ledger_key_invalid", "ledger resume key does not match the v1 contract")
    owner, name = str(repo).split("/", 1)
    return absolute_path(ledger_root) / owner / name / f"{resume_key}.json"


def validate_resume_plan_path(resume_plan: Path, ledger_root: Path) -> Path:
    path = absolute_path(resume_plan)
    root = absolute_path(ledger_root)
    require_private_directory(root, create=False)
    reject_symlink_components(path)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ConsumerError("resume_path_outside_ledger", "resume plan must come from the Harness ledger") from exc
    if (
        len(relative.parts) != 3
        or not valid_repo_slug(f"{relative.parts[0]}/{relative.parts[1]}")
        or relative.suffix != ".json"
        or not RESUME_KEY_RE.fullmatch(relative.stem)
    ):
        raise ConsumerError("resume_path_invalid", "resume plan path does not match OWNER/REPO/<resume-key>.json")
    if not path.is_file():
        raise ConsumerError("resume_plan_missing", "resume plan is missing from the Harness ledger")
    cursor = path.parent
    while True:
        require_private_directory(cursor, create=False)
        if cursor == root:
            break
        if root not in cursor.parents:
            raise ConsumerError("resume_path_outside_ledger", "resume plan must come from the Harness ledger")
        cursor = cursor.parent
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConsumerError("ledger_permissions", "resume plan must use owner-only permissions")
    return path


def public_safe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    safe = copy.deepcopy(plan)
    safe.get("repo", {}).pop("path", None)
    worktree = safe.get("worktree", {})
    for field in ("path", "current_repo_path", "canonical_checkout_path"):
        worktree.pop(field, None)
    for checkout_name in ("current_checkout", "canonical_checkout", "requested_checkout"):
        checkout = worktree.get(checkout_name)
        if isinstance(checkout, dict):
            checkout.pop("top_level", None)
            checkout.pop("common_dir", None)
    safe["ledger"] = {
        "schema_version": "evozeus.coevolve.branch-ledger.v1",
        "resume_key": safe.get("resume", {}).get("key"),
        "storage": "local_private_runtime",
        "path_redacted": True,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    return safe


def compute_resume_key(
    *,
    profile: str,
    repo: str,
    base_ref: str,
    issue: str,
    actor: str,
    permission: str,
    purpose_type: str,
    component: str,
    summary: str,
) -> str:
    fields = (
        profile,
        repo.lower(),
        base_ref,
        issue,
        actor.lower(),
        permission,
        purpose_type,
        component,
        summary,
    )
    digest = hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()[:24]
    return f"branch_v1_{digest}"


def public_pr_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    evidence = plan.get("permission_evidence", {})
    repository_evidence = evidence.get("repository", {})
    purpose = plan.get("purpose", {})
    issue = plan.get("issue", {}).get("reference")
    actor = plan.get("actor", {}).get("id")
    permission = plan.get("permission_path", {}).get("resolved")
    expected_resume_key = compute_resume_key(
        profile=plan.get("profile"),
        repo=plan.get("repo", {}).get("canonical"),
        base_ref=plan.get("base", {}).get("ref"),
        issue=issue,
        actor=actor,
        permission=permission,
        purpose_type=purpose.get("type"),
        component=purpose.get("component"),
        summary=purpose.get("summary"),
    )
    if plan.get("resume", {}).get("key") != expected_resume_key:
        raise ConsumerError("planner_output_invalid", "Core planner resume key does not match its public identity fields")
    return {
        "schema_version": "evozeus.coevolve.branch-pr-metadata.v1",
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "sha256": CONTRACT_SHA256,
            "source_revision": CORE_REVISION,
        },
        "resume_key": plan.get("resume", {}).get("key"),
        "profile": plan.get("profile"),
        "purpose": purpose,
        "repo": plan.get("repo", {}).get("canonical"),
        "base": plan.get("base"),
        "branch": plan.get("branch", {}).get("target"),
        "issue": issue,
        "actor": {
            "id": actor,
            "verified": plan.get("actor", {}).get("verified"),
        },
        "permission": permission,
        "planning_permission_evidence": {
            "source": evidence.get("source"),
            "checked_at": evidence.get("checked_at"),
            "viewer_permission": repository_evidence.get("viewer_permission"),
            "fork_allowed": repository_evidence.get("fork_allowed"),
        },
    }


def write_ledger_plan(path: Path, plan: dict[str, Any]) -> None:
    ledger_root = path.parents[2]
    require_private_directory(ledger_root, create=True)
    require_private_directory(path.parent.parent, create=True)
    require_private_directory(path.parent, create=True)
    if path.is_symlink():
        raise ConsumerError("ledger_symlink", "ledger file cannot be a symlink")
    if path.exists():
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ConsumerError("ledger_permissions", "existing ledger file must be private and regular")
        existing = read_json(path)
        identity_fields = (
            existing.get("resume", {}).get("key") == plan.get("resume", {}).get("key"),
            existing.get("repo", {}).get("canonical") == plan.get("repo", {}).get("canonical"),
            existing.get("actor", {}).get("id") == plan.get("actor", {}).get("id"),
            existing.get("base", {}).get("ref") == plan.get("base", {}).get("ref"),
            existing.get("base", {}).get("commit") == plan.get("base", {}).get("commit"),
            existing.get("branch", {}).get("target") == plan.get("branch", {}).get("target"),
            existing.get("permission_path", {}).get("resolved")
            == plan.get("permission_path", {}).get("resolved"),
        )
        if not all(identity_fields):
            raise ConsumerError("ledger_collision", "existing ledger ownership metadata does not match")

    payload = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    temporary = path.parent / f".{path.stem}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_core_planner(
    options: dict[str, str | bool | None],
    *,
    ledger_root: Path,
    approve_save_plan: bool,
    asset_root: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 30,
) -> tuple[dict[str, Any], int]:
    assets = verify_managed_snapshot(asset_root)
    node = shutil.which("node", path=(env or os.environ).get("PATH"))
    if node is None:
        raise ConsumerError("dependency_missing", "node is required for live branch preflight")

    if options.get("profile") != PROFILE:
        raise ConsumerError("profile_incompatible", f"CoEvolve consumer requires profile {PROFILE}")
    resume_plan = options.get("resume_plan")
    if resume_plan:
        options = dict(options)
        options["resume_plan"] = str(validate_resume_plan_path(Path(resume_plan), ledger_root))

    planner_directory = Path(
        tempfile.mkdtemp(
            prefix="evozeus-contributor-planner-",
            dir=os.path.realpath("/tmp"),
        )
    )
    os.chmod(planner_directory, 0o700)
    scripts_copy = planner_directory / "scripts"
    contracts_copy = planner_directory / "contracts" / "v1"
    scripts_copy.mkdir(mode=0o700)
    (planner_directory / "contracts").mkdir(mode=0o700)
    contracts_copy.mkdir(mode=0o700)
    planner_copy = scripts_copy / "evozeus-branch-preflight.mjs"
    contract_copy = contracts_copy / "contributor-branch-contract.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        os.rmdir(planner_directory)
        raise ConsumerError(
            "snapshot_unsafe",
            "captured planner execution requires O_NOFOLLOW",
        )
    try:
        for destination, payload in (
            (planner_copy, assets["planner_bytes"]),
            (contract_copy, assets["contract_bytes"]),
        ):
            descriptor = os.open(destination, flags | nofollow, 0o600)
            try:
                offset = 0
                while offset < len(payload):
                    offset += os.write(descriptor, payload[offset:])
                os.fsync(descriptor)
                os.fchmod(descriptor, 0o400)
            finally:
                os.close(descriptor)
    except Exception:
        shutil.rmtree(planner_directory)
        raise

    arguments = [node, str(planner_copy), "plan"]
    for key, value in options.items():
        if value is None or value is False:
            continue
        arguments.append(f"--{key.replace('_', '-')}")
        if not isinstance(value, bool):
            arguments.append(str(value))
    arguments.append("--json")
    try:
        try:
            result = subprocess.run(
                arguments,
                text=True,
                capture_output=True,
                env=env,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConsumerError(
                "planner_timeout",
                "Core branch planner exceeded its execution deadline",
            ) from exc
    finally:
        shutil.rmtree(planner_directory)
    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConsumerError("planner_output_invalid", "Core planner did not return valid JSON") from exc
    if not isinstance(plan, dict):
        raise ConsumerError("planner_output_invalid", "Core planner output must be an object")
    if result.returncode not in {0, 2}:
        raise ConsumerError("planner_failed", "Core planner failed before producing a branch decision")
    blockers = plan.get("blockers")
    if not isinstance(blockers, list):
        raise ConsumerError("planner_output_invalid", "Core planner blockers must be a list")
    if (result.returncode == 0) != (len(blockers) == 0):
        raise ConsumerError(
            "planner_output_invalid",
            "Core planner exit status does not match its blocker decision",
        )

    provenance = assets["provenance"]
    plan["consumer"] = {
        "schema_version": "evozeus.coevolve.contributor-branch-consumer.v1",
        "canonical_contract_url": provenance["contract"]["canonical_id_url"],
        "immutable_contract_url": provenance["contract"]["immutable_source_url"],
        "source_revision": CORE_REVISION,
        "contract_sha256": CONTRACT_SHA256,
        "planner_sha256": PLANNER_SHA256,
        "runtime_network_fetch": False,
        "permission_authority": "core_planner_live_github_evidence",
    }
    plan["pr_metadata"] = None if blockers else public_pr_metadata(plan)
    ledger_path = ledger_path_for(plan, ledger_root)
    plan["ledger"] = {
        "schema_version": "evozeus.coevolve.branch-ledger.v1",
        "path": str(ledger_path),
        "status": "not_saved",
        "writes": False,
        "approval_required": "--approve-save-plan",
    }
    plan["consumer_operation"] = {"writes": False, "scope": "read_only_plan"}

    if resume_plan:
        validate_ledger_root_location(plan, ledger_root)
    if approve_save_plan:
        if blockers:
            plan["ledger"]["status"] = "blocked"
        else:
            validate_ledger_root_location(plan, ledger_root)
            safe_plan = public_safe_plan(plan)
            write_ledger_plan(ledger_path, safe_plan)
            plan["ledger"].update({"status": "saved", "writes": True})
            plan["consumer_operation"] = {"writes": True, "scope": "local_private_ledger_only"}
    return plan, (2 if blockers else 0)


def error_report(error: ConsumerError) -> dict[str, Any]:
    return {
        "schema_version": "evozeus.coevolve.contributor-branch-consumer.v1",
        "stage": "contributor_branch_preflight",
        "blockers": [{"code": error.code, "message": str(error)}],
        "consumer_operation": {"writes": False, "scope": "blocked"},
        "writes": False,
    }


def snapshot_report() -> dict[str, Any]:
    assets = verify_managed_snapshot()
    provenance = assets["provenance"]
    return {
        "schema_version": "evozeus.coevolve.contributor-branch-consumer.v1",
        "stage": "contributor_branch_snapshot",
        "status": "verified",
        "source_revision": provenance["source_revision"],
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "sha256": CONTRACT_SHA256,
            "canonical_id_url": provenance["contract"]["canonical_id_url"],
        },
        "planner": {"sha256": PLANNER_SHA256},
        "runtime_network_fetch": False,
        "writes": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume the pinned EvoZeus contributor branch contract.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-snapshot", help="Verify the pinned offline Core snapshot.")
    verify.add_argument("--json", action="store_true")
    plan = subparsers.add_parser("plan", help="Run Core branch preflight and optionally save its plan.")
    plan.add_argument("--profile", default=PROFILE, choices=[PROFILE])
    for name in ("repo", "repo-path", "base", "issue", "actor", "type", "component", "summary", "permission", "worktree"):
        plan.add_argument(f"--{name}", required=True)
    plan.add_argument("--date")
    plan.add_argument("--resume-plan")
    plan.add_argument("--reconfirm-owner", action="store_true")
    plan.add_argument("--approve-save-plan", action="store_true")
    plan.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify-snapshot":
        try:
            report, returncode = snapshot_report(), 0
        except ConsumerError as error:
            report, returncode = error_report(error), 2
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return returncode

    ledger_root = Path.home() / DEFAULT_LEDGER_RELATIVE_PATH
    option_names = ("profile", "repo", "repo_path", "base", "issue", "actor", "type", "component", "summary", "permission", "worktree", "date", "resume_plan", "reconfirm_owner")
    options = {name: getattr(args, name) for name in option_names}
    try:
        report, returncode = run_core_planner(
            options,
            ledger_root=ledger_root,
            approve_save_plan=args.approve_save_plan,
        )
    except ConsumerError as error:
        report, returncode = error_report(error), 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
