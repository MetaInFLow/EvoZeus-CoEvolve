# EvoZeus 调用身份头与反馈捕获标志设计

状态：已被 [目标 Skill Notice 系统](2026-07-27-target-skill-notice-system.md) 扩展；本文件保留 v0.12.1 历史决策

受众：EvoZeus-CoEvolve 维护者、受管 Skill owner、Harness 验收人

用途：定义受 EvoZeus-CoEvolve 管理的 Skill 在每次调用开始时如何声明维护身份、事实源、双版本轴和运行渠道，并约束反馈捕获后的用户可见状态与授权边界。

## 1. 真问题

当前 CoEvolve Harness 能检查 canonical repo、Skill release、Harness version 和反馈信号，但没有统一的用户可见身份头。用户无法在调用开始时确认：

- 当前 Skill 是否由 EvoZeus 管理自进化；
- 维护事实源在哪里；
- 正在运行哪个 Skill release 与哪个 Harness version；
- 当前来源属于开发版、UAT 或正式版；
- 反馈被识别后是否已经发生外部写入。

反馈审计当前返回 `should_capture`、Issue 草案和 `next_action=create_or_confirm_feedback_issue`。这个结果缺少明确的展示合同，也没有把“识别信号”“提交 Issue”“启动修复”三个授权阶段隔离得足够醒目，Agent 容易把完整进化闭环当作一次授权。

## 2. 目标与成功标准

每次调用受管 Skill 时，第一条用户可见输出只出现一次身份头：

```text
🧙🏻‍♂️ [EvoZeus 自进化维护] [MetaInFLow/metainflow-developer-onboarding](https://github.com/MetaInFLow/metainflow-developer-onboarding) · Skill v0.1.1 · Harness v0.12.1 · 正式版
```

成功标准：

1. 用户在业务内容前看到维护者、可点击事实源、Skill 版本、Harness 版本和渠道。
2. 每次 Skill invocation 只展示一次，不在同一次调用的 commentary、工具进度和 final 中重复。
3. 渠道固定为 `开发版`、`UAT`、`正式版` 三态；证据不足时保守显示 `开发版`。
4. 使用原生 Unicode `🧙🏻‍♂️`，不输出 HTML、自定义图片语法或依赖宿主支持的 shortcode。
5. 反馈捕获只声明本地待确认；未获单独授权时不创建 Issue、不设计修复、不创建 PR。

## 3. 已选方案

采用“入口 preflight 生成事实，Skill instruction surface 原样展示”的组合方案。

原因：

- 只靠提示词拼接身份头无法可靠判断版本和渠道。
- 当前宿主没有原生 `SkillInvoke` 事件，全局 `SessionStart` dispatcher 无法精确知道本轮选中的 Skill。
- preflight 可以从现有 manifest、release 和 Git 状态生成可测试的结构化事实；目标 Skill 只负责把 `display_line` 放在首次用户可见输出的第一行。

全局 dispatcher 继续负责 SessionStart 的聚合健康和版本提醒，不承担具体 Skill 的身份头渲染。

## 4. 用户可见合同

### 4.1 调用身份头

固定格式：

```text
🧙🏻‍♂️ [EvoZeus 自进化维护] [<OWNER/REPO>](https://github.com/<OWNER/REPO>) · Skill <skill_release> · Harness <harness_version> · <channel_label>
```

展示规则：

- 每次受管 Skill invocation 展示一次。
- 必须位于该 invocation 第一条用户可见输出的第一行。
- 后续 commentary、阶段更新和 final 不重复。
- 多个受管 Skill 在同一任务中先后被真实调用时，每个 Skill 各展示一次自己的身份头。
- 普通 EvoZeus SessionStart 检查、未选中目标 Skill 的任务和未接入 CoEvolve 的 Skill 不展示身份头。
- Header 后空一行，再进入业务输出。

### 4.2 反馈捕获标志

当本次 invocation 内识别到可复用的不满意、纠正或机制缺陷时，在身份头之后追加：

