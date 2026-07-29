import json
import fcntl
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.evozeus_harness_publish import (
    plan_admin_upgrade_all,
    publish_admin_upgrade_all,
    publish_target_upgrade,
    resolve_github_admin_access,
    verify_canonical_github_origin,
)


def completed(returncode=0, stdout="", stderr=""):
    return {"returncode": returncode, "stdout": stdout, "stderr": stderr}


class AdminUpgradePlanningTest(unittest.TestCase):
    def upgrade_plan(self):
        return {
            "stage": "harness_upgrade_all",
            "status": "planned",
            "writes": False,
            "latest_version": "v0.13.0",
            "targets": [
                {
                    "repo": "MetaInFLow/admin-skill",
                    "target": "/tmp/admin-skill",
                    "wrapper_version": "v0.12.1",
                    "migration": {"can_apply": True},
                },
                {
                    "repo": "MetaInFLow/write-skill",
                    "target": "/tmp/write-skill",
                    "wrapper_version": "v0.12.1",
                    "migration": {"can_apply": True},
                },
            ],
        }

    def test_plan_allows_only_repo_admins_to_publish(self):
        def access(repo):
            permission = "ADMIN" if repo.endswith("admin-skill") else "WRITE"
            return {
                "repo": repo,
                "viewer": "anthonyf",
                "permission": permission,
                "is_admin": permission == "ADMIN",
                "default_branch": "main",
                "url": f"https://github.com/{repo}",
                "error": None,
            }

        report = plan_admin_upgrade_all(
            Path("/tmp/home"),
            Path("/tmp/wrapper"),
            "v0.13.0",
            upgrade_planner=lambda *args, **kwargs: self.upgrade_plan(),
            access_resolver=access,
        )

        self.assertEqual(report["status"], "planned")
        self.assertFalse(report["writes"])
        self.assertEqual(report["publishable_count"], 1)
        self.assertEqual(report["skipped_permission_count"], 1)
        self.assertEqual(report["publishable_targets"][0]["repo"], "MetaInFLow/admin-skill")
        self.assertEqual(report["skipped_targets"][0]["reason"], "github_admin_required")

    def test_publish_requires_explicit_approval(self):
        calls = []
        report = publish_admin_upgrade_all(
            Path("/tmp/home"),
            Path("/tmp/wrapper"),
            "v0.13.0",
            approve=False,
            upgrade_planner=lambda *args, **kwargs: self.upgrade_plan(),
            access_resolver=lambda repo: {
                "repo": repo,
                "viewer": "anthonyf",
                "permission": "ADMIN",
                "is_admin": True,
                "default_branch": "main",
                "url": f"https://github.com/{repo}",
                "error": None,
            },
            target_publisher=lambda *args, **kwargs: calls.append(args),
        )

        self.assertEqual(report["status"], "approval_required")
        self.assertFalse(report["writes"])
        self.assertEqual(calls, [])

    def test_plan_skips_a_failed_target_without_blocking_other_admin_repos(self):
        plan = self.upgrade_plan()
        plan["targets"][0]["errors"] = []
        plan["targets"][1]["errors"] = ["target preflight failed"]

        report = plan_admin_upgrade_all(
            Path("/tmp/home"),
            Path("/tmp/wrapper"),
            "v0.13.0",
            upgrade_planner=lambda *args, **kwargs: plan,
            access_resolver=lambda repo: {
                "repo": repo,
                "viewer": "anthonyf",
                "permission": "ADMIN",
                "is_admin": True,
                "default_branch": "main",
                "url": f"https://github.com/{repo}",
                "error": None,
            },
        )

        self.assertEqual(report["status"], "planned")
        self.assertEqual(report["publishable_count"], 1)
        self.assertEqual(report["skipped_preflight_count"], 1)
        self.assertEqual(report["skipped_targets"][0]["reason"], "target_preflight_failed")

    def test_publish_writes_run_and_event_ledger_for_admin_targets_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            published = []

            def access(repo):
                permission = "ADMIN" if repo.endswith("admin-skill") else "MAINTAIN"
                return {
                    "repo": repo,
                    "viewer": "anthonyf",
                    "permission": permission,
                    "is_admin": permission == "ADMIN",
                    "default_branch": "main",
                    "url": f"https://github.com/{repo}",
                    "error": None,
                }

            def publisher(item, **kwargs):
                published.append(item["repo"])
                return {
                    "repo": item["repo"],
                    "status": "published",
                    "branch": "evozeus/harness-v0.12.1-to-v0.13.0",
                    "commit": "a" * 40,
                    "pr_url": "https://github.com/MetaInFLow/admin-skill/pull/1",
                }

            report = publish_admin_upgrade_all(
                home,
                Path("/tmp/wrapper"),
                "v0.13.0",
                approve=True,
                upgrade_planner=lambda *args, **kwargs: self.upgrade_plan(),
                access_resolver=access,
                target_publisher=publisher,
                run_id="upgrade_test_001",
            )

            self.assertEqual(report["status"], "partial")
            self.assertTrue(report["writes"])
            self.assertEqual(published, ["MetaInFLow/admin-skill"])
            run_path = home / ".evozeus/skills/runs/upgrade_test_001.json"
            events_path = home / ".evozeus/skills/events.jsonl"
            self.assertTrue(run_path.is_file())
            self.assertTrue(events_path.is_file())
            self.assertEqual(json.loads(run_path.read_text())["run_id"], "upgrade_test_001")
            events = [json.loads(line) for line in events_path.read_text().splitlines()]
            self.assertEqual(events[0]["event"], "harness_upgrade_published")
            self.assertEqual(events[0]["skill_id"], "MetaInFLow/admin-skill")
            self.assertNotIn("raw_session", run_path.read_text())

    def test_publish_refuses_a_concurrent_upgrade_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            lock_path = home / ".evozeus/locks/harness-upgrade-all.lock"
            lock_path.parent.mkdir(parents=True)
            with lock_path.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                report = publish_admin_upgrade_all(
                    home,
                    Path("/tmp/wrapper"),
                    "v0.13.0",
                    approve=True,
                    upgrade_planner=lambda *args, **kwargs: self.upgrade_plan(),
                    access_resolver=lambda repo: {
                        "repo": repo,
                        "viewer": "anthonyf",
                        "permission": "ADMIN",
                        "is_admin": True,
                        "default_branch": "main",
                        "url": f"https://github.com/{repo}",
                        "error": None,
                    },
                    target_publisher=lambda *args, **kwargs: self.fail("publisher must not run"),
                )

            self.assertEqual(report["status"], "busy")
            self.assertFalse(report["writes"])


