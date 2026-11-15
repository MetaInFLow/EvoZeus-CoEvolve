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


def discover_wrapped_targets(
    home: Path,
    problems: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    projects_root = home.expanduser().resolve() / PROJECTS_DIR
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    if not projects_root.is_dir():
        return targets, errors

    def record(kind: str, pointer: Path) -> None:
        errors.append(kind)
        if problems is not None:
            problems.append(
                {
                    "kind": kind,
                    "repo": f"{pointer.parent.name}/{pointer.name}",
                    "pointer": pointer,
                }
            )

    for owner_dir in sorted(projects_root.iterdir()):
        if not owner_dir.is_dir():
            continue
        for pointer in sorted(owner_dir.iterdir()):
            if not pointer.is_symlink():
                record("invalid_project_pointer_type", pointer)
                continue
            if not pointer.exists():
                record("broken_project_pointer", pointer)
                continue
            try:
                canonical = pointer.resolve(strict=True)
            except OSError:
                record("unresolvable_project_pointer", pointer)
                continue
            if not canonical.is_dir():
                record("project_pointer_target_not_directory", pointer)
                continue
            manifest_path = next(
                (canonical / candidate for candidate in MANIFEST_CANDIDATES if (canonical / candidate).is_file()),
                None,
            )
            if manifest_path is None:
                continue
            manifest = read_json_object(manifest_path)
            if not manifest:
                record("invalid_wrapper_manifest", pointer)
                continue
            expected_repo = f"{owner_dir.name}/{pointer.name}"
            if manifest.get("canonical_repo") != expected_repo:
                record("canonical_repo_mismatch", pointer)
                continue
            version = manifest.get("wrapper_version")
            if not isinstance(version, str) or version_key(version) is None:
                record("invalid_wrapper_version", pointer)
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


def _allow(message: str | None, next_action: str, additional_context: str | None = None) -> dict[str, Any]:
    """Let the session continue; only speak to the user when there is something to do."""
    context = f"evozeus_global_gate=allow; next_action={next_action}"
    if additional_context:
        context = f"{context}; {additional_context}"
    payload: dict[str, Any] = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
    }
    if message:
        payload["systemMessage"] = message
    return payload


def repair_command() -> str:
    return f'python3 "{Path(__file__).resolve()}" --repair-projects'


def _registration_problem_notice(count: int) -> dict[str, Any]:
    """A broken local registration must never stop unrelated work.

    The affected Skills are skipped; everything else runs normally. The agent is told
    not to maintain, upgrade or publish harness files until the registration is repaired.
    """
    command = repair_command()
    return {
        "continue": True,
        "systemMessage": (
            f"有 {count} 个已登记的 Skill 找不到了（可能挪了位置或改了名），已先跳过，不影响现在的工作。"
            f"需要修复时运行：{command}"
        ),
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "evozeus_global_gate=degraded; "
                "next_action=continue_business_skip_broken_registrations; "
                f"skipped_registrations={count}; "
                f"repair_command={command}; "
                "不要对找不到的 Skill 做维护、升级、迁移或发布；用户要求修复时先运行 repair_command 预览，"
                "确认后再加 --apply。"
            ),
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
        return _registration_problem_notice(len(errors))

    resolution = (
        latest_resolver()
        if latest_resolver is not None
        else resolve_latest_version(home=home)
    )
    latest = resolution.get("version")
    if not isinstance(latest, str) or version_key(latest) is None:
        return _allow(None, "retry_evozeus_latest_release_lookup", "latest_harness_version=unknown")

    latest_key = version_key(latest)
    outdated_count = sum(
        1
        for target in targets
        if version_key(target["wrapper_version"]) is not None
        and version_key(target["wrapper_version"]) < latest_key
    )
    if outdated_count:
        return _allow(
            None,
            "continue_business_without_harness_writes",
            (
                "selected_skill_notice=current_to_latest_advisory; "
                f"outdated_harness_count={outdated_count}; "
                f"latest_harness_version={latest}; "
                "不影响使用，不要在开场或普通任务里提起。普通 Skill 调用不授权 Harness 升级、迁移、创建分支或 worktree；"
                "只有用户明确要求维护或升级这个 Skill 时，才说一句“这个 Skill 的维护工具有新版本，不影响使用”，"
                "并先给 dry-run 方案、单独确认写入。"
            ),
        )
    return _allow(None, "none")


# --- Registration repair -------------------------------------------------------------

REPAIR_SEARCH_ENV = "EVOZEUS_REPAIR_SEARCH_ROOTS"
DEFAULT_REPAIR_SEARCH_ROOTS = (
    "Documents",
    "projects",
    "code",
    "src",
    "dev",
    "Developer",
    ".codex/skills",
    ".agents/skills",
    ".claude/skills",
    ".coos",
)
REPAIR_MAX_DEPTH = 4
REPAIR_SKIP_DIRS = {"node_modules", ".git", "Library", "__pycache__", ".venv", "venv", "dist", "build"}


