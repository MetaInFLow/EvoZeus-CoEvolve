# Global Dispatcher And Skill Entry Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Issue #12 的 hook 作用域夸大，并交付 user-level `SessionStart` dispatcher、Skill 入口 preflight 与“升级全部落后 harness”的可回滚流程。

**Architecture:** `integration.mode` 只描述 Skill invocation enforcement；project hook、global dispatcher、Skill entry preflight 和未来 SkillInvoke 分别记录为 capabilities。全局配置生命周期放进独立模块，稳定 dispatcher 作为可复制的标准库 Python 脚本运行；批量升级先全量计划，再备份 wrapper-managed surface 后应用和回滚。

**Tech Stack:** Python 3 标准库、`unittest`/`pytest`、Codex JSON hooks、现有 EvoZeus-CoEvolve lifecycle/preflight。

---

### Task 1: 修正 Integration Capability Model

**Files:**
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `scripts/evozeus_wrapper_preflight.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: 写 project hook 作用域失败测试**

```python
def test_project_hook_is_repo_maintenance_not_skill_invocation(self):
    integration = classify_integration_mode(
        target_kind="single_skill",
        root_entry="SKILL.md",
        hook_files=[CODEX_HOOKS_CONFIG, CODEX_START_HOOK_SCRIPT],
        plugin_manifests=[],
        skill_entries=[],
    )
    self.assertEqual(integration["mode"], "prompt_runtime_check")
    self.assertFalse(integration["native_skill_invocation_hook_installed"])
    self.assertTrue(integration["capabilities"]["repo_maintenance_hook"]["installed"])
    self.assertFalse(
        integration["capabilities"]["repo_maintenance_hook"]["covers_skill_invocation"]
    )
```

- [ ] **Step 2: 写 preflight 拒绝能力夸大的失败测试**

```python
def test_preflight_rejects_native_invocation_claim_backed_only_by_project_hook(self):
    manifest = {
        "integration": {
            "mode": "native_host_hook",
            "native_host_hook_installed": True,
        }
    }
    with self.assertRaises(SystemExit):
        check_integration_contract(target_with_project_hook, manifest)
```

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_evozeus_wrapper_lifecycle.PreflightContractTest.test_project_hook_is_repo_maintenance_not_skill_invocation \
  tests.test_evozeus_wrapper_lifecycle.PreflightContractTest.test_preflight_rejects_native_invocation_claim_backed_only_by_project_hook
```

Expected: project hook 仍返回 `native_host_hook`，测试失败。

- [ ] **Step 4: 实现 additive capability model**

```python
capabilities = {
    "repo_maintenance_hook": {
        "installed": codex_project_hook,
        "native_enforced": codex_project_hook,
        "event": CODEX_START_HOOK_EVENT if codex_project_hook else None,
        "scope": "canonical_repository",
        "covers_skill_invocation": False,
    },
    "global_session_dispatcher": {
        "installed": False,
        "native_enforced": False,
        "event": CODEX_START_HOOK_EVENT,
        "scope": "all_registered_wrapped_skills",
        "covers_skill_invocation": False,
    },
    "skill_entry_preflight": {
        "installed": bool(root_entry),
        "native_enforced": False,
        "scope": "selected_skill_instruction_surface",
        "covers_skill_invocation": bool(root_entry),
    },
    "skill_invocation_hook": {
        "supported": False,
        "installed": False,
        "event": None,
    },
}
```

`mode` 在 project hook-only 情况保持 `prompt_runtime_check`；旧 `native_host_hook_installed` 保留为 deprecated alias，但不得因 project hook 置真。

- [ ] **Step 5: 收紧 preflight**

`check_integration_contract()` 必须校验：

```python
if mode == "native_host_hook" and not integration.get(
    "native_skill_invocation_hook_installed", False
):
    fail("native Skill invocation coverage requires explicit invocation-hook evidence")
```

同时验证 capability 的 `scope` 与 `covers_skill_invocation` 一致。

