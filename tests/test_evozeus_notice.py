import copy
import hashlib
import importlib._bootstrap_external
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from scripts.evozeus_notice import DEFAULT_NOTICE_POLICY, load_notice_policy, render_notice


ROOT = Path(__file__).resolve().parents[1]
NOTICE_SCRIPT = ROOT / "scripts" / "evozeus_notice.py"


def test_lesson_pending_renders_compact_evozeus_tag_and_record_only_action() -> None:
    notice = render_notice(
        kind="lesson",
        state="pending",
        message="项目巡检必须校验未关闭任务负责人和实时在职状态。",
        action="是否记录到 Skill Feedback Issue？仅记录，不启动修复。",
        signal_id="sig_DEADBEEF",
        policy=DEFAULT_NOTICE_POLICY,
    )

    assert notice == {
        "schema_version": "v1",
        "kind": "lesson",
        "state": "pending",
        "icon": "💡",
        "tag": "EvoZeus · Lesson",
        "state_label": "待记录",
        "message": "项目巡检必须校验未关闭任务负责人和实时在职状态。",
        "action": "是否记录到 Skill Feedback Issue？仅记录，不启动修复。",
        "signal_id": "sig_DEADBEEF",
        "display_text": (
            "💡 `EvoZeus · Lesson` 待记录\n\n"
            "项目巡检必须校验未关闭任务负责人和实时在职状态。\n\n"
            "是否记录到 Skill Feedback Issue？仅记录，不启动修复。"
        ),
        "writes": False,
    }
    assert "sig_DEADBEEF" not in notice["display_text"]


def test_notice_policy_can_override_visuals_without_changing_renderer() -> None:
    policy = copy.deepcopy(DEFAULT_NOTICE_POLICY)
    policy["events"]["lesson"]["tag"] = "EvoZeus · Learning"
    policy["events"]["lesson"]["states"]["pending"] = {
        "icon": "🧠",
        "label": "待确认",
    }

    notice = render_notice(
        kind="lesson",
        state="pending",
        message="Reusable rule found.",
        policy=policy,
    )

    assert notice["icon"] == "🧠"
    assert notice["tag"] == "EvoZeus · Learning"
    assert notice["state_label"] == "待确认"
    assert notice["display_text"] == "🧠 `EvoZeus · Learning` 待确认\n\nReusable rule found."


def test_skill_notice_renders_identity_details_inline_without_body() -> None:
    notice = render_notice(
        kind="skill",
        state="active",
        details=(
            "[MetaInFLow/skill](https://github.com/MetaInFLow/skill) · "
            "Skill v1.2.3 · Harness v0.13.0 · `渠道：UAT`"
        ),
    )

    assert notice["message"] is None
    assert notice["display_text"] == (
        "🧙🏻‍♂️ `EvoZeus · 受管 Skill` "
        "[MetaInFLow/skill](https://github.com/MetaInFLow/skill) · "
        "Skill v1.2.3 · Harness v0.13.0 · `渠道：UAT`"
    )


def test_notice_renderer_rejects_unknown_kind_and_state() -> None:
    with pytest.raises(ValueError, match="unsupported notice kind"):
        render_notice(kind="business", state="running", message="Do work.")

    with pytest.raises(ValueError, match="unsupported state"):
        render_notice(kind="lesson", state="released", message="Do work.")


def test_notice_cli_loads_target_policy_and_returns_json_without_writes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        policy_path = Path(tmp) / "notice-policy.json"
        policy = copy.deepcopy(DEFAULT_NOTICE_POLICY)
        policy["show_signal_id"] = True
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(NOTICE_SCRIPT),
                "render",
                "--policy",
                str(policy_path),
                "--kind",
                "lesson",
                "--state",
                "pending",
                "--message",
                "A reusable lesson.",
                "--signal-id",
                "sig_12345678",
                "--json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["writes"] is False
        assert payload["signal_id"] == "sig_12345678"
        assert "sig_12345678" in payload["display_text"]
        assert load_notice_policy(policy_path)["show_signal_id"] is True


