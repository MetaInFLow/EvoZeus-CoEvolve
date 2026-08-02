from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.evozeus_wrapper_global_hook import (
    CORE_ACTIVE_CHANNEL,
    CORE_CHANNEL_STATE,
    CORE_DISPATCHER_SCHEMA,
    CORE_DISPATCHER_SOURCE,
    CORE_DISPATCHER_STATE,
    CORE_USER_PROMPT_RUNTIME_API,
    GLOBAL_DISPATCHER,
    GLOBAL_DISPATCHER_COMMAND,
    GLOBAL_HOOK_STATE,
    apply_global_hook_install,
    apply_global_hook_uninstall,
    _product_manifest_digest,
    read_global_hook_status,
    record_global_hook_trust,
)
from scripts.evozeus_wrapper_bootstrap import WRAPPER_VERSION


ROOT = Path(__file__).resolve().parents[1]


class GlobalLessonWatcherLifecycleTest(unittest.TestCase):
    def _seed_core_runtime(
        self,
        home: Path,
        *,
        source: Path | None = None,
        include_lesson_marker: bool = True,
    ) -> tuple[Path, Path]:
        home = home.expanduser().resolve()
        product_home = home / ".evozeus"
        install_root = product_home / "worktrees/uat/test-product"
        core_root = install_root / "evozeus"
        source_dispatcher = core_root / CORE_DISPATCHER_SOURCE
        source_dispatcher.parent.mkdir(parents=True, exist_ok=True)
        dispatcher = home / GLOBAL_DISPATCHER
        dispatcher.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            marker = (
                f'USER_PROMPT_RUNTIME_API = "{CORE_USER_PROMPT_RUNTIME_API}"\n'
                if include_lesson_marker
                else ""
            )
            source_dispatcher.write_text(
                "#!/usr/bin/env python3\n"
                f'SCHEMA_VERSION = "{CORE_DISPATCHER_SCHEMA}"\n'
                f"{marker}",
                encoding="utf-8",
            )
        else:
            shutil.copy2(source, source_dispatcher)
        shutil.copy2(source_dispatcher, dispatcher)
        dispatcher.chmod(0o700)
        manifest = {
            "schema_version": "evozeus.product-channel.v2",
            "product_version": "v0.5.0",
            "channel": "uat",
            "generated_at": "2026-08-02T00:00:00Z",
            "components": {
                "evozeus": {
                    "version": "v0.5.0",
                    "commit": "d54ad32d0cb23043055098f0fe32f5378296209d",
                    "source": {"kind": "git", "ref": "test"},
                    "required_paths": [CORE_DISPATCHER_SOURCE.as_posix()],
                },
                "coevolve": {
                    "version": "v0.14.0",
                    "commit": "97cbf7aa00000000000000000000000000000000",
                    "source": {"kind": "git", "ref": "test"},
                    "required_paths": ["scripts/evozeus_wrapper.py"],
                },
            },
            "embedded": {},
            "compatibility": {
                "runtime_min_inclusive": "0.2.0",
                "runtime_max_exclusive": "0.3.0",
                "coevolve_contract": "v1.1.0",
            },
        }
        (home / CORE_ACTIVE_CHANNEL).write_text(
            json.dumps(
                {
                    "schema_version": "evozeus.active-channel.v1",
                    "channel": "uat",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (home / CORE_CHANNEL_STATE).write_text(
            json.dumps(
                {
                    "schema_version": "evozeus.channel-state.v1",
                    "channels": {
                        "stable": None,
                        "uat": {
                            "manifest": manifest,
                            "manifest_digest": _product_manifest_digest(manifest),
                            "install_root": str(install_root),
                            "component_roots": {"evozeus": str(core_root)},
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        core_state = home / CORE_DISPATCHER_STATE
        core_state.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "wrapper_source": "channel-managed",
                    "source_repository": "MetaInFLow/EvoZeus",
                    "installed_version": "v0.14.0",
                    "core_version": "v0.5.0",
                    "runtime_api": CORE_USER_PROMPT_RUNTIME_API,
                    "trust_status": "verified_by_product_manifest",
                    "active_channel_source": "active-channel.json",
                    "command": f'/usr/bin/python3 "{dispatcher}"',
                    "installation_status": "installed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return dispatcher, core_state

    def test_patch_version_identifies_the_new_harness(self):
        self.assertEqual(WRAPPER_VERSION, "v0.14.0")

    def test_coevolve_dispatcher_template_remains_session_start_only(self):
        source = (ROOT / "templates/global/evozeus_wrapper_dispatcher.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("UserPromptSubmit", source)
        self.assertNotIn("lesson-candidate", source)
        self.assertNotIn("SESSION_SIGNAL", source)

    def test_install_requires_core_owned_runtime_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertTrue(
                any("Core-owned global dispatcher" in error for error in report["errors"])
            )
            self.assertFalse((home / ".codex/hooks.json").exists())
            self.assertFalse((home / GLOBAL_HOOK_STATE).exists())

    def test_install_rejects_self_asserted_markers_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher = home / GLOBAL_DISPATCHER
            dispatcher.parent.mkdir(parents=True)
            dispatcher.write_text(
                "# arbitrary program\n"
                f"# {CORE_DISPATCHER_SCHEMA}\n"
                f"# {CORE_USER_PROMPT_RUNTIME_API}\n",
                encoding="utf-8",
            )
            (home / CORE_DISPATCHER_STATE).write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "wrapper_source": "channel-managed",
                        "source_repository": "MetaInFLow/EvoZeus",
                        "installed_version": "v0.14.0",
                        "core_version": "v0.5.0",
                        "runtime_api": CORE_USER_PROMPT_RUNTIME_API,
                        "trust_status": "verified_by_product_manifest",
                        "active_channel_source": "active-channel.json",
                        "command": f'/usr/bin/python3 "{dispatcher}"',
                        "installation_status": "installed",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertTrue(
                any("active-channel" in error for error in report["errors"])
            )
            self.assertFalse((home / ".codex/hooks.json").exists())

    def test_install_rejects_symlinked_core_runtime_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            dispatcher, core_state = self._seed_core_runtime(home)
            outside_hooks = root / "outside-hooks"
            outside_hooks.mkdir()
            shutil.copy2(dispatcher, outside_hooks / dispatcher.name)
            shutil.copy2(core_state, outside_hooks / core_state.name)
            shutil.rmtree(dispatcher.parent)
            dispatcher.parent.symlink_to(outside_hooks, target_is_directory=True)

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertTrue(any("symlink" in error for error in report["errors"]))
            self.assertFalse((home / ".codex/hooks.json").exists())

    def test_install_binds_dispatcher_bytes_to_active_core_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher, _ = self._seed_core_runtime(home)
            dispatcher.write_text(
                "# different arbitrary program\n"
                f"# {CORE_DISPATCHER_SCHEMA}\n"
                f"# {CORE_USER_PROMPT_RUNTIME_API}\n",
                encoding="utf-8",
            )

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertIn(
                "Core-owned global dispatcher does not match the active product component",
                report["errors"],
            )

    def test_install_rejects_active_core_root_outside_product_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            self._seed_core_runtime(home)
            channel_state_path = home / CORE_CHANNEL_STATE
            channel_state = json.loads(channel_state_path.read_text(encoding="utf-8"))
            entry = channel_state["channels"]["uat"]
            outside_install = root / "outside-product"
            outside_core = outside_install / "evozeus"
            outside_core.mkdir(parents=True)
            source = Path(entry["component_roots"]["evozeus"]) / CORE_DISPATCHER_SOURCE
            destination = outside_core / CORE_DISPATCHER_SOURCE
            destination.parent.mkdir(parents=True)
            shutil.copy2(source, destination)
            entry["install_root"] = str(outside_install)
            entry["component_roots"]["evozeus"] = str(outside_core)
            channel_state_path.write_text(json.dumps(channel_state) + "\n", encoding="utf-8")

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertTrue(
                any("outside the product home" in error for error in report["errors"])
            )

    def test_install_reports_incomplete_product_evidence_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            self._seed_core_runtime(home)
            channel_state_path = home / CORE_CHANNEL_STATE
            channel_state = json.loads(channel_state_path.read_text(encoding="utf-8"))
            channel_state["channels"]["uat"].pop("manifest")
            channel_state_path.write_text(json.dumps(channel_state) + "\n", encoding="utf-8")

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["writes"])
            self.assertIn("Core active product entry has no manifest", report["errors"])

    def test_install_owns_registrations_and_preserves_core_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher, core_state = self._seed_core_runtime(home)
            dispatcher_before = dispatcher.read_bytes()
            core_state_before = core_state.read_bytes()
            hooks_path = home / ".codex/hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "python3 keep.py"}
                                    ]
                                }
                            ],
                            "UserPromptSubmit": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python3 keep-prompt.py",
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            first = apply_global_hook_install(home, ROOT, approve=True)
            second = apply_global_hook_install(home, ROOT, approve=True)
            installed = json.loads(hooks_path.read_text(encoding="utf-8"))
            status = read_global_hook_status(home)

            self.assertEqual(first["status"], "installed")
            self.assertEqual(second["status"], "already_installed")
            for event in ("SessionStart", "UserPromptSubmit"):
                commands = [
                    handler["command"]
                    for entry in installed["hooks"][event]
                    for handler in entry["hooks"]
                ]
                self.assertEqual(commands.count(GLOBAL_DISPATCHER_COMMAND), 1)
            self.assertIn("PreToolUse", installed["hooks"])
            self.assertEqual(dispatcher.read_bytes(), dispatcher_before)
            self.assertEqual(core_state.read_bytes(), core_state_before)
            self.assertTrue((home / GLOBAL_HOOK_STATE).is_file())
            self.assertEqual(status["status"], "installed")
            self.assertTrue(status["runtime_endpoint_ready"])
            self.assertEqual(status["runtime_owner"], "MetaInFLow/EvoZeus")
            self.assertEqual(status["trust_status"], "pending_review")

    def test_install_rejects_pre_core_user_prompt_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher, _ = self._seed_core_runtime(
                home,
                include_lesson_marker=False,
            )
            before = dispatcher.read_bytes()

            report = apply_global_hook_install(home, ROOT, approve=True)

            self.assertEqual(report["status"], "blocked")
            self.assertIn(
                "Core-owned UserPromptSubmit Lesson runtime marker is missing",
                report["errors"],
            )
            self.assertEqual(dispatcher.read_bytes(), before)

    def test_status_reports_core_utf8_decode_errors_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher, core_state = self._seed_core_runtime(home)

            dispatcher.write_bytes(b"\xff\xfe")
            dispatcher_status = read_global_hook_status(home)
            self.assertTrue(
                any("Core-owned global dispatcher cannot be read" in error
                    for error in dispatcher_status["errors"])
            )

            self._seed_core_runtime(home)
            core_state.write_bytes(b"\xff\xfe")
            state_status = read_global_hook_status(home)
            self.assertTrue(
                any("invalid Core-owned global dispatcher state" in error
                    for error in state_status["errors"])
            )

    def test_partial_registration_is_upgrade_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            self._seed_core_runtime(home)
            hooks_path = home / ".codex/hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "startup|resume",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": GLOBAL_DISPATCHER_COMMAND,
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            status = read_global_hook_status(home)

            self.assertEqual(status["status"], "upgrade_required")
            self.assertTrue(status["session_registration_installed"])
            self.assertFalse(status["prompt_registration_installed"])

    def test_uninstall_removes_only_lifecycle_owned_files_and_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher, core_state = self._seed_core_runtime(home)
            apply_global_hook_install(home, ROOT, approve=True)
            hooks_path = home / ".codex/hooks.json"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks["hooks"]["SessionStart"].append(
                {"hooks": [{"type": "command", "command": "python3 keep.py"}]}
            )
            hooks["hooks"]["UserPromptSubmit"].append(
                {
                    "hooks": [
                        {"type": "command", "command": "python3 keep-prompt.py"}
                    ]
                }
            )
            hooks_path.write_text(json.dumps(hooks), encoding="utf-8")
            dispatcher_before = dispatcher.read_bytes()
            core_state_before = core_state.read_bytes()

            report = apply_global_hook_uninstall(home, approve=True)
            remaining = json.loads(hooks_path.read_text(encoding="utf-8"))
            serialized = json.dumps(remaining)

            self.assertEqual(report["status"], "uninstalled")
            self.assertNotIn(GLOBAL_DISPATCHER_COMMAND, serialized)
            self.assertIn("python3 keep.py", serialized)
            self.assertIn("python3 keep-prompt.py", serialized)
            self.assertEqual(dispatcher.read_bytes(), dispatcher_before)
            self.assertEqual(core_state.read_bytes(), core_state_before)
            self.assertFalse((home / GLOBAL_HOOK_STATE).exists())

    def test_trust_record_updates_only_lifecycle_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            _, core_state = self._seed_core_runtime(home)
            apply_global_hook_install(home, ROOT, approve=True)
            core_state_before = core_state.read_bytes()

            report = record_global_hook_trust(home, status="trusted", approve=True)

            self.assertEqual(report["status"], "trusted")
            self.assertEqual(core_state.read_bytes(), core_state_before)
            lifecycle = json.loads((home / GLOBAL_HOOK_STATE).read_text(encoding="utf-8"))
            self.assertEqual(lifecycle["trust_status"], "trusted")
            self.assertNotIn("wrapper_source", lifecycle)

    def test_uninstalling_orphan_lifecycle_state_does_not_create_hooks_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            self._seed_core_runtime(home)
            apply_global_hook_install(home, ROOT, approve=True)
            hooks_path = home / ".codex/hooks.json"
            hooks_path.unlink()

            report = apply_global_hook_uninstall(home, approve=True)

            self.assertEqual(report["status"], "uninstalled")
            self.assertFalse(hooks_path.exists())
            self.assertFalse((home / GLOBAL_HOOK_STATE).exists())

    @unittest.skipUnless(
        os.environ.get("EVOZEUS_TEST_CORE_ROOT"),
        "requires an explicit EvoZeus Core PR checkout",
    )
    def test_real_core_dispatcher_is_registered_without_copy_or_mutation(self):
        core_root = Path(os.environ["EVOZEUS_TEST_CORE_ROOT"]).resolve()
        source = core_root / "scripts/evozeus-coevolve-dispatcher.py"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dispatcher, core_state = self._seed_core_runtime(home, source=source)
            before = {
                "dispatcher": dispatcher.read_bytes(),
                "core_state": core_state.read_bytes(),
            }

            report = apply_global_hook_install(home, ROOT, approve=True)
            result = subprocess.run(
                [sys.executable, str(dispatcher)],
                input=json.dumps(
                    {"hook_event_name": "UserPromptSubmit", "prompt": "neutral", "cwd": None}
                ),
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "EVOZEUS_HOME": str(home / ".evozeus"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                check=False,
            )

            self.assertEqual(report["status"], "installed")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"continue": True})
            self.assertEqual(dispatcher.read_bytes(), before["dispatcher"])
            self.assertEqual(core_state.read_bytes(), before["core_state"])


if __name__ == "__main__":
    unittest.main()
