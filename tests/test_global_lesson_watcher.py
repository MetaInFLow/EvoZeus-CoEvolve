from __future__ import annotations

import hashlib
import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.evozeus_wrapper_global_hook import (
    apply_global_hook_install,
    apply_global_hook_uninstall,
    read_global_hook_status,
)
from scripts.evozeus_wrapper_bootstrap import WRAPPER_VERSION


class GlobalLessonWatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher_template = Path("templates/global/evozeus_wrapper_dispatcher.py").resolve()
        spec = importlib.util.spec_from_file_location(
            "evozeus_global_lesson_watcher_test_module",
            self.dispatcher_template,
        )
        self.dispatcher_module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(self.dispatcher_module)

    def test_patch_version_identifies_the_new_harness(self):
        self.assertEqual(WRAPPER_VERSION, "v0.14.0")

    def create_wrapped_target(self, home: Path, name: str, version: str = "v0.13.0") -> Path:
        target = home.parent / f"canonical-{name}"
        target.mkdir()
        manifest = target / ".evozeus-wrapper" / "wrapper.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "canonical_repo": f"MetaInFLow/{name}",
                    "wrapper_version": version,
                }
            ),
            encoding="utf-8",
        )
        pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / name
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.symlink_to(target)
        return target

    def create_component_fixture(
        self,
        home: Path,
        *,
        product_home: Path | None = None,
    ) -> dict[str, object]:
        product_home = product_home or home / ".evozeus"
        install_root = product_home / "releases" / "fixture"
        core_root = install_root / "evozeus"
        session_root = core_root / "packs" / "session-signal"
        script = session_root / "scripts" / "evaluate_lesson_candidate.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            textwrap.dedent(
                """\
                import json
                import sys
                import time

                request = json.loads(sys.stdin.read())
                prompt = request.get("prompt")
                if prompt == "timeout":
                    time.sleep(5)
                if prompt == "invalid-json":
                    sys.stdout.write("{")
                elif prompt == "error":
                    raise SystemExit(3)
                elif prompt in {"candidate-assigned", "candidate-unassigned"}:
                    target = None
                    if prompt == "candidate-assigned" and request.get("targets"):
                        target = request["targets"][0]["repo"]
                    print(json.dumps({
                        "schema_version": "evozeus.session-signal.lesson-candidate.v1",
                        "candidate": True,
                        "target_repo": target,
                        "model_guidance": "Model-only Lesson guidance. Ask before record; do not start a fix.",
                    }))
                else:
                    print(json.dumps({
                        "schema_version": "evozeus.session-signal.lesson-candidate.v1",
                        "candidate": False,
                    }))
                """
            ),
            encoding="utf-8",
        )
        component_manifest = {
            "schema_version": "evozeus.session-signal.lesson-candidate-component.v1",
            "component_version": "v0.1.1",
            "api": "evozeus.session-signal.lesson-candidate.v1",
            "entrypoint": "scripts/evaluate_lesson_candidate.py",
            "files": [
                {
                    "path": "scripts/evaluate_lesson_candidate.py",
                    "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                }
            ],
        }
        component_manifest_path = session_root / "contracts" / "lesson-candidate-v1.json"
        component_manifest_path.parent.mkdir(parents=True)
        component_manifest_path.write_text(
            json.dumps(component_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        attachment = {
            **self.dispatcher_module.SESSION_SIGNAL_ATTACHMENT,
            "component_manifest_sha256": hashlib.sha256(
                component_manifest_path.read_bytes()
            ).hexdigest(),
        }
        product_manifest = {
            "schema_version": "evozeus.product-channel.v2",
            "product_version": "v0.5.0",
            "channel": "uat",
            "generated_at": "2026-07-31T00:00:00Z",
            "components": {},
            "embedded": {
                "session_signal": {
                    "version": "v0.1.1",
                    "path": "packs/session-signal",
                    "required_paths": [
                        "contracts/lesson-candidate-v1.json",
                        "scripts/evaluate_lesson_candidate.py",
                    ],
                }
            },
            "compatibility": {},
        }
        product_home.mkdir(parents=True, exist_ok=True)
        (product_home / "active-channel.json").write_text(
            json.dumps({"channel": "uat"}),
            encoding="utf-8",
        )
        state_path = product_home / "channel-state.json"
        state = {
            "channels": {
                "uat": {
                    "manifest": product_manifest,
                    "manifest_digest": self.dispatcher_module._product_manifest_digest(
                        product_manifest
                    ),
                    "install_root": str(install_root),
                    "component_roots": {"evozeus": str(core_root)},
                    "embedded_roots": {"session_signal": str(session_root)},
                }
            }
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return {
            "attachment": attachment,
            "product_home": product_home,
            "state_path": state_path,
            "session_root": session_root,
            "script": script,
            "component_manifest_path": component_manifest_path,
        }

    def run_prompt_hook(
        self,
        home: Path,
        cwd: Path,
        prompt: str,
        fixture: dict[str, object],
    ) -> dict[str, object]:
        return self.dispatcher_module.evaluate_user_prompt_submit(
            home,
            {
                "session_id": "thread-test",
                "turn_id": "turn-test",
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(cwd),
                "prompt": prompt,
            },
            evozeus_home=fixture["product_home"],
            attachment=fixture["attachment"],
        )

    def test_install_registers_prompt_watcher_without_replacing_other_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "python3 keep-prompt-hook.py"}
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            first = apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            second = apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            installed = json.loads(hooks_path.read_text(encoding="utf-8"))
            prompt_commands = [
                handler["command"]
                for entry in installed["hooks"]["UserPromptSubmit"]
                for handler in entry["hooks"]
            ]

            self.assertEqual(first["status"], "installed")
            self.assertEqual(second["status"], "already_installed")
            self.assertIn("python3 keep-prompt-hook.py", prompt_commands)
            self.assertEqual(
                prompt_commands.count(
                    '/usr/bin/python3 "$HOME/.evozeus/hooks/evozeus_wrapper_dispatcher.py"'
                ),
                1,
            )

    def test_status_reports_session_gate_and_prompt_lesson_watcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"

            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)
            status = read_global_hook_status(home)

            self.assertEqual(status["status"], "installed")
            self.assertTrue(status["session_registration_installed"])
            self.assertTrue(status["prompt_registration_installed"])
            self.assertEqual(
                status["capabilities"],
                ["global_session_dispatcher", "global_prompt_lesson_watcher"],
            )

    def test_status_reports_non_list_prompt_hook_values_as_errors(self):
        for invalid in (None, 1, {}):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                hooks_path = home / ".codex" / "hooks.json"
                hooks_path.parent.mkdir(parents=True)
                hooks_path.write_text(
                    json.dumps({"hooks": {"UserPromptSubmit": invalid}}),
                    encoding="utf-8",
                )

                status = read_global_hook_status(home)

                self.assertEqual(status["status"], "not_installed")
                self.assertEqual(
                    status["errors"],
                    ["global hooks UserPromptSubmit must be a list"],
                )

    def test_session_only_legacy_install_is_reported_as_upgrade_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            dispatcher = home / ".evozeus" / "hooks" / "evozeus_wrapper_dispatcher.py"
            state_path = home / ".evozeus" / "hooks" / "state.json"
            hooks_path.parent.mkdir(parents=True)
            dispatcher.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": '/usr/bin/python3 "$HOME/.evozeus/hooks/evozeus_wrapper_dispatcher.py"',
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            dispatcher.write_text("# legacy\n", encoding="utf-8")
            state_path.write_text(json.dumps({"trust_status": "trusted"}), encoding="utf-8")

            status = read_global_hook_status(home)

            self.assertEqual(status["status"], "upgrade_required")
            self.assertTrue(status["any_registration_installed"])
            self.assertTrue(status["session_registration_installed"])
            self.assertFalse(status["prompt_registration_installed"])

            refreshed = apply_global_hook_install(
                home=home,
                wrapper_root=Path.cwd(),
                approve=True,
            )
            current = read_global_hook_status(home)

            self.assertEqual(refreshed["registration_action"], "refresh")
            self.assertEqual(current["status"], "installed")
            self.assertTrue(current["prompt_registration_installed"])

    def test_uninstall_removes_only_evozeus_handlers_from_both_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"hooks": [{"type": "command", "command": "python3 keep-start.py"}]}
                            ],
                            "UserPromptSubmit": [
                                {"hooks": [{"type": "command", "command": "python3 keep-prompt.py"}]}
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)

            report = apply_global_hook_uninstall(home=home, approve=True)
            remaining = json.loads(hooks_path.read_text(encoding="utf-8"))
            commands = [
                handler["command"]
                for event in ("SessionStart", "UserPromptSubmit")
                for entry in remaining["hooks"][event]
                for handler in entry["hooks"]
            ]

            self.assertEqual(report["status"], "uninstalled")
            self.assertEqual(commands, ["python3 keep-start.py", "python3 keep-prompt.py"])

    def test_uninstall_restores_config_shape_when_events_were_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            original = {"description": "keep", "hooks": {"PreToolUse": []}}
            hooks_path.write_text(json.dumps(original), encoding="utf-8")
            apply_global_hook_install(home=home, wrapper_root=Path.cwd(), approve=True)

            apply_global_hook_uninstall(home=home, approve=True)

            self.assertEqual(json.loads(hooks_path.read_text(encoding="utf-8")), original)

    def test_active_component_candidate_returns_only_model_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = self.create_wrapped_target(home, "metainflow-dev-tasks")
            fixture = self.create_component_fixture(home)

            payload = self.run_prompt_hook(
                home,
                target,
                "candidate-assigned",
                fixture,
            )
            serialized = json.dumps(payload, ensure_ascii=False)

            self.assertEqual(
                payload,
                {
                    "continue": True,
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": (
                            "Model-only Lesson guidance. Ask before record; do not start a fix."
                        ),
                    },
                },
            )
            self.assertNotIn("candidate-assigned", serialized)
            self.assertNotIn(str(target), serialized)
            self.assertNotIn("signal_id", serialized)

    def test_component_neutral_and_unassigned_results_preserve_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()
            fixture = self.create_component_fixture(home)

            neutral = self.run_prompt_hook(home, cwd, "neutral", fixture)
            unassigned = self.run_prompt_hook(home, cwd, "candidate-unassigned", fixture)

            self.assertEqual(neutral, {"continue": True})
            self.assertIn(
                "Model-only Lesson guidance",
                unassigned["hookSpecificOutput"]["additionalContext"],
            )

    def test_component_receives_registered_target_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "consumer"
            cwd.mkdir()
            target = self.create_wrapped_target(home, "project-management-repo")
            (target / "SKILL.md").write_text(
                "---\nname: project-management-assistant\n---\n# Project management\n",
                encoding="utf-8",
            )
            fixture = self.create_component_fixture(home)
            captured: dict[str, object] = {}

            def runner(command, **kwargs):
                captured.update({"command": command, **kwargs})
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": "evozeus.session-signal.lesson-candidate.v1",
                            "candidate": False,
                        }
                    ).encode(),
                    stderr=b"",
                )

            payload = self.dispatcher_module.evaluate_user_prompt_submit(
                home,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(cwd),
                    "prompt": "opaque user turn",
                },
                evozeus_home=fixture["product_home"],
                attachment=fixture["attachment"],
                runner=runner,
            )
            request = json.loads(captured["input"].decode())

            self.assertEqual(payload, {"continue": True})
            self.assertEqual(request["event_name"], "UserPromptSubmit")
            self.assertEqual(request["schema_version"], fixture["attachment"]["api"])
            self.assertEqual(request["targets"][0]["repo"], "MetaInFLow/project-management-repo")
            self.assertIn("project-management-assistant", request["targets"][0]["aliases"])
            self.assertEqual(captured["shell"], False)
            self.assertEqual(captured["timeout"], 1.5)
            self.assertEqual(
                captured["env"],
                {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
            )

    def test_custom_product_home_keeps_fixed_user_project_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            target = self.create_wrapped_target(home, "custom-product-target")
            fixture = self.create_component_fixture(
                home,
                product_home=root / "custom-product-home",
            )
            captured: dict[str, object] = {}

            def runner(command, **kwargs):
                captured.update({"command": command, **kwargs})
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": "evozeus.session-signal.lesson-candidate.v1",
                            "candidate": False,
                        }
                    ).encode(),
                    stderr=b"",
                )

            payload = self.dispatcher_module.evaluate_user_prompt_submit(
                home,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(target),
                    "prompt": "opaque user turn",
                },
                evozeus_home=fixture["product_home"],
                attachment=fixture["attachment"],
                runner=runner,
            )
            request = json.loads(captured["input"].decode())

            self.assertEqual(payload, {"continue": True})
            self.assertEqual(
                request["targets"][0]["repo"],
                "MetaInFLow/custom-product-target",
            )

    def test_component_resolution_requires_verified_manifest_and_root_containment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = self.create_component_fixture(home)
            state = json.loads(fixture["state_path"].read_text(encoding="utf-8"))

            state["channels"]["uat"]["manifest"]["embedded"]["session_signal"][
                "version"
            ] = "v0.1.0"
            state["channels"]["uat"]["manifest_digest"] = (
                self.dispatcher_module._product_manifest_digest(
                    state["channels"]["uat"]["manifest"]
                )
            )
            fixture["state_path"].write_text(json.dumps(state), encoding="utf-8")
            self.assertIsNone(
                self.dispatcher_module.resolve_session_signal_component(
                    fixture["product_home"], attachment=fixture["attachment"]
                )
            )

            state["channels"]["uat"]["manifest"]["embedded"]["session_signal"][
                "version"
            ] = "v0.1.1"
            state["channels"]["uat"]["manifest_digest"] = "sha256:" + "0" * 64
            fixture["state_path"].write_text(json.dumps(state), encoding="utf-8")
            self.assertIsNone(
                self.dispatcher_module.resolve_session_signal_component(
                    fixture["product_home"], attachment=fixture["attachment"]
                )
            )

            state["channels"]["uat"]["manifest_digest"] = (
                self.dispatcher_module._product_manifest_digest(
                    state["channels"]["uat"]["manifest"]
                )
            )
            state["channels"]["uat"]["install_root"] = str(Path(tmp) / "other-root")
            (Path(tmp) / "other-root").mkdir()
            fixture["state_path"].write_text(json.dumps(state), encoding="utf-8")
            self.assertIsNone(
                self.dispatcher_module.resolve_session_signal_component(
                    fixture["product_home"], attachment=fixture["attachment"]
                )
            )

    def test_component_resolution_rejects_missing_or_damaged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = self.create_component_fixture(home)
            component_manifest_path = fixture["component_manifest_path"]
            original_manifest = component_manifest_path.read_bytes()

            component_manifest_path.write_bytes(original_manifest + b" ")
            self.assertIsNone(
                self.dispatcher_module.resolve_session_signal_component(
                    fixture["product_home"], attachment=fixture["attachment"]
                )
            )

            component_manifest_path.write_bytes(original_manifest)
            fixture["script"].unlink()
            self.assertIsNone(
                self.dispatcher_module.resolve_session_signal_component(
                    fixture["product_home"], attachment=fixture["attachment"]
                )
            )

    def test_component_resolution_rejects_symlinked_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = self.create_component_fixture(home)
            script = fixture["script"]
            outside = Path(tmp) / "outside.py"
            outside.write_bytes(script.read_bytes())
            script.unlink()
            script.symlink_to(outside)

            self.assertIsNone(
                self.dispatcher_module.resolve_session_signal_component(
                    fixture["product_home"], attachment=fixture["attachment"]
                )
            )

    def test_missing_timeout_error_and_invalid_output_are_silent_fail_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "private-workspace"
            cwd.mkdir()

            self.assertEqual(
                self.dispatcher_module.evaluate_user_prompt_submit(
                    home,
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "cwd": str(cwd),
                        "prompt": "PRIVATE-MISSING-COMPONENT",
                    },
                ),
                {"continue": True},
            )
            fixture = self.create_component_fixture(home)
            for prompt in ("timeout", "error", "invalid-json"):
                with self.subTest(prompt=prompt):
                    payload = self.run_prompt_hook(home, cwd, prompt, fixture)
                    self.assertEqual(payload, {"continue": True})
                    self.assertNotIn(
                        str(cwd),
                        json.dumps(payload, ensure_ascii=False),
                    )

    def test_oversized_inventory_degrades_to_unassigned_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()
            fixture = self.create_component_fixture(home)
            oversized = [
                {
                    "repo": f"MetaInFLow/target-{index}",
                    "canonical_path": Path(tmp) / f"target-{index}",
                    "aliases": (f"target-{index}",),
                }
                for index in range(self.dispatcher_module.SESSION_SIGNAL_MAX_TARGETS + 1)
            ]

            with patch.object(
                self.dispatcher_module,
                "discover_wrapped_targets",
                return_value=(oversized, []),
            ):
                payload = self.run_prompt_hook(
                    home,
                    cwd,
                    "candidate-unassigned",
                    fixture,
                )

            self.assertIn(
                "Model-only Lesson guidance",
                payload["hookSpecificOutput"]["additionalContext"],
            )

    def test_oversized_prompt_does_not_launch_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = self.create_component_fixture(home)

            def runner(*_args, **_kwargs):
                self.fail("oversized prompt must stop before subprocess launch")

            payload = self.dispatcher_module.evaluate_user_prompt_submit(
                home,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(Path(tmp) / "workspace"),
                    "prompt": "x" * (
                        self.dispatcher_module.SESSION_SIGNAL_MAX_PROMPT_CHARS + 1
                    ),
                },
                evozeus_home=fixture["product_home"],
                attachment=fixture["attachment"],
                runner=runner,
            )

            self.assertEqual(payload, {"continue": True})

    @unittest.skipUnless(
        os.environ.get("EVOZEUS_TEST_SESSION_SIGNAL_ROOT"),
        "requires an explicit Session Signal companion checkout",
    )
    def test_real_companion_hook_smoke_in_isolated_home(self):
        source = Path(os.environ["EVOZEUS_TEST_SESSION_SIGNAL_ROOT"]).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            product_home = root / "custom-product-home"
            install_root = product_home / "releases" / "fixture"
            core_root = install_root / "evozeus"
            session_root = core_root / "packs" / "session-signal"
            for relative in ("contracts", "scripts", "src"):
                shutil.copytree(source / relative, session_root / relative)
            product_manifest = {
                "schema_version": "evozeus.product-channel.v2",
                "product_version": "v0.5.0",
                "channel": "uat",
                "generated_at": "2026-07-31T00:00:00Z",
                "components": {},
                "embedded": {
                    "session_signal": {
                        "version": "v0.1.1",
                        "path": "packs/session-signal",
                        "required_paths": [
                            "contracts/lesson-candidate-v1.json",
                            "scripts/evaluate_lesson_candidate.py",
                        ],
                    }
                },
                "compatibility": {},
            }
            product_home.mkdir(parents=True, exist_ok=True)
            (product_home / "active-channel.json").write_text(
                json.dumps({"channel": "uat"}),
                encoding="utf-8",
            )
            (product_home / "channel-state.json").write_text(
                json.dumps(
                    {
                        "channels": {
                            "uat": {
                                "manifest": product_manifest,
                                "manifest_digest": (
                                    self.dispatcher_module._product_manifest_digest(
                                        product_manifest
                                    )
                                ),
                                "install_root": str(install_root),
                                "component_roots": {"evozeus": str(core_root)},
                                "embedded_roots": {
                                    "session_signal": str(session_root)
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            target = self.create_wrapped_target(home, "real-companion-target")

            def tree_snapshot() -> dict[str, str]:
                return {
                    path.relative_to(root).as_posix(): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in root.rglob("*")
                    if path.is_file() and not path.is_symlink()
                }

            def invoke(prompt: str) -> dict[str, object]:
                result = subprocess.run(
                    [sys.executable, str(self.dispatcher_template)],
                    input=json.dumps(
                        {
                            "hook_event_name": "UserPromptSubmit",
                            "cwd": str(target),
                            "prompt": prompt,
                        }
                    ),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=target,
                    env={
                        **os.environ,
                        "HOME": str(home),
                        "EVOZEUS_HOME": str(product_home),
                    },
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            before = tree_snapshot()
            correction = invoke("你漏检了回滚要求，是否可以补上？")
            neutral = invoke("请总结今天的项目进展。")
            ambiguous = invoke("这个结果是不是不对？")
            after = tree_snapshot()

            context = correction["hookSpecificOutput"]["additionalContext"]
            self.assertIn("EvoZeus · Lesson", context)
            self.assertIn("MetaInFLow/real-companion-target", context)
            self.assertNotIn("你漏检了回滚要求", json.dumps(correction, ensure_ascii=False))
            self.assertNotIn(str(target), json.dumps(correction, ensure_ascii=False))
            self.assertEqual(neutral, {"continue": True})
            self.assertEqual(ambiguous, {"continue": True})
            self.assertEqual(before, after)

    def test_prompt_watcher_fails_open_on_unexpected_runtime_exception(self):
        with patch.object(
            self.dispatcher_module,
            "resolve_session_signal_component",
            side_effect=OSError("private path and component JSON must not leak"),
        ):
            payload = self.dispatcher_module.evaluate_user_prompt_submit(
                Path("/private/home"),
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/private/workspace",
                    "prompt": "PRIVATE-PROMPT",
                },
            )

        self.assertEqual(payload, {"continue": True})
        self.assertNotIn("private", json.dumps(payload, ensure_ascii=False).casefold())


if __name__ == "__main__":
    unittest.main()
