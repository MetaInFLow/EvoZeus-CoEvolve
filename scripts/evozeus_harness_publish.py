#!/usr/bin/env python3
"""Admin-gated publication for registered Harness upgrades."""

from __future__ import annotations

import json
import fcntl
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

try:
    from .evozeus_wrapper_global_hook import plan_upgrade_all
    from .evozeus_wrapper_lifecycle import migrate_target_layout
except ImportError:
    from evozeus_wrapper_global_hook import plan_upgrade_all
    from evozeus_wrapper_lifecycle import migrate_target_layout


CommandRunner = Callable[[list[str], Path | None], dict[str, Any]]


def _redact_url_credentials(value: str) -> str:
    return re.sub(
        r"(?i)((?:https?|ssh)://)[^\s/]*@",
        r"\1",
        value,
    )


def run_command(args: list[str], cwd: Path | None = None) -> dict[str, Any]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _call_runner(runner, args: list[str], cwd: Path | None = None) -> dict[str, Any]:
    try:
        return runner(args, cwd=cwd)
    except TypeError:
        return runner(args, cwd)


def _checked(runner, args: list[str], cwd: Path | None = None) -> str:
    result = _call_runner(runner, args, cwd)
    if result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "command failed").strip()
        raise RuntimeError(f"{' '.join(args)}: {_redact_url_credentials(detail)}")
    return str(result.get("stdout") or "").strip()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "upgrade_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _split_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/", 1)
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError(f"invalid GitHub repository name: {repo}")
    return parts[0], parts[1]


def resolve_github_admin_access(repo: str, runner=run_command) -> dict[str, Any]:
    owner, name = _split_repo(repo)
    query = (
        "query($owner:String!,$name:String!){"
        "viewer{login}"
        "repository(owner:$owner,name:$name){viewerPermission defaultBranchRef{name} url}"
        "}"
    )
    result = _call_runner(
        runner,
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
        ],
    )
    if result.get("returncode") != 0:
        return {
            "repo": repo,
            "viewer": None,
            "permission": None,
            "is_admin": False,
            "default_branch": None,
            "url": None,
            "error": (result.get("stderr") or result.get("stdout") or "GitHub permission check failed").strip(),
        }
    try:
        payload = json.loads(result.get("stdout") or "{}")
        data = payload.get("data") or {}
        repository = data.get("repository") or {}
        viewer = data.get("viewer") or {}
        permission = repository.get("viewerPermission")
        default_branch = (repository.get("defaultBranchRef") or {}).get("name")
        if not default_branch:
            raise ValueError("repository has no default branch")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "repo": repo,
            "viewer": None,
            "permission": None,
            "is_admin": False,
            "default_branch": None,
            "url": None,
            "error": f"invalid GitHub permission response: {exc}",
        }
    return {
        "repo": repo,
        "viewer": viewer.get("login"),
        "permission": permission,
        "is_admin": permission == "ADMIN",
        "default_branch": default_branch,
        "url": repository.get("url"),
        "error": None,
    }


def _github_repo_path(path: str) -> str | None:
    value = path.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) else None


def _github_repo_from_remote(remote: str) -> str | None:
    value = remote.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname and parsed.hostname.casefold() == "github.com":
            return _github_repo_path(parsed.path)
        return None
    match = re.fullmatch(
        r"git@github\.com:(.+)",
        value,
        flags=re.IGNORECASE,
    )
    return _github_repo_path(match.group(1)) if match else None


def _redacted_remote(remote: str) -> str:
    resolved = _github_repo_from_remote(remote)
    if resolved:
        return f"github.com/{resolved}"
    value = remote.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname:
            return parsed.hostname
    match = re.match(r"^(?:[^@\s]+@)?([^:/\s]+)[:/]", value)
    return match.group(1) if match else "unrecognized origin"


def verify_canonical_github_origin(repo: str, canonical: Path, runner=run_command) -> str:
    remote = _checked(
        runner,
        ["git", "-C", str(canonical), "remote", "get-url", "origin"],
    )
    resolved = _github_repo_from_remote(remote)
    if resolved is None or resolved.casefold() != repo.casefold():
        raise RuntimeError(
            f"canonical origin does not match {repo}: {_redacted_remote(remote)}"
        )
    return repo


