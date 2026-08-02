import base64
import fcntl
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.evozeus_wrapper_bootstrap import copy_templates
from scripts.evozeus_harness_publish import (
    _default_existing_pr,
    plan_admin_upgrade_all,
    publish_admin_upgrade_all,
    publish_target_upgrade,
    resolve_github_admin_access,
    resolve_official_upgrade_source,
    run_command,
    verify_canonical_github_origin,
)
from scripts.evozeus_wrapper_lifecycle import (
    WRAPPER_MANAGED_FILES,
    build_harness_activation_block,
    build_wrapper_manifest,
    detect_target_architecture,
    write_wrapper_manifest,
)
from scripts.evozeus_wrapper_preflight import (
    TRUSTED_CONTROL_SOURCES,
    check_trusted_pr_checkouts,
)


ROOT = Path(__file__).resolve().parents[1]


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

    def test_failed_pr_creation_records_a_recoverable_remote_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            plan = self.upgrade_plan()
            plan["targets"] = plan["targets"][:1]

            report = publish_admin_upgrade_all(
                home,
                Path("/tmp/wrapper"),
                "v0.13.0",
                approve=True,
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
                target_publisher=lambda item, **kwargs: {
                    "repo": item["repo"],
                    "status": "pr_creation_failed",
                    "writes": True,
                    "branch": "evozeus/harness-v0.12.1-to-v0.13.0",
                    "commit": "a" * 40,
                    "pr_url": None,
                    "worktree": "/Users/private/recovery-worktree",
                    "error": (
                        "PR creation failed after the branch push at "
                        "https://oauth2:secret-token@github.com/MetaInFLow/admin-skill"
                    ),
                    "remote_side_effect": {
                        "kind": "branch_push",
                        "repo": item["repo"],
                        "branch": "evozeus/harness-v0.12.1-to-v0.13.0",
                        "commit": "a" * 40,
                        "target_base_ref": "main",
                        "target_base_commit": "b" * 40,
                        "source_revision": "v0.13.0",
                        "plan_identity": "c" * 64,
                        "recovery": "retry_same_upgrade",
                    },
                },
                run_id="upgrade_pr_failure_001",
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["failed_count"], 1)
            run_payload = json.loads(
                (home / ".evozeus/skills/runs/upgrade_pr_failure_001.json").read_text()
            )
            effect = run_payload["results"][0]["remote_side_effect"]
            self.assertEqual(effect["kind"], "branch_push")
            self.assertEqual(effect["recovery"], "retry_same_upgrade")
            self.assertNotIn("secret-token", json.dumps(run_payload))
            self.assertNotIn("/Users/private", json.dumps(run_payload))
            events = [
                json.loads(line)
                for line in (home / ".evozeus/skills/events.jsonl").read_text().splitlines()
            ]
            self.assertEqual(events[0]["event"], "harness_upgrade_pr_creation_failed")
            self.assertEqual(events[0]["commit"], "a" * 40)


