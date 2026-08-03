#!/usr/bin/env python3
from __future__ import annotations

import copy
import ctypes
import errno
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


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
SNAPSHOT_ANCHOR_SCHEMA_VERSION = (
    "evozeus.coevolve.harness-migration-snapshot-anchor.v1"
)
SNAPSHOT_TRANSACTION_SCHEMA_VERSION = (
    "evozeus.coevolve.harness-migration-transaction.v1"
)
TARGET_BINDING_SCHEMA_VERSION = "evozeus.target-root-binding.v1"
OFFICIAL_SOURCE_REPOSITORY = "MetaInFLow/EvoZeus-CoEvolve"
OFFICIAL_SOURCE_URLS = {
    "https://github.com/MetaInFLow/EvoZeus-CoEvolve.git",
    "git@github.com:MetaInFLow/EvoZeus-CoEvolve.git",
}
OfficialTagResolver = Callable[[str, str], dict[str, str] | None]
CANONICAL_ACTIVATION_CONTRACT = {
    "block_id": "evozeus-harness-entry",
    "marker_version": "v1",
    "begin_marker": "<!-- evozeus-harness-entry:v1 -->",
    "end_marker": "<!-- /evozeus-harness-entry -->",
    "sha256_lf": "078bb2020284fbd6f91c12e46a2c726e64a4f4bbdef0320f4e40adcef26d3cea",
}

OFFICIAL_UPGRADE_PROTOCOL_REL = "migrations/protocols/official-upgrade-protocol-v1.json"
OFFICIAL_UPGRADE_CLOSURE_POINTER_REL = "migrations/history/harness-skill/current.json"
OFFICIAL_UPGRADE_PROFILE_POINTER_REL = "migrations/profiles/current.json"
OFFICIAL_UPGRADE_PROFILE_SCHEMA_REL = "migrations/schemas/official-upgrade-profile-v1.schema.json"
OFFICIAL_UPGRADE_CLOSURE_SCHEMA_REL = "migrations/schemas/target-closure-v1.schema.json"


def _atomic_rename_between_directories(
    source_parent_fd: int,
    source: str,
    destination_parent_fd: int,
    destination: str,
    *,
    exchange: bool,
) -> None:
    """Use a kernel atomic rename primitive or reject the mutation."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        function = getattr(library, "renameat2", None)
        flag = 0x2 if exchange else 0x1  # RENAME_EXCHANGE / RENAME_NOREPLACE
    elif sys.platform == "darwin":
        function = getattr(library, "renameatx_np", None)
        flag = (0x2 if exchange else 0x4) | 0x10  # SWAP / EXCL + NOFOLLOW_ANY
    else:
        function = None
        flag = 0
    if function is None:
        raise ValueError(
            "secure target mutation requires atomic rename exchange/no-replace support"
        )
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        source_parent_fd,
        os.fsencode(source),
        destination_parent_fd,
        os.fsencode(destination),
        flag,
    )
    if result == 0:
        return
    error = ctypes.get_errno() or errno.EIO
    if error in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
        errno.EOPNOTSUPP,
    }:
        raise ValueError(
            "secure target filesystem lacks the required atomic rename primitive"
        )
    raise OSError(
        error,
        os.strerror(error),
        f"{source} -> {destination}",
    )


def _atomic_rename_same_directory(
    parent_fd: int,
    source: str,
    destination: str,
    *,
    exchange: bool,
) -> None:
    _atomic_rename_between_directories(
        parent_fd,
        source,
        parent_fd,
        destination,
        exchange=exchange,
    )


def _atomic_exchange_same_directory(
    parent_fd: int,
    source: str,
    destination: str,
) -> None:
    _atomic_rename_same_directory(
        parent_fd,
        source,
        destination,
        exchange=True,
    )


def _atomic_rename_noreplace_same_directory(
    parent_fd: int,
    source: str,
    destination: str,
) -> None:
    _atomic_rename_same_directory(
        parent_fd,
        source,
        destination,
        exchange=False,
    )


def _atomic_rename_noreplace_between_directories(
    source_parent_fd: int,
    source: str,
    destination_parent_fd: int,
    destination: str,
) -> None:
    _atomic_rename_between_directories(
        source_parent_fd,
        source,
        destination_parent_fd,
        destination,
        exchange=False,
    )


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
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def _strict_json_value(value: Any, label: str) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"invalid {label}: non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _strict_json_value(item, label)
    elif isinstance(value, list):
        for item in value:
            _strict_json_value(item, label)
    return value


def _strict_json_loads(data: str, label: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"invalid {label}: duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid {label}: non-finite JSON number: {value}")

    value = json.loads(
        data,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )
    return _strict_json_value(value, label)


def _target_binding(target: Path) -> dict[str, Any]:
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")
    ):
        raise ValueError("target binding requires POSIX dirfd/O_NOFOLLOW support")
    lexical = Path(os.path.abspath(os.fspath(target.expanduser())))
    try:
        canonical = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"target root cannot be resolved: {lexical}: {exc}") from exc
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(canonical, flags)
    except OSError as exc:
        raise ValueError(f"target root cannot be opened safely: {canonical}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(canonical)
        rebound = lexical.resolve(strict=True)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or rebound != canonical
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError("target root identity changed while binding")
        return {
            "schema_version": TARGET_BINDING_SCHEMA_VERSION,
            "lexical_path": str(lexical),
            "canonical_path": str(canonical),
            "root_st_dev": opened.st_dev,
            "root_st_ino": opened.st_ino,
        }
    finally:
        os.close(descriptor)


def _validated_target_binding(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != (
        TARGET_BINDING_SCHEMA_VERSION
    ):
        raise ValueError("target root binding is missing or unsupported")
    if any(
        not isinstance(value.get(field), str) or not value.get(field)
        for field in ("lexical_path", "canonical_path")
    ) or any(
        not isinstance(value.get(field), int) or isinstance(value.get(field), bool)
        for field in ("root_st_dev", "root_st_ino")
    ):
        raise ValueError("target root binding fields are invalid")
    return dict(value)


def verify_target_binding(target: Path, expected: object) -> dict[str, Any]:
    approved = _validated_target_binding(expected)
    actual = _target_binding(target)
    if actual != approved:
        raise ValueError(
            "target root binding changed after planning: "
            f"expected={approved}; actual={actual}"
        )
    return actual


def _target_inventory(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def visit(directory: Path, relative_root: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise ValueError(f"target inventory cannot read {relative_root}: {exc}") from exc
        for child in children:
            relative = relative_root / child.name
            if not relative_root.parts and child.name == ".git":
                continue
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"target inventory cannot stat {relative}: {exc}") from exc
            item: dict[str, Any] = {
                "path": relative.as_posix(),
                "mode": stat.S_IMODE(metadata.st_mode),
                "st_dev": metadata.st_dev,
                "st_ino": metadata.st_ino,
            }
            if stat.S_ISLNK(metadata.st_mode):
                item.update({"kind": "symlink", "target": os.readlink(child.path)})
            elif stat.S_ISDIR(metadata.st_mode):
                item["kind"] = "directory"
                entries.append(item)
                visit(Path(child.path), relative)
                continue
            elif stat.S_ISREG(metadata.st_mode):
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(child.path, flags)
                    try:
                        opened = os.fstat(descriptor)
                        if (opened.st_dev, opened.st_ino) != (
                            metadata.st_dev,
                            metadata.st_ino,
                        ):
                            raise ValueError(
                                f"target inventory identity changed: {relative}"
                            )
                        data = b""
                        while True:
                            chunk = os.read(descriptor, 1024 * 1024)
                            if not chunk:
                                break
                            data += chunk
                    finally:
                        os.close(descriptor)
                except OSError as exc:
                    raise ValueError(
                        f"target inventory cannot read regular file {relative}: {exc}"
                    ) from exc
                item.update(
                    {
                        "kind": "file",
                        "size": len(data),
                        "sha256": sha256_bytes(data),
                    }
                )
            else:
                item["kind"] = "unsupported"
            entries.append(item)

    visit(root, Path())
    return entries


def target_git_state(target: Path) -> dict[str, Any]:
    binding = _target_binding(target)
    target = Path(binding["canonical_path"])
    head = _git(target, "rev-parse", "HEAD")
    tree = _git(target, "rev-parse", "HEAD^{tree}")
    index = _git_bytes(target, "ls-files", "--stage", "-z")
    status_result = _git_bytes(
        target,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if any(result.returncode != 0 for result in (head, tree, index, status_result)):
        raise ValueError("target Git identity/status cannot be collected")
    inventory = _target_inventory(target)
    state = {
        "target_binding": binding,
        "head_commit": head.stdout.strip(),
        "head_tree_oid": tree.stdout.strip(),
        "index_sha256": f"sha256:{sha256_bytes(index.stdout)}",
        "status_sha256": f"sha256:{sha256_bytes(status_result.stdout)}",
        "status_clean": status_result.stdout == b"",
        "inventory_sha256": f"sha256:{canonical_json_sha256(inventory)}",
        "inventory_entries": len(inventory),
    }
    verify_target_binding(Path(binding["lexical_path"]), binding)
    return state


def verify_target_git_state(target: Path, plan: dict[str, Any]) -> None:
    expected = plan.get("target_git_state")
    if not isinstance(expected, dict):
        raise ValueError("migration plan is missing the complete target Git state")
    actual = target_git_state(target)
    if actual != expected:
        raise ValueError(
            "target Git tree/status/inventory changed after planning: "
            f"expected={expected}; actual={actual}"
        )


def _unplanned_target_inventory(target: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    planned = set(planned_target_paths(plan))
    planned_ancestors: set[str] = set()
    for relative_text in planned:
        parent = _safe_relative_path(relative_text).parent
        while parent.parts:
            planned_ancestors.add(parent.as_posix())
            parent = parent.parent
    excluded = planned | planned_ancestors
    return [
        item
        for item in _target_inventory(target)
        if item.get("path") not in excluded
    ]


def capture_post_apply_baseline(target: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Bind Git identity and every byte outside the approved mutation footprint."""
    requested_target = Path(os.path.abspath(os.fspath(target.expanduser())))
    git_state = target_git_state(requested_target)
    inventory = _unplanned_target_inventory(
        Path(git_state["target_binding"]["canonical_path"]),
        plan,
    )
    return {
        "schema_version": "evozeus.target-post-apply-baseline.v1",
        "target_binding": git_state["target_binding"],
        "head_commit": git_state["head_commit"],
        "head_tree_oid": git_state["head_tree_oid"],
        "index_sha256": git_state["index_sha256"],
        "unplanned_inventory_sha256": (
            f"sha256:{canonical_json_sha256(inventory)}"
        ),
        "unplanned_inventory_entries": len(inventory),
    }


