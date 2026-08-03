from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "contracts/v1"
SCHEMA = json.loads(
    (BUNDLE / "migrations/schemas/target-closure-v1.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = Draft202012Validator(SCHEMA)
NOTICE_TARGET = ".evozeus-wrapper/scripts/evozeus_notice.py"
NOTICE_SOURCE = "scripts/evozeus_notice.py"


def _closure(version: str) -> dict[str, object]:
    return json.loads(
        (
            BUNDLE
            / f"migrations/history/harness-skill/{version}/closure.json"
        ).read_text(encoding="utf-8")
    )


def _entry(closure: dict[str, object], target_path: str) -> dict[str, object]:
    return next(
        item
        for item in closure["files"]
        if item["target_path"] == target_path
    )


def _git_mode(revision: str, path: str) -> str:
    result = subprocess.run(
        ["git", "ls-tree", revision, "--", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.split(maxsplit=1)[0]


@pytest.mark.parametrize("version", ["v1.0.0", "v1.1.0"])
def test_notice_declares_the_only_target_mode_materialization_exception(
    version: str,
) -> None:
    closure = _closure(version)
    VALIDATOR.validate(closure)
    notice = _entry(closure, NOTICE_TARGET)
    artifact = (
        BUNDLE
        / "migrations/history/harness-skill"
        / version
        / notice["artifact_path"]
    )

    assert notice["source_path"] == NOTICE_SOURCE
    assert notice["mode"] == "100755"
    assert notice["materialization"] == {
        "policy": "copy_exact",
        "mode_policy": "set_declared_executable",
    }
    assert _git_mode("HEAD", str(artifact.relative_to(ROOT))) == "100644"
    assert _git_mode(closure["source"]["construction_revision"], NOTICE_SOURCE) == "100644"

    exceptions = [
        item
        for item in closure["files"]
        if "mode_policy" in item["materialization"]
    ]
    assert exceptions == [notice]


@pytest.mark.parametrize("version", ["v1.0.0", "v1.1.0"])
def test_notice_mode_exception_is_required_and_cannot_move_to_skill(
    version: str,
) -> None:
    closure = _closure(version)
    missing = copy.deepcopy(closure)
    del _entry(missing, NOTICE_TARGET)["materialization"]["mode_policy"]
    assert list(VALIDATOR.iter_errors(missing))

    misplaced = copy.deepcopy(closure)
    skill = _entry(
        misplaced,
        ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md",
    )
    skill["materialization"]["mode_policy"] = "set_declared_executable"
    assert list(VALIDATOR.iter_errors(misplaced))
