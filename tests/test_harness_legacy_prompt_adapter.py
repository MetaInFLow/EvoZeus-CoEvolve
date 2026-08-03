from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts import evozeus_harness_legacy_prompt_adapter as adapter


ROOT = Path(__file__).resolve().parents[1]
SURFACE_PATH = ROOT / "tests/fixtures/diagnose-enterprise-ai-scenarios/SKILL.md"
MANIFEST_TEMPLATE_PATH = (
    ROOT
    / "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/artifacts/wrapper.json.tpl"
)
REAL_REPO = "MetaInFLow/diagnose-enterprise-ai-scenarios"
REAL_SKILL = "diagnose-enterprise-ai-scenarios"
REAL_SURFACE_SHA256 = "22b519a18fa4ec9b5ed1a892cd1895c1b68b84366c1286af8f8a403f35d79a04"
REAL_MANIFEST_SHA256 = "c05dbb63db5deb391a13e7093948324e6018f7c6bcb318c537b936dd1e173b52"

RENDERED_PRESERVE_DIGESTS = {
    ".evozeus-wrapper/CHANGELOG.md": (
        "278f1936971b585b0c9b632b843f6dcab6a1dcd589129d6a359c612f778b48bd"
    ),
    ".evozeus-wrapper/WRAPPER.md": (
        "8f28a3881ae8fa455a079b00c7af73d1fe0f90222cb26a2f821a41afe9ef385d"
    ),
    ".evozeus-wrapper/docs/_config.yml": (
        "ee8f601f0bab6634fe4b6f49d4423161b89e434b0dd5872733f69a9396ae0cd2"
    ),
    ".evozeus-wrapper/docs/index.md": (
        "4b83659ad45fd16ac5c60d8490eb9088753897144ddc5be17c28e29c0f3a116c"
    ),
    ".github/ISSUE_TEMPLATE/config.yml": (
        "2d3dfcb5da01e07883d1f2874263232d0f88b5b9dc77c1f5cb6b6a5916aec94b"
    ),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _render_manifest(
    *,
    applied_at: str = "2026-07-30",
    repo: str = REAL_REPO,
    skill_name: str = REAL_SKILL,
) -> bytes:
    text = MANIFEST_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        text.replace("{{APPLIED_AT}}", applied_at)
        .replace("{{REPO_NAME}}", repo)
        .replace("{{SKILL_NAME}}", skill_name)
        .encode("utf-8")
    )


def _file_states(
    surface: bytes,
    manifest: bytes,
    *,
    bundle: adapter.FrozenLegacyPromptBundle,
) -> dict[str, dict[str, str]]:
    states: dict[str, dict[str, str]] = {}
    for item in bundle.envelope["files"]:
        path = item["path"]
        if item["kind"] == "absent":
            states[path] = {"kind": "absent"}
        elif item["kind"] == "exact":
            states[path] = {
                "kind": "file",
                "sha256": item["sha256"],
                "mode": item["mode"],
            }
        else:
            states[path] = {
                "kind": "file",
                "sha256": RENDERED_PRESERVE_DIGESTS[path],
                "mode": item["mode"],
            }
    states[".evozeus-wrapper/wrapper.json"] = {
        "kind": "file",
        "sha256": _sha256(manifest),
        "mode": "100644",
    }
    states["SKILL.md"] = {
        "kind": "file",
        "sha256": _sha256(surface),
        "mode": "100644",
    }
    return states


@pytest.fixture(scope="module")
def bundle() -> adapter.FrozenLegacyPromptBundle:
    return adapter.load_frozen_bundle(ROOT)


def _plan(
    surface: bytes,
    *,
    bundle: adapter.FrozenLegacyPromptBundle,
    manifest: bytes | None = None,
    states: dict[str, dict[str, str]] | None = None,
) -> adapter.LegacyPromptTransformResult:
    manifest_bytes = manifest if manifest is not None else _render_manifest()
    file_states = states or _file_states(surface, manifest_bytes, bundle=bundle)
    return adapter.plan_supervised_legacy_prompt_transform(
        instruction_surface_bytes=surface,
        manifest_bytes=manifest_bytes,
        file_states=file_states,
        bundle=bundle,
    )


