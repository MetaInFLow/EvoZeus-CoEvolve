#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const DEFAULT_CONTRACT = resolve(ROOT, "contracts/v1/contributor-branch-contract.json");

function git(repoPath, args) {
  return spawnSync("git", ["-C", repoPath, ...args], {
    encoding: "utf8",
    env: { ...process.env, GIT_OPTIONAL_LOCKS: "0" },
    shell: false
  });
}

function gitText(repoPath, args, required = true) {
  const result = git(repoPath, args);
  if (result.status !== 0) {
    if (!required) return null;
    throw new Error(result.stderr.trim() || result.stdout.trim() || `git ${args[0]} failed`);
  }
  return result.stdout.trim();
}

function canonicalPath(path) {
  const absolute = resolve(path);
  if (existsSync(absolute)) return realpathSync.native(absolute);

  const missingSegments = [];
  let existingAncestor = absolute;
  while (!existsSync(existingAncestor)) {
    const parent = dirname(existingAncestor);
    if (parent === existingAncestor) return absolute;
    missingSegments.unshift(basename(existingAncestor));
    existingAncestor = parent;
  }
  return resolve(realpathSync.native(existingAncestor), ...missingSegments);
}

function isSameOrDescendant(path, ancestor) {
  const pathFromAncestor = relative(ancestor, path);
  return pathFromAncestor === ""
    || (pathFromAncestor !== ".." && !pathFromAncestor.startsWith(`..${sep}`) && !isAbsolute(pathFromAncestor));
}

function pathEntryExists(path) {
  try {
    lstatSync(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") return false;
    return true;
  }
}

function parseWorktrees(text) {
  const worktrees = [];
  let current = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("worktree ")) {
      current = { path: canonicalPath(line.slice(9)), branch: null, bare: false, prunable: false };
      worktrees.push(current);
    } else if (current && line.startsWith("branch ")) {
      current.branch = line.slice(7);
    } else if (current && line === "bare") {
      current.bare = true;
    } else if (current && (line === "prunable" || line.startsWith("prunable "))) {
      current.prunable = true;
    }
  }
  return worktrees;
}

function checkoutStatus(path, { bare = false } = {}) {
  const reason = bare ? "bare" : (!existsSync(path) ? "missing" : null);
  if (reason) return { path, available: false, reason, dirty_entries: [] };
  const result = git(path, ["status", "--porcelain=v1", "--untracked-files=all"]);
  if (result.status !== 0) {
    return { path, available: false, reason: "git_status_failed", dirty_entries: [] };
  }
  return {
    path,
    available: true,
    reason: null,
    dirty_entries: result.stdout.trim().split("\n").filter(Boolean)
  };
}