def _git_changed_paths(target: Path) -> list[str]:
    result = _git_bytes(
        target,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if result.returncode != 0:
        raise ValueError("post-apply Git changed set cannot be collected")
    chunks = result.stdout.split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(chunks):
        record = chunks[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise ValueError("post-apply Git changed set has invalid porcelain output")
        status_code = record[:2].decode("ascii", errors="strict")
        paths.add(record[3:].decode("utf-8", errors="surrogateescape"))
        if "R" in status_code or "C" in status_code:
            if index >= len(chunks) or not chunks[index]:
                raise ValueError("post-apply Git rename record is incomplete")
            paths.add(chunks[index].decode("utf-8", errors="surrogateescape"))
            index += 1
    return sorted(paths)


def _approved_changed_paths(plan: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for item in plan.get("write_set", []):
        if (
            item.get("preimage_sha256") != item.get("postimage_sha256")
            or item.get("preimage_mode") != item.get("postimage_mode")
        ):
            paths.add(_safe_relative_path(item.get("path")).as_posix())
    for item in plan.get("delete_set", []):
        paths.add(_safe_relative_path(item.get("path")).as_posix())
    for item in plan.get("move_set", []):
        paths.add(_safe_relative_path(item.get("source")).as_posix())
        paths.add(_safe_relative_path(item.get("destination")).as_posix())
    return sorted(paths)


def verify_post_apply_target_state(target: Path, plan: dict[str, Any]) -> None:
    """Require the final target delta to equal the approved mutation set exactly."""
    requested_target = Path(os.path.abspath(os.fspath(target.expanduser())))
    baseline = plan.get("post_apply_baseline")
    if not isinstance(baseline, dict) or baseline.get("schema_version") != (
        "evozeus.target-post-apply-baseline.v1"
    ):
        raise ValueError("migration plan is missing the post-apply target baseline")
    expected_binding = (plan.get("target_git_state") or {}).get("target_binding")
    binding = verify_target_binding(requested_target, expected_binding)
    target = Path(binding["canonical_path"])
    if baseline.get("target_binding") != expected_binding:
        raise ValueError("post-apply target baseline binding differs from the plan")
    current_git = target_git_state(requested_target)
    for field in ("head_commit", "head_tree_oid", "index_sha256"):
        if current_git.get(field) != baseline.get(field):
            raise ValueError(f"post-apply target {field} changed outside the plan")

    with SecureTargetFS(
        requested_target,
        expected_binding=expected_binding,
    ) as secure_target:
        for item in plan.get("protected_business_surfaces", []):
            current = secure_target.file_state(item.get("path"))
            if current.get("sha256") != item.get("preimage_sha256"):
                raise ValueError(
                    f"post-apply protected business surface changed: {item.get('path')}"
                )
        for item in plan.get("write_set", []):
            current = secure_target.file_state(item.get("path"))
            if (
                current.get("sha256") != item.get("postimage_sha256")
                or (
                    "postimage_mode" in item
                    and current.get("mode") != item.get("postimage_mode")
                )
            ):
                raise ValueError(
                    f"post-apply approved write is missing: {item.get('path')}"
                )
        for item in plan.get("delete_set", []):
            if secure_target.file_state(item.get("path"))["kind"] != "absent":
                raise ValueError(
                    f"post-apply approved delete is incomplete: {item.get('path')}"
                )
        for item in plan.get("move_set", []):
            if secure_target.file_state(item.get("source"))["kind"] != "absent":
                raise ValueError(
                    f"post-apply approved move source remains: {item.get('source')}"
                )
            destination = secure_target.file_state(item.get("destination"))
            expected = item.get("destination_postimage_sha256") or item.get(
                "source_preimage_sha256"
            )
            if destination.get("sha256") != expected:
                raise ValueError(
                    f"post-apply approved move destination differs: {item.get('destination')}"
                )

    inventory = _unplanned_target_inventory(target, plan)
    actual_inventory = f"sha256:{canonical_json_sha256(inventory)}"
    if (
        actual_inventory != baseline.get("unplanned_inventory_sha256")
        or len(inventory) != baseline.get("unplanned_inventory_entries")
    ):
        raise ValueError("post-apply unplanned target inventory changed")
    actual_changed = _git_changed_paths(target)
    expected_changed = _approved_changed_paths(plan)
    if actual_changed != expected_changed:
        raise ValueError(
            "post-apply Git changed set differs from approved mutations: "
            f"expected={expected_changed}; actual={actual_changed}"
        )
    verify_target_binding(requested_target, expected_binding)


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}: expected a JSON object: {path}")
    return value


def _json_bytes_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(data.decode("utf-8"), label)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}: expected a JSON object")
    return value


def _read_owned_snapshot_file(
    secure_snapshot: "SecureSnapshotFS",
    relative: str,
    *,
    mode: int,
    label: str,
) -> bytes:
    state = secure_snapshot.file_state(relative)
    if state.get("kind") == "absent":
        raise ValueError(f"{label} is missing: {relative}")
    if (
        state.get("kind") != "file"
        or state.get("uid") != os.getuid()
        or state.get("mode") != mode
        or not isinstance(state.get("sha256"), str)
    ):
        raise ValueError(f"{label} owner, type, or mode is invalid: {relative}")
    return secure_snapshot.read_exact(relative, state["sha256"])


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


def _official_origin_transport(wrapper_root: Path) -> dict[str, Any]:
    """Reject local/global Git URL rewrites before any official remote access."""
    configured = _git(wrapper_root, "config", "--get-all", "remote.origin.url")
    configured_urls = [line.strip() for line in configured.stdout.splitlines() if line.strip()]
    effective = _git(wrapper_root, "remote", "get-url", "--all", "origin")
    effective_urls = [line.strip() for line in effective.stdout.splitlines() if line.strip()]
    reasons: list[str] = []
    if configured.returncode != 0 or len(configured_urls) != 1:
        reasons.append("source origin must declare exactly one fetch URL")
    elif configured_urls[0] not in OFFICIAL_SOURCE_URLS:
        reasons.append("source origin URL is not an exact official GitHub transport")
    if effective.returncode != 0 or len(effective_urls) != 1:
        reasons.append("source effective origin transport cannot be resolved exactly")
    elif effective_urls[0] not in OFFICIAL_SOURCE_URLS:
        reasons.append("source effective origin transport was rewritten away from GitHub")
    elif configured_urls and effective_urls[0] != configured_urls[0]:
        reasons.append("source effective origin transport differs from configured origin")
    return {
        "configured_urls": configured_urls,
        "effective_urls": effective_urls,
        "verified": not reasons,
        "reasons": reasons,
    }


def _github_api_json(url: str, headers: dict[str, str]) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            if (
                final_url.scheme != "https"
                or final_url.hostname != "api.github.com"
                or final_url.port not in {None, 443}
            ):
                return None
            value = _strict_json_loads(
                response.read().decode("utf-8"),
                "GitHub API response",
            )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        urllib.error.URLError,
    ):
        return None
    return value if isinstance(value, dict) else None


def _resolve_official_remote_tag(
    repository: str,
    revision: str,
) -> dict[str, str] | None:
    """Resolve and peel a fixed official GitHub tag through GitHub's API."""
    if repository != OFFICIAL_SOURCE_REPOSITORY:
        return None
    if not re.fullmatch(r"v\d+\.\d+\.\d+", revision):
        return None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "EvoZeus-CoEvolve-source-trust",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    ref_url = (
        "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve/git/ref/tags/"
        + urllib.parse.quote(revision, safe="")
    )
    ref = _github_api_json(ref_url, headers)
    ref_object = ref.get("object") if isinstance(ref, dict) else None
    if not isinstance(ref_object, dict):
        return None
    ref_oid = ref_object.get("sha")
    object_type = ref_object.get("type")
    peeled_oid = ref_oid
    for _ in range(5):
        if object_type == "commit":
            break
        if object_type != "tag" or not isinstance(peeled_oid, str):
            return None
        tag = _github_api_json(
            "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve/git/tags/"
            + peeled_oid,
            headers,
        )
        tag_object = tag.get("object") if isinstance(tag, dict) else None
        if not isinstance(tag_object, dict):
            return None
        peeled_oid = tag_object.get("sha")
        object_type = tag_object.get("type")
    if (
        object_type != "commit"
        or not isinstance(ref_oid, str)
        or not re.fullmatch(r"[0-9a-f]{40}", ref_oid)
        or not isinstance(peeled_oid, str)
        or not re.fullmatch(r"[0-9a-f]{40}", peeled_oid)
    ):
        return None
    return {
        "provider": "github-api",
        "repository": OFFICIAL_SOURCE_REPOSITORY,
        "tag": revision,
        "ref_oid": ref_oid,
        "peeled_commit_oid": peeled_oid,
    }


