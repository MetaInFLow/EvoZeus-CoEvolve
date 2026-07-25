# 对内-未审核-EvoZeus-CoEvolve 分阶段生命周期设计

## 背景

EvoZeus-CoEvolve 当前已经具备最小 wrapper harness 能力：为一个本地 Skill folder 注入 dashboard、Issue template、design doc template、CHANGELOG、preflight checker，并建立 release/version 检查。

现有机制的不足不是单个检查缺失，而是运行过程没有把完整生命周期显式拆开。真实场景中，用户给出的目标 Skill 可能已经安装过、已经有 GitHub repo、已经部分接入 harness，甚至本地有多个 clone 和多个 runtime 安装副本。如果继续把这些判断塞进 bootstrap，会导致 source of truth 混乱。

本设计把 EvoZeus-CoEvolve 收敛为五阶段生命周期：

```text
环境诊断
-> 目标 Skill 诊断
-> 目标 Skill 改造
-> 发布重新安装
-> 持续迭代 loop
```

核心原则：

```text
One physical repo as truth. EvoZeus and runtime installs are pointers.
```

同一个 Skill 在本地只允许一个 physical GitHub repo clone。`~/.evozeus/.projects`、`~/.codex/skills`、`~/.agents/skills` 都应通过 symlink 指向 canonical repo，不应复制出第二事实源。

## 目标

1. 让每个生命周期阶段在运行过程中可见、可单独执行、可验证。
2. 把阶段拆成清晰的脚本子命令和 Agent 子 Skill。
3. 在写入、发布、重装前先完成诊断和用户确认。
4. 支持已安装 Skill、已有 repo、多个 clone、多个安装副本、wrapper 版本漂移等真实状态。
5. 保持 wrapper 的边界：不做 agent runtime，不做通用 prompt 平台，不改写目标 Skill 的业务规则。

## 非目标

- 不自动选择 public/private。
- 不自动删除旧安装副本。
- 不把安装副本当作 canonical source。
- 不全盘扫描用户 home 目录。
- 不把 wrapper harness 升级混同于目标 Skill 行为升级。
- 不替代 GitHub PR review 或 human review。

## 推荐运行入口

保留现有 `scripts/evozeus_wrapper_preflight.py` 作为底层检查脚本，新增一个总入口：

```text
scripts/evozeus_wrapper.py
```

总入口按阶段组织子命令：

```bash
python3 scripts/evozeus_wrapper.py env diagnose
python3 scripts/evozeus_wrapper.py skill diagnose --target /absolute/path/to/skill --repo OWNER/REPO
python3 scripts/evozeus_wrapper.py skill transform --mode bootstrap|adopt|repair|verify
python3 scripts/evozeus_wrapper.py publish reinstall --target codex|agents|all|path
python3 scripts/evozeus_wrapper.py loop lesson
python3 scripts/evozeus_wrapper.py loop issue-to-pr
python3 scripts/evozeus_wrapper.py harness upgrade-check
python3 scripts/evozeus_wrapper.py harness upgrade
```

运行输出必须显式显示阶段：

```text
[1/5] Environment Diagnosis
[2/5] Target Skill Diagnosis
[3/5] Target Skill Transform
[4/5] Publish & Reinstall
[5/5] Continuous Evolution Loop
```

## 子 Skill 拆分

根目录 `SKILL.md` 保持 router 角色，只负责判断用户意图并路由到子 Skill。

建议新增：

```text
skills/
  environment-diagnosis/SKILL.md
  target-skill-diagnosis/SKILL.md
  target-skill-transform/SKILL.md
  publish-reinstall/SKILL.md
  evolution-loop/SKILL.md
  harness-upgrade/SKILL.md
```

| 子 Skill | 阶段 | 职责 |
| --- | --- | --- |
| `environment-diagnosis` | 环境诊断 | 检查 `.evozeus`、EvoZeus 母项目、`git`、`gh`、`gh auth`、多个 EvoZeus repo |
| `target-skill-diagnosis` | 目标 Skill 诊断 | 确认 Skill 身份、安装副本、repo clone、GitHub repo、harness 状态、visibility、数据边界 |
| `target-skill-transform` | 目标 Skill 改造 | 执行 `bootstrap/adopt/repair/verify`，注入或修复 harness，不改业务规则 |
| `publish-reinstall` | 发布重新安装 | 创建或确认 release，处理 runtime symlink，归档旧副本，验证 active runtime |
| `evolution-loop` | 持续迭代 | 处理 lesson intake 和 Issue-to-PR |
| `harness-upgrade` | Harness 升级 | 检查 wrapper 版本，生成或执行 harness upgrade PR |

## 阶段 1：环境诊断

目标：确认 EvoZeus 母体环境可靠，再处理目标 Skill。

检查项：