def plan_admin_upgrade_all(
    home: Path,
    wrapper_root: Path,
    latest_version: str,
    *,
    latest_resolver=None,
    upgrade_planner=plan_upgrade_all,
    access_resolver=resolve_github_admin_access,
) -> dict[str, Any]:
    plan = upgrade_planner(
        home,
        wrapper_root,
        latest_version,
        latest_resolver=latest_resolver,
        allow_partial=True,
    )
    if plan.get("status") != "planned":
        return {
            **plan,
            "stage": "harness_upgrade_all_publish",
            "publishable_count": 0,
            "skipped_permission_count": 0,
            "skipped_preflight_count": 0,
            "publishable_targets": [],
            "skipped_targets": [],
        }

    publishable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for target in plan.get("targets", []):
        if target.get("errors"):
            skipped.append(
                {
                    "repo": target["repo"],
                    "status": "skipped_preflight",
                    "reason": "target_preflight_failed",
                    "errors": target["errors"],
                }
            )
            continue
        try:
            access = access_resolver(target["repo"])
        except Exception as exc:
            access = {
                "repo": target["repo"],
                "viewer": None,
                "permission": None,
                "is_admin": False,
                "default_branch": None,
                "url": None,
                "error": str(exc),
            }
        item = {**target, "github": access}
        if access.get("is_admin") is True:
            publishable.append(item)
        else:
            skipped.append(
                {
                    "repo": target["repo"],
                    "status": "skipped_permission",
                    "reason": "github_admin_required",
                    "viewer": access.get("viewer"),
                    "permission": access.get("permission"),
                    "error": access.get("error"),
                }
            )
    return {
        **plan,
        "stage": "harness_upgrade_all_publish",
        "status": "planned" if publishable else "permission_denied",
        "writes": False,
        "publishable_count": len(publishable),
        "skipped_permission_count": sum(
            item.get("reason") == "github_admin_required" for item in skipped
        ),
        "skipped_preflight_count": sum(
            item.get("reason") == "target_preflight_failed" for item in skipped
        ),
        "publishable_targets": publishable,
        "skipped_targets": skipped,
    }


def _branch_name(current: str, latest: str) -> str:
    safe_current = re.sub(r"[^0-9A-Za-z.-]+", "-", current).strip("-")
    safe_latest = re.sub(r"[^0-9A-Za-z.-]+", "-", latest).strip("-")
    return f"evozeus/harness-{safe_current}-to-{safe_latest}"


def _default_existing_pr(repo: str, branch: str, runner=run_command) -> str | None:
    result = _call_runner(
        runner,
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "--limit",
            "1",
        ],
    )
    if result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "GitHub PR lookup failed").strip()
        raise RuntimeError(detail)
    payload = json.loads(result.get("stdout") or "[]")
    return payload[0].get("url") if payload else None


def _default_pr_creator(*, repo: str, branch: str, base: str, title: str, body: str, runner=run_command) -> str:
    return _checked(
        runner,
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--head",
            branch,
            "--base",
            base,
            "--title",
            title,
            "--body",
            body,
        ],
    )


def _relative_changed_files(worktree: Path, changed_files: list[str]) -> set[str]:
    relative: set[str] = set()
    for item in changed_files:
        path = Path(item)
        if path.is_absolute():
            try:
                path = path.relative_to(worktree)
            except ValueError:
                continue
        relative.add(path.as_posix())
    return relative


