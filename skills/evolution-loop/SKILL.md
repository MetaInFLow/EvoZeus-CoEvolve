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

Use this when the user corrected the agent, expressed dissatisfaction, identified a reusable Skill/wrapper defect, or asked to preserve a repeatable behavior. The command returns whether to capture feedback, the route, severity, evidence boundary, Issue draft, and `gh issue create` command. It does not write GitHub by default.

## Issue-to-PR

```bash
python3 scripts/evozeus_wrapper.py loop issue-to-pr --dry-run --json
```

Before creating a PR, check GitHub permissions:

- write access: branch and PR in canonical repo.
- fork access only: fork and PR.
- no PR permission: local patch/design doc only.

## Stop Conditions

- Lesson contains raw private session, secret, customer data, or unredacted commercial context.
- `gh auth` fails.
- Private repo access is missing.
