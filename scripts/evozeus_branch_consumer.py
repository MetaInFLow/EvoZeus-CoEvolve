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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE = "coevolve_target_skillware_consumer"
PROVENANCE_SCHEMA = "evozeus.coevolve.contributor-branch-snapshot-provenance.v1"
CONTRACT_ID = "evozeus.contributor_branch"
CONTRACT_VERSION = "1.2.0"
CORE_REVISION = "363a579693dd236bef5ecef9eb45309de15625f7"
CONTRACT_SHA256 = "a518ca6d6192ead0bac76eb2ec27f0581cbee82813033824bd5e40f805a65d6f"
PLANNER_SHA256 = "78edb3c6dd1e6d96bdd1011d3a631cab0f73f8fc73ebbe76bf0244dba186660b"
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsumerError("invalid_json", f"invalid managed JSON: {path.name}") from exc
    if not isinstance(data, dict):
        raise ConsumerError("invalid_json", f"managed JSON must be an object: {path.name}")
    return data


def managed_asset_root() -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    if (repository_root / CONTRACT_RELATIVE_PATH).is_file():
        return repository_root
    template_root = repository_root / "templates" / "target"
    if (template_root / CONTRACT_RELATIVE_PATH).is_file():
        return template_root
    raise ConsumerError("snapshot_missing", "managed contributor branch snapshot is missing")


def managed_regular_file(root: Path, relative_path: Path) -> Path:
    cursor = root
    for part in relative_path.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ConsumerError("snapshot_symlink", "managed snapshot paths cannot contain symlinks")
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ConsumerError("snapshot_escape", "managed snapshot must stay inside its asset root") from exc
    if not resolved.is_file():
        raise ConsumerError("snapshot_missing", f"managed snapshot must be a regular file: {resolved.name}")
    return resolved


def verify_managed_snapshot(asset_root: Path | None = None) -> dict[str, Any]:
    try:
        root = (asset_root or managed_asset_root()).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ConsumerError("snapshot_missing", "managed snapshot asset root is missing") from exc
    if not root.is_dir():
        raise ConsumerError("snapshot_missing", "managed snapshot asset root must be a directory")
    contract_path = managed_regular_file(root, CONTRACT_RELATIVE_PATH)
    provenance_path = managed_regular_file(root, PROVENANCE_RELATIVE_PATH)
    planner_path = managed_regular_file(root, PLANNER_RELATIVE_PATH)

    provenance = read_json(provenance_path)
    contract = read_json(contract_path)
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

    contract_digest = sha256_file(contract_path)
    planner_digest = sha256_file(planner_path)
    if contract_digest != CONTRACT_SHA256 or contract_digest != provenance.get("contract", {}).get("sha256"):
        raise ConsumerError("contract_digest_mismatch", "Core contributor branch contract digest mismatch")
    if planner_digest != PLANNER_SHA256 or planner_digest != provenance.get("planner", {}).get("sha256"):
        raise ConsumerError("planner_digest_mismatch", "Core contributor branch planner digest mismatch")
    return {
        "asset_root": root,
        "contract_path": contract_path,
        "planner_path": planner_path,
        "provenance_path": provenance_path,
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

    arguments = [node, str(assets["planner_path"]), "plan"]
    for key, value in options.items():
        if value is None or value is False:
            continue
        arguments.append(f"--{key.replace('_', '-')}")
        if not isinstance(value, bool):
            arguments.append(str(value))
    arguments.append("--json")
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
        raise ConsumerError("planner_timeout", "Core branch planner exceeded its execution deadline") from exc
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
