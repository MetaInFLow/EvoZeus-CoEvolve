# EvoZeus 普通 Chat Lesson 自动捕捉

状态：对内-已通过，等待跨 Repo 集成与产品渠道 UAT
Related issue: https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/29
Companion PR: https://github.com/MetaInFLow/EvoZeus-session-signal-skill/pull/9

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
| registered target inventory | EvoZeus-CoEvolve |
| 活动产品渠道发现、固定 attachment、subprocess transport | EvoZeus-CoEvolve |

CoEvolve 禁止保留 correction regex、durable-rule regex、目标选择算法或 guidance 文案副本。

## 3. Hook 架构

同一 user-level dispatcher 注册两个事件：

| 事件 | 职责 | 失败语义 |
| --- | --- | --- |
| `SessionStart` | 聚合检查注册目标的 Harness 健康与版本 | 保持现有确定性 source-contract 行为 |
| `UserPromptSubmit` | 发现可信 Session Signal 组件并转发当前用户轮次 | 任何异常均 `{continue:true}` |

`UserPromptSubmit` 只从 `$EVOZEUS_HOME/active-channel.json` 与 `channel-state.json` 读取活动 `stable|uat`。生产路径不接受任意环境变量提供 Session Signal root。

## 4. 固定 Component Attachment

CoEvolve `contracts/v1/manifest.json` 固定：

- dependency repo：`MetaInFLow/EvoZeus-session-signal-skill`；
- Unreleased component version：`v0.1.1`，公开 attachment 合同固定 `availability: unreleased`，产品渠道发布后才更新；
- API：`evozeus.session-signal.lesson-candidate.v1`；
- component manifest 与 entrypoint；
- component manifest SHA-256。

执行前必须验证：

1. active channel entry 的 product manifest digest；
2. `install_root` 对 `component_roots.evozeus` 与 `embedded_roots.session_signal` 的 containment；
3. product manifest 的 embedded version、path 与 required paths；
4. Session Signal component manifest schema、version、API、entrypoint 与全部 file digest；
5. manifest、entrypoint 和实现文件均为无 symlink component 的 regular file。

组件缺失、仍为 `v0.1.0`、摘要损坏、路径越界或 symlink 均静默 fail-open。

## 5. Transport 与隐私

- 使用 `sys.executable` + argument list，显式 `shell=false`。
- stdin 传 JSON，stdout 只接受固定 API JSON；stderr 永不外显。
- timeout 固定为 1.5 秒。
- prompt 超过 32,000 chars 或 stdin JSON 超过 256 KiB 时静默 fail-open，不启动子进程。
- 子进程环境只保留 `PYTHONDONTWRITEBYTECODE=1` 与 `PYTHONNOUSERSITE=1`。
- 不写 signal、不创建 Issue、不生成缓存或 bytecode。
- 组件响应只能进入 model-only `additionalContext`；raw prompt、cwd、canonical path、component path、JSON diagnostics 与 signal id 不进入 Hook 响应。
- registered targets 超过 API v1 的 256 上限时传空 inventory，组件仍可返回 unassigned Lesson。

## 6. 安装与升级

- `hook global install` 结构化合并两个事件，保留第三方 Hook，并保持幂等。
- 仅有旧 `SessionStart` 注册的安装状态为 `upgrade_required`。
- 刷新 Hook 会改变 trust hash；安装后重新通过 Codex `/hooks` 审核。
- 卸载只移除 EvoZeus handler，保留同事件的第三方 handler。

## 7. 完成边界

本 PR 只 `Addresses #29`。Issue 保持打开，直到：

1. Session Signal companion PR 合并；
2. 后续 EvoZeus 产品渠道内嵌 `v0.1.1` 并通过完整性检查；
3. 真实 fresh-chat correction / neutral / ambiguous UAT 通过。

本轮禁止下载、安装、tag、Release 或合并 PR。