class GitHubAccessTest(unittest.TestCase):
    def test_resolves_admin_permission_from_authenticated_github_viewer(self):
        payload = {
            "data": {
                "viewer": {"login": "anthonyf"},
                "repository": {
                    "viewerPermission": "ADMIN",
                    "defaultBranchRef": {"name": "main"},
                    "url": "https://github.com/MetaInFLow/example",
                },
            }
        }

        access = resolve_github_admin_access(
            "MetaInFLow/example",
            runner=lambda args, cwd=None: completed(stdout=json.dumps(payload)),
        )

        self.assertTrue(access["is_admin"])
        self.assertEqual(access["viewer"], "anthonyf")
        self.assertEqual(access["default_branch"], "main")

    def test_rejects_a_canonical_checkout_pointing_at_another_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            canonical.mkdir()

            with self.assertRaisesRegex(RuntimeError, "origin does not match"):
                verify_canonical_github_origin(
                    "MetaInFLow/expected",
                    canonical,
                    runner=lambda args, cwd=None: completed(
                        stdout="git@github.com:MetaInFLow/another.git\n"
                    ),
                )

    def test_accepts_https_and_ssh_origins_for_the_canonical_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            canonical.mkdir()
            for remote in (
                "https://github.com/MetaInFLow/example.git\n",
                "git@github.com:MetaInFLow/example.git\n",
            ):
                self.assertEqual(
                    verify_canonical_github_origin(
                        "MetaInFLow/example",
                        canonical,
                        runner=lambda args, cwd=None, value=remote: completed(stdout=value),
                    ),
                    "MetaInFLow/example",
                )


