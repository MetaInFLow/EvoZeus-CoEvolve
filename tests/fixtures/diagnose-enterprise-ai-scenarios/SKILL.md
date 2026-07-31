---
name: diagnose-enterprise-ai-scenarios
description: 从简短的企业背景中诊断并排序可落地的 AI 应用场景。用于用户描述一家企业、业务流程、经营问题或部门，并询问“哪里适合用 AI”“应该先做哪个 AI 场景”或“如何验证 AI 机会”时。固定输出三个候选场景、一个首选场景和一个可量化验证动作；不生成完整咨询报告或大规模场景清单。
---

## EvoZeus-CoEvolve 状态检查

本段是 Skill 入口 preflight。Agent 选中本 Skill 后、进入业务主链路前执行；它基本绑定当前 Skill，但依赖 instruction compliance，不是 native Skill invocation hook。

`.evozeus-wrapper/wrapper.json` 分开记录 capability：`repo_maintenance_hook` 只在 canonical repository 作为活动项目时原生触发；`global_session_dispatcher` 在每个任务启动时聚合检查全部 wrapped Skills；本入口仍记录为 `prompt_runtime_check`。当前 Codex 没有 `SkillInvoke` 事件，不得把前两者描述成 per-Skill native invocation hook。

若当前只是 runtime-only install，缺少维护资产时不要把安装副本当作事实源，回 canonical repo 处理 wrapper harness 或 Skill release。

1. Skill release 状态
   - 当前记录版本：`v0.1.0`
   - 检查命令：`python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo MetaInFLow/diagnose-enterprise-ai-scenarios`
   - 如果 GitHub latest release 更新：先更新 canonical repo，并确认 runtime install 仍指向 canonical repo。
   - 如果本地版本领先 GitHub release：先完成 changelog、验证和 `vMAJOR.MINOR.PATCH` release，再把它当作稳定运行版本。
2. Wrapper harness 状态
   - 当前 wrapper 版本：`v0.14.0`
   - 事实源：`.evozeus-wrapper/wrapper.json`
   - 检查命令：在 EvoZeus-CoEvolve repo 运行 `python3 scripts/evozeus_wrapper.py harness upgrade-check --target <this-skill-repo> --json`
   - 如果 wrapper 落后且 `upgrade-check` 未发现冲突或不兼容：报告当前与最新版本；兼容的旧 wrapper 只作为维护提醒，不阻塞业务主链路。
   - 普通 Skill 调用不授权 Harness 升级或其他维护写入。只有用户明确请求 Harness 维护或升级后，才运行 `harness upgrade --dry-run` 生成方案；实际写入仍需单独确认。
3. Source contract 状态
   - 检查命令：`python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo MetaInFLow/diagnose-enterprise-ai-scenarios`
   - 如果 `~/.evozeus/.projects`、git origin 或 runtime install 不一致：先修复为同一个 canonical repo，再继续。
4. 调用身份头
   - 检查命令：`python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py identity --json`
   - 读取 `runtime_identity.display_line`，并将其原样放在本次 Skill invocation 第一条用户可见输出的第一行。
   - 身份头固定以 `🧙🏻‍♂️` 开始；禁止使用 HTML、自定义图片或 shortcode 替代。
   - 同一次 invocation 的后续 commentary 和 final 不重复；下一次 invocation 再展示一次。
5. EvoZeus Notice
   - 渲染入口：`python3 .evozeus-wrapper/scripts/evozeus_notice.py render --kind <kind> --state <state> --message <message> [--action <action>] [--json]`。
   - 配置事实源：`.evozeus-wrapper/policies/notice-policy.json`。普通业务进度不展示 EvoZeus Tag。
   - 用户纠错、不满意或复盘发现可复用机制缺陷时，先完成当前业务纠正，再运行 feedback audit，并通过 `--context` 传入一句脱敏、可复用、可行动的 Lesson 摘要；在同一响应末尾原样显示 `user_notice.display_text`。
   - Lesson Notice 的 Tag 为 `EvoZeus · Lesson`、状态为 `待记录`，只询问是否记录到 Skill Feedback Issue。
   - Lesson 记录、Skill 修复、Harness 维护、UAT 与正式发布分别使用配置中的独立 kind；任何 Notice 都不扩张写入授权。

解决顺序：Source contract 损坏、manifest 无效、迁移冲突或已确认不兼容时停止业务流程并说明原因；其他情况完成只读检查后直接进入主链路。

# 企业 AI 场景诊断

把简短的企业描述转化为紧凑、可决策、可验证的 AI 场景诊断。默认使用中文；用户明确使用其他语言时跟随用户语言。

## 输入

优先使用用户已经提供的信息，提取：

- 企业类型或行业；
- 核心客户和产品或服务；
- 关键业务流程；
- 当前痛点；
- 已有数据或知识；
- 受影响角色。

