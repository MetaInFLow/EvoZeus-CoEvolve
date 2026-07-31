---
name: using-evozeus-harness
description: Use when a wrapped Skill entry activates the canonical EvoZeus-CoEvolve Harness contract before its business flow.
metadata:
  version: "v1.1.0"
---

# Using EvoZeus Harness

本 Skill 是 EvoZeus-CoEvolve 在目标仓库内唯一的 Harness 指令事实源。目标业务入口只保留指向本文件的强制 Read 块；读取完成后，先确认状态，再进入业务主链路。

## 运行时契约

1. 读取同一仓库的 `.evozeus-wrapper/wrapper.json`，以 `canonical_repo`、`instruction_surface`、`wrapper_version`、`harness_skill_path`、`harness_skill_version`、`integration.mode` 和 `integration.capabilities` 为事实。
2. 定位 manifest 所属的本地 canonical Repo 根目录并在该目录执行只读检查。`canonical_repo` 是 `OWNER/REPO` 标识，禁止把它当作 `--target` 路径：
   - `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure --target .`
   - `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --target .`
   - `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py identity --target . --json`
3. 将 `identity --json` 返回的 `runtime_identity.display_line` 放在本次调用第一条用户可见消息首行，同一次调用只显示一次。
4. identity 由 `.evozeus-wrapper/policies/notice-policy.json` 渲染；普通业务输出不展示额外 EvoZeus Notice。
5. 检查通过后进入业务主链路。兼容旧 Harness 产生维护提醒；路径越界、manifest 损坏、canonical Harness Skill 缺失或版本不兼容会阻断执行。

按 manifest 解释 integration 事实，不根据模板文字推断当前模式：

- `prompt_runtime_check`：instruction surface 提供检查入口，执行依赖指令遵循。
- `bootstrap_skill`：Plugin lifecycle 可加载控制 Skill；具体覆盖范围以 `plugin_lifecycle_hook` capability 和实际 hook/plugin 文件为准。
- `manual_only`：没有已证明的自动入口，按需运行只读命令。
- 其他 mode：逐项核对 `capabilities` 与实际文件后再说明覆盖范围。

任何 mode 都不能自行宣称存在 native `SkillInvoke`。仅当 manifest capability 与宿主实际证据共同证明 `skill_invocation_hook` supported、installed 且 native enforced 时，才能报告原生单次 Skill invocation 覆盖；repo maintenance hook 与全局 session dispatcher 仍各自遵守 manifest 中的 scope。

若当前目录属于 runtime-only install，所有维护动作回到 `canonical_repo` 执行。安装副本只用于运行和定位事实源。

## 意图路由与授权边界

正常业务调用只执行上面的运行时契约。以下低频资料在对应意图出现后再读取：

- 业务调用：完成上述只读检查，继续目标 Skill 的业务流程。
- Feedback Issue：按需读取 `.evozeus-wrapper/policies/feedback-policy.json` 与 `.evozeus-wrapper/policies/audit-rule.md`；先纠正当前结果并形成脱敏 Lesson，仅在用户确认记录后创建 Skill Feedback Issue。
- Issue-to-PR：仅在 Feedback Issue 已存在且用户另行授权实现后进入下方“贡献分支门禁”。创建或切换分支/worktree、提交、推送与 PR 继续使用各自授权门。
- Harness 维护：按需读取 `.evozeus-wrapper/WRAPPER.md` 与 `.evozeus-wrapper/docs/migrations/README.md`，先给出 upgrade-check 或迁移计划；普通 Skill 调用不授权 Harness 写入、升级或发布。
- UAT：按需读取 `.evozeus-wrapper/WRAPPER.md` 的验收门禁，仅在用户要求验收时进入并记录证据与结论。
- Release：按需读取 `.evozeus-wrapper/CHANGELOG.md` 与 preflight release 合同，仅在用户授权发布后执行，要求版本、tag 与 release notes 一致。
- rollback：出现验证失败或不兼容时停止写入，按 `.evozeus-wrapper/docs/migrations/` 的迁移记录回滚当前维护提交。

