#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LATEST_VERSION_ENV = "EVOZEUS_WRAPPER_LATEST_VERSION"
LATEST_RELEASE_URL = "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve/releases/latest"
PROJECTS_DIR = Path(".evozeus/.projects")
CACHE_PATH = Path(".evozeus/cache/evozeus-wrapper-latest.json")
CACHE_TTL_SECONDS = 3600
STALE_CACHE_LIMIT_SECONDS = 86400
MANIFEST_CANDIDATES = (
    Path(".evozeus-wrapper/wrapper.json"),
    Path(".evozeus_evoinfra/wrapper.json"),
    Path(".evozeus/wrapper.json"),
)


def version_key(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def fetch_latest_release() -> dict[str, str | None]:
    request = Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "EvoZeus-CoEvolve-global-dispatcher",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"version": None, "url": None, "error": str(exc)}
    version = payload.get("tag_name") if isinstance(payload, dict) else None
    url = payload.get("html_url") if isinstance(payload, dict) else None
    if not isinstance(version, str) or version_key(version) is None:
        return {"version": None, "url": None, "error": "latest release has no valid tag"}
    return {"version": version, "url": url if isinstance(url, str) else None, "error": None}


def _write_cache(path: Path, version: str, checked_at_epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {"version": version, "checked_at_epoch": checked_at_epoch},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def resolve_latest_version(
    home: Path,
    *,
    now_epoch: int | None = None,
    environment: dict[str, str] | None = None,
    fetcher=None,
) -> dict[str, str | None]:
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    environment = os.environ if environment is None else environment
    explicit = environment.get(LATEST_VERSION_ENV)
    if explicit and version_key(explicit):
        return {"version": explicit, "source": "environment", "error": None}

    cache_path = home.expanduser().resolve() / CACHE_PATH
    cache = read_json_object(cache_path) or {}
    cached_version = cache.get("version")
    checked_at = cache.get("checked_at_epoch")
    cache_age = None
    if isinstance(cached_version, str) and version_key(cached_version) and isinstance(checked_at, int):
        cache_age = max(0, now_epoch - checked_at)
        if cache_age <= CACHE_TTL_SECONDS:
            return {"version": cached_version, "source": "fresh_cache", "error": None}

    remote = (fetcher or fetch_latest_release)()
    remote_version = remote.get("version")
    if isinstance(remote_version, str) and version_key(remote_version):
        try:
            _write_cache(cache_path, remote_version, now_epoch)
        except OSError:
            pass
        return {"version": remote_version, "source": "github_latest_release", "error": None}

    if cache_age is not None and cache_age <= STALE_CACHE_LIMIT_SECONDS:
        return {
            "version": cached_version,
            "source": "stale_cache",
            "error": remote.get("error"),
        }
    return {
        "version": None,
        "source": "unavailable",
        "error": remote.get("error") or "latest release is unavailable",
    }


def discover_wrapped_targets(home: Path) -> tuple[list[dict[str, Any]], list[str]]:
    projects_root = home.expanduser().resolve() / PROJECTS_DIR
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    if not projects_root.is_dir():
        return targets, errors

    for owner_dir in sorted(projects_root.iterdir()):
        if not owner_dir.is_dir():
            continue
        for pointer in sorted(owner_dir.iterdir()):
            if not pointer.is_symlink():
                errors.append("invalid_project_pointer_type")
                continue
            if not pointer.exists():
                errors.append("broken_project_pointer")
                continue
            try:
                canonical = pointer.resolve(strict=True)
            except OSError:
                errors.append("unresolvable_project_pointer")
                continue
            if not canonical.is_dir():
                errors.append("project_pointer_target_not_directory")
                continue
            manifest_path = next(
                (canonical / candidate for candidate in MANIFEST_CANDIDATES if (canonical / candidate).is_file()),
                None,
            )
            if manifest_path is None:
                continue
            manifest = read_json_object(manifest_path)
            if not manifest:
                errors.append("invalid_wrapper_manifest")
                continue
            expected_repo = f"{owner_dir.name}/{pointer.name}"
            if manifest.get("canonical_repo") != expected_repo:
                errors.append("canonical_repo_mismatch")
                continue
            version = manifest.get("wrapper_version")
            if not isinstance(version, str) or version_key(version) is None:
                errors.append("invalid_wrapper_version")
                continue
            targets.append(
                {
                    "canonical_path": canonical,
                    "repo": expected_repo,
                    "wrapper_version": version,
                    "manifest_path": manifest_path,
                }
            )
    return targets, errors


def _allow(message: str, next_action: str) -> dict[str, Any]:
    return {
        "continue": True,
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"evozeus_global_gate=allow; next_action={next_action}",
        },
    }


def _block(reason: str, message: str, next_action: str) -> dict[str, Any]:
    return {
        "continue": False,
        "stopReason": reason,
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"evozeus_global_gate=block; next_action={next_action}",
        },
    }


def evaluate_session_start(
    home: Path,
    *,
    latest_resolver=None,
    hook_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets, errors = discover_wrapped_targets(home)
    if errors:
        return _block(
            f"检测到 {len(errors)} 个本地 harness 注册异常。是否现在修复？",
            "EvoZeus harness source contract 检查未通过；请先运行全局诊断。",
            "evozeus_harness_repair_all",
        )

    resolution = (
        latest_resolver()
        if latest_resolver is not None
        else resolve_latest_version(home=home)
    )
    latest = resolution.get("version")
    if not isinstance(latest, str) or version_key(latest) is None:
        return _allow(
            "EvoZeus wrapper latest release is unavailable; continuing without claiming current status.",
            "retry_evozeus_latest_release_lookup",
        )

    latest_key = version_key(latest)
    outdated_count = sum(
        1
        for target in targets
        if version_key(target["wrapper_version"]) is not None
        and version_key(target["wrapper_version"]) < latest_key
    )
    if outdated_count:
        return _block(
            f"检测到 {outdated_count} 个 EvoZeus harness 落后，最新版本为 {latest}。是否升级全部？",
            "回复‘升级全部’执行统一预检与升级；回复‘稍后’仅跳过本次任务。",
            "evozeus_harness_upgrade_all",
        )
    return _allow("EvoZeus wrapper harnesses are current.", "none")


def main() -> int:
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        hook_input = {}
    payload = evaluate_session_start(home=Path.home(), hook_input=hook_input)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
