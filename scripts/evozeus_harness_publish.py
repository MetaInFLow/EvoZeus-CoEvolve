#!/usr/bin/env python3
"""Admin-gated publication for registered Harness upgrades."""

from __future__ import annotations

import json
import fcntl
import hashlib
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

UPGRADE_PLAN_SCHEMA = "evozeus.coevolve.harness-upgrade-plan.v1"
UPGRADE_PLAN_MARKER = "evozeus-harness-upgrade-plan:v1"
UPGRADE_SOURCE_REPOSITORY = "MetaInFLow/EvoZeus-CoEvolve"


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
        "repository(owner:$owner,name:$name){"
        "viewerPermission defaultBranchRef{name target{... on Commit{oid}}} url}"
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
            "default_branch_oid": None,
            "url": None,
            "error": (result.get("stderr") or result.get("stdout") or "GitHub permission check failed").strip(),
        }
    try:
        payload = json.loads(result.get("stdout") or "{}")
        data = payload.get("data") or {}
        repository = data.get("repository") or {}
        viewer = data.get("viewer") or {}
        permission = repository.get("viewerPermission")
        default_branch_ref = repository.get("defaultBranchRef") or {}
        default_branch = default_branch_ref.get("name")
        default_branch_oid = (default_branch_ref.get("target") or {}).get("oid")
        if not default_branch:
            raise ValueError("repository has no default branch")
        if not isinstance(default_branch_oid, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40,64}", default_branch_oid
        ):
            raise ValueError("repository default branch has no commit oid")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "repo": repo,
            "viewer": None,
            "permission": None,
            "is_admin": False,
            "default_branch": None,
            "default_branch_oid": None,
            "url": None,
            "error": f"invalid GitHub permission response: {exc}",
        }
    return {
        "repo": repo,
        "viewer": viewer.get("login"),
        "permission": permission,
        "is_admin": permission == "ADMIN",
        "default_branch": default_branch,
        "default_branch_oid": default_branch_oid,
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


def resolve_official_upgrade_source(
    wrapper_root: Path,
    latest_version: str,
    runner=run_command,
) -> dict[str, str]:
    try:
        root = wrapper_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("official Harness source checkout is unavailable") from exc
    if not root.is_dir():
        raise RuntimeError("official Harness source checkout is not a directory")
    git_root = Path(
        _checked(runner, ["git", "-C", str(root), "rev-parse", "--show-toplevel"])
    ).resolve()
    if git_root != root:
        raise RuntimeError("official Harness source must be an independent Git repository root")
    remote = _checked(runner, ["git", "-C", str(root), "remote", "get-url", "origin"])
    resolved_repo = _github_repo_from_remote(remote)
    if resolved_repo is None or resolved_repo.casefold() != UPGRADE_SOURCE_REPOSITORY.casefold():
        raise RuntimeError(
            "official Harness source origin mismatch: " + _redacted_remote(remote)
        )
    dirty = _checked(
        runner,
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
    )
    if dirty:
        raise RuntimeError("official Harness source checkout must be clean")
    head = _checked(runner, ["git", "-C", str(root), "rev-parse", "HEAD"])
    tag_commit = _checked(
        runner,
        ["git", "-C", str(root), "rev-parse", "--verify", f"refs/tags/{latest_version}^{{commit}}"],
    )
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", head) or head.casefold() != tag_commit.casefold():
        raise RuntimeError("official Harness release tag does not resolve to source HEAD")
    remote_tags = _checked(
        runner,
        [
            "git",
            "-C",
            str(root),
            "ls-remote",
            "origin",
            f"refs/tags/{latest_version}",
            f"refs/tags/{latest_version}^{{}}",
        ],
    )
    remote_refs: dict[str, str] = {}
    for line in remote_tags.splitlines():
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{40,64}", fields[0]):
            raise RuntimeError("official Harness remote release tag response is invalid")
        remote_refs[fields[1]] = fields[0]
    peeled_ref = f"refs/tags/{latest_version}^{{}}"
    direct_ref = f"refs/tags/{latest_version}"
    remote_tag_commit = remote_refs.get(peeled_ref) or remote_refs.get(direct_ref)
    if remote_tag_commit is None or remote_tag_commit.casefold() != head.casefold():
        raise RuntimeError("official Harness remote release tag does not resolve to source HEAD")

    manifest_path = root / "contracts/v1/manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("official Harness contract manifest is missing or unsafe")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise RuntimeError("official Harness contract manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("official Harness contract manifest is invalid")
    if manifest.get("source_repository") != UPGRADE_SOURCE_REPOSITORY:
        raise RuntimeError("official Harness contract manifest source repository mismatch")
    if manifest.get("source_revision") != latest_version:
        raise RuntimeError("official Harness contract manifest release revision mismatch")
    bundle_id = manifest.get("bundle_id")
    bundle_version = manifest.get("bundle_version")
    if not isinstance(bundle_id, str) or not bundle_id:
        raise RuntimeError("official Harness contract manifest bundle id is invalid")
    if not isinstance(bundle_version, str) or not bundle_version:
        raise RuntimeError("official Harness contract manifest bundle version is invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("official Harness contract manifest files are invalid")
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("official Harness contract manifest file entry is invalid")
        relative = item.get("path")
        expected_sha = item.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(expected_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
        ):
            raise RuntimeError("official Harness contract manifest file entry is invalid")
        source_path = root / "contracts/v1" / relative
        if source_path.is_symlink() or not source_path.is_file():
            raise RuntimeError(f"official Harness contract file is missing or unsafe: {relative}")
        actual_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(f"official Harness contract file digest mismatch: {relative}")
    return {
        "schema_version": "evozeus.coevolve.harness-upgrade-source.v1",
        "source_repository": UPGRADE_SOURCE_REPOSITORY,
        "source_tag": latest_version,
        "source_revision": head.lower(),
        "contract_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "contract_bundle_id": bundle_id,
        "contract_bundle_version": bundle_version,
    }


def _validate_upgrade_source_evidence(
    evidence: Any,
    latest_version: str,
) -> dict[str, str]:
    if not isinstance(evidence, dict):
        raise RuntimeError("official Harness source evidence is invalid")
    expected_literals = {
        "schema_version": "evozeus.coevolve.harness-upgrade-source.v1",
        "source_repository": UPGRADE_SOURCE_REPOSITORY,
        "source_tag": latest_version,
    }
    if any(evidence.get(key) != value for key, value in expected_literals.items()):
        raise RuntimeError("official Harness source evidence identity mismatch")
    for key, pattern in (
        ("source_revision", r"[0-9a-fA-F]{40,64}"),
        ("contract_manifest_sha256", r"[0-9a-fA-F]{64}"),
    ):
        value = evidence.get(key)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise RuntimeError(f"official Harness source evidence has invalid {key}")
    for key in ("contract_bundle_id", "contract_bundle_version"):
        if not isinstance(evidence.get(key), str) or not evidence[key]:
            raise RuntimeError(f"official Harness source evidence has invalid {key}")
    return {key: str(value) for key, value in evidence.items()}


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


def _default_existing_pr(repo: str, branch: str, runner=run_command) -> dict[str, Any] | None:
    owner, _ = _split_repo(repo)
    result = _call_runner(
        runner,
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{repo}/pulls",
            "-f",
            "state=open",
            "-f",
            f"head={owner}:{branch}",
            "-f",
            "per_page=2",
        ],
    )
    if result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "GitHub PR lookup failed").strip()
        raise RuntimeError(_redact_url_credentials(detail))
    try:
        payload = json.loads(result.get("stdout") or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid GitHub PR lookup response: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("invalid GitHub PR lookup response")
    if len(payload) > 1:
        raise RuntimeError("multiple open Harness upgrade PRs use the same head branch")
    if not payload:
        return None
    item = payload[0]
    if not isinstance(item, dict):
        raise RuntimeError("invalid GitHub PR lookup response")
    head = item.get("head") or {}
    base = item.get("base") or {}
    head_repo = head.get("repo") or {}
    base_repo = base.get("repo") or {}
    return {
        "url": item.get("html_url"),
        "number": item.get("number"),
        "body": item.get("body"),
        "head_ref": head.get("ref"),
        "head_commit": head.get("sha"),
        "head_repo": head_repo.get("full_name"),
        "base_ref": base.get("ref"),
        "base_commit": base.get("sha"),
        "base_repo": base_repo.get("full_name"),
    }


def _require_live_admin(repo: str, access_resolver) -> dict[str, Any]:
    try:
        access = access_resolver(repo)
    except Exception as exc:
        raise PermissionError(
            f"live GitHub ADMIN verification failed for {repo}: "
            f"{_redact_url_credentials(str(exc))}"
        ) from exc
    if not isinstance(access, dict):
        raise PermissionError(f"live GitHub ADMIN verification failed for {repo}")
    resolved_repo = access.get("repo")
    if isinstance(resolved_repo, str) and resolved_repo.casefold() != repo.casefold():
        raise PermissionError(f"live GitHub ADMIN evidence targets another repository: {repo}")
    if access.get("is_admin") is not True or access.get("permission") != "ADMIN":
        raise PermissionError(f"live GitHub ADMIN permission is required for {repo}")
    if not isinstance(access.get("viewer"), str) or not access["viewer"]:
        raise PermissionError(f"live GitHub ADMIN evidence has no verified actor for {repo}")
    if not isinstance(access.get("default_branch"), str) or not access["default_branch"]:
        raise PermissionError(f"live GitHub ADMIN evidence has no default branch for {repo}")
    default_branch_oid = access.get("default_branch_oid")
    if default_branch_oid is not None and (
        not isinstance(default_branch_oid, str)
        or not re.fullmatch(r"[0-9a-fA-F]{40,64}", default_branch_oid)
    ):
        raise PermissionError(f"live GitHub ADMIN evidence has an invalid default branch oid for {repo}")
    return access


def _require_same_live_context(
    repo: str,
    initial: dict[str, Any],
    current: dict[str, Any],
) -> None:
    if current.get("viewer") != initial.get("viewer"):
        raise PermissionError(f"live GitHub ADMIN actor changed while publishing {repo}")
    if current.get("default_branch") != initial.get("default_branch"):
        raise PermissionError(f"live GitHub default branch changed while publishing {repo}")


def _fetch_base_commit(
    canonical: Path,
    default_branch: str,
    runner,
    *,
    access: dict[str, Any] | None = None,
) -> str:
    _checked(runner, ["git", "-C", str(canonical), "fetch", "origin", default_branch])
    commit = _checked(
        runner,
        [
            "git",
            "-C",
            str(canonical),
            "rev-parse",
            f"refs/remotes/origin/{default_branch}",
        ],
    )
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        raise RuntimeError("target base lookup returned an invalid commit")
    github_oid = (access or {}).get("default_branch_oid")
    if github_oid is not None and github_oid.casefold() != commit.casefold():
        raise RuntimeError("live GitHub default branch commit does not match fetched origin")
    return commit


def _upgrade_plan_metadata(
    *,
    repo: str,
    actor: str,
    source: dict[str, str],
    current_version: str,
    latest_version: str,
    base_ref: str,
    base_commit: str,
    head_ref: str,
    head_commit: str,
) -> dict[str, str]:
    identity = {
        "schema_version": UPGRADE_PLAN_SCHEMA,
        "repo": repo,
        "verified_actor": actor,
        "source_repository": source["source_repository"],
        "source_tag": source["source_tag"],
        "source_revision": source["source_revision"],
        "contract_manifest_sha256": source["contract_manifest_sha256"],
        "contract_bundle_id": source["contract_bundle_id"],
        "contract_bundle_version": source["contract_bundle_version"],
        "from_version": current_version,
        "to_version": latest_version,
        "target_base_ref": base_ref,
        "target_base_commit": base_commit,
        "target_head_ref": head_ref,
        "target_head_commit": head_commit,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**identity, "plan_identity": digest}


def _upgrade_plan_marker(metadata: dict[str, str]) -> str:
    payload = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    return f"<!-- {UPGRADE_PLAN_MARKER} {payload} -->"


def _read_upgrade_plan_marker(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    prefix = f"<!-- {UPGRADE_PLAN_MARKER} "
    matches = [
        line[len(prefix) : -len(" -->")]
        for line in body.splitlines()
        if line.startswith(prefix) and line.endswith(" -->")
    ]
    if len(matches) != 1:
        return None
    try:
        payload = json.loads(matches[0])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _validated_existing_pr_url(
    repo: str,
    existing_pr: Any,
    expected: dict[str, str],
) -> str:
    if not isinstance(existing_pr, dict):
        raise RuntimeError(
            "same-name open Harness upgrade PR cannot be safely reused without live identity evidence"
        )
    actual = {
        "head_ref": existing_pr.get("head_ref"),
        "head_commit": existing_pr.get("head_commit"),
        "head_repo": existing_pr.get("head_repo"),
        "base_ref": existing_pr.get("base_ref"),
        "base_commit": existing_pr.get("base_commit"),
        "base_repo": existing_pr.get("base_repo"),
    }
    required = {
        "head_ref": expected["target_head_ref"],
        "head_commit": expected["target_head_commit"],
        "head_repo": repo,
        "base_ref": expected["target_base_ref"],
        "base_commit": expected["target_base_commit"],
        "base_repo": repo,
    }
    if any(
        not isinstance(actual[key], str)
        or actual[key].casefold() != value.casefold()
        for key, value in required.items()
    ):
        raise RuntimeError(
            "same-name open Harness upgrade PR cannot be safely reused: live head/base mismatch"
        )
    marker = _read_upgrade_plan_marker(existing_pr.get("body"))
    if marker != expected:
        raise RuntimeError(
            "same-name open Harness upgrade PR cannot be safely reused: plan identity mismatch"
        )
    url = existing_pr.get("url")
    if not isinstance(url, str) or not re.fullmatch(
        rf"https://github\.com/{re.escape(repo)}/pull/[1-9][0-9]*", url, re.IGNORECASE
    ):
        raise RuntimeError(
            "same-name open Harness upgrade PR cannot be safely reused: invalid PR URL"
        )
    return url


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
    access_resolver=None,
    source_resolver=None,
) -> dict[str, Any]:
    repo = target["repo"]
    canonical = Path(target["target"]).expanduser().resolve()
    current_version = target.get("wrapper_version") or "unknown"
    branch = _branch_name(current_version, latest_version)
    source_resolver = source_resolver or (
        lambda source_root, version: resolve_official_upgrade_source(
            source_root,
            version,
            runner=runner,
        )
    )
    source = _validate_upgrade_source_evidence(
        source_resolver(wrapper_root, latest_version),
        latest_version,
    )
    access_resolver = access_resolver or (
        lambda repo_name: resolve_github_admin_access(repo_name, runner=runner)
    )
    initial_access = _require_live_admin(repo, access_resolver)
    default_branch = initial_access["default_branch"]
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
    base_commit = _fetch_base_commit(
        canonical,
        default_branch,
        runner,
        access=initial_access,
    )
    remote_branch_oid = _remote_branch_oid(canonical, branch, runner)
    existing_pr = existing_pr_resolver(repo, branch)
    if existing_pr:
        if remote_branch_oid is None:
            raise RuntimeError(
                "same-name open Harness upgrade PR cannot be safely reused: remote head is missing"
            )
        expected = _upgrade_plan_metadata(
            repo=repo,
            actor=initial_access["viewer"],
            source=source,
            current_version=current_version,
            latest_version=latest_version,
            base_ref=default_branch,
            base_commit=base_commit,
            head_ref=branch,
            head_commit=remote_branch_oid,
        )
        pr_url = _validated_existing_pr_url(repo, existing_pr, expected)
        return {
            "repo": repo,
            "status": "existing_pr",
            "writes": False,
            "branch": branch,
            "commit": remote_branch_oid,
            "pr_url": pr_url,
            "worktree": None,
            "source_revision": source["source_revision"],
            "source_tag": source["source_tag"],
            "contract_manifest_sha256": source["contract_manifest_sha256"],
            "plan_identity": expected["plan_identity"],
            "target_base_ref": default_branch,
            "target_base_commit": base_commit,
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
                "writes": False,
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
        current_source = _validate_upgrade_source_evidence(
            source_resolver(wrapper_root, latest_version),
            latest_version,
        )
        if current_source != source:
            raise RuntimeError("official Harness source changed after the upgrade plan was created")
        push_access = _require_live_admin(repo, access_resolver)
        _require_same_live_context(repo, initial_access, push_access)
        current_base_commit = _fetch_base_commit(
            canonical,
            default_branch,
            runner,
            access=push_access,
        )
        if current_base_commit.casefold() != base_commit.casefold():
            raise RuntimeError("target default branch changed after the upgrade plan was created")
        remote_branch_oid = _remote_branch_oid(canonical, branch, runner)
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
        metadata = _upgrade_plan_metadata(
            repo=repo,
            actor=initial_access["viewer"],
            source=source,
            current_version=current_version,
            latest_version=latest_version,
            base_ref=default_branch,
            base_commit=base_commit,
            head_ref=branch,
            head_commit=commit,
        )
        try:
            pr_access = _require_live_admin(repo, access_resolver)
            _require_same_live_context(repo, initial_access, pr_access)
            current_base_commit = _fetch_base_commit(
                canonical,
                default_branch,
                runner,
                access=pr_access,
            )
            if current_base_commit.casefold() != base_commit.casefold():
                raise RuntimeError("target default branch changed before PR creation")
            pushed_oid = _remote_branch_oid(canonical, branch, runner)
            if pushed_oid is None or pushed_oid.casefold() != commit.casefold():
                raise RuntimeError("remote upgrade branch no longer matches the planned target head")
            changed_file_lines = "\n".join(f"- `{path}`" for path in sorted(changed))
            pr_url = pr_creator(
                repo=repo,
                branch=branch,
                base=default_branch,
                title=f"chore(evozeus): upgrade Harness to {latest_version}",
                body=(
                    f"## Official Harness upgrade\n\n"
                    f"- Profile: `official_harness_upgrade`\n"
                    f"- Source: `{source['source_repository']}@{source['source_tag']}`\n"
                    f"- Source revision: `{source['source_revision']}`\n"
                    f"- Contract manifest SHA-256: `{source['contract_manifest_sha256']}`\n"
                    f"- From: `{current_version}`\n"
                    f"- To: `{latest_version}`\n"
                    f"- Branch: `{branch}`\n"
                    f"- Target base: `{default_branch}@{base_commit}`\n"
                    f"- Target head: `{branch}@{commit}`\n"
                    f"- Plan identity: `{metadata['plan_identity']}`\n"
                    f"- Run: `{run_id}`\n\n"
                    f"{_upgrade_plan_marker(metadata)}\n\n"
                    "## Changed files\n\n"
                    f"{changed_file_lines}\n\n"
                    "This PR refreshes official EvoZeus-managed Harness outputs while preserving "
                    "the target Skill changelog and business content."
                ),
            )
            if not isinstance(pr_url, str) or not re.fullmatch(
                rf"https://github\.com/{re.escape(repo)}/pull/[1-9][0-9]*",
                pr_url.strip(),
                re.IGNORECASE,
            ):
                raise RuntimeError("PR creation returned an invalid PR URL")
            pr_url = pr_url.strip()
        except Exception as exc:
            cleanup_safe = True
            return {
                "repo": repo,
                "status": "pr_creation_failed",
                "writes": True,
                "branch": branch,
                "commit": commit,
                "pr_url": None,
                "worktree": None,
                "changed_files": sorted(changed),
                "error": _redact_url_credentials(str(exc)),
                "remote_side_effect": {
                    "kind": "branch_push",
                    "repo": repo,
                    "branch": branch,
                    "commit": commit,
                    "target_base_ref": default_branch,
                    "target_base_commit": base_commit,
                    "source_revision": source["source_revision"],
                    "source_tag": source["source_tag"],
                    "contract_manifest_sha256": source["contract_manifest_sha256"],
                    "plan_identity": metadata["plan_identity"],
                    "recovery": "retry_same_upgrade",
                },
            }
        cleanup_safe = True
        return {
            "repo": repo,
            "status": "published",
            "writes": True,
            "branch": branch,
            "commit": commit,
            "pr_url": pr_url,
            "worktree": str(worktree),
            "changed_files": sorted(changed),
            "source_revision": source["source_revision"],
            "source_tag": source["source_tag"],
            "contract_manifest_sha256": source["contract_manifest_sha256"],
            "plan_identity": metadata["plan_identity"],
            "target_base_ref": default_branch,
            "target_base_commit": base_commit,
        }
    finally:
        if worktree_added and cleanup_safe:
            _call_runner(
                runner,
                ["git", "-C", str(canonical), "worktree", "remove", "--force", str(worktree)],
            )


def _safe_remote_side_effect(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "kind",
        "repo",
        "branch",
        "commit",
        "target_base_ref",
        "target_base_commit",
        "source_revision",
        "source_tag",
        "contract_manifest_sha256",
        "plan_identity",
        "recovery",
    )
    return {key: value.get(key) for key in allowed if value.get(key) is not None}


def _safe_ledger_report(report: dict[str, Any]) -> dict[str, Any]:
    top_level = (
        "stage",
        "status",
        "writes",
        "run_id",
        "latest_version",
        "target_count",
        "publishable_count",
        "published_count",
        "skipped_permission_count",
        "skipped_preflight_count",
        "failed_count",
        "completed_at",
    )
    result_fields = (
        "repo",
        "status",
        "writes",
        "branch",
        "commit",
        "pr_url",
        "from_version",
        "source_revision",
        "source_tag",
        "contract_manifest_sha256",
        "plan_identity",
        "target_base_ref",
        "target_base_commit",
        "error",
    )
    safe: dict[str, Any] = {
        key: report.get(key) for key in top_level if report.get(key) is not None
    }
    safe_results: list[dict[str, Any]] = []
    for result in report.get("results", []):
        if not isinstance(result, dict):
            continue
        item = {key: result.get(key) for key in result_fields if result.get(key) is not None}
        for key in ("pr_url", "error"):
            if isinstance(item.get(key), str):
                item[key] = _redact_url_credentials(item[key])
        effect = _safe_remote_side_effect(result.get("remote_side_effect"))
        if effect is not None:
            item["remote_side_effect"] = effect
        safe_results.append(item)
    safe["results"] = safe_results
    safe["skipped_targets"] = [
        {
            key: _redact_url_credentials(value) if key == "error" and isinstance(value, str) else value
            for key in ("repo", "status", "reason", "viewer", "permission", "error")
            if (value := item.get(key)) is not None
        }
        for item in report.get("skipped_targets", [])
        if isinstance(item, dict)
    ]
    safe["failures"] = []
    for failure in report.get("failures", []):
        if not isinstance(failure, dict):
            continue
        item = {
            key: failure.get(key)
            for key in ("repo", "status", "error")
            if failure.get(key) is not None
        }
        if isinstance(item.get("error"), str):
            item["error"] = _redact_url_credentials(item["error"])
        effect = _safe_remote_side_effect(failure.get("remote_side_effect"))
        if effect is not None:
            item["remote_side_effect"] = effect
        safe["failures"].append(item)
    return safe


def _write_ledgers(home: Path, report: dict[str, Any]) -> dict[str, str]:
    root = home.expanduser().resolve() / ".evozeus/skills"
    run_path = root / "runs" / f"{report['run_id']}.json"
    events_path = root / "events.jsonl"
    _atomic_json(run_path, _safe_ledger_report(report))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    existing_event_ids: set[str] = set()
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("event_id"), str):
                existing_event_ids.add(event["event_id"])
    with events_path.open("a", encoding="utf-8") as handle:
        for result in report.get("results", []):
            status = result.get("status")
            if status in {"published", "existing_pr"}:
                event_name = "harness_upgrade_published"
                event_payload = {
                    "from": result.get("from_version"),
                    "to": report.get("latest_version"),
                    "commit": result.get("commit"),
                    "pr_url": result.get("pr_url"),
                    "result": status,
                }
            elif status == "pr_creation_failed" and isinstance(
                result.get("remote_side_effect"), dict
            ):
                effect = result["remote_side_effect"]
                event_name = "harness_upgrade_pr_creation_failed"
                event_payload = {
                    "from": result.get("from_version"),
                    "to": report.get("latest_version"),
                    "commit": effect.get("commit"),
                    "branch": effect.get("branch"),
                    "target_base_ref": effect.get("target_base_ref"),
                    "target_base_commit": effect.get("target_base_commit"),
                    "source_revision": effect.get("source_revision"),
                    "plan_identity": effect.get("plan_identity"),
                    "recovery": effect.get("recovery"),
                    "result": status,
                }
            else:
                continue
            event_id = f"{report['run_id']}:{result['repo']}:{event_name}"
            if event_id in existing_event_ids:
                continue
            handle.write(
                json.dumps(
                    {
                        "event_id": event_id,
                        "event": event_name,
                        "skill_id": result["repo"],
                        **event_payload,
                        "occurred_at": report.get("completed_at"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            existing_event_ids.add(event_id)
    os.chmod(events_path, 0o600)
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
    source_resolver=None,
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
                access_resolver=access_resolver,
                source_resolver=source_resolver,
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
    access_resolver,
    source_resolver,
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
                access_resolver=access_resolver,
                source_resolver=source_resolver,
            )
            if isinstance(result.get("error"), str):
                result["error"] = _redact_url_credentials(result["error"])
            result.setdefault("from_version", target.get("wrapper_version"))
            results.append(result)
            if result.get("status") == "pr_creation_failed":
                failures.append(
                    {
                        "repo": target["repo"],
                        "status": "pr_creation_failed",
                        "error": _redact_url_credentials(str(result.get("error") or "PR creation failed")),
                        "remote_side_effect": result.get("remote_side_effect"),
                    }
                )
        except Exception as exc:
            failures.append(
                {
                    "repo": target["repo"],
                    "status": "failed",
                    "error": _redact_url_credentials(str(exc)),
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
