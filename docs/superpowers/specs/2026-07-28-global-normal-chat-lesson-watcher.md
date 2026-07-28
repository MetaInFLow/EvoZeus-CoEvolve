# EvoZeus 普通 Chat Lesson 自动捕捉

状态：对内-已通过，开发验证中  
Related issue: https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/29

## 1. 真问题

目标 Skill 内的 feedback audit 只能在该 Skill 已被 Agent 加载后生效。普通 Chat 中用户直接纠错时，Harness 没有逐轮观察入口；旧 Harness 还可能把内部 audit JSON 当成用户可见结果。

目标是让普通 Chat 自动发现“可能值得记录”的 Lesson，同时保持三条治理边界：

1. 自动发现不等于自动持久化。
2. 记录授权不等于修复授权。
3. 用户只看到自然语言 Notice，内部 Hook/audit 字段永不外显。

## 2. 架构决策

复用已安装的 user-level dispatcher，在 `~/.codex/hooks.json` 同时注册两个事件：

| 事件 | 职责 | 失败语义 |
|---|---|---|
| `SessionStart` | 聚合检查注册目标的 Harness 健康与版本 | 确定性 source-contract 错误可阻断 |
| `UserPromptSubmit` | 每轮高精度识别纠错、遗漏、缺陷和长期规则候选 | 永远 fail-open |

`UserPromptSubmit` watcher 只返回简短 `additionalContext`。它不创建 Issue、不写本地 signal、不读取不稳定 transcript 格式，也不调用远端模型。

## 3. 触发与误报控制

Watcher 只识别强信号：

- 明确否定或不满意：不对、错了、有误、漏检、误判、不符合预期。
- 明确机制缺陷：发现 Bug、无法自动捕捉或正常运行。
- 明确长期规则：以后、每次、永远、始终、所有用户，并同时包含检查、记住、自动、统一、不得等行动词。

“应该怎么做”“为什么没有更新”“帮我总结”等普通问题不触发。Hook 只标记候选；Agent 仍需判断它能否抽象为可复用、可归因、可行动的 Lesson。

## 4. 用户体验合同

Agent 必须先完成当前业务纠正。只有 Lesson 成立时，在正常回复末尾追加：

```text
💡 `EvoZeus · Lesson` 待记录

捕捉到一条可复用 Lesson：<一句脱敏、可行动、可复用的总结>。

是否记录到 `<target Skill>` Feedback Issue？本次只记录，不启动修复。
```

禁止展示：`should_capture`、`signal_id`、`capture_state`、route、severity、Issue draft、Hook JSON 或其他诊断字段。

## 5. 路由

1. 当前 `cwd` 位于某个注册目标的 canonical repo 内时，使用该目标。
2. 用户消息明确提到唯一注册 repo/Skill 名时，使用该目标。
3. 其余情况标记为未归属，由 Agent 结合当前对话确认；无法证明时询问目标 Skill，不猜测。

## 6. 安装与升级

- `hook global install` 结构化合并两个事件，保留其他 Hook，并保持幂等。
- 仅有旧 `SessionStart` 注册的安装状态为 `upgrade_required`。
- 刷新 Hook 会改变 trust hash；安装后必须重新通过 Codex `/hooks` 审核。
- 卸载只移除 EvoZeus handler，保留同事件的第三方 handler。

## 7. 验收

- 普通 Chat 纠错无需 `@Skill` 即获得 Lesson 开发者指引。
- 中性问题零 Lesson 上下文。
- 注册表异常时继续用户任务且不泄露本地路径。
- 用户可见合同无内部 JSON 与 signal 字段。
- 安装、刷新和卸载保留无关 Hook，重复安装无重复 handler。
- 既有 `SessionStart` 行为和完整测试集保持通过。