def _release_source_trust(
    wrapper_root: Path,
    bundle_manifest: dict[str, Any],
    contract_bytes: bytes,
    contract: dict[str, Any],
    remote_tag_resolver: OfficialTagResolver | None = None,
) -> dict[str, Any]:
    revision = bundle_manifest.get("source_revision")
    reasons: list[str] = []
    transport = _official_origin_transport(wrapper_root)
    reasons.extend(transport["reasons"])
    configured_urls = transport["configured_urls"]
    repository = _repo_from_remote(configured_urls[0]) if len(configured_urls) == 1 else None
    if repository != OFFICIAL_SOURCE_REPOSITORY:
        reasons.append(
            "source repository is not the official EvoZeus-CoEvolve origin"
        )
    if not isinstance(revision, str) or not revision.startswith("v"):
        reasons.append("contract bundle source_revision is not an immutable release tag")
        revision = None

    resolved_commit = None
    local_tag_ref_oid = None
    head_commit = None
    worktree_clean = False
    remote_tag_commit = None
    remote_tag_verified = False
    remote_tag_attestation = None
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
        local_ref = _git(wrapper_root, "rev-parse", f"refs/tags/{revision}")
        if resolved.returncode != 0 or not resolved.stdout.strip():
            reasons.append(f"release tag is unavailable: {revision}")
        else:
            resolved_commit = resolved.stdout.strip()
            if local_ref.returncode != 0 or not re.fullmatch(
                r"[0-9a-f]{40}",
                local_ref.stdout.strip(),
            ):
                reasons.append(f"release tag ref object is unavailable: {revision}")
            else:
                local_tag_ref_oid = local_ref.stdout.strip()
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
                working_manifest_bytes = (
                    wrapper_root / MIGRATION_CONTRACT_BUNDLE_ROOT / "manifest.json"
                ).read_bytes()
                if tagged_manifest_bytes != working_manifest_bytes:
                    reasons.append(
                        "working contract bundle manifest differs from the declared release artifact"
                    )
                try:
                    tagged_manifest = _strict_json_loads(
                        tagged_manifest_bytes.decode("utf-8"),
                        "tagged contract bundle manifest",
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    tagged_manifest = None
                if tagged_manifest != bundle_manifest:
                    reasons.append("release tag contract bundle manifest identity is invalid")
                for entry in bundle_manifest.get("files", []):
                    if not isinstance(entry, dict):
                        reasons.append("contract bundle file entry is invalid")
                        continue
                    entry_path = entry.get("path")
                    expected_entry_sha256 = entry.get("sha256")
                    if not isinstance(entry_path, str) or not _is_plain_sha256(
                        expected_entry_sha256
                    ):
                        reasons.append("contract bundle file identity is incomplete")
                        continue
                    try:
                        entry_relative = _safe_relative_path(
                            entry_path,
                            "contract bundle file path",
                        )
                        working_entry = _safe_file_below(
                            wrapper_root / MIGRATION_CONTRACT_BUNDLE_ROOT,
                            entry_relative,
                            "contract bundle file",
                        )
                    except ValueError as exc:
                        reasons.append(str(exc))
                        continue
                    working_entry_bytes = working_entry.read_bytes()
                    tagged_entry_result = _git_bytes(
                        wrapper_root,
                        "show",
                        f"{revision}:{MIGRATION_CONTRACT_BUNDLE_ROOT}/{entry_relative.as_posix()}",
                    )
                    if sha256_bytes(working_entry_bytes) != expected_entry_sha256:
                        reasons.append(
                            f"working contract bundle file digest mismatch: {entry_path}"
                        )
                    if tagged_entry_result.returncode != 0:
                        reasons.append(
                            f"contract bundle file is absent from release tag: {entry_path}"
                        )
                    elif (
                        tagged_entry_result.stdout != working_entry_bytes
                        or sha256_bytes(tagged_entry_result.stdout) != expected_entry_sha256
                    ):
                        reasons.append(
                            f"contract bundle file is not release-bound: {entry_path}"
                        )
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
                for profile in contract.get("profiles", []):
                    if not isinstance(profile, dict) or profile.get("automatic") is not True:
                        continue
                    release_axis = profile.get("release_axis")
                    if not isinstance(release_axis, dict):
                        continue
                    artifact_source_to = release_axis.get("artifact_source_to")
                    release_to = (
                        artifact_source_to.get("release")
                        if isinstance(artifact_source_to, dict)
                        else None
                    )
                    declared_diff = release_axis.get("managed_diff_paths")
                    adapter_payload = profile.get("adapter_payload") or {}
                    if (
                        release_to != revision
                        or artifact_source_to.get("kind") != "required_release"
                        or artifact_source_to.get("binding")
                        != "contract_bundle.source_revision"
                        or not isinstance(adapter_payload, dict)
                        or adapter_payload.get("authority_source")
                        != "external-hash-bound-official-upgrade-profile"
                    ):
                        reasons.append(
                            f"automatic profile release authority is invalid: {profile.get('profile_id')}"
                        )
                        continue
                    operation_sources = sorted(
                        item.get("source_path")
                        for item in adapter_payload.get("write_sources", [])
                        if isinstance(item, dict)
                        and isinstance(item.get("source_path"), str)
                    )
                    if operation_sources != sorted(declared_diff or []):
                        reasons.append(
                            "official migration operation sources disagree with the "
                            f"managed release set: {profile.get('profile_id')}"
                        )
            if not reasons:
                resolver = remote_tag_resolver or _resolve_official_remote_tag
                remote_tag_attestation = resolver(
                    OFFICIAL_SOURCE_REPOSITORY,
                    revision,
                )
                if not isinstance(remote_tag_attestation, dict):
                    reasons.append("declared release tag is absent from the official origin")
                elif any(
                    remote_tag_attestation.get(field) != expected
                    for field, expected in {
                        "provider": "github-api",
                        "repository": OFFICIAL_SOURCE_REPOSITORY,
                        "tag": revision,
                    }.items()
                ):
                    reasons.append("official release tag attestation identity is invalid")
                else:
                    remote_tag_commit = remote_tag_attestation.get(
                        "peeled_commit_oid"
                    )
                remote_ref_oid = (
                    remote_tag_attestation.get("ref_oid")
                    if isinstance(remote_tag_attestation, dict)
                    else None
                )
                if remote_tag_attestation is not None and (
                    not re.fullmatch(r"[0-9a-f]{40}", str(remote_ref_oid))
                    or not re.fullmatch(r"[0-9a-f]{40}", str(remote_tag_commit))
                ):
                    reasons.append("official release tag attestation commit is invalid")
                elif remote_ref_oid and remote_ref_oid != local_tag_ref_oid:
                    reasons.append(
                        "local release tag ref object differs from the official origin"
                    )
                elif remote_tag_commit and remote_tag_commit != resolved_commit:
                    reasons.append(
                        "local release tag commit differs from the official origin"
                    )
                elif remote_tag_commit:
                    remote_tag_verified = True
    return {
        "status": "trusted_release" if not reasons else "source_unreleased",
        "official_repository": OFFICIAL_SOURCE_REPOSITORY,
        "repository": repository,
        "release_tag": revision,
        "resolved_commit": resolved_commit,
        "local_tag_ref_oid": local_tag_ref_oid,
        "head_commit": head_commit,
        "worktree_clean": worktree_clean,
        "remote_tag_commit": remote_tag_commit,
        "remote_tag_verified": remote_tag_verified,
        "remote_tag_attestation": remote_tag_attestation,
        "remote_transport": transport,
        "tagged_bundle_sha256": tagged_bundle_sha256,
        "source_attestations": source_attestations,
        "reasons": reasons,
    }


def _official_upgrade_profile_compatibility(
    raw_profile: dict[str, Any],
) -> dict[str, Any]:
    """Expose the data profile through the legacy planner shape during v1 rollout."""
    from_closure = raw_profile.get("_verified_from_closure")
    to_closure = raw_profile.get("_verified_to_closure")
    if not isinstance(from_closure, dict) or not isinstance(to_closure, dict):
        raise ValueError("official upgrade profile closures were not verified")
    from_entries = {
        item.get("target_path"): item
        for item in from_closure.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("target_path"), str)
    }
    trusted_preimages: list[dict[str, Any]] = []
    write_sources: list[dict[str, Any]] = []
    for operation in raw_profile.get("operations", []):
        if not isinstance(operation, dict):
            raise ValueError("official upgrade profile operation is invalid")
        operation_type = operation.get("type")
        target_path = operation.get("target_path")
        if operation_type == "replace_exact":
            preimage = operation.get("preimage")
            if not isinstance(preimage, dict):
                raise ValueError("official replace operation lacks a preimage")
            trusted_preimages.append(
                {
                    "artifact_id": "official-closure-preimage:" + str(target_path),
                    "target_path": target_path,
                    "sha256": preimage.get("sha256"),
                    "mode": preimage.get("mode"),
                }
            )
        if operation_type in {"create_exact", "replace_exact"}:
            postimage = operation.get("postimage")
            if not isinstance(postimage, dict):
                raise ValueError("official exact operation lacks a postimage")
            artifact_path = _safe_relative_path(
                postimage.get("artifact_path"),
                "official profile postimage artifact",
            )
            write_sources.append(
                {
                    "target_path": target_path,
                    "source_path": (
                        Path(MIGRATION_CONTRACT_BUNDLE_ROOT) / artifact_path
                    ).as_posix(),
                    "sha256": postimage.get("sha256"),
                }
            )
    from_state = from_closure.get("state")
    to_state = to_closure.get("state")
    if not isinstance(from_state, dict) or not isinstance(to_state, dict):
        raise ValueError("official upgrade closure state is invalid")
    manifest_entry = from_entries.get(".evozeus-wrapper/wrapper.json")
    manifest_state = (
        manifest_entry.get("owned_state") if isinstance(manifest_entry, dict) else None
    )
    if not isinstance(manifest_state, dict):
        raise ValueError("official upgrade from closure lacks manifest state")
    required_manifest_fields = {
        field: manifest_state[field]
        for field in (
            "layout_version",
            "harness_skill_path",
            "harness_skill_version",
            "harness_skill_managed",
        )
        if field in manifest_state
    }
    migration_records = raw_profile.get("migration_records")
    current_migration_record = raw_profile.get("current_migration_record")
    if (
        not isinstance(migration_records, list)
        or not migration_records
        or any(not isinstance(item, str) for item in migration_records)
        or not isinstance(current_migration_record, str)
        or current_migration_record not in migration_records
    ):
        raise ValueError("official upgrade profile migration records were not verified")
    payload = {
        "type": "exact-artifact-and-stable-block",
        "authority_source": "external-hash-bound-official-upgrade-profile",
        "official_profile": {
            "profile_id": raw_profile.get("profile_id"),
            "profile_version": raw_profile.get("profile_version"),
            "path": raw_profile.get("_verified_profile_path"),
            "sha256": raw_profile.get("_verified_profile_sha256"),
            "protocol": raw_profile.get("protocol"),
            "from_closure": raw_profile.get("from_closure"),
            "to_closure": raw_profile.get("to_closure"),
        },
        "operations": copy.deepcopy(raw_profile.get("operations", [])),
        "migration_records": copy.deepcopy(migration_records),
        "current_migration_record": current_migration_record,
        "from_closure_files": copy.deepcopy(from_closure.get("files", [])),
        "write_sources": write_sources,
        "trusted_preimages": trusted_preimages,
        "stable_blocks": [
            {
                "block_id": CANONICAL_ACTIVATION_CONTRACT["block_id"],
                "target_path_source": "manifest.instruction_surface",
                "begin_marker": CANONICAL_ACTIVATION_CONTRACT["begin_marker"],
                "end_marker": CANONICAL_ACTIVATION_CONTRACT["end_marker"],
                "sha256_lf": CANONICAL_ACTIVATION_CONTRACT["sha256_lf"],
            }
        ],
        "deferred_rendered_surfaces": copy.deepcopy(
            raw_profile.get("deferred_rendered_surfaces", [])
        ),
        "protected_business_rule": "instruction_surface_bytes_unchanged",
    }
    release_axis = raw_profile.get("release_axis")
    if not isinstance(release_axis, dict):
        raise ValueError("official upgrade profile lacks a release axis")
    artifact_source_to = release_axis.get("artifact_source_to")
    to_source = to_closure.get("source")
    if (
        not isinstance(artifact_source_to, dict)
        or not isinstance(to_source, dict)
        or artifact_source_to.get("kind") != "required_release"
        or artifact_source_to.get("release") != to_source.get("required_release")
        or artifact_source_to.get("binding") != "contract_bundle.source_revision"
    ):
        raise ValueError("official upgrade profile target release is not closure-bound")
    return {
        "profile_id": raw_profile.get("profile_id"),
        "profile_version": raw_profile.get("profile_version"),
        "from": {
            "layout": from_state.get("layout"),
            "harness_skill_version": from_state.get("harness_skill_version"),
        },
        "to": {
            "layout": to_state.get("layout"),
            "harness_skill_version": to_state.get("harness_skill_version"),
        },
        "release_axis": {
            **copy.deepcopy(release_axis),
            "managed_diff_paths": sorted(
                item["source_path"] for item in write_sources
            ),
            "target_closure_authority": "external-hash-bound-official-upgrade-profile",
        },
        "adapter_id": "official-upgrade-data-profile",
        "adapter_version": raw_profile.get("protocol", {}).get("version", "v1.0.0"),
        "adapter_sha256": canonical_json_sha256(payload),
        "adapter_payload": payload,
        "automatic": raw_profile.get("automatic") is True,
        "required_manifest_fields": required_manifest_fields,
        "protected_business_rule": "instruction_surface_bytes_unchanged",
        "default_decision": "automatic_migration_available",
        "reason": "Exact closure diff and operations are a verified one-to-one mapping.",
        "from_closure_state": copy.deepcopy(from_state),
        "to_closure_state": copy.deepcopy(to_state),
    }


