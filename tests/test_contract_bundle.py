import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "contracts" / "v1"
TARGET_TEMPLATES = ROOT / "templates" / "target"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(root: Path, relative_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(relative_paths):
        path = root / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_contract_manifest_hashes_every_declared_file() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "evozeus.coevolve.contract-manifest.v1"
    assert manifest["bundle_id"] == "evozeus-coevolve"
    assert manifest["runtime_compatibility"] == {
        "min_inclusive": "0.1.0",
        "max_exclusive": "0.3.0",
    }
    assert manifest["source_revision"] == "v0.14.0"
    declared_paths = {entry["path"] for entry in manifest["files"]}
    actual_paths = {
        path.relative_to(BUNDLE).as_posix()
        for path in BUNDLE.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert declared_paths == actual_paths
    for entry in manifest["files"]:
        assert entry["sha256"] == sha256_file(BUNDLE / entry["path"])


def test_contract_manifest_depends_on_core_owned_user_prompt_runtime() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["source_revision"] == "v0.14.0"
    assert manifest["external_component_dependencies"] == [
        {
            "component_id": "evozeus_user_prompt_lesson_runtime",
            "repository": "MetaInFLow/EvoZeus",
            "source_revision": "0ee5c2db86dbddb830173eb841d3e0dc623896df",
            "availability": "unreleased",
            "pull_request": "https://github.com/MetaInFLow/EvoZeus/pull/50",
            "api": "evozeus.user-prompt.lesson-runtime.v1",
            "dispatcher": ".evozeus/hooks/evozeus_wrapper_dispatcher.py",
            "owner": "evozeus-core",
        }
    ]


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