def _assert_manual(result: adapter.LegacyPromptTransformResult, phrase: str) -> None:
    assert result.decision == adapter.MANUAL_DECISION
    assert result.postimage is None
    assert result.proof["writes"] is False
    assert result.proof["destructive_authority"] is False
    assert phrase in result.proof["reasons"][0]


def test_frozen_real_source_projection_and_templates_are_byte_exact(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    manifest = _render_manifest()

    assert _sha256(surface) == REAL_SURFACE_SHA256
    assert len(surface) == 13_645
    assert _sha256(manifest) == REAL_MANIFEST_SHA256
    assert len(manifest) == 5_345
    assert bundle.envelope["source_evidence"] == {
        "repository": REAL_REPO,
        "repository_url": "https://github.com/MetaInFLow/diagnose-enterprise-ai-scenarios",
        "commit": "ee2bd6e8232d14ad9070b4ee7875595e9e8e4a4f",
        "tree": "801f60fa4df1a6c3a60fe44e8f0b81fe490cc5eb",
        "instruction_surface_path": "SKILL.md",
        "instruction_surface_sha256": REAL_SURFACE_SHA256,
        "instruction_surface_line_count_lf": 232,
        "manifest_sha256": REAL_MANIFEST_SHA256,
        "legacy_preflight_sha256": (
            "0ef6e008461dc8e61845ad6deae5fe239122c2415d81550a1e9d6e9838570aa1"
        ),
        "captured_at": "2026-07-31",
        "purpose": "Issue #38 reviewed v0.14 three-section Prompt source envelope",
    }

    expected_deleted = {
        "status": (
            3_725,
            "b6406d12b5eda61318bb12d2e2748b3733f62e64fd8a2bb34d44fbbfec84b535",
        ),
        "evolution": (
            2_438,
            "4a69026ecaa570ffd2aa8c3a298317cb091d542d07ac7beda404c2e748825ea6",
        ),
        "wrapper": (
            3_374,
            "e2019209aa242c6bf5d40430d36abed58a9cf01052f3d6a56233eda07f4c1ff8",
        ),
    }
    for binding in bundle.adapter["templates"]:
        kind = binding["kind"]
        rendered = adapter._render_template(  # noqa: SLF001 - byte oracle
            bundle.templates[kind],
            {
                "CURRENT_VERSION": "v0.1.0",
                "REPO_NAME": REAL_REPO,
                "VISIBILITY": "public",
                "WRAPPER_VERSION": "v0.14.0",
            },
            binding["placeholders"],
        )
        length, digest = expected_deleted[kind]
        assert len(rendered) == length
        assert _sha256(rendered) == digest


def test_lf_oracle_proves_exact_delete_retain_splice_and_is_deterministic(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    manifest = _render_manifest()
    states = _file_states(surface, manifest, bundle=bundle)
    before_states = copy.deepcopy(states)
    first = _plan(surface, bundle=bundle, manifest=manifest, states=states)
    second = _plan(surface, bundle=bundle, manifest=manifest, states=states)

    assert first.decision == adapter.SUPERVISED_DECISION
    assert first.postimage is not None
    assert first.postimage == second.postimage
    assert first.proof == second.proof
    assert states == before_states
    assert surface == SURFACE_PATH.read_bytes()
    assert manifest == _render_manifest()

    proof = first.proof
    transform = proof["instruction_surface_transform"]
    assert proof["writes"] is False
    assert proof["destructive_authority"] is False
    assert proof["manifest"]["sha256"] == "sha256:" + REAL_MANIFEST_SHA256
    assert transform["newline_style"] == "lf"
    assert transform["preimage_byte_length"] == 13_645
    assert transform["preimage_sha256"] == "sha256:" + REAL_SURFACE_SHA256
    assert transform["postimage_byte_length"] == 4_372
    assert transform["postimage_sha256"] == (
        "sha256:0d44ec65677c0dad94491362541fc1fbaf67fecc89f2a917f4f6bb5a8e52bc29"
    )
    assert [
        (item["kind"], item["start_byte"], item["end_byte"], item["sha256"])
        for item in transform["deleted_spans"]
    ] == [
        (
            "status",
            449,
            4_174,
            "sha256:b6406d12b5eda61318bb12d2e2748b3733f62e64fd8a2bb34d44fbbfec84b535",
        ),
        (
            "evolution",
            7_833,
            10_271,
            "sha256:4a69026ecaa570ffd2aa8c3a298317cb091d542d07ac7beda404c2e748825ea6",
        ),
        (
            "wrapper",
            10_271,
            13_645,
            "sha256:e2019209aa242c6bf5d40430d36abed58a9cf01052f3d6a56233eda07f4c1ff8",
        ),
    ]
    assert transform["retained_target_bytes"]["byte_length"] == 4_108
    assert transform["retained_target_bytes"]["concatenated_sha256"] == (
        "sha256:3822b34e173d290cd2a93ccd083f706546dcc90c2069c77d4d9b3bcf74db8b2e"
    )
    assert transform["retained_target_bytes"]["postimage_projection_sha256"] == (
        transform["retained_target_bytes"]["concatenated_sha256"]
    )
    assert transform["inserted_envelope"]["start_byte"] == 449
    assert transform["inserted_envelope"]["byte_length"] == 264
    assert transform["inserted_envelope"]["activation_sha256_lf"] == (
        "sha256:078bb2020284fbd6f91c12e46a2c726e64a4f4bbdef0320f4e40adcef26d3cea"
    )
    assert transform["postconditions"] == {
        "canonical_marker_count": 1,
        "legacy_owned_section_count": 0,
        "retained_target_bytes": "byte_exact",
    }
    assert first.postimage.count(b"<!-- evozeus-harness-entry:v1 -->") == 1
    assert b"## EvoZeus-CoEvolve \xe7\x8a\xb6\xe6\x80\x81\xe6\xa3\x80\xe6\x9f\xa5" not in first.postimage
    assert b"## \xe8\x87\xaa\xe8\xbf\x9b\xe5\x8c\x96\xe6\x96\xb9\xe6\xb3\x95" not in first.postimage
    assert b"## EvoZeus-CoEvolve\n" not in first.postimage
    assert all(
        line.startswith(("@@", "+", "-"))
        for line in transform["zero_context_diff"]["text"].splitlines()[2:]
    )
    proof_without_digest = dict(proof)
    proof_without_digest.pop("proof_sha256")
    assert proof["proof_sha256"] == (
        "sha256:" + adapter.canonical_json_sha256(proof_without_digest)
    )


def test_crlf_oracle_preserves_newline_style_and_normalized_diff(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    lf_surface = SURFACE_PATH.read_bytes()
    lf_result = _plan(lf_surface, bundle=bundle)
    crlf_surface = lf_surface.replace(b"\n", b"\r\n")
    result = _plan(crlf_surface, bundle=bundle)

    assert result.decision == adapter.SUPERVISED_DECISION
    assert result.postimage is not None
    transform = result.proof["instruction_surface_transform"]
    assert transform["newline_style"] == "crlf"
    assert transform["preimage_byte_length"] == 13_877
    assert transform["preimage_sha256"] == (
        "sha256:f4b6d8ed29e2e3383fba2cac6526171021489c356353e04ac501db2f38e0156e"
    )
    assert transform["postimage_byte_length"] == 4_503
    assert transform["postimage_sha256"] == (
        "sha256:b01832ace3aa1de1c10fcf37771420c55a47164f73300cea9cb2878a1f465daa"
    )
    assert [item["sha256"] for item in transform["deleted_spans"]] == [
        "sha256:9c6bb03c3ec892db6cd140a2dedb77f3ac34b32427a17d1c8831eea9a05a6a80",
        "sha256:efb2d7f574328f025db08becf816f1a4a497338be8a9a92e7212d18742c6a082",
        "sha256:0998576508ba7c4c74208e8f081075e0f55744478aa2cee308ecb991a8445666",
    ]
    assert transform["retained_target_bytes"]["byte_length"] == 4_234
    assert transform["retained_target_bytes"]["concatenated_sha256"] == (
        "sha256:86f923032c74dc316f88e95f969370820871451bf13a463e54e38d1a2633c87d"
    )
    assert b"\r\n" in result.postimage
    assert result.postimage.replace(b"\r\n", b"").find(b"\n") == -1
    assert transform["zero_context_diff"] == (
        lf_result.proof["instruction_surface_transform"]["zero_context_diff"]
    )


def test_dynamic_reviewed_variables_are_template_bound(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes().replace(b"v0.1.0", b"v12.34.56")
    surface = surface.replace(b"Visibility: `public`", b"Visibility: `private`")
    result = _plan(surface, bundle=bundle)

    assert result.decision == adapter.SUPERVISED_DECISION
    assert result.proof["instruction_surface_transform"]["variables"] == {
        "REPO_NAME": REAL_REPO,
        "WRAPPER_VERSION": "v0.14.0",
        "CURRENT_VERSION": "v12.34.56",
        "VISIBILITY": "private",
    }


def test_code_fenced_lookalikes_are_business_bytes_not_wrapper_ambiguity(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    anchor = "把简短的企业描述转化为紧凑、可决策、可验证的 AI 场景诊断。默认使用中文；用户明确使用其他语言时跟随用户语言。\n"
    lookalikes = """
```markdown
## EvoZeus-CoEvolve 状态检查
本段是 Skill 入口 preflight
## 自进化方法
本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环
## EvoZeus-CoEvolve
本区由 EvoZeus-CoEvolve 追加
```
"""
    surface = surface.replace(
        anchor.encode("utf-8"),
        (anchor + lookalikes).encode("utf-8"),
        1,
    )
    result = _plan(surface, bundle=bundle)

    assert result.decision == adapter.SUPERVISED_DECISION
    assert result.postimage is not None
    assert lookalikes.encode("utf-8") in result.postimage
    assert result.proof["instruction_surface_transform"]["retained_target_bytes"][
        "postimage_projection_sha256"
    ] == result.proof["instruction_surface_transform"]["retained_target_bytes"][
        "concatenated_sha256"
    ]


@pytest.mark.parametrize(
    "visible_duplicate",
    [
        "\n## EvoZeus-CoEvolve 状态检查\n\nATX duplicate.\n",
        "\nEvoZeus-CoEvolve 状态检查\n-------------------------------\n\nSetext duplicate.\n",
        "\n> ## EvoZeus-CoEvolve 状态检查\n>\n> blockquote duplicate.\n",
        (
            "\n## *EvoZeus*-CoEvolve "
            "[状态](https://example.invalid)&#x68C0;&#x67E5;\n\n"
            "Inline AST duplicate.\n"
        ),
        "\n## EvoZeus-CoEvolve `状态检查`\n\nCode inline duplicate.\n",
        (
            "\n## ![EvoZeus-CoEvolve 状态检查]"
            "(https://example.invalid/heading.png)\n\nImage alt duplicate.\n"
        ),
        "\n<h2>EvoZeus-CoEvolve <em>状态</em>&#x68C0;&#x67E5;</h2>\n",
        (
            "\nvisible HTML inline <h2>EvoZeus-CoEvolve "
            "<span>状态</span>&#x68C0;&#x67E5;</h2> tail\n"
        ),
    ],
    ids=[
        "atx",
        "setext",
        "blockquote",
        "emphasis-link-entity",
        "code-inline",
        "image-alt",
        "html-block",
        "html-inline",
    ],
)
def test_commonmark_ast_visible_heading_forms_fail_closed(
    bundle: adapter.FrozenLegacyPromptBundle,
    visible_duplicate: str,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    anchor = (
        "把简短的企业描述转化为紧凑、可决策、可验证的 AI 场景诊断。"
        "默认使用中文；用户明确使用其他语言时跟随用户语言。\n"
    )
    surface = surface.replace(
        anchor.encode("utf-8"),
        (anchor + visible_duplicate).encode("utf-8"),
        1,
    )

    result = _plan(surface, bundle=bundle)

    _assert_manual(result, "legacy status heading is missing or ambiguous")


def test_indented_code_heading_is_ignored_as_business_bytes(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    anchor = (
        "把简短的企业描述转化为紧凑、可决策、可验证的 AI 场景诊断。"
        "默认使用中文；用户明确使用其他语言时跟随用户语言。\n"
    )
    lookalike = "\n    ## EvoZeus-CoEvolve 状态检查\n"
    surface = surface.replace(
        anchor.encode("utf-8"),
        (anchor + lookalike).encode("utf-8"),
        1,
    )

    result = _plan(surface, bundle=bundle)

    assert result.decision == adapter.SUPERVISED_DECISION
    assert result.postimage is not None
    assert lookalike.encode("utf-8") in result.postimage


@pytest.mark.parametrize(
    "dependency_state",
    ["missing", "wrong-parser-version", "wrong-transitive-version"],
)
def test_commonmark_dependency_failure_is_manual_zero_write(
    bundle: adapter.FrozenLegacyPromptBundle,
    monkeypatch: pytest.MonkeyPatch,
    dependency_state: str,
) -> None:
    if dependency_state == "missing":
        def missing(_distribution: str) -> str:
            raise adapter.importlib.metadata.PackageNotFoundError("markdown-it-py")

        monkeypatch.setattr(adapter.importlib.metadata, "version", missing)
        reason = "dependency is unavailable"
    elif dependency_state == "wrong-parser-version":
        monkeypatch.setattr(
            adapter.importlib.metadata,
            "version",
            lambda _distribution: "3.0.0",
        )
        reason = "parser version differs"
    else:
        monkeypatch.setattr(
            adapter.importlib.metadata,
            "version",
            lambda distribution: (
                adapter.COMMONMARK_VERSION
                if distribution == adapter.COMMONMARK_DISTRIBUTION
                else "9.9.9"
            ),
        )
        reason = "parser version differs"

    result = _plan(SURFACE_PATH.read_bytes(), bundle=bundle)

    _assert_manual(result, reason)


def test_backtick_in_backtick_fence_info_does_not_hide_visible_duplicate_heading(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    anchor = "把简短的企业描述转化为紧凑、可决策、可验证的 AI 场景诊断。默认使用中文；用户明确使用其他语言时跟随用户语言。\n"
    invalid_fence = """
```markdown`not-a-fence
## EvoZeus-CoEvolve 状态检查
```
"""
    surface = surface.replace(
        anchor.encode("utf-8"),
        (anchor + invalid_fence).encode("utf-8"),
        1,
    )

    result = _plan(surface, bundle=bundle)
    _assert_manual(result, "legacy status heading is missing or ambiguous")


def test_canonical_marker_inside_valid_fence_is_manual_zero_write(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    anchor = "把简短的企业描述转化为紧凑、可决策、可验证的 AI 场景诊断。默认使用中文；用户明确使用其他语言时跟随用户语言。\n"
    fenced_marker = """
```markdown
<!-- evozeus-harness-entry:v1 -->
<!-- /evozeus-harness-entry -->
```
"""
    surface = surface.replace(
        anchor.encode("utf-8"),
        (anchor + fenced_marker).encode("utf-8"),
        1,
    )

    result = _plan(surface, bundle=bundle)
    _assert_manual(result, "canonical Harness marker already coexists")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda value: value.replace(
                "## EvoZeus-CoEvolve 状态检查".encode(),
                "## 状态检查".encode(),
                1,
            ),
            "legacy status heading is missing or ambiguous",
        ),
        (
            lambda value: value.replace(
                "# 企业 AI 场景诊断\n".encode(),
                (
                    "# 企业 AI 场景诊断\n\n"
                    "## EvoZeus-CoEvolve 状态检查\n\n"
                    "业务同名标题。\n"
                ).encode(),
                1,
            ),
            "legacy status heading is missing or ambiguous",
        ),
        (
            lambda value: value.replace(
                "# 企业 AI 场景诊断\n".encode(),
                (
                    "# 企业 AI 场景诊断\n\n"
                    "## EvoZeus-wrapper\n\n"
                    "旧所有权标题。\n"
                ).encode(),
                1,
            ),
            "additional legacy wrapper heading requires manual review",
        ),
        (
            lambda value: value.replace(
                "# 企业 AI 场景诊断\n".encode(),
                (
                    "# 企业 AI 场景诊断\n\n"
                    "本段是 Skill 入口 preflight\n"
                ).encode(),
                1,
            ),
            "legacy status ownership signature is missing or ambiguous",
        ),
        (
            lambda value: value.replace(
                "# 企业 AI 场景诊断\n".encode(),
                (
                    "# 企业 AI 场景诊断\n\n"
                    "<!-- evozeus-harness-entry:v1 -->\n"
                ).encode(),
                1,
            ),
            "canonical Harness marker already coexists",
        ),
        (
            lambda value: value.replace(
                "当前记录版本".encode(),
                "当前记录版次".encode(),
                1,
            ),
            "legacy status section differs from its frozen v0.14 template",
        ),
        (
            lambda value: value + b"\nunknown suffix\n",
            "legacy wrapper section differs from its frozen v0.14 template",
        ),
        (
            lambda value: value.replace(
                b"\n", b"\r\n", 1
            ),
            "instruction surface uses mixed LF and CRLF newlines",
        ),
        (
            lambda value: b"\xef\xbb\xbf" + value,
            "instruction surface has a UTF-8 BOM",
        ),
        (
            lambda value: value[:100] + b"\xff" + value[101:],
            "instruction surface is not strict UTF-8",
        ),
        (
            lambda value: value.replace(b"\n", b"\r", 1),
            "instruction surface contains a lone CR",
        ),
    ],
    ids=[
        "missing-heading",
        "duplicate-visible-heading",
        "forbidden-legacy-heading",
        "duplicate-ownership-signature",
        "canonical-marker-coexistence",
        "one-byte-template-drift",
        "unknown-wrapper-suffix",
        "mixed-newlines",
        "utf8-bom",
        "invalid-utf8",
        "lone-cr",
    ],
)
def test_ambiguous_or_drifted_prompt_shapes_fail_closed_without_postimage(
    bundle: adapter.FrozenLegacyPromptBundle,
    mutate,
    reason: str,
) -> None:
    result = _plan(mutate(SURFACE_PATH.read_bytes()), bundle=bundle)
    _assert_manual(result, reason)


def test_reordered_trailing_sections_fail_closed(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    evolution_start = surface.index("## 自进化方法\n".encode())
    wrapper_start = surface.index(b"## EvoZeus-CoEvolve\n")
    reordered = (
        surface[:evolution_start]
        + surface[wrapper_start:]
        + surface[evolution_start:wrapper_start]
    )
    result = _plan(reordered, bundle=bundle)
    _assert_manual(result, "legacy wrapper sections are not in the reviewed order")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda value: value.replace(
                b'{\n', b'{\n  "unknown": true,\n', 1
            ),
            "legacy manifest differs from its frozen v0.14 projection",
        ),
        (
            lambda value: value.replace(
                b'{\n', b'{\n  "wrapper_repo": "duplicate",\n', 1
            ),
            "target legacy manifest contains duplicate key: wrapper_repo",
        ),
        (
            lambda value: value.replace(b'"layout_version": 2', b'"layout_version": NaN'),
            "target legacy manifest contains invalid JSON constant: NaN",
        ),
        (
            lambda value: value.replace(
                b'  "layout_version": 2,\n',
                b'  "layout_version": 2,\n  "instruction_surface": "SKILL.md",\n',
                1,
            ),
            "legacy manifest contains a field reserved for canonical Harness state",
        ),
        (
            lambda value: value.replace(
                b'"canonical_repo": "MetaInFLow/diagnose-enterprise-ai-scenarios"',
                b'"canonical_repo": "invalid repo"',
                1,
            ),
            "legacy manifest canonical_repo is invalid",
        ),
    ],
    ids=[
        "unknown-field",
        "duplicate-json-key",
        "nonfinite-json",
        "canonical-field-coexistence",
        "invalid-repo-binding",
    ],
)
def test_manifest_projection_drift_fails_closed(
    bundle: adapter.FrozenLegacyPromptBundle,
    mutate,
    reason: str,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    manifest = mutate(_render_manifest())
    result = _plan(surface, bundle=bundle, manifest=manifest)
    _assert_manual(result, reason)


@pytest.mark.parametrize(
    ("repo", "skill_name"),
    [
        ("./skill", "skill"),
        ("../skill", "skill"),
        ("owner/.", "."),
        ("owner/..", ".."),
    ],
)
def test_manifest_rejects_dot_repository_components(
    bundle: adapter.FrozenLegacyPromptBundle,
    repo: str,
    skill_name: str,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    manifest = _render_manifest(repo=repo, skill_name=skill_name)
    result = _plan(surface, bundle=bundle, manifest=manifest)
    _assert_manual(result, "legacy manifest canonical_repo is invalid")


@pytest.mark.parametrize(
    ("path", "state", "reason"),
    [
        (
            ".codex/hooks.json",
            {"kind": "absent"},
            "legacy exact managed file differs: .codex/hooks.json",
        ),
        (
            ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py",
            {"kind": "file", "sha256": "0" * 64, "mode": "100755"},
            "legacy exact managed file differs: .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py",
        ),
        (
            ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md",
            {"kind": "file", "sha256": "1" * 64, "mode": "100644"},
            "legacy required-absent path is present: .evozeus-wrapper/skills/using-evozeus-harness/SKILL.md",
        ),
        (
            ".evozeus-wrapper/docs/index.md",
            {"kind": "file", "sha256": "2" * 64, "mode": "100755"},
            "legacy rendered surface is missing or unsafe: .evozeus-wrapper/docs/index.md",
        ),
    ],
    ids=[
        "missing-exact-host-entrypoint",
        "changed-exact-preflight",
        "required-absent-present",
        "rendered-preserve-mode-drift",
    ],
)
def test_full_file_envelope_mismatch_fails_closed(
    bundle: adapter.FrozenLegacyPromptBundle,
    path: str,
    state: dict[str, str],
    reason: str,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    manifest = _render_manifest()
    states = _file_states(surface, manifest, bundle=bundle)
    states[path] = state
    result = _plan(surface, bundle=bundle, manifest=manifest, states=states)
    _assert_manual(result, reason)


def test_supplied_bytes_must_match_full_file_facts(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes()
    manifest = _render_manifest()
    states = _file_states(surface, manifest, bundle=bundle)

    states["SKILL.md"]["sha256"] = "3" * 64
    result = _plan(surface, bundle=bundle, manifest=manifest, states=states)
    _assert_manual(result, "instruction surface file fact differs from its supplied bytes")

    states = _file_states(surface, manifest, bundle=bundle)
    states[".evozeus-wrapper/wrapper.json"]["sha256"] = "4" * 64
    result = _plan(surface, bundle=bundle, manifest=manifest, states=states)
    _assert_manual(result, "legacy manifest file fact differs from its supplied bytes")


def test_frozen_bundle_rejects_template_drift_before_target_analysis(
    tmp_path: Path,
) -> None:
    (tmp_path / "scripts").mkdir()
    shutil.copy2(
        ROOT / "scripts/evozeus_harness_legacy_prompt_adapter.py",
        tmp_path / "scripts/evozeus_harness_legacy_prompt_adapter.py",
    )
    shutil.copytree(
        ROOT / "contracts/v1/migrations",
        tmp_path / "contracts/v1/migrations",
    )
    shutil.copy2(
        ROOT / "requirements-commonmark.lock",
        tmp_path / "requirements-commonmark.lock",
    )
    template = (
        tmp_path
        / "contracts/v1/migrations/adapters/legacy-v0.14-three-section/status.md.tpl"
    )
    template.write_bytes(template.read_bytes() + b"drift\n")

    with pytest.raises(adapter.FrozenBundleError, match="adapter template digest mismatch"):
        adapter.load_frozen_bundle(tmp_path)


def test_manual_proof_is_deterministic_and_zero_write(
    bundle: adapter.FrozenLegacyPromptBundle,
) -> None:
    surface = SURFACE_PATH.read_bytes().replace(
        "## 自进化方法".encode(),
        "## 未知方法".encode(),
        1,
    )
    first = _plan(surface, bundle=bundle)
    second = _plan(surface, bundle=bundle)

    assert first == second
    _assert_manual(first, "legacy evolution heading is missing or ambiguous")
    proof_without_digest = dict(first.proof)
    proof_without_digest.pop("proof_sha256")
    assert first.proof["proof_sha256"] == (
        "sha256:" + adapter.canonical_json_sha256(proof_without_digest)
    )


def test_published_schemas_validate_the_frozen_documents() -> None:
    pairs = (
        (
            ROOT / "contracts/v1/migrations/schemas/legacy-source-envelope-v1.schema.json",
            ROOT / "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/envelope.json",
        ),
        (
            ROOT / "contracts/v1/migrations/schemas/legacy-prompt-adapter-v1.schema.json",
            ROOT / "contracts/v1/migrations/adapters/legacy-v0.14-three-section/adapter-v1.json",
        ),
        (
            ROOT / "contracts/v1/migrations/schemas/supervised-legacy-profile-v1.schema.json",
            ROOT
            / "contracts/v1/migrations/profiles/legacy-v0.14-three-section-to-canonical-v1.1-v1.json",
        ),
    )
    for schema_path, document_path in pairs:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        document = json.loads(document_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(document)


def test_supervised_profile_schema_requires_frozen_preflight_artifact_binding() -> None:
    schema = json.loads(
        (
            ROOT
            / "contracts/v1/migrations/schemas/supervised-legacy-profile-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    profile = json.loads(
        (
            ROOT
            / "contracts/v1/migrations/profiles/legacy-v0.14-three-section-to-canonical-v1.1-v1.json"
        ).read_text(encoding="utf-8")
    )
    operation = next(
        item
        for item in profile["operations"]
        if item["target_path"]
        == ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py"
    )
    operation["preimage"].pop("artifact")

    errors = list(Draft202012Validator(schema).iter_errors(profile))
    assert errors
    assert any("not valid under any of the given schemas" in error.message for error in errors)


@pytest.mark.parametrize(
    ("document_name", "mutate"),
    [
        (
            "envelope",
            lambda value: {key: item for key, item in value.items() if key != "files"},
        ),
        (
            "envelope",
            lambda value: {**value, "unknown": True},
        ),
        (
            "envelope",
            lambda value: {**value, "envelope_version": 1},
        ),
        (
            "adapter",
            lambda value: {**value, "adapter_version": "1.0.0"},
        ),
        (
            "adapter",
            lambda value: {
                **value,
                "templates": [value["templates"][0], value["templates"][0], value["templates"][2]],
            },
        ),
        (
            "adapter",
            lambda value: {
                **value,
                "implementation": {**value["implementation"], "unknown": True},
            },
        ),
    ],
    ids=[
        "missing-required-envelope-field",
        "unknown-envelope-field",
        "non-string-envelope-version",
        "non-semver-adapter-version",
        "duplicate-template-kind",
        "unknown-implementation-field",
    ],
)
def test_published_schemas_reject_contract_drift(document_name: str, mutate) -> None:
    if document_name == "envelope":
        schema_path = ROOT / "contracts/v1/migrations/schemas/legacy-source-envelope-v1.schema.json"
        document_path = ROOT / "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/envelope.json"
    else:
        schema_path = ROOT / "contracts/v1/migrations/schemas/legacy-prompt-adapter-v1.schema.json"
        document_path = ROOT / "contracts/v1/migrations/adapters/legacy-v0.14-three-section/adapter-v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    document = json.loads(document_path.read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(mutate(document)))