function githubRepoFromRemote(remoteUrl) {
  const value = String(remoteUrl ?? "").trim();
  const scpLike = value.match(/^git@github\.com:([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?$/i);
  if (scpLike) return scpLike[1];
  try {
    const url = new URL(value);
    if (
      url.hostname.toLowerCase() !== "github.com"
      || !["https:", "ssh:"].includes(url.protocol)
      || url.search
      || url.hash
      || (url.protocol === "ssh:" && url.username !== "git")
    ) return null;
    const path = url.pathname.replace(/^\/+/, "").replace(/\.git$/, "");
    return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(path) ? path : null;
  } catch {
    return null;
  }
}

function parseIssue(rawIssue, repo) {
  const value = String(rawIssue ?? "").trim();
  const match = value.match(/^(?:([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#|#?)([1-9][0-9]*)$/);
  if (!match) return null;
  const issueRepo = match[1] || repo;
  if (issueRepo.toLowerCase() !== repo.toLowerCase()) return null;
  return { reference: `${repo}#${match[2]}`, number: Number(match[2]) };
}

function normalizeProtectedRef(ref) {
  return String(ref ?? "")
    .replace(/^refs\/heads\//, "")
    .replace(/^refs\/remotes\/origin\//, "")
    .replace(/^origin\//, "");
}

function isProtectedRef(ref, contract) {
  const normalized = normalizeProtectedRef(ref);
  return contract.protected_refs.exact.includes(normalized)
    || contract.protected_refs.prefixes.some((prefix) => normalized.startsWith(prefix));
}

function addBlocker(blockers, code, message) {
  if (!blockers.some((blocker) => blocker.code === code)) blockers.push({ code, message });
}

function resumeKeyFor(fields) {
  const source = fields.join("\u001f");
  return `branch_v1_${createHash("sha256").update(source).digest("hex").slice(0, 24)}`;
}

export function resolvePlanDate(options, resumePlan, fallbackDate = new Date().toISOString().slice(0, 10).replaceAll("-", "")) {
  if (options.date) return options.date;
  const target = resumePlan?.branch?.target;
  const purpose = resumePlan?.purpose;
  const prefix = `codex/${options.type}/`;
  const suffix = `-${options.component}-${options.summary}`;
  if (
    typeof target === "string"
    && purpose?.type === options.type
    && purpose?.component === options.component
    && purpose?.summary === options.summary
    && target.startsWith(prefix)
    && target.endsWith(suffix)
  ) {
    const date = target.slice(prefix.length, target.length - suffix.length);
    if (/^20[0-9]{6}$/.test(date)) return date;
  }
  return fallbackDate;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

export function loadContributorBranchContract(path = DEFAULT_CONTRACT) {
  const contract = readJson(resolve(path));
  if (
    contract.contract !== "evozeus.contributor_branch"
    || contract.schema_version !== "v1"
    || !/^1\.[0-9]+\.[0-9]+$/.test(contract.version)
  ) {
    throw new Error("unsupported contributor branch contract identity, schema, or major version");
  }
  return contract;
}

export function collectGitFacts(repoPath, baseRef, targetBranch) {
  const root = canonicalPath(gitText(repoPath, ["rev-parse", "--show-toplevel"]));
  const worktrees = parseWorktrees(gitText(root, ["worktree", "list", "--porcelain"]));
  const currentStatus = checkoutStatus(root);
  const canonicalDescriptor = worktrees[0] || { path: root, bare: false };
  const canonicalStatus = canonicalDescriptor.path === root
    ? currentStatus
    : checkoutStatus(canonicalDescriptor.path, canonicalDescriptor);
  const originUrl = gitText(root, ["config", "--get", "remote.origin.url"], false);
  const baseCommit = gitText(root, ["rev-parse", "--verify", `${baseRef}^{commit}`], false);
  const localCommit = targetBranch
    ? gitText(root, ["show-ref", "--verify", "--hash", `refs/heads/${targetBranch}`], false)
    : null;
  const remoteCommit = targetBranch
    ? gitText(root, ["show-ref", "--verify", "--hash", `refs/remotes/origin/${targetBranch}`], false)
    : null;
  const targetCommit = localCommit || remoteCommit;
  const targetDescendsFromBase = Boolean(
    baseCommit
    && targetCommit
    && git(root, ["merge-base", "--is-ancestor", baseCommit, targetCommit]).status === 0
  );
  return {
    root,
    origin_url: originUrl,
    origin_repo: githubRepoFromRemote(originUrl),
    head: gitText(root, ["rev-parse", "HEAD"]),
    current_branch: gitText(root, ["branch", "--show-current"], false),
    dirty_entries: currentStatus.dirty_entries,
    current_status: currentStatus,
    canonical_status: canonicalStatus,
    base_commit: baseCommit,
    target_commit: targetCommit,
    target_descends_from_base: targetDescendsFromBase,
    target_local: Boolean(localCommit),
    target_remote: Boolean(remoteCommit),
    worktrees
  };
}

const VIEWER_PERMISSION_QUERY = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { viewerPermission } }";

function commandJson(runner, args) {
  try {
    const result = runner("gh", args, { encoding: "utf8", shell: false });
    if (result.status !== 0) return null;
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

export function collectGitHubPermissionEvidence(repo, checkedAt, runner = spawnSync) {
  const [owner, name] = repo.split("/");
  const identity = commandJson(runner, ["api", "user", "--hostname", "github.com"]);
  const permissionData = commandJson(runner, [
    "api", "graphql", "--hostname", "github.com", "-f", `query=${VIEWER_PERMISSION_QUERY}`,
    "-F", `owner=${owner}`, "-F", `name=${name}`
  ]);
  const repositoryData = commandJson(runner, ["api", `repos/${repo}`, "--hostname", "github.com"]);
  const login = typeof identity?.login === "string" ? identity.login : null;
  const viewerPermission = typeof permissionData?.data?.repository?.viewerPermission === "string"
    ? permissionData.data.repository.viewerPermission.toUpperCase()
    : null;
  const forkPolicyAvailable = Boolean(repositoryData && typeof repositoryData.private === "boolean");
  const forkAllowed = forkPolicyAvailable
    ? !repositoryData.archived
      && !repositoryData.disabled
      && (!repositoryData.private || repositoryData.allow_forking === true)
    : null;
  const identityAvailable = Boolean(login);
  const permissionAvailable = Boolean(viewerPermission);

  return {
    source: identityAvailable && permissionAvailable && forkPolicyAvailable
      ? "github_api"
      : (identityAvailable || permissionAvailable || forkPolicyAvailable ? "github_api_partial" : "unavailable"),
    checked_at: checkedAt,
    identity: {
      source: "gh api user",
      available: identityAvailable,
      login
    },
    repository: {
      canonical: repo,
      permission_source: "gh api graphql repository.viewerPermission",
      permission_available: permissionAvailable,
      viewer_permission: viewerPermission,
      fork_policy_source: `gh api repos/${repo}`,
      fork_policy_available: forkPolicyAvailable,
      fork_allowed: forkAllowed
    }
  };
}

export function collectGitHubIssueEvidence(repo, issueNumber, checkedAt, runner = spawnSync) {
  const issueData = Number.isInteger(issueNumber) && issueNumber > 0
    ? commandJson(runner, ["api", `repos/${repo}/issues/${issueNumber}`, "--hostname", "github.com"])
    : null;
  const labels = Array.isArray(issueData?.labels)
    ? issueData.labels
      .map((label) => typeof label === "string" ? label : label?.name)
      .filter((label) => typeof label === "string")
    : null;
  const available = Boolean(
    issueData
    && Number.isInteger(issueData.number)
    && typeof issueData.state === "string"
    && typeof issueData.title === "string"
    && labels
  );
  return {
    source: available ? "github_api" : "unavailable",
    checked_at: checkedAt,
    repository: repo,
    available,
    number: available ? issueData.number : null,
    state: available ? issueData.state.toUpperCase() : null,
    is_pull_request: available ? Boolean(issueData.pull_request) : null,
    labels: available ? labels : [],
    title: available ? issueData.title : null
  };
}

function resolvePermission(evidence) {
  if (evidence.source !== "github_api") return "local";
  if (!evidence.identity.available || !evidence.repository.permission_available) return "local";
  if (["ADMIN", "MAINTAIN", "WRITE"].includes(evidence.repository.viewer_permission)) return "direct";
  if (
    ["READ", "TRIAGE"].includes(evidence.repository.viewer_permission)
    && evidence.repository.fork_policy_available
    && evidence.repository.fork_allowed
  ) return "fork";
  return "local";
}

function validateInput(options, contract, profile, blockers) {
  const tokenPattern = new RegExp(contract.branch_naming.component_pattern);
  const summaryPattern = new RegExp(contract.branch_naming.summary_pattern);
  const actorPattern = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
  const datePattern = /^20[0-9]{6}$/;
  const repoPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

  if (!profile) addBlocker(blockers, "unknown_profile", `Unknown profile: ${options.profile}`);
  if (!repoPattern.test(options.repo)) addBlocker(blockers, "invalid_repo", "repo must use OWNER/REPO");
  if (!actorPattern.test(options.actor)) addBlocker(blockers, "invalid_actor", "actor must be a stable GitHub-style identifier");
  if (!datePattern.test(options.date)) addBlocker(blockers, "invalid_date", "date must use YYYYMMDD");
  if (!contract.branch_naming.types.includes(options.type)) addBlocker(blockers, "invalid_type", "type is not allowed by the v1 contract");
  if (!tokenPattern.test(options.component)) addBlocker(blockers, "invalid_component", "component must use lowercase kebab-case");
  if (!summaryPattern.test(options.summary)) addBlocker(blockers, "invalid_summary", "summary must contain one to seven lowercase kebab-case terms");
  if (!isAbsolute(options.worktree)) addBlocker(blockers, "invalid_worktree", "worktree must be an absolute path");
  if (!parseIssue(options.issue, options.repo)) addBlocker(blockers, "invalid_issue", "issue must identify this repo and a positive issue number");

  if (profile) {
    if (!new RegExp(profile.repo_pattern).test(options.repo)) addBlocker(blockers, "wrong_repo", "repo does not match the selected profile");
    if (!profile.canonical_bases.includes(options.base)) addBlocker(blockers, "wrong_base_ref", "base is not canonical for the selected profile");
    if (!profile.allowed_types.includes(options.type)) addBlocker(blockers, "type_not_allowed", "type is not allowed for the selected profile");
    if (profile.allowed_components && !profile.allowed_components.includes(options.component)) {
      addBlocker(blockers, "component_not_allowed", "component is not allowed for the selected profile");
    }
  }
  if (!contract.permission_paths[options.permission]) addBlocker(blockers, "invalid_permission", "permission must be direct, fork, or local");
}

export function buildBranchPlan(options, contract, facts, permissionEvidence, issueEvidence, resumePlan = null) {
  const blockers = [];
  const profile = contract.profiles[options.profile] || null;
  validateInput(options, contract, profile, blockers);

  const resolvedPermission = resolvePermission(permissionEvidence);
  const verifiedActor = permissionEvidence.identity.login;
  const resolvedActor = verifiedActor || options.actor;
  if (verifiedActor && verifiedActor.toLowerCase() !== options.actor.toLowerCase()) {
    addBlocker(blockers, "actor_mismatch", "expected actor does not match the authenticated GitHub viewer");
  }
  if (resolvedPermission !== options.permission) {
    addBlocker(
      blockers,
      "permission_expectation_mismatch",
      `expected ${options.permission} permission path but GitHub evidence resolves ${resolvedPermission}`
    );
  }
  if (profile && !profile.allowed_permissions.includes(resolvedPermission)) {
    addBlocker(blockers, "permission_not_allowed", "resolved permission path is not allowed for the selected profile");
  }

  const issue = parseIssue(options.issue, options.repo);
  if (!issueEvidence?.available || issueEvidence.source !== "github_api") {
    addBlocker(blockers, "issue_evidence_unavailable", "Feedback Issue existence cannot be verified from GitHub");
  } else {
    if (issueEvidence.repository.toLowerCase() !== options.repo.toLowerCase() || issueEvidence.number !== issue?.number) {
      addBlocker(blockers, "issue_evidence_mismatch", "GitHub Issue evidence does not match the requested canonical Issue");
    }
    if (issueEvidence.is_pull_request) {
      addBlocker(blockers, "issue_is_pull_request", "the requested governance entity is a pull request, not an Issue");
    }
    if (issueEvidence.state !== contract.issue_resolution.required_state) {
      addBlocker(blockers, "issue_not_open", "the requested Feedback Issue must remain open during implementation");
    }
    const classification = profile?.issue_classification;
    if (classification) {
      const evidenceLabels = issueEvidence.labels.map((label) => label.toLowerCase());
      const labelMatch = classification.labels_any.some((label) => evidenceLabels.includes(label.toLowerCase()));
      const titleMatch = classification.title_prefixes.some((prefix) => issueEvidence.title.startsWith(prefix));
      if (classification.match !== "label_or_title_prefix" || (!labelMatch && !titleMatch)) {
        addBlocker(blockers, "issue_not_feedback", "the requested Issue is not classified as Skill feedback");
      }
    }
  }
  const branchName = `codex/${options.type}/${options.date}-${options.component}-${options.summary}`;
  const branchCheck = git(facts.root, ["check-ref-format", "--branch", branchName]);
  if (branchCheck.status !== 0) addBlocker(blockers, "invalid_branch", "generated branch fails git check-ref-format");
  if (isProtectedRef(branchName, contract)) addBlocker(blockers, "protected_target", "target branch is protected");
  if (!facts.base_commit) addBlocker(blockers, "missing_base", `base ref does not resolve: ${options.base}`);
  if (!facts.current_status.available) {
    addBlocker(blockers, "current_checkout_status_unavailable", "current checkout status cannot be verified");
  } else if (facts.dirty_entries.length > 0) {
    addBlocker(blockers, "dirty_tree", "repository worktree is dirty");
  }
  if (!facts.canonical_status.available) {
    addBlocker(blockers, "canonical_checkout_status_unavailable", "canonical checkout status cannot be verified");
  } else if (facts.canonical_status.dirty_entries.length > 0) {
    addBlocker(blockers, "canonical_checkout_dirty", "canonical checkout must remain clean");
  }
  if (!facts.origin_repo) {
    addBlocker(blockers, "missing_origin_identity", "remote.origin must identify a GitHub OWNER/REPO");
  } else if (facts.origin_repo.toLowerCase() !== options.repo.toLowerCase()) {
    addBlocker(blockers, "repo_remote_mismatch", "remote.origin does not match the requested canonical repo");
  }

  const requestedWorktree = canonicalPath(options.worktree);
  const canonicalCheckout = facts.worktrees[0]?.path ?? facts.root;
  const insideCanonicalCheckout = isSameOrDescendant(requestedWorktree, canonicalCheckout);
  const nestedRegisteredWorktree = facts.worktrees.find(
    (item) => requestedWorktree !== item.path && isSameOrDescendant(requestedWorktree, item.path)
  );
  const currentProtected = isProtectedRef(facts.current_branch, contract);
  if (currentProtected && requestedWorktree === facts.root) {
    addBlocker(blockers, "protected_checkout_write", "protected checkout cannot be the contribution worktree");
  } else if (insideCanonicalCheckout) {
    addBlocker(blockers, "canonical_checkout_write", "canonical checkout and its descendants cannot be the contribution worktree");
  } else if (nestedRegisteredWorktree) {
    addBlocker(blockers, "registered_worktree_descendant", "contribution worktree cannot be nested inside any registered worktree");
  }

  const resumeKey = resumeKeyFor([
    options.profile,
    options.repo.toLowerCase(),
    options.base,
    issue?.reference ?? options.issue,
    resolvedActor.toLowerCase(),
    resolvedPermission,
    options.type,
    options.component,
    options.summary
  ]);
  let decision = "new";
  let resumeValid = false;
  let ownerReconfirmed = false;

  if (resumePlan) {
    const resumeEvidenceValid = resumePlan.writes === false
      && Array.isArray(resumePlan.blockers)
      && resumePlan.blockers.length === 0
      && resumePlan.next_write_action !== "blocked"
      && ["new", "resume"].includes(resumePlan.resume?.decision);
    const ownershipTime = Date.parse(resumePlan.ownership?.checked_at ?? "");
    const nowTime = Date.parse(options.now);
    const staleMs = contract.resume.stale_after_days * 86_400_000;
    const ownershipMatches = resumePlan.resume?.key === resumeKey
      && resumePlan.actor?.id === resolvedActor
      && resumePlan.repo?.canonical === options.repo
      && resumePlan.base?.ref === options.base
      && resumePlan.branch?.target === branchName;
    const ownershipStale = !Number.isFinite(ownershipTime)
      || !Number.isFinite(nowTime)
      || ownershipTime > nowTime
      || nowTime - ownershipTime > staleMs;
    if (!resumeEvidenceValid) {
      addBlocker(blockers, "resume_evidence_invalid", "resume plan must be a prior blocker-free zero-write plan");
    } else if (!ownershipMatches) {
      addBlocker(blockers, "stale_ownership", "resume plan owner, key, or branch does not match");
    } else if (ownershipStale && !options.reconfirm_owner) {
      addBlocker(blockers, "stale_ownership", "resume plan ownership window is stale; Owner reconfirmation is required");
    } else if (resumePlan.base?.commit !== facts.base_commit) {
      addBlocker(blockers, "stale_base", "resume plan base commit no longer matches the canonical base");
    } else if (!facts.target_commit) {
      addBlocker(blockers, "resume_branch_missing", "resume plan target branch no longer exists");
    } else if (!facts.target_descends_from_base) {
      addBlocker(blockers, "resume_branch_wrong_base", "resume target branch does not descend from the saved canonical base");
    } else {
      decision = "resume";
      resumeValid = true;
      ownerReconfirmed = ownershipStale && Boolean(options.reconfirm_owner);
    }
  } else if (facts.target_commit) {
    addBlocker(blockers, "branch_collision", "target branch exists without a matching resume plan");
  }

  if (!resumeValid && facts.base_commit && facts.head !== facts.base_commit) {
    addBlocker(blockers, "wrong_base_commit", "HEAD does not match the canonical base commit for a new plan");
  }
  if (resumeValid && facts.current_branch !== branchName && facts.head !== facts.base_commit) {
    addBlocker(blockers, "wrong_resume_checkout", "resume must start from the target branch or canonical base");
  }

  const registeredAtPath = facts.worktrees.find((item) => item.path === requestedWorktree);
  const registeredForBranch = facts.worktrees.find((item) => item.branch === `refs/heads/${branchName}`);
  const registeredPathExists = pathEntryExists(requestedWorktree);
  const usableRegisteredAtPath = registeredAtPath && registeredPathExists && !registeredAtPath.prunable;
  if (registeredPathExists && !registeredAtPath) {
    addBlocker(blockers, "worktree_collision", "requested worktree path exists but is not registered");
  }
  if (registeredAtPath && registeredAtPath.branch !== `refs/heads/${branchName}`) {
    addBlocker(blockers, "worktree_collision", "requested worktree belongs to another branch");
  }
  if (registeredForBranch && registeredForBranch.path !== requestedWorktree) {
    addBlocker(blockers, "branch_worktree_collision", "target branch is registered at another worktree path");
  }

  const permission = contract.permission_paths[resolvedPermission] || null;
  const repoName = options.repo.split("/")[1] || null;
  const sourceRepo = resolvedPermission === "fork" ? `${resolvedActor}/${repoName}` : options.repo;
  if (blockers.length > 0) decision = "blocked";

  return {
    schema_version: "v1",
    contract: {
      id: contract.contract,
      version: contract.version,
      compatibility: contract.compatibility
    },
    profile: options.profile,
    generated_at: options.now,
    repo: { canonical: options.repo, source: sourceRepo, path: facts.root },
    base: { ref: options.base, commit: facts.base_commit },
    branch: {
      target: branchName,
      class: profile?.branch_class ?? null,
      user_channel_claim: profile?.user_channel_claim ?? null,
      current: facts.current_branch,
      existing_commit: facts.target_commit
    },
    issue,
    issue_evidence: issueEvidence,
    actor: {
      id: resolvedActor,
      expected: options.actor,
      verified: Boolean(verifiedActor),
      source: permissionEvidence.identity.source
    },
    purpose: { type: options.type, component: options.component, summary: options.summary },
    permission_path: permission
      ? { expected: options.permission, resolved: resolvedPermission, ...permission, source_repo: sourceRepo }
      : { expected: options.permission, resolved: resolvedPermission },
    permission_evidence: permissionEvidence,
    worktree: {
      required: true,
      path: requestedWorktree,
      current_repo_path: facts.root,
      canonical_checkout_path: canonicalCheckout,
      isolated: !insideCanonicalCheckout && !nestedRegisteredWorktree,
      registered: Boolean(usableRegisteredAtPath),
      registration_present: Boolean(registeredAtPath),
      registration_prunable: Boolean(registeredAtPath?.prunable || (registeredAtPath && !registeredPathExists)),
      current_checkout: {
        status_available: facts.current_status.available,
        status_reason: facts.current_status.reason,
        dirty_entry_count: facts.current_status.dirty_entries.length
      },
      canonical_checkout: {
        status_source: "git status --porcelain=v1",
        status_available: facts.canonical_status.available,
        status_reason: facts.canonical_status.reason,
        dirty_entry_count: facts.canonical_status.dirty_entries.length
      }
    },
    ownership: { actor: resolvedActor, checked_at: options.now },
    resume: { key: resumeKey, decision, owner_reconfirmed: ownerReconfirmed },
    next_write_action: blockers.length === 0
      ? (decision === "resume"
        ? (usableRegisteredAtPath
          ? "resume_existing_branch_in_isolated_worktree"
          : (registeredAtPath
            ? "prune_and_recreate_resume_worktree_for_existing_branch"
            : "recreate_resume_worktree_for_existing_branch"))
        : permission.next_write_action)
      : "blocked",
    approval_boundaries: contract.approval_boundaries,
    blockers,
    writes: false
  };
}

function parseArguments(argv) {
  if (argv[0] !== "plan") throw new Error("first argument must be plan");
  const options = { json: false };
  const valueOptions = new Set([
    "profile", "repo", "repo-path", "base", "issue", "actor", "type", "component", "summary",
    "permission", "worktree", "date", "resume-plan"
  ]);
  for (let index = 1; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--json") {
      options.json = true;
      continue;
    }
    if (token === "--reconfirm-owner") {
      options.reconfirm_owner = true;
      continue;
    }
    if (!token.startsWith("--") || !valueOptions.has(token.slice(2)) || index + 1 >= argv.length) {
      throw new Error(`invalid argument: ${token}`);
    }
    options[token.slice(2).replaceAll("-", "_")] = argv[index + 1];
    index += 1;
  }
  const required = ["profile", "repo", "repo_path", "base", "issue", "actor", "type", "component", "summary", "permission", "worktree"];
  const missing = required.filter((key) => !options[key]);
  if (missing.length > 0) throw new Error(`missing arguments: ${missing.join(", ")}`);
  if (options.reconfirm_owner && !options.resume_plan) {
    throw new Error("--reconfirm-owner requires --resume-plan");
  }
  options.now = new Date().toISOString();
  return options;
}

function main() {
  let plan;
  try {
    const options = parseArguments(process.argv.slice(2));
    const contract = loadContributorBranchContract();
    const resumePlan = options.resume_plan ? readJson(resolve(options.resume_plan)) : null;
    options.date = resolvePlanDate(options, resumePlan);
    const provisionalBranch = `codex/${options.type}/${options.date}-${options.component}-${options.summary}`;
    const facts = collectGitFacts(resolve(options.repo_path), options.base, provisionalBranch);
    const permissionEvidence = collectGitHubPermissionEvidence(options.repo, options.now);
    const parsedIssue = parseIssue(options.issue, options.repo);
    const issueEvidence = collectGitHubIssueEvidence(options.repo, parsedIssue?.number, options.now);
    plan = buildBranchPlan(options, contract, facts, permissionEvidence, issueEvidence, resumePlan);
  } catch (error) {
    plan = {
      schema_version: "v1",
      blockers: [{ code: "preflight_error", message: error.message }],
      next_write_action: "blocked",
      writes: false
    };
  }
  process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
  if (plan.blockers.length > 0) process.exitCode = 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) main();
