#!/usr/bin/env python3
"""Pure, fail-closed adapter for the frozen v0.14 three-section Prompt shape.

This module never writes a target repository.  It consumes already-collected
file facts plus exact manifest/instruction bytes and returns either a
deterministic supervised-transform proof or a manual-review decision.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


ADAPTER_DOCUMENT_REL = (
    "contracts/v1/migrations/adapters/legacy-v0.14-three-section/adapter-v1.json"
)
SOURCE_ENVELOPE_REL = (
    "contracts/v1/migrations/history/legacy-wrapper/v0.14.0/envelope.json"
)
IMPLEMENTATION_REL = "scripts/evozeus_harness_legacy_prompt_adapter.py"
PROOF_SCHEMA = "evozeus.coevolve.legacy-prompt-transform-proof.v1"
MANUAL_DECISION = "manual_migration_required"
SUPERVISED_DECISION = "supervised_migration_available"
CANONICAL_BEGIN = "<!-- evozeus-harness-entry:v1 -->"
CANONICAL_END = "<!-- /evozeus-harness-entry -->"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SEMVER_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SKILL_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class FrozenBundleError(ValueError):
    """The reviewed adapter/envelope bundle is malformed or has drifted."""


class TargetMismatch(ValueError):
    """The target does not match the narrow reviewed legacy envelope."""


@dataclass(frozen=True)
class FrozenLegacyPromptBundle:
    repository_root: Path
    adapter: dict[str, Any]
    adapter_sha256: str
    envelope: dict[str, Any]
    envelope_sha256: str
    templates: dict[str, bytes]

    @property
    def adapter_identity(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter["adapter_id"],
            "adapter_version": self.adapter["adapter_version"],
            "path": ADAPTER_DOCUMENT_REL,
            "sha256": self.adapter_sha256,
            "implementation": copy.deepcopy(self.adapter["implementation"]),
            "template_bindings": copy.deepcopy(self.adapter["templates"]),
        }

    @property
    def envelope_identity(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope["envelope_id"],
            "envelope_version": self.envelope["envelope_version"],
            "path": SOURCE_ENVELOPE_REL,
            "sha256": self.envelope_sha256,
            "source_evidence": copy.deepcopy(self.envelope["source_evidence"]),
        }


@dataclass(frozen=True)
class LegacyPromptTransformResult:
    decision: str
    proof: dict[str, Any]
    postimage: bytes | None


@dataclass(frozen=True)
class _Heading:
    level: int
    label: str
    start: int
    end: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_uri(data: bytes) -> str:
    return "sha256:" + _sha256(data)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def _reject_nonfinite(value: object, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise FrozenBundleError(f"{label} contains a non-finite number")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite(child, label)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child, label)


def _strict_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FrozenBundleError(f"{label} is not strict UTF-8: {exc}") from exc

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise FrozenBundleError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise FrozenBundleError(f"{label} contains invalid JSON constant: {value}")

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, FrozenBundleError) as exc:
        if isinstance(exc, FrozenBundleError):
            raise
        raise FrozenBundleError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise FrozenBundleError(f"{label} must be a JSON object")
    _reject_nonfinite(value, label)
    return value


def _safe_source_file(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise FrozenBundleError(f"{label} must be a POSIX relative path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise FrozenBundleError(f"{label} escapes the reviewed source root: {raw}")
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise FrozenBundleError(f"{label} is missing or unsafe: {raw}")
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FrozenBundleError(f"{label} escapes the reviewed source root: {raw}") from exc
    return candidate


def _plain_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise FrozenBundleError(f"{label} must be a lowercase SHA-256")
    return value


def load_frozen_bundle(
    repository_root: Path | None = None,
) -> FrozenLegacyPromptBundle:
    root = (
        Path(__file__).resolve().parents[1]
        if repository_root is None
        else repository_root.expanduser().resolve()
    )
    adapter_path = _safe_source_file(root, ADAPTER_DOCUMENT_REL, "adapter document")
    envelope_path = _safe_source_file(root, SOURCE_ENVELOPE_REL, "source envelope")
    adapter_bytes = adapter_path.read_bytes()
    envelope_bytes = envelope_path.read_bytes()
    adapter = _strict_json_bytes(adapter_bytes, "adapter document")
    envelope = _strict_json_bytes(envelope_bytes, "source envelope")
    if adapter.get("schema_version") != "evozeus.coevolve.legacy-prompt-adapter.v1":
        raise FrozenBundleError("unsupported legacy Prompt adapter schema")
    if envelope.get("schema_version") != "evozeus.coevolve.legacy-source-envelope.v1":
        raise FrozenBundleError("unsupported legacy source envelope schema")
    envelope_binding = adapter.get("source_envelope")
    if not isinstance(envelope_binding, dict):
        raise FrozenBundleError("adapter source envelope binding is missing")
    if envelope_binding.get("path") != SOURCE_ENVELOPE_REL:
        raise FrozenBundleError("adapter source envelope path is not canonical")
    if _plain_sha256(envelope_binding.get("sha256"), "source envelope binding") != _sha256(
        envelope_bytes
    ):
        raise FrozenBundleError("adapter source envelope digest mismatch")
    implementation = adapter.get("implementation")
    if not isinstance(implementation, dict) or implementation.get("path") != IMPLEMENTATION_REL:
        raise FrozenBundleError("adapter implementation path is not canonical")
    implementation_path = _safe_source_file(
        root,
        implementation["path"],
        "adapter implementation",
    )
    if _plain_sha256(
        implementation.get("sha256"),
        "adapter implementation binding",
    ) != _sha256(implementation_path.read_bytes()):
        raise FrozenBundleError("adapter implementation digest mismatch")
    if implementation.get("entrypoint") != "plan_supervised_legacy_prompt_transform":
        raise FrozenBundleError("adapter implementation entrypoint is invalid")

    templates_raw = adapter.get("templates")
    if not isinstance(templates_raw, list) or [
        item.get("kind") if isinstance(item, dict) else None for item in templates_raw
    ] != ["status", "evolution", "wrapper"]:
        raise FrozenBundleError("adapter template identities are incomplete or unordered")
    templates: dict[str, bytes] = {}
    for item in templates_raw:
        path = _safe_source_file(root, item.get("path"), "adapter template")
        data = path.read_bytes()
        if b"\r" in data or not data.endswith(b"\n"):
            raise FrozenBundleError("adapter templates must use LF and end with a newline")
        if _plain_sha256(item.get("sha256"), "adapter template binding") != _sha256(data):
            raise FrozenBundleError(f"adapter template digest mismatch: {item.get('kind')}")
        templates[str(item["kind"])] = data

    manifest_projection = envelope.get("manifest_projection")
    if not isinstance(manifest_projection, dict):
        raise FrozenBundleError("legacy source envelope manifest projection is missing")
    manifest_template = _safe_source_file(
        root,
        manifest_projection.get("template_path"),
        "legacy manifest template",
    )
    if _plain_sha256(
        manifest_projection.get("template_sha256"),
        "legacy manifest template binding",
    ) != _sha256(manifest_template.read_bytes()):
        raise FrozenBundleError("legacy manifest template digest mismatch")
    return FrozenLegacyPromptBundle(
        repository_root=root,
        adapter=adapter,
        adapter_sha256=_sha256(adapter_bytes),
        envelope=envelope,
        envelope_sha256=_sha256(envelope_bytes),
        templates=templates,
    )


def _newline_style(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise TargetMismatch("instruction surface has a UTF-8 BOM")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TargetMismatch(f"instruction surface is not strict UTF-8: {exc}") from exc
    if "\r" in text.replace("\r\n", ""):
        raise TargetMismatch("instruction surface contains a lone CR")
    crlf_count = text.count("\r\n")
    lf_count = text.count("\n")
    if crlf_count and lf_count != crlf_count:
        raise TargetMismatch("instruction surface uses mixed LF and CRLF newlines")
    if lf_count == 0:
        raise TargetMismatch("instruction surface has no newline contract")
    return text, "crlf" if crlf_count else "lf"


def _frontmatter_end(text: str) -> int:
    records = list(_line_records(text))
    if not records or records[0][2] != "---":
        raise TargetMismatch("instruction surface lacks exact YAML frontmatter")
    for _, end, content, _ in records[1:]:
        if content == "---":
            return end
    raise TargetMismatch("instruction surface frontmatter is unterminated")


def _line_records(text: str) -> list[tuple[int, int, str, str]]:
    records: list[tuple[int, int, str, str]] = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        if raw.endswith("\r\n"):
            content, ending = raw[:-2], "\r\n"
        elif raw.endswith("\n"):
            content, ending = raw[:-1], "\n"
        else:
            content, ending = raw, ""
        records.append((offset, offset + len(raw), content, ending))
        offset += len(raw)
    if offset < len(text):
        records.append((offset, len(text), text[offset:], ""))
    return records


def _visible_headings(text: str) -> tuple[list[_Heading], str]:
    headings: list[_Heading] = []
    visible_parts: list[str] = []
    fence: tuple[str, int] | None = None
    for start, end, content, ending in _line_records(text):
        stripped = content.lstrip(" ")
        indent = len(content) - len(stripped)
        fence_match = re.match(r"(`{3,}|~{3,})(.*)$", stripped) if indent <= 3 else None
        if fence is not None:
            char, minimum = fence
            if re.fullmatch(rf"{re.escape(char)}{{{minimum},}}[ \t]*", stripped):
                fence = None
            visible_parts.append(" " * len(content) + ending)
            continue
        if fence_match:
            run = fence_match.group(1)
            info = fence_match.group(2)
            # CommonMark does not recognize a backtick fence when its info
            # string itself contains a backtick.  Treating it as a fence would
            # hide real headings and turn an ambiguous target into authority.
            if run[0] != "`" or "`" not in info:
                fence = (run[0], len(run))
                visible_parts.append(" " * len(content) + ending)
                continue
        visible_parts.append(content + ending)
        heading_match = re.match(r"^[ ]{0,3}(#{1,6})(?:[ \t]+(.*?))[ \t]*$", content)
        if not heading_match:
            continue
        label = heading_match.group(2).strip()
        label = re.sub(r"[ \t]+#+[ \t]*$", "", label).strip()
        headings.append(
            _Heading(
                level=len(heading_match.group(1)),
                label=label,
                start=start,
                end=end,
            )
        )
    return headings, "".join(visible_parts)


def _section_span(headings: list[_Heading], selected: _Heading, text_length: int) -> tuple[int, int]:
    end = text_length
    selected_index = headings.index(selected)
    for candidate in headings[selected_index + 1 :]:
        if candidate.level <= selected.level:
            end = candidate.start
            break
    return selected.start, end


def _byte_offset(text: str, character_offset: int) -> int:
    return len(text[:character_offset].encode("utf-8"))


def _render_template(
    template: bytes,
    replacements: Mapping[str, str],
    allowed: list[str],
    *,
    newline: str = "\n",
) -> bytes:
    try:
        text = template.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FrozenBundleError(f"frozen template is not UTF-8: {exc}") from exc
    placeholders = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", text))
    if placeholders != set(allowed):
        raise FrozenBundleError(
            "frozen template placeholder set changed: " + ", ".join(sorted(placeholders))
        )
    for key in allowed:
        value = replacements.get(key)
        if not isinstance(value, str):
            raise TargetMismatch(f"template value is missing: {key}")
        if "{{" in value or "}}" in value or "\r" in value or "\n" in value:
            raise TargetMismatch(f"template value is unsafe: {key}")
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text or "}}" in text:
        raise FrozenBundleError("frozen template contains an unbound placeholder")
    if newline == "\r\n":
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def _manifest_projection(
    manifest_bytes: bytes,
    file_states: Mapping[str, object],
    bundle: FrozenLegacyPromptBundle,
) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        manifest = _strict_json_bytes(manifest_bytes, "target legacy manifest")
    except FrozenBundleError as exc:
        raise TargetMismatch(str(exc)) from exc
    repo = manifest.get("canonical_repo")
    applied_at = manifest.get("applied_at")
    if not isinstance(repo, str) or REPO_RE.fullmatch(repo) is None:
        raise TargetMismatch("legacy manifest canonical_repo is invalid")
    if any(component in {".", ".."} for component in repo.split("/")):
        raise TargetMismatch("legacy manifest canonical_repo is invalid")
    if not isinstance(applied_at, str) or DATE_RE.fullmatch(applied_at) is None:
        raise TargetMismatch("legacy manifest applied_at is invalid")
    installation = (
        manifest.get("onboarding", {}).get("installation", {})
        if isinstance(manifest.get("onboarding"), dict)
        else {}
    )
    command = installation.get("command") if isinstance(installation, dict) else None
    match = (
        re.fullmatch(
            r"python3 scripts/evozeus_wrapper\.py publish reinstall --skill-name "
            r"([A-Za-z0-9_.-]+) --canonical-path <canonical-repo-path> --target codex --json",
            command,
        )
        if isinstance(command, str)
        else None
    )
    if match is None:
        raise TargetMismatch("legacy manifest Skill name binding is invalid")
    skill_name = match.group(1)
    if (
        SKILL_NAME_RE.fullmatch(skill_name) is None
        or skill_name in {".", ".."}
        or repo.rsplit("/", 1)[1] != skill_name
    ):
        raise TargetMismatch("legacy manifest Skill name differs from canonical_repo")
    projection = bundle.envelope["manifest_projection"]
    required_absent = projection.get("required_absent_fields")
    if not isinstance(required_absent, list) or any(field in manifest for field in required_absent):
        raise TargetMismatch("legacy manifest contains a field reserved for canonical Harness state")
    template_path = _safe_source_file(
        bundle.repository_root,
        projection["template_path"],
        "legacy manifest template",
    )
    replacements = {
        "APPLIED_AT": applied_at,
        "REPO_NAME": repo,
        "SKILL_NAME": skill_name,
    }
    rendered = _render_template(
        template_path.read_bytes(),
        replacements,
        projection["template_placeholders"],
    )
    if rendered != manifest_bytes:
        raise TargetMismatch("legacy manifest differs from its frozen v0.14 projection")
    state = _normalized_file_state(
        file_states.get(projection["path"]),
        projection["path"],
    )
    if (
        state.get("kind") != "file"
        or state.get("sha256") != _sha256(manifest_bytes)
        or state.get("mode") != projection.get("mode")
    ):
        raise TargetMismatch("legacy manifest file fact differs from its supplied bytes")
    return manifest, replacements


def _normalized_file_state(raw: object, path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TargetMismatch(f"target file fact is missing: {path}")
    kind = raw.get("kind")
    if kind == "absent":
        return {"kind": "absent"}
    if kind != "file":
        raise TargetMismatch(f"target file fact has an unsupported kind: {path}")
    sha256 = raw.get("sha256")
    mode = raw.get("mode")
    if isinstance(sha256, str) and sha256.startswith("sha256:"):
        sha256 = sha256.removeprefix("sha256:")
    if not isinstance(sha256, str) or SHA256_RE.fullmatch(sha256) is None:
        raise TargetMismatch(f"target file fact has an invalid digest: {path}")
    if mode not in {"100644", "100755"}:
        raise TargetMismatch(f"target file fact has an invalid mode: {path}")
    return {"kind": "file", "sha256": sha256, "mode": mode}


def _verify_envelope_files(
    file_states: Mapping[str, object],
    envelope: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    entries = envelope.get("files")
    if not isinstance(entries, list):
        raise FrozenBundleError("legacy source envelope files are missing")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise FrozenBundleError("legacy source envelope file entry is invalid")
        path = entry["path"]
        if path in seen:
            raise FrozenBundleError(f"legacy source envelope repeats a path: {path}")
        seen.add(path)
        state = _normalized_file_state(file_states.get(path), path)
        kind = entry.get("kind")
        if kind == "exact":
            if (
                state.get("kind") != "file"
                or state.get("sha256") != entry.get("sha256")
                or state.get("mode") != entry.get("mode")
            ):
                raise TargetMismatch(f"legacy exact managed file differs: {path}")
        elif kind == "absent":
            if state != {"kind": "absent"}:
                raise TargetMismatch(f"legacy required-absent path is present: {path}")
        elif kind == "rendered_preserve":
            if state.get("kind") != "file" or state.get("mode") != entry.get("mode"):
                raise TargetMismatch(f"legacy rendered surface is missing or unsafe: {path}")
            preserved.append(
                {
                    "path": path,
                    "rule": "byte_exact_no_write",
                    "preimage_sha256": "sha256:" + state["sha256"],
                    "preimage_mode": state["mode"],
                }
            )
        else:
            raise FrozenBundleError(f"legacy source envelope file kind is invalid: {kind}")
        evidence.append({"path": path, "expected": copy.deepcopy(entry), "actual": state})
    return evidence, preserved


def _legacy_sections(
    surface_bytes: bytes,
    manifest_values: Mapping[str, str],
    bundle: FrozenLegacyPromptBundle,
) -> tuple[dict[str, Any], bytes]:
    text, newline_style = _newline_style(surface_bytes)
    newline = "\r\n" if newline_style == "crlf" else "\n"
    frontmatter_end = _frontmatter_end(text)
    headings, visible = _visible_headings(text)
    expected_labels = bundle.adapter.get("expected_headings")
    if expected_labels != {
        "status": "EvoZeus-CoEvolve 状态检查",
        "evolution": "自进化方法",
        "wrapper": "EvoZeus-CoEvolve",
    }:
        raise FrozenBundleError("adapter expected heading contract changed")
    selected: dict[str, _Heading] = {}
    for kind in ("status", "evolution", "wrapper"):
        matches = [item for item in headings if item.level == 2 and item.label == expected_labels[kind]]
        if len(matches) != 1:
            raise TargetMismatch(f"legacy {kind} heading is missing or ambiguous")
        selected[kind] = matches[0]
    if not (
        selected["status"].start
        < selected["evolution"].start
        < selected["wrapper"].start
    ):
        raise TargetMismatch("legacy wrapper sections are not in the reviewed order")
    forbidden_labels = set(bundle.adapter.get("forbidden_legacy_headings") or [])
    if any(item.label in forbidden_labels for item in headings):
        raise TargetMismatch("additional legacy wrapper heading requires manual review")
    for phrase, kind in (
        ("本段是 Skill 入口 preflight", "status"),
        ("本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环", "evolution"),
        ("本区由 EvoZeus-CoEvolve 追加", "wrapper"),
    ):
        if visible.count(phrase) != 1:
            raise TargetMismatch(f"legacy {kind} ownership signature is missing or ambiguous")
    if CANONICAL_BEGIN in text or CANONICAL_END in text:
        raise TargetMismatch("canonical Harness marker already coexists with legacy sections")

    spans = {
        kind: _section_span(headings, heading, len(text))
        for kind, heading in selected.items()
    }
    if text[frontmatter_end : spans["status"][0]].strip():
        raise TargetMismatch("legacy status section is not the first instruction after frontmatter")
    if spans["evolution"][1] != spans["wrapper"][0] or spans["wrapper"][1] != len(text):
        raise TargetMismatch("legacy trailing wrapper sections have an unknown interleave or suffix")
    business_start = spans["status"][1]
    business_end = spans["evolution"][0]
    business = text[business_start:business_end]
    if not business.strip() or not any(
        item.level == 1 and business_start <= item.start < business_end for item in headings
    ):
        raise TargetMismatch("legacy instruction surface lacks a non-empty target business region")

    evolution_text = text[spans["evolution"][0] : spans["evolution"][1]]
    version_matches = re.findall(r"(?m)^Current Skill version: `(v[0-9]+\.[0-9]+\.[0-9]+)`\r?$", evolution_text)
    visibility_matches = re.findall(r"(?m)^Visibility: `(public|private)`\r?$", evolution_text)
    if len(version_matches) != 1 or len(visibility_matches) != 1:
        raise TargetMismatch("legacy evolution section has invalid target variables")
    replacements = {
        "REPO_NAME": manifest_values["REPO_NAME"],
        "WRAPPER_VERSION": "v0.14.0",
        "CURRENT_VERSION": version_matches[0],
        "VISIBILITY": visibility_matches[0],
    }
    if not SEMVER_RE.fullmatch(replacements["CURRENT_VERSION"]):
        raise TargetMismatch("legacy Skill version is invalid")
    if manifest_values["REPO_NAME"] not in evolution_text:
        raise TargetMismatch("legacy evolution section does not bind canonical_repo")

    template_by_kind = {
        item["kind"]: item for item in bundle.adapter["templates"]
    }
    deleted: list[dict[str, Any]] = []
    byte_spans: list[tuple[int, int]] = []
    for kind in ("status", "evolution", "wrapper"):
        char_start, char_end = spans[kind]
        byte_start = _byte_offset(text, char_start)
        byte_end = _byte_offset(text, char_end)
        actual = surface_bytes[byte_start:byte_end]
        binding = template_by_kind[kind]
        expected = _render_template(
            bundle.templates[kind],
            replacements,
            binding["placeholders"],
            newline=newline,
        )
        if actual != expected:
            raise TargetMismatch(f"legacy {kind} section differs from its frozen v0.14 template")
        byte_spans.append((byte_start, byte_end))
        deleted.append(
            {
                "kind": kind,
                "start_byte": byte_start,
                "end_byte": byte_end,
                "byte_length": byte_end - byte_start,
                "sha256": _sha256_uri(actual),
                "template_path": binding["path"],
                "template_sha256": binding["sha256"],
            }
        )

    retained_segments: list[dict[str, Any]] = []
    retained_parts: list[bytes] = []
    cursor = 0
    for start, end in byte_spans:
        part = surface_bytes[cursor:start]
        retained_parts.append(part)
        retained_segments.append(
            {
                "start_byte": cursor,
                "end_byte": start,
                "byte_length": len(part),
                "sha256": _sha256_uri(part),
            }
        )
        cursor = end
    tail = surface_bytes[cursor:]
    retained_parts.append(tail)
    retained_segments.append(
        {
            "start_byte": cursor,
            "end_byte": len(surface_bytes),
            "byte_length": len(tail),
            "sha256": _sha256_uri(tail),
        }
    )
    retained = b"".join(retained_parts)
    insertion_offset = byte_spans[0][0]
    prefix = retained[:insertion_offset]
    suffix = retained[insertion_offset:]
    separator = newline.encode("ascii") * 2
    if not prefix.endswith(separator):
        raise TargetMismatch("legacy status prefix lacks the reviewed blank-line boundary")
    activation_lf = bundle.adapter.get("canonical_activation_block_lf")
    if not isinstance(activation_lf, str):
        raise FrozenBundleError("adapter canonical activation block is missing")
    activation = activation_lf.replace("\n", newline).encode("utf-8")
    expected_activation_sha = bundle.adapter.get("canonical_activation_sha256_lf")
    if _sha256(activation_lf.encode("utf-8")) != expected_activation_sha:
        raise FrozenBundleError("adapter canonical activation block digest mismatch")
    inserted = activation + separator
    postimage = prefix + inserted + suffix
    projected = postimage[:insertion_offset] + postimage[insertion_offset + len(inserted) :]
    if projected != retained:
        raise FrozenBundleError("adapter retained-byte projection is inconsistent")
    post_text = postimage.decode("utf-8", errors="strict")
    if post_text.count(CANONICAL_BEGIN) != 1 or post_text.count(CANONICAL_END) != 1:
        raise FrozenBundleError("adapter postimage does not contain one canonical marker block")
    post_headings, _ = _visible_headings(post_text)
    legacy_post_labels = {
        "EvoZeus-CoEvolve 状态检查",
        "自进化方法",
        "EvoZeus-CoEvolve",
    }
    if any(
        item.level == 2 and item.label in legacy_post_labels
        for item in post_headings
    ):
        raise FrozenBundleError("adapter postimage retains a legacy wrapper heading")

    pre_lf = text.replace("\r\n", "\n")
    post_lf = post_text.replace("\r\n", "\n")
    diff_text = "".join(
        difflib.unified_diff(
            pre_lf.splitlines(keepends=True),
            post_lf.splitlines(keepends=True),
            fromfile="a/SKILL.md",
            tofile="b/SKILL.md",
            n=0,
            lineterm="\n",
        )
    )
    for line in diff_text.splitlines()[2:]:
        if line.startswith("@@") or line.startswith(("+", "-")):
            continue
        raise FrozenBundleError("adapter diff unexpectedly contains context lines")
    proof = {
        "path": "SKILL.md",
        "encoding": "utf-8",
        "newline_style": newline_style,
        "preimage_sha256": _sha256_uri(surface_bytes),
        "preimage_byte_length": len(surface_bytes),
        "preimage_mode": "100644",
        "postimage_sha256": _sha256_uri(postimage),
        "postimage_byte_length": len(postimage),
        "postimage_mode": "100644",
        "deleted_spans": deleted,
        "retained_target_bytes": {
            "segments": retained_segments,
            "concatenated_sha256": _sha256_uri(retained),
            "byte_length": len(retained),
            "postimage_projection_sha256": _sha256_uri(projected),
            "postimage_projection_byte_length": len(projected),
        },
        "business_region": {
            "preimage_start_byte": _byte_offset(text, business_start),
            "preimage_end_byte": _byte_offset(text, business_end),
            "sha256": _sha256_uri(business.encode("utf-8")),
            "byte_length": len(business.encode("utf-8")),
        },
        "inserted_envelope": {
            "start_byte": insertion_offset,
            "end_byte": insertion_offset + len(inserted),
            "byte_length": len(inserted),
            "sha256": _sha256_uri(inserted),
            "activation_sha256": _sha256_uri(activation),
            "activation_sha256_lf": "sha256:" + expected_activation_sha,
        },
        "zero_context_diff": {
            "format": "unified-diff-v1",
            "context_lines": 0,
            "normalization": "lf-display-only",
            "sha256": _sha256_uri(diff_text.encode("utf-8")),
            "text": diff_text,
        },
        "variables": replacements,
        "postconditions": {
            "canonical_marker_count": 1,
            "legacy_owned_section_count": 0,
            "retained_target_bytes": "byte_exact",
        },
    }
    return proof, postimage


def _manual_result(
    bundle: FrozenLegacyPromptBundle,
    reason: str,
) -> LegacyPromptTransformResult:
    proof: dict[str, Any] = {
        "schema_version": PROOF_SCHEMA,
        "decision": MANUAL_DECISION,
        "writes": False,
        "destructive_authority": False,
        "adapter": bundle.adapter_identity,
        "source_envelope": bundle.envelope_identity,
        "reasons": [reason],
    }
    proof["proof_sha256"] = "sha256:" + canonical_json_sha256(proof)
    return LegacyPromptTransformResult(MANUAL_DECISION, proof, None)


def plan_supervised_legacy_prompt_transform(
    *,
    instruction_surface_bytes: bytes,
    manifest_bytes: bytes,
    file_states: Mapping[str, object],
    bundle: FrozenLegacyPromptBundle | None = None,
    repository_root: Path | None = None,
) -> LegacyPromptTransformResult:
    """Build a deterministic proof and postimage without mutating any filesystem."""
    frozen = bundle or load_frozen_bundle(repository_root)
    before_surface = bytes(instruction_surface_bytes)
    before_manifest = bytes(manifest_bytes)
    before_states = copy.deepcopy(dict(file_states))
    try:
        file_evidence, preserved = _verify_envelope_files(file_states, frozen.envelope)
        manifest, manifest_values = _manifest_projection(
            manifest_bytes,
            file_states,
            frozen,
        )
        surface_state = _normalized_file_state(file_states.get("SKILL.md"), "SKILL.md")
        if (
            surface_state.get("kind") != "file"
            or surface_state.get("mode") != "100644"
            or surface_state.get("sha256") != _sha256(instruction_surface_bytes)
        ):
            raise TargetMismatch("instruction surface file fact differs from its supplied bytes")
        transform, postimage = _legacy_sections(
            instruction_surface_bytes,
            manifest_values,
            frozen,
        )
        proof: dict[str, Any] = {
            "schema_version": PROOF_SCHEMA,
            "decision": SUPERVISED_DECISION,
            "writes": False,
            "destructive_authority": False,
            "authority_requirements": {
                "trusted_release": True,
                "clean_independent_git_root": True,
                "approve_plan_exact_digest": True,
                "full_file_compare_and_swap": True,
                "snapshot_before_write": True,
                "rollback_on_any_failure": True,
            },
            "adapter": frozen.adapter_identity,
            "source_envelope": frozen.envelope_identity,
            "manifest": {
                "path": frozen.envelope["manifest_projection"]["path"],
                "sha256": _sha256_uri(manifest_bytes),
                "byte_length": len(manifest_bytes),
                "mode": frozen.envelope["manifest_projection"]["mode"],
                "canonical_repo": manifest["canonical_repo"],
                "wrapper_version": manifest["wrapper_version"],
                "projection": "frozen-template-byte-exact",
            },
            "envelope_file_evidence": file_evidence,
            "protected_no_write_surfaces": preserved,
            "instruction_surface_transform": transform,
            "write_operation": {
                "path": "SKILL.md",
                "operation": "replace_exact",
                "preimage_sha256": transform["preimage_sha256"],
                "preimage_mode": transform["preimage_mode"],
                "postimage_sha256": transform["postimage_sha256"],
                "postimage_mode": transform["postimage_mode"],
                "authority": (
                    frozen.adapter["adapter_id"] + "@" + frozen.adapter["adapter_version"]
                ),
            },
        }
        proof["proof_sha256"] = "sha256:" + canonical_json_sha256(proof)
        if (
            instruction_surface_bytes != before_surface
            or manifest_bytes != before_manifest
            or dict(file_states) != before_states
        ):
            raise FrozenBundleError("pure adapter mutated its supplied input")
        return LegacyPromptTransformResult(SUPERVISED_DECISION, proof, postimage)
    except TargetMismatch as exc:
        return _manual_result(frozen, str(exc))


__all__ = [
    "FrozenBundleError",
    "FrozenLegacyPromptBundle",
    "LegacyPromptTransformResult",
    "MANUAL_DECISION",
    "SUPERVISED_DECISION",
    "canonical_json_sha256",
    "load_frozen_bundle",
    "plan_supervised_legacy_prompt_transform",
]