```text
🧙🏻‍♂️ [EvoZeus][进化信号已捕获｜本地待确认｜sig_7K2M]
```

标志含义：

- `已捕获`：已形成当前 invocation 内的脱敏结构化审计结果。
- `本地待确认`：尚未创建 GitHub Issue，也未启动修复流程。
- `sig_7K2M`：根据脱敏审计事实生成的本次调用短标识，便于用户在当前任务中确认；不得包含用户身份、repo 私有路径或原始会话内容。

首版不建立跨任务持久化 pending-signal ledger。`本地待确认` 描述当前 invocation 的授权状态，不代表已经写入磁盘。

捕获后的默认输出还要明确：

```text
处理边界：仅当前调用内捕获；未创建 Issue、未修改 Skill、未发起 PR。
当前任务：继续执行原业务。
```

## 5. 双版本轴

身份头必须同时显示两条版本轴：

| 字段 | 事实源 | 含义 |
|---|---|---|
| `Skill <version>` | GitHub latest release、目标 CHANGELOG 与本地 tag/HEAD 校验 | 目标 Skill 的业务行为版本 |
| `Harness <version>` | 目标 wrapper manifest 的 `wrapper_version` | 注入到该 Skill 的 CoEvolve Harness 版本 |

两条版本轴不能互相替代。Harness 落后时，身份头仍显示实际安装版本；在下一行追加 `当前 Harness → latest` advisory。普通调用不因此获得升级授权。

Skill 尚未发布时显示 `Skill 未发布`，渠道固定为 `开发版`。

## 6. 渠道判定

渠道按下列优先级判定：

1. `UAT`：source contract 通过，工作区干净，并且当前 commit 由 UAT manifest 明确引用，或处于受控 `uat/*` 渠道。
2. `正式版`：source contract 通过，工作区干净，当前 HEAD 与 GitHub latest release tag 指向同一 commit，Skill release 与 latest release 一致。
3. `开发版`：开发分支、工作区有改动、本地 commit 领先正式 release、Skill 未发布、网络或 release 事实无法核验，以及其他未满足前两项的情况。

判定必须 fail closed：任何不确定性都不得声称 `UAT` 或 `正式版`。

渠道描述的是当前目标 Skill 的运行来源。Harness freshness 通过独立版本轴和 advisory 表达。

## 7. 结构化输出

Skill entry preflight 新增版本化的 `runtime_identity`：

```json
{
  "schema_version": "v1",
  "managed_by": "EvoZeus-CoEvolve",
  "icon": "🧙🏻‍♂️",
  "canonical_repo": "MetaInFLow/metainflow-developer-onboarding",
  "canonical_url": "https://github.com/MetaInFLow/metainflow-developer-onboarding",
  "skill_release": "v0.1.1",
  "harness_version": "v0.12.1",
  "channel": "stable",
  "channel_label": "正式版",
  "display_once_scope": "skill_invocation",
  "display_line": "🧙🏻‍♂️ [EvoZeus 自进化维护] [MetaInFLow/metainflow-developer-onboarding](https://github.com/MetaInFLow/metainflow-developer-onboarding) · Skill v0.1.1 · Harness v0.12.1 · 正式版"
}
```

`channel` 机器值固定为 `development | uat | stable`；中文标签只用于展示。

目标 Skill instruction surface 必须要求 Agent：

1. 在入口 preflight 后读取 `runtime_identity.display_line`。
2. 在首次用户可见输出的第一行原样展示。
3. 在当前 invocation 的后续输出中抑制重复身份头。
4. preflight 无法生成可信 identity 时停止声称受管版本，按现有 source-contract 错误路径处理。

## 8. 组件边界

### Target preflight

负责读取 wrapper manifest、canonical repo、Skill release、Git 状态与渠道证据，生成 `runtime_identity`。应复用现有 release/version/source-contract 检查，避免为身份头重复发起网络请求。

### Target instruction surface

负责“首次第一行、每次 invocation 一次”的展示规则。该层仍属于 `prompt_runtime_check`，不能声称获得原生 Skill invocation enforcement。

