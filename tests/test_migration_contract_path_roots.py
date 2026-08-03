from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import evozeus_harness_migration as migration_kernel


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_RELATIVE = Path(
    "contracts/v1/migrations/harness-migration-contract-v1.json"
)
MANIFEST_RELATIVE = Path("contracts/v1/manifest.json")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_contract_loader_accepts_the_three_explicit_path_roots() -> None:
    bundle = migration_kernel.load_migration_contract(ROOT)

    assert bundle["contract"]["path_roots"] == {
        "artifact_path": "contracts/v1",
        "repository_path": "repository_root",
        "target_path": "target_repository_root",
    }


def test_contract_loader_rejects_a_missing_repository_path_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    shutil.copytree(ROOT / "contracts", source / "contracts")
    contract_path = source / CONTRACT_RELATIVE
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    del contract["path_roots"]["repository_path"]
    _write_json(contract_path, contract)

    manifest_path = source / MANIFEST_RELATIVE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in manifest["files"]
        if item["path"] == "migrations/harness-migration-contract-v1.json"
    )
    entry["sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    with pytest.raises(
        ValueError,
        match="migration contract path_roots are missing or ambiguous",
    ):
        migration_kernel.load_migration_contract(source)
