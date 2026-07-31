from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
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

    def run_prompt_hook(self, home: Path, cwd: Path, prompt: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(self.dispatcher_template)],
            input=json.dumps(
                {
                    "session_id": "thread-test",
                    "turn_id": "turn-test",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(cwd),
                    "prompt": prompt,
                }
            ),
            text=True,
            capture_output=True,
            cwd=cwd,
            env={**os.environ, "HOME": str(home)},
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

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

    def test_reusable_correction_in_normal_chat_injects_record_only_lesson_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = self.create_wrapped_target(home, "metainflow-dev-tasks")

            payload = self.run_prompt_hook(
                home,
                target,
                "这样的 log 怎么能对呢？应该在正常 chat 里提示捕捉到 Lesson，不能展示 JSON。",
            )
            context = payload["hookSpecificOutput"]["additionalContext"]

            self.assertTrue(payload["continue"])
            self.assertEqual(
                payload["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit"
            )
            self.assertIn("先完成用户当前请求", context)
            self.assertIn("💡 `EvoZeus · Lesson` 待记录", context)
            self.assertIn("本次只记录，不启动修复", context)
            self.assertIn("MetaInFLow/metainflow-dev-tasks", context)
            self.assertIn("不得展示内部 JSON", context)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("should_capture", serialized)
            self.assertNotIn("signal_id", serialized)
            self.assertNotIn("capture_state", serialized)

    def test_direct_correction_and_missed_check_phrases_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()

            for prompt in (
                "不对，负责人已经离职了。",
                "错了，正确答案是 B。",
                "这是漏检，需要补上。",
            ):
                with self.subTest(prompt=prompt):
                    payload = self.run_prompt_hook(home, cwd, prompt)
                    self.assertIn(
                        "EvoZeus · Lesson",
                        payload["hookSpecificOutput"]["additionalContext"],
                    )

    def test_neutral_prompt_does_not_inject_lesson_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = self.create_wrapped_target(home, "neutral-skill")

            payload = self.run_prompt_hook(home, target, "帮我总结今天的项目进展。")

            self.assertEqual(payload, {"continue": True})

    def test_generic_question_with_should_language_does_not_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()

            payload = self.run_prompt_hook(home, cwd, "这个项目下一步应该怎么做？")

            self.assertEqual(payload, {"continue": True})

    def test_no_issue_found_status_sentence_does_not_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()

            payload = self.run_prompt_hook(home, cwd, "今天巡检没有发现问题，继续观察。")

            self.assertEqual(payload, {"continue": True})

    def test_ambiguous_neutral_phrases_do_not_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()

            for prompt in (
                "这个政策哪里不合理？",
                "这个项目采用不对称加密。",
                "这个变量有误差，需要计算。",
                "We should have three meetings this week.",
                "You missed the train because it left early.",
                "所有用户看到了什么提示？",
                "以后你知道天气会怎么变化吗？",
                "所有用户应该怎么登录？",
                "所有用户必须怎么登录？",
                "以后必须怎么处理？",
                "下次先去哪里吃饭？",
                "下次需要准备什么材料？",
                "下次务必带什么材料？",
                "For all users, must the password contain a number?",
            ):
                with self.subTest(prompt=prompt):
                    self.assertEqual(self.run_prompt_hook(home, cwd, prompt), {"continue": True})

    def test_direct_missed_fact_correction_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()

            payload = self.run_prompt_hook(
                home,
                cwd,
                "你没有发现负责人已经离职，这个巡检漏掉了关键状态。",
            )

            self.assertIn("EvoZeus · Lesson", payload["hookSpecificOutput"]["additionalContext"])

    def test_future_reminder_rule_triggers_without_explicit_skill_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()

            payload = self.run_prompt_hook(
                home,
                cwd,
                "以后也要知道，如果没有开早会，记得去看飞书私信。",
            )

            self.assertIn("EvoZeus · Lesson", payload["hookSpecificOutput"]["additionalContext"])

    def test_explicit_registered_skill_name_routes_from_consumer_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "consumer"
            cwd.mkdir()
            self.create_wrapped_target(home, "metainflow-dev-tasks")

            payload = self.run_prompt_hook(
                home,
                cwd,
                "metainflow-dev-tasks 这个输出不对，以后必须隐藏内部字段。",
            )

            self.assertIn(
                "MetaInFLow/metainflow-dev-tasks",
                payload["hookSpecificOutput"]["additionalContext"],
            )

    def test_declared_skill_name_routes_when_it_differs_from_repo_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "consumer"
            cwd.mkdir()
            target = self.create_wrapped_target(home, "project-management-repo")
            (target / "SKILL.md").write_text(
                "---\nname: project-management-assistant\n---\n# Project management\n",
                encoding="utf-8",
            )

            payload = self.run_prompt_hook(
                home,
                cwd,
                "project-management-assistant 这个结果不对，漏了离职负责人。",
            )

            self.assertIn(
                "MetaInFLow/project-management-repo",
                payload["hookSpecificOutput"]["additionalContext"],
            )

    def test_short_repo_slug_does_not_match_inside_another_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "consumer"
            cwd.mkdir()
            self.create_wrapped_target(home, "app")

            payload = self.run_prompt_hook(
                home,
                cwd,
                "What happened is wrong; this should be corrected.",
            )

            self.assertIn(
                "无法确定目标 Skill",
                payload["hookSpecificOutput"]["additionalContext"],
            )

    def test_prompt_watcher_fails_open_when_target_registry_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "workspace"
            cwd.mkdir()
            invalid_pointer = home / ".evozeus" / ".projects" / "MetaInFLow" / "invalid"
            invalid_pointer.mkdir(parents=True)

            payload = self.run_prompt_hook(
                home,
                cwd,
                "这个结果不对，以后每次都应该先核对实时负责人状态。",
            )
            context = payload["hookSpecificOutput"]["additionalContext"]

            self.assertTrue(payload["continue"])
            self.assertIn("无法确定目标 Skill", context)
            self.assertNotIn(str(cwd), json.dumps(payload, ensure_ascii=False))

    def test_prompt_watcher_fails_open_on_unexpected_runtime_exception(self):
        with patch.object(
            self.dispatcher_module,
            "discover_wrapped_targets",
            side_effect=OSError("private path must not leak"),
        ):
            payload = self.dispatcher_module.evaluate_user_prompt_submit(
                Path("/private/home"),
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/private/workspace",
                    "prompt": "不对，这次漏检了负责人状态。",
                },
            )

        self.assertEqual(payload, {"continue": True})
        self.assertNotIn("private", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
