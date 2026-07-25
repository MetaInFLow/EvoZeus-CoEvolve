# EvoZeus-CoEvolve 目标仓库归拢布局与升级迁移

日期：2026-07-18

状态：canonical。本文取代 `2026-07-07-evoinfra-dir-split.md` 的目标布局；旧文档只保留为历史决策记录。

来源：基于现有 v0.7 target harness 的真实输出结构，以及用户对“wrapper 成品物散落在原 repo”的反馈整理。

## 问题

v0.7 会在目标 repo 根部增加 `CHANGELOG.md`、`WRAPPER.md`、`docs/`、`scripts/`、`.evozeus_evoinfra/`、`.codex/` 和 `.github/`。这些文件虽然各自有用途，但会混入用户原有业务结构，难以识别 owner，也不利于整体升级和卸载。

## Layout v2

Wrapper 自有产物统一进入 `.evozeus-wrapper/`：

```text
.evozeus-wrapper/
├── wrapper.json
├── CHANGELOG.md
├── WRAPPER.md
├── policies/
├── hooks/
├── scripts/
└── docs/
    ├── index.md
    ├── design-doc-template.md
    ├── designs/
    └── migrations/
```

目标 repo 外部只保留宿主必须从固定位置发现的薄接点：

- `SKILL.md`、`AGENTS.md` 或被选中的 instruction surface：保留精简的 wrapper 状态与自进化说明。
- `.codex/hooks.json`：Codex project-local hook 注册入口，实际 adapter 位于 `.evozeus-wrapper/hooks/`。
- `.github/ISSUE_TEMPLATE/`、`.github/pull_request_template.md`、`.github/workflows/`：GitHub 固定发现位置。

GitHub Pages 由 workflow 从 `.evozeus-wrapper/docs/` 构建，不再依赖根 `docs/` legacy Pages source。

## 升级不是兼容

`.evozeus_evoinfra/` 与更早的 `.evozeus/wrapper.json` 只用于识别待迁移状态。目标 preflight 发现旧 manifest 后必须失败并要求升级，不能把旧目录当作长期 fallback。

迁移命令：

```bash
python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/target \
  --latest-version v0.8.0 \
  --dry-run \
  --json

python3 scripts/evozeus_wrapper.py harness migrate-layout \
  --target /absolute/path/to/target \
  --latest-version v0.8.0 \
  --json
```

## 迁移协议

1. Detect：识别旧 manifest 与散落文件，状态标记为 `migration_required`。
2. Plan：输出每个 source/destination、保留的宿主接点、冲突和回滚方式，不写文件。
3. Approve：用户审核 dry-run；任何 destination 内容冲突都会阻止执行。
4. Apply：移动 wrapper 文件，重写说明面、hook、workflow 和 policy 引用。
5. Record：写入 `layout_version=2` manifest 和 `.evozeus-wrapper/docs/migrations/` 记录。
6. Cleanup：只删除已经变空的旧 wrapper 目录，不删除含未知用户文件的目录。
7. Verify：运行 structure、runtime、doctor 与测试；通过后把迁移作为独立 Git commit 提交，回滚方式是回退该 commit。

## 主要路径映射

| v1 | v2 |
| --- | --- |
| `CHANGELOG.md` | `.evozeus-wrapper/CHANGELOG.md` |
| `WRAPPER.md` | `.evozeus-wrapper/WRAPPER.md` |
| `docs/designs/` | `.evozeus-wrapper/docs/designs/` |
| `docs/wrapper-migrations/` | `.evozeus-wrapper/docs/migrations/` |
| `scripts/evozeus_wrapper_preflight.py` | `.evozeus-wrapper/scripts/evozeus_wrapper_preflight.py` |
| `.evozeus_evoinfra/wrapper.json` | `.evozeus-wrapper/wrapper.json` |
| `.evozeus_evoinfra/feedback-policy.json` | `.evozeus-wrapper/policies/feedback-policy.json` |
| `.evozeus_evoinfra/audit-rule.md` | `.evozeus-wrapper/policies/audit-rule.md` |
| `.codex/hooks/evozeus_wrapper_start_check.py` | `.evozeus-wrapper/hooks/evozeus_wrapper_start_check.py` |

## 验收标准

- 新 bootstrap 不在根部创建 wrapper 自有 `CHANGELOG.md`、`WRAPPER.md`、`docs/` 或 `scripts/`。
- 旧布局不能通过 target preflight。
- dry-run 能完整列出迁移，不产生写入。
- 同名不同内容的 destination 会让 apply 零写入失败。
- apply 后旧 manifest 消失，layout v2 manifest 成为唯一事实源。
- `.github/` 与 `.codex/hooks.json` 继续可被宿主发现，实际 wrapper 内容归入 `.evozeus-wrapper/`。
