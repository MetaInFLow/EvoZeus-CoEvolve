#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GLOBAL_DISPATCHER_COMMAND = (
    '/usr/bin/python3 "$HOME/.evozeus/hooks/evozeus_wrapper_dispatcher.py"'
)
GLOBAL_HOOK_EVENT = "SessionStart"
GLOBAL_PROMPT_HOOK_EVENT = "UserPromptSubmit"
GLOBAL_HOOK_MATCHER = "startup|resume"
GLOBAL_HOOKS_CONFIG = Path(".codex/hooks.json")
GLOBAL_DISPATCHER = Path(".evozeus/hooks/evozeus_wrapper_dispatcher.py")
CORE_DISPATCHER_STATE = Path(".evozeus/hooks/state.json")
GLOBAL_HOOK_STATE = Path(".evozeus/hooks/coevolve-lifecycle.json")
GLOBAL_HOOK_BACKUPS = Path(".evozeus/backups/global-hooks")
CORE_DISPATCHER_SCHEMA = "evozeus.channel-coevolve-dispatcher.v2"
CORE_USER_PROMPT_RUNTIME_API = "evozeus.user-prompt.lesson-runtime.v1"
HARNESS_UPGRADE_BACKUPS = Path(".evozeus/backups/harness-upgrades")
LATEST_VERSION_CACHE = Path(".evozeus/cache/evozeus-wrapper-latest.json")
LATEST_VERSION_CACHE_LIMIT_SECONDS = 86400
TARGET_MANIFEST = Path(".evozeus-wrapper/wrapper.json")
LEGACY_TARGET_MANIFESTS = (
    Path(".evozeus_evoinfra/wrapper.json"),
    Path(".evozeus/wrapper.json"),
)
WRAPPER_UPGRADE_SOURCE_FILES = (
    Path("scripts/evozeus_wrapper_preflight.py"),
    Path("templates/global/evozeus_wrapper_dispatcher.py"),
    Path("templates/target/.codex/hooks/evozeus_wrapper_start_check.py"),
    Path("templates/target/.github/workflows/evozeus-wrapper-preflight.yml"),
    Path("templates/target/docs/onboarding.md"),
)


def _utc_transaction_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _paths(home: Path) -> dict[str, Path]:
    home = home.expanduser().resolve()
    return {
        "hooks": home / GLOBAL_HOOKS_CONFIG,
        "dispatcher": home / GLOBAL_DISPATCHER,
        "core_state": home / CORE_DISPATCHER_STATE,
        "state": home / GLOBAL_HOOK_STATE,
        "backups": home / GLOBAL_HOOK_BACKUPS,
    }


def _dispatcher_entry(event: str) -> dict[str, Any]:
    handler = {
        "type": "command",
        "command": GLOBAL_DISPATCHER_COMMAND,
        "timeout": 30 if event == GLOBAL_HOOK_EVENT else 3,
    }
    if event == GLOBAL_HOOK_EVENT:
        handler["statusMessage"] = "Checking EvoZeus harnesses"
    else:
        handler["additionalContextLimit"] = 1200
    entry: dict[str, Any] = {"hooks": [handler]}
    if event == GLOBAL_HOOK_EVENT:
        entry["matcher"] = GLOBAL_HOOK_MATCHER
    return entry


