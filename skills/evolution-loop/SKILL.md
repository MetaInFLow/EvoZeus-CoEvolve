---
name: evozeus-wrapper-evolution-loop
description: Use for lesson intake and Issue-to-PR flow after a Skill has an EvoZeus-CoEvolve harness.
---

# Evolution Loop

Use this stage after the target Skill is wrapped and installed through canonical repo pointers.

## Lesson Intake

安装 user-level global hook 后，`UserPromptSubmit` 会把普通 Chat 用户轮次交给活动产品渠道内已验证的 Session Signal companion；候选判断、目标选择与模型侧指引均由 companion 负责，无需先调用目标 Skill。Watcher 不持久化内容；先完成当前业务纠正，再决定是否展示 Lesson。

```bash
python3 scripts/evozeus_wrapper.py loop lesson --dry-run --json
```

Ask the user whether to submit a lesson candidate. If approved, submit it as an Issue or lesson entry after checking sensitive data.

## Feedback Audit

```bash
python3 scripts/evozeus_wrapper.py loop audit --target /absolute/path/to/skill --user-input "<input>" --json
```

Use this manual audit after the watcher or user identifies a candidate and the target needs a deterministic route, severity, evidence boundary, and Issue draft. It does not persist a signal or write GitHub.

When `should_capture=true`, finish the current business correction first, then show `user_notice.display_text` as a separate block at the end of the same response. The action must ask whether to record the Lesson and state that recording does not start a fix. The signal exists only in the current invocation, no Issue was created, no Skill was modified, and no PR was opened.

The audit JSON is machine-only. Never paste the object, signal id, capture state, route, severity, evidence boundary, or Issue draft into the normal Chat response.

The marker contract is:

```text
💡 `EvoZeus · Lesson` 待记录

<reusable lesson summary>

是否记录到 Skill Feedback Issue？本次授权仅用于记录，不启动修复。
```

Authorization is staged:

1. Feedback capture creates `LOCAL_PENDING_CONFIRMATION` only.
2. Create an Issue only after explicit user confirmation.
3. Issue approval does not authorize a fix, branch, design doc, or PR.
4. Enter Issue-to-PR only after a separate explicit request to implement the fix.

## Issue-to-PR

```bash
python3 scripts/evozeus_wrapper.py loop issue-to-pr --dry-run --json
```

Run this stage only after the feedback Issue exists and the user separately authorized implementation.

Before creating a PR, check GitHub permissions:

- write access: branch and PR in canonical repo.
- fork access only: fork and PR.
- no PR permission: local patch/design doc only.

## Stop Conditions

- Lesson contains raw private session, secret, customer data, or unredacted commercial context.
- `gh auth` fails.
- Private repo access is missing.
