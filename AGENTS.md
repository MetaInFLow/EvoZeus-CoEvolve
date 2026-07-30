# AGENTS.md

## 项目约定

- 项目产出默认用中文，关键专有名词和专业名词可以用英文。
- 本 repo 面向 public-ready harness，不保存 raw private session、客户资料、商业资料、secret 或未脱敏 evidence。
- 修改必须保持小步、可解释、可回归；不要为了“通用”提前写框架。

## Repo 职责

EvoZeus-CoEvolve 是 EvoZeus 体系中的独立进化扩展、Evolution Harness SDK 与公开论文 artifact。它面向已经具备独立 GitHub Repo、Owner、Issue、PR、UAT 和 Release 边界的 Skillware Repo，为该 Repo 增加可反馈、可审查、可发布、可恢复的协同演进机制：

- 为目标 Skill repo 生成 GitHub Pages dashboard。
- 让用户明确选择 `public` 或 `private`。
- 创建 Harness 前必须确认目标路径等于 Git Repo 根目录，并检查目标 GitHub Repo 已存在。
- 在 `~/.evozeus/.projects/OWNER/REPO/` 保存指向独立 canonical Repo 根目录的指针。
- 在目标 `SKILL.md` 中补充自进化方法说明。
- 注入 `CHANGELOG.md`。
- 注入 Skill 反馈 Issue template。
- 注入 Skill 更新 design doc template。
- 注入 GitHub 上传前 preflight 检查。
- 保留目标 Repo 的 GitHub latest release 或 Owner 确认的 `CHANGELOG.md` 版本，并要求运行前检查 GitHub latest release。

用户入口由 EvoZeus 提供。本 repo 不负责改写目标 Skill 的业务内容；除演进状态和方法说明外，不主动改变原 Skill 的业务规则。它也不保存 raw private session、客户资料、商业资料、secret 或未脱敏 evidence。

论文标题固定为 `EvoZeus-CoEvolve: An Add-On Harness for Collaborative Evolution of Existing Skillware`。任何公开文档必须区分 Implemented、Partial 和 Planned，不能把跨用户聚合、frontier code/research adapters、candidate generation 或效果实验写成现有能力。

论文作者元数据固定为两位作者，顺序与 Skillware 论文一致：Haodi Fan（`anthonyfan@metainflow.cn`）、Zucong Lan（`neillan@metainflow.cn`），两位作者 affiliation 均为 MetaInFlow。任何 title page、manifest、CITATION、README、release 或 arXiv source bundle 都不得省略任一作者或邮箱，也不得简化为 single-author / sole-author 记录。

## 编辑原则

1. 先明确目标独立 Repo 根目录、GitHub Repo 名称和 visibility。
2. visibility 没有明确给出时必须问用户，不要默认 public 或 private。
3. 创建 Harness 前必须检查 GitHub Repo 已存在、origin 匹配，且执行者拥有 `ADMIN` 权限。
4. 目标不在 Git Repo 内、目标位于 Repo 子目录、或 Repo 内已有嵌套 Harness 时必须停止。
5. 对目标 Repo 根目录做增量注入，不覆盖用户已有文件，除非用户明确要求。
6. 检查逻辑放在 `scripts/evozeus_wrapper_preflight.py`，模板放在 `templates/target/`。
7. 不要把 attachment layer 做成复杂 runtime；当前 repo 的直接实现保持在驾驶舱文件、release/version 检查、迁移恢复和上传前检查。
8. Runtime 与 Session Signal 已内嵌 EvoZeus 主 Repo；相关改动路由到 `MetaInFLow/EvoZeus`。

## 验证标准

- 文档变更至少通过人工阅读检查：边界清楚、无内部废话、无 private 数据。
- 模板变更必须能支持最小闭环：feedback Issue -> design doc -> PR -> CHANGELOG -> release。
- `scripts/evozeus_wrapper_preflight.py` 变更后必须用一个临时 target folder 跑通 doctor / structure / issue / pr / release 检查。
- attach/bootstrap 变更必须验证普通文件夹被拒绝、Repo 子目录归一到 Repo 根目录、`~/.evozeus/.projects/OWNER/REPO` 指向根目录，且根说明面出现自进化方法段。
- Harness 写入、升级与上传必须验证 GitHub `viewerPermission=ADMIN`；只读诊断和升级计划不要求管理员权限。
- release tag 必须使用 `vMAJOR.MINOR.PATCH`；接入 Harness 不得重置目标 Repo 的已有版本。
- 版本化 Release Notes 固定存放在 `docs/releases/vMAJOR.MINOR.PATCH.md`；tag 工作流是路径解析的执行事实源，仓库根目录不得新增 `release-notes-v*.md`。
