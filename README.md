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

python3 scripts/evozeus_wrapper.py harness upgrade-all \
  --latest-version vMAJOR.MINOR.PATCH \
  --publish \
  --json
```

`--dry-run`、`--approve`、`--publish` 是互斥模式。`--approve` 用于本地全量升级；`--publish` 是管理员发布流程，会逐个验证目标 Repo 的 `ADMIN` 权限、可信 origin、干净工作区和可回滚快照，再从远端默认分支建立隔离 worktree 与升级 PR。UAT Harness source 只有成为权威 GitHub Release 后才能发布。

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
