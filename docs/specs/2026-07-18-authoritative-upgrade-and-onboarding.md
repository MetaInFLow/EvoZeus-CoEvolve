# 权威升级检查与 onboarding 契约

> 历史设计：生成子 Skill 的独立 wrapper lifecycle 已被 [2026-07-30 独立 Repo Harness 边界](2026-07-30-independent-repo-harness-boundary.md) 取代。升级权威源与安全重装规则继续有效。

Related issues: #8, #9

## 优化目标

1. wrapper 不得在最新版本未知时用当前版本自比较并误报 `up_to_date`。
2. wrapped Skill 必须明确说明如何安装、调用和完成目标所有的首次初始化。
3. 生成的子 Skill 必须明确不继承父级 hooks，并有单独接入和验收路径。
4. runtime 重装必须可实际执行，同时保留真实目录且要求显式归档许可。

## 实现方向

- `harness upgrade-check` 和 SessionStart hook 默认查询 `MetaInFLow/EvoZeus-CoEvolve` 的 GitHub latest release；失败时输出来源、时间和错误。
- manifest 增加 `onboarding`，覆盖 installation、invocation、initialization 和 generated child Skills。
- 初始化命令由目标 Skill 通过 bootstrap 参数提供，wrapper 只保存和校验，不包含 company 专用业务逻辑。
- `publish reinstall` 在任何写入前校验所有目标；真实目录移动到 EvoZeus archive 后再建立 symlink，绝不删除。
- legacy layout migration 同步生成 onboarding guide 并补全 manifest 契约。

## 验证计划

- 覆盖远端最新版本、远端不可用、layout migration 优先级和 SessionStart refresh。
- 覆盖缺失链接、错误链接、真实目录无许可零写入、许可后归档、不同内容归档及 canonical path 校验。
- 覆盖 initialization 字段配对、child hook 非继承契约、preflight 拒绝非法 manifest 和旧 layout 迁移补全。
- 在真实 v0.7 target 上仅运行 `upgrade-check`，确认无需 `--latest-version` 即可发现 GitHub latest release。

## 发布计划

作为新增写入行为和 onboarding contract 的 minor release 发布 `v0.9.0`。release notes 关联 #8、#9，并列出升级查询、安全重装、初始化和子 Skill hook 接入变化。
