{
  "wrapper_repo": "MetaInFLow/EvoZeus-CoEvolve",
  "wrapper_version": "v0.14.0",
  "applied_at": "{{APPLIED_AT}}",
  "layout_version": 2,
  "target_wrapper_dir": ".evozeus-wrapper",
  "target_infra_dir": ".evozeus-wrapper",
  "legacy_layout_dirs": [
    ".evozeus_evoinfra",
    ".evozeus"
  ],
  "canonical_repo": "{{REPO_NAME}}",
  "managed_files": [
    ".evozeus-wrapper/CHANGELOG.md",
    ".evozeus-wrapper/WRAPPER.md",
    ".evozeus-wrapper/policies/feedback-policy.json",
    ".evozeus-wrapper/policies/audit-rule.md",
    ".evozeus-wrapper/policies/notice-policy.json",
    ".codex/hooks.json",
    ".evozeus-wrapper/hooks/evozeus_wrapper_start_check.py",
    ".evozeus-wrapper/docs/index.md",
    ".evozeus-wrapper/docs/_config.yml",
    ".evozeus-wrapper/docs/design-doc-template.md",
    ".evozeus-wrapper/docs/designs/README.md",
    ".evozeus-wrapper/docs/migrations/README.md",
    ".evozeus-wrapper/docs/onboarding.md",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/skill-feedback.yml",
    ".github/pull_request_template.md",
    ".github/workflows/evozeus-wrapper-preflight.yml",
    ".evozeus-wrapper/scripts/evozeus_wrapper_preflight.py",
    ".evozeus-wrapper/scripts/evozeus_notice.py"
  ],
  "install_links": [],
  "dashboard": {
    "deployment_mode": "opt_in_github_pages",
    "enablement_variable": "EVOZEUS_PAGES_ENABLED",
    "enabled_value": "true",
    "fallback": "repository_only",
    "capability_check": "confirm repository Pages support before enabling deployment"
  },
  "onboarding": {
    "installation": {
      "mode": "canonical_repo_symlink",
      "command": "python3 scripts/evozeus_wrapper.py publish reinstall --skill-name {{SKILL_NAME}} --canonical-path <canonical-repo-path> --target codex --json",
      "verification": "test -L \"$HOME/.codex/skills\"/{{SKILL_NAME}} && python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo {{REPO_NAME}}"
    },
    "invocation": {
      "mode": "host_skill_discovery",
      "owner": "target_skill",
      "instruction": "Start a new host session in a consumer project and invoke {{SKILL_NAME}} using the trigger contract in its canonical SKILL.md.",
      "verification": "Confirm the host selects the canonical {{SKILL_NAME}}/SKILL.md and pass a consumer-project smoke test."
    },
    "initialization": {
      "required": false,
      "owner": "target_skill",
      "command": null,
      "verification": null
    },
    "generated_child_skills": {
      "supported": false,
      "hooks_inherited": false,
      "repo_harness_inherited": false,
      "attachment": "not_applicable",
      "separate_harness_boundary": "independent_git_repository",
      "trust_review": "not_applicable",
      "verification": "not_applicable"
    }
  },
  "hook_registration": {
    "codex": {
      "capability": "repo_maintenance_hook",
      "config_file": ".codex/hooks.json",
      "hook_script": ".evozeus-wrapper/hooks/evozeus_wrapper_start_check.py",
      "event": "SessionStart",
      "matcher": "startup|resume",
      "scope": "canonical_repository",
      "covers_skill_invocation": false,
      "installation_status": "installed",
      "trust_status": "pending_review",
      "trust_review": "required_by_codex_hooks",
      "latest_version_env": "EVOZEUS_WRAPPER_LATEST_VERSION",
      "enforcement_env": "EVOZEUS_WRAPPER_HOOK_ENFORCEMENT"
    }
  },
  "integration": {
    "mode": "prompt_runtime_check",
    "native_skill_invocation_hook_installed": false,
    "native_host_hook_installed": false,
    "codex_project_hook": true,
    "plugin_lifecycle_hook": false,
    "capabilities": {
      "repo_maintenance_hook": {
        "installed": true,
        "native_enforced": true,
        "event": "SessionStart",
        "scope": "canonical_repository",
        "covers_skill_invocation": false
      },
      "plugin_lifecycle_hook": {
        "installed": false,
        "native_enforced": false,
        "scope": "plugin_lifecycle",
        "covers_skill_invocation": false
      },
      "global_session_dispatcher": {
        "installed": false,
        "native_enforced": false,
        "event": "SessionStart",
        "scope": "all_registered_wrapped_skills",
        "covers_skill_invocation": false
      },
      "skill_entry_preflight": {
        "installed": true,
        "native_enforced": false,
        "scope": "selected_skill_instruction_surface",
        "covers_skill_invocation": true
      },
      "tool_gateway": {
        "installed": false,
        "native_enforced": false,
        "event": "PreToolUse",
        "scope": "toolized_execution_path",
        "covers_skill_invocation": false
      },
      "skill_invocation_hook": {
        "supported": false,
        "installed": false,
        "event": null
      }
    },
    "manual_wrapper_command": "not_runtime_integration",
    "target_kind": "single_skill",
    "root_entry": "SKILL.md",
    "hook_files": [
      ".codex/hooks.json",
      ".evozeus-wrapper/hooks/evozeus_wrapper_start_check.py"
    ],
    "plugin_manifests": [],
    "skill_count": 0,
    "description": "The instruction surface can require a Skill-entry preflight, but enforcement depends on prompt compliance."
  }
}
