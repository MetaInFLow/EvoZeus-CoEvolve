# EvoZeus 普通 Chat Lesson 自动捕捉

状态：Partial / Addresses #29（Issue 保持 open；CoEvolve lifecycle 已实现，Session Signal/Core runtime 合入、产品渠道内嵌与 fresh-chat UAT 待完成）
Related issue: https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/29
Companion PR: https://github.com/MetaInFLow/EvoZeus-session-signal-skill/pull/9
Core runtime PR: https://github.com/MetaInFLow/EvoZeus/pull/50

## 1. 真问题

目标 Skill 内的 feedback audit 只能在该 Skill 已被 Agent 加载后生效。普通 Chat 中用户直接纠错时，Harness 缺少逐轮接线；旧 Harness 还可能把内部 audit JSON 带入用户回复。

目标是让普通 Chat 获得 Lesson 候选入口，同时保持三条治理边界：

1. 自动发现不产生持久化。
2. 记录授权与修复授权分别确认。
3. 用户只看到自然语言 Notice，Hook、组件与路由内部字段保持隐藏。

## 2. 跨 Repo 职责

| 责任 | Owner |
| --- | --- |
| correction / durable-rule 高精度判断 | `EvoZeus-session-signal-skill` |
| `cwd` containment / 唯一 alias 目标选择 | `EvoZeus-session-signal-skill` |
| model-only natural-language guidance | `EvoZeus-session-signal-skill` |
| `UserPromptSubmit` 注册、刷新与卸载 | EvoZeus-CoEvolve |
| registered target pointer 生命周期 | EvoZeus-CoEvolve |
| target inventory 消费、活动产品渠道发现、固定 attachment、subprocess transport | EvoZeus Core |

CoEvolve 禁止保留 correction regex、durable-rule regex、目标选择算法或 guidance 文案副本。

## 3. Hook 架构

同一 user-level dispatcher 注册两个事件：

| 事件 | 职责 | 失败语义 |
| --- | --- | --- |
| `SessionStart` | 聚合检查注册目标的 Harness 健康与版本 | 保持现有确定性 source-contract 行为 |
| `UserPromptSubmit` | 进入 Core-owned Lesson runtime | 任何异常均 `{continue:true}` |

CoEvolve 只把同一 Core-owned dispatcher 注册到两个事件。Core 的 `UserPromptSubmit` 分支负责读取 `$EVOZEUS_HOME/active-channel.json`、`channel-state.json` 与固定的 `~/.evozeus/.projects` 注册表。CoEvolve template 保持 `SessionStart`-only。

## 4. 固定 Core Runtime 依赖

CoEvolve `contracts/v1/manifest.json` 固定：

- dependency repo：`MetaInFLow/EvoZeus`；
- Unreleased Core PR 与精确 source revision；
- API：`evozeus.user-prompt.lesson-runtime.v1`；
- Core-owned dispatcher 路径与 owner。

CoEvolve 安装前必须验证：

1. dispatcher 和 Core state 均为 regular file，且不接受 symlink；
2. Core state 为 `channel-managed` 且信任来源为产品 manifest；
3. dispatcher 包含 Core schema 与 `evozeus.user-prompt.lesson-runtime.v1` marker。

Core runtime 缺失或版本过旧时，CoEvolve lifecycle 安装在任何写入前阻塞。每轮执行期的渠道、attachment、摘要、路径、transport 与隐私校验由 Core 负责并 fail-open。

## 5. Transport 与隐私

- Core runtime 负责 bounded stdin/stdout、timeout、`shell=false`、隔离环境与私密值回显拒绝。
- CoEvolve lifecycle 不读取 prompt，不执行 Session Signal，不写 signal，不创建 Issue。
- CoEvolve 只修改 `~/.codex/hooks.json` 中自己的 handler 和独立 lifecycle state。

## 6. 安装与升级

- `hook global install` 结构化合并两个事件，保留第三方 Hook，并保持幂等。
- install / refresh 不复制或覆盖 Core dispatcher 和 Core state。
- 仅有旧 `SessionStart` 注册的安装状态为 `upgrade_required`。
- 刷新 Hook 会改变 trust hash；安装后重新通过 Codex `/hooks` 审核。
- 卸载只移除 EvoZeus handler 与 CoEvolve lifecycle state，保留第三方 handler、Core dispatcher 和 Core state。

## 7. 完成边界

当前能力状态分层如下：

- CoEvolve lifecycle：`Implemented`，本 PR 的 Hook 注册、刷新、卸载与 fail-open 生命周期已实现并通过回归。
- Session Signal companion：`Partial`，依赖 PR #9 合入并完成真实 fresh-chat UAT。
- Core runtime：`Partial`，依赖 PR #50 合入并由产品渠道发布固定组件。
- 产品渠道内嵌与完整 UAT：`Planned`，尚未执行发布或真实用户验证。

本 PR 只 `Addresses #29`。Issue 保持打开，直到：

1. Session Signal companion PR 合并；
2. Core runtime PR 合并；
3. 后续 EvoZeus 产品渠道内嵌 Session Signal 并通过完整性检查；
4. 真实 fresh-chat correction / neutral / ambiguous UAT 通过。

本轮禁止下载、安装、tag、Release 或合并 PR。
