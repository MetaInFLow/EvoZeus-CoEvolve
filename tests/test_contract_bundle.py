import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "contracts" / "v1"
TARGET_TEMPLATES = ROOT / "templates" / "target"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_mode(path: Path) -> str:
    return "100755" if path.stat().st_mode & 0o100 else "100644"


def tree_sha256(root: Path, relative_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(relative_paths):
        path = root / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_ci_uses_official_commit_pinned_actions() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4"
        in ci
    )
    assert (
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5"
        in ci
    )
    assert re.search(r"uses:\s+actions/(?:checkout|setup-python)@v[0-9]+", ci) is None


def test_contract_manifest_hashes_every_declared_file() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    executable_paths = {
        (
            "migrations/history/harness-skill/v1.0.0/artifacts/scripts/"
            "evozeus_wrapper_preflight.py"
        ),
        (
            "migrations/history/harness-skill/v1.1.0/artifacts/scripts/"
            "evozeus_wrapper_preflight.py"
        ),
        (
            "migrations/history/legacy-wrapper/v0.14.0/artifacts/scripts/"
            "evozeus_wrapper_preflight.py"
        ),
    }

    assert manifest["schema_version"] == "evozeus.coevolve.contract-manifest.v1"
    assert manifest["bundle_id"] == "evozeus-coevolve"
    assert manifest["bundle_version"] == "v1.2.0"
    assert manifest["runtime_compatibility"] == {
        "min_inclusive": "0.1.0",
        "max_exclusive": "0.3.0",
    }
    assert manifest["source_revision"] == "v0.15.0"
    declared_paths = {entry["path"] for entry in manifest["files"]}
    actual_paths = {
        path.relative_to(BUNDLE).as_posix()
        for path in BUNDLE.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert declared_paths == actual_paths
    assert git_mode(BUNDLE / "manifest.json") == "100644"
    for entry in manifest["files"]:
        path = BUNDLE / entry["path"]
        assert set(entry) == {"path", "sha256", "mode", "role"}
        assert entry["sha256"] == sha256_file(path)
        assert entry["mode"] == (
            "100755" if entry["path"] in executable_paths else "100644"
        )
        assert entry["mode"] == git_mode(path)

    repository_files = {
        entry["path"]: entry for entry in manifest["trusted_repository_files"]
    }
    assert set(repository_files) == {
        "requirements-commonmark.lock",
        "scripts/evozeus_harness_legacy_prompt_adapter.py",
    }
    for relative, entry in repository_files.items():
        path = ROOT / relative
        assert set(entry) == {"path", "sha256", "mode", "role"}
        assert entry["sha256"] == sha256_file(path)
        assert entry["mode"] == "100644" == git_mode(path)


def test_contract_manifest_depends_on_core_owned_user_prompt_runtime() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    dependency = json.loads(
        (BUNDLE / "user-prompt-lesson-runtime-lifecycle.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["source_revision"] == "v0.15.0"
    assert "external_component_dependencies" not in manifest
    assert dependency == {
        "schema_version": "evozeus.coevolve.external-runtime-dependency.v1",
        "component_id": "evozeus_user_prompt_lesson_runtime",
        "repository": "MetaInFLow/EvoZeus",
        "source_revision": "4dc94613ee01c6bd3c7fa8f5f123c6fe398742f4",
        "availability": "unreleased",
        "pull_request": "https://github.com/MetaInFLow/EvoZeus/pull/50",
        "api": "evozeus.user-prompt.lesson-runtime.v1",
        "dispatcher": ".evozeus/hooks/evozeus_wrapper_dispatcher.py",
        "owner": "evozeus-core",
    }
    dependency_entry = next(
        entry
        for entry in manifest["files"]
        if entry["path"] == "user-prompt-lesson-runtime-lifecycle.json"
    )
    assert dependency_entry["role"] == "external-runtime-dependency"
    assert dependency_entry["sha256"] == sha256_file(
        BUNDLE / dependency_entry["path"]
    )


def test_external_sidecar_inventory_has_no_target_writes() -> None:
    inventory = json.loads(
        (BUNDLE / "target-template-inventory.json").read_text(encoding="utf-8")
    )

    external = inventory["modes"]["external-sidecar"]
    assert external == {"target_writes": False, "files": []}


def test_governed_template_inventory_is_complete_and_hash_bound() -> None:
    inventory = json.loads(
        (BUNDLE / "target-template-inventory.json").read_text(encoding="utf-8")
    )
    governed = inventory["modes"]["governed-sidecar"]
    declared_paths = governed["files"]
    actual_paths = [
        path.relative_to(TARGET_TEMPLATES).as_posix()
        for path in TARGET_TEMPLATES.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]

    assert governed["availability"] == "compatibility-only"
    assert sorted(declared_paths) == sorted(actual_paths)
    assert governed["source_tree_sha256"] == f"sha256:{tree_sha256(TARGET_TEMPLATES, declared_paths)}"


def test_attachment_schema_fixes_slice_one_to_external_sidecar() -> None:
    schema = json.loads(
        (BUNDLE / "schemas" / "attachment-v1.schema.json").read_text(encoding="utf-8")
    )

    assert schema["additionalProperties"] is False
    assert schema["properties"]["mode"] == {"const": "external-sidecar"}
    assert schema["properties"]["target"]["additionalProperties"] is False
