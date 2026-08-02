from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

import pytest

from scripts import evozeus_official_upgrade_verify as verifier


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "contracts/v1"
PROTOCOL_SHA256 = "83f1957eb416ec8bc8f14c9a6d5cf8a476ce2fd03cea49a26f18d262ce8b3519"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate_blob(
    path: str,
    value: bytes | None,
    *,
    status: str = "modified",
    mode: str | None = "100644",
    object_type: str | None = "blob",
) -> verifier.CandidateBlob:
    return verifier.CandidateBlob(
        path=path,
        status=status,
        mode=mode,
        object_type=object_type,
        oid=None,
        loader=(None if value is None else lambda content=value: content),
    )


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _base_store() -> verifier.FilesystemStore:
    return verifier.FilesystemStore(ROOT)


def _profile() -> tuple[str, dict[str, object]]:
    relative = "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
    return relative, json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_current_official_upgrade_catalog_is_hash_closed() -> None:
    report = verifier.verify_catalog(_base_store())

    assert report == {
        "status": "verified",
        "protocol": "evozeus-official-upgrade@v1.0.0",
        "current_closure": (
            "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
        ),
        "current_closure_version": "v1.1.0",
        "profiles": ["canonical-v1.0-to-v1.1@v1.0.0"],
    }
    assert _sha256((ROOT / verifier.PROTOCOL_REL).read_bytes()) == PROTOCOL_SHA256


@pytest.mark.parametrize("version", ["v1.0.0", "v1.1.0"])
def test_frozen_closure_artifacts_equal_the_declared_construction_revision(
    version: str,
) -> None:
    closure_path = (
        ROOT
        / "contracts/v1/migrations/history/harness-skill"
        / version
        / "closure.json"
    )
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    revision = closure["source"]["construction_revision"]

    for item in closure["files"]:
        source_path = item.get("source_path")
        artifact_path = item.get("artifact_path")
        if source_path is None or item.get("source_binding") != "construction_revision":
            continue
        historical = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{revision}:{source_path}"],
            capture_output=True,
            check=False,
        )
        assert historical.returncode == 0, (version, source_path, historical.stderr)
        assert historical.stdout == (closure_path.parent / artifact_path).read_bytes()


def test_closure_release_status_does_not_claim_checkpoint_is_a_release() -> None:
    v10 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.0.0/closure.json").read_text()
    )
    v11 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.1.0/closure.json").read_text()
    )

    assert v10["source"] == {
        "repository": "MetaInFLow/EvoZeus-CoEvolve",
        "construction_revision": "44d1fbdefc1e1de47a35c3ca39d2ba083661d569",
        "release_status": "unreleased_exact_snapshot",
        "required_release": None,
    }
    assert v11["source"] == {
        "repository": "MetaInFLow/EvoZeus-CoEvolve",
        "construction_revision": "ee199b5d50bd12b26d8150538a85b1e959cadf0a",
        "release_status": "release_required_for_apply",
        "required_release": "v0.15.0",
    }

    _, profile = _profile()
    assert profile["release_axis"] == {
        "target_wrapper_from": "v0.14.0",
        "target_wrapper_to": "v0.15.0",
        "artifact_source_from": {
            "kind": "construction_revision",
            "revision": "44d1fbdefc1e1de47a35c3ca39d2ba083661d569",
            "release": None,
        },
        "artifact_source_to": {
            "kind": "required_release",
            "release": "v0.15.0",
            "binding": "contract_bundle.source_revision",
        },
    }


@pytest.mark.parametrize(
    ("axis", "field", "value", "message"),
    [
        (
            "artifact_source_from",
            "revision",
            "1" * 40,
            "from-artifact provenance",
        ),
        (
            "artifact_source_to",
            "release",
            "v9.9.9",
            "to-artifact provenance",
        ),
    ],
)
def test_profile_artifact_provenance_must_equal_bound_closure_sources(
    axis: str,
    field: str,
    value: str,
    message: str,
) -> None:
    relative, profile = _profile()
    profile["release_axis"][axis][field] = value
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match=message):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


