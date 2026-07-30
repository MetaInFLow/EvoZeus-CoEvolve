---
name: evozeus-wrapper-status-assessment
description: Use after environment and target repo diagnosis to explain the wrapper assessment in user-understandable language, show process status, identify blockers, and choose the next wrapper lifecycle step.
---

# Status Assessment

Use this Skill after running `env diagnose` and `skill diagnose`, before any transform, repair, publish, or write operation.

The CLI scripts provide facts. This Skill provides judgment, user-facing explanation, and the next-step decision.

## Required Inputs

- `python3 scripts/evozeus_wrapper.py env diagnose --json`
- `python3 scripts/evozeus_wrapper.py skill diagnose --target /absolute/path/to/repo --repo OWNER/REPO --json`
- Evolution surface decision from `skills/evolution-surface-diagnosis/SKILL.md`

Do not invent missing fields. If diagnosis JSON is incomplete, say which field is missing and rerun diagnosis first.

## Assessment Order

Explain the process in this order so the user can feel where we are:

1. **环境检查**：`~/.evozeus`、`git`、`gh`、`gh auth` 是否可用。
2. **GitHub 权限检查**：repo 是否存在，visibility 是什么，当前账号是 `READ` / `WRITE` / `ADMIN`。
3. **Repo 架构判断**：`single_skill`、`runtime_skill_bundle`、`hooked_skill_bundle`、`skill_bundle`、`agents_runtime` 或 `unknown`。
4. **Runtime 集成能力**：读取 `skill.integration.mode` 和 `skill.integration.capabilities`，逐项说明 `scope`、是否 native、是否覆盖 Skill invocation。project-local hook 只能描述成 canonical repository maintenance；user-level dispatcher 的 installed/trust 状态以本机诊断覆盖值为准。
5. **自进化说明位置**：读取 `skills/evolution-surface-diagnosis/SKILL.md` 的 decision，说明为什么放在那里。
6. **缺失组件**：读取 `skill.component_gaps.missing_files` 和 `missing_concepts`，说明缺什么。
7. **版本策略**：读取 `version.status` 和 `requires_owner_choice`，说明是否能继续。
8. **下一步**：选择 transform mode 或停止条件。

## Human Labels

Use these labels when explaining `target_kind`:

| target_kind | 用户可理解说法 |
|---|---|
| `single_skill` | 单 Skill 仓库 |
| `runtime_skill_bundle` | Runtime Kit / 多 Skill 系统 |
| `hooked_skill_bundle` | Hook / Plugin 控制的 Skill bundle |
| `skill_bundle` | 多 Skill bundle |
| `agents_runtime` | AGENTS.md 控制的 Agent runtime |
| `unknown` | 结构未知 |

## Decision Rules

Stop before transform when:

- `env.next_action == "install_evozeus"`：先安装 / 初始化 EvoZeus。
- GitHub repo exists but `repo.access.can_write` is false：不能直接写目标 repo；改走 fork / local patch / 申请权限。
- Evolution surface decision is missing：先运行 `skills/evolution-surface-diagnosis/SKILL.md`。
- Evolution surface decision returns `NEEDS_OWNER_CHOICE`：需要 owner 选择 instruction surface。
- `version.requires_owner_choice` is true：已有 repo 缺 GitHub release 和 CHANGELOG 版本，owner 必须先确认首个 Skill / kit release 版本。
- `source_contract.status == "error"`：先修 source contract。

Choose next transform mode from harness state:

| harness.state | repo.exists_on_github | next |
|---|---|---|
| `complete` | any | `verify` |
| `partial` | any | `repair` |
| `missing` | `true` | `attach` |
| `missing` | `false` | create the independent Repo first |
| `missing` | `null` | rerun repo check |

## Output Shape

Use concise Chinese. Do not dump raw JSON.

```text
状态判断：<一句话结论>

流程进度：
1. 环境检查：<PASS / NEEDS_ACTION> - <原因>
2. GitHub 权限：<PASS / NEEDS_ACTION> - <原因>
3. Repo 架构：<PASS / NEEDS_ACTION> - <用户可理解类型；Skill 数量>
4. Runtime 集成：<PASS / NEEDS_ACTION> - <integration.mode + capability scopes + global install/trust 状态>
5. 自进化说明位置：<PASS / NEEDS_ACTION> - <surface decision path + reason>
6. 缺失组件：<PASS / NEEDS_ACTION> - <缺失数量和关键项>
7. 版本策略：<PASS / NEEDS_ACTION> - <版本结论>

阻塞项：
- <没有则写“无”>

下一步：
- <如果可继续，给出具体命令>
- <如果不可继续，给出先要用户确认或补齐的事项>
```

## Command Templates

If ready to continue:

```bash
python3 scripts/evozeus_wrapper.py skill transform --mode <attach|adopt|repair|verify> \
  --target /absolute/path/to/repo \
  --repo OWNER/REPO \
  --instruction-surface <relative path> \
  --visibility <public|private> \
  --dry-run \
  --json
```

For `verify`, omit `--visibility` and `--dry-run` if the CLI mode requires it:

```bash
python3 scripts/evozeus_wrapper.py skill transform --mode verify --target /absolute/path/to/repo
```

## Guardrails

- Do not move user-facing assessment logic into Python scripts.
- Do not write files during status assessment.
- Do not proceed from assessment to transform if a stop condition is present.
- Do not say “ready” when version requires owner choice.
- Do not hide why the evolution surface was selected; explain the controlling evidence, such as `AGENTS.md`, hooks, plugin manifests, or a hook-loaded Skill.
- Do not treat script-produced `evolution_surface.candidates` as final placement; they are evidence for the evolution surface diagnosis Skill.
- Do not call `prompt_runtime_check`, `manual_only`, project-local hooks, or wrapper CLI commands native per-Skill invocation hooks.
- Do not infer user-level global hook installation from a portable target manifest; use the live diagnosis overlay.
