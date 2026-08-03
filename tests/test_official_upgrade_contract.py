from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from scripts import evozeus_official_upgrade_verify as verifier


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "contracts/v1"
PROTOCOL_SHA256 = "40421d4f89f853a872f47c85d2a71a52c239292ac41e8de284fc18c8861d9fce"


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


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _protocol_v1_base(tmp_path: Path) -> Path:
    root = tmp_path / "trusted-base"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    protocol_path = root / verifier.PROTOCOL_REL
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["allowed_operation_types"] = [
        "create_exact",
        "replace_exact",
        "manifest_patch",
    ]
    _write_json(protocol_path, protocol)
    protocol_sha256 = _sha256(protocol_path.read_bytes())
    profile_path = (
        root / "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["protocol"]["sha256"] = protocol_sha256
    _write_json(profile_path, profile)
    pointer_path = root / verifier.PROFILES_CURRENT_REL
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["entries"][0]["sha256"] = _sha256(profile_path.read_bytes())
    _write_json(pointer_path, pointer)
    return root


def _candidate_star(
    root: Path,
) -> tuple[
    dict[str, verifier.CandidateBlob],
    verifier.ConstructionRevisionResolver,
    str,
]:
    construction_revision = "a" * 40
    head_sha = "b" * 40
    v11_relative = "contracts/v1/migrations/history/harness-skill/v1.1.0/closure.json"
    v12_relative = "contracts/v1/migrations/history/harness-skill/v1.2.0/closure.json"
    v11 = json.loads((root / v11_relative).read_text(encoding="utf-8"))
    v12 = json.loads(json.dumps(v11))
    v12["closure_version"] = "v1.2.0"
    v12["source"] = {
        "repository": "MetaInFLow/EvoZeus-CoEvolve",
        "construction_revision": construction_revision,
        "release_status": "release_required_for_apply",
        "required_release": "v0.16.0",
    }
    changes: dict[str, verifier.CandidateBlob] = {}
    construction_files: dict[str, verifier.ConstructionBlob] = {}
    skill_target = ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md"
    skill_source = "templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md"
    skill_before: dict[str, object] | None = None
    skill_after: dict[str, object] | None = None
    for item in v12["files"]:
        artifact = item.get("artifact_path")
        if artifact is None:
            continue
        old_artifact = (Path(v11_relative).parent / artifact).as_posix()
        new_artifact = (Path(v12_relative).parent / artifact).as_posix()
        data = (root / old_artifact).read_bytes()
        if item["target_path"] == skill_target:
            skill_before = next(
                entry for entry in v11["files"] if entry["target_path"] == skill_target
            )
            data += b"\n<!-- candidate-harness-v1.2 -->\n"
            item["sha256"] = _sha256(data)
            skill_after = item
            changes[skill_source] = _candidate_blob(skill_source, data)
        changes[new_artifact] = _candidate_blob(
            new_artifact,
            data,
            status="added",
            mode=item["mode"],
        )
        source_path = item.get("source_path")
        if source_path is not None and item.get("source_binding") == "construction_revision":
            source_data = data if source_path == skill_source else (root / source_path).read_bytes()
            source_mode = (
                "100755"
                if (root / source_path).stat().st_mode & 0o100
                else "100644"
            )
            construction_files[source_path] = verifier.ConstructionBlob(
                path=source_path,
                mode=source_mode,
                data=source_data,
            )
    assert skill_before is not None and skill_after is not None
    changes[v12_relative] = _candidate_blob(
        v12_relative,
        _json_bytes(v12),
        status="added",
    )
    v12_sha256 = _sha256(_json_bytes(v12))
    protocol_sha256 = _sha256((root / verifier.PROTOCOL_REL).read_bytes())
    old_profile = json.loads(
        (
            root
            / "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
        ).read_text(encoding="utf-8")
    )
    direct_v10 = json.loads(json.dumps(old_profile))
    direct_v10["profile_id"] = "canonical-v1.0-to-v1.2"
    direct_v10["protocol"]["sha256"] = protocol_sha256
    direct_v10["to_closure"] = {
        "path": "migrations/history/harness-skill/v1.2.0/closure.json",
        "sha256": v12_sha256,
    }
    direct_v10["release_axis"]["target_wrapper_to"] = "v0.15.0"
    direct_v10["release_axis"]["artifact_source_to"]["release"] = "v0.16.0"
    for operation in direct_v10["operations"]:
        if operation["target_path"] == skill_target:
            operation["postimage"] = {
                "artifact_path": (
                    "migrations/history/harness-skill/v1.2.0/"
                    + str(skill_after["artifact_path"])
                ),
                "sha256": skill_after["sha256"],
                "mode": skill_after["mode"],
            }
    direct_v11 = {
        "schema_version": "evozeus.coevolve.official-upgrade-profile.v1",
        "profile_id": "canonical-v1.1-to-v1.2",
        "profile_version": "v1.0.0",
        "protocol": {
            "path": "migrations/protocols/official-upgrade-protocol-v1.json",
            "sha256": protocol_sha256,
        },
        "from_closure": {
            "path": "migrations/history/harness-skill/v1.1.0/closure.json",
            "sha256": _sha256((root / v11_relative).read_bytes()),
        },
        "to_closure": {
            "path": "migrations/history/harness-skill/v1.2.0/closure.json",
            "sha256": v12_sha256,
        },
        "release_axis": {
            "target_wrapper_from": "v0.15.0",
            "target_wrapper_to": "v0.15.0",
            "artifact_source_from": {
                "kind": "construction_revision",
                "revision": v11["source"]["construction_revision"],
                "release": "v0.15.0",
            },
            "artifact_source_to": {
                "kind": "required_release",
                "release": "v0.16.0",
                "binding": "contract_bundle.source_revision",
            },
        },
        "automatic": True,
        "operations": [
            {
                "change_id": "replace:" + skill_target,
                "type": "replace_exact",
                "target_path": skill_target,
                "preimage": {
                    "sha256": skill_before["sha256"],
                    "mode": skill_before["mode"],
                },
                "postimage": {
                    "artifact_path": (
                        "migrations/history/harness-skill/v1.2.0/"
                        + str(skill_after["artifact_path"])
                    ),
                    "sha256": skill_after["sha256"],
                    "mode": skill_after["mode"],
                },
            }
        ],
        "deferred_rendered_surfaces": old_profile["deferred_rendered_surfaces"],
        "protected_business_surfaces": old_profile["protected_business_surfaces"],
        "fallback": old_profile["fallback"],
    }
    profile_entries = []
    for filename, profile in (
        ("canonical-v1.0-to-v1.2-v1.json", direct_v10),
        ("canonical-v1.1-to-v1.2-v1.json", direct_v11),
    ):
        relative = "contracts/v1/migrations/profiles/" + filename
        data = _json_bytes(profile)
        changes[relative] = _candidate_blob(relative, data, status="added")
        profile_entries.append(
            {
                "id": profile["profile_id"],
                "version": profile["profile_version"],
                "path": "migrations/profiles/" + filename,
                "sha256": _sha256(data),
            }
        )
    closure_pointer = {
        "schema_version": "evozeus.coevolve.current-pointer.v1",
        "pointer_id": "using-evozeus-harness-current-closure",
        "entries": [
            {
                "id": "using-evozeus-harness",
                "version": "v1.2.0",
                "path": "migrations/history/harness-skill/v1.2.0/closure.json",
                "sha256": v12_sha256,
            }
        ],
    }
    profile_pointer = {
        "schema_version": "evozeus.coevolve.current-pointer.v1",
        "pointer_id": "official-upgrade-current-profiles",
        "entries": profile_entries,
    }
    changes[verifier.HISTORY_CURRENT_REL] = _candidate_blob(
        verifier.HISTORY_CURRENT_REL,
        _json_bytes(closure_pointer),
    )
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(profile_pointer),
    )

    def resolver(
        repository: str,
        revision: str,
        resolved_head: str,
        source_paths: frozenset[str],
    ) -> verifier.ConstructionRevisionEvidence:
        return verifier.ConstructionRevisionEvidence(
            repository=repository,
            revision=revision,
            head_sha=resolved_head,
            is_ancestor=True,
            files={path: construction_files[path] for path in source_paths},
        )

    return changes, resolver, head_sha


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