def test_profile_operations_are_a_strict_bijection_with_closure_diff() -> None:
    store = _base_store()
    protocol = verifier.load_protocol(store)
    relative, raw_profile = _profile()
    profile = verifier.load_profile(store, relative, protocol)
    from_path = profile["_verified_from_path"]
    to_path = profile["_verified_to_path"]
    _, before = verifier.load_closure(store, from_path)
    _, after = verifier.load_closure(store, to_path)
    changes = verifier.closure_diff(before, after)

    assert {path: operation for path, (operation, _, _) in changes.items()} == {
        ".evozeus-wrapper/contracts/harness-migration-contract-v1.json": "create_exact",
        (
            ".evozeus-wrapper/docs/migrations/"
            "harness-skill-v1.0.0-to-v1.1.0.md"
        ): "create_exact",
        ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py": "replace_exact",
        ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md": "replace_exact",
        ".evozeus-wrapper/wrapper.json": "manifest_patch",
    }
    assert {item["target_path"] for item in raw_profile["operations"]} == set(changes)


def test_migration_contract_postimage_hash_is_locked_across_profile_and_closure() -> None:
    contract_sha256 = _sha256(
        (BUNDLE / "migrations/harness-migration-contract-v1.json").read_bytes()
    )
    _, profile = _profile()
    closure = json.loads(
        (BUNDLE / profile["to_closure"]["path"]).read_text(encoding="utf-8")
    )
    operation = next(
        item
        for item in profile["operations"]
        if item["target_path"]
        == ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
    )
    manifest_operation = next(
        item
        for item in profile["operations"]
        if item["target_path"] == ".evozeus-wrapper/wrapper.json"
    )
    manifest_contract = next(
        item["value"]
        for item in manifest_operation["patch"]
        if item["field"] == "migration_contract"
    )
    contract_file = next(
        item
        for item in closure["files"]
        if item["target_path"]
        == ".evozeus-wrapper/contracts/harness-migration-contract-v1.json"
    )
    wrapper_state = next(
        item["owned_state"]
        for item in closure["files"]
        if item["target_path"] == ".evozeus-wrapper/wrapper.json"
    )

    assert operation["postimage"]["sha256"] == contract_sha256
    assert contract_file["sha256"] == contract_sha256
    assert manifest_contract["sha256"] == f"sha256:{contract_sha256}"
    assert wrapper_state["migration_contract"]["sha256"] == f"sha256:{contract_sha256}"


def test_every_rendered_surface_is_receipt_gated_and_deferred() -> None:
    _, profile = _profile()
    v10 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.0.0/closure.json").read_text()
    )
    v11 = json.loads(
        (BUNDLE / "migrations/history/harness-skill/v1.1.0/closure.json").read_text()
    )
    rendered = {
        item["target_path"]
        for item in v10["files"]
        if item["kind"] == "rendered_template"
    }

    assert rendered == {
        ".evozeus-wrapper/CHANGELOG.md",
        ".evozeus-wrapper/WRAPPER.md",
        ".evozeus-wrapper/docs/_config.yml",
        ".evozeus-wrapper/docs/index.md",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/evozeus-wrapper-preflight.yml",
    }
    assert {
        item["target_path"]
        for item in profile["deferred_rendered_surfaces"]
    } == rendered
    assert {
        item["target_path"]
        for item in v11["files"]
        if item["kind"] == "rendered_template"
    } == rendered
    for closure in (v10, v11):
        for item in closure["files"]:
            if item["target_path"] in rendered:
                assert item["materialization"] == {
                    "policy": "render_with_install_receipt",
                    "without_receipt": "preserve_byte_exact",
                    "migration_policy": "receipt_gated_preserve_exact",
                }


@pytest.mark.parametrize("operation_type", ["delete", "rename", "shell", "copy"])
def test_unknown_or_destructive_profile_operation_is_rejected(operation_type: str) -> None:
    relative, profile = _profile()
    profile["operations"][0]["type"] = operation_type
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match="unknown operation"):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


def test_profile_cannot_target_business_instruction_bytes() -> None:
    relative, profile = _profile()
    profile["operations"][0]["target_path"] = "SKILL.md"
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match="no closure diff"):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