私有会话、客户资料、secret 与未脱敏商业上下文不得进入公开 Issue、设计文档、PR 或 Release 记录。

## 贡献分支门禁

任何目标业务文件写入前，先读取 manifest 的 `contributor_branch`，并在 canonical Repo 根目录运行只读计划：

```bash
python3 .evozeus-wrapper/scripts/evozeus_branch_consumer.py plan \
  --profile coevolve_target_skillware_consumer \
  --repo <canonical_repo> \
  --repo-path <canonical-repo-root> \
  --base origin/main \
  --issue OWNER/REPO#NUMBER \
  --actor <expected-github-login> \
  --type <dev|bug|refactor|docs|test|chore> \
  --component <lowercase-component> \
  --summary <lowercase-summary> \
  --permission <direct|fork|local> \
  --worktree <absolute-isolated-worktree> \
  --json
```

`--actor` 与 `--permission` 只表达预期。Core planner 每次重新采集 Git 状态、GitHub identity、repository permission、fork policy 与 Issue evidence；manifest 和旧 ledger 均无权覆盖实时证据。权限证据缺失或不完整时权限路径降级为 local patch，禁止 push/PR；Issue 无法查询、已关闭、实际为 Pull Request、Repo/编号不匹配，或缺少 `skill-feedback` / `[Skill Feedback]` 分类时整个计划阻断。

向用户完整展示 `repo.canonical`、`base.ref`/`base.commit`、`branch.target`、`issue_evidence`、actor、`permission_path.resolved`、`permission_evidence`、隔离 worktree、resume decision、`next_write_action` 和 blockers。满足以下条件后才能进入业务写入：

1. blockers 为空，canonical checkout 与当前 checkout 均 clean，目标 worktree 位于 canonical checkout 之外。
2. 用户明确授权计划中的 branch/worktree 动作；随后用同一参数增加 `--approve-save-plan`，只把脱敏计划写入私有 ledger。
3. 按 `next_write_action` 创建或恢复独立 branch/worktree，并再次运行计划确认 `resume.decision=resume`。

默认 ledger 位于 `~/.evozeus/coevolve/branch-plans/OWNER/REPO/<resume-key>.json`，仅保存 owner 可读写内容。后续 commit、push、PR 前都使用该文件作为 `--resume-plan` 重新执行实时门禁；repo、base ref、base commit、target branch、actor 或 resolved permission 任一变化即停止并要求 Owner 重新确认。

PR 描述只复制输出中的 `pr_metadata`，不得发布 ledger 路径、Repo 本地路径、worktree 路径或内部错误。目标 PR 检查使用 `pull_request_target`：从 base SHA 执行可信 validator，把 head SHA 仅作为数据读取，通过 live GitHub event/API 重算 actor、head Repo 对应的 direct/fork、Issue 状态/类型/分类与 resume key。候选分支中的 validator、consumer 或 workflow 不参与本次信任判定。

官方 Harness upgrade 使用独立 gate，并消费 CoEvolve PR #31 admin publisher 的 `evozeus/harness-vX-to-vY` 输出。Head 必须来自 canonical Repo，PR author 必须由 live API 证明为 `ADMIN`；除 target-owned Changelog 外，全部 managed files 都从已发布且非 prerelease 的 CoEvolve Release 取得 source 并完成目标渲染后核对，`.codex/hooks.json` 保持非 wrapper entries。Diff 只允许官方 managed files、canonical manifest、受所有权 marker 约束的 activation surface 与版本迁移记录。该 profile 不要求 Contributor Branch Plan 元数据。Issue 授权、ledger 保存与 branch plan 均不自动授权 commit、push 或 PR。

## 维护闭环

Harness 维护采用 Issue → design doc → focused implementation → tests → UAT → Release → rollback record。目标业务规则始终由业务入口拥有；迁移只删除具备明确 wrapper 所有权签名的历史段落，并保持其余字节内容。