只有当缺失信息会实质改变首选场景时，才询问一个简短问题。其他情况直接推进，并明确标注假设。

除非用户要求外部证据，或诊断依赖某家真实企业的当前事实，否则不要主动开展网络调研。

## 工作流

### 1. 定义业务问题

用不超过五个环节概括业务链。识别高频、耗时、易错、知识密集或依赖重复判断的环节。

区分：

- **事实**：用户提供或已经核验的信息；
- **假设**：为继续诊断而采用的未验证条件；
- **证据缺口**：会影响判断可信度的缺失信息。

### 2. 生成三个场景

生成三个互不重复的候选场景。每个场景必须形成完整链条：

```text
业务环节 -> 当前摩擦 -> AI 动作 -> 所需输入 -> 可观察结果
```

使用“基于已审核案例生成售前方案初稿”“识别并分派客户需求”等具体动作名称。避免使用“AI 助手”“智能化转型”等泛化名称。

### 3. 评估价值与可行性

为每个场景分别打 1–5 分：

- **业务价值**
  - `1`：局部便利；
  - `3`：明显改善时间、质量或工作量；
  - `5`：直接影响收入、成本、重大风险或客户体验。
- **实施可行性**
  - `1`：关键数据不可用或流程不稳定；
  - `3`：部分数据可用，需要人工复核；
  - `5`：输入已具备、流程清晰、风险可控。

按以下规则确定优先级：

- `P0`：价值 >= 4 且可行性 >= 4；
- `P1`：价值 >= 4 或可行性 >= 4；
- `P2`：其他情况。

不要虚构 ROI 数字。无法确认的收益应写成待验证假设，并给出验证指标。

### 4. 选择一个首发场景

只推荐一个首发场景。优先选择能利用现有输入、保留人工复核，并以较小样本验证业务价值的场景。

给出一个最小验证动作，包含：

- 样本输入；
- 参与角色；
- 当前基线；
- 成功指标；
- 通过条件。

## 输出合同

使用以下结构：

```markdown
## 核心诊断
<用一段话说明主要业务瓶颈和 AI 机会。>

## 候选场景
| 排名 | 业务环节 | 场景 | 当前摩擦 | AI 动作 | 所需输入 | 价值 | 可行性 | 优先级 |
|---|---|---|---|---|---|---:|---:|---|
| 1 | ... | ... | ... | ... | ... | 1-5 | 1-5 | P0/P1/P2 |
| 2 | ... | ... | ... | ... | ... | 1-5 | 1-5 | P0/P1/P2 |
| 3 | ... | ... | ... | ... | ... | 1-5 | 1-5 | P0/P1/P2 |

## 建议先验证
- 场景：...
- 优先原因：...
- 样本：...
- 参与角色：...
- 当前基线：...
- 成功指标：...
- 通过条件：...

## 假设与证据缺口
- 假设：...
- 证据缺口：...
```

## 质量门禁

交付前确认：

- 三个场景均对应真实业务环节和痛点；
- 三个场景互不重复；
- 每个场景都写明所需输入；
- 价值和可行性评分符合统一标准；
- 首选场景包含可量化的通过条件；
- 事实、假设和证据缺口清晰分开；
- 输出保持紧凑，不扩写为完整转型路线图。

## 示例调用

```text
一家提供企业软件定制服务的公司，销售线索来自多个群聊，售前方案主要依靠个人经验，历史案例分散。请诊断三个适合优先验证的 AI 场景。
```

## 自进化方法

本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环。后续任何行为改动都必须先留下可追踪证据，再进入实现。

源头发现顺序：

1. 先读取本 repo 的 `.evozeus-wrapper/wrapper.json`，以 `canonical_repo` 作为目标 repo。
2. 再检查 `~/.evozeus/.projects/MetaInFLow/diagnose-enterprise-ai-scenarios` 是否存在并指向 canonical repo。
3. 验证 canonical repo 的 git origin / GitHub repo 可访问。
4. 再检查 runtime install：`~/.codex/skills/<skill-name>`、`~/.agents/skills/<skill-name>`；它们只能是指向 canonical repo 的安装指针。
5. 只有 wrapper manifest 和 project pointer 都无法确认时，才进入 GitHub user/org/public search。

进化流程：

