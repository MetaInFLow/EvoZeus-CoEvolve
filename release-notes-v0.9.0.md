# EvoZeus-CoEvolve v0.9.0

## 概要

本版本修复 wrapper harness 的版本自比较问题，并补齐 wrapped Skill 的安全重装、调用、初始化和子 Skill hook 接入契约。

关联 Issues：[#8](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/8)、[#9](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/9)。

## 主要变化

- `harness upgrade-check` 在未传 `--latest-version` 时查询 GitHub latest release；查询失败返回 `latest_unknown`、来源、检查时间和错误，不再用当前版本冒充最新版本。
- Codex `SessionStart` hook 在安装后仍会刷新 GitHub latest release；advisory 模式在查询失败时警告，strict 模式阻断。
- `publish reinstall` 支持实际写入：创建缺失 symlink、修正错误 symlink，并在所有目标通过预校验后才开始写入。
- 真实目录只有在显式 `--approve-archive` 后才会移动到 `~/.evozeus/archives/runtime-installs/`，不会删除原内容。
- `.evozeus-wrapper/wrapper.json` 增加 `onboarding`，记录 canonical symlink 安装、目标 Skill 调用、目标所有的初始化，以及生成子 Skill 的独立 wrapper 生命周期。
- 子 Skill 明确 `hooks_inherited: false`，必须单独接入、通过 `/hooks` 信任审核、运行 structure preflight，并通过 consumer-project smoke test。
- legacy layout migration 会生成 `.evozeus-wrapper/docs/onboarding.md` 并补全迁移后 manifest。

## 使用

先预览 runtime 重装：

```bash
python3 scripts/evozeus_wrapper.py publish reinstall \
  --skill-name my-skill \
  --canonical-path /absolute/path/to/my-skill \
  --target codex \
  --dry-run \
  --json
```

无真实目录冲突时，移除 `--dry-run` 执行。需要保留并替换真实目录时，审核计划后增加 `--approve-archive`。

检查 wrapper harness 时不再传入当前版本：

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-check \
  --target /absolute/path/to/target \
  --json
```

## 验证

- `python3 -m pytest -q`：73 passed。
- wrapper 脚本与 SessionStart hook 通过 Python compilation。
- target JSON/YAML templates 解析通过。
- 真实 v0.7 target 在不传 `--latest-version` 时成功发现 GitHub latest release，并正确优先建议 layout migration。

## 回滚

runtime 真实目录的归档位置会写入 JSON report；需要回滚安装时，先移除新 symlink，再把对应 archive 移回原路径。wrapper 本身可 checkout 或重新安装 `v0.8.0`。