def _remote_branch_oid(canonical: Path, branch: str, runner) -> str | None:
    result = _call_runner(
        runner,
        ["git", "-C", str(canonical), "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
    )
    if result.get("returncode") != 0:
        raise RuntimeError("remote upgrade branch lookup failed")
    output = str(result.get("stdout") or "").strip()
    if not output:
        return None
    fields = output.split()
    if (
        len(fields) != 2
        or fields[1] != f"refs/heads/{branch}"
        or not re.fullmatch(r"[0-9a-fA-F]{40,64}", fields[0])
    ):
        raise RuntimeError("remote upgrade branch lookup returned an invalid ref")
    return fields[0]


def publish_target_upgrade(
    target: dict[str, Any],
    *,
    home: Path,
    wrapper_root: Path,
    latest_version: str,
    run_id: str,
    runner=run_command,
    migrator=migrate_target_layout,
    existing_pr_resolver=None,
    pr_creator=None,
    origin_verifier=None,
) -> dict[str, Any]:
    access = target.get("github") or {}
    if access.get("is_admin") is not True or access.get("permission") != "ADMIN":
        raise PermissionError(f"GitHub ADMIN permission is required for {target.get('repo')}")

    repo = target["repo"]
    canonical = Path(target["target"]).expanduser().resolve()
    default_branch = access["default_branch"]
    current_version = target.get("wrapper_version") or "unknown"
    branch = _branch_name(current_version, latest_version)
    origin_verifier = origin_verifier or (
        lambda repo_name, checkout: verify_canonical_github_origin(
            repo_name,
            checkout,
            runner=runner,
        )
    )
    origin_verifier(repo, canonical)
    existing_pr_resolver = existing_pr_resolver or (
        lambda repo_name, head: _default_existing_pr(repo_name, head, runner=runner)
    )
    pr_creator = pr_creator or (
        lambda **kwargs: _default_pr_creator(**kwargs, runner=runner)
    )
    existing_pr = existing_pr_resolver(repo, branch)
    if existing_pr:
        return {
            "repo": repo,
            "status": "existing_pr",
            "branch": branch,
            "commit": None,
            "pr_url": existing_pr,
            "worktree": None,
        }

    worktree = (
        home.expanduser().resolve()
        / ".evozeus/worktrees/harness-upgrade"
        / run_id
        / repo.replace("/", "--")
    )
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if worktree.exists():
        raise RuntimeError(f"upgrade worktree already exists: {worktree}")

    worktree_added = False
    cleanup_safe = False
    try:
        _checked(runner, ["git", "-C", str(canonical), "fetch", "origin", default_branch])
        remote_branch_oid = _remote_branch_oid(canonical, branch, runner)
        _checked(
            runner,
            [
                "git",
                "-C",
                str(canonical),
                "worktree",
                "add",
                "-B",
                branch,
                str(worktree),
                f"origin/{default_branch}",
            ],
        )
        worktree_added = True
        migration = migrator(
            worktree,
            latest_version,
            wrapper_root=wrapper_root,
            require_clean_git=True,
        )
        _checked(runner, ["git", "-C", str(worktree), "add", "--all"])
        _checked(runner, ["git", "-C", str(worktree), "diff", "--cached", "--check"])
        changed_output = _checked(
            runner,
            ["git", "-C", str(worktree), "diff", "--cached", "--name-only"],
        )
        changed = {line for line in changed_output.splitlines() if line}
        if not changed:
            cleanup_safe = True
            return {
                "repo": repo,
                "status": "up_to_date",
                "branch": branch,
                "commit": None,
                "pr_url": None,
                "worktree": str(worktree),
            }
        declared = _relative_changed_files(worktree, migration.get("changed_files", []))
        undeclared = sorted(changed - declared)
        if undeclared:
            raise RuntimeError("migration changed undeclared files: " + ", ".join(undeclared))

        _checked(
            runner,
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "user.name=EvoZeus",
                "-c",
                "user.email=evozeus@users.noreply.github.com",
                "commit",
                "-m",
                f"chore(evozeus): upgrade harness to {latest_version}",
            ],
        )
        commit = _checked(runner, ["git", "-C", str(worktree), "rev-parse", "HEAD"])
        _checked(
            runner,
            [
                "git",
                "-C",
                str(worktree),
                "push",
                f"--force-with-lease=refs/heads/{branch}:{remote_branch_oid or ''}",
                "-u",
                "origin",
                branch,
            ],
        )
        changed_file_lines = "\n".join(f"- `{path}`" for path in sorted(changed))
        pr_url = pr_creator(
            repo=repo,
            branch=branch,
            base=default_branch,
            title=f"chore(evozeus): upgrade Harness to {latest_version}",
            body=(
                f"## Official Harness upgrade\n\n"
                f"- Profile: `official_harness_upgrade`\n"
                f"- Source: `MetaInFLow/EvoZeus-CoEvolve@{latest_version}`\n"
                f"- From: `{current_version}`\n"
                f"- To: `{latest_version}`\n"
                f"- Branch: `{branch}`\n"
                f"- Run: `{run_id}`\n\n"
                "## Changed files\n\n"
                f"{changed_file_lines}\n\n"
                "This PR refreshes official EvoZeus-managed Harness outputs while preserving "
                "the target Skill changelog and business content."
            ),
        )
        cleanup_safe = True
        return {
            "repo": repo,
            "status": "published",
            "branch": branch,
            "commit": commit,
            "pr_url": pr_url,
            "worktree": str(worktree),
            "changed_files": sorted(changed),
        }
    finally:
        if worktree_added and cleanup_safe:
            _call_runner(
                runner,
                ["git", "-C", str(canonical), "worktree", "remove", "--force", str(worktree)],
            )