def test_staged_target_preflight_ignores_consumer_pyc_and_creates_no_bytecode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scripts_dir = Path(tmp) / ".evozeus-wrapper" / "scripts"
        scripts_dir.mkdir(parents=True)
        shutil.copy2(
            ROOT
            / "construction/harness-skill/v1.2.0/target/.evozeus-wrapper/"
            "scripts/evozeus_wrapper_preflight.py",
            scripts_dir,
        )
        consumer = scripts_dir / "evozeus_branch_consumer.py"
        shutil.copy2(ROOT / "scripts" / "evozeus_branch_consumer.py", consumer)
        shutil.copy2(NOTICE_SCRIPT, scripts_dir)
        marker = Path(tmp) / "consumer-pyc-executed"
        code = compile(
            f"open({str(marker)!r}, 'w').write('executed')\n",
            str(consumer),
            "exec",
        )
        metadata = consumer.stat()
        cache = Path(importlib.util.cache_from_source(str(consumer)))
        cache.parent.mkdir(parents=True)
        cache.write_bytes(
            importlib._bootstrap_external._code_to_timestamp_pyc(  # type: ignore[attr-defined]
                code,
                int(metadata.st_mtime),
                metadata.st_size,
            )
        )

        result = subprocess.run(
            [sys.executable, str(scripts_dir / "evozeus_wrapper_preflight.py"), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert not marker.exists()
        assert not list(scripts_dir.glob("__pycache__/evozeus_notice.*.pyc"))
        assert not list(scripts_dir.glob("__pycache__/evozeus_wrapper_preflight.*.pyc"))


def test_staged_target_preflight_rejects_scripts_directory_exchange() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / ".evozeus-wrapper"
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True)
        preflight = scripts_dir / "evozeus_wrapper_preflight.py"
        shutil.copy2(
            ROOT
            / "construction/harness-skill/v1.2.0/target/.evozeus-wrapper/"
            "scripts/evozeus_wrapper_preflight.py",
            preflight,
        )
        shutil.copy2(ROOT / "scripts/evozeus_branch_consumer.py", scripts_dir)
        retained = root / "scripts-retained"
        replacement = root / "scripts-replacement"
        replacement.mkdir()
        marker = Path(tmp) / "replacement-consumer-executed"
        (replacement / "evozeus_branch_consumer.py").write_text(
            f"open({str(marker)!r}, 'w').write('executed')\n",
            encoding="utf-8",
        )
        notice_source = NOTICE_SCRIPT.read_text(encoding="utf-8")
        exchange = (
            "\nimport os as _exchange_os\n"
            f"_exchange_os.rename({str(scripts_dir)!r}, {str(retained)!r})\n"
            f"_exchange_os.rename({str(replacement)!r}, {str(scripts_dir)!r})\n"
        )
        notice_source = notice_source.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n" + exchange,
            1,
        )
        (scripts_dir / "evozeus_notice.py").write_text(
            notice_source,
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(preflight), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode != 0
        assert "trusted scripts directory identity changed" in result.stderr
        assert not marker.exists()


def test_staged_target_preflight_accepts_fully_injected_trusted_sources() -> None:
    preflight = (
        ROOT
        / "construction/harness-skill/v1.2.0/target/.evozeus-wrapper/"
        "scripts/evozeus_wrapper_preflight.py"
    ).read_bytes()
    notice = NOTICE_SCRIPT.read_bytes()
    consumer = (ROOT / "scripts/evozeus_branch_consumer.py").read_bytes()
    namespace = {
        "__file__": "/__evozeus_trusted__/evozeus_wrapper_preflight.py",
        "__name__": "_evozeus_preflight_test",
        "_EVOZEUS_TRUSTED_NOTICE_SOURCE": notice,
        "_EVOZEUS_TRUSTED_NOTICE_SHA256": hashlib.sha256(notice).hexdigest(),
        "_EVOZEUS_TRUSTED_CONTRIBUTOR_SOURCE": consumer,
        "_EVOZEUS_TRUSTED_CONTRIBUTOR_SHA256": hashlib.sha256(consumer).hexdigest(),
    }

    exec(
        compile(
            preflight,
            namespace["__file__"],
            "exec",
            dont_inherit=True,
        ),
        namespace,
    )

    assert namespace["_TRUSTED_SOURCE_RUNTIME"]["scripts_dir"] == (
        "/__evozeus_trusted__"
    )


@pytest.mark.parametrize(
    ("kind", "state", "expected"),
    [
        ("skill", "active", "🧙🏻‍♂️ `EvoZeus · 受管 Skill`"),
        ("lesson", "recorded", "📝 `EvoZeus · Lesson` 已记录"),
        ("evolution", "verified", "🛠️ `EvoZeus · Evolution` 已验证"),
        ("maintenance", "completed", "🔧 `EvoZeus · Maintenance` 已完成"),
        ("advisory", "continue", "⚠️ `EvoZeus · Advisory` 可继续"),
        ("blocked", "blocked", "🛑 `EvoZeus · Blocked` 已阻塞"),
        ("uat", "replaced", "🧪 `EvoZeus · UAT` 已覆盖"),
        ("release", "published", "🚀 `EvoZeus · Release` 已发布"),
    ],
)
def test_default_notice_taxonomy_has_stable_visual_contract(
    kind: str,
    state: str,
    expected: str,
) -> None:
    notice = render_notice(kind=kind, state=state, message="Result.")

    assert notice["display_text"].splitlines()[0] == expected
