---
name: evozeus-wrapper-evolution-loop
description: Use for lesson intake and Issue-to-PR flow after a Skill has an EvoZeus-CoEvolve harness.
---

# Evolution Loop

Use this stage after the target Skill is wrapped and installed through canonical repo pointers.

## Lesson Intake

```bash
python3 scripts/evozeus_wrapper.py loop lesson --dry-run --json
```

Ask the user whether to submit a lesson candidate. If approved, submit it as an Issue or lesson entry after checking sensitive data.

## Feedback Audit

```bash
python3 scripts/evozeus_wrapper.py loop audit --target /absolute/path/to/skill --user-input "<input>" --json
```

Use this when the user corrected the agent, expressed dissatisfaction, identified a reusable Skill/wrapper defect, or asked to preserve a repeatable behavior. The command returns whether to capture feedback, a short signal id, the user-visible capture marker, route, severity, evidence boundary, and an Issue draft. It does not persist a signal or write GitHub.

When `should_capture=true`, show the returned `capture_marker` and state the boundary verbatim: the signal exists only in the current invocation, no Issue was created, no Skill was modified, and no PR was opened. Continue the original business task while waiting for the user to decide whether to submit the feedback.

The marker contract is:

```text
🧙🏻‍♂️ [EvoZeus][进化信号已捕获｜本地待确认｜<signal-id>]
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