- `/Users/anthonyf/.evozeus` 是否存在。
- `.evozeus/runtime` 和 `~/.evozeus/.projects` 是否存在。
- 本地是否已有 `MetaInFLow/EvoZeus` repo。
- 如果有多个 EvoZeus repo，列出路径、remote、branch、HEAD、dirty status。
- `git` 是否可用。
- `gh` 是否可用。
- `gh auth status` 是否通过。
- 远端 `MetaInFLow/EvoZeus` 是否可访问。

交互点：

- `.evozeus` 不存在时，询问是否安装或初始化 `MetaInFLow/EvoZeus`。
- 本地发现多个 EvoZeus repo 时，让用户选择唯一真源位置。
- `gh auth` 不可用时，停止并提示用户登录。

输出：

```json
{
  "stage": "environment_diagnosis",
  "evozeus_home": {
    "exists": true,
    "path": "/Users/anthonyf/.evozeus"
  },
  "mother_repo": {
    "remote": "MetaInFLow/EvoZeus",
    "candidates": [],
    "canonical_path": null,
    "needs_user_choice": false
  },
  "dependencies": {
    "git": "ok",
    "gh": "ok",
    "gh_auth": "ok"
  }
}
```

## 阶段 2：目标 Skill 诊断

目标：确认目标 Skill 的身份、真源、安装状态和发布边界。

检查项：

- 目标路径是否存在。
- 是否包含 `SKILL.md`。
- Skill name 和 display name。
- 目标 GitHub repo：`OWNER/REPO`。
- GitHub repo 是否已存在。
- 当前目录是否在 git repo 内。
- origin 是否是 GitHub repo。
- 本地是否有多个同 remote clone。
- `~/.codex/skills/<skill-name>` 是否存在。
- `~/.agents/skills/<skill-name>` 是否存在。
- 安装路径是 symlink 还是 real directory。
- 安装副本与 candidate repo 的 `SKILL.md` hash 是否一致。
- 是否已接入 wrapper harness。
- 是否部分接入 wrapper harness。
- visibility 是否明确。
- 是否存在 private session、客户资料、secret、商业上下文等敏感发布风险。

扫描范围必须限定：

```text
当前 target path
当前 repo root
~/.evozeus/.projects
~/.codex/skills
~/.agents/skills
用户显式传入的 workspace root
```

不要默认递归扫描整个 `/Users/anthonyf`。

交互点：

- 目标 Skill 路径不明确时，询问目标路径。
- 发现多个 Skill repo clone 时，让用户选择 canonical repo。
- 发现多个安装副本且内容不同，要求用户确认保留或归档策略。
- visibility 未指定时，必须询问 `public` 或 `private`。
- 发现敏感内容风险时，要求用户确认脱敏、private、禁用 Pages 或停止。

输出：

```json
{
  "stage": "target_skill_diagnosis",
  "skill": {
    "name": "resume-screening",
    "target_path": "/Users/anthonyf/.codex/skills/resume-screening",
    "has_skill_md": true
  },
  "repo": {
    "name": "MetaInFLow/resume-screening",
    "exists_on_github": true,
    "candidates": [],
    "canonical_path": null,
    "needs_user_choice": false
  },
  "installs": [
    {
      "path": "/Users/anthonyf/.codex/skills/resume-screening",
      "kind": "real_directory",
      "matches_canonical": null
    }
  ],
  "harness": {
    "state": "missing|partial|complete",
    "wrapper_version": null
  },
  "publication": {
    "visibility": null,
    "sensitive_risk": "unknown"
  }
}
```

## 阶段 3：目标 Skill 改造

目标：根据诊断结果执行最小改造，不把所有状态塞进 bootstrap。

动作模式：

| 模式 | 触发条件 | 动作 |
| --- | --- | --- |
| `bootstrap` | GitHub repo 不存在，目标 Skill 未接入 harness | 注入完整 harness，准备新建 repo |
| `adopt` | GitHub repo 已存在，但未接入 harness | 在既有 repo 中接入 harness |
| `repair` | harness 部分接入或 managed files 缺失 | 补齐缺口，保护用户改动 |
| `verify` | harness 已完整 | 只运行检查，不写入 |

写入内容：

- `WRAPPER.md`
- `CHANGELOG.md`
- `docs/index.md`
- `docs/design-doc-template.md`
- `docs/designs/README.md`
- `.github/ISSUE_TEMPLATE/skill-feedback.yml`
- `.github/pull_request_template.md`
- `.github/workflows/evozeus-wrapper-preflight.yml`
- `scripts/evozeus_wrapper_preflight.py`
- `.evozeus_evoinfra/wrapper.json`
- 根目录 `SKILL.md` 的 `## 自进化方法` 段

改造规则：