- [ ] **Step 6: 运行 focused 和 full tests**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
```

Expected: PASS；旧 native project-hook expectations 已更新为 capability assertions。

- [ ] **Step 7: Commit**

```bash
git add scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "fix: distinguish hook capability scopes"
```

### Task 2: 实现 Global Hook 安装生命周期

**Files:**
- Create: `scripts/evozeus_wrapper_global_hook.py`
- Create: `templates/global/evozeus_wrapper_dispatcher.py`
- Modify: `scripts/evozeus_wrapper.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: 写 plan/install/status/uninstall 失败测试**

覆盖：空配置、已有 unrelated hook、非法 JSON、pending trust、重复安装和回滚。

```python
def test_global_hook_install_preserves_unrelated_hooks_and_is_idempotent(self):
    hooks_path.write_text(json.dumps(unrelated), encoding="utf-8")
    first = apply_global_hook_install(home, wrapper_root, approve=True)
    second = apply_global_hook_install(home, wrapper_root, approve=True)
    merged = json.loads(hooks_path.read_text(encoding="utf-8"))
    self.assertEqual(first["status"], "installed")
    self.assertEqual(second["status"], "already_installed")
    self.assertEqual(count_dispatcher_entries(merged), 1)
    self.assertIn("PreToolUse", merged["hooks"])
```

- [ ] **Step 2: 运行测试并确认 RED**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.GlobalHookLifecycleTest -v
```

Expected: import/function not found。

- [ ] **Step 3: 实现 stable paths 和结构化 merge**

```python
GLOBAL_HOOKS_CONFIG = Path(".codex/hooks.json")
GLOBAL_DISPATCHER = Path(".evozeus/hooks/evozeus_wrapper_dispatcher.py")
GLOBAL_HOOK_STATE = Path(".evozeus/hooks/state.json")
GLOBAL_COMMAND = '/usr/bin/python3 "$HOME/.evozeus/hooks/evozeus_wrapper_dispatcher.py"'
```

`plan_global_hook_install()` 只读解析现状；`apply_global_hook_install(..., approve=True)` 先备份，再 merge exactly one dispatcher registration，复制 stable dispatcher，写 `trust_status=pending_review`。

- [ ] **Step 4: 实现 uninstall 与 rollback**

uninstall 只删除命令等于 `GLOBAL_COMMAND` 的 registration。安装或卸载任一步异常时，从 transaction backup 恢复 hooks config、dispatcher 和 state。

- [ ] **Step 5: 接入 CLI**

```text
python3 scripts/evozeus_wrapper.py hook global plan --json
python3 scripts/evozeus_wrapper.py hook global install --approve --json
python3 scripts/evozeus_wrapper.py hook global status --json
python3 scripts/evozeus_wrapper.py hook global trust --status trusted --approve --json
python3 scripts/evozeus_wrapper.py hook global uninstall --approve --json
```

`trust` 只记录用户已在 `/hooks` 完成审核的事实，不尝试伪造或修改 Codex 内部 trust store。

- [ ] **Step 6: 运行生命周期测试**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.GlobalHookLifecycleTest -v
```

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add scripts/evozeus_wrapper_global_hook.py templates/global/evozeus_wrapper_dispatcher.py scripts/evozeus_wrapper.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: add global wrapper hook lifecycle"
```

### Task 3: 实现严格 Global Dispatcher

**Files:**
- Modify: `templates/global/evozeus_wrapper_dispatcher.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: 写 dispatcher RED tests**

```python
def test_dispatcher_blocks_with_aggregate_count_when_targets_are_stale(self):
    payload = evaluate_session_start(
        home=home,
        latest_resolver=lambda: {"version": "v0.10.0", "source": "remote"},
    )
    self.assertFalse(payload["continue"])
    self.assertIn("2 个 EvoZeus harness 落后", payload["stopReason"])
    self.assertNotIn("private-skill", json.dumps(payload))
```

