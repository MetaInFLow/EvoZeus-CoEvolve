# Skill Evolution PR

## Related Issue

- Closes:

## Design Doc

- Path:

<!-- evozeus-contributor-branch-plan:v1 -->
## Contributor Branch Plan

- Resume key:
- Core source revision:
- Contract SHA-256:
- Profile:
- Purpose type / component / summary:
- Canonical repo:
- Base ref / commit:
- Target branch:
- Issue:
- Verified actor:
- Permission path:

For a business contribution, copy these values from `pr_metadata`. The trusted base-SHA workflow recomputes actor, head repository/permission path, Issue state/type/classification, target branch, and resume key from the GitHub event and API. Keep ledger paths, local Repo/worktree paths, internal errors, secrets, and private context out of the PR.

An official Harness upgrade on `evozeus/harness-vX-to-vY` may omit this section. Its separate gate requires a canonical direct branch, live `ADMIN`, an official published Stable CoEvolve Release, and a restricted migration-only diff.
<!-- /evozeus-contributor-branch-plan -->

## What Changed

-

## Verification

- [ ] `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo <OWNER/REPO>`
- [ ] `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure`
- [ ] `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py pr --design-doc <path>`
- [ ] Manual Skill behavior check completed
- [ ] Branch plan was resumed from the private ledger and revalidated before commit/push/PR
- [ ] Current repo, base commit, target branch, actor, and permission still match the saved plan
- [ ] This PR does not change the trusted workflow, PR validator, or branch consumer; Harness control-plane upgrades require Owner bootstrap review

## Release Readiness

- [ ] `.evozeus-wrapper/CHANGELOG.md` updated
- [ ] Release tag planned
- [ ] Release description drafted

## Data Safety

- [ ] No raw private session data
- [ ] No secrets, tokens, cookies, or credentials
- [ ] No customer or commercial private context without redaction