class GitHubAccessTest(unittest.TestCase):
    def test_existing_pr_lookup_returns_live_head_and_base_identity(self):
        calls = []
        payload = [
            {
                "html_url": "https://github.com/MetaInFLow/example/pull/7",
                "number": 7,
                "body": "bound plan",
                "head": {
                    "ref": "evozeus/harness-v0.12.1-to-v0.13.0",
                    "sha": "a" * 40,
                    "repo": {"full_name": "MetaInFLow/example"},
                },
                "base": {
                    "ref": "main",
                    "sha": "b" * 40,
                    "repo": {"full_name": "MetaInFLow/example"},
                },
            }
        ]

        def runner(args, cwd=None):
            calls.append(args)
            return completed(stdout=json.dumps(payload))

        existing = _default_existing_pr(
            "MetaInFLow/example",
            "evozeus/harness-v0.12.1-to-v0.13.0",
            runner=runner,
        )

        self.assertEqual(existing["head_commit"], "a" * 40)
        self.assertEqual(existing["base_commit"], "b" * 40)
        self.assertIn("head=MetaInFLow:evozeus/harness-v0.12.1-to-v0.13.0", calls[0])

    def test_resolves_admin_permission_from_authenticated_github_viewer(self):
        payload = {
            "data": {
                "viewer": {"login": "anthonyf"},
                "repository": {
                    "viewerPermission": "ADMIN",
                    "defaultBranchRef": {"name": "main", "target": {"oid": "a" * 40}},
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
        self.assertEqual(access["default_branch_oid"], "a" * 40)

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

    def test_accepts_credentialed_https_origin_without_exposing_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            canonical.mkdir()
            credential = "secret-token-value"
            remote = f"https://oauth2:{credential}@github.com/MetaInFLow/example.git\n"

            self.assertEqual(
                verify_canonical_github_origin(
                    "MetaInFLow/example",
                    canonical,
                    runner=lambda args, cwd=None: completed(stdout=remote),
                ),
                "MetaInFLow/example",
            )

            with self.assertRaisesRegex(RuntimeError, "github.com/MetaInFLow/example") as raised:
                verify_canonical_github_origin(
                    "MetaInFLow/another",
                    canonical,
                    runner=lambda args, cwd=None: completed(stdout=remote),
                )
            self.assertNotIn(credential, str(raised.exception))
            self.assertNotIn("oauth2", str(raised.exception))


class OfficialUpgradeSourceTest(unittest.TestCase):
    def initialize_source(self, root: Path, version: str = "v0.13.0") -> Path:
        source = root / "source"
        source.mkdir()
        manifest_path = source / "contracts/v1/manifest.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "evozeus.coevolve.contract-manifest.v1",
                    "bundle_id": "evozeus-coevolve",
                    "bundle_version": "v1.0.0",
                    "source_repository": "MetaInFLow/EvoZeus-CoEvolve",
                    "source_revision": version,
                    "files": [],
                }
            )
            + "\n"
        )
        subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(source),
                "-c", "user.name=EvoZeus Test",
                "-c", "user.email=evozeus@example.invalid",
                "commit", "-qm", "release source",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(source), "remote", "add", "origin",
                "https://github.com/MetaInFLow/EvoZeus-CoEvolve.git",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(source), "tag", version], check=True)
        return source

    @staticmethod
    def resolve_source(source: Path, version: str = "v0.13.0") -> dict:
        head = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", f"{version}^{{commit}}"],
            text=True,
        ).strip()

        def runner(args, cwd=None):
            if "ls-remote" in args:
                return completed(stdout=f"{head}\trefs/tags/{version}\n")
            return run_command(args, cwd)

        return resolve_official_upgrade_source(source, version, runner=runner)

    def test_binds_source_to_official_clean_tagged_commit_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.initialize_source(Path(tmp))

            evidence = self.resolve_source(source)

            head = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
            ).strip()
            self.assertEqual(evidence["source_revision"], head)
            self.assertEqual(evidence["source_tag"], "v0.13.0")
            self.assertRegex(evidence["contract_manifest_sha256"], r"^[0-9a-f]{64}$")

    def test_rejects_dirty_or_tag_mismatched_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.initialize_source(Path(tmp))
            (source / "untracked.txt").write_text("dirty\n")
            with self.assertRaisesRegex(RuntimeError, "source checkout must be clean"):
                self.resolve_source(source)

            (source / "untracked.txt").unlink()
            (source / "next.txt").write_text("next\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(source),
                    "-c", "user.name=EvoZeus Test",
                    "-c", "user.email=evozeus@example.invalid",
                    "commit", "-qm", "next commit",
                ],
                check=True,
            )
            with self.assertRaisesRegex(RuntimeError, "tag does not resolve to source HEAD"):
                self.resolve_source(source)

    def test_redacts_credentials_from_origin_command_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            canonical.mkdir()
            credential = "secret-token-value"
            detail = (
                "fatal: unable to access "
                f"https://oauth2:{credential}@github.com/MetaInFLow/example.git"
            )

            with self.assertRaisesRegex(RuntimeError, "https://github.com") as raised:
                verify_canonical_github_origin(
                    "MetaInFLow/example",
                    canonical,
                    runner=lambda args, cwd=None: completed(returncode=1, stderr=detail),
                )
            self.assertNotIn(credential, str(raised.exception))
            self.assertNotIn("oauth2", str(raised.exception))


