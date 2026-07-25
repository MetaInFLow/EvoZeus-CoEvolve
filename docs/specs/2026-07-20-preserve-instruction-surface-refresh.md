# v0.10.1：保留 Skill instruction surface 的业务内容

## Related issue

- GitHub Issue #14：`P1: v0.10.0 migration refresh removes target H1 and records the wrong layout`

## 问题

v0.10.0 在刷新 `SKILL.md` 的 wrapper 状态区时，仅用下一个 `##` 作为结束边界。如果目标 Skill 在状态区后以 `#` 一级标题开始业务正文，该标题及其后的介绍文本会被一并替换。

同一流程还把迁移说明中的 layout transition 固定写成 `scattered-v1 -> consolidated-v2`，导致 consolidated-v2 harness 的纯版本刷新留下错误记录。

## 设计

1. 状态区的结束边界改为下一个同级或更高级 Markdown 标题。对于 `## EvoZeus-CoEvolve 状态检查`，边界是后续第一个 `#` 或 `##` 标题；支持最多 3 个前导空格的 ATX 标题，并跳过 YAML frontmatter 与 fenced code block 内的伪标题。
2. 替换时不对边界外文本执行 `strip`，并禁用文本读写的通用换行转换，保证目标 Skill 的 frontmatter、一级标题、业务正文、CRLF 和空白字节不被改写；缺少状态区时同时支持 LF/CRLF frontmatter 并在其后插入。
3. instruction surface 刷新显式接收 `from_layout`、`to_layout` 和 `layout_migration_required`。
4. 布局迁移使用 `Migration Note`；consolidated-v2 的纯版本升级使用 `Version Refresh Note`，并记录真实 layout transition。

## 验证

- 单元测试：状态区后紧跟一级标题时，一级标题起的全部后缀字节保持不变。
- 单元测试：YAML frontmatter、缩进 ATX H1、fenced code 内的 `#` 和 CRLF instruction surface 均保持正确边界。
- 单元测试：consolidated-v2 版本刷新记录 `consolidated-v2 -> consolidated-v2`，且使用 `Version Refresh Note`。
- 集成测试：真实 `upgrade-all` 对该 instruction-surface 形状完成版本刷新且保留业务块。
- 回归测试：完整测试套件与 Python 编译检查通过。

## Release plan

- 以 patch release `v0.10.1` 发布。
- release notes 关联并关闭 Issue #14。
- 修复发布前不继续处理三个已被 v0.10.0 迁移且仍有未提交改动的目标 Skill；恢复和重新升级必须使用可审计、可回滚的单独步骤。
