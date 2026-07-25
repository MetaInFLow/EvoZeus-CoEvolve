# EvoZeus-CoEvolve v0.10.1

本 patch release 修复 [Issue #14](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/14) 中的 P1 instruction-surface 迁移问题。

## 修复内容

- 刷新 `## EvoZeus-CoEvolve 状态检查` 时，以下一个同级或更高级 Markdown 标题作为边界，跳过 YAML frontmatter 和 fenced code 内的伪标题，并支持缩进 ATX 标题。缺少状态区时会在 LF/CRLF frontmatter 后插入；目标 Skill 元数据、一级标题、介绍文本、业务段落、CRLF 和原始空白不再被删除或改写。
- 旧命令参数和 wrapper version 的兼容刷新仅作用于 wrapper 自有 Markdown 段落，不再全局改写目标业务正文。
- 布局迁移说明记录真实的 `from_layout -> to_layout`。
- consolidated-v2 的纯版本升级使用 `Version Refresh Note`，并记录 `consolidated-v2 -> consolidated-v2`，不再误报为 `scattered-v1 -> consolidated-v2`。

## 升级

先查看全部 registered harnesses 的升级计划：

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.10.1 --dry-run --json
```

确认目标仓库均为 clean Git worktree 后再执行：

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.10.1 --approve --json
```

已被 v0.10.0 误删业务内容且仍有未提交迁移改动的仓库，不应直接覆盖或提交；先从 Git 或事务备份恢复被删除内容，再运行 v0.10.1 升级。

## 验证

- `python3 -m pytest -q`：123 passed
- Python 编译检查通过
