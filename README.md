# EvoZeus-CoEvolve

**An Add-On Harness for Collaborative Evolution of Existing Skillware**

EvoZeus-CoEvolve 是 EvoZeus 体系中的 Collaborative Evolution 系统与公开论文 artifact 入口。它为本地静态 `SKILL.md`、根入口为 `AGENTS.md` 的 runtime kit，或由 hook / plugin 控制的 Skill bundle 加装可审查、可发布、可恢复的演进 harness，同时保留目标 Skillware 的核心任务逻辑和原开发方式。

当前 `v0.11.2` 已实现 target diagnosis、增量 attachment、canonical repo/runtime binding、feedback/design/PR/release preflight、版本检查、迁移和本地恢复等 lifecycle substrate，并支持 plugin-first Skillware 的 canonical 子 Skill 安装指针。跨用户信号聚合、frontier code/research adapters、candidate generation 和独立行为评价仍按公开研究边界推进；README 和论文不会把这些待实现能力写成现有结果。

## Research paper

- **Title**: *EvoZeus-CoEvolve: An Add-On Harness for Collaborative Evolution of Existing Skillware*
- **Author**: Haodi Fan, MetaInFlow
- **Email**: [anthonyfan@metainflow.cn](mailto:anthonyfan@metainflow.cn)
- **Author GitHub**: [HaodiFan](https://github.com/HaodiFan)
- **Artifact**: [MetaInFLow/EvoZeus-CoEvolve](https://github.com/MetaInFLow/EvoZeus-CoEvolve)
- **Skillware foundation**: [arXiv:2607.18970](https://arxiv.org/abs/2607.18970)
- **Artifact manifest and claim boundary**: [`research/collaborative-evolution/`](research/collaborative-evolution/)

Collaborative Evolution 在本文中指：一个具有共享身份的 Skillware Unit，从其用户群体和指定外部来源获得演进信号，将信号转换为可独立验证的候选变更，并把通过治理的版本重新分发给兼容用户。贡献证据的用户与受益用户可以分离。

Repository 与产品名已统一为 `EvoZeus-CoEvolve`。为保持已接入 Skill 的运行兼容，`.evozeus-wrapper/`、`evozeus_wrapper.py`、`EVOZEUS_WRAPPER_*` 与现有 Skill slug 继续作为稳定技术标识。

用户入口由 EvoZeus 提供。当 EvoZeus 判断一个 promoted Skill 或已有本地 Skill 需要 repo 化、反馈闭环和版本治理时，会路由到 EvoZeus-CoEvolve。

本 repo 的 root `SKILL.md` 只做薄入口，不承载完整操作流程。实际使用协议放在 `skills/using-evozeus-wrapper/SKILL.md`，再由它按阶段调用 environment diagnosis、target diagnosis、evolution surface diagnosis、status assessment、transform、publish/reinstall、loop 和 harness upgrade Skills。

**Before**：本地可能是单 Skill 文件夹（根 `SKILL.md`），也可能是 runtime kit / Skill bundle（根 `AGENTS.md` + `skills/*/SKILL.md`），还可能是由 plugin manifest 和 session hook 加载控制 Skill 的系统。

**After**：该 Skill 或 runtime kit 变成一个可运行、可反馈、可审查、可发布的 GitHub repo：

- 有一个 repo-local 驾驶舱；GitHub Pages 在仓库能力确认后显式启用。
- 用户在创建时明确选择 `public` 或 `private`。
- 创建前检查目标 GitHub repo 是否已经存在，避免重复 harness。
- 本地只保留一个 physical canonical repo，`~/.evozeus/.projects/OWNER/REPO` 和 runtime 安装路径都指向它。
- wrapper 先做 evolution surface assessment，再把 wrapper-owned 状态检查区放进真正控制 agent 行为的说明面：单 Skill 通常是 `SKILL.md`，runtime kit 通常是 `AGENTS.md`，hook/plugin 控制的系统可能是被 session hook 加载的控制 Skill，例如 `skills/<control-skill>/SKILL.md`。新增内容只说明状态检查、自进化方法和 `EvoZeus-CoEvolve` 路由，不改写业务规则。
- wrapper 产物统一归入 `.evozeus-wrapper/`，manifest 位于 `.evozeus-wrapper/wrapper.json`。
- `.evozeus-wrapper/docs/onboarding.md` 和 manifest 的 `onboarding` 字段记录安装、调用、目标 Skill 初始化及子 Skill hook 接入契约。
- `.evozeus-wrapper/CHANGELOG.md` 记录每次 Skill 迭代。
- 增加 GitHub Issue 反馈入口，专门收集“使用中结果不满意”的场景。
- 增加 `.evozeus-wrapper/docs/design-doc-template.md` 和 `.evozeus-wrapper/docs/designs/`，每次 Skill 更新先写 design doc。
- 增加上传前检查：Issue 格式、PR design doc、CHANGELOG、release tag 和 release notes。

## 第一性原理

静态 Skill 会在真实使用中持续暴露新需求、环境差异和可迁移改进。EvoZeus-CoEvolve 为这些演进信号提供可追踪、可审查、可回滚的治理路径，并使通过验证的共享版本能够服务后续兼容用户。

EvoZeus-CoEvolve 的最小闭环是：

```text
environment diagnosis
  -> GitHub access / permission check
  -> target repo architecture, Skill inventory, and evolution surface assessment
  -> component gap report
  -> one physical canonical GitHub repo
  -> ~/.evozeus/.projects/OWNER/REPO pointer
  -> runtime symlink install
  -> repo dashboard + optional GitHub Pages deployment
  -> feedback Issue
  -> design doc
  -> PR
  -> CHANGELOG
  -> release
  -> run-time latest release check
```

这不是 agent runtime，也不是 prompt 管理平台，也不是和 EvoZeus 平级的用户入口。它只做一件事：在 EvoZeus 调度下，把一个本地 Skill 文件夹或 Skill bundle / runtime kit 包装成可自我进化的协作单元。

## 使用方式

### 1. 分阶段诊断

通常由 EvoZeus 判断并路由到本 repo。维护者也可以直接运行阶段 CLI：

```bash
python3 scripts/evozeus_wrapper.py env diagnose --json
python3 scripts/evozeus_wrapper.py skill diagnose --target /absolute/path/to/my-skill-or-runtime-kit --repo MetaInFLow/my-skill --json
python3 scripts/evozeus_wrapper.py skill transform --mode bootstrap --target /absolute/path/to/my-skill --repo MetaInFLow/my-skill --instruction-surface <relative path> --visibility private --dry-run --json
python3 scripts/evozeus_wrapper.py publish reinstall --skill-name my-skill --canonical-path /absolute/path/to/my-skill --target codex --dry-run --json
python3 scripts/evozeus_wrapper.py publish reinstall --skill-name my-skill --canonical-path /absolute/path/to/my-skill --target codex --json
python3 scripts/evozeus_wrapper.py hook global plan --json
python3 scripts/evozeus_wrapper.py hook global install --approve --json
python3 scripts/evozeus_wrapper.py hook global status --json
python3 scripts/evozeus_wrapper.py harness upgrade-check --target /absolute/path/to/my-skill --json
python3 scripts/evozeus_wrapper.py harness migrate-layout --target /absolute/path/to/my-skill --latest-version v0.11.2 --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.11.2 --dry-run --json
```

如果 `env diagnose` 返回 `next_action: install_evozeus`，先安装 / 初始化 EvoZeus，不进入目标 repo transform。如果没有给 `Visibility`，Agent 必须先问用户选择 `public` 还是 `private`。如果本地发现多个 repo clone 或多个 real-directory 安装副本，必须先让用户选择 canonical repo 或归档策略。

目标 repo 诊断必须先用 `gh repo view` 检查 repo 是否存在、visibility、default branch 和当前账号权限，再判断架构并输出 evolution surface 候选事实：

- `single_skill`：根目录 `SKILL.md`。
- `runtime_skill_bundle`：根目录 `AGENTS.md`，并存在 `skills/*/SKILL.md`、`runtime/`、`agents/`、`automation/` 等 runtime 结构。
- `hooked_skill_bundle`：存在 Codex project-local hook、plugin manifest lifecycle hook 或 hook-loaded control Skill，例如 `.codex/hooks.json` 或 `skills/<control-skill>/SKILL.md`。
- `skill_bundle`：存在多个 `skills/*/SKILL.md`，但没有完整 runtime 结构。
- `agents_runtime`：根目录 `AGENTS.md`，但没有可识别 Skill inventory。

诊断输出必须包含：

- `evolution_surface`：候选说明入口、controller files、脚本事实边界；最终 placement 由 `skills/evolution-surface-diagnosis/SKILL.md` 判断。
- `component_gaps`：当前 repo 缺少哪些 wrapper 文件、manifest、状态检查和 changelog。
- `skill_inventory`：发现了哪些 `skills/*/SKILL.md`。
- `controller_files`：Codex hook / plugin manifest / runtime controller 文件。
- `integration`：分别报告 `repo_maintenance_hook`、`global_session_dispatcher`、`skill_entry_preflight`、Plugin/tool gateway 和未来 Skill invocation hook。当前 Codex 没有 `SkillInvoke`，不得把 project/global hook 描述成 native per-Skill invocation hook。

诊断 JSON 是事实输入，不直接承担用户解释。诊断后必须先使用 `skills/evolution-surface-diagnosis/SKILL.md` 浏览整个 repo 并选择 instruction surface，再使用 `skills/status-assessment/SKILL.md` 做状态分析，把事实和判断转成用户可理解的流程进度、阻塞项和下一步命令；通过状态分析后才进入 transform。

维护者或 agent 直接使用本 repo 时，应先读取 `skills/using-evozeus-wrapper/SKILL.md`，不要把 root `SKILL.md` 当成完整操作手册。

### 2. 用 bootstrap 脚本生成本地驾驶舱文件

```bash
python3 scripts/evozeus_wrapper_bootstrap.py /absolute/path/to/my-skill \
  --skill-name "My Skill" \
  --repo "MetaInFLow/my-skill"
```

如果目标 Skill 有首次初始化，必须同时提供 `--init-command` 和 `--init-verification`。会生成子 Skill 的 factory 还要增加 `--generates-child-skills`；wrapper 只记录和验证契约，不实现 company 专用逻辑。

脚本会先用 `gh repo view OWNER/REPO` 检查目标 GitHub repo 是否已经存在；如果已存在，必须停止，不要重复创建 harness。
脚本在检查 repo 前必须先确认本机有 `git`、`gh`，且 `gh auth status` 通过；如果目标 Skill 已经有 git origin，则 origin 必须是可访问的 GitHub repo。bootstrap 阶段允许目标 repo 尚不存在，但必须明确使用 `--allow-missing-repo`。
然后脚本会交互询问 `public/private`，把 `templates/target/` 中的文件注入目标 Skill 文件夹，并复制 preflight checker。
同时，脚本会把 dashboard、changelog、design docs、policy、preflight 和 hook adapter 统一写入 `.evozeus-wrapper/`，并在 `.evozeus-wrapper/wrapper.json` 记录 `instruction_surface`、`integration.mode` 和 `dashboard` deployment contract。目标说明面只增加状态检查、自进化方法和 wrapper 路由，不改写原业务规则。

### 3. 创建 GitHub repo 和 Pages

在目标 Skill 文件夹中执行：

```bash
git init
git add .
git commit -m "Initialize wrapped Skill dashboard"
gh repo create MetaInFLow/my-skill --source . --public --push
gh release create v0.1.0 --repo MetaInFLow/my-skill --target main \
  --title "v0.1.0" \
  --notes "Initial wrapped Skill harness."
gh api --method POST repos/MetaInFLow/my-skill/pages -f build_type=workflow
gh variable set EVOZEUS_PAGES_ENABLED --body true --repo MetaInFLow/my-skill
gh workflow run evozeus-wrapper-preflight.yml --repo MetaInFLow/my-skill
```

如果选择 `private`，把 `--public` 改成 `--private`，且不要设置 `EVOZEUS_PAGES_ENABLED=true`，除非已确认当前 plan 支持 private Pages。未启用 Pages 时，workflow 仍运行 maintainer validation，并以 repository-only mode 成功结束。Pages 可能成为互联网发布面；不要把敏感内容放进 `.evozeus-wrapper/docs/`。

## 目标 repo 会被增加什么

```text
target-skill/
├── <evolution-surface>
├── .evozeus-wrapper/
│   ├── wrapper.json
│   ├── CHANGELOG.md
│   ├── WRAPPER.md
│   ├── policies/
│   ├── hooks/
│   ├── scripts/
│   └── docs/
│       └── onboarding.md
├── .codex/
│   └── hooks.json
└── .github/
    ├── ISSUE_TEMPLATE/
    │   ├── config.yml
    │   └── skill-feedback.yml
    ├── pull_request_template.md
    └── workflows/
        └── evozeus-wrapper-preflight.yml
```

`.codex/hooks.json` 与 `.github/` 是宿主固定发现位置，只保留薄接点；wrapper 自有实现和内容都在 `.evozeus-wrapper/`。

## 上传前检查

目标 Skill repo 中可以运行：

```bash
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo MetaInFLow/my-skill --allow-missing-repo
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo MetaInFLow/my-skill
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py issue --file issue.md
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py pr --design-doc .evozeus-wrapper/docs/designs/2026-06-26-example.md
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py release --tag v0.1.0 --release-notes release-notes.md
```

检查规则：

- Doctor：必须能找到 `git`、`gh`，`gh auth status` 必须通过；读取 `.evozeus-wrapper/wrapper.json` 后验证 project pointer、canonical repo origin、GitHub repo 和 runtime install pointer。发现 `.evozeus_evoinfra/` 或旧 `.evozeus/wrapper.json` 时必须先迁移，不能继续 fallback 运行。
- Structure：目标 repo 必须包含 `.evozeus-wrapper/` canonical harness。说明入口由 manifest 的 `instruction_surface` 或根 `SKILL.md` / `AGENTS.md` 决定；project hook 必须声明 `scope=canonical_repository` 且不得声称 Skill invocation coverage；`onboarding` 和 `dashboard` contract 必须完整。
- Version：运行 Skill 前必须检查 GitHub latest release；如果远端 release 比本地 `.evozeus-wrapper/CHANGELOG.md` 新，先更新再运行。
- Existing repo version：已有 repo 不得重置为 `v0.1.0`；先取 GitHub latest release，再取 `.evozeus-wrapper/CHANGELOG.md`，两者都没有时让 owner 选择当前版本。
- Harness：`harness upgrade-check` 默认从 GitHub latest release 获取权威最新版本；查询失败时返回 `latest_unknown`，不得把当前版本当成最新版本。旧 layout 或残缺的较早 v2 harness 先运行 `harness migrate-layout --dry-run`；迁移会安全合并 Codex hooks、刷新状态段和 manifest、追加 migration note，并在 structure post-validation 通过后才报告成功。
- Workflow：push 和 workflow_dispatch 始终运行 maintainer validation。Pages deployment 只有在仓库变量 `EVOZEUS_PAGES_ENABLED=true` 时运行；否则明确使用 repository-only fallback。
- Project hook：target `.codex/hooks.json` 只覆盖 canonical repo 维护；adapter 优先复用 global dispatcher 的 latest cache，避免重复联网。
- Global hook：`~/.codex/hooks.json` 调用 `~/.evozeus/hooks/evozeus_wrapper_dispatcher.py`，在 `SessionStart` 聚合检查全部 registered wrapped Skills。安装和 trust 分开报告，写操作必须显式批准。
- Upgrade all：显式版本必须与 dispatcher cache、环境 override 或 GitHub release 的 authoritative latest 一致；全部 target 均需可验证的 clean Git worktree 和写权限。任一 preflight 失败时零写入，应用中途失败则恢复完整 write-set snapshots。
- Reinstall：先用 `--dry-run` 查看计划；实际执行会创建或修正 symlink。真实目录必须显式增加 `--approve-archive`，原内容移动到 `~/.evozeus/archives/runtime-installs/`，不会删除。
- Feedback audit：`python3 scripts/evozeus_wrapper.py loop audit --target <repo> --user-input "<input>" --json` 判断用户纠正、不满意或机制缺陷是否应转为 Skill Feedback Issue，并输出脱敏 Issue draft；默认不写 GitHub。
- Issue：必须符合反馈模板，说明不满意结果、期望结果、复现场景、证据边界和影响程度。
- PR：必须有 design doc，且 design doc 说明修复 issue、优化目标、优化方向、实现方式和验证计划。
- PR：必须更新 `.evozeus-wrapper/CHANGELOG.md`。
- Release：必须有对应 tag 的 changelog 记录，且 release description 非空。

## Release 版本标准

所有 release tag 必须使用 `vMAJOR.MINOR.PATCH`。

- `MAJOR`：不兼容的 Skill 行为、输入要求或输出格式变化。
- `MINOR`：新增能力、新增必需证据规则、新增 harness 行为。
- `PATCH`：文案、示例、bug fix、校验修复或不破坏兼容性的澄清。

新建目标 repo 的首个 wrapped Skill release 使用 `v0.1.0`。已有 repo 走 `adopt`，保留既有 Skill release；`.evozeus-wrapper/wrapper.json` 中的 wrapper harness version 是另一条版本轴。

## 边界

本 repo 不保存目标 Skill 的业务内容，不上传 raw private session，不替代目标 Skill 的 owner 判断。

如果目标 Skill 涉及客户资料、商业资料、secret 或个人隐私，默认使用 private repo，并且 `.evozeus-wrapper/docs/` 里只放脱敏内容。
