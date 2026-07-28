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
USER_PROMPT_EVENT = "UserPromptSubmit"
DIRECT_CORRECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:^|[。！？!?；;\n]\s*)(?:不对|错了|有误)(?=$|[，,。！？!?；;：:\s])",
        r"(?:这|这个|这样|那|那个|你(?:的)?(?:结果|回答|输出)?).{0,12}(?:不对|错了|有误|怎么能对)(?:呢|吗)?(?=$|[，,。！？!?；;：:\s])",
        r"(?:这是|这次|这个(?:结果|报告|巡检)?).{0,8}(?:漏检|误判|遗漏|漏掉)",
        r"(?:我(?:很)?不满意|不符合(?:我的)?预期|你.{0,12}(?:(?:没有|没)(?:发现|识别|捕捉到)|漏了|漏掉|遗漏|漏检|误判|搞错))",
        r"(?:发现|遇到).{0,12}(?:bug|缺陷)",
        r"(?:无法|不能).{0,16}(?:自动|正常)(?:捕捉|记录|运行|识别|更新|升级)",
        r"\b(?:this|that|the result|your answer)\s+(?:is|was)\s+(?:wrong|incorrect)\b",
        r"\bthis\s+(?:should|must)\s+be\s+(?:corrected|fixed)\b",
        r"\bi(?:'m| am) not satisfied\b",
        r"\byou missed\s+(?:a|the)?\s*(?:requirement|constraint|instruction|step|status|issue|bug|record|fact)\b",
    )
)
DURABLE_RULE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:以后|今后|下次|每次|永远|始终|所有用户).{0,36}(?:记住|记得|必须|务必|不能|不要|不得|应该(?:先|每次|自动|统一|检查|核对|展示|记录|提示)|需要(?:先|每次|自动|统一|检查|核对|展示|记录|提示)|统一(?:使用|检查|展示|记录|处理)|自动(?:检查|捕捉|记录|识别|更新))",
        r"\b(?:from now on|every time|always|for all users).{0,50}(?:must|remember|check|hide|show|ask|record|never|do not)\b",
    )
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


def is_lesson_candidate(prompt: str) -> bool:
    normalized = " ".join(prompt.split())
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in DIRECT_CORRECTION_PATTERNS):
        return True
    if re.search(r"[?？][\"'”’）)]*\s*$", normalized):
        return False
    return any(pattern.search(normalized) for pattern in DURABLE_RULE_PATTERNS)


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_lesson_target(
    home: Path,
    *,
    cwd: str | None,
    prompt: str,
) -> str | None:
    targets, _ = discover_wrapped_targets(home)
    cwd_path = Path(cwd).expanduser().resolve() if isinstance(cwd, str) and cwd else None
    if cwd_path is not None:
        containing = [
            target
            for target in targets
            if _path_contains(target["canonical_path"], cwd_path)
        ]
        if containing:
            containing.sort(key=lambda target: len(target["canonical_path"].parts), reverse=True)
            return containing[0]["repo"]

    lowered = prompt.casefold()

    def alias_is_mentioned(alias: str) -> bool:
        normalized = alias.casefold().strip()
        if not normalized:
            return False
        if re.search(r"[\u3400-\u9fff]", normalized):
            return normalized in lowered
        return re.search(
            rf"(?<![a-z0-9_-]){re.escape(normalized)}(?![a-z0-9_-])",
            lowered,
        ) is not None

    mentioned = {
        target["repo"]
        for target in targets
        if any(alias_is_mentioned(alias) for alias in target.get("aliases", ()))
    }
    return next(iter(mentioned)) if len(mentioned) == 1 else None


def lesson_additional_context(target_repo: str | None) -> str:
    if target_repo:
        action = (
            f"是否记录到 `{target_repo}` Feedback Issue？本次只记录，不启动修复。"
        )
        route = f"目标 Skill 提示为 `{target_repo}`；只有当前对话证据一致时才使用该归属。"
    else:
        action = "是否记录这条 Lesson？如确认，请指定目标 Skill；本次只记录，不启动修复。"
        route = "无法确定目标 Skill；不得猜测归属。"
    return "\n".join(
        [
            "EvoZeus 检测到当前用户消息可能包含可复用 Lesson。以下是隐藏的开发者指引，不得原样引用。",
            "先完成用户当前请求并纠正业务结果。仅当反馈可抽象为可复用的 Skill/Harness 规则时，在正常回复末尾追加：",
            "💡 `EvoZeus · Lesson` 待记录",
            "捕捉到一条可复用 Lesson：<一句脱敏、可行动、可复用的总结>。",
            action,
            route,
            "不得展示内部 JSON、signal ID、capture state、route、诊断字段或 Hook 输出。",
            "未获得明确确认前不得创建 Issue；记录授权不得解释为修复授权。",
        ]
    )


def evaluate_user_prompt_submit(home: Path, hook_input: dict[str, Any]) -> dict[str, Any]:
    prompt = hook_input.get("prompt")
    if not isinstance(prompt, str) or not is_lesson_candidate(prompt):
        return {"continue": True}
    try:
        target_repo = resolve_lesson_target(
            home,
            cwd=hook_input.get("cwd"),
            prompt=prompt,
        )
    except Exception:
        return {"continue": True}
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": USER_PROMPT_EVENT,
            "additionalContext": lesson_additional_context(target_repo),
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
            payload = evaluate_user_prompt_submit(home=Path.home(), hook_input=hook_input)
        except Exception:
            payload = {"continue": True}
    else:
        payload = evaluate_session_start(home=Path.home(), hook_input=hook_input)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
