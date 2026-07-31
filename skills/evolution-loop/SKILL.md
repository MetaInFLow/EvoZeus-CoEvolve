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

Use this when the user corrected the agent, expressed dissatisfaction, identified a reusable Skill/wrapper defect, or asked to preserve a repeatable behavior. The command returns whether to capture feedback, a short signal id, a structured `user_notice`, route, severity, evidence boundary, and an Issue draft. It does not persist a signal or write GitHub.

When `should_capture=true`, finish the current business correction first, then show `user_notice.display_text` as a separate block at the end of the same response. The action must ask whether to record the Lesson and state that recording does not start a fix. The signal exists only in the current invocation, no Issue was created, no Skill was modified, and no PR was opened.

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
python3 scripts/evozeus_wrapper.py loop issue-to-pr \
  --target /absolute/path/to/target-repo \
  --base origin/main \
  --issue OWNER/REPO#NUMBER \
  --actor <expected-github-login> \
  --type bug \
  --component skill \
  --summary fix-feedback-flow \
  --permission <direct|fork|local> \
  --worktree /absolute/path/outside/canonical-checkout \
  --json
```

Run this stage only after the feedback Issue exists and the user separately authorized implementation.

The command reads the target manifest, verifies the pinned EvoZeus Core contract/planner snapshot, collects live Git/GitHub evidence, and returns a zero-write branch plan. Display canonical repo, base ref/commit, Issue evidence, target branch, verified actor, resolved permission, evidence timestamp/source, isolated worktree, resume decision, next action, and blockers before any target-file write. The Issue must live-resolve to the same Repo and number, remain OPEN, resolve as an Issue rather than a Pull Request, and carry `skill-feedback` or the `[Skill Feedback]` title prefix.

- Empty blockers plus separate branch/worktree approval: rerun with `--approve-save-plan`, execute the declared next action, then resume from the private ledger.
- A ledger outside the ownership window stays blocked. If repo, owner, base, branch, resume key, and permission still match, obtain explicit Owner confirmation and rerun with `--resume-plan ... --reconfirm-owner`; persist the refreshed ledger only with the separate `--approve-save-plan` flag. When `--date` is omitted during resume, the planner recovers the original date only from a purpose-matching validated ledger target branch. Identity mismatches cannot use this path.
- Direct evidence: create the isolated contribution branch in the canonical repo.
- Fork evidence: create the isolated fork branch and later open the fork PR.
- Local evidence: create a local-only patch/design context; push and PR remain forbidden.

`--actor` and `--permission` are expectations. Live Core evidence controls the result. The length-bounded target branch includes the verified actor; direct requires a writable non-archived/non-disabled Repo and uses canonical origin, while fork requires a configured remote whose effective fetch/push URLs identify the verified actor's exact fork. Canonical base and direct/fork target existence come from their live effective remotes. Missing or partial permission evidence resolves the permission path to local; unavailable or invalid Issue evidence blocks the whole plan. A dirty current/canonical/requested-resume worktree, redirected registered-worktree identity, stale live base, local/remote branch divergence, local ref namespace conflict, remote-only resume branch, occupied prunable/invalid ancestor path, branch/worktree collision, unreconfirmed stale ledger ownership, or a canonical-checkout path also returns blockers. A remote-only resume requires separate approval to fetch and create the local branch before replanning. Do not modify business files while any blocker remains.

The ledger defaults to `~/.evozeus/coevolve/branch-plans/OWNER/REPO/<resume-key>.json`. Before commit, push, and PR, rerun with `--resume-plan <ledger-file>` and require the same repo, base ref, base commit, target branch, actor, and resolved permission. Copy only `pr_metadata` into the PR; local paths and ledger contents remain private. Before attach writes any template, GitHub must confirm that the default branch protects the exact `EvoZeus Contributor Gate` required check bound to GitHub Actions `app_id=15368`; unknown preexisting gate bytes block attach. The target `pull_request_target` gate executes the validator from the exact base SHA, reads the exact head SHA through the base Repo PR ref as untrusted data, live-verifies actor/head-repo permission topology and Issue evidence, then recomputes the resume key. Subsequent Issue state, transfer, title, or classification-label changes re-run the linked open PR's trusted workflow through the Actions API. Official Harness upgrades use a separate direct-branch ADMIN gate tied byte-for-byte to a published non-prerelease CoEvolve Release and a restricted migration diff.

## Stop Conditions

- Lesson contains raw private session, secret, customer data, or unredacted commercial context.
- Core snapshot provenance/digest verification fails.
- The branch plan has any blocker or the isolated worktree has not been created/resumed under separate authorization.
