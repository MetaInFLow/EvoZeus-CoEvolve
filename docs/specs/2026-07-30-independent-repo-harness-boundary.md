# CoEvolve 独立 Repo Harness 边界

状态：Active  
日期：2026-07-30  
上游决策：[EvoZeus ADR-0005](https://github.com/MetaInFLow/EvoZeus/blob/main/docs/decisions/ADR-0005-plugin-first-monorepo-and-repo-scoped-harness.md)

## 决策

1. Evolution Harness 的最小治理单位是独立 Git Repo。
2. 一个 Repo 最多拥有一个活动 Harness，固定在根目录 `.evozeus-wrapper/`。
3. Repo 内 Skill、package、pack、app 和生成物继承根 Harness。
4. 子模块需要独立 Harness 时，先成为具备独立 Owner、Issue、PR、UAT、Release 和回滚边界的 Git Repo。
5. 普通用户可以诊断和生成计划；Harness 写入、升级和上传只允许目标 GitHub Repo 的管理员执行。

## CLI 行为

| 输入 | 行为 |
|---|---|
| 独立 Repo 根目录 | 使用该根目录 |
| Repo 内 Skill 或 package 路径 | 归一到 Repo 根目录 |
| 普通文件夹 | 阻塞，不生成 Harness |
| 含嵌套活动 Harness 的 Repo | 阻塞，要求先迁移到根目录 |
| 只读诊断或 dry-run | 不要求管理员权限 |
| attach、migrate、upgrade、upload | 验证 GitHub `viewerPermission=ADMIN` |

## 迁移规则

- 已存在的根 Harness 保持原版本轴。
- 已存在的嵌套 Harness 不自动覆盖或合并；先输出路径和冲突，再由 Repo Owner 决定迁移。
- 接入 Harness 不重置 Skillware Release。
- `~/.evozeus/.projects/OWNER/REPO` 必须指向 Repo 根目录。
- `upgrade-all` 在第一次写入前完成全部 Repo 边界、管理员权限、干净工作区和写入路径检查；任一目标失败则零写入。

## 验收

- Repo 子目录输入返回根目录 Harness 位置。
- 普通文件夹被拒绝。
- 嵌套 `.evozeus-wrapper/wrapper.json` 被拒绝。
- `WRITE` 或 `MAINTAIN` 权限不能执行 Harness 写入。
- `ADMIN` 权限通过后，现有迁移回滚测试仍全部通过。