- 不改写目标 Skill 的业务规则。
- 不覆盖用户已有文件，除非用户明确确认。
- managed files 如果已被用户改过，生成 diff 并进入确认。
- `~/.evozeus/.projects/OWNER/REPO` 应指向 canonical repo，不再复制一份 repo。

`.evozeus_evoinfra/wrapper.json` 示例：

```json
{
  "wrapper_repo": "MetaInFLow/EvoZeus-CoEvolve",
  "wrapper_version": "v0.2.0",
  "applied_at": "2026-06-27",
  "canonical_repo": "MetaInFLow/resume-screening",
  "managed_files": [
    "WRAPPER.md",
    "docs/index.md",
    "docs/design-doc-template.md",
    "docs/designs/README.md",
    ".github/ISSUE_TEMPLATE/skill-feedback.yml",
    ".github/pull_request_template.md",
    ".github/workflows/evozeus-wrapper-preflight.yml",
    "scripts/evozeus_wrapper_preflight.py"
  ],
  "install_links": [
    "/Users/anthonyf/.codex/skills/resume-screening"
  ]
}
```

## 阶段 4：发布重新安装

目标：让实际 runtime 读取 canonical repo，而不是旧安装副本。

流程：

```text
structure check
-> version check
-> release check
-> create or confirm release
-> archive old real-directory installs when needed
-> create symlink installs
-> verify active runtime realpath
```

安装规则：

- `~/.codex/skills/<skill-name>` 默认是主要 runtime pointer。
- `~/.agents/skills/<skill-name>` 只有用户需要时保留。
- runtime install path 必须是 symlink。
- symlink 目标必须是 canonical repo。
- 如果旧安装副本是 real directory：
  - 内容一致：可替换为 symlink。
  - 内容不一致：先 diff，再归档或让用户确认。
- 删除旧副本必须确认；归档可以作为默认安全动作。

验证规则：

```text
realpath ~/.codex/skills/<skill-name> == canonical repo path
root SKILL.md contains self-evolution method
CHANGELOG.md latest local tag matches GitHub latest release
.evozeus_evoinfra/wrapper.json exists and wrapper version is known
```

## 阶段 5：持续迭代 Loop

持续迭代分两条线：Lesson intake 和 Issue-to-PR。

### 5A：Lesson Intake

目标：把一次使用中出现的可复用经验沉淀为候选 lesson。

流程：

```text
发现 lesson candidate
-> 判断是否重复、是否有价值、是否含敏感信息
-> 问用户是否提交
-> 如果提交，直接创建 GitHub Issue 或 lesson entry
```

规则：

- lesson intake 不直接改 Skill。
- 用户确认提交后直接提交，不进入复杂 PR 流程。
- 仍需检查 `gh auth`、Issue 权限和敏感信息。

### 5B：Issue-to-PR

目标：把已确认的问题或 lesson 转成可审查 Skill 更新。

流程：

```text
选择 Issue
-> 检查 GitHub 权限
-> 生成 design doc
-> 修改 SKILL.md / docs / templates
-> 更新 CHANGELOG.md
-> 跑 preflight
-> 创建 branch
-> push
-> 创建 PR
```

权限分支：

| 权限状态 | 动作 |
| --- | --- |
| 有 write 权限 | 在 canonical repo 创建 branch、push、开 PR |
| 无 write 但可 fork | fork repo，从 fork branch 开 PR |
| 无 fork/PR 权限 | 只生成本地 branch、patch、design doc |
| 无 `gh auth` | 停止，提示登录 |
| private repo 无权限 | 停止 |

## Harness 版本升级

目标 Skill 版本和 wrapper harness 版本必须分开。

| 版本 | 含义 | 发布方 |
| --- | --- | --- |
| Skill release | 目标 Skill 的行为版本 | 目标 Skill repo |
| Wrapper harness version | 注入的 harness 模板、脚本和治理逻辑版本 | `MetaInFLow/EvoZeus-CoEvolve` |

检查时机：

- 环境诊断时检查本地 EvoZeus-CoEvolve 是否有更新。
- 目标 Skill 诊断时读取 `.evozeus_evoinfra/wrapper.json`。
- 运行 `version` 时同时提示 wrapper harness 是否落后。
- Issue-to-PR 前，如果旧 wrapper 会影响检查逻辑，先走 harness upgrade。

升级分支：

| 状态 | 动作 |
| --- | --- |
| wrapper 已最新 | 不处理 |
| patch/minor 更新且 managed files 未改过 | 自动生成 harness upgrade PR |
| managed files 被用户改过 | 生成 diff，让用户确认 merge 策略 |
| major 更新 | 必须确认，因为可能改变 contract |

升级规则：

- 只更新 harness-managed files。
- 不触碰目标 Skill 业务规则。
- 更新 `.evozeus_evoinfra/wrapper.json`。
- 在 `CHANGELOG.md` 中记录 Harness changes。
- PR 标题使用 `Upgrade EvoZeus-CoEvolve harness to vX.Y.Z`。