def test_rendered_surfaces_are_explicitly_excluded_from_automatic_upgrade() -> None:
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
                    "policy": "render_at_fresh_attach",
                    "without_receipt": "preserve_byte_exact",
                    "migration_policy": "preserve_byte_exact_no_auto_upgrade",
                }
    for closure in (v10, v11):
        workflow = next(
            item
            for item in closure["files"]
            if item["target_path"]
            == ".github/workflows/evozeus-wrapper-preflight.yml"
        )
        assert workflow["kind"] == "exact"
        assert workflow["materialization"] == {"policy": "copy_exact"}


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


def test_github_construction_resolver_binds_compare_tree_and_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40
    head_sha = "b" * 40
    blob_oid = "c" * 40
    calls: list[str] = []

    def github_json(url: str, _token: str) -> dict[str, object]:
        calls.append(url)
        if "/compare/" in url:
            return {
                "status": "ahead",
                "merge_base_commit": {"sha": revision},
            }
        return {
            "truncated": False,
            "tree": [
                {
                    "path": "templates/target/example.txt",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_oid,
                }
            ],
        }

    monkeypatch.setattr(verifier, "_github_json", github_json)
    monkeypatch.setattr(verifier, "_github_blob", lambda *_args: b"trusted\n")
    resolver = verifier._github_construction_revision_resolver("token")

    evidence = resolver(
        "MetaInFLow/EvoZeus-CoEvolve",
        revision,
        head_sha,
        frozenset({"templates/target/example.txt"}),
    )

    assert evidence.is_ancestor is True
    assert evidence.files["templates/target/example.txt"].mode == "100644"
    assert evidence.files["templates/target/example.txt"].data == b"trusted\n"
    assert calls[0].endswith(f"/compare/{revision}...{head_sha}")
    assert calls[1].endswith(f"/git/trees/{revision}?recursive=1")


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