def test_missing_operation_breaks_closure_diff_bijection() -> None:
    relative, profile = _profile()
    profile["operations"].pop()
    store = verifier.CandidateStore(
        _base_store(),
        {relative: _candidate_blob(relative, _json_bytes(profile))},
    )

    with pytest.raises(verifier.VerificationError, match="lacks profile operations"):
        verifier.load_profile(store, relative, verifier.load_protocol(_base_store()))


@pytest.mark.parametrize(
    "path",
    [
        verifier.VERIFIER_REL,
        verifier.WORKFLOW_REL,
        verifier.PROTOCOL_REL,
        "contracts/v1/migrations/schemas/target-closure-v1.schema.json",
        "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json",
    ],
)
def test_candidate_cannot_modify_trusted_base_authority_or_history(path: str) -> None:
    changes = {path: _candidate_blob(path, b"candidate bytes\n")}

    with pytest.raises(
        verifier.VerificationError,
        match="modifies trusted base authority or history",
    ):
        verifier.verify_candidate(_base_store(), changes, head_sha="1" * 40)


def test_candidate_cannot_add_to_an_existing_immutable_version_directory() -> None:
    path = (
        "contracts/v1/migrations/history/harness-skill/"
        "v1.1.0/artifacts/generated/late-addition.md"
    )

    with pytest.raises(
        verifier.VerificationError,
        match="modifies trusted base authority or history",
    ):
        verifier.verify_candidate(
            _base_store(),
            {path: _candidate_blob(path, b"late mutation\n", status="added")},
            head_sha="2" * 40,
        )


@pytest.mark.parametrize(
    "mode,object_type",
    [
        ("120000", "blob"),
        ("160000", "commit"),
        ("040000", "tree"),
    ],
)
def test_candidate_symlink_submodule_or_tree_is_rejected(
    mode: str,
    object_type: str,
) -> None:
    path = "candidate-object"
    changes = {
        path: _candidate_blob(
            path,
            b"object\n",
            status="added",
            mode=mode,
            object_type=object_type,
        )
    }

    with pytest.raises(verifier.VerificationError, match="symlink, submodule"):
        verifier.verify_candidate(_base_store(), changes, head_sha="3" * 40)


def test_generated_migration_ledger_rejects_dates_and_self_reference() -> None:
    closure_relative = (
        "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
    )
    closure = json.loads((ROOT / closure_relative).read_text(encoding="utf-8"))
    ledger = next(
        item
        for item in closure["files"]
        if item["target_path"].endswith("harness-skill-v1.0.0-to-v1.1.0.md")
    )
    ledger_relative = (
        str(Path(closure_relative).parent / ledger["artifact_path"])
        .replace("\\", "/")
    )
    bad_ledger = b"migration date 2026-08-02 and plan_sha256=self\n"
    ledger["sha256"] = _sha256(bad_ledger)
    store = verifier.CandidateStore(
        _base_store(),
        {
            closure_relative: _candidate_blob(
                closure_relative,
                _json_bytes(closure),
            ),
            ledger_relative: _candidate_blob(ledger_relative, bad_ledger),
        },
    )

    with pytest.raises(verifier.VerificationError, match="contains a date"):
        verifier.load_closure(store, closure_relative)


def test_github_request_is_get_without_candidate_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve"

        def read(self, _limit: int) -> bytes:
            return b"{}"

    def open_request(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 30
        captured.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    assert verifier._github_json(
        "https://api.github.com/repos/MetaInFLow/EvoZeus-CoEvolve",
        "token",
    ) == {}
    assert captured[0].data is None
    assert captured[0].get_method() == "GET"


def test_pull_request_target_workflow_executes_only_trusted_base_code() -> None:
    workflow = (ROOT / verifier.WORKFLOW_REL).read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "permissions:\n  contents: read\n" in workflow
    assert "pull-requests:" not in workflow
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in workflow
    )
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.pull_request.head" not in workflow
    assert "verify-pull-request" in workflow
    assert "pip install" not in workflow