def _read_hooks_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    if not path.is_file():
        raise ValueError(f"global hooks config must be a regular JSON file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid global hooks JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("global hooks config must contain a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("global hooks config hooks must be a JSON object")
    return data


def _core_runtime_status(paths: dict[str, Path]) -> tuple[bool, dict[str, Any], list[str]]:
    errors: list[str] = []
    dispatcher = paths["dispatcher"]
    core_state_path = paths["core_state"]
    core_state: dict[str, Any] = {}
    if not dispatcher.is_file() or dispatcher.is_symlink():
        errors.append("Core-owned global dispatcher is missing or is not a regular file")
    else:
        try:
            source = dispatcher.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"Core-owned global dispatcher cannot be read: {exc}")
        else:
            if CORE_DISPATCHER_SCHEMA not in source:
                errors.append("Core-owned global dispatcher schema marker is missing")
            if CORE_USER_PROMPT_RUNTIME_API not in source:
                errors.append("Core-owned UserPromptSubmit Lesson runtime marker is missing")
    if not core_state_path.is_file() or core_state_path.is_symlink():
        errors.append("Core-owned global dispatcher state is missing or invalid")
    else:
        try:
            loaded = json.loads(core_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid Core-owned global dispatcher state: {exc}")
        else:
            core_state = loaded if isinstance(loaded, dict) else {}
            if (
                core_state.get("schema_version") != 2
                or core_state.get("wrapper_source") != "channel-managed"
                or core_state.get("source_repository") != "MetaInFLow/EvoZeus"
                or core_state.get("runtime_api") != CORE_USER_PROMPT_RUNTIME_API
                or core_state.get("trust_status") != "verified_by_product_manifest"
            ):
                errors.append("Core-owned global dispatcher state is not product-managed")
    return not errors, core_state, errors


def _entry_has_dispatcher(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    handlers = entry.get("hooks")
    return isinstance(handlers, list) and any(
        isinstance(handler, dict) and handler.get("command") == GLOBAL_DISPATCHER_COMMAND
        for handler in handlers
    )


def _without_dispatcher_handlers(entry: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    handlers = entry.get("hooks")
    if not isinstance(handlers, list):
        raise ValueError("global hook entry hooks must be a list")
    preserved_handlers: list[dict[str, Any]] = []
    removed = False
    for handler in handlers:
        if not isinstance(handler, dict):
            raise ValueError("global hook handlers must be objects")
        if handler.get("command") == GLOBAL_DISPATCHER_COMMAND:
            removed = True
        else:
            preserved_handlers.append(handler)
    if not preserved_handlers:
        return None, removed
    preserved_entry = json.loads(json.dumps(entry))
    preserved_entry["hooks"] = preserved_handlers
    return preserved_entry, removed


def _merge_event_registration(
    hooks: dict[str, Any], event: str
) -> bool:
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise ValueError(f"global hooks {event} must be a list")
    preserved: list[dict[str, Any]] = []
    found = False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"global hooks {event}[{index}] must be an object")
        try:
            preserved_entry, removed = _without_dispatcher_handlers(entry)
        except ValueError as exc:
            raise ValueError(f"global hooks {event}[{index}]: {exc}") from exc
        found = found or removed
        if preserved_entry is not None:
            preserved.append(preserved_entry)
    hooks[event] = [*preserved, _dispatcher_entry(event)]
    return found


def _merge_dispatcher_registration(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    merged = json.loads(json.dumps(config))
    hooks = merged.setdefault("hooks", {})
    found = False
    for event in (GLOBAL_HOOK_EVENT, GLOBAL_PROMPT_HOOK_EVENT):
        found = _merge_event_registration(hooks, event) or found
    if merged == config:
        return merged, "already_registered"
    return merged, "refresh" if found else "merge"


def _without_dispatcher_registration(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    updated = json.loads(json.dumps(config))
    hooks = updated.setdefault("hooks", {})
    removed = False
    for event in (GLOBAL_HOOK_EVENT, GLOBAL_PROMPT_HOOK_EVENT):
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"global hooks {event} must be a list")
        preserved: list[dict[str, Any]] = []
        event_removed = False
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(f"global hooks {event}[{index}] must be an object")
            try:
                preserved_entry, entry_removed = _without_dispatcher_handlers(entry)
            except ValueError as exc:
                raise ValueError(f"global hooks {event}[{index}]: {exc}") from exc
            removed = removed or entry_removed
            event_removed = event_removed or entry_removed
            if preserved_entry is not None:
                preserved.append(preserved_entry)
        if event_removed:
            if preserved:
                hooks[event] = preserved
            else:
                hooks.pop(event, None)
    return updated, removed


def _latest_changelog_version(wrapper_root: Path) -> str | None:
    path = wrapper_root / "CHANGELOG.md"
    if not path.is_file():
        return None
    match = re.search(r"(?m)^## \[(v\d+\.\d+\.\d+)\]", path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _version_key(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _state_payload(wrapper_root: Path) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "owner": "MetaInFLow/EvoZeus-CoEvolve",
        "lifecycle_version": _latest_changelog_version(wrapper_root),
        "runtime_owner": "MetaInFLow/EvoZeus",
        "runtime_api": CORE_USER_PROMPT_RUNTIME_API,
        "command": GLOBAL_DISPATCHER_COMMAND,
        "events": [GLOBAL_HOOK_EVENT, GLOBAL_PROMPT_HOOK_EVENT],
        "installation_status": "installed",
        "trust_status": "pending_review",
        "trust_status_source": "requires_user_confirmation_after_codex_hooks_review",
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _snapshot(paths: dict[str, Path], backup_root: Path) -> dict[str, bytes | None]:
    snapshots: dict[str, bytes | None] = {}
    backup_root.mkdir(parents=True, exist_ok=False)
    for name in ("hooks", "state"):
        path = paths[name]
        data = path.read_bytes() if path.is_file() else None
        snapshots[name] = data
        if data is not None:
            destination = backup_root / name
            destination.write_bytes(data)
    (backup_root / "snapshot.json").write_text(
        json.dumps({name: data is not None for name, data in snapshots.items()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshots


def _restore(paths: dict[str, Path], snapshots: dict[str, bytes | None]) -> None:
    for name, data in snapshots.items():
        path = paths[name]
        if data is None:
            if path.is_file() or path.is_symlink():
                path.unlink()
        else:
            _atomic_write(path, data)


def plan_global_hook_install(home: Path, wrapper_root: Path) -> dict[str, Any]:
    paths = _paths(home)
    errors: list[str] = []
    action = None
    try:
        config = _read_hooks_config(paths["hooks"])
        _, action = _merge_dispatcher_registration(config)
    except ValueError as exc:
        errors.append(str(exc))
    runtime_ready, _, runtime_errors = _core_runtime_status(paths)
    errors.extend(runtime_errors)
    return {
        "stage": "global_hook_install",
        "status": "blocked" if errors else "planned",
        "writes": False,
        "approved": False,
        "registration_action": action,
        "hooks_config_exists": paths["hooks"].is_file(),
        "dispatcher_exists": paths["dispatcher"].is_file(),
        "core_runtime_ready": runtime_ready,
        "core_state_exists": paths["core_state"].is_file(),
        "state_exists": paths["state"].is_file(),
        "errors": errors,
    }


def read_global_hook_status(home: Path) -> dict[str, Any]:
    paths = _paths(home)
    runtime_ready, core_state, errors = _core_runtime_status(paths)
    session_registered = False
    prompt_registered = False
    try:
        config = _read_hooks_config(paths["hooks"])
        session_start = config.get("hooks", {}).get(GLOBAL_HOOK_EVENT, [])
        if not isinstance(session_start, list):
            raise ValueError(f"global hooks {GLOBAL_HOOK_EVENT} must be a list")
        session_registered = any(_entry_has_dispatcher(entry) for entry in session_start)
        prompt_submit = config.get("hooks", {}).get(GLOBAL_PROMPT_HOOK_EVENT, [])
        if not isinstance(prompt_submit, list):
            raise ValueError(f"global hooks {GLOBAL_PROMPT_HOOK_EVENT} must be a list")
        prompt_registered = any(_entry_has_dispatcher(entry) for entry in prompt_submit)
    except ValueError as exc:
        errors.append(str(exc))
    state: dict[str, Any] = {}
    if paths["state"].is_file() and not paths["state"].is_symlink():
        try:
            loaded = json.loads(paths["state"].read_text(encoding="utf-8"))
            state = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid global hook state JSON: {exc}")
        if state and (
            state.get("schema_version") != 2
            or state.get("owner") != "MetaInFLow/EvoZeus-CoEvolve"
            or state.get("runtime_api") != CORE_USER_PROMPT_RUNTIME_API
        ):
            errors.append("global hook lifecycle state has an incompatible owner or runtime API")
            state = {}
    elif paths["state"].exists():
        errors.append("global hook lifecycle state must be a regular file")
    registered = session_registered and prompt_registered
    any_registered = session_registered or prompt_registered
    installed = registered and runtime_ready and bool(state)
    upgrade_required = any_registered and not installed
    return {
        "stage": "global_hook_status",
        "status": "installed" if installed else "upgrade_required" if upgrade_required else "not_installed",
        "writes": False,
        "mode": "global_session_dispatcher",
        "capabilities": [
            "global_session_dispatcher",
            "global_prompt_lesson_watcher",
        ],
        "scope": "all_registered_wrapped_skills",
        "native_enforced": installed and state.get("trust_status") == "trusted",
        "registration_installed": registered,
        "any_registration_installed": any_registered,
        "session_registration_installed": session_registered,
        "prompt_registration_installed": prompt_registered,
        "dispatcher_installed": paths["dispatcher"].is_file(),
        "runtime_endpoint_ready": runtime_ready,
        "runtime_owner": "MetaInFLow/EvoZeus",
        "runtime_api": CORE_USER_PROMPT_RUNTIME_API,
        "core_state_installed": paths["core_state"].is_file(),
        "core_runtime_version": core_state.get("core_version"),
        "state_installed": bool(state),
        "trust_status": state.get("trust_status", "not_installed"),
        "installed_version": state.get("lifecycle_version"),
        "errors": errors,
    }


def apply_global_hook_install(home: Path, wrapper_root: Path, *, approve: bool = False) -> dict[str, Any]:
    plan = plan_global_hook_install(home, wrapper_root)
    if plan["status"] == "blocked":
        return plan
    if not approve:
        return {**plan, "status": "approval_required"}

    paths = _paths(home)
    config = _read_hooks_config(paths["hooks"])
    merged, registration_action = _merge_dispatcher_registration(config)
    status = read_global_hook_status(home)
    if (
        registration_action == "already_registered"
        and status["status"] == "installed"
    ):
        return {**plan, "status": "already_installed", "approved": True, "errors": []}

    transaction_id = _utc_transaction_id()
    backup_root = paths["backups"] / transaction_id
    snapshots = _snapshot(paths, backup_root)
    try:
        _atomic_write(
            paths["hooks"],
            (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        _atomic_write(
            paths["state"],
            (json.dumps(_state_payload(wrapper_root), ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    except Exception:
        _restore(paths, snapshots)
        raise
    return {
        **plan,
        "status": "installed",
        "writes": True,
        "approved": True,
        "registration_action": registration_action,
        "backup": str(backup_root),
        "trust_status": "pending_review",
        "errors": [],
    }


def apply_global_hook_uninstall(home: Path, *, approve: bool = False) -> dict[str, Any]:
    paths = _paths(home)
    try:
        config = _read_hooks_config(paths["hooks"])
        updated, removed = _without_dispatcher_registration(config)
    except ValueError as exc:
        return {
            "stage": "global_hook_uninstall",
            "status": "blocked",
            "writes": False,
            "errors": [str(exc)],
        }
    if not approve:
        return {
            "stage": "global_hook_uninstall",
            "status": "approval_required",
            "writes": False,
            "registration_found": removed,
            "errors": [],
        }
    if not removed and not paths["state"].exists():
        return {
            "stage": "global_hook_uninstall",
            "status": "already_uninstalled",
            "writes": False,
            "errors": [],
        }

    backup_root = paths["backups"] / _utc_transaction_id()
    snapshots = _snapshot(paths, backup_root)
    try:
        if removed:
            _atomic_write(
                paths["hooks"],
                (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        state_path = paths["state"]
        if state_path.is_file() or state_path.is_symlink():
            state_path.unlink()
    except Exception:
        _restore(paths, snapshots)
        raise
    return {
        "stage": "global_hook_uninstall",
        "status": "uninstalled",
        "writes": True,
        "backup": str(backup_root),
        "errors": [],
    }


def record_global_hook_trust(home: Path, *, status: str, approve: bool = False) -> dict[str, Any]:
    allowed = {"pending_review", "trusted", "rejected"}
    if status not in allowed:
        raise ValueError(f"global hook trust status must be one of: {', '.join(sorted(allowed))}")
    current = read_global_hook_status(home)
    if current["status"] != "installed":
        return {
            "stage": "global_hook_trust",
            "status": "blocked",
            "writes": False,
            "errors": ["global hook must be installed before recording trust"],
        }
    if not approve:
        return {
            "stage": "global_hook_trust",
            "status": "approval_required",
            "writes": False,
            "requested_trust_status": status,
            "errors": [],
        }

    paths = _paths(home)
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    state["trust_status"] = status
    state["trust_status_source"] = "user_confirmed_after_codex_hooks_review"
    state["trust_status_recorded_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(
        paths["state"],
        (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return {
        "stage": "global_hook_trust",
        "status": status,
        "writes": True,
        "trust_status": status,
        "errors": [],
    }


def _lifecycle_module():
    try:
        from . import evozeus_wrapper_lifecycle as lifecycle
    except ImportError:
        import evozeus_wrapper_lifecycle as lifecycle
    return lifecycle


def _registered_upgrade_targets(home: Path) -> tuple[list[dict[str, Any]], list[str]]:
    projects_root = home.expanduser().resolve() / ".evozeus/.projects"
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    if not projects_root.is_dir():
        return targets, errors
    lifecycle = _lifecycle_module()
    for owner_dir in sorted(projects_root.iterdir()):
        if not owner_dir.is_dir():
            continue
        for pointer in sorted(owner_dir.iterdir()):
            if not pointer.is_symlink():
                errors.append(f"invalid project pointer type: {owner_dir.name}/{pointer.name}")
                continue
            if not pointer.exists():
                errors.append(f"broken project pointer: {owner_dir.name}/{pointer.name}")
                continue
            try:
                canonical = pointer.resolve(strict=True)
            except OSError:
                errors.append(f"unresolvable project pointer: {owner_dir.name}/{pointer.name}")
                continue
            if not canonical.is_dir():
                errors.append(f"project pointer target is not a directory: {owner_dir.name}/{pointer.name}")
                continue
            try:
                repo_root = lifecycle.independent_repo_root(canonical)
            except ValueError as exc:
                errors.append(f"invalid Harness repository boundary {owner_dir.name}/{pointer.name}: {exc}")
                continue
            if canonical != repo_root:
                errors.append(
                    "project pointer must resolve to the independent Git repository root: "
                    f"{owner_dir.name}/{pointer.name}; repo_root={repo_root}"
                )
                continue
            nested = lifecycle.nested_harness_manifests(repo_root)
            if nested:
                errors.append(
                    f"nested Harness manifests are forbidden in {owner_dir.name}/{pointer.name}: "
                    + ", ".join(nested)
                )
                continue
            manifest_path = canonical / TARGET_MANIFEST
            if not manifest_path.is_file():
                legacy = next(
                    (canonical / candidate for candidate in LEGACY_TARGET_MANIFESTS if (canonical / candidate).is_file()),
                    None,
                )
                if legacy is None:
                    continue
                manifest_path = legacy
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                errors.append(f"invalid wrapper manifest: {owner_dir.name}/{pointer.name}")
                continue
            expected_repo = f"{owner_dir.name}/{pointer.name}"
            if not isinstance(manifest, dict) or manifest.get("canonical_repo") != expected_repo:
                errors.append(f"canonical repo mismatch: {expected_repo}")
                continue
            version = manifest.get("wrapper_version")
            if not isinstance(version, str) or _version_key(version) is None:
                errors.append(f"invalid wrapper version: {expected_repo}")
                continue
            targets.append(
                {
                    "repo": expected_repo,
                    "target": canonical,
                    "manifest_path": manifest_path,
                    "wrapper_version": version,
                }
            )
    return targets, errors


def _resolve_authoritative_upgrade_latest(
    home: Path,
    latest_resolver=None,
) -> dict[str, Any]:
    if latest_resolver is not None:
        return latest_resolver()
    explicit = os.environ.get("EVOZEUS_WRAPPER_LATEST_VERSION")
    if isinstance(explicit, str) and _version_key(explicit) is not None:
        return {"version": explicit, "source": "environment", "error": None}
    cache_path = home / LATEST_VERSION_CACHE
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    cached_version = cache.get("version") if isinstance(cache, dict) else None
    checked_at = cache.get("checked_at_epoch") if isinstance(cache, dict) else None
    if (
        isinstance(cached_version, str)
        and _version_key(cached_version) is not None
        and isinstance(checked_at, int)
        and max(0, int(time.time()) - checked_at) <= LATEST_VERSION_CACHE_LIMIT_SECONDS
    ):
        return {"version": cached_version, "source": "dispatcher_cache", "error": None}
    lifecycle = _lifecycle_module()
    return lifecycle.resolve_latest_wrapper_release()


def _target_write_errors(target: Path, migration: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not os.access(target, os.W_OK | os.X_OK):
        return ["target repository is not writable"]
    candidates = {
        migration.get("instruction_surface", "SKILL.md"),
        TARGET_MANIFEST.as_posix(),
        ".codex/hooks.json",
        migration.get("migration_record"),
        *migration.get("managed_file_refreshes", []),
        *migration.get("text_rewrite_candidates", []),
        *migration.get("generated_cache_candidates", []),
        *(
            item.get(key)
            for item in migration.get("moves", [])
            for key in ("source", "destination")
            if isinstance(item, dict)
        ),
    }
    for relative in sorted(item for item in candidates if isinstance(item, str) and item):
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"migration path escapes target repository: {relative}")
            continue
        path = target / relative
        cursor = path
        symlink_component = None
        while cursor != target:
            if cursor.is_symlink():
                symlink_component = cursor
                break
            cursor = cursor.parent
        if symlink_component is not None:
            errors.append(
                "migration path contains a symlink: "
                + str(symlink_component.relative_to(target))
            )
            continue
        if path.exists() and not os.access(path, os.W_OK):
            errors.append(f"migration path is not writable: {relative}")
            continue
        parent = path if path.is_dir() else path.parent
        while not parent.exists() and parent != target:
            parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            errors.append(
                "migration path parent is not writable: "
                + str(parent.relative_to(target) if parent != target else Path("."))
            )
    return errors


def _wrapper_upgrade_source_errors(wrapper_root: Path) -> list[str]:
    return [
        f"wrapper upgrade source is missing: {relative}"
        for relative in WRAPPER_UPGRADE_SOURCE_FILES
        if not (wrapper_root / relative).is_file()
    ]


def plan_upgrade_all(
    home: Path,
    wrapper_root: Path,
    latest_version: str,
    *,
    latest_resolver=None,
) -> dict[str, Any]:
    home = home.expanduser().resolve()
    wrapper_root = wrapper_root.expanduser().resolve()
    latest_key = _version_key(latest_version)
    if latest_key is None:
        return {
            "stage": "harness_upgrade_all",
            "status": "blocked",
            "writes": False,
            "errors": ["latest version must use vMAJOR.MINOR.PATCH"],
            "targets": [],
        }
    source_version = _latest_changelog_version(wrapper_root)
    if source_version != latest_version:
        return {
            "stage": "harness_upgrade_all",
            "status": "blocked",
            "writes": False,
            "errors": [
                f"wrapper source must be updated to {latest_version} before target migrations; current={source_version}"
            ],
            "targets": [],
        }
    source_errors = _wrapper_upgrade_source_errors(wrapper_root)
    if source_errors:
        return {
            "stage": "harness_upgrade_all",
            "status": "blocked",
            "writes": False,
            "errors": source_errors,
            "targets": [],
        }

    global_hook_status = read_global_hook_status(home)
    if (
        global_hook_status["any_registration_installed"]
        and not global_hook_status["runtime_endpoint_ready"]
    ):
        return {
            "stage": "harness_upgrade_all",
            "status": "blocked",
            "writes": False,
            "errors": global_hook_status["errors"],
            "targets": [],
        }

    latest_resolution = _resolve_authoritative_upgrade_latest(home, latest_resolver)
    authoritative_latest = latest_resolution.get("version")
    if authoritative_latest != latest_version:
        return {
            "stage": "harness_upgrade_all",
            "status": "blocked",
            "writes": False,
            "errors": [
                "requested latest version does not match the authoritative wrapper release; "
                f"requested={latest_version}; authoritative={authoritative_latest or 'unknown'}"
            ],
            "latest_version": latest_version,
            "latest_source": latest_resolution.get("source", "unavailable"),
            "targets": [],
        }
    registered, discovery_errors = _registered_upgrade_targets(home)
    if discovery_errors:
        return {
            "stage": "harness_upgrade_all",
            "status": "blocked",
            "writes": False,
            "errors": discovery_errors,
            "targets": [],
        }
    outdated = [
        target
        for target in registered
        if _version_key(target["wrapper_version"]) < latest_key
    ]
    if not outdated:
        return {
            "stage": "harness_upgrade_all",
            "status": "up_to_date",
            "writes": False,
            "errors": [],
            "latest_version": latest_version,
            "latest_source": latest_resolution.get("source", "unavailable"),
            "targets": [],
        }

    lifecycle = _lifecycle_module()
    target_plans: list[dict[str, Any]] = []
    errors: list[str] = []
    for target in outdated:
        migration = lifecycle.plan_target_layout_migration(
            target["target"],
            latest_version,
            require_clean_git=True,
        )
        public_target = {
            **target,
            "target": str(target["target"]),
            "manifest_path": str(target["manifest_path"]),
            "migration": migration,
        }
        target_plans.append(public_target)
        if migration.get("conflicts"):
            errors.extend(f"{target['repo']}: {item}" for item in migration["conflicts"])
        elif not migration.get("can_apply"):
            errors.append(f"{target['repo']}: migration plan is not applicable")
        errors.extend(
            f"{target['repo']}: {item}"
            for item in _target_write_errors(target["target"], migration)
        )
    return {
        "stage": "harness_upgrade_all",
        "status": "blocked" if errors else "planned",
        "writes": False,
        "errors": errors,
        "latest_version": latest_version,
        "latest_source": latest_resolution.get("source", "unavailable"),
        "target_count": len(target_plans),
        "targets": target_plans,
    }


def _snapshot_candidate_paths(target: Path, migration: dict[str, Any]) -> set[Path]:
    paths = {
        target / migration.get("instruction_surface", "SKILL.md"),
        target / ".codex/hooks.json",
        target / ".github/ISSUE_TEMPLATE/config.yml",
        target / ".github/workflows/evozeus-wrapper-preflight.yml",
        target / migration.get("migration_record", ".evozeus-wrapper/docs/migrations/refresh.md"),
    }
    for relative in migration.get("managed_file_refreshes", []):
        paths.add(target / relative)
    for relative in migration.get("text_rewrite_candidates", []):
        paths.add(target / relative)
    for relative in migration.get("generated_cache_candidates", []):
        paths.add(target / relative)
    for move in migration.get("moves", []):
        paths.add(target / move["source"])
        paths.add(target / move["destination"])
    for directory in (".evozeus-wrapper", ".evozeus_evoinfra", ".evozeus"):
        root = target / directory
        if root.is_dir() and not root.is_symlink():
            paths.update(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    return paths


def _candidate_directories(target: Path, candidates: set[Path]) -> set[Path]:
    directories: set[Path] = set()
    for candidate in candidates:
        parent = candidate.parent
        while parent != target and target in parent.parents:
            directories.add(parent)
            parent = parent.parent
    for relative in (".evozeus-wrapper", ".evozeus_evoinfra", ".evozeus"):
        root = target / relative
        if root.is_dir() and not root.is_symlink():
            directories.add(root)
            directories.update(path for path in root.rglob("*") if path.is_dir())
    return directories


def _snapshot_target(
    target: Path,
    migration: dict[str, Any],
    backup_root: Path,
) -> dict[str, Any]:
    candidates = _snapshot_candidate_paths(target, migration)
    existing_directories = {
        str(path.relative_to(target))
        for path in _candidate_directories(target, candidates)
        if path.is_dir() and not path.is_symlink()
    }
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(candidates):
        relative = str(path.relative_to(target))
        exists = path.is_file() or path.is_symlink()
        item = {"exists": exists, "mode": path.lstat().st_mode if exists else None}
        files[relative] = item
        if exists:
            destination = backup_root / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                item["symlink"] = str(path.readlink())
            else:
                destination.write_bytes(path.read_bytes())
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / "snapshot.json").write_text(
        json.dumps(files, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "target": target,
        "migration": migration,
        "backup_root": backup_root,
        "files": files,
        "directories": existing_directories,
    }


def _restore_target(snapshot: dict[str, Any]) -> None:
    target: Path = snapshot["target"]
    migration = snapshot["migration"]
    files: dict[str, dict[str, Any]] = snapshot["files"]
    current_candidates = _snapshot_candidate_paths(target, migration)
    for path in sorted(current_candidates, reverse=True):
        relative = str(path.relative_to(target))
        if relative not in files or not files[relative]["exists"]:
            if path.is_file() or path.is_symlink():
                path.unlink()
    for relative, item in files.items():
        path = target / relative
        if not item["exists"]:
            if path.is_file() or path.is_symlink():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() or path.is_symlink():
            path.unlink()
        if item.get("symlink") is not None:
            path.symlink_to(item["symlink"])
        else:
            source = snapshot["backup_root"] / "files" / relative
            path.write_bytes(source.read_bytes())
            path.chmod(item["mode"])
    existing_directories: set[str] = snapshot["directories"]
    current_directories = _candidate_directories(target, _snapshot_candidate_paths(target, migration))
    for directory in sorted(current_directories, reverse=True):
        if str(directory.relative_to(target)) in existing_directories:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass


def apply_upgrade_all(
    home: Path,
    wrapper_root: Path,
    latest_version: str,
    *,
    approve: bool = False,
    latest_resolver=None,
    admin_resolver=None,
) -> dict[str, Any]:
    wrapper_root = wrapper_root.expanduser().resolve()
    plan = plan_upgrade_all(
        home,
        wrapper_root,
        latest_version,
        latest_resolver=latest_resolver,
    )
    if plan["status"] in {"blocked", "up_to_date"}:
        return plan
    if not approve:
        return {**plan, "status": "approval_required"}

    home = home.expanduser().resolve()
    lifecycle = _lifecycle_module()
    resolve_admin = admin_resolver or (
        lambda target, repo: lifecycle.require_repo_admin(Path(target), repo)
    )
    authorities: list[dict[str, Any]] = []
    try:
        for item in plan["targets"]:
            authorities.append(resolve_admin(item["target"], item["repo"]))
    except ValueError as exc:
        return {
            **plan,
            "status": "blocked",
            "writes": False,
            "errors": [str(exc)],
            "administrator_authorities": authorities,
        }
    refresh_installed_global_hook = read_global_hook_status(home)["any_registration_installed"]
    backup_root = home / HARNESS_UPGRADE_BACKUPS / _utc_transaction_id()
    snapshots: list[dict[str, Any]] = []
    for index, item in enumerate(plan["targets"]):
        label = item["repo"].replace("/", "--")
        snapshots.append(
            _snapshot_target(
                Path(item["target"]),
                item["migration"],
                backup_root / f"{index:04d}-{label}",
            )
        )

    results: list[dict[str, Any]] = []
    global_hook_refresh: dict[str, Any] = {
        "status": "not_installed",
        "writes": False,
    }
    try:
        for item in plan["targets"]:
            results.append(
                lifecycle.migrate_target_layout(
                    Path(item["target"]),
                    latest_version,
                    wrapper_root=wrapper_root,
                    require_clean_git=True,
                )
            )
        if refresh_installed_global_hook:
            global_hook_refresh = apply_global_hook_install(home, wrapper_root, approve=True)
            if global_hook_refresh["status"] not in {"installed", "already_installed"}:
                raise RuntimeError(
                    "global dispatcher refresh failed: "
                    + "; ".join(global_hook_refresh.get("errors", []))
                )
    except Exception as exc:
        for snapshot in reversed(snapshots):
            _restore_target(snapshot)
        return {
            **plan,
            "status": "rolled_back",
            "writes": False,
            "backup": str(backup_root),
            "errors": [str(exc)],
            "results": [],
            "global_hook_refresh": global_hook_refresh,
        }
    return {
        **plan,
        "status": "applied",
        "writes": True,
        "backup": str(backup_root),
        "errors": [],
        "results": results,
        "global_hook_refresh": global_hook_refresh,
        "administrator_authorities": authorities,
    }