def _write_ledgers(home: Path, report: dict[str, Any]) -> dict[str, str]:
    root = home.expanduser().resolve() / ".evozeus/skills"
    run_path = root / "runs" / f"{report['run_id']}.json"
    events_path = root / "events.jsonl"
    _atomic_json(run_path, report)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        for result in report.get("results", []):
            if result.get("status") not in {"published", "existing_pr"}:
                continue
            handle.write(
                json.dumps(
                    {
                        "event_id": f"{report['run_id']}:{result['repo']}",
                        "event": "harness_upgrade_published",
                        "skill_id": result["repo"],
                        "from": result.get("from_version"),
                        "to": report.get("latest_version"),
                        "commit": result.get("commit"),
                        "pr_url": result.get("pr_url"),
                        "result": result.get("status"),
                        "occurred_at": report.get("completed_at"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return {"run": str(run_path), "events": str(events_path)}


def publish_admin_upgrade_all(
    home: Path,
    wrapper_root: Path,
    latest_version: str,
    *,
    approve: bool = False,
    latest_resolver=None,
    upgrade_planner=plan_upgrade_all,
    access_resolver=resolve_github_admin_access,
    target_publisher=publish_target_upgrade,
    run_id: str | None = None,
) -> dict[str, Any]:
    plan = plan_admin_upgrade_all(
        home,
        wrapper_root,
        latest_version,
        latest_resolver=latest_resolver,
        upgrade_planner=upgrade_planner,
        access_resolver=access_resolver,
    )
    if plan.get("status") not in {"planned", "permission_denied"}:
        return plan
    if not approve:
        return {**plan, "status": "approval_required", "writes": False}
    if not plan.get("publishable_targets"):
        return {**plan, "status": "permission_denied", "writes": False}

    lock_path = home.expanduser().resolve() / ".evozeus/locks/harness-upgrade-all.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                **plan,
                "status": "busy",
                "writes": False,
                "error": "another Harness upgrade-all publication is already running",
            }
        try:
            return _publish_admin_upgrade_plan(
                plan,
                home=home,
                wrapper_root=wrapper_root,
                latest_version=latest_version,
                target_publisher=target_publisher,
                run_id=run_id,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _publish_admin_upgrade_plan(
    plan: dict[str, Any],
    *,
    home: Path,
    wrapper_root: Path,
    latest_version: str,
    target_publisher,
    run_id: str | None,
) -> dict[str, Any]:

    run_id = run_id or _run_id()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for target in plan["publishable_targets"]:
        try:
            result = target_publisher(
                target,
                home=home,
                wrapper_root=wrapper_root,
                latest_version=latest_version,
                run_id=run_id,
            )
            result.setdefault("from_version", target.get("wrapper_version"))
            results.append(result)
        except Exception as exc:
            failures.append(
                {
                    "repo": target["repo"],
                    "status": "failed",
                    "error": str(exc),
                }
            )

    succeeded = sum(result.get("status") in {"published", "existing_pr"} for result in results)
    skipped_targets = plan.get("skipped_targets", [])
    skipped = len(skipped_targets)
    skipped_permission = sum(
        item.get("reason") == "github_admin_required" for item in skipped_targets
    )
    skipped_preflight = sum(
        item.get("reason") == "target_preflight_failed" for item in skipped_targets
    )
    status = "published" if succeeded == len(plan["publishable_targets"]) and not skipped else "partial"
    if succeeded == 0 and failures:
        status = "failed"
    completed_at = _utc_now()
    report = {
        "stage": "harness_upgrade_all_publish",
        "status": status,
        "writes": True,
        "run_id": run_id,
        "latest_version": latest_version,
        "target_count": len(plan.get("targets", [])),
        "publishable_count": plan.get("publishable_count", 0),
        "published_count": succeeded,
        "skipped_permission_count": skipped_permission,
        "skipped_preflight_count": skipped_preflight,
        "failed_count": len(failures),
        "results": results,
        "skipped_targets": plan.get("skipped_targets", []),
        "failures": failures,
        "completed_at": completed_at,
    }
    report["ledger"] = _write_ledgers(home, report)
    return report
