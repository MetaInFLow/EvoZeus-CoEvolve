# EvoZeus-CoEvolve v0.9.1

## 概要

本版本修复 v0.9.0 layout migration 的残缺成功状态，并让 GitHub Pages 从必需 workflow 变为显式 opt-in 的可选部署能力。

关联 Issues：[#10](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/10)、[#11](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/11)。

## Migration 修复

- 在写入前解析并校验 `.codex/hooks.json`；非法 JSON 或不兼容结构会阻断迁移。
- 保留 target 的其他 hooks，创建或刷新唯一的 EvoZeus-CoEvolve `SessionStart` registration。
- 同步刷新 hook adapter、status prelude、无自比较参数的升级命令，以及 manifest integration/hook facts。
- 在 instruction surface 追加 migration note，同时保持 target 业务段不变。
- 修正 dashboard contact link，并补全 onboarding 与 dashboard manifest contract。
- 删除 legacy manifest 后自动运行 structure post-validation；验证失败不会返回成功报告。
- 已经迁到 layout v2 的残缺 v0.9.0 target 可直接刷新到 v0.9.1，并生成版本专属 migration record。

## Pages 修复

- push 和 workflow dispatch 始终运行 maintainer validation。
- Pages deployment 仅在 repository variable `EVOZEUS_PAGES_ENABLED=true` 时运行。
- 不支持 private Pages 的 repo 默认使用 repository-only mode，不再因 `actions/configure-pages` 永久失败。
- workflow dispatch 会明确报告 Pages enabled 或 repository-only 状态。

启用已确认可用的 Pages：

```bash
gh api --method POST repos/OWNER/REPO/pages -f build_type=workflow
gh variable set EVOZEUS_PAGES_ENABLED --body true --repo OWNER/REPO
```

## 验证

- `python3 -m pytest -q`：78 passed。
- wrapper scripts 和 SessionStart hook 通过 Python compilation。
- target JSON/YAML templates 解析通过。
- 完整 v0.6 legacy target 通过 migration、structure、maintainer、runtime 和 hook smoke test；业务段保持不变。
- 真实 v0.7 target 的临时副本通过 CLI migration 和迁移后 maintainer validation，原 repo 未修改。

## 回滚

layout migration 必须在 clean worktree 中单独提交；如果 post-validation 失败或需要回退，revert 该 migration commit。Pages 可通过删除 `EVOZEUS_PAGES_ENABLED` repository variable 立即停用，repo validation 不受影响。