### Feedback audit

负责生成脱敏 signal id、捕获展示字段和授权状态。默认结果保持 `writes=false`，下一步改为“继续业务并等待用户确认是否提交反馈”。

### Global dispatcher

保持 SessionStart 聚合检查职责。它可以提供 latest Harness advisory 上下文，但不输出目标 Skill 地址或伪造具体 invocation 身份头。

## 9. 授权状态机

```text
SIGNAL_DETECTED
  -> LOCAL_PENDING_CONFIRMATION
  -> ISSUE_SUBMITTED       （需要用户明确授权）
  -> FIX_AUTHORIZED        （需要新的明确授权）
  -> DESIGN_AND_PR
```

约束：

- 捕获信号不授权 GitHub 写入。
- 创建 Issue 不授权修改 Skill、创建分支、设计文档或 PR。
- 用户明确同意修复后，才进入 design 与 PR 流程。
- 发布与 Harness 升级继续沿用各自现有的单独授权门。

## 10. 错误处理

- manifest 缺失、canonical repo 冲突或 source contract 损坏：走现有硬错误，不生成看似可信的身份头。
- GitHub latest release 暂时不可用：显示 `开发版`，保留网络核验 advisory，继续兼容业务。
- Harness 落后但兼容：显示实际 Harness version，追加升级提醒，继续业务且不写 Harness。
- display line 已在当前 invocation 输出：后续阶段不得重复。
- 宿主不支持 Markdown link：保留可读的 `OWNER/REPO` 文本；禁止退回 HTML。

## 11. 验证计划

新增或扩展回归用例：

1. 正式 release、UAT commit、开发分支、dirty worktree、未发布 Skill、GitHub 不可用六类渠道判定。
2. `runtime_identity` schema、双版本轴、canonical URL 和中文标签精确匹配。
3. 身份头以 `🧙🏻‍♂️` 开头，不含 HTML、图片路径或自定义 shortcode。
4. 同一 invocation 只展示一次；下一次 invocation 再展示一次。
5. Harness 落后时显示当前版本与独立 advisory，不触发升级写入。
6. 捕获结果包含 signal id、`writes=false` 和 `LOCAL_PENDING_CONFIRMATION`。
7. 用户只授权 Issue 时不生成 design/PR 动作；单独授权修复后才进入后续状态。
8. managed template 升级可重复执行，保持目标 Skill 业务字节不变。
9. 完整运行现有 lifecycle、contract bundle、preflight 和 dispatcher 测试。

## 12. 发布与迁移

- 计划作为向后兼容的 Harness 行为修复发布 `v0.12.1`。
- 先进入开发分支，通过测试后晋级 UAT；UAT 验收完成后再合并 Stable 并创建正式 release。
- 已接入 Skill 通过现有 `harness upgrade --dry-run` 与单独写入确认升级 managed files。
- 迁移只能更新 Harness 管理的状态段、policy、preflight 和模板；不得改写目标 Skill 业务规则。

## 13. 非目标

- 不开发宿主原生 `SkillInvoke` Hook；当前平台尚无该事件。
- 不引入图片型自定义 Emoji；首版固定使用 Unicode `🧙🏻‍♂️`。
- 不建立跨任务持久化 pending-signal ledger；该能力需要另行定义保留期限、隐私和清理合同。
- 不在身份头展示本地绝对路径、用户身份、私有 evidence 或原始会话。
- 不让调用身份头承担 Harness 升级、Issue 提交、PR 或 release 授权。

## 14. 验收标准

- 任一受管 Skill 的每次 invocation 首行准确显示一次身份头。
- 地址可点击并指向 manifest 声明的 canonical repo。
- Skill release、Harness version 与三态渠道均有可复核事实源。
- 无法核验时显示开发版，绝不错误声称 UAT 或正式版。
- 反馈标志明确显示本地待确认，Issue 与修复保持两次独立授权。
- 所有新增测试和现有回归门禁通过。
