# EvoZeus Harness 全局预检与 Skill 入口预检设计

## Related Issue

- [EvoZeus-CoEvolve #12](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/12)
- 当前缺陷：target repo 内的 `.codex/hooks.json` 只在 canonical repo 作为活动项目时生效，却被 manifest 描述成已覆盖任意 consumer workspace 的 Skill 调用。
- 目标：准确表达每种检查能力的作用域，并提供当前 Codex 能支持的最强组合方案。

2026-07-28 补充：Issue #29 在 user-level command 上增加 `UserPromptSubmit` Lesson watcher。CoEvolve 负责注册、目标 inventory、活动产品渠道组件发现与 fail-open transport；Session Signal companion 负责普通 Chat 候选判断、目标选择和模型指引。该能力仍不承担 exact Skill invocation enforcement；详细合同见 [`2026-07-28-global-normal-chat-lesson-watcher.md`](../superpowers/specs/2026-07-28-global-normal-chat-lesson-watcher.md)。

## 平台事实

当前 Codex 没有 `SkillInvoke` 或等价的 Skill 选择生命周期事件。现有事件包括 `SessionStart`、`UserPromptSubmit`、`PreToolUse` 等，但都不能精确表达“Agent 已选中某个 Skill”。

| 方案 | 原生强制 | 精确绑定某个 Skill | 实际时机 |
|---|---|---|---|
| 全局 `SessionStart` dispatcher | 是 | 否 | 每个任务启动时检查全部 wrapped Skills |
| 全局 `UserPromptSubmit` Lesson watcher | 是（逐轮执行） | 否 | Skill 选择前把当前轮次转发给已验证 Session Signal companion；fail-open、零持久化 |
| `SKILL.md` 入口 preflight | 否 | 基本是 | Agent 选中 Skill 后按指令检查 |
| MCP/tool 网关 | 可强制 | 仅限工具化路径 | 具体工具调用前 |
| 未来 `SkillInvoke` 事件 | 是 | 是 | Skill 被选中时 |

补充边界：

- `UserPromptSubmit` 发生在 Skill 选择前，且该事件不能依靠 matcher 精确绑定 Skill。因此 CoEvolve 只提供注册目标 inventory 与可信 companion transport，方法判断归 Session Signal，不作为 exact Skill invocation enforcement。
- MCP 不必取消自然语言入口，但只有统一进入 MCP/tool 的关键执行路径才能被 `PreToolUse` 强制拦截。纯分析型 Skill 不适合强制工具化。
- Plugin 可以提供稳定的 hook 分发路径，但仍受现有生命周期事件限制，不等于拥有 `SkillInvoke`。
- target repo 内的 project hook 对 canonical repo 维护仍然有价值，但不覆盖 consumer workspace 中的 installed-Skill 调用。

## 决策

Issue #12 拆成两层目标。

### 当前可实现

1. 把 project hook 明确标记为 `repo_maintenance_hook`，不得再作为 Skill invocation coverage 的证据。
2. 安装一个 user-level `SessionStart` dispatcher，在任意 workspace 启动任务时聚合检查全部 wrapped Skills。
3. 在每个 wrapped Skill 的 instruction surface 保留入口 preflight，使 Agent 选中该 Skill 后再校验该 Skill 的 source contract 和 release 状态。
4. 全局 dispatcher 发现兼容的落后 harness 时给出 advisory warning 并继续业务流程；普通 Skill 调用不授权任何 harness 维护写入。
5. manifest、诊断、preflight 和 reinstall 输出必须分别报告这两层能力，不再用一个布尔值混合表达。

### 当前无法精确实现

真正的 per-Skill native invocation hook 仍需要 Codex 提供 `SkillInvoke` 生命周期事件。在该事件出现前，不得把当前组合方案命名为 per-Skill invocation hook。

## 能力模型

`integration.mode` 继续描述 Skill invocation 本身的 enforcement，不描述仓库里是否存在任意 hook 文件。

```json
{
  "integration": {
    "mode": "prompt_runtime_check",
    "native_skill_invocation_hook_installed": false,
    "capabilities": {
      "repo_maintenance_hook": {
        "installed": true,
        "native_enforced": true,
        "event": "SessionStart",
        "scope": "canonical_repository",
        "covers_skill_invocation": false
      },
      "global_session_dispatcher": {
        "installed": false,
        "native_enforced": false,
        "event": "SessionStart",
        "scope": "all_registered_wrapped_skills",
        "covers_skill_invocation": false
      },
      "skill_entry_preflight": {
        "installed": true,
        "native_enforced": false,
        "scope": "selected_skill_instruction_surface",
        "covers_skill_invocation": true
      },
      "skill_invocation_hook": {
        "supported": false,
        "installed": false,
        "event": null
      }
    }
  }
}
```

target manifest 是 portable harness 声明，因此其中 `global_session_dispatcher.installed` 不代表某个用户环境。`skill diagnose` 在运行时读取 `~/.evozeus/hooks/state.json` 与 `~/.codex/hooks.json`，再用 live installed/trust 状态覆盖诊断输出；portable manifest 本身保持未安装状态。

兼容规则：

- 旧字段 `native_host_hook_installed` 在一个 release 周期内保留，但只能在有明确 `scope` 时读取，并标记 deprecated。
- 仅存在 `.codex/hooks.json` 和 repo-local adapter 时，`integration.mode` 必须是 `prompt_runtime_check`，`native_skill_invocation_hook_installed` 必须是 `false`。
- preflight 必须拒绝只凭 project hook 声称 native Skill invocation coverage 的 manifest。
- Plugin lifecycle、MCP gateway 和未来 `SkillInvoke` 必须分别建模，不能合并成模糊的 `native_host_hook`。

## 全局 Dispatcher

### 安装位置

- Hook 配置：`~/.codex/hooks.json`
- 稳定入口：`~/.evozeus/hooks/evozeus_wrapper_dispatcher.py`
- 状态：`~/.evozeus/hooks/state.json`
- 版本缓存：`~/.evozeus/cache/evozeus-wrapper-latest.json`
- 备份：`~/.evozeus/backups/global-hooks/<transaction-id>/`

全局 command 只能引用稳定入口，不能使用 `git rev-parse --show-toplevel`，也不能依赖当前 workspace。

### 发现范围

dispatcher 以 `~/.evozeus/.projects/OWNER/REPO` 为 wrapped target 注册事实源：

1. 只读取有效 project pointer。
2. 只接纳含 `.evozeus-wrapper/wrapper.json` 的 canonical repo。
3. 校验 manifest 的 `canonical_repo` 与 pointer owner/repo 一致。
4. 聚合比较所有 target 的 `wrapper_version` 与 authoritative EvoZeus-CoEvolve latest release。
5. 不在 hook 输出中暴露 Skill 名称、本地路径、客户数据或 manifest 业务内容。

### 运行与维护授权

当 latest release 已确定，且至少一个 target harness 落后时，dispatcher 返回：

```json
{
  "continue": true,
  "systemMessage": "检测到 3 个 EvoZeus harness 落后，最新版本为 v0.10.0。正常业务继续；普通 Skill 调用不授权 Harness 维护写入。只有用户明确请求维护后，才生成 dry-run 方案并单独确认写入。",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "evozeus_global_gate=allow; next_action=continue_business_without_harness_writes"
  }
}
```

具体 target 列表只在用户明确请求维护后由 upgrade plan 展示，不进入全局 hook 输出。普通业务 Prompt、Skill 被选中或用户未声明“只读”均不构成维护授权。

网络失败处理：

- 有最近成功缓存时使用缓存。
- 没有可用缓存且无法确定 latest release 时，输出警告并放行；未知状态不能伪装成“已是最新”，也不能因一次网络故障永久锁死 Codex。
- manifest 无效、pointer/source contract 冲突等本地确定性错误仍严格阻断，并给出 repair next action。

### 性能与噪音

- 每个 `SessionStart` 最多做一次 latest release 查询，不按 Skill 重复请求。
- latest release 使用有时效的成功缓存；本地 pointer/manifest 检查保持只读。
- 正常状态不逐个输出 Skill；只返回一个聚合 allow 结果。
- `startup` 和 `resume` 是当前明确支持的 SessionStart source；不宣称未验证 source 已覆盖。

## Skill 入口 Preflight

每个 wrapped Skill 的 instruction surface 在业务主链路前执行自身 preflight：

1. 读取自身 manifest。
2. 确认 canonical project pointer、origin 和 runtime install 指向同一 source。
3. 确认 Skill release 与 target changelog/release contract。
4. 全局 dispatcher 已检查 harness latest 时可复用本 session context，避免重复远端查询；自身 source contract 仍需校验。
5. source contract 损坏、manifest 无效、迁移冲突或已确认不兼容时停在 Skill 业务逻辑之前；兼容版本落后只提示并继续。

这层能力必须命名为 `skill_entry_preflight` 或 `prompt_runtime_check`，不能称为 native hook。

## Project Hook 去重

canonical repo 内继续保留 project-local `SessionStart` hook，用于仓库维护。

- 全局 dispatcher 仍聚合检查全部 wrapped targets，不能仅凭当前工作目录或 hook 文件存在就假设 project hook 已获 Codex trust。
- global/project 两个 adapter 共享 latest release 成功缓存；无论执行顺序如何，一个 `SessionStart` 最多做一次远端查询，后执行者只做本地比较。
- project hook 只检查当前 canonical repo，不扫描全局 target。
- manifest 分别记录 global dispatcher 与 project hook 的安装、scope 和 trust 状态；诊断明确报告两者同时存在，但不把两个 registration 当成两份 invocation coverage。

## 安装、信任与回滚

新增 lifecycle：

```text
hook global plan
  -> hook global install --approve
  -> /hooks trust review
  -> hook global status
  -> hook global uninstall --approve
```

要求：

- dry-run 必须先解析现有 `~/.codex/hooks.json`；非法 JSON 在任何写入前阻断。
- install 使用结构化 merge，保留所有 unrelated hooks，并保证只有一个 EvoZeus global dispatcher registration。
- 文件安装状态与 Codex trust 状态分开持久化；刚安装或 command 变化后状态为 `pending_review`，不能报告 `trusted`。
- 重复 install 幂等，不重复 registration，不覆盖无关配置。
- 写入前备份 hooks config、dispatcher 和 state；任一步失败都恢复完整备份。
- uninstall 只移除 EvoZeus registration 和 EvoZeus-owned runtime files；保留 unrelated hooks。
- project/global 同时存在时报告 duplicate handling 状态，不把两个 registration 当成两份 invocation coverage。

## 升级全部落后 Harness

用户回复“升级全部”后执行两阶段事务：

1. **Plan**：发现全部落后 targets，确认显式版本与 dispatcher cache、环境 override 或 GitHub latest release 一致，并检查 canonical repo、clean Git worktree、manifest、迁移冲突和完整 write set 写权限；任何 target 不可安全升级时保持零写入。
2. **Apply**：为所有可能被迁移改写、移动或删除的文件建立 transaction backup，包括 target-owned 文件中的 legacy wrapper 路径引用；逐个应用最新 harness migration并运行 target preflight，任一步失败时回滚本事务已经修改的 target。
3. **Finalize**：全部 target 验证通过后，刷新 global dispatcher/state；不自动提交 target 业务文件。

升级报告可以在用户确认后的本地命令输出中列出 target，但不得进入全局 hook 的自动输出。

## MCP/Tool 路径

MCP/tool gateway 不是本次默认迁移目标。只有满足以下条件的 Skill 才可额外使用：

- 关键副作用已经统一封装为一个或少量 MCP tools。
- 绕过工具不会完成核心任务。
- `PreToolUse` 能明确识别目标 tool。

纯分析、写作、判断型 Skill 继续使用 Skill 入口 preflight，不为追求 native hook 强行工具化。

## 验证计划

### Manifest 与 Preflight

- project hook 文件存在时，不得得到 native Skill invocation coverage。
- manifest 混淆 project/global/invocation scope 时 preflight 失败。
- Plugin、MCP/tool 和 prompt entry 能力保持独立字段。

### Consumer Workspace

- 在 canonical repo 外启动 synthetic consumer workspace，确认 user-level dispatcher 被发现。
- 未注册 global hook 时，consumer workspace 只剩 Skill 入口 preflight。
- canonical repo 内同时存在 global/project hook 时，验证去重路径。

### Global Hook Lifecycle

- 空 hooks config 安装。
- 已有 unrelated hooks 时结构化 merge 和卸载保留。
- 非法 hooks JSON 零写入。
- trust `pending_review` 与 file `installed` 分开报告。
- 重复安装幂等。
- 模拟中途写失败后完整回滚。
- command 不依赖 active git repository。

### Runtime Authorization Gate

- 全部 target 最新时 allow。
- 一个或多个 compatible target 落后时 warn/allow，并只输出数量和 latest version。
- 普通 Skill 调用不授权 Harness 写入；用户明确请求维护后才进入 upgrade dry-run。
- latest 查询失败但有缓存时使用缓存。
- latest 查询失败且无缓存时 warn/allow，不伪造 latest。
- 本地 manifest/source contract 确定性错误时 block。

### Upgrade All

- 多 target 全部可升级时统一 apply 并验证。
- 任一 target 无法验证 clean Git、不可写、dirty 或 conflict 时 plan 零写入。
- apply 中途失败时恢复所有已修改 target。
- 升级后再次运行结果幂等。

## 发布与迁移

- 该变更新增全局 runtime capability 和 manifest capability model，计划发布 `v0.10.0`。
- v0.9.x target 升级后保留 project hook，但其语义改为 `repo_maintenance_hook`。
- global dispatcher 为显式 opt-in 安装；wrapper upgrade 不得静默修改 `~/.codex/hooks.json`。
- release notes 必须明确：当前仍没有 native per-Skill invocation hook；组合方案是 global session enforcement + Skill entry preflight。

## 回滚

- global hook 可用 `hook global uninstall --approve` 移除，并从 transaction backup 恢复安装前配置。
- target harness 升级使用 wrapper-managed file backup 回滚，不触碰目标 Skill 业务内容。
- 回滚 global dispatcher 后，Skill 入口 preflight 仍保留为 fallback；manifest 必须相应降级 global capability 状态。
