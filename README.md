# EvoZeus CoEvolve

**为独立 Skillware Repo 增加可审查、可验证、可回滚的进化闭环。**

[![Release](https://img.shields.io/github/v/release/MetaInFLow/EvoZeus-CoEvolve)](https://github.com/MetaInFLow/EvoZeus-CoEvolve/releases)
[![CI](https://github.com/MetaInFLow/EvoZeus-CoEvolve/actions/workflows/ci.yml/badge.svg)](https://github.com/MetaInFLow/EvoZeus-CoEvolve/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

EvoZeus CoEvolve 是 [EvoZeus](https://github.com/MetaInFLow/EvoZeus) 的可选进化扩展与 Evolution Harness SDK。它把真实使用中的 Lesson 转换为有证据、有责任人、有验证结果的 Repo 变更：

```text
Lesson → Feedback Issue → Design → PR → UAT → Release → Rollback Point
```

普通用户通过 EvoZeus 使用这项能力。维护者在需要诊断、接入、升级或开发 Harness 时直接进入本 Repo。

## 核心边界

Evolution Harness 的治理单位是独立 Git Repo：Issue、PR、Owner、UAT、Release 和回滚都发生在这个边界。

- 一个独立 Git Repo 最多拥有一个活动 Harness。
- Harness 固定在 Repo 根目录的 `.evozeus-wrapper/`。
- Skill、package、pack、app、example 和其他子目录继承所在 Repo 的根 Harness。
- 普通文件夹不能接入 Harness。
- 传入 Repo 子目录时，工具统一定位到 Repo 根目录。
- 发现嵌套 Harness 时，诊断和维护操作立即停止。
- 只读诊断和升级计划对普通用户开放。
- Harness 写入、升级和上传要求目标 GitHub Repo 的 `ADMIN` 权限。

需要让某个内部模块独立进化时，先把它建立为有明确 Owner 和发布边界的独立 Repo，再接入 CoEvolve。

完整决策见 [独立 Repo Harness 边界](docs/specs/2026-07-30-independent-repo-harness-boundary.md)。

## 适用场景

适合：

- 一个 Skillware Repo 已经有人使用，需要持续吸收真实反馈。
- 团队需要区分开发、唯一 UAT 候选与正式 Release。
- Skill 行为变化需要 Issue、设计、验证和回滚证据。
- 多个用户贡献 Lesson，但发布权仍由 Repo Owner 管理。

无需使用：

- 只想临时修改一个本地提示词。
- 目标只是 monorepo 内部 package 或 Skill 目录。
- 没有独立版本、Owner、Issue 或 Release 边界。
- 仅需要 EvoZeus 的会话复盘与本地判断能力。

## 用户体验

在 EvoZeus 中可以直接表达目标：

```text
检查这个 Skillware Repo 是否已经接入进化机制
```

```text
这里捕捉到一条 Lesson，先给我看准备记录的内容
```

```text
检查所有我有管理权限的 Harness，给出升级计划
```

EvoZeus 负责选择入口和解释结果；CoEvolve 负责 Repo 级治理与执行合同。任何外部写入都要经过明确授权。

## 用户可见事件词典

EvoZeus 只在关键生命周期状态真实变化时显示一行短标记。事件语义以 EvoZeus 主仓库的 [canonical user-visible event contract](https://github.com/MetaInFLow/EvoZeus/blob/main/docs/reference/user-visible-events.md) 为准；下表帮助 CoEvolve 使用者理解完整产品合同。

发出组件分为三层：`Core` 是 EvoZeus Plugin、产品 CLI 与渠道事务；`CoEvolve` 负责独立 Skillware Repo 的进化流程；`Harness` 是目标 Repo 内的身份检查与只读 Notice renderer。

| 事件 | 完整示例 | 业务含义 | 触发条件 | 发出组件 | 禁止误用边界 |
| --- | --- | --- | --- | --- | --- |
| 启动 | `🧙 EvoZeus · 已启动｜复盘这次 Agent 执行` | EvoZeus 已接管本次显式任务。 | 用户明确调用 EvoZeus。 | Core | 隐式辅助、普通问答和后台检查不得宣称已启动。 |
| 受管运行 | `👁️ EvoZeus · 受管运行｜企业场景地图 Skill · UAT` | 当前业务任务由已纳入治理的 Skillware 承载。 | Repo、Harness/Plugin 身份与渠道均已核验。 | Core + Harness | 未核验 Repo、版本或渠道时不得显示。 |
| Lesson 候选 | `🧙 EvoZeus · 捕捉到一条 Lesson｜证据不足时不能直接报完成。要记录下来吗？` | 发现了一条可能复用的改进，并请求记录授权。 | 当前业务结果已完成，Lesson 已脱敏且可行动。 | Core；Harness 可渲染目标 Repo 的本地 Lesson Notice | 标记不得抢在业务结果前；候选状态不得表示已持久化。 |
| Lesson 已记录 | `📝 EvoZeus · Lesson 已记录｜Feedback Issue #12` | 经用户确认的 Lesson 已成功写入指定载体。 | 本地记录或 Feedback Issue 创建成功。 | Core / CoEvolve | 写入失败、仅生成草稿或仍在等待授权时不得显示。 |
| 等待确认 | `🔐 EvoZeus · 等待确认｜创建 Feedback Issue` | 下一步会产生本地写入或外部影响，当前正在等用户授权。 | 写入、Issue、维护、发布等具体动作需要新的批准。 | Core / CoEvolve | 普通进度、只读诊断或模糊的未来计划不得使用。 |
| 版本状态 | `🧭 EvoZeus · 版本状态｜Stable v0.4.1 → UAT v0.4.2` | 展示对齐、切换或升级前的当前版本与目标版本。 | 渠道事务开始前已解析出两端版本。 | Core | 该标记只说明状态，不得当作切换成功证明。 |
| 发现更新 | `🧭 EvoZeus · 发现更新｜Stable v0.4.1 → v0.4.2` | 当前订阅渠道发现了可验证的新候选。 | 自动检查确认远端版本高于当前版本。 | Core | 无版本变化时保持安静；跨渠道候选不得伪装成当前渠道更新。 |
| 自动更新中 | `🛠️ EvoZeus · 自动更新中｜正在对齐Plugin、Runtime、Session Signal与CoEvolve` | 产品级更新事务已经开始。 | 下载、校验与组件对齐事务真实启动。 | Core | 仅完成检查或生成计划时不得显示。 |
| 自动更新完成 | `✅ EvoZeus · 自动更新完成｜Stable v0.4.2 · 新会话加载Plugin` | 新产品已通过验证并完成渠道切换。 | 组件完整性、Doctor 与切换门禁全部通过。 | Core | 只下载文件、只更新单组件或尚需恢复时不得报完成。 |
| 自动更新失败 | `🛡️ EvoZeus · 自动更新失败｜继续使用Stable v0.4.1 · 清单校验失败` | 更新事务失败，上一验证版本仍可用。 | 下载、校验、Plugin 对齐或切换失败，回退已确认。 | Core | 上一版本未保全时不得声称“继续使用”；失败不得同时标成完成。 |
| 进化执行 | `🛠️ EvoZeus · 进化中｜MetaInFLow/example-skill · 修复验收门禁` | 已批准的 Skillware 改动正在实施。 | 用户已单独授权修改目标 Repo。 | CoEvolve | Lesson 记录授权、Issue 创建授权和修改授权不得互相替代。 |
| UAT 就绪 | `🧪 EvoZeus · UAT 就绪｜MetaInFLow/example-skill · abc1234` | 唯一 UAT 候选已更新并可验收。 | 目标 Commit 通过门禁且 UAT 指针已真实更新。 | CoEvolve | 计划、未通过测试的 Commit 或 Stable Release 不得标成 UAT 就绪。 |
| 正式发布 | `🚀 EvoZeus · 已发布｜MetaInFLow/example-skill · v1.2.0` | 已验证的候选已成为真实 Release。 | Release 与对应不可变版本实际存在。 | Core / CoEvolve | tag 草稿、发布计划或仅通过 UAT 时不得显示。 |
| 回滚 | `↩️ EvoZeus · 已回滚｜Stable v0.4.1` | 系统已恢复到上一份可用版本。 | 恢复动作完成且目标版本通过 Doctor。 | Core；Skillware 回滚由 CoEvolve 编排 | 只选择回滚目标或尚未验证恢复结果时不得显示。 |
| 暂停 | `🛡️ EvoZeus · 暂停｜缺少可脱敏的验证证据` | 安全、隐私、权限或证据条件阻止继续执行。 | 继续动作会越过明确边界。 | Core / CoEvolve | 普通警告、低优先级建议或可继续的兼容漂移不得升级为暂停。 |
| 验证完成 | `✅ EvoZeus · 已验证｜Plugin、Runtime 与渠道一致` | 声明完成所需的检查已取得可复核证据。 | 相关验收门禁全部通过。 | Core / CoEvolve | 计划运行、部分通过或仅人工推测不得标成已验证。 |

显示规则：每次真实状态变化最多显示一条；普通业务分析和普通工具调用不打标；Lesson 提示位于业务结果之后；计划、UAT、Stable 与完成状态必须严格区分；内部 JSON、signal id、私有路径、客户资料和 raw session 不进入用户文案。

### Harness 本地 Notice renderer（已实现）

目标 Repo 中的 `.evozeus-wrapper/scripts/evozeus_notice.py` 按 `.evozeus-wrapper/policies/notice-policy.json` 渲染下列本地事件，输出始终声明 `writes=false`。该 renderer 只支持下列 kind/state，不生成上表全部 `Core-only` 事件，也不替代 Core 的产品级状态判断。

| 本地 kind / state | renderer 首行 | 使用边界 |
| --- | --- | --- |
| `skill` / `active` | 🧙🏻‍♂️ <code>EvoZeus · 受管 Skill</code>；policy label 为“运行中”，首行隐藏该 label | 每次受管 Skill invocation 的身份行，只在身份与渠道可核验后出现。 |
| `lesson` / `pending` | 💡 <code>EvoZeus · Lesson</code> 待记录 | 业务纠正完成后询问是否记录，不产生写入。 |
| `lesson` / `recorded` | 📝 <code>EvoZeus · Lesson</code> 已记录 | 仅在记录动作成功后出现。 |
| `evolution` / `authorized` | 🛠️ <code>EvoZeus · Evolution</code> 已授权 | 仅表示修改授权已取得。 |
| `evolution` / `running` | 🛠️ <code>EvoZeus · Evolution</code> 进行中 | 仅在获批变更真实执行时出现。 |
| `evolution` / `verified` | 🛠️ <code>EvoZeus · Evolution</code> 已验证 | 仅表示该次进化门禁通过，不代替 Core 的产品级“已验证”。 |
| `maintenance` / `pending` | 🔧 <code>EvoZeus · Maintenance</code> 待授权 | Harness 维护写入仍在等待单独批准。 |
| `maintenance` / `running` | 🔧 <code>EvoZeus · Maintenance</code> 进行中 | Harness 维护动作已经开始。 |
| `maintenance` / `completed` | 🔧 <code>EvoZeus · Maintenance</code> 已完成 | 维护与 post-validation 均完成后出现。 |
| `advisory` / `continue` | ⚠️ <code>EvoZeus · Advisory</code> 可继续 | 兼容漂移可继续业务时使用，不升级为硬阻塞。 |
| `blocked` / `blocked` | 🛑 <code>EvoZeus · Blocked</code> 已阻塞 | 仅用于本地 Harness 硬错误，不代替 Core 的“暂停”语义。 |
| `uat` / `replaced` | 🧪 <code>EvoZeus · UAT</code> 已覆盖 | 唯一 UAT 候选已由新 Commit 覆盖。 |
| `uat` / `passed` | 🧪 <code>EvoZeus · UAT</code> 已通过 | 当前 UAT 候选通过目标门禁。 |
| `uat` / `failed` | 🧪 <code>EvoZeus · UAT</code> 未通过 | 当前候选未通过门禁，禁止升级成已发布。 |
| `release` / `published` | 🚀 <code>EvoZeus · Release</code> 已发布 | 目标 Repo Release 实际发布后出现。 |

## 维护者快速开始

### 1. 诊断目标 Repo

目标必须已经是独立 Git Repo，并配置匹配的 GitHub `origin`：

```bash
python3 scripts/evozeus_wrapper.py skill diagnose \
  --target /absolute/path/to/repo-or-any-path-inside-it \
  --repo OWNER/REPO \
  --json
```

传入子目录时，结果中的 `repository_boundary.repo_root` 是唯一 Harness 位置。

### 2. 生成首次接入计划

```bash
python3 scripts/evozeus_wrapper.py skill transform \
  --mode attach \
  --target /absolute/path/to/repo \
  --repo OWNER/REPO \
  --instruction-surface SKILL.md \
  --visibility private \
  --dry-run \
  --json
```

### 3. 接入 Harness

写入前会验证：目标是 Repo 根目录、GitHub origin 匹配、仓库已存在、当前账号具备 `ADMIN` 权限。

```bash
python3 scripts/evozeus_wrapper_bootstrap.py /absolute/path/to/repo \
  --skill-name "My Skillware" \
  --repo OWNER/REPO
```

完成后在目标 Repo 内提交 Harness 变更，并通过 PR 进入该 Repo 的唯一 UAT 候选。

### 4. 检查与升级

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-check \
  --target /absolute/path/to/repo \
  --json

python3 scripts/evozeus_wrapper.py harness upgrade-all \
  --latest-version vMAJOR.MINOR.PATCH \
  --dry-run \
  --json
```

`upgrade-all` 的实际写入还需要 `--approve`，并逐个验证目标 Repo 的管理员权限、干净工作区和可回滚快照。

## Harness 产物

CoEvolve 在目标 Repo 根目录维护：

```text
target-repo/
├── .evozeus-wrapper/
│   ├── wrapper.json
│   ├── CHANGELOG.md
│   ├── policies/
│   ├── hooks/
│   ├── scripts/
│   └── docs/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/
└── <existing Skillware files>
```

`.codex/` 或其他宿主固定发现位置只保留薄接点。目标业务规则仍由原 Repo Owner 管理。

## 版本与渠道

Skillware Release 与 Harness Version 是两条版本轴：

- **Skillware Release**：目标 Repo 对用户交付的业务版本。
- **Harness Version**：CoEvolve 治理能力版本，记录在 `.evozeus-wrapper/wrapper.json`。

每个目标 Repo 只有一个 UAT 候选。修复 UAT 问题时覆盖该候选；通过验证后，使用同一个已验证 Commit 发布正式 Release。

## 安全与隐私

- 本 Repo 和公开模板不保存 raw private session、客户资料、Secret 或未脱敏证据。
- Feedback Issue 只记录复现场景、期望行为和脱敏证据边界。
- Public / private 由目标 Repo Owner 决定。
- GitHub Pages 属于潜在公开发布面，未确认前保持关闭。
- 普通 Skill 调用不会自动获得 Harness 维护权限。

## Research artifact

**Paper:** *EvoZeus-CoEvolve: An Add-On Harness for Collaborative Evolution of Existing Skillware*

**Authors:** Haodi Fan and Zucong Lan, MetaInFlow

**Emails:** [anthonyfan@metainflow.cn](mailto:anthonyfan@metainflow.cn), [neillan@metainflow.cn](mailto:neillan@metainflow.cn)

**Artifact manifest:** [`research/collaborative-evolution/`](research/collaborative-evolution/)

**Skillware foundation:** [arXiv:2607.18970](https://arxiv.org/abs/2607.18970)

公开证据当前支持 attachment 与 governed lifecycle 的可行性。跨用户聚合效果、frontier code/research adapters、candidate generation 与效果优越性继续按 Implemented / Partial / Planned 边界陈述。

## 开发与验证

```bash
python3 -m pytest -q
python3 -m py_compile \
  scripts/evozeus_wrapper.py \
  scripts/evozeus_wrapper_bootstrap.py \
  scripts/evozeus_wrapper_global_hook.py \
  scripts/evozeus_wrapper_lifecycle.py \
  scripts/evozeus_wrapper_preflight.py
```

贡献前请阅读 [AGENTS.md](AGENTS.md) 与 [CHANGELOG.md](CHANGELOG.md)。

版本说明归档见 [Release Notes](docs/releases/README.md)。

## License

[MIT](LICENSE)