## 交互策略

原则：

```text
能自动查的自动查；会改变真源、公开范围、安装入口、历史副本的地方，必须主动让用户确认。
```

主动交互点：

- `.evozeus` 不存在，是否安装或初始化 EvoZeus。
- 多个 EvoZeus repo，选择母项目真源。
- 多个目标 Skill repo clone，选择 canonical repo。
- visibility 是 `public` 还是 `private`。
- 发现敏感内容风险，选择脱敏、private、禁用 Pages 或停止。
- GitHub repo 已存在，选择 adopt 或停止。
- harness 文件冲突，选择 repair、保留或覆盖。
- runtime 安装副本是 real directory，选择归档并替换 symlink 或停止。
- `~/.codex` 和 `~/.agents` 都有安装，选择只保留 Codex 或双入口 symlink。
- 创建 GitHub repo、push、release、启用 Pages 前确认。
- 删除旧副本前确认。
- lesson candidate 是否提交。
- wrapper major upgrade 是否执行。

不主动交互：

- 检查 `.evozeus` 是否存在。
- 扫描限定路径内的安装副本。
- 读取 `SKILL.md`、remote、branch、HEAD、dirty status。
- 判断 symlink 或 real directory。
- 运行 doctor、structure、version。
- 生成 diagnosis report。

## 当前机制优化点

1. 增加 `scripts/evozeus_wrapper.py` 总入口，把阶段显式化。
2. 为 `preflight` 增加或配套 `diagnose` 能力，输出机器可读 JSON。
3. 把 `bootstrap` 收窄为“新建 harness 注入”，不再承担真源判断。
4. 将 `~/.evozeus/.projects` 从 local mirror 语义升级为 canonical pointer。
5. 新增 `.evozeus_evoinfra/wrapper.json` 记录 wrapper version、managed files、canonical repo、install links。
6. 增加 symlink reinstall 和 `realpath` 验证。
7. 增加 wrapper harness version check。
8. 增加 `adopt` 和 `repair` 模式，覆盖已有 repo 和部分接入状态。
9. 限定扫描范围，避免全盘 home 扫描。
10. 为每个阶段建立 fixture 测试。

## 测试策略

测试按阶段建立 fixture：

| 阶段 | Fixture |
| --- | --- |
| 环境诊断 | 有 `.evozeus`、无 `.evozeus`、多个 EvoZeus repo、`gh auth` 失败 |
| 目标 Skill 诊断 | 非 Skill 目录、单 Skill、多安装副本、多 repo clone、GitHub repo 已存在 |
| 目标 Skill 改造 | 未包装、部分包装、完整包装、managed files 冲突 |
| 发布重新安装 | real directory install、symlink install、内容一致、内容不一致 |
| 持续迭代 | lesson 提交、有 write 权限、无 write 可 fork、无 PR 权限 |
| Harness 升级 | wrapper 最新、patch/minor 更新、major 更新、managed files 被改过 |

最低验证命令：

```bash
python3 scripts/evozeus_wrapper.py env diagnose --json
python3 scripts/evozeus_wrapper.py skill diagnose --target /tmp/skill --repo OWNER/REPO --json
python3 scripts/evozeus_wrapper.py skill transform --mode verify --target /tmp/skill
python3 scripts/evozeus_wrapper.py publish reinstall --dry-run --target codex
python3 scripts/evozeus_wrapper.py harness upgrade-check --target /tmp/skill --json
```

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 阶段过多导致使用复杂 | 总入口提供 `run` 聚合模式，但内部仍显示阶段 |
| symlink 破坏现有安装习惯 | 先 dry-run，旧 real directory 默认归档，不默认删除 |
| 多 repo 扫描慢或误报 | 限定扫描路径，按 remote/hash 过滤 |
| wrapper 升级覆盖用户改动 | managed files 记录 hash，冲突时生成 diff 并确认 |
| GitHub 权限不足导致半成品 | 先权限检查，再写入/推送；无权限时只生成本地 patch |
| private 内容进入 Pages | visibility 和敏感边界为强交互 gate |

## 成功标准

1. 用户能从运行输出看清当前处于哪一阶段。
2. `diagnose` 能在不写文件的情况下给出下一步建议。
3. 同一个 Skill 本地只有一个 physical repo。
4. `~/.evozeus/.projects` 和 runtime install path 都能指向 canonical repo。
5. 已安装 Skill 接入 harness 后，runtime 读取的是新 canonical repo。
6. 目标 Skill release 与 wrapper harness version 分开检查。
7. Lesson intake 能快速提交，Issue-to-PR 能按 GitHub 权限降级处理。
8. 所有写入前都有明确状态判断和必要用户确认。
