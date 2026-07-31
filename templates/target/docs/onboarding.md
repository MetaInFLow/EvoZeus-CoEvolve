# 安装、初始化与子 Skill 接入

本页是 wrapper 的通用接入契约。实际命令以 `.evozeus-wrapper/wrapper.json` 的 `onboarding` 字段为准。

## 安装

1. 在 EvoZeus-CoEvolve repo 中运行 `onboarding.installation.command`，把 runtime install 建成指向 canonical repo 的 symlink。
2. 真实目录不会被删除。先用 `--dry-run` 查看计划；确认归档后显式增加 `--approve-archive`。原目录归档到 `~/.evozeus/archives/runtime-installs/`。
3. 运行 `onboarding.installation.verification`，确认 runtime pointer 和 canonical repo 一致。
4. runtime Skill 安装与全局 hook 安装是两个状态；软链接成功不代表 `global_session_dispatcher` 已安装或已信任。

## 全局 Dispatcher

- 在 EvoZeus-CoEvolve repo 先运行 `python3 scripts/evozeus_wrapper.py hook global plan --json`。
- 用户明确确认后运行 `hook global install --approve --json`，结构化合并 `~/.codex/hooks.json`。
- 安装后通过 `/hooks` 审核，再用 `hook global trust --status trusted --approve --json` 记录审核结果。
- global dispatcher 在 `SessionStart` 聚合检查全部 registered wrapped Skills，不精确绑定随后被选中的某个 Skill。

## 调用

- 调用触发词和业务入口归目标 Skill 的 canonical `SKILL.md` 所有，wrapper 不猜测业务调用方式。
- 按 `onboarding.invocation.instruction` 在新的消费项目会话中调用 Skill。
- 运行 `onboarding.invocation.verification`，确认宿主加载的是 canonical repo，并通过 consumer-project smoke test。
- Skill 被选中后，instruction surface 的 `skill_entry_preflight` 在业务主链路前检查当前 Skill；该步骤不是 native hook。
- 启动身份与后续 EvoZeus 生命周期事件由 `.evozeus-wrapper/scripts/evozeus_notice.py` 按 `.evozeus-wrapper/policies/notice-policy.json` 渲染。
- 用 `python3 .evozeus-wrapper/scripts/evozeus_notice.py render --kind lesson --state pending --message "smoke" --json` 验证本地 Notice 能力；结果必须包含 `writes=false`。
- 普通业务输出不使用 EvoZeus Tag；可复用 Lesson 在业务纠正完成后独立展示。

## 初始化

- 初始化逻辑归目标 Skill 所有，wrapper 不实现 company 或业务专用初始化。
- 当 `onboarding.initialization.required` 为 `true` 时，必须运行其中的 `command`，再运行 `verification`；两者缺一不可。
- 当 `required` 为 `false` 时，不额外推断或执行初始化。

## 生成的子 Skill

- 子 Skill 继承父级 Git Repo 根目录的 Evolution Harness，不创建嵌套 `.evozeus-wrapper/`。
- 子 Skill 的运行入口和 consumer-project smoke test 仍需单独验证。
- Repo 级 host adapter 由父 Repo 统一审核和维护。
- 子 Skill 只有在成为具备独立 Owner、Issue、PR、UAT 与 Release 边界的 Git Repo 后，才可以接入自己的 Harness。

## Issue-to-PR 贡献分支

- Harness attach 之前，默认分支必须把 exact `EvoZeus Contributor Gate` 配置为 protected required status check，并绑定 GitHub Actions `app_id=15368`；bootstrap 使用 GitHub API 只读确认，并在任何写入前拒绝未知 preexisting gate bytes。缺失或无法取证时停止。
- Feedback Issue 和实现授权成立后，先运行 `.evozeus-wrapper/scripts/evozeus_branch_consumer.py plan`；该步骤只读。
- `wrapper.json` 的 `contributor_branch` 给出 consumer、Core contract/planner snapshot 与 provenance 路径。执行前用 `verify-snapshot --json` 校验摘要和来源。
- Agent 必须先展示 canonical repo、base ref/commit、`issue_evidence`、目标 branch、verified actor、resolved permission、`permission_evidence`、隔离 worktree、next action 与 blockers。
- blockers 清空后仍需 branch/worktree 单独授权。增加 `--approve-save-plan` 只保存 owner-only 本地 ledger；分支、worktree、commit、push 与 PR 按各自授权执行。
- 每位参与者使用独立 branch/worktree；canonical checkout 保持 clean。后续动作通过 `--resume-plan` 重新取实时证据并核对完整身份；未重传 `--date` 时从 purpose 匹配的 validated ledger target branch 恢复原日期。Ownership 时间窗超期且身份完全匹配时，Owner 显式增加 `--reconfirm-owner`；refreshed ledger 仍须通过 `--approve-save-plan` 单独保存。
- Issue 后续发生编辑、关闭、转移、重新打开或分类标签变化时，trusted issue job 会重跑精确关联的开放 PR gate；PR required check 以最新 live Issue evidence 为准。
- Length-bounded target branch 包含 verified actor；Direct 只对未 archived/disabled 的可写 Repo 成立并使用 canonical origin，fork 要求已配置 remote 的有效 fetch/push URL 全部精确指向 verified actor fork。Effective origin fetch identity 精确匹配 canonical Repo 后才允许 live 查询。Canonical base 与 direct/fork target branch 使用对应 live remote 取证；cached base 过期、本地/remote target 分叉、local/live remote ref namespace 冲突、resume target 仅存在于 live remote、requested resume worktree dirty/status 不可用或 live top-level/common dir/branch 与 registration 不一致、prunable path 仍存在或路径祖先不可作为目录时阻断。Remote-only resume 需要独立授权 fetch 目标 ref 并创建本地分支，再重新计划。无法证明 direct/fork 时权限路径进入 local patch；push 与 PR 保持禁用。Issue 无法 live 验证为同 Repo 的 OPEN Skill Feedback Issue 时，计划整体阻断。
- 业务 PR 由 base-SHA `pull_request_target` validator 读取候选数据并重算 plan identity；候选脚本不执行。官方 Harness upgrade 走 direct + live ADMIN + published Stable Release + restricted diff 专用 gate，不要求业务 Contributor Plan。

## Dashboard

- repo-local dashboard 始终保留在 `.evozeus-wrapper/docs/`。
- workflow validation 不依赖 GitHub Pages。
- 只有确认仓库和当前 plan 支持 Pages 后，才设置 repository variable `EVOZEUS_PAGES_ENABLED=true`；否则保持 repository-only mode。