1. 使用中出现不满意、纠错、漏检或可复用机制缺陷时，先完成当前业务纠正，再运行 feedback audit，并通过 `--context` 提供一句脱敏 Lesson 摘要；原样显示返回的 `user_notice.display_text`，询问是否只记录到 Skill Feedback Issue。只有明确确认后才创建 Issue，修复继续要求单独授权。
2. 每次运行本 Skill 前，先执行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo MetaInFLow/diagnose-enterprise-ai-scenarios`，确认 wrapper source contract 成立。
3. 再执行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo MetaInFLow/diagnose-enterprise-ai-scenarios`，确认 GitHub latest release 没有新版本。
4. 开始修改前，在 `.evozeus-wrapper/docs/designs/` 新建设计文档，明确 Related issue、优化目标、实现计划、验证计划和 release plan。
5. PR 必须同步更新 `SKILL.md` 与 `.evozeus-wrapper/CHANGELOG.md`，并通过 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure` 和 PR 检查。
6. 合并后用 `vMAJOR.MINOR.PATCH` release tag 和 release notes 固化本次进化，保留可回滚记录。

边界：不要把 raw private session、客户资料、secret、未脱敏商业上下文写入公开 Issue、docs 或 release notes；`~/.evozeus/.projects/MetaInFLow/diagnose-enterprise-ai-scenarios/` 应指向 canonical repo，runtime-only install 只能是指针，不能作为 copied install 或第二事实源直接修改。

Target repo: `MetaInFLow/diagnose-enterprise-ai-scenarios`
Visibility: `public`
Current Skill version: `v0.1.0`
Wrapper harness version: `v0.14.0`

## EvoZeus-CoEvolve

本区由 EvoZeus-CoEvolve 追加，用来说明本 Skill 的 wrapper harness 路由、版本记录和迁移规则。它不覆盖原 Skill 的业务规则；涉及业务行为变化时，仍必须走 Issue、design doc、PR、CHANGELOG 和 release。

调用 wrapper 的场景：

1. 本 Skillware Repo 需要 attach/adopt/repair Harness，或确认 canonical source。
2. `.evozeus-wrapper/wrapper.json` 中的 wrapper harness version 落后于 EvoZeus-CoEvolve 最新版本。
3. `~/.evozeus/.projects/MetaInFLow/diagnose-enterprise-ai-scenarios`、`.codex` 或 `.agents` runtime install 疑似不是同一个 source of truth。
4. 使用反馈先进入当前 invocation 的本地待确认状态；用户明确确认后才提交 Skill Feedback Issue；另获修复授权后才能进入 design doc、PR、CHANGELOG、release 的自进化闭环。
5. 目标 GitHub repo、release tag、GitHub Pages 或 preflight check 需要创建、诊断或修复。

路由规则：

- 目标 Skill 行为问题：先捕获为本地待确认信号；用户确认提交后才创建 Skill Feedback Issue，修复和 PR 继续要求单独授权。
- 源头/安装问题：先运行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo MetaInFLow/diagnose-enterprise-ai-scenarios`。
- 结构问题：运行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure`。
- Skill release 问题：运行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo MetaInFLow/diagnose-enterprise-ai-scenarios`。
- wrapper harness 升级：回到 EvoZeus-CoEvolve repo，运行 `python3 scripts/evozeus_wrapper.py harness upgrade-check --target <this-skill-repo> --json`，再用检查结果中的最新版本运行 `harness upgrade --dry-run` 生成迁移方案。

Append-only 迁移规则：

- wrapper 升级必须保留 frontmatter 后的状态检查；其他 `SKILL.md` wrapper 内容只能追加本区缺失内容或 migration note，不要重写原 Skill 业务段落。
- 如果本区已存在，升级时追加 migration note，不改写旧文本。
- 每次 wrapper 升级必须记录 from/to wrapper version、planned files、验证命令、回滚方案和是否需要人工 merge review。
- wrapper version 事实源是 `.evozeus-wrapper/wrapper.json` 的 `wrapper_version`；Skill release 仍以 GitHub release / `.evozeus-wrapper/CHANGELOG.md` 为准。

Wrapper harness version: `v0.14.0`
Wrapper manifest: `.evozeus-wrapper/wrapper.json`
Feedback audit policy: `.evozeus-wrapper/policies/feedback-policy.json`
Feedback audit rule: `.evozeus-wrapper/policies/audit-rule.md`
Notice policy: `.evozeus-wrapper/policies/notice-policy.json`
Notice CLI: `.evozeus-wrapper/scripts/evozeus_notice.py`
Wrapper migration log: `.evozeus-wrapper/docs/migrations/`

Runtime integration modes:

- `repo_maintenance_hook`：project-local `SessionStart` hook，仅覆盖 canonical repository 维护。
- `global_session_dispatcher`：user-level `SessionStart` 聚合检查全部 wrapped Skills，不是 per-Skill invocation hook。
- `bootstrap_skill`：Plugin lifecycle 可以稳定加载控制 Skill，但当前没有 `SkillInvoke` 事件。
- `prompt_runtime_check`：Skill 入口 preflight，基本绑定被选中的 Skill，但依赖 prompt compliance。
- `manual_only`：只能手动运行 wrapper 命令。