def _git_origin_repo(directory: Path) -> str | None:
    """Return owner/repo from a main checkout's origin remote (worktrees are ignored)."""
    config = directory / ".git" / "config"
    if not config.is_file():
        return None
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    section = re.search(r'(?ms)^\[remote "origin"\]\s*$(.*?)(?=^\[|\Z)', text)
    if not section:
        return None
    url = re.search(r"(?m)^\s*url\s*=\s*(\S+)", section.group(1))
    if not url:
        return None
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url.group(1))
    return match.group(1) if match else None


def _manifest_repo(directory: Path) -> str | None:
    for candidate in MANIFEST_CANDIDATES:
        manifest = read_json_object(directory / candidate)
        if manifest and isinstance(manifest.get("canonical_repo"), str):
            return manifest["canonical_repo"]
    return None


def _repair_search_roots(home: Path) -> list[Path]:
    configured = os.environ.get(REPAIR_SEARCH_ENV)
    raw = configured.split(os.pathsep) if configured else [str(home / root) for root in DEFAULT_REPAIR_SEARCH_ROOTS]
    roots: list[Path] = []
    for value in raw:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_dir() and path not in roots:
            roots.append(path)
    return roots


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def find_relocation_candidates(home: Path, repos: set[str]) -> dict[str, list[Path]]:
    """Find directories whose wrapper manifest or git origin names one of ``repos``."""
    wanted = {repo.casefold(): repo for repo in repos}
    found: dict[str, list[Path]] = {repo: [] for repo in repos}
    evozeus_home = (home / ".evozeus").resolve()
    seen: set[Path] = set()
    for root in _repair_search_roots(home):
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            try:
                resolved = directory.resolve()
            except OSError:
                continue
            if resolved in seen or _path_contains(evozeus_home, resolved):
                continue
            seen.add(resolved)
            for identity in (_manifest_repo(directory), _git_origin_repo(directory)):
                key = identity.casefold() if identity else None
                if key in wanted and resolved not in found[wanted[key]]:
                    found[wanted[key]].append(resolved)
                    break
            if depth >= REPAIR_MAX_DEPTH:
                continue
            try:
                children = sorted(directory.iterdir())
            except OSError:
                continue
            for child in children:
                if child.name in REPAIR_SKIP_DIRS or child.is_symlink():
                    continue
                if child.is_dir():
                    stack.append((child, depth + 1))
    return found


def repair_projects(home: Path, *, apply: bool, out=sys.stdout) -> int:
    problems: list[dict[str, Any]] = []
    discover_wrapped_targets(home, problems)
    if not problems:
        print("所有已登记的 Skill 都能找到，不需要修复。", file=out)
        return 0
    relocatable = [problem for problem in problems if problem["kind"] == "broken_project_pointer"]
    candidates = find_relocation_candidates(home, {problem["repo"] for problem in relocatable})
    print(f"发现 {len(problems)} 个登记有问题：", file=out)
    fixed = 0
    unresolved = 0
    for problem in problems:
        repo, pointer = problem["repo"], problem["pointer"]
        if problem["kind"] != "broken_project_pointer":
            unresolved += 1
            print(f"- {repo}：登记内容不一致（{problem['kind']}），需要人工检查 {pointer}", file=out)
            continue
        matches = candidates.get(repo, [])
        if len(matches) == 1:
            if apply:
                pointer.unlink()
                pointer.symlink_to(matches[0])
                fixed += 1
                print(f"- {repo}：已改指到 {matches[0]}", file=out)
            else:
                print(f"- {repo}：找到新位置 {matches[0]}（加 --apply 改指过去）", file=out)
            continue
        unresolved += 1
        if matches:
            print(f"- {repo}：找到多个可能的位置，请手动选一个：", file=out)
            for match in matches:
                print(f'    ln -sfn "{match}" "{pointer}"', file=out)
        else:
            print(
                f"- {repo}：没找到。如果已不再使用，可删掉这条登记：rm \"{pointer}\"；"
                f"如果仓库在别处，设置 {REPAIR_SEARCH_ENV} 后重试。",
                file=out,
            )
    if apply:
        print(f"已修复 {fixed} 个，剩 {unresolved} 个需要手动处理。", file=out)
    return 0 if unresolved == 0 and (apply or not relocatable) else 1


def main() -> int:
    if "--repair-projects" in sys.argv[1:]:
        return repair_projects(Path.home(), apply="--apply" in sys.argv[1:])
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        hook_input = {}
    payload = evaluate_session_start(home=Path.home(), hook_input=hook_input)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
