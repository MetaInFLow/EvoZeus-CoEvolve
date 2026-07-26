# AGENTS.md

## 项目约定

- 项目产出默认用中文，关键专有名词和专业名词可以用英文。
- 本 repo 面向 public-ready harness，不保存 raw private session、客户资料、商业资料、secret 或未脱敏 evidence。
- 修改必须保持小步、可解释、可回归；不要为了“通用”提前写框架。

## Repo 职责

EvoZeus-CoEvolve 是 EvoZeus 体系中的 Collaborative Evolution 论文系统与公开 artifact 入口。当前 repo 直接实现 attachment/lifecycle substrate，并通过固定版本 manifest 连接其他公开 EvoZeus 组件。它负责把一个本地 Skill 文件夹转换为可反馈、可审查、可发布、可恢复的协同演进单元：

- 为目标 Skill repo 生成 GitHub Pages dashboard。
- 让用户明确选择 `public` 或 `private`。
- 创建 harness 前必须检查目标 GitHub repo 是否已经存在。
- 在 `~/.evozeus/.projects/OWNER/REPO/` 保留 repo 化前的本地 Skill 项目入口。
- 在目标 `SKILL.md` 中补充自进化方法说明。
- 注入 `CHANGELOG.md`。
- 注入 Skill 反馈 Issue template。
- 注入 Skill 更新 design doc template。
- 注入 GitHub 上传前 preflight 检查。
- 新建目标 repo 初始化后创建 `v0.1.0` release；已有 repo 必须保留 GitHub latest release 或 owner 确认的 `CHANGELOG.md` 版本，并要求运行前检查 GitHub latest release。

用户入口由 EvoZeus 提供。本 repo 不负责改写目标 Skill 的业务内容；除演进状态和方法说明外，不主动改变原 Skill 的业务规则。它也不保存 raw private session、客户资料、商业资料、secret 或未脱敏 evidence。

论文标题固定为 `EvoZeus-CoEvolve: An Add-On Harness for Collaborative Evolution of Existing Skillware`。任何公开文档必须区分 Implemented、Partial 和 Planned，不能把跨用户聚合、frontier code/research adapters、candidate generation 或效果实验写成现有能力。

论文作者元数据固定为两位作者，顺序与 Skillware 论文一致：Haodi Fan（`anthonyfan@metainflow.cn`）、Zucong Lan（`neillan@metainflow.cn`），两位作者 affiliation 均为 MetaInFlow。任何 title page、manifest、CITATION、README、release 或 arXiv source bundle 都不得省略任一作者或邮箱，也不得简化为 single-author / sole-author 记录。

## 编辑原则

1. 先明确目标 Skill 文件夹、GitHub repo 名称和 visibility。
2. visibility 没有明确给出时必须问用户，不要默认 public 或 private。
3. 创建 harness 前必须检查 GitHub repo 是否已存在；已存在就停止，不要重复创建。
4. 对目标 Skill 文件夹做增量注入，不覆盖用户已有文件，除非用户明确要求。
5. 检查逻辑放在 `scripts/evozeus_wrapper_preflight.py`，模板放在 `templates/target/`。
6. 不要把 attachment layer 做成复杂 runtime；当前 repo 的直接实现保持在驾驶舱文件、release/version 检查、迁移恢复和上传前检查。
7. 如果涉及 Session Signal 方法或 official factor tools，把内容路由到 `EvoZeus-session-signal-skill`。

## 验证标准

- 文档变更至少通过人工阅读检查：边界清楚、无内部废话、无 private 数据。
- 模板变更必须能支持最小闭环：feedback Issue -> design doc -> PR -> CHANGELOG -> release。
- `scripts/evozeus_wrapper_preflight.py` 变更后必须用一个临时 target folder 跑通 doctor / structure / issue / pr / release 检查。
- bootstrap 变更必须验证 `~/.evozeus/.projects/OWNER/REPO/SKILL.md` 会保留原始本地 Skill，且根目录 `SKILL.md` 会出现自进化方法段。
- release tag 必须使用 `vMAJOR.MINOR.PATCH`；只有新建目标 repo 的初始 wrapped release 固定为 `v0.1.0`，已有 repo 不得被重置到 `v0.1.0`。
