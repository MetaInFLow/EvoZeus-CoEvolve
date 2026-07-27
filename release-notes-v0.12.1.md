# EvoZeus-CoEvolve v0.12.1

This release makes the CoEvolve maintenance state visible at every wrapped Skill invocation and closes the authorization gap between feedback capture and implementation.

## Included

- Versioned `runtime_identity` output with a Unicode `🧙🏻‍♂️` maintenance header, clickable canonical repository, Skill release, Harness version, and development/UAT/stable channel.
- Fail-closed channel classification: unverified sources report development rather than claiming UAT or Stable.
- Feedback capture marker with a non-sensitive signal id and `LOCAL_PENDING_CONFIRMATION` state.
- Independent authorization gates for feedback capture, Issue submission, and fix/PR execution.
- Updated managed target templates, lifecycle guidance, contract bundle hashes, release metadata, and upgrade commands.

## Runtime Contract

```text
🧙🏻‍♂️ [EvoZeus 自进化维护] [OWNER/REPO](https://github.com/OWNER/REPO) · Skill vX.Y.Z · Harness v0.12.1 · 开发版/UAT/正式版
```

Feedback audit remains read-only and no longer returns an executable Issue command before explicit confirmation.

## Verification

- `python3 -m pytest -q` — 139 passed, 8 subtests passed.
- Python compile checks for all executable lifecycle, preflight, dispatcher, and hook scripts.
- Real identity smoke against `MetaInFLow/metainflow-developer-onboarding` returned `Skill v0.1.1 · Harness v0.12.0 · 正式版` before target upgrade.
- Real feedback-audit smoke returned `writes=false`, `capture_persisted=false`, `LOCAL_PENDING_CONFIRMATION`, and `issue_create_command=null`.

## Upgrade

Run the authoritative dry-run before applying the managed-file upgrade:

```text
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.12.1 --dry-run --json
```
