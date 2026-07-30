# EvoZeus-CoEvolve v0.11.0

## Summary

- Renames the product and canonical repository from `EvoZeus-wrapper` to `EvoZeus-CoEvolve`.
- Updates generated status sections, documentation links, release discovery, templates, and user-facing runtime messages to the new product name.
- Preserves existing `.evozeus-wrapper/`, Python module names, environment variables, workflow filenames, and Skill slugs as compatibility identifiers.
- Migrates legacy `EvoZeus-wrapper` status headings to `EvoZeus-CoEvolve` while preserving target-owned Skill content.

## Upgrade

Preview the coordinated harness refresh first:

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.11.0 --dry-run --json
```

Apply only after reviewing the complete write set:

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.11.0 --approve --json
```

## Verification

- `python3 -m pytest -q` — 124 passed.
- Python compilation checks passed for the CLI, bootstrap, lifecycle, preflight, dispatcher, and hook adapter.
