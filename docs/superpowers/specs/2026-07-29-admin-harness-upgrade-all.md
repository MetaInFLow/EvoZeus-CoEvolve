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
5. `MAINTAIN`、`WRITE`、`TRIAGE`、`READ` 和未知权限只进入跳过清单。
6. 所有发布都走独立分支和 PR；禁止直接写默认分支。
7. UAT Harness 不得向目标 repo 默认分支发布；批量发布只消费权威 Stable CoEvolve Release。

## 执行流程

```text
Registry discovery
  -> authoritative Stable Harness resolution
  -> source completeness check
  -> per-repo GitHub ADMIN check
  -> isolated worktree from origin/default-branch
  -> managed Harness migration
  -> structure/preflight + git diff check
  -> commit + push branch
  -> create or reuse one PR
  -> append local run and event ledger
```

## 隔离与幂等

- worktree 根目录：`~/.evozeus/worktrees/harness-upgrade/<run-id>/<owner>--<repo>`。
- 分支：`evozeus/harness-<from>-to-<to>`。
- 相同 head branch 已存在开放 PR 时复用，不重复创建。
- canonical repo 工作区保持不变。
- 单 repo 失败不影响已创建的其他 PR；批次状态为 `partial`。
- 失败 worktree保留并返回恢复路径；成功 worktree清理。

## 审计

- 批次报告：`~/.evozeus/skills/runs/<run-id>.json`。
- 事件账本：`~/.evozeus/skills/events.jsonl`。
- 只记录 repo、版本、commit、PR、状态和时间，不记录 raw session、客户资料或 secret。

## 验收标准

1. 普通用户可生成计划，但无法发布。
2. 非 `ADMIN` repo 不发生 worktree、commit、push 或 PR 写入。
3. `ADMIN` repo 从隔离 worktree升级，canonical checkout字节与分支保持不变。
4. 每个成功目标得到独立 commit、远端分支和 PR URL。
5. Managed Harness升级保留Skill业务内容。
6. 相同升级重复执行不会创建重复 PR。
7. 本地Run Ledger完整记录成功、跳过和失败。