再覆盖：全部最新、cache fallback、无 cache 网络失败、本地 manifest 错误、canonical workspace 去重和 consumer workspace。

- [ ] **Step 2: 运行测试并确认 RED**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.GlobalDispatcherTest -v
```

Expected: evaluation API 缺失。

- [ ] **Step 3: 实现 target discovery 与单次 latest resolve**

dispatcher 扫描 `~/.evozeus/.projects/*/*`，只读取 pointer 和 manifest；latest release 每个事件最多解析一次，成功结果写 cache。

- [ ] **Step 4: 实现严格 hook payload**

```python
def blocked_upgrade_payload(outdated_count: int, latest: str) -> dict[str, Any]:
    return {
        "continue": False,
        "stopReason": (
            f"检测到 {outdated_count} 个 EvoZeus harness 落后，"
            f"最新版本为 {latest}。是否升级全部？"
        ),
        "systemMessage": "回复‘升级全部’执行统一预检与升级；回复‘稍后’仅跳过本次任务。",
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "next_action=evozeus_harness_upgrade_all",
        },
    }
```

未知 remote 且无 cache 时 warn/allow；确定性 manifest/source 错误 block。

- [ ] **Step 5: 运行 dispatcher 与 consumer workspace tests**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.GlobalDispatcherTest -v
```

Expected: PASS，输出不含 target 名称或路径。

- [ ] **Step 6: Commit**

```bash
git add templates/global/evozeus_wrapper_dispatcher.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: enforce global harness session gate"
```

### Task 4: 更新 Target Manifest 与 Skill Entry Prelude

**Files:**
- Modify: `scripts/evozeus_wrapper_bootstrap.py`
- Modify: `scripts/evozeus_wrapper_lifecycle.py`
- Modify: `templates/target/WRAPPER.md`
- Modify: `templates/target/docs/index.md`
- Modify: `templates/target/docs/onboarding.md`
- Modify: `templates/target/docs/wrapper-migrations/README.md`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: 写 bootstrap/migration RED tests**

验证新 target 和 v0.9.1 migration 都得到：

```python
self.assertEqual(manifest["integration"]["mode"], "prompt_runtime_check")
self.assertTrue(
    manifest["integration"]["capabilities"]["repo_maintenance_hook"]["installed"]
)
self.assertFalse(manifest["integration"]["native_skill_invocation_hook_installed"])
self.assertIn("Skill 入口 preflight", skill_text)
```

- [ ] **Step 2: 运行测试并确认 RED**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.LayoutMigrationTest -v
```

Expected: 旧 manifest 仍声称 native hook。

- [ ] **Step 3: 更新 status prelude 与 manifest 构建**

状态段明确三项：repo maintenance hook scope、global dispatcher state、Skill entry preflight。`build_wrapper_manifest()` 接收 global status，但不因 project 文件推断 global installation。

- [ ] **Step 4: 更新 target templates**

所有模板统一使用 capability 名称；删除“project-local hook 等于 installed Skill native coverage”的表述。

- [ ] **Step 5: 运行 migration、structure、runtime tests**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle -v
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_lifecycle.py templates/target tests/test_evozeus_wrapper_lifecycle.py
git commit -m "fix: emit scoped target harness capabilities"
```

### Task 5: 实现 Upgrade-All 事务

**Files:**
- Modify: `scripts/evozeus_wrapper_global_hook.py`
- Modify: `scripts/evozeus_wrapper.py`
- Modify: `tests/test_evozeus_wrapper_lifecycle.py`

- [ ] **Step 1: 写 multi-target RED tests**

```python
def test_upgrade_all_prevalidates_every_target_before_writing(self):
    dirty_target = create_wrapped_target(home, "dirty", "v0.9.1", dirty=True)
    clean_target = create_wrapped_target(home, "clean", "v0.9.1")
    report = apply_upgrade_all(home, wrapper_root, "v0.10.0", approve=True)
    self.assertEqual(report["status"], "blocked")
    self.assertFalse(report["writes"])
    self.assertEqual(read_version(clean_target), "v0.9.1")
```

另写 apply 中途失败恢复两个 target、重复运行幂等测试。

- [ ] **Step 2: 运行测试并确认 RED**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.UpgradeAllHarnessTest -v
```

Expected: upgrade-all API 缺失。

- [ ] **Step 3: 实现 plan**

`plan_upgrade_all()` 必须：解析 authoritative latest、确认当前 wrapper source 已是 latest、发现落后 targets、要求 clean worktree、调用每个 target migration plan，并在任何 conflict 时返回零写入。

- [ ] **Step 4: 实现 apply 和 rollback**

为 instruction surface、manifest、project hook、wrapper-managed files、legacy move sources 和 migration record 建 transaction snapshot。应用后逐 target 跑 structure/runtime preflight；异常时按 snapshot 恢复所有已改 target。

- [ ] **Step 5: 接入 CLI**

```text
python3 scripts/evozeus_wrapper.py harness upgrade-all --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --approve --json
```

- [ ] **Step 6: 运行 upgrade-all tests**

```bash
python3 -m unittest tests.test_evozeus_wrapper_lifecycle.UpgradeAllHarnessTest -v
```

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper.py tests/test_evozeus_wrapper_lifecycle.py
git commit -m "feat: upgrade wrapped harnesses transactionally"
```

### Task 6: 文档、Release 与端到端验证

**Files:**
- Modify: `README.md`
- Modify: `docs/harness-contract.md`
- Modify: `docs/specs/2026-07-07-runtime-integration-modes.md`
- Modify: `skills/using-evozeus-wrapper/SKILL.md`
- Modify: `skills/status-assessment/SKILL.md`
- Modify: `skills/target-skill-diagnosis/SKILL.md`
- Modify: `skills/publish-reinstall/SKILL.md`
- Modify: `skills/harness-upgrade/SKILL.md`
- Modify: `CHANGELOG.md`
- Create: `docs/releases/v0.10.0.md`

- [ ] **Step 1: 更新用户与维护文档**

文档必须明确：

- global dispatcher 是原生 `SessionStart` 聚合检查，不是 per-Skill hook；
- Skill entry preflight 基本绑定被选中的 Skill，但不是 native enforcement；
- MCP/tool 只覆盖工具化路径；Plugin 不新增 lifecycle event；
- 真正 native per-Skill hook 等待 `SkillInvoke`。

- [ ] **Step 2: 更新 changelog/release notes**

`v0.10.0` release notes 关联 Issue #12，列出安装、信任、性能、严格阻断、升级全部和回滚行为。

- [ ] **Step 3: 运行完整验证**

```bash
python3 -m pytest -q
python3 -m py_compile \
  scripts/evozeus_wrapper.py \
  scripts/evozeus_wrapper_bootstrap.py \
  scripts/evozeus_wrapper_lifecycle.py \
  scripts/evozeus_wrapper_preflight.py \
  scripts/evozeus_wrapper_global_hook.py \
  templates/global/evozeus_wrapper_dispatcher.py \
  templates/target/.codex/hooks/evozeus_wrapper_start_check.py
python3 scripts/evozeus_wrapper.py hook global plan --json
```

Expected: all tests PASS；compile PASS；global plan `writes=false`。

- [ ] **Step 4: 临时 HOME consumer-workspace smoke test**

在临时 HOME 安装 global hook，创建两个 synthetic wrapped targets，从 canonical repo 外执行 dispatcher；验证 current/stale/rollback/uninstall 全链路，且 unrelated hook 内容保持一致。

- [ ] **Step 5: Commit**

```bash
git add README.md docs skills CHANGELOG.md docs/releases/v0.10.0.md
git commit -m "docs: document global harness enforcement"
```

- [ ] **Step 6: PR readiness**

```bash
git status --short
git log --oneline origin/main..HEAD
git diff --check origin/main...HEAD
```

Expected: 仅本 Issue 相关文件；无 whitespace errors；测试证据已写入 PR body。
