# EvoZeus-CoEvolve Contributor Branch Gate v1 设计规范（Issue #36，实施候选）

- 状态：PR 实施候选，等待依赖合入与 Owner review
- 受众：EvoZeus / CoEvolve 维护者、目标 Skillware Owner、贡献者
- 用途：统一 Issue-to-PR 首次写入门禁、恢复上下文与验收口径
- 上游依赖：[EvoZeus Core PR #47](https://github.com/MetaInFLow/EvoZeus/pull/47)
- 基线依赖：[EvoZeus-CoEvolve PR #40](https://github.com/MetaInFLow/EvoZeus-CoEvolve/pull/40)

## 核心判断

Issue-to-PR 的主要风险发生在首次业务写入之前：Repo、base、参与者、权限路径、目标分支和 worktree 尚未形成共同事实，Agent 已经可能沿用随机 checkout 开始修改。

v1 门禁把这组事实压缩成一个可展示、可恢复、可阻断的 branch plan。EvoZeus Core 提供唯一规则和只读 planner；CoEvolve 固化经摘要校验的离线快照，负责目标 Repo 注入、私有 ledger、公开 PR 元数据和流程强制执行。

## 成功标准

1. 所有目标文件写入发生在通过门禁的独立 branch/worktree 中。
2. canonical checkout 在并行贡献期间保持 clean。
3. direct、fork、local 由实时 GitHub 证据决定；调用参数、manifest 和 ledger 只提供预期与恢复上下文。
4. branch plan 可被用户完整理解；任一 blocker 会在业务写入前停止流程。
5. resume 绑定 Repo、base ref、base commit、target branch、actor、resolved permission 和稳定 resume key。
6. ledger 只存在于本机 owner-only 目录；PR 仅携带公开安全元数据。
7. 已接入目标可通过 Harness upgrade 获得同一门禁。
8. 默认分支把 exact `EvoZeus Contributor Gate` 作为 protected required status check；fresh attach 在首次模板写入前通过 GitHub API 验证，无法确认则阻断。

## 权威来源与供应链

权威文件来自 EvoZeus Core revision `5ccddc77a77e8dbe02dde54e4588a01a25ebce7a`：

- contract：`evozeus.contributor_branch` `1.3.0`
- planner：`scripts/evozeus-branch-preflight.mjs`
- canonical contract `$id`：`https://github.com/MetaInFLow/EvoZeus/blob/main/contracts/v1/contributor-branch-contract.json`

CoEvolve 在目标模板中保存 byte-for-byte snapshot 和 provenance。Consumer 在每次计划前验证 source revision、contract identity/version、canonical `$id`、contract/planner SHA-256、普通文件边界与 symlink 边界。运行期不下载 contract 或 planner；来源 revision 变化时必须重新生成 snapshot、摘要与 provenance。

## 写入前流程

1. Feedback Issue 已存在，用户另行授权实现。
2. Consumer 读取目标 manifest 的 canonical Repo 与受管资产路径。
3. Core planner 实时读取 Git worktree/base/branch 状态、有效 fetch/push 目标、live remote target branch、GitHub identity、viewer permission、Repo archived/disabled 状态、fork policy 与 Issue evidence。
4. 用户看到 repo、base ref/commit、Issue 状态/类型/分类、target branch、verified actor、resolved permission、证据来源/时间、isolated worktree、resume decision、next action 与 blockers。
5. blockers 为空后，用户单独授权 branch/worktree 动作。
6. `--approve-save-plan` 把脱敏计划原子写入私有 ledger；Agent 执行 planner 声明的 next action。
7. 首次业务写入前再次以 ledger 执行 resume 计划，确认同一上下文。
8. commit、push、PR 前重复实时门禁；对应动作继续使用独立授权。

Consumer 只生成计划与可选 ledger 记录，不创建 branch/worktree，不 commit、push 或创建 PR。

## 权限路径

| 实时证据 | 路径 | 边界 |
| --- | --- | --- |
| 完整证据证明 `ADMIN` / `MAINTAIN` / `WRITE`，且 Repo 未 archived/disabled | direct branch | canonical Repo 独立分支，可在后续授权后 push/PR |
| 完整证据证明 `READ` / `TRIAGE` 且 fork policy 允许 | fork PR | contributor fork 独立分支，可在后续授权后 PR |
| `gh` 缺失、API 不可用、证据不完整、fork 不允许 | local patch | push/PR 固定禁用 |

`--permission` 是用户看到的期望值。期望与实时解析结果不同会产生 `permission_expectation_mismatch`，避免静默改变执行路径。

本地 `remote.origin` 的有效 fetch URL 与全部有效 push URL 都必须解析为声明的 exact GitHub Repo；`pushurl`、`insteadOf` 或 `pushInsteadOf` 指向其他 host/Repo 时直接阻断。
目标 branch 固定包含 verified actor 的小写 login；相同日期和 purpose 下的不同 actor 拥有不同 branch/resume identity。目标 branch 是否存在通过有效 origin 的 live `git ls-remote` 取证；查询不可用、本地/live remote 同名分支分叉时阻断。已注册的 requested resume worktree 还必须自身 status 可用且 clean。

## Ledger 与公开元数据

默认位置：

```text
~/.evozeus/coevolve/branch-plans/OWNER/REPO/<resume-key>.json
```

目录权限固定为 `0700`，文件权限固定为 `0600`，写入采用同目录临时文件加原子替换。路径各层拒绝 symlink；Repo slug 与 resume key 使用严格白名单。

Ledger 删除 canonical Repo、worktree 和 ledger 的绝对路径。已有记录发生 resume key、Repo、actor、base ref、base commit、target branch 或 resolved permission 冲突时停止覆盖。

PR 只使用 `pr_metadata`：contract revision/digest、profile、purpose、resume key、Repo、base、branch、Issue、verified actor、resolved permission 和脱敏 planning evidence 摘要。planner stderr、内部错误、ledger 路径和本地路径不得进入公开面。

业务 PR 的公开字段只用于提交计划身份，不承担自证。`pull_request_target` workflow 从 event 指定的 exact base SHA checkout 执行可信 validator/consumer，再把 exact head SHA checkout 当作数据读取。Validator 以 live event/API 核验 canonical Repo、head Repo 对应的 direct/fork 路径、PR author、base、OPEN Issue/非 PR/Skill Feedback 分类，并按合同字段重算 resume key。候选分支内的 workflow、validator、consumer 与 timestamp 不参与本次信任判定。

Issue 的 `edited/deleted/transferred/closed/reopened/labeled/unlabeled` 事件会由默认分支中的 trusted issue job 定位 PR body 精确引用该 Issue 的开放业务 PR，并通过 GitHub Actions API 重跑各 PR 当前 head 对应的最新 `pull_request_target` workflow run。原 PR check 在原 ref 上重新读取 live Issue evidence，required check 随之进入 pending 并更新为 success/failure；已有 run 正在执行时不重复排队，找不到当前 head 的 trusted run 时 fail closed 并要求 edit/reopen PR。

官方 Harness upgrade 使用独立 profile，直接消费 [CoEvolve PR #31](https://github.com/MetaInFLow/EvoZeus-CoEvolve/pull/31) 的 admin publisher 合同：branch 为 `evozeus/harness-vX-to-vY`，head 来自 canonical Repo，PR author 由 live API 证明为 `ADMIN`。除 target-owned `.evozeus-wrapper/CHANGELOG.md` 外，全部 wrapper-managed files 都与 base 绑定；upgrade 时从同版本、已发布且非 prerelease 的 CoEvolve Release 取得每一份官方 source，完成目标占位符渲染后逐字节核对。`.codex/hooks.json` 只替换 wrapper-owned entry并保持其他 target hooks。PR template 仅在内容命中已知完整受管基线时整体刷新；其他模板保留 target-owned bytes，只确定性注入或替换 `evozeus-contributor-branch-plan:v1` marker block，marker 或未标记同名 section 无法安全归属时在 plan 阶段阻断。API diff 只允许官方 managed controls、canonical manifest、所有权 marker 内 activation 内容和明确版本迁移记录。Marker 外业务字节、额外业务文件、rename source、fork copy 或 manifest target/source identity 变化均阻断。该 profile 跳过业务 Contributor Plan metadata。

## 失败与恢复

| 条件 | 结果 | 恢复动作 |
| --- | --- | --- |
| snapshot/provenance/digest 不一致 | 停止 | 从已审核 Core revision 重新生成受管快照 |
| planner 超时或输出结构/退出码矛盾 | 稳定 blocker | 检查本机 Node/Git，修复后重新计划 |
| dirty canonical/current checkout | 停止 | 清理或保留现状并选择新的 clean 上下文 |
| wrong base / branch collision / worktree collision | 停止 | 回到 canonical base，重新选择目标或提供匹配 ledger |
| ledger 超过 ownership 时间窗且完整身份仍匹配 | 默认停止 | Owner 显式增加 `--reconfirm-owner` 生成 refreshed plan；持久化仍需 `--approve-save-plan` |
| ledger 完整身份变化 | 停止 | 原 ledger 不可重新确认；重新核对 owner、base、branch 与授权 |
| permission evidence 缺失 | local permission path | 保持 push/PR 禁用，或恢复证据后重新计划 |
| live target remote evidence 缺失或分叉 | 停止 | 恢复 effective origin 查询并对齐本地/live remote branch |
| requested resume worktree dirty/status 不可用 | 停止 | 处理该 worktree 的已有改动或状态错误后重新计划 |
| Issue evidence 缺失、关闭、PR-shaped 或未分类 | 停止 | 恢复 live OPEN Skill Feedback Issue 证据 |
| candidate PR control code 与 base 不一致 | 停止 | 普通业务 PR 撤销控制面改动 |
| 官方 Harness upgrade provenance/ADMIN/diff 不满足 | 停止 | 使用 published Stable Release 和专用迁移分支重新生成 |

## 升级与回滚

Harness upgrade 把 consumer、Core contract/planner snapshot、provenance、canonical Harness Skill、manifest `contributor_branch`、onboarding、workflow 和 PR metadata surface 作为同一受管集合刷新。升级 PR 由 base validator 通过官方 Release provenance、live ADMIN 与受限 diff 专用 gate；升级后必须通过 structure 和 snapshot verification。

升级不读取或迁移用户的本地 branch ledger。回滚通过撤销目标 Harness upgrade commit 完成；已存在 branch/worktree 保持原 Git 状态，由 Owner 决定保留或清理。

## 验收证据

- clean new branch 与 matching resume
- dirty tree、wrong base、branch collision
- direct、fork-only、no-PR local
- 缺少 `gh`、partial permission evidence、archived/disabled Repo 与 invalid Issue evidence 的 fail-closed 行为
- 有效 fetch/push URL、multi-pushurl 与 Git URL rewrite 校验
- actor-exclusive branch、live target remote 与 requested resume worktree status 校验
- canonical checkout 后代与 symlink alias 路径阻断
- snapshot digest/provenance/symlink 校验
- ledger `0700/0600`、原子写入、路径脱敏与完整身份 collision
- planner timeout、退出码/blockers 矛盾与 stderr 不外泄
- base-validator/candidate-data-only workflow、business plan identity 重算、fresh attach、official ADMIN upgrade、target structure 和 workflow smoke