def _load_official_upgrade_profiles(
    wrapper_root: Path,
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    official = contract.get("official_upgrade")
    expected = {
        "protocol_path": OFFICIAL_UPGRADE_PROTOCOL_REL,
        "current_closure_pointer": OFFICIAL_UPGRADE_CLOSURE_POINTER_REL,
        "current_profile_pointer": OFFICIAL_UPGRADE_PROFILE_POINTER_REL,
        "profile_schema": OFFICIAL_UPGRADE_PROFILE_SCHEMA_REL,
        "closure_schema": OFFICIAL_UPGRADE_CLOSURE_SCHEMA_REL,
        "candidate_execution": "trusted_base_verifier_candidate_blobs_as_data",
    }
    if official != expected:
        raise ValueError("Harness migration official_upgrade locator is invalid")
    try:
        from . import evozeus_official_upgrade_verify as official_verifier
    except ImportError:
        import evozeus_official_upgrade_verify as official_verifier

    store = official_verifier.FilesystemStore(wrapper_root)
    catalog_report = official_verifier.verify_catalog(store)
    pointer_entries = official_verifier.load_pointer(
        store,
        official_verifier.PROFILES_CURRENT_REL,
        "official-upgrade-current-profiles",
    )
    protocol = official_verifier.load_protocol(store)
    profiles: list[dict[str, Any]] = []
    raw_profiles: list[dict[str, Any]] = []
    current_closure_path = catalog_report.get("current_closure")
    current_closure: dict[str, Any] | None = None
    for entry in pointer_entries:
        profile_path = _safe_relative_path(
            entry["path"],
            "official current profile path",
        )
        repository_profile_path = (
            Path(MIGRATION_CONTRACT_BUNDLE_ROOT) / profile_path
        ).as_posix()
        raw_profile = official_verifier.load_profile(
            store,
            repository_profile_path,
            protocol,
            expected_sha256=entry["sha256"],
        )
        raw_profile["_verified_profile_path"] = repository_profile_path
        raw_profile["_verified_profile_sha256"] = entry["sha256"]
        if raw_profile.get("_verified_to_path") != current_closure_path:
            raise ValueError(
                "official current profile does not target the verified current closure: "
                + str(raw_profile.get("profile_id"))
            )
        verified_to_closure = raw_profile.get("_verified_to_closure")
        if not isinstance(verified_to_closure, dict):
            raise ValueError("official current profile target closure is missing")
        if current_closure is None:
            current_closure = copy.deepcopy(verified_to_closure)
        elif current_closure != verified_to_closure:
            raise ValueError("official current profiles disagree on the current closure")
        raw_profiles.append(raw_profile)
        profiles.append(
            _official_upgrade_profile_compatibility(raw_profile)
        )
    if current_closure is None:
        raise ValueError("official current closure is unavailable")
    return profiles, {
        "report": catalog_report,
        "protocol": protocol,
        "profiles": raw_profiles,
        "current_closure": {
            "path": current_closure_path,
            "closure": current_closure,
        },
    }


def load_migration_contract(
    wrapper_root: Path | None = None,
    *,
    remote_tag_resolver: OfficialTagResolver | None = None,
) -> dict[str, Any]:
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

    contract_source = _json_object(contract_path, "Harness migration contract")
    expected_contract_identity = {
        "schema_version": MIGRATION_CONTRACT_SCHEMA_VERSION,
        "migration_protocol_version": MIGRATION_PROTOCOL_VERSION,
        "contract_id": MIGRATION_CONTRACT_ID,
        "contract_version": MIGRATION_CONTRACT_VERSION,
        "canonical_harness_skill_path": CANONICAL_HARNESS_SKILL,
        "canonical_activation_block": CANONICAL_ACTIVATION_CONTRACT,
    }
    identity_mismatches = [
        field
        for field, expected in expected_contract_identity.items()
        if contract_source.get(field) != expected
    ]
    if identity_mismatches:
        raise ValueError(
            "Harness migration contract identity is incompatible: "
            + ", ".join(identity_mismatches)
        )
    if contract_source.get("migration_protocol_version") != MIGRATION_PROTOCOL_VERSION:
        raise ValueError(
            "unsupported Harness migration protocol: "
            f"{contract_source.get('migration_protocol_version')}"
        )
    roots = contract_source.get("path_roots")
    if roots != {
        "artifact_path": MIGRATION_CONTRACT_BUNDLE_ROOT,
        "repository_path": "repository_root",
        "target_path": "target_repository_root",
    }:
        raise ValueError("migration contract path_roots are missing or ambiguous")

    discovery_profiles = contract_source.get("discovery_profiles")
    if not isinstance(discovery_profiles, list) or not discovery_profiles:
        raise ValueError("migration contract must declare discovery profiles")
    official_profiles, official_upgrade = _load_official_upgrade_profiles(
        wrapper_root,
        contract_source,
    )
    current_closure = official_upgrade["current_closure"]["closure"]
    current_state = current_closure.get("state")
    current_harness_skill_version = (
        current_state.get("harness_skill_version")
        if isinstance(current_state, dict)
        else None
    )
    if (
        not isinstance(current_harness_skill_version, str)
        or re.fullmatch(r"v\d+\.\d+\.\d+", current_harness_skill_version) is None
        or contract_source.get("current_harness_skill_version")
        != current_harness_skill_version
    ):
        raise ValueError(
            "Harness migration current_harness_skill_version disagrees with "
            "the verified current closure"
        )
    contract = copy.deepcopy(contract_source)
    contract["profiles"] = [*copy.deepcopy(discovery_profiles), *official_profiles]
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
        release_axis = profile.get("release_axis")
        if profile.get("automatic") is True:
            if not isinstance(release_axis, dict):
                raise ValueError(
                    f"automatic migration profile must bind a source release axis: {profile_id}"
                )
            if any(
                not isinstance(release_axis.get(field), str)
                or re.fullmatch(r"v\d+\.\d+\.\d+", release_axis[field]) is None
                for field in ("target_wrapper_from", "target_wrapper_to")
            ):
                raise ValueError(f"migration source release axis is invalid: {profile_id}")
            artifact_source_from = release_axis.get("artifact_source_from")
            artifact_source_to = release_axis.get("artifact_source_to")
            artifact_source_from_release = (
                artifact_source_from.get("release")
                if isinstance(artifact_source_from, dict)
                else None
            )
            if (
                not isinstance(artifact_source_from, dict)
                or artifact_source_from.get("kind") != "construction_revision"
                or not isinstance(artifact_source_from.get("revision"), str)
                or re.fullmatch(r"[0-9a-f]{40}", artifact_source_from["revision"])
                is None
                or (
                    artifact_source_from_release is not None
                    and (
                        not isinstance(artifact_source_from_release, str)
                        or re.fullmatch(
                            r"v\d+\.\d+\.\d+",
                            artifact_source_from_release,
                        )
                        is None
                    )
                )
                or not isinstance(artifact_source_to, dict)
                or artifact_source_to.get("kind") != "required_release"
                or artifact_source_to.get("binding")
                != "contract_bundle.source_revision"
                or not isinstance(artifact_source_to.get("release"), str)
                or re.fullmatch(
                    r"v\d+\.\d+\.\d+",
                    artifact_source_to["release"],
                )
                is None
            ):
                raise ValueError(
                    f"migration profile release provenance is invalid: {profile_id}"
                )
            managed_diff_paths = release_axis.get("managed_diff_paths")
            if (
                not isinstance(managed_diff_paths, list)
                or any(not isinstance(item, str) for item in managed_diff_paths)
                or len(managed_diff_paths) != len(set(managed_diff_paths))
            ):
                raise ValueError(
                    f"migration source release managed diff is invalid: {profile_id}"
                )
            for relative_text in managed_diff_paths:
                _safe_relative_path(relative_text, "managed source release diff path")
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
        remote_tag_resolver,
    )
    return {
        "contract": contract,
        "contract_source": contract_source,
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
        "official_upgrade": official_upgrade,
        "current_closure": copy.deepcopy(official_upgrade["current_closure"]),
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
    identity["release_axis"] = profile.get("release_axis")
    official_profile = (profile.get("adapter_payload") or {}).get("official_profile")
    if isinstance(official_profile, dict):
        identity["profile_path"] = official_profile.get("path")
        identity["profile_sha256"] = official_profile.get("sha256")
        identity["from_closure"] = copy.deepcopy(
            official_profile.get("from_closure")
        )
        identity["to_closure"] = copy.deepcopy(official_profile.get("to_closure"))
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


class SecureTargetFS:
    """Root-dirfd anchored target I/O with no-follow traversal and exact CAS."""

    def __init__(
        self,
        target: Path,
        *,
        directory_mode: int = 0o755,
        expected_binding: dict[str, Any] | None = None,
        retirement_root: Path | None = None,
    ):
        if os.name != "posix" or not all(
            hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")
        ):
            raise ValueError("secure target mutation requires POSIX dirfd/O_NOFOLLOW support")
        lexical = Path(os.path.abspath(os.fspath(target.expanduser())))
        self.target = lexical.resolve(strict=True)
        metadata = os.lstat(self.target)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("secure target root must be a non-symlink directory")
        self._root_identity = (metadata.st_dev, metadata.st_ino)
        self._directory_mode = directory_mode
        self._retirement_fd = -1
        self._retirement_root: Path | None = None
        self._retirement_identity: tuple[int, int] | None = None
        self.retained_quarantine: list[dict[str, Any]] = []
        self._root_fd = os.open(
            self.target,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(self._root_fd)
        if (opened.st_dev, opened.st_ino) != self._root_identity:
            os.close(self._root_fd)
            raise ValueError("secure target root changed while opening")
        actual_binding = {
            "schema_version": TARGET_BINDING_SCHEMA_VERSION,
            "lexical_path": str(lexical),
            "canonical_path": str(self.target),
            "root_st_dev": opened.st_dev,
            "root_st_ino": opened.st_ino,
        }
        if expected_binding is not None:
            approved_binding = _validated_target_binding(expected_binding)
            if actual_binding != approved_binding:
                os.close(self._root_fd)
                self._root_fd = -1
                raise ValueError(
                    "secure target root differs from the approved plan binding: "
                    f"expected={approved_binding}; actual={actual_binding}"
                )
        self.binding = actual_binding
        self.created_directories: list[str] = []
        self._created_directory_identities: dict[str, tuple[int, int]] = {}
        if retirement_root is not None:
            try:
                self._configure_retirement_root(retirement_root)
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        if self._retirement_fd >= 0:
            os.close(self._retirement_fd)
            self._retirement_fd = -1
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> "SecureTargetFS":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _verify_root(self) -> None:
        metadata = os.lstat(self.target)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != self._root_identity
        ):
            raise ValueError("secure target root identity changed")

    def _configure_retirement_root(
        self,
        retirement_root: Path,
        *,
        allow_inside_target: bool = False,
    ) -> None:
        lexical = Path(os.path.abspath(os.fspath(retirement_root.expanduser())))
        _reject_symlink_chain(lexical, "secure retirement root")
        canonical = lexical.resolve(strict=True)
        if not allow_inside_target and (
            canonical == self.target
            or self.target in canonical.parents
            or canonical in self.target.parents
        ):
            raise ValueError("secure retirement root must be outside the target")
        metadata = os.lstat(canonical)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError(
                "secure retirement root must be an owned non-symlink 0700 directory"
            )
        descriptor = os.open(
            canonical,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            os.close(descriptor)
            raise ValueError("secure retirement root changed while opening")
        if opened.st_dev != self._root_identity[0]:
            os.close(descriptor)
            raise ValueError(
                "secure retirement root must share the target filesystem for atomic moves"
            )
        if self._retirement_fd >= 0:
            os.close(self._retirement_fd)
        self._retirement_fd = descriptor
        self._retirement_root = canonical
        self._retirement_identity = (opened.st_dev, opened.st_ino)

    def _verify_retirement_root(self) -> None:
        if (
            self._retirement_fd < 0
            or self._retirement_root is None
            or self._retirement_identity is None
        ):
            raise ValueError(
                "secure target cleanup_required: no external retirement root; "
                "candidate preserved in target"
            )
        try:
            metadata = os.lstat(self._retirement_root)
            opened = os.fstat(self._retirement_fd)
        except OSError as exc:
            raise ValueError(
                "secure target cleanup_required: retirement root cannot be "
                "verified; candidate preserved in target"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (metadata.st_dev, metadata.st_ino) != self._retirement_identity
            or (opened.st_dev, opened.st_ino) != self._retirement_identity
        ):
            raise ValueError(
                "secure target cleanup_required: retirement root identity changed; "
                "candidate preserved in target"
            )

    def _open_parent(
        self,
        raw: object,
        *,
        create_parents: bool,
        missing_is_absent: bool = False,
    ) -> tuple[list[int], str, Path]:
        relative = _safe_relative_path(raw)
        self._verify_root()
        descriptors = [os.dup(self._root_fd)]
        current_rel = Path()
        try:
            for part in relative.parts[:-1]:
                current_rel /= part
                try:
                    descriptor = os.open(
                        part,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                except FileNotFoundError:
                    if not create_parents:
                        if missing_is_absent:
                            self._close_descriptors(descriptors)
                            return [], relative.name, relative
                        raise ValueError(
                            f"secure target parent is missing: {current_rel.as_posix()}"
                        )
                    os.mkdir(part, mode=self._directory_mode, dir_fd=descriptors[-1])
                    os.fsync(descriptors[-1])
                    descriptor = os.open(
                        part,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                    try:
                        os.fchmod(descriptor, self._directory_mode)
                        os.fsync(descriptor)
                        created_metadata = os.fstat(descriptor)
                        created_path = current_rel.as_posix()
                        self.created_directories.append(created_path)
                        self._created_directory_identities[created_path] = (
                            created_metadata.st_dev,
                            created_metadata.st_ino,
                        )
                    except BaseException:
                        os.close(descriptor)
                        raise
                except OSError as exc:
                    raise ValueError(
                        f"secure target parent is unsafe: {current_rel.as_posix()}: {exc}"
                    ) from exc
                descriptors.append(descriptor)
            return descriptors, relative.name, relative
        except Exception:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise

    @staticmethod
    def _close_descriptors(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    def _verify_parent_binding(self, relative: Path, parent_fd: int) -> None:
        self._verify_root()
        descriptors = [os.dup(self._root_fd)]
        try:
            for part in relative.parts[:-1]:
                descriptors.append(
                    os.open(
                        part,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                )
            expected = os.fstat(parent_fd)
            actual = os.fstat(descriptors[-1])
            if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
                raise ValueError(
                    f"secure target parent identity changed: {relative.parent.as_posix()}"
                )
        except OSError as exc:
            raise ValueError(
                f"secure target parent changed: {relative.parent.as_posix()}: {exc}"
            ) from exc
        finally:
            self._close_descriptors(descriptors)

    @staticmethod
    def _read_descriptor(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _named_entry_identity(parent_fd: int, name: str) -> tuple[int, int, int]:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return metadata.st_dev, metadata.st_ino, metadata.st_mode

    @classmethod
    def _named_identity(cls, parent_fd: int, name: str) -> tuple[int, int]:
        device, inode, mode = cls._named_entry_identity(parent_fd, name)
        if not stat.S_ISREG(mode):
            raise ValueError("secure target staging path is not a regular file")
        return device, inode

    def _retire_named_entry(
        self,
        parent_fd: int,
        name: str,
        expected_identity: tuple[int, int],
        *,
        source_path: str,
        expected_kind: str = "file",
    ) -> str:
        """Atomically move a candidate to trusted storage and retain it permanently."""
        self._verify_retirement_root()
        assert self._retirement_fd >= 0
        source_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", source_path).strip("-")
        source_slug = (source_slug or "entry")[-40:]
        source_digest = sha256_bytes(source_path.encode("utf-8"))[:12]
        retired_name: str | None = None
        for _attempt in range(32):
            candidate = (
                f"{expected_kind}-{source_slug}-{source_digest}-{uuid.uuid4().hex}"
            )
            try:
                _atomic_rename_noreplace_between_directories(
                    parent_fd,
                    name,
                    self._retirement_fd,
                    candidate,
                )
            except FileExistsError:
                continue
            except BaseException as exc:
                raise ValueError(
                    "secure target cleanup_required: candidate could not be "
                    f"atomically retired; preserved={source_path}"
                ) from exc
            retired_name = candidate
            break
        if retired_name is None:
            raise ValueError(
                "secure target cleanup_required: no unique retirement name; "
                f"preserved={source_path}"
            )

        record: dict[str, Any] = {
            "source_path": source_path,
            "retired_name": retired_name,
            "expected_st_dev": expected_identity[0],
            "expected_st_ino": expected_identity[1],
            "kind": expected_kind,
        }
        self.retained_quarantine.append(record)
        try:
            retired = self._named_entry_identity(self._retirement_fd, retired_name)
            record.update(
                {
                    "actual_st_dev": retired[0],
                    "actual_st_ino": retired[1],
                }
            )
            os.fsync(parent_fd)
            os.fsync(self._retirement_fd)
        except BaseException as exc:
            raise ValueError(
                "secure target cleanup_required: retired candidate could not be "
                f"verified; preserved={retired_name}"
            ) from exc
        retired_kind_matches = (
            stat.S_ISREG(retired[2])
            if expected_kind == "file"
            else stat.S_ISDIR(retired[2])
        )
        if retired[:2] != expected_identity or not retired_kind_matches:
            raise ValueError(
                "secure target cleanup_required: candidate identity changed during "
                f"retirement; preserved={retired_name}"
            )
        return retired_name

    def _restore_retired_entry(
        self,
        parent_fd: int,
        name: str,
        retired_name: str,
        expected_identity: tuple[int, int],
    ) -> tuple[bool, str]:
        """Restore from trusted retirement storage only into an absent destination."""
        try:
            self._verify_retirement_root()
            assert self._retirement_fd >= 0
            if self._named_entry_identity(self._retirement_fd, retired_name)[:2] != (
                expected_identity
            ):
                return False, "retired identity changed before recovery"
            _atomic_rename_noreplace_between_directories(
                self._retirement_fd,
                retired_name,
                parent_fd,
                name,
            )
            if self._named_entry_identity(parent_fd, name)[:2] != expected_identity:
                return False, "retirement recovery identity verification failed"
            os.fsync(self._retirement_fd)
            os.fsync(parent_fd)
            return True, "retired destination restored"
        except (OSError, ValueError) as exc:
            return False, f"retirement recovery failed: {exc}"

    @staticmethod
    def _open_unique_staging_file(parent_fd: int) -> tuple[str, int, tuple[int, int]]:
        for _attempt in range(32):
            name = f".evozeus-tmp-{uuid.uuid4().hex}"
            try:
                descriptor = os.open(
                    name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                raise ValueError("secure target staging path is not a regular file")
            return name, descriptor, (metadata.st_dev, metadata.st_ino)
        raise ValueError("secure target could not allocate a unique staging path")

    def _verified_replace_preimage(
        self,
        parent_fd: int,
        name: str,
        relative: Path,
        *,
        expected_preimage: str,
        expected_mode: int | None,
        expected_identity: tuple[int, int] | None = None,
    ) -> tuple[int, int]:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise ValueError(
                f"secure target replace CAS cannot open: {relative.as_posix()}: {exc}"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    f"secure target replace path is not regular: {relative.as_posix()}"
                )
            identity = metadata.st_dev, metadata.st_ino
            if expected_identity is not None and identity != expected_identity:
                raise ValueError(
                    f"secure target replace identity CAS changed: {relative.as_posix()}"
                )
            current = self._read_descriptor(descriptor)
            if f"sha256:{sha256_bytes(current)}" != expected_preimage:
                raise ValueError(
                    f"secure target replace CAS changed: {relative.as_posix()}"
                )
            if (
                expected_mode is not None
                and stat.S_IMODE(metadata.st_mode) != expected_mode
            ):
                raise ValueError(
                    f"secure target replace mode CAS changed: {relative.as_posix()}"
                )
            return identity
        finally:
            os.close(descriptor)

    def _recover_atomic_exchange(
        self,
        parent_fd: int,
        name: str,
        staging_name: str,
        relative: Path,
        *,
        staging_identity: tuple[int, int],
        displaced_identity: tuple[int, int, int] | None,
    ) -> tuple[bool, str]:
        """Put the displaced destination back without deleting either inode."""
        try:
            if self._named_identity(parent_fd, name) != staging_identity:
                return False, "published destination identity changed before recovery"
            if displaced_identity is None:
                displaced_identity = self._named_entry_identity(
                    parent_fd,
                    staging_name,
                )
            if self._named_entry_identity(parent_fd, staging_name) != displaced_identity:
                return False, "displaced destination changed before recovery"
            self._verify_parent_binding(relative, parent_fd)
            _atomic_exchange_same_directory(parent_fd, staging_name, name)
            self._verify_parent_binding(relative, parent_fd)
            restored = self._named_entry_identity(parent_fd, name)
            recovered_staging = self._named_identity(parent_fd, staging_name)
            if restored != displaced_identity or recovered_staging != staging_identity:
                return False, "atomic exchange recovery identity verification failed"
            os.fsync(parent_fd)
            return True, "displaced destination restored"
        except (OSError, ValueError) as exc:
            return False, f"atomic exchange recovery failed: {exc}"

    def _verify_published_postimage(
        self,
        parent_fd: int,
        name: str,
        relative: Path,
        *,
        expected_identity: tuple[int, int],
        expected_data: bytes,
        expected_mode: int,
    ) -> None:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise ValueError(
                f"secure target published path cannot be opened: "
                f"{relative.as_posix()}: {exc}"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    f"secure target published path is not regular: "
                    f"{relative.as_posix()}"
                )
            if (metadata.st_dev, metadata.st_ino) != expected_identity:
                raise ValueError(
                    f"secure target published identity changed: {relative.as_posix()}"
                )
            if self._read_descriptor(descriptor) != expected_data:
                raise ValueError(
                    f"secure target published bytes changed: {relative.as_posix()}"
                )
            if stat.S_IMODE(metadata.st_mode) != expected_mode:
                raise ValueError(
                    f"secure target published mode changed: {relative.as_posix()}"
                )
        finally:
            os.close(descriptor)
        if self._named_identity(parent_fd, name) != expected_identity:
            raise ValueError(
                f"secure target published identity changed: {relative.as_posix()}"
            )

    def _cleanup_created_directory_paths(self, paths: list[str]) -> list[str]:
        removed: set[str] = set()
        unresolved: set[str] = set()
        for relative_text in sorted(
            set(paths),
            key=lambda value: len(Path(value).parts),
            reverse=True,
        ):
            relative = _safe_relative_path(relative_text, "created target directory")
            try:
                descriptors, name, _ = self._open_parent(
                    relative.as_posix(),
                    create_parents=False,
                    missing_is_absent=True,
                )
            except ValueError:
                unresolved.add(relative_text)
                continue
            if not descriptors:
                if relative_text in self._created_directory_identities:
                    unresolved.add(relative_text)
                else:
                    removed.add(relative_text)
                continue
            try:
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                except FileNotFoundError:
                    if relative_text in self._created_directory_identities:
                        unresolved.add(relative_text)
                    else:
                        removed.add(relative_text)
                    continue
                except OSError:
                    unresolved.add(relative_text)
                    continue
                try:
                    metadata = os.fstat(descriptor)
                    current_identity = (metadata.st_dev, metadata.st_ino)
                finally:
                    os.close(descriptor)
                expected_identity = self._created_directory_identities.get(
                    relative_text,
                    current_identity,
                )
                try:
                    retired_name = self._retire_named_entry(
                        descriptors[-1],
                        name,
                        expected_identity,
                        source_path=relative_text,
                        expected_kind="directory",
                    )
                except (OSError, ValueError):
                    unresolved.add(relative_text)
                    continue
                removed.add(relative_text)
                try:
                    assert self._retirement_fd >= 0
                    retired_descriptor = os.open(
                        retired_name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=self._retirement_fd,
                    )
                    try:
                        if os.listdir(retired_descriptor):
                            unresolved.add(relative_text)
                            self.retained_quarantine[-1]["unexpected_contents"] = True
                    finally:
                        os.close(retired_descriptor)
                except (OSError, ValueError):
                    unresolved.add(relative_text)
            finally:
                self._close_descriptors(descriptors)
        if removed:
            self.created_directories = [
                path for path in self.created_directories if path not in removed
            ]
        return sorted((set(paths) - removed) | unresolved)

    def file_state(self, raw: object) -> dict[str, Any]:
        descriptors, name, relative = self._open_parent(
            raw,
            create_parents=False,
            missing_is_absent=True,
        )
        if not descriptors:
            return {"kind": "absent", "sha256": None, "mode": None, "uid": None}
        try:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptors[-1],
                )
            except FileNotFoundError:
                return {"kind": "absent", "sha256": None, "mode": None, "uid": None}
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ValueError(
                        f"secure target file is a symlink: {relative.as_posix()}"
                    ) from exc
                raise ValueError(
                    f"secure target file is unsafe: {relative.as_posix()}: {exc}"
                ) from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(
                        f"secure target path is not a regular file: {relative.as_posix()}"
                    )
                data = self._read_descriptor(descriptor)
            finally:
                os.close(descriptor)
            return {
                "kind": "file",
                "sha256": f"sha256:{sha256_bytes(data)}",
                "mode": stat.S_IMODE(metadata.st_mode),
                "uid": metadata.st_uid,
                "st_dev": metadata.st_dev,
                "st_ino": metadata.st_ino,
            }
        finally:
            self._close_descriptors(descriptors)

    def read_exact(self, raw: object, expected_sha256: str) -> bytes:
        descriptors, name, relative = self._open_parent(raw, create_parents=False)
        try:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptors[-1],
                )
            except OSError as exc:
                raise ValueError(
                    f"secure target file cannot be opened: {relative.as_posix()}: {exc}"
                ) from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(
                        f"secure target path is not a regular file: {relative.as_posix()}"
                    )
                data = self._read_descriptor(descriptor)
            finally:
                os.close(descriptor)
            actual = f"sha256:{sha256_bytes(data)}"
            if actual != expected_sha256:
                raise ValueError(
                    f"secure target CAS preimage changed: {relative.as_posix()}"
                )
            return data
        finally:
            self._close_descriptors(descriptors)

    def write_exact(
        self,
        raw: object,
        data: bytes,
        *,
        expected_preimage: str | None,
        expected_mode: int | None = None,
        mode: int,
    ) -> None:
        created_directory_start = len(self.created_directories)
        descriptors: list[int] = []
        parent_fd = -1
        staging_descriptor = -1
        staging_name: str | None = None
        staging_identity: tuple[int, int] | None = None
        staging_was_exchanged = False
        published_create = False
        replace_identity: tuple[int, int] | None = None
        try:
            descriptors, name, relative = self._open_parent(
                raw,
                create_parents=True,
            )
            parent_fd = descriptors[-1]
            self._verify_parent_binding(relative, parent_fd)
            if expected_preimage is not None:
                replace_identity = self._verified_replace_preimage(
                    parent_fd,
                    name,
                    relative,
                    expected_preimage=expected_preimage,
                    expected_mode=expected_mode,
                )

            staging_name, staging_descriptor, staging_identity = (
                self._open_unique_staging_file(parent_fd)
            )
            view = memoryview(data)
            while view:
                written = os.write(staging_descriptor, view)
                if written <= 0:
                    raise OSError("secure target staging write made no progress")
                view = view[written:]
            os.fchmod(staging_descriptor, mode)
            os.fsync(staging_descriptor)
            os.lseek(staging_descriptor, 0, os.SEEK_SET)
            actual = self._read_descriptor(staging_descriptor)
            if actual != data:
                raise ValueError(
                    f"secure target postimage verification failed: {relative.as_posix()}"
                )
            if stat.S_IMODE(os.fstat(staging_descriptor).st_mode) != mode:
                raise ValueError(
                    f"secure target postimage mode verification failed: {relative.as_posix()}"
                )

            self._verify_parent_binding(relative, parent_fd)
            if self._named_identity(parent_fd, staging_name) != staging_identity:
                raise ValueError(
                    f"secure target staging identity changed: {relative.as_posix()}"
                )
            if expected_preimage is None:
                try:
                    _atomic_rename_noreplace_same_directory(
                        parent_fd,
                        staging_name,
                        name,
                    )
                except FileExistsError as exc:
                    raise ValueError(
                        f"secure target create CAS changed: {relative.as_posix()}"
                    ) from exc
                published_create = True
                self._verify_parent_binding(relative, parent_fd)
                self._verify_published_postimage(
                    parent_fd,
                    name,
                    relative,
                    expected_identity=staging_identity,
                    expected_data=data,
                    expected_mode=mode,
                )
                try:
                    self._named_entry_identity(parent_fd, staging_name)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError(
                        "secure target create staging was not consumed by atomic "
                        f"publication: {relative.as_posix()}"
                    )
                staging_name = None
            else:
                self._verified_replace_preimage(
                    parent_fd,
                    name,
                    relative,
                    expected_preimage=expected_preimage,
                    expected_mode=expected_mode,
                    expected_identity=replace_identity,
                )
                self._verify_parent_binding(relative, parent_fd)
                _atomic_exchange_same_directory(parent_fd, staging_name, name)
                staging_was_exchanged = True
                displaced_identity: tuple[int, int, int] | None = None
                try:
                    displaced_identity = self._named_entry_identity(
                        parent_fd,
                        staging_name,
                    )
                    self._verify_parent_binding(relative, parent_fd)
                    self._verify_published_postimage(
                        parent_fd,
                        name,
                        relative,
                        expected_identity=staging_identity,
                        expected_data=data,
                        expected_mode=mode,
                    )
                    self._verified_replace_preimage(
                        parent_fd,
                        staging_name,
                        relative,
                        expected_preimage=expected_preimage,
                        expected_mode=expected_mode,
                        expected_identity=replace_identity,
                    )
                except BaseException as exc:
                    recovered, recovery = self._recover_atomic_exchange(
                        parent_fd,
                        name,
                        staging_name,
                        relative,
                        staging_identity=staging_identity,
                        displaced_identity=displaced_identity,
                    )
                    if recovered:
                        staging_was_exchanged = False
                    state = "restored" if recovered else "quarantined"
                    raise ValueError(
                        "secure target replace CAS changed during atomic exchange: "
                        f"{relative.as_posix()}; state={state}; recovery={recovery}"
                    ) from exc
                self._retire_named_entry(
                    parent_fd,
                    staging_name,
                    replace_identity,
                    source_path=relative.as_posix(),
                )
                staging_name = None
            os.fsync(parent_fd)
        except BaseException as operation_error:
            if staging_descriptor >= 0:
                os.close(staging_descriptor)
                staging_descriptor = -1
            cleanup_errors: list[str] = []
            if published_create and parent_fd >= 0 and staging_identity is not None:
                try:
                    self._retire_named_entry(
                        parent_fd,
                        name,
                        staging_identity,
                        source_path=relative.as_posix(),
                    )
                    published_create = False
                except (OSError, ValueError) as cleanup_error:
                    cleanup_errors.append(str(cleanup_error))
            if (
                parent_fd >= 0
                and staging_name is not None
                and staging_identity is not None
            ):
                try:
                    cleanup_identity = (
                        replace_identity
                        if staging_was_exchanged and replace_identity is not None
                        else staging_identity
                    )
                    self._retire_named_entry(
                        parent_fd,
                        staging_name,
                        cleanup_identity,
                        source_path=relative.as_posix(),
                    )
                    staging_name = None
                except (OSError, ValueError) as cleanup_error:
                    cleanup_errors.append(str(cleanup_error))
            if descriptors:
                self._close_descriptors(descriptors)
                descriptors = []
            unresolved_directories = self._cleanup_created_directory_paths(
                self.created_directories[created_directory_start:]
            )
            if unresolved_directories:
                cleanup_errors.append(
                    "unresolved created directories: "
                    + ", ".join(unresolved_directories)
                )
            if cleanup_errors:
                raise ValueError(
                    "secure target cleanup_required after failed write: "
                    + "; ".join(cleanup_errors)
                ) from operation_error
            raise
        finally:
            if staging_descriptor >= 0:
                os.close(staging_descriptor)
            if descriptors:
                self._close_descriptors(descriptors)

    def remove_exact(
        self,
        raw: object,
        expected_sha256: str,
        *,
        expected_mode: int | None = None,
    ) -> None:
        descriptors, name, relative = self._open_parent(raw, create_parents=False)
        try:
            expected_identity = self._verified_replace_preimage(
                descriptors[-1],
                name,
                relative,
                expected_preimage=expected_sha256,
                expected_mode=expected_mode,
            )
            self._verify_parent_binding(relative, descriptors[-1])
            retired_name = self._retire_named_entry(
                descriptors[-1],
                name,
                expected_identity,
                source_path=relative.as_posix(),
            )
            try:
                assert self._retirement_fd >= 0
                self._verified_replace_preimage(
                    self._retirement_fd,
                    retired_name,
                    relative,
                    expected_preimage=expected_sha256,
                    expected_mode=expected_mode,
                    expected_identity=expected_identity,
                )
            except BaseException as exc:
                recovered, recovery = self._restore_retired_entry(
                    descriptors[-1],
                    name,
                    retired_name,
                    expected_identity,
                )
                if self.retained_quarantine:
                    self.retained_quarantine[-1]["restored"] = recovered
                state = "restored" if recovered else "retained"
                raise ValueError(
                    "secure target remove CAS changed during atomic retirement: "
                    f"{relative.as_posix()}; state={state}; recovery={recovery}"
                ) from exc
            os.fsync(descriptors[-1])
        finally:
            self._close_descriptors(descriptors)

    def cleanup_created_directories(self) -> list[str]:
        return self._cleanup_created_directory_paths(self.created_directories)

    def directory_state(self, raw: object) -> dict[str, Any]:
        relative = _safe_relative_path(raw, "secure directory")
        self._verify_root()
        descriptors = [os.dup(self._root_fd)]
        try:
            for part in relative.parts:
                try:
                    descriptor = os.open(
                        part,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                except FileNotFoundError:
                    return {"kind": "absent", "mode": None, "uid": None}
                except OSError as exc:
                    raise ValueError(
                        f"secure directory is unsafe: {relative.as_posix()}: {exc}"
                    ) from exc
                descriptors.append(descriptor)
            metadata = os.fstat(descriptors[-1])
            return {
                "kind": "directory",
                "mode": stat.S_IMODE(metadata.st_mode),
                "uid": metadata.st_uid,
                "identity": (metadata.st_dev, metadata.st_ino),
            }
        finally:
            self._close_descriptors(descriptors)

    def ensure_directory_exact(
        self,
        raw: object,
        *,
        mode: int,
        require_absent: bool,
    ) -> None:
        relative = _safe_relative_path(raw, "secure directory")
        self._verify_root()
        descriptors = [os.dup(self._root_fd)]
        created_final = False
        try:
            for index, part in enumerate(relative.parts):
                is_final = index == len(relative.parts) - 1
                try:
                    descriptor = os.open(
                        part,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                    if is_final and require_absent:
                        os.close(descriptor)
                        raise ValueError(
                            f"secure directory create CAS changed: {relative.as_posix()}"
                        )
                except FileNotFoundError:
                    try:
                        os.mkdir(part, mode=mode, dir_fd=descriptors[-1])
                    except FileExistsError as exc:
                        raise ValueError(
                            f"secure directory create CAS changed: {relative.as_posix()}"
                        ) from exc
                    os.fsync(descriptors[-1])
                    descriptor = os.open(
                        part,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=descriptors[-1],
                    )
                    os.fchmod(descriptor, mode)
                    os.fsync(descriptor)
                    current = Path(*relative.parts[: index + 1]).as_posix()
                    self.created_directories.append(current)
                    created_metadata = os.fstat(descriptor)
                    self._created_directory_identities[current] = (
                        created_metadata.st_dev,
                        created_metadata.st_ino,
                    )
                    if is_final:
                        created_final = True
                except OSError as exc:
                    raise ValueError(
                        f"secure directory is unsafe: {relative.as_posix()}: {exc}"
                    ) from exc
                descriptors.append(descriptor)
            metadata = os.fstat(descriptors[-1])
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != mode:
                raise ValueError(
                    f"secure directory owner or mode is invalid: {relative.as_posix()}"
                )
            expected_identity = (metadata.st_dev, metadata.st_ino)
            rebound = self.directory_state(relative.as_posix())
            if rebound.get("identity") != expected_identity:
                raise ValueError(
                    f"secure directory identity changed: {relative.as_posix()}"
                )
            if require_absent and not created_final:
                raise ValueError(
                    f"secure directory create CAS changed: {relative.as_posix()}"
                )
        finally:
            self._close_descriptors(descriptors)


class SecureSnapshotFS(SecureTargetFS):
    """Private snapshot-root backend with the same dirfd/CAS guarantees."""

    def __init__(self, root: Path):
        super().__init__(root, directory_mode=0o700)
        metadata = os.fstat(self._root_fd)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            self.close()
            raise ValueError("trusted snapshot root owner or mode is invalid")
        try:
            try:
                os.mkdir(".retired", mode=0o700, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            except FileExistsError:
                pass
            self._configure_retirement_root(
                self.target / ".retired",
                allow_inside_target=True,
            )
        except BaseException:
            self.close()
            raise


def migration_plan_digest(plan: dict[str, Any]) -> str:
    return canonical_json_sha256(migration_plan_payload(plan))


def migration_plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"plan_sha256", "approval", "snapshot"}
    }


def canonical_plan_bytes(plan: dict[str, Any]) -> bytes:
    return json.dumps(
        migration_plan_payload(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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
    expected_binding = (plan.get("target_git_state") or {}).get("target_binding")
    requested_target = Path(os.path.abspath(os.fspath(target.expanduser())))
    verify_target_binding(requested_target, expected_binding)
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
    verify_target_git_state(requested_target, plan)
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
        preimage_mode = item.get("preimage_mode")
        postimage = item.get("postimage_sha256")
        postimage_mode = item.get("postimage_mode")
        mode_bound = "preimage_mode" in item or "postimage_mode" in item
        if preimage is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(preimage)
        ):
            raise ValueError(f"migration write preimage is invalid: {relative}")
        if mode_bound and preimage is None and preimage_mode is not None:
            raise ValueError(f"migration create preimage mode is invalid: {relative}")
        if mode_bound and preimage is not None and (
            not isinstance(preimage_mode, int)
            or isinstance(preimage_mode, bool)
            or not 0 <= preimage_mode <= 0o7777
        ):
            raise ValueError(f"migration write preimage mode is invalid: {relative}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(postimage)):
            raise ValueError(f"migration write postimage is invalid: {relative}")
        if mode_bound and (
            not isinstance(postimage_mode, int)
            or isinstance(postimage_mode, bool)
            or not 0 <= postimage_mode <= 0o7777
        ):
            raise ValueError(f"migration write postimage mode is invalid: {relative}")
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

    with SecureTargetFS(
        requested_target,
        expected_binding=expected_binding,
    ) as secure_target:
        for item in operation_sets["write_set"]:
            state = secure_target.file_state(item.get("path"))
            expected = item.get("preimage_sha256")
            if expected is None:
                if state["kind"] != "absent":
                    raise ValueError(
                        f"migration create path appeared after planning: {item.get('path')}"
                    )
                continue
            if state.get("sha256") != expected:
                raise ValueError(
                    f"migration preimage hash changed: {item.get('path')}: "
                    f"expected={expected}; actual={state.get('sha256')}"
                )
            if "preimage_mode" in item and state.get("mode") != item.get(
                "preimage_mode"
            ):
                raise ValueError(
                    f"migration preimage mode changed: {item.get('path')}: "
                    f"expected={item.get('preimage_mode')}; actual={state.get('mode')}"
                )
        for item in operation_sets["delete_set"]:
            state = secure_target.file_state(item.get("path"))
            if state.get("sha256") != item.get("preimage_sha256"):
                raise ValueError(
                    f"migration delete preimage hash changed: {item.get('path')}"
                )
        for item in operation_sets["move_set"]:
            source_state = secure_target.file_state(item.get("source"))
            destination_state = secure_target.file_state(item.get("destination"))
            if source_state.get("sha256") != item.get("source_preimage_sha256"):
                raise ValueError(
                    f"migration move source preimage hash changed: {item.get('source')}"
                )
            expected_destination = item.get("destination_preimage_sha256")
            if expected_destination is None:
                if destination_state["kind"] != "absent":
                    raise ValueError(
                        "migration move destination appeared after planning: "
                        f"{item.get('destination')}"
                    )
            elif destination_state.get("sha256") != expected_destination:
                raise ValueError(
                    f"migration move destination preimage hash changed: {item.get('destination')}"
                )
        for item in protected:
            expected = item.get("preimage_sha256")
            state = secure_target.file_state(item.get("path"))
            if not isinstance(expected, str) or state.get("sha256") != expected:
                raise ValueError(
                    f"protected business surface preimage changed: {item.get('path')}"
                )
    verify_target_binding(requested_target, expected_binding)


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


def _require_owned_mode(
    path: Path,
    *,
    mode: int,
    kind: str,
    label: str,
) -> os.stat_result:
    if os.name != "posix" or not hasattr(os, "getuid"):
        raise ValueError(f"{label} requires POSIX ownership verification")
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} must not be a symlink")
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a directory")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"{label} owner does not match the current user")
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if actual_mode != mode:
        raise ValueError(
            f"{label} mode is invalid: expected={oct(mode)}; actual={oct(actual_mode)}"
        )
    return metadata


def _snapshot_anchor_path(base: Path, transaction_id: str) -> Path:
    return base / ".anchors" / f"{transaction_id}.json"


def _transaction_quarantine_inventory(snapshot_root: Path) -> list[dict[str, Any]]:
    quarantine = snapshot_root / "quarantine"
    try:
        _require_owned_mode(
            quarantine,
            mode=0o700,
            kind="directory",
            label="migration transaction quarantine",
        )
    except FileNotFoundError:
        return []
    entries: list[dict[str, Any]] = []
    with os.scandir(quarantine) as iterator:
        children = sorted(iterator, key=lambda item: item.name)
    for child in children:
        metadata = child.stat(follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("migration transaction quarantine contains a symlink")
        if stat.S_ISREG(metadata.st_mode):
            kind = "file"
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
        else:
            raise ValueError(
                "migration transaction quarantine contains an unsupported entry"
            )
        entries.append(
            {
                "name": child.name,
                "kind": kind,
                "st_dev": metadata.st_dev,
                "st_ino": metadata.st_ino,
            }
        )
    return entries


def mark_migration_transaction(
    snapshot_root: Path,
    *,
    state: str,
    changed_paths: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    if state not in {
        "in_progress",
        "applied",
        "rollback_in_progress",
        "cleanup_required",
        "rolled_back",
        "rollback_failed",
    }:
        raise ValueError(f"unsupported migration transaction state: {state}")
    base = snapshot_root.parent
    _require_owned_mode(
        base,
        mode=0o700,
        kind="directory",
        label="trusted snapshot root",
    )
    relative = f"{snapshot_root.name}/transaction.json"
    current: dict[str, Any] = {}
    with SecureSnapshotFS(base) as secure_base:
        existing = secure_base.file_state(relative)
        if existing["kind"] == "file":
            try:
                current_value = _strict_json_loads(
                    secure_base.read_exact(relative, existing["sha256"]).decode(
                        "utf-8"
                    ),
                    "migration transaction state",
                )
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid migration transaction state: {exc}") from exc
            if not isinstance(current_value, dict):
                raise ValueError("migration transaction state must be a JSON object")
            current = current_value
        transaction = {
            "schema_version": SNAPSHOT_TRANSACTION_SCHEMA_VERSION,
            "transaction_id": snapshot_root.name,
            "state": state,
            "changed_paths": list(changed_paths or current.get("changed_paths") or []),
            "retained_quarantine": _transaction_quarantine_inventory(snapshot_root),
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        transaction_bytes = (
            json.dumps(
                transaction,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        secure_base.write_exact(
            relative,
            transaction_bytes,
            expected_preimage=existing.get("sha256"),
            mode=0o600,
        )
    return transaction


def _rollback_file_state(sha256: object, mode: object) -> dict[str, Any]:
    if sha256 is None or sha256 == "absent":
        if mode is not None:
            raise ValueError("absent rollback state cannot declare a file mode")
        return {"kind": "absent"}
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(sha256)):
        raise ValueError("rollback file state digest is invalid")
    if (
        not isinstance(mode, int)
        or isinstance(mode, bool)
        or not 0 <= mode <= 0o7777
    ):
        raise ValueError("rollback file state mode is invalid")
    return {"kind": "file", "sha256": sha256, "mode": mode}


def _rollback_allowed_states(
    plan: dict[str, Any],
    relative_text: str,
    preimage_sha256: object,
    preimage_mode: object,
) -> list[dict[str, Any]]:
    states = [_rollback_file_state(preimage_sha256, preimage_mode)]
    for item in plan.get("write_set", []):
        if item.get("path") == relative_text:
            postimage = item.get("postimage_sha256")
            if not isinstance(postimage, str):
                raise ValueError(
                    f"migration write set has no postimage: {relative_text}"
                )
            states.append(
                _rollback_file_state(postimage, item.get("postimage_mode"))
            )
    for item in plan.get("delete_set", []):
        if item.get("path") == relative_text:
            states.append(_rollback_file_state("absent", None))
    for item in plan.get("move_set", []):
        if item.get("source") == relative_text:
            states.append(_rollback_file_state("absent", None))
        if item.get("destination") == relative_text:
            postimage = item.get("destination_postimage_sha256") or item.get(
                "source_preimage_sha256"
            )
            if not isinstance(postimage, str):
                raise ValueError(
                    f"migration move set has no destination postimage: {relative_text}"
                )
            postimage_mode = item.get("destination_postimage_mode") or item.get(
                "source_preimage_mode"
            )
            states.append(_rollback_file_state(postimage, postimage_mode))
    unique: list[dict[str, Any]] = []
    for state in states:
        if state not in unique:
            unique.append(state)
    return unique


def create_migration_snapshot(
    target: Path,
    plan: dict[str, Any],
    snapshot_root: Path | None = None,
) -> Path:
    """Persist the complete declared write set before the first target write."""
    expected_binding = (plan.get("target_git_state") or {}).get("target_binding")
    requested_target = Path(os.path.abspath(os.fspath(target.expanduser())))
    binding = verify_target_binding(requested_target, expected_binding)
    target = Path(binding["canonical_path"])
    verify_plan_preimages(requested_target, plan)
    base = _trusted_snapshot_base(target, snapshot_root)
    base.mkdir(parents=True, mode=0o700, exist_ok=True)
    _reject_symlink_chain(base, "trusted snapshot root")
    _require_owned_mode(
        base,
        mode=0o700,
        kind="directory",
        label="trusted snapshot root",
    )
    transaction_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + uuid.uuid4().hex[:12]
    )
    destination = base / transaction_id
    plan_bytes = canonical_plan_bytes(plan)
    plan_sha256 = f"sha256:{sha256_bytes(plan_bytes)}"
    if plan_sha256 != plan.get("plan_sha256"):
        raise ValueError("approved migration plan bytes do not match plan_sha256")
    files: list[dict[str, Any]] = []
    existing_directories: set[str] = set()
    with SecureSnapshotFS(base) as secure_snapshot:
        secure_snapshot.ensure_directory_exact(
            ".anchors",
            mode=0o700,
            require_absent=False,
        )
        secure_snapshot.ensure_directory_exact(
            transaction_id,
            mode=0o700,
            require_absent=True,
        )
        secure_snapshot.ensure_directory_exact(
            f"{transaction_id}/files",
            mode=0o700,
            require_absent=True,
        )
        secure_snapshot.ensure_directory_exact(
            f"{transaction_id}/quarantine",
            mode=0o700,
            require_absent=True,
        )
        secure_snapshot.write_exact(
            f"{transaction_id}/approved-plan.json",
            plan_bytes,
            expected_preimage=None,
            mode=0o600,
        )
        with SecureTargetFS(
            requested_target,
            expected_binding=expected_binding,
            retirement_root=destination / "quarantine",
        ) as secure_target:
            for relative_text in planned_target_paths(plan):
                state = secure_target.file_state(relative_text)
                relative = _safe_relative_path(relative_text)
                parent = relative.parent
                while parent.parts:
                    if secure_target.directory_state(parent.as_posix())["kind"] == (
                        "directory"
                    ):
                        existing_directories.add(parent.as_posix())
                    parent = parent.parent
                item: dict[str, Any] = {
                    "path": relative_text,
                    "kind": state["kind"],
                    "mode": state["mode"],
                    "sha256": state["sha256"],
                }
                if state["kind"] == "file":
                    data = secure_target.read_exact(relative_text, state["sha256"])
                    secure_snapshot.write_exact(
                        f"{transaction_id}/files/{relative_text}",
                        data,
                        expected_preimage=None,
                        mode=0o600,
                    )
                item["allowed_rollback_states"] = _rollback_allowed_states(
                    plan,
                    relative_text,
                    state["sha256"],
                    state["mode"],
                )
                files.append(item)

        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "target": str(target),
            "target_binding": binding,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "migration_protocol_version": plan.get("migration_protocol_version"),
            "profile": plan.get("profile"),
            "plan_sha256": plan_sha256,
            "approved_plan_file": "approved-plan.json",
            "quarantine_directory": "quarantine",
            "target_git_state": plan.get("target_git_state"),
            "planned_paths": planned_target_paths(plan),
            "files": files,
            "existing_directories": sorted(existing_directories),
        }
        descriptor_bytes = (
            json.dumps(
                snapshot,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        receipt = {
            "schema_version": SNAPSHOT_RECEIPT_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "target": str(target),
            "target_binding": binding,
            "plan_sha256": snapshot["plan_sha256"],
            "approved_plan_sha256": plan_sha256,
            "descriptor_sha256": f"sha256:{sha256_bytes(descriptor_bytes)}",
            "backup_set_sha256": f"sha256:{canonical_json_sha256(files)}",
        }
        receipt_bytes = (
            json.dumps(
                receipt,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        anchor = {
            "schema_version": SNAPSHOT_ANCHOR_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "target": str(target),
            "target_binding": binding,
            "plan_sha256": plan_sha256,
            "descriptor_sha256": receipt["descriptor_sha256"],
            "receipt_sha256": f"sha256:{sha256_bytes(receipt_bytes)}",
        }
        anchor_bytes = (
            json.dumps(
                anchor,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for relative, data, mode in (
            (f"{transaction_id}/snapshot.json", descriptor_bytes, 0o600),
            (f"{transaction_id}/receipt.json", receipt_bytes, 0o600),
            (f".anchors/{transaction_id}.json", anchor_bytes, 0o400),
        ):
            secure_snapshot.write_exact(
                relative,
                data,
                expected_preimage=None,
                mode=mode,
            )
    verify_target_binding(requested_target, expected_binding)
    return destination


def rollback_migration_snapshot(
    target: Path,
    snapshot_root: Path,
    *,
    trusted_snapshot_root: Path | None = None,
) -> dict[str, Any]:
    target = Path(os.path.abspath(os.fspath(target.expanduser())))
    current_canonical_target = target.resolve(strict=True)
    trusted_base = _trusted_snapshot_base(
        current_canonical_target,
        trusted_snapshot_root,
    )
    _require_owned_mode(
        trusted_base,
        mode=0o700,
        kind="directory",
        label="trusted snapshot root",
    )
    snapshot_root = _snapshot_path(trusted_base, snapshot_root)
    transaction_id = snapshot_root.name
    with SecureSnapshotFS(trusted_base) as secure_snapshot:
        for relative, label in (
            (transaction_id, "migration snapshot transaction"),
            (f"{transaction_id}/files", "migration snapshot files root"),
        ):
            state = secure_snapshot.directory_state(relative)
            if (
                state.get("kind") != "directory"
                or state.get("uid") != os.getuid()
                or state.get("mode") != 0o700
            ):
                raise ValueError(f"{label} owner, type, or mode is invalid")
        quarantine_relative = f"{transaction_id}/quarantine"
        quarantine_state = secure_snapshot.directory_state(quarantine_relative)
        if quarantine_state.get("kind") == "absent":
            # Authenticated v1 snapshots created before the retirement protocol
            # gain an empty, private quarantine before any target mutation.
            secure_snapshot.ensure_directory_exact(
                quarantine_relative,
                mode=0o700,
                require_absent=True,
            )
            quarantine_state = secure_snapshot.directory_state(quarantine_relative)
        if (
            quarantine_state.get("kind") != "directory"
            or quarantine_state.get("uid") != os.getuid()
            or quarantine_state.get("mode") != 0o700
        ):
            raise ValueError(
                "migration transaction quarantine owner, type, or mode is invalid"
            )
        plan_bytes = _read_owned_snapshot_file(
            secure_snapshot,
            f"{transaction_id}/approved-plan.json",
            mode=0o600,
            label="approved migration plan",
        )
        descriptor_bytes = _read_owned_snapshot_file(
            secure_snapshot,
            f"{transaction_id}/snapshot.json",
            mode=0o600,
            label="migration snapshot descriptor",
        )
        receipt_bytes = _read_owned_snapshot_file(
            secure_snapshot,
            f"{transaction_id}/receipt.json",
            mode=0o600,
            label="migration snapshot receipt",
        )
        anchor_bytes = _read_owned_snapshot_file(
            secure_snapshot,
            f".anchors/{transaction_id}.json",
            mode=0o400,
            label="migration snapshot external anchor",
        )
    approved_plan = _json_bytes_object(plan_bytes, "approved migration plan")
    approved_plan_sha256 = f"sha256:{sha256_bytes(plan_bytes)}"
    snapshot = _json_bytes_object(descriptor_bytes, "migration snapshot")
    receipt = _json_bytes_object(receipt_bytes, "migration snapshot receipt")
    anchor = _json_bytes_object(anchor_bytes, "migration snapshot external anchor")
    if anchor.get("schema_version") != SNAPSHOT_ANCHOR_SCHEMA_VERSION:
        raise ValueError("unsupported migration snapshot external anchor schema")
    if anchor.get("receipt_sha256") != f"sha256:{sha256_bytes(receipt_bytes)}":
        raise ValueError("migration snapshot external anchor receipt mismatch")
    if receipt.get("schema_version") != SNAPSHOT_RECEIPT_SCHEMA_VERSION:
        raise ValueError("unsupported migration snapshot receipt schema")
    if receipt.get("descriptor_sha256") != f"sha256:{sha256_bytes(descriptor_bytes)}":
        raise ValueError("migration snapshot descriptor digest mismatch")
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported migration snapshot schema")
    if snapshot.get("target") != str(current_canonical_target):
        raise ValueError("migration snapshot target does not match requested target")
    if snapshot.get("transaction_id") != snapshot_root.name:
        raise ValueError("migration snapshot transaction identity mismatch")
    if snapshot.get("quarantine_directory") not in {None, "quarantine"}:
        raise ValueError("migration snapshot quarantine binding is invalid")
    if any(
        receipt.get(field) != snapshot.get(field)
        for field in ("transaction_id", "target", "target_binding", "plan_sha256")
    ):
        raise ValueError("migration snapshot receipt identity mismatch")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(snapshot.get("plan_sha256"))):
        raise ValueError("migration snapshot plan digest is invalid")
    if (
        approved_plan_sha256 != snapshot.get("plan_sha256")
        or approved_plan_sha256 != receipt.get("approved_plan_sha256")
        or approved_plan_sha256 != anchor.get("plan_sha256")
        or migration_plan_digest(approved_plan) != sha256_bytes(plan_bytes)
    ):
        raise ValueError("approved migration plan digest binding mismatch")
    if any(
        anchor.get(field) != snapshot.get(field)
        for field in ("transaction_id", "target", "target_binding", "plan_sha256")
    ):
        raise ValueError("migration snapshot external anchor identity mismatch")
    if anchor.get("descriptor_sha256") != receipt.get("descriptor_sha256"):
        raise ValueError("migration snapshot external anchor descriptor mismatch")
    expected_binding = _validated_target_binding(snapshot.get("target_binding"))
    approved_binding = _validated_target_binding(
        (approved_plan.get("target_git_state") or {}).get("target_binding")
    )
    if expected_binding != approved_binding:
        raise ValueError("migration snapshot target binding differs from approved plan")
    verify_target_binding(target, expected_binding)

    files = snapshot.get("files")
    if not isinstance(files, list):
        raise ValueError("migration snapshot files must be a list")
    if receipt.get("backup_set_sha256") != f"sha256:{canonical_json_sha256(files)}":
        raise ValueError("migration snapshot backup-set digest mismatch")
    planned_paths = snapshot.get("planned_paths")
    approved_paths = planned_target_paths(approved_plan)
    if (
        not isinstance(planned_paths, list)
        or planned_paths != approved_paths
        or planned_paths != [item.get("path") for item in files if isinstance(item, dict)]
        or len(planned_paths) != len(set(planned_paths))
    ):
        raise ValueError("migration snapshot planned path set is invalid")
    validated: list[tuple[dict[str, Any], dict[str, Any], bytes | None]] = []
    seen_paths: set[str] = set()
    with SecureSnapshotFS(trusted_base) as secure_snapshot, SecureTargetFS(
        target,
        expected_binding=expected_binding,
    ) as secure_target:
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("migration snapshot file entry must be an object")
            relative_text = item.get("path")
            if relative_text in seen_paths:
                raise ValueError(f"migration snapshot contains duplicate path: {relative_text}")
            seen_paths.add(str(relative_text))
            current = secure_target.file_state(relative_text)
            current_state = _rollback_file_state(
                current.get("sha256"),
                current.get("mode"),
            )
            allowed_states = item.get("allowed_rollback_states")
            expected_allowed_states = _rollback_allowed_states(
                approved_plan,
                str(relative_text),
                item.get("sha256"),
                item.get("mode"),
            )
            if (
                not isinstance(allowed_states, list)
                or not allowed_states
                or allowed_states != expected_allowed_states
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
                validated.append((item, current_state, None))
                continue
            if item.get("kind") != "file":
                raise ValueError(f"unsupported snapshot file kind: {item.get('kind')}")
            if (
                not isinstance(item.get("mode"), int)
                or isinstance(item.get("mode"), bool)
                or not 0 <= item["mode"] <= 0o7777
            ):
                raise ValueError(f"migration snapshot mode is invalid: {item.get('path')}")
            backup_relative = (
                f"{transaction_id}/files/"
                + _safe_relative_path(item.get("path")).as_posix()
            )
            data = _read_owned_snapshot_file(
                secure_snapshot,
                backup_relative,
                mode=0o600,
                label="migration snapshot backup",
            )
            if f"sha256:{sha256_bytes(data)}" != item.get("sha256"):
                raise ValueError(f"migration snapshot backup hash mismatch: {item.get('path')}")
            validated.append((item, current_state, data))

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

    removable_directories: set[str] = set()
    for relative_text in approved_paths:
        parent = _safe_relative_path(relative_text).parent
        while parent.parts:
            normalized = parent.as_posix()
            if normalized not in existing_directories:
                removable_directories.add(normalized)
            parent = parent.parent
    removable_directory_identities: dict[str, tuple[int, int]] = {}
    with SecureTargetFS(
        target,
        expected_binding=expected_binding,
    ) as secure_target:
        for relative_text in sorted(removable_directories):
            state = secure_target.directory_state(relative_text)
            if state.get("kind") == "absent":
                continue
            identity = state.get("identity")
            if (
                state.get("kind") != "directory"
                or not isinstance(identity, tuple)
                or len(identity) != 2
                or any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in identity
                )
            ):
                raise ValueError(
                    "rollback transaction-created directory binding is invalid: "
                    + relative_text
                )
            removable_directory_identities[relative_text] = identity
    changed_paths = [str(item["path"]) for item in files]
    mark_migration_transaction(
        snapshot_root,
        state="rollback_in_progress",
        changed_paths=changed_paths,
    )
    try:
        with SecureTargetFS(
            target,
            expected_binding=expected_binding,
            retirement_root=snapshot_root / "quarantine",
        ) as secure_target:
            for item, current_state, data in validated:
                if item.get("kind") == "absent":
                    if current_state.get("kind") != "absent":
                        secure_target.remove_exact(
                            item["path"],
                            current_state["sha256"],
                            expected_mode=current_state["mode"],
                        )
                    continue
                desired_state = _rollback_file_state(
                    item.get("sha256"),
                    item.get("mode"),
                )
                if current_state == desired_state:
                    continue
                secure_target.write_exact(
                    item["path"],
                    data or b"",
                    expected_preimage=(
                        None
                        if current_state.get("kind") == "absent"
                        else current_state["sha256"]
                    ),
                    expected_mode=(
                        None
                        if current_state.get("kind") == "absent"
                        else current_state["mode"]
                    ),
                    mode=int(item["mode"]),
                )

        with SecureTargetFS(
            target,
            expected_binding=expected_binding,
            retirement_root=snapshot_root / "quarantine",
        ) as secure_target:
            secure_target.created_directories.extend(removable_directories)
            secure_target._created_directory_identities.update(
                removable_directory_identities
            )
            unresolved_directories = secure_target.cleanup_created_directories()
            if unresolved_directories:
                raise ValueError(
                    "rollback preserved a non-empty transaction-created directory "
                    "or unverified replacement in quarantine: "
                    + ", ".join(unresolved_directories)
                )
            for relative_text in sorted(removable_directories):
                if secure_target.directory_state(relative_text)["kind"] != "absent":
                    raise ValueError(
                        "rollback failed to remove transaction-created directory: "
                        + relative_text
                    )

        with SecureTargetFS(
            target,
            expected_binding=expected_binding,
        ) as secure_target:
            for item in files:
                current = secure_target.file_state(item.get("path"))
                if item.get("kind") == "absent":
                    if current["kind"] != "absent":
                        raise ValueError(
                            f"rollback failed to remove created path: {item.get('path')}"
                        )
                    continue
                if (
                    current.get("sha256") != item.get("sha256")
                    or current.get("mode") != item.get("mode")
                ):
                    raise ValueError(
                        f"rollback verification failed: {item.get('path')}"
                    )
        quarantine_residue = [
            item["path"]
            for item in _target_inventory(target)
            if any(
                part.startswith((".evozeus-tmp-", ".evozeus-quarantine-"))
                for part in Path(item["path"]).parts
            )
        ]
        if quarantine_residue:
            raise ValueError(
                "rollback preserved concurrent quarantine content: "
                + ", ".join(quarantine_residue)
            )
        verify_target_binding(target, expected_binding)
        mark_migration_transaction(
            snapshot_root,
            state="rolled_back",
            changed_paths=changed_paths,
        )
    except Exception as exc:
        try:
            mark_migration_transaction(
                snapshot_root,
                state="rollback_failed",
                changed_paths=changed_paths,
                error=str(exc),
            )
        except Exception as state_exc:
            raise ValueError(
                f"rollback_failed: {exc}; transaction state write failed: {state_exc}"
            ) from exc
        raise ValueError(f"rollback_failed: {exc}") from exc
    return {
        "stage": "harness_migration_rollback",
        "status": "rolled_back",
        "writes": True,
        "target": str(current_canonical_target),
        "snapshot": str(snapshot_root),
        "plan_sha256": snapshot["plan_sha256"],
        "restored_files": [item["path"] for item in files],
        "verification": "passed",
    }
