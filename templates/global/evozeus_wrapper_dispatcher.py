#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
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
USER_PROMPT_EVENT = "UserPromptSubmit"
SESSION_SIGNAL_ATTACHMENT = {
    "component_id": "session_signal",
    "repository": "MetaInFLow/EvoZeus-session-signal-skill",
    "component_version": "v0.1.1",
    "availability": "unreleased",
    "component_manifest": "contracts/lesson-candidate-v1.json",
    "component_manifest_sha256": "15edff23fe06a5cd16e12a4374bba256b980e1a0af0033c7af449c7f85e7e3f7",
    "api": "evozeus.session-signal.lesson-candidate.v1",
    "entrypoint": "scripts/evaluate_lesson_candidate.py",
}
SESSION_SIGNAL_COMPONENT_SCHEMA = "evozeus.session-signal.lesson-candidate-component.v1"
SESSION_SIGNAL_TIMEOUT_SECONDS = 1.5
SESSION_SIGNAL_MAX_REQUEST_BYTES = 256 * 1024
SESSION_SIGNAL_MAX_OUTPUT_BYTES = 16 * 1024
SESSION_SIGNAL_MAX_PROMPT_CHARS = 32_000
SESSION_SIGNAL_MAX_TARGETS = 256


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _product_manifest_digest(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{_sha256_bytes(canonical)}"


def _safe_relative_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return relative


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolved_directory(value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    raw = Path(value).expanduser()
    if not raw.is_absolute() or raw.is_symlink():
        return None
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _regular_file_under(root: Path, relative_value: object) -> Path | None:
    relative = _safe_relative_path(relative_value)
    if relative is None:
        return None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        try:
            mode = cursor.lstat().st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return None
    try:
        resolved = cursor.resolve(strict=True)
    except OSError:
        return None
    if not _contains(root, resolved) or not stat.S_ISREG(resolved.lstat().st_mode):
        return None
    return resolved


def resolve_session_signal_component(
    evozeus_home: Path,
    *,
    attachment: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Resolve one digest-bound Session Signal API from the active product channel."""
    attachment = SESSION_SIGNAL_ATTACHMENT if attachment is None else attachment
    active = read_json_object(evozeus_home / "active-channel.json")
    channel = active.get("channel") if active else None
    if channel not in {"stable", "uat"}:
        return None
    state = read_json_object(evozeus_home / "channel-state.json")
    channels = state.get("channels") if state else None
    entry = channels.get(channel) if isinstance(channels, dict) else None
    if not isinstance(entry, dict):
        return None
    manifest = entry.get("manifest")
    if (
        not isinstance(manifest, dict)
        or entry.get("manifest_digest") != _product_manifest_digest(manifest)
    ):
        return None
    install_root = _resolved_directory(entry.get("install_root"))
    component_roots = entry.get("component_roots")
    embedded_roots = entry.get("embedded_roots")
    if (
        install_root is None
        or not isinstance(component_roots, dict)
        or not isinstance(embedded_roots, dict)
    ):
        return None
    core_root = _resolved_directory(component_roots.get("evozeus"))
    session_root = _resolved_directory(embedded_roots.get("session_signal"))
    if (
        core_root is None
        or session_root is None
        or not _contains(install_root, core_root)
        or not _contains(install_root, session_root)
    ):
        return None
    embedded_map = manifest.get("embedded")
    embedded = embedded_map.get("session_signal") if isinstance(embedded_map, dict) else None
    if not isinstance(embedded, dict) or embedded.get("version") != attachment["component_version"]:
        return None
    embedded_path = _safe_relative_path(embedded.get("path"))
    required_paths = embedded.get("required_paths")
    if embedded_path is None or not isinstance(required_paths, list):
        return None
    try:
        expected_session_root = (core_root / embedded_path).resolve(strict=True)
    except OSError:
        return None
    if expected_session_root != session_root:
        return None
    if not {attachment["component_manifest"], attachment["entrypoint"]}.issubset(
        {value for value in required_paths if isinstance(value, str)}
    ):
        return None
    component_manifest_path = _regular_file_under(
        session_root,
        attachment["component_manifest"],
    )
    if component_manifest_path is None:
        return None
    manifest_bytes = component_manifest_path.read_bytes()
    if _sha256_bytes(manifest_bytes) != attachment["component_manifest_sha256"]:
        return None
    try:
        component_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(component_manifest, dict) or any(
        (
            component_manifest.get("schema_version") != SESSION_SIGNAL_COMPONENT_SCHEMA,
            component_manifest.get("component_version") != attachment["component_version"],
            component_manifest.get("api") != attachment["api"],
            component_manifest.get("entrypoint") != attachment["entrypoint"],
        )
    ):
        return None
    files = component_manifest.get("files")
    if not isinstance(files, list) or not files:
        return None
    verified_files: dict[str, Path] = {}
    for file_entry in files:
        if not isinstance(file_entry, dict):
            return None
        relative = file_entry.get("path")
        expected_sha256 = file_entry.get("sha256")
        path = _regular_file_under(session_root, relative)
        if (
            path is None
            or not isinstance(relative, str)
            or not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
            or _sha256_bytes(path.read_bytes()) != expected_sha256
        ):
            return None
        verified_files[relative] = path
    script = verified_files.get(attachment["entrypoint"])
    if script is None:
        return None
    return {
        "api": attachment["api"],
        "script": script,
        "component_root": session_root,
    }


def read_skill_name(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", text, re.DOTALL)
    if not frontmatter:
        return None
    match = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", frontmatter.group(1))
    return match.group(1).strip() if match else None


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


def discover_wrapped_targets(
    home: Path,
    *,
    evozeus_home: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    projects_root = (
        evozeus_home.expanduser().resolve() / ".projects"
        if evozeus_home is not None
        else home.expanduser().resolve() / PROJECTS_DIR
    )
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
            aliases = [expected_repo, pointer.name]
            instruction_surface = manifest.get("instruction_surface") or "SKILL.md"
            if isinstance(instruction_surface, str):
                relative_surface = Path(instruction_surface)
                if not relative_surface.is_absolute() and ".." not in relative_surface.parts:
                    declared_name = read_skill_name(canonical / relative_surface)
                    if declared_name:
                        aliases.append(declared_name)
            targets.append(
                {
                    "canonical_path": canonical,
                    "repo": expected_repo,
                    "aliases": tuple(dict.fromkeys(aliases)),
                    "wrapper_version": version,
                    "manifest_path": manifest_path,
                }
            )
    return targets, errors


def _lesson_component_request(
    hook_input: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    api: str,
) -> dict[str, Any]:
    bounded_targets = targets if len(targets) <= SESSION_SIGNAL_MAX_TARGETS else []
    return {
        "schema_version": api,
        "event_name": USER_PROMPT_EVENT,
        "prompt": hook_input.get("prompt"),
        "cwd": hook_input.get("cwd"),
        "targets": [
            {
                "repo": target["repo"],
                "canonical_path": str(target["canonical_path"]),
                "aliases": list(target.get("aliases", ())),
            }
            for target in bounded_targets
        ],
    }


def _invoke_lesson_component(
    component: dict[str, Any],
    request: dict[str, Any],
    *,
    runner=subprocess.run,
) -> dict[str, Any] | None:
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > SESSION_SIGNAL_MAX_REQUEST_BYTES:
        return None
    try:
        result = runner(
            [sys.executable, str(component["script"])],
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=component["component_root"],
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            },
            timeout=SESSION_SIGNAL_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > SESSION_SIGNAL_MAX_OUTPUT_BYTES:
        return None
    try:
        response = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(response, dict) or response.get("schema_version") != component["api"]:
        return None
    candidate = response.get("candidate")
    if candidate is False and set(response) == {"schema_version", "candidate"}:
        return response
    if candidate is not True or set(response) != {
        "schema_version",
        "candidate",
        "target_repo",
        "model_guidance",
    }:
        return None
    guidance = response.get("model_guidance")
    target_repo = response.get("target_repo")
    registered_repos = {target["repo"] for target in request["targets"]}
    if (
        not isinstance(guidance, str)
        or not guidance
        or len(guidance) > 4_096
        or (target_repo is not None and target_repo not in registered_repos)
        or any(
            private_value and private_value in guidance
            for private_value in (
                str(request.get("prompt") or ""),
                str(request.get("cwd") or ""),
                str(component["component_root"]),
                str(component["script"]),
                *(target["canonical_path"] for target in request["targets"]),
            )
        )
    ):
        return None
    return response


def evaluate_user_prompt_submit(
    home: Path,
    hook_input: dict[str, Any],
    *,
    evozeus_home: Path | None = None,
    attachment: dict[str, str] | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    if hook_input.get("hook_event_name") != USER_PROMPT_EVENT:
        return {"continue": True}
    prompt = hook_input.get("prompt")
    if not isinstance(prompt, str) or len(prompt) > SESSION_SIGNAL_MAX_PROMPT_CHARS:
        return {"continue": True}
    product_home = (
        evozeus_home.expanduser().resolve()
        if evozeus_home is not None
        else home.expanduser().resolve() / ".evozeus"
    )
    try:
        component = resolve_session_signal_component(product_home, attachment=attachment)
        if component is None:
            return {"continue": True}
        targets, _ = discover_wrapped_targets(home, evozeus_home=product_home)
        request = _lesson_component_request(hook_input, targets, api=component["api"])
        response = _invoke_lesson_component(component, request, runner=runner)
    except Exception:
        return {"continue": True}
    if not response or response.get("candidate") is not True:
        return {"continue": True}
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": USER_PROMPT_EVENT,
            "additionalContext": response["model_guidance"],
        },
    }


def _allow(message: str, next_action: str, additional_context: str | None = None) -> dict[str, Any]:
    context = f"evozeus_global_gate=allow; next_action={next_action}"
    if additional_context:
        context = f"{context}; {additional_context}"
    return {
        "continue": True,
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
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
        return _allow(
            f"检测到 {outdated_count} 个 EvoZeus harness 落后，最新版本为 {latest}。"
            "正常业务继续；普通 Skill 调用不授权 Harness 升级、迁移、创建分支或 worktree。"
            "只有用户明确请求 Harness 维护或升级后，才生成 dry-run 方案并单独确认写入。"
            "若本任务选中了落后 Skill，向用户展示一行‘当前 Harness 版本 → latest’，说明兼容并继续运行。",
            "continue_business_without_harness_writes",
            (
                "selected_skill_notice=current_to_latest_advisory; "
                f"latest_harness_version={latest}"
            ),
        )
    return _allow("EvoZeus wrapper harnesses are current.", "none")


def main() -> int:
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        hook_input = {}
    if hook_input.get("hook_event_name") == USER_PROMPT_EVENT:
        try:
            product_home = Path(
                os.environ.get("EVOZEUS_HOME", Path.home() / ".evozeus")
            )
            payload = evaluate_user_prompt_submit(
                home=Path.home(),
                evozeus_home=product_home,
                hook_input=hook_input,
            )
        except Exception:
            payload = {"continue": True}
    else:
        payload = evaluate_session_start(home=Path.home(), hook_input=hook_input)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