class IsolatedPublishTest(unittest.TestCase):
    def test_publishes_from_isolated_worktree_and_preserves_canonical_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            canonical = root / "canonical"
            home = root / "home"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(canonical)], check=True)
            (canonical / "SKILL.md").write_text("# Business Skill\n\nKEEP-BUSINESS\n")
            manifest = canonical / ".evozeus-wrapper/wrapper.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"wrapper_version": "v0.12.1"}))
            subprocess.run(["git", "-C", str(canonical), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(canonical),
                    "-c", "user.name=EvoZeus Test",
                    "-c", "user.email=evozeus@example.invalid",
                    "commit", "-qm", "fixture",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", str(canonical), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(canonical), "push", "-q", "-u", "origin", "main"], check=True)
            original_head = subprocess.check_output(["git", "-C", str(canonical), "rev-parse", "HEAD"], text=True).strip()

            def migrate(worktree, latest_version, **kwargs):
                target_manifest = worktree / ".evozeus-wrapper/wrapper.json"
                data = json.loads(target_manifest.read_text())
                data["wrapper_version"] = latest_version
                target_manifest.write_text(json.dumps(data, indent=2) + "\n")
                migration = worktree / ".evozeus-wrapper/docs/migrations/upgrade.md"
                migration.parent.mkdir(parents=True)
                migration.write_text("Harness upgraded.\n")
                return {
                    "writes": True,
                    "changed_files": [
                        ".evozeus-wrapper/wrapper.json",
                        ".evozeus-wrapper/docs/migrations/upgrade.md",
                    ],
                }

            report = publish_target_upgrade(
                {
                    "repo": "MetaInFLow/example",
                    "target": str(canonical),
                    "wrapper_version": "v0.12.1",
                    "github": {
                        "viewer": "anthonyf",
                        "permission": "ADMIN",
                        "is_admin": True,
                        "default_branch": "main",
                        "url": "https://github.com/MetaInFLow/example",
                    },
                },
                home=home,
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_test_002",
                migrator=migrate,
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=lambda **kwargs: "https://github.com/MetaInFLow/example/pull/2",
                origin_verifier=lambda repo, canonical: repo,
            )

            self.assertEqual(report["status"], "published")
            self.assertEqual(report["pr_url"], "https://github.com/MetaInFLow/example/pull/2")
            self.assertFalse(Path(report["worktree"]).exists())
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(canonical), "branch", "--show-current"], text=True).strip(),
                "main",
            )
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(canonical), "rev-parse", "HEAD"], text=True).strip(),
                original_head,
            )
            self.assertIn("KEEP-BUSINESS", (canonical / "SKILL.md").read_text())
            remote_branch = subprocess.check_output(
                ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/evozeus/harness-v0.12.1-to-v0.13.0"],
                text=True,
            ).strip()
            self.assertEqual(remote_branch, report["commit"])

    def test_removes_isolated_worktree_when_remote_target_is_already_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            canonical = root / "canonical"
            home = root / "home"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(canonical)], check=True)
            (canonical / "SKILL.md").write_text("# Current Skill\n")
            subprocess.run(["git", "-C", str(canonical), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(canonical),
                    "-c", "user.name=EvoZeus Test",
                    "-c", "user.email=evozeus@example.invalid",
                    "commit", "-qm", "fixture",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", str(canonical), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(canonical), "push", "-q", "-u", "origin", "main"], check=True)

            report = publish_target_upgrade(
                {
                    "repo": "MetaInFLow/current",
                    "target": str(canonical),
                    "wrapper_version": "v0.12.1",
                    "github": {
                        "viewer": "anthonyf",
                        "permission": "ADMIN",
                        "is_admin": True,
                        "default_branch": "main",
                        "url": "https://github.com/MetaInFLow/current",
                    },
                },
                home=home,
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_test_003",
                migrator=lambda *args, **kwargs: {"writes": False, "changed_files": []},
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=lambda **kwargs: self.fail("PR must not be created"),
                origin_verifier=lambda repo, canonical: repo,
            )

            self.assertEqual(report["status"], "up_to_date")
            self.assertFalse(Path(report["worktree"]).exists())


if __name__ == "__main__":
    unittest.main()
