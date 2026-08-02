# 管理员批量 Harness 升级与 GitHub PR 发布

## 状态

- 状态：开发中
- 日期：2026-07-29
- 入口所有者：EvoZeus
- 执行所有者：EvoZeus-CoEvolve

## 目标

让管理员通过一个 EvoZeus 指令发现所有已注册且 Harness 过时的 Skill，在隔离 worktree 中完成升级和验证，并为每个有管理员权限的 canonical repo 创建独立 Pull Request。

## 用户命令

```text
evozeus harness upgrade-all
evozeus harness upgrade-all --publish
```

- 无 `--publish`：只读计划，不写本地目标 repo，不写 GitHub。
- 有 `--publish`：显式授权本次批量发布；仍需逐 repo 验证 GitHub `viewerPermission=ADMIN`。

## 权限边界

1. 本地配置不能自证管理员身份。
2. GitHub 是 repo 管理权限事实源。
3. 每个目标 repo 独立查询当前登录身份的 `viewerPermission`。
4. 只有 `ADMIN` 可以进入升级 worktree、push 和 PR 创建阶段。
5. push 前和 PR 创建前分别重新查询 live `ADMIN`、actor、default branch 与 base commit；规划期权限不作为执行授权。
6. `MAINTAIN`、`WRITE`、`TRIAGE`、`READ` 和未知权限只进入跳过清单。
7. 所有发布都走独立分支和 PR；禁止直接写默认分支。
8. UAT Harness 不得向目标 repo 默认分支发布；批量发布只消费权威 Stable CoEvolve Release。
9. `wrapper-root` 必须是 clean 的独立 Git checkout，origin 精确指向官方 CoEvolve repo，本地 tag、远端 tag 与 HEAD 必须解析到同一 commit；contract manifest 与其声明文件必须通过摘要核验。
10. 目标 canonical checkout 的 origin fetch URL 与 push URL 都必须精确指向目标 GitHub repo，禁止用独立 pushurl 绕过目标身份检查。

## 执行流程

```text
Registry discovery
  -> authoritative Stable Harness resolution
  -> source completeness check
  -> per-repo GitHub ADMIN check
  -> isolated worktree from origin/default-branch
  -> managed Harness migration
  -> structure/preflight + git diff check
  -> commit + owner-only prepared receipt
  -> push new branch + live head verification + pushed receipt
  -> create PR + live PR read-back
  -> append local run and event ledger
```

## 隔离与幂等

- worktree 根目录：`~/.evozeus/worktrees/harness-upgrade/<run-id>/<owner>--<repo>`。
- 分支：`evozeus/harness-<from>-to-<to>`。
- Recovery Receipt：`~/.evozeus/skills/harness-upgrade-receipts/<owner>/<repo>/<branch-sha256>.json`；目录固定 `0700`，文件固定 `0600`，禁止 symlink 与路径逃逸。
- Receipt 在 push 前以 `prepared` 原子落盘，绑定 repo、verified actor、target base/head、official source tag/commit、manifest digest、changed-files digest 和 plan identity；远端 head 精确匹配后转为 `pushed`，PR read-back 通过后转为 `pr_created`。
- 相同 head branch 或开放 PR 只在本地 Receipt、live head/base repo/ref/commit、PR marker 全部一致时复用。PR marker 是公开审计信息，不能单独证明发布来源。
- 已有远端 branch 或 PR 缺少匹配 Receipt 时返回 `manual_review_required`，不迁移、不 force push、不创建或复用 PR。
- canonical repo 工作区保持不变。
- 单 repo 失败不影响已创建的其他 PR；批次状态为 `partial`。
- push 前失败保留 worktree 和恢复路径；push 已成功时以确定性 branch/commit 与账本作为恢复点并清理 worktree，避免重试时占用同一分支；成功 worktree清理。
- push 成功但 PR 创建失败时，Receipt 是立即可用的本地恢复事实源；Run Ledger 和 Event Ledger 记录 branch、commit、base、source revision、manifest/changed-files digest、plan identity 与恢复动作。重试只消费 Receipt 精确绑定的同一 commit。

## 审计

- 批次报告：`~/.evozeus/skills/runs/<run-id>.json`。
- 事件账本：`~/.evozeus/skills/events.jsonl`。
- 只记录 repo、版本、branch/commit、PR、target base、official source revision、manifest/changed-files digest、plan identity、恢复动作、稳定 error code、白名单错误摘要、状态和时间。
- raw exception、command argv、absolute local path、Authorization/Bearer、token/query secret 不进入 Run Ledger 或 Event Ledger。

## 验收标准

1. 普通用户可生成计划，但无法发布。
2. 非 `ADMIN` repo 不发生 worktree、commit、push 或 PR 写入。
3. `ADMIN` repo 从隔离 worktree升级，canonical checkout字节与分支保持不变。
4. 每个成功目标得到独立 commit、远端分支和 PR URL。
5. Managed Harness升级保留Skill业务内容。
6. 相同升级重复执行不会创建重复 PR。
7. 本地Run Ledger完整记录成功、跳过和失败。
8. 公开 marker 自行构造、同名远端 branch、PR body 篡改、PR 创建后 head/base 漂移均无法绕过 Receipt 与 live read-back。
9. push 后进程中断时，`prepared` Receipt 与精确 remote head 可恢复为 `pushed`；任何不一致进入人工审查。