def test_catalog_requires_a_unique_direct_profile_from_each_historical_closure(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    profile_relative = "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json"
    duplicate_relative = "contracts/v1/migrations/profiles/duplicate-v1.0-to-v1.1-v1.json"
    duplicate = json.loads((root / profile_relative).read_text(encoding="utf-8"))
    duplicate["profile_id"] = "duplicate-v1.0-to-v1.1"
    _write_json(root / duplicate_relative, duplicate)
    pointer_path = root / verifier.PROFILES_CURRENT_REL
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["entries"].append(
        {
            "id": duplicate["profile_id"],
            "version": duplicate["profile_version"],
            "path": "migrations/profiles/" + Path(duplicate_relative).name,
            "sha256": _sha256((root / duplicate_relative).read_bytes()),
        }
    )
    _write_json(pointer_path, pointer)

    with pytest.raises(verifier.VerificationError, match="duplicate from closure"):
        verifier.verify_catalog(verifier.FilesystemStore(root))


def test_catalog_rejects_an_active_profile_that_does_not_end_at_current(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    current_path = root / verifier.HISTORY_CURRENT_REL
    current = json.loads(current_path.read_text(encoding="utf-8"))
    v10_relative = "contracts/v1/migrations/history/harness-skill/v1.0.0/closure.json"
    current["entries"][0] = {
        "id": "using-evozeus-harness",
        "version": "v1.0.0",
        "path": "migrations/history/harness-skill/v1.0.0/closure.json",
        "sha256": _sha256((root / v10_relative).read_bytes()),
    }
    _write_json(current_path, current)

    with pytest.raises(verifier.VerificationError, match="point directly to the current"):
        verifier.verify_catalog(verifier.FilesystemStore(root))


def test_candidate_rotates_to_a_direct_to_current_profile_star(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)

    report = verifier.verify_candidate(
        verifier.FilesystemStore(root),
        changes,
        head_sha=head_sha,
        construction_resolver=resolver,
    )

    assert report["status"] == "verified_candidate"
    assert report["base_closure_version"] == "v1.1.0"
    assert report["candidate_closure_version"] == "v1.2.0"


def test_candidate_star_must_cover_base_current_and_prior_active_from_closures(
    tmp_path: Path,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, resolver, head_sha = _candidate_star(root)
    pointer = json.loads(changes[verifier.PROFILES_CURRENT_REL].loader().decode("utf-8"))
    pointer["entries"] = pointer["entries"][1:]
    changes[verifier.PROFILES_CURRENT_REL] = _candidate_blob(
        verifier.PROFILES_CURRENT_REL,
        _json_bytes(pointer),
    )

    with pytest.raises(verifier.VerificationError, match="historical coverage"):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=resolver,
        )


@pytest.mark.parametrize("fault", ["not_ancestor", "wrong_bytes", "wrong_mode"])
def test_candidate_construction_revision_requires_ancestor_tree_bytes_and_mode(
    tmp_path: Path,
    fault: str,
) -> None:
    root = _protocol_v1_base(tmp_path)
    changes, valid_resolver, head_sha = _candidate_star(root)

    def faulty_resolver(
        repository: str,
        revision: str,
        resolved_head: str,
        source_paths: frozenset[str],
    ) -> verifier.ConstructionRevisionEvidence:
        evidence = valid_resolver(repository, revision, resolved_head, source_paths)
        files = dict(evidence.files)
        if fault in {"wrong_bytes", "wrong_mode"}:
            path = sorted(files)[0]
            original = files[path]
            files[path] = verifier.ConstructionBlob(
                path=path,
                mode="100755" if fault == "wrong_mode" and original.mode == "100644" else (
                    "100644" if fault == "wrong_mode" else original.mode
                ),
                data=(original.data + b"drift") if fault == "wrong_bytes" else original.data,
            )
        return verifier.ConstructionRevisionEvidence(
            repository=evidence.repository,
            revision=evidence.revision,
            head_sha=evidence.head_sha,
            is_ancestor=fault != "not_ancestor",
            files=files,
        )

    with pytest.raises(
        verifier.VerificationError,
        match="ancestor|differs from construction revision",
    ):
        verifier.verify_candidate(
            verifier.FilesystemStore(root),
            changes,
            head_sha=head_sha,
            construction_resolver=faulty_resolver,
        )


def test_inactive_existing_history_and_profiles_remain_immutable(tmp_path: Path) -> None:
    root = _protocol_v1_base(tmp_path)
    base = verifier.FilesystemStore(root)
    inactive_closure = (
        root
        / "contracts/v1/migrations/history/harness-skill/v0.9.0/closure.json"
    )
    inactive_closure.parent.mkdir(parents=True)
    inactive_closure.write_text("{}\n", encoding="utf-8")
    prefixes = verifier._immutable_history_prefixes(base)

    assert verifier._protected_candidate_change(
        base,
        "contracts/v1/migrations/history/harness-skill/v0.9.0/artifacts/late.md",
        prefixes,
    )
    assert verifier._protected_candidate_change(
        base,
        "contracts/v1/migrations/profiles/canonical-v1.0-to-v1.1-v1.json",
        prefixes,
    )


def test_protocol_v1_rejects_rendered_surface_changes() -> None:
    before = {
        "kind": "rendered_template",
        "mode": "100644",
        "ownership": "wrapper_managed",
        "sha256": "1" * 64,
        "materialization": {"policy": "preserve_byte_exact_no_auto_upgrade"},
    }
    after = {**before, "sha256": "2" * 64}

    with pytest.raises(verifier.VerificationError, match="cannot change a rendered surface"):
        verifier.closure_diff({"rendered.md": before}, {"rendered.md": after})