class IsolatedPublishTest(unittest.TestCase):
    def initialize_remote_repo(self, root: Path, files: dict[str, str]):
        remote = root / "remote.git"
        canonical = root / "canonical"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(canonical)], check=True)
        for relative, content in files.items():
            path = canonical / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
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
        subprocess.run(
            ["git", "-C", str(canonical), "remote", "add", "origin", str(remote)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(canonical), "push", "-q", "-u", "origin", "main"],
            check=True,
        )
        return remote, canonical

    @staticmethod
    def live_admin(repo: str) -> dict:
        return {
            "repo": repo,
            "viewer": "anthonyf",
            "permission": "ADMIN",
            "is_admin": True,
            "default_branch": "main",
            "default_branch_oid": None,
            "url": f"https://github.com/{repo}",
            "error": None,
        }

    @staticmethod
    def trusted_source(_wrapper_root: Path, version: str) -> dict:
        return {
            "schema_version": "evozeus.coevolve.harness-upgrade-source.v1",
            "source_repository": "MetaInFLow/EvoZeus-CoEvolve",
            "source_tag": version,
            "source_revision": "d" * 40,
            "contract_manifest_sha256": "e" * 64,
            "contract_bundle_id": "evozeus-coevolve",
            "contract_bundle_version": "v1.0.0",
        }

    @staticmethod
    def publish_target(canonical: Path, repo: str = "MetaInFLow/example") -> dict:
        return {
            "repo": repo,
            "target": str(canonical),
            "wrapper_version": "v0.12.1",
            "github": IsolatedPublishTest.live_admin(repo),
        }

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
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
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
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(report["status"], "up_to_date")
            self.assertFalse(Path(report["worktree"]).exists())

    def test_publish_accepts_a_declared_legacy_source_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, canonical = self.initialize_remote_repo(
                root,
                {"legacy-wrapper.json": "legacy\n", "SKILL.md": "# Business Skill\n"},
            )

            def migrate(worktree, latest_version, **kwargs):
                (worktree / "legacy-wrapper.json").unlink()
                (worktree / "current-wrapper.json").write_text(
                    latest_version + "\n",
                    encoding="utf-8",
                )
                return {
                    "writes": True,
                    "changed_files": ["legacy-wrapper.json", "current-wrapper.json"],
                }

            report = publish_target_upgrade(
                self.publish_target(canonical),
                home=root / "home",
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_source_deletion",
                migrator=migrate,
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=lambda **kwargs: "https://github.com/MetaInFLow/example/pull/3",
                origin_verifier=lambda repo, checkout: repo,
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(report["status"], "published")
            self.assertEqual(
                set(report["changed_files"]),
                {"legacy-wrapper.json", "current-wrapper.json"},
            )
            published = subprocess.check_output(
                ["git", "--git-dir", str(remote), "show", f"{report['commit']}:current-wrapper.json"],
                text=True,
            )
            self.assertEqual(published, "v0.13.0\n")

    def test_reuses_deterministic_local_and_remote_branch_with_exact_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, canonical = self.initialize_remote_repo(
                root,
                {"SKILL.md": "# Business Skill\n"},
            )
            migration_number = 0

            def migrate(worktree, latest_version, **kwargs):
                nonlocal migration_number
                migration_number += 1
                (worktree / "upgrade.txt").write_text(
                    f"{latest_version} run {migration_number}\n",
                    encoding="utf-8",
                )
                return {"writes": True, "changed_files": ["upgrade.txt"]}

            arguments = {
                "target": self.publish_target(canonical),
                "home": root / "home",
                "wrapper_root": root / "wrapper",
                "latest_version": "v0.13.0",
                "migrator": migrate,
                "existing_pr_resolver": lambda repo, branch: None,
                "pr_creator": lambda **kwargs: "https://github.com/MetaInFLow/example/pull/4",
                "origin_verifier": lambda repo, checkout: repo,
                "access_resolver": self.live_admin,
                "source_resolver": self.trusted_source,
            }
            first = publish_target_upgrade(run_id="upgrade_reuse_1", **arguments)
            second = publish_target_upgrade(run_id="upgrade_reuse_2", **arguments)

            self.assertEqual(first["status"], "published")
            self.assertEqual(second["status"], "published")
            self.assertNotEqual(first["commit"], second["commit"])
            remote_head = subprocess.check_output(
                [
                    "git",
                    "--git-dir",
                    str(remote),
                    "rev-parse",
                    "refs/heads/evozeus/harness-v0.12.1-to-v0.13.0",
                ],
                text=True,
            ).strip()
            self.assertEqual(remote_head, second["commit"])
            self.assertFalse(Path(first["worktree"]).exists())
            self.assertFalse(Path(second["worktree"]).exists())

    def test_rechecks_live_admin_before_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, canonical = self.initialize_remote_repo(
                root,
                {"SKILL.md": "# Business Skill\n"},
            )
            permissions = iter(("ADMIN", "WRITE"))

            def live_access(repo):
                permission = next(permissions)
                return {
                    **self.live_admin(repo),
                    "permission": permission,
                    "is_admin": permission == "ADMIN",
                }

            def migrate(worktree, latest_version, **kwargs):
                (worktree / "upgrade.txt").write_text(latest_version + "\n")
                return {"writes": True, "changed_files": ["upgrade.txt"]}

            with self.assertRaisesRegex(PermissionError, "live GitHub ADMIN"):
                publish_target_upgrade(
                    self.publish_target(canonical),
                    home=root / "home",
                    wrapper_root=root / "wrapper",
                    latest_version="v0.13.0",
                    run_id="upgrade_permission_changed_before_push",
                    migrator=migrate,
                    existing_pr_resolver=lambda repo, branch: None,
                    pr_creator=lambda **kwargs: self.fail("PR must not be created"),
                    origin_verifier=lambda repo, checkout: repo,
                    access_resolver=live_access,
                    source_resolver=self.trusted_source,
                )

            branch = "refs/heads/evozeus/harness-v0.12.1-to-v0.13.0"
            self.assertNotEqual(
                subprocess.run(
                    ["git", "--git-dir", str(remote), "rev-parse", "--verify", branch],
                    capture_output=True,
                ).returncode,
                0,
            )

    def test_rechecks_live_admin_before_pr_creation_and_records_the_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, canonical = self.initialize_remote_repo(
                root,
                {"SKILL.md": "# Business Skill\n"},
            )
            permissions = iter(("ADMIN", "ADMIN", "WRITE"))

            def live_access(repo):
                permission = next(permissions)
                return {
                    **self.live_admin(repo),
                    "permission": permission,
                    "is_admin": permission == "ADMIN",
                }

            def migrate(worktree, latest_version, **kwargs):
                (worktree / "upgrade.txt").write_text(latest_version + "\n")
                return {"writes": True, "changed_files": ["upgrade.txt"]}

            result = publish_target_upgrade(
                self.publish_target(canonical),
                home=root / "home",
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_permission_changed_before_pr",
                migrator=migrate,
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=lambda **kwargs: self.fail("PR must not be created"),
                origin_verifier=lambda repo, checkout: repo,
                access_resolver=live_access,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(result["status"], "pr_creation_failed")
            self.assertIn("live GitHub ADMIN", result["error"])
            self.assertEqual(result["remote_side_effect"]["commit"], result["commit"])
            self.assertIsNone(result["worktree"])
            remote_head = subprocess.check_output(
                [
                    "git",
                    "--git-dir",
                    str(remote),
                    "rev-parse",
                    "refs/heads/evozeus/harness-v0.12.1-to-v0.13.0",
                ],
                text=True,
            ).strip()
            self.assertEqual(remote_head, result["commit"])

            retry = publish_target_upgrade(
                self.publish_target(canonical),
                home=root / "home",
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_permission_retry",
                migrator=migrate,
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=lambda **kwargs: "https://github.com/MetaInFLow/example/pull/8",
                origin_verifier=lambda repo, checkout: repo,
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(retry["status"], "published")
            self.assertEqual(retry["branch"], result["branch"])
            retry_worktree = (
                root
                / "home/.evozeus/worktrees/harness-upgrade"
                / "upgrade_permission_retry/MetaInFLow--example"
            )
            self.assertFalse(retry_worktree.exists())

    def test_pr_creation_failure_is_recoverable_and_retry_reuses_exact_live_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, canonical = self.initialize_remote_repo(
                root,
                {"SKILL.md": "# Business Skill\n"},
            )
            created: dict[str, str] = {}

            def migrate(worktree, latest_version, **kwargs):
                (worktree / "upgrade.txt").write_text(latest_version + "\n")
                return {"writes": True, "changed_files": ["upgrade.txt"]}

            def create_then_fail(**kwargs):
                created.update(kwargs)
                raise RuntimeError(
                    "transport closed after creating "
                    "https://oauth2:secret-token@github.com/MetaInFLow/example"
                )

            first = publish_target_upgrade(
                self.publish_target(canonical),
                home=root / "home",
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_pr_failure_then_retry",
                migrator=migrate,
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=create_then_fail,
                origin_verifier=lambda repo, checkout: repo,
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(first["status"], "pr_creation_failed")
            self.assertNotIn("secret-token", first["error"])
            base_commit = subprocess.check_output(
                ["git", "-C", str(canonical), "rev-parse", "refs/remotes/origin/main"],
                text=True,
            ).strip()
            live_pr = {
                "url": "https://github.com/MetaInFLow/example/pull/9",
                "number": 9,
                "body": created["body"],
                "head_ref": first["branch"],
                "head_commit": first["commit"],
                "head_repo": "MetaInFLow/example",
                "base_ref": "main",
                "base_commit": base_commit,
                "base_repo": "MetaInFLow/example",
            }

            with self.assertRaisesRegex(RuntimeError, "live head/base mismatch"):
                publish_target_upgrade(
                    self.publish_target(canonical),
                    home=root / "home",
                    wrapper_root=root / "wrapper",
                    latest_version="v0.13.0",
                    run_id="upgrade_wrong_existing_identity",
                    migrator=lambda *args, **kwargs: self.fail("migration must not rerun"),
                    existing_pr_resolver=lambda repo, branch: {
                        **live_pr,
                        "base_commit": "f" * 40,
                    },
                    pr_creator=lambda **kwargs: self.fail("PR must not be duplicated"),
                    origin_verifier=lambda repo, checkout: repo,
                    access_resolver=self.live_admin,
                    source_resolver=self.trusted_source,
                )

            second = publish_target_upgrade(
                self.publish_target(canonical),
                home=root / "home",
                wrapper_root=root / "wrapper",
                latest_version="v0.13.0",
                run_id="upgrade_pr_failure_retry",
                migrator=lambda *args, **kwargs: self.fail("migration must not rerun"),
                existing_pr_resolver=lambda repo, branch: live_pr,
                pr_creator=lambda **kwargs: self.fail("PR must not be duplicated"),
                origin_verifier=lambda repo, checkout: repo,
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(second["status"], "existing_pr")
            self.assertEqual(second["commit"], first["commit"])
            self.assertEqual(second["pr_url"], live_pr["url"])

    def test_same_name_open_pr_without_bound_identity_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, canonical = self.initialize_remote_repo(
                root,
                {"SKILL.md": "# Business Skill\n"},
            )

            with self.assertRaisesRegex(RuntimeError, "cannot be safely reused"):
                publish_target_upgrade(
                    self.publish_target(canonical),
                    home=root / "home",
                    wrapper_root=root / "wrapper",
                    latest_version="v0.13.0",
                    run_id="upgrade_unbound_existing_pr",
                    migrator=lambda *args, **kwargs: self.fail("migration must not run"),
                    existing_pr_resolver=lambda repo, branch: (
                        "https://github.com/MetaInFLow/example/pull/10"
                    ),
                    pr_creator=lambda **kwargs: self.fail("PR must not be created"),
                    origin_verifier=lambda repo, checkout: repo,
                    access_resolver=self.live_admin,
                    source_resolver=self.trusted_source,
                )

    def test_real_publisher_output_passes_official_upgrade_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            canonical = root / "canonical"
            candidate = root / "candidate"
            home = root / "home"
            repo = "MetaInFLow/example-skill"
            canonical.mkdir()
            replacements = {
                "DATE": "2026-07-31",
                "INITIAL_VERSION": "v0.1.0",
                "CURRENT_VERSION": "v0.1.0",
                "REPO_NAME": repo,
                "REPO_URL": f"https://github.com/{repo}",
                "SKILL_NAME": "example-skill",
                "VISIBILITY": "private",
                "WRAPPER_VERSION": "v0.14.0",
            }
            copy_templates(canonical, replacements, force=False)
            (canonical / "SKILL.md").write_text(
                "---\nname: example-skill\n---\n"
                "# Example Skill\n\n"
                f"{build_harness_activation_block()}\n\n"
                "Business instructions stay byte-stable.\n",
                encoding="utf-8",
            )
            hooks_path = canonical / ".codex/hooks.json"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks["hooks"]["SessionStart"].insert(
                0,
                {
                    "matcher": "custom",
                    "hooks": [{"type": "command", "command": "echo target-owned"}],
                },
            )
            hooks_path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
            integration = detect_target_architecture(canonical)["integration"]
            write_wrapper_manifest(
                canonical,
                build_wrapper_manifest(
                    repo,
                    "v0.14.0",
                    WRAPPER_MANAGED_FILES,
                    [],
                    instruction_surface="SKILL.md",
                    integration=integration,
                ),
            )

            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(canonical)], check=True)
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
            subprocess.run(
                ["git", "-C", str(canonical), "remote", "add", "origin", str(remote)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(canonical), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
            base_sha = subprocess.check_output(
                ["git", "-C", str(canonical), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            created_pr: dict[str, str] = {}

            def create_pr(**kwargs):
                created_pr.update(kwargs)
                return "https://github.com/MetaInFLow/example-skill/pull/7"

            report = publish_target_upgrade(
                {
                    "repo": repo,
                    "target": str(canonical),
                    "wrapper_version": "v0.14.0",
                    "github": {
                        "viewer": "anthonyf",
                        "permission": "ADMIN",
                        "is_admin": True,
                        "default_branch": "main",
                        "url": f"https://github.com/{repo}",
                    },
                },
                home=home,
                wrapper_root=ROOT,
                latest_version="v0.15.0",
                run_id="upgrade_official_gate",
                existing_pr_resolver=lambda repo, branch: None,
                pr_creator=create_pr,
                origin_verifier=lambda repo, checkout: repo,
                access_resolver=self.live_admin,
                source_resolver=self.trusted_source,
            )

            self.assertEqual(report["status"], "published")
            self.assertEqual(report["branch"], "evozeus/harness-v0.14.0-to-v0.15.0")
            self.assertIn("official_harness_upgrade", created_pr["body"])
            self.assertIn("MetaInFLow/EvoZeus-CoEvolve@v0.15.0", created_pr["body"])
            self.assertIn(f"- Source revision: `{'d' * 40}`", created_pr["body"])
            self.assertIn(f"- Contract manifest SHA-256: `{'e' * 64}`", created_pr["body"])
            self.assertIn("evozeus-harness-upgrade-plan:v1", created_pr["body"])
            self.assertIn(f"- Branch: `{report['branch']}`", created_pr["body"])
            self.assertNotIn(".evozeus-wrapper/CHANGELOG.md", report["changed_files"])
            for path in report["changed_files"]:
                self.assertIn(f"- `{path}`", created_pr["body"])

            subprocess.run(
                ["git", "clone", "-q", "--branch", report["branch"], str(remote), str(candidate)],
                check=True,
            )
            diff_lines = subprocess.check_output(
                ["git", "-C", str(candidate), "diff", "--name-status", base_sha, report["commit"]],
                text=True,
            ).splitlines()
            changed_entries = []
            for line in diff_lines:
                status, path = line.split("\t", 1)
                changed_entries.append(
                    {
                        "filename": path,
                        "status": "added" if status == "A" else "modified",
                    }
                )
            self.assertEqual(
                {entry["filename"] for entry in changed_entries},
                set(report["changed_files"]),
            )

            def github_api(command, **kwargs):
                endpoint = command[2]
                if "/collaborators/" in endpoint:
                    payload = {
                        "permission": "admin",
                        "user": {"permissions": {"admin": True}},
                    }
                elif endpoint.endswith("/releases/tags/v0.15.0"):
                    payload = {
                        "tag_name": "v0.15.0",
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-07-31T00:00:00Z",
                    }
                elif "/pulls/7/files?" in endpoint:
                    payload = changed_entries
                else:
                    source_path = endpoint.split("/contents/", 1)[1].split("?ref=", 1)[0]
                    self.assertIn(source_path, TRUSTED_CONTROL_SOURCES.values())
                    payload = {
                        "content": base64.b64encode((ROOT / source_path).read_bytes()).decode("ascii")
                    }
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

            mode = check_trusted_pr_checkouts(
                candidate,
                canonical,
                github_head_sha=report["commit"],
                github_base_sha=base_sha,
                github_repository=repo,
                github_head_repo=repo,
                github_actor="anthonyf",
                github_head_ref=report["branch"],
                github_pr_number=7,
                github_api_runner=github_api,
            )
            self.assertEqual(mode, "official_harness_upgrade")


if __name__ == "__main__":
    unittest.main()
