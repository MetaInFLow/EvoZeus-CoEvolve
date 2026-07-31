---
title: "{{SKILL_NAME}} 自进化驾驶舱"
---

# {{SKILL_NAME}} 自进化驾驶舱

这是 {{SKILL_NAME}} 的最小自进化驾驶舱。它用于公开当前 Skill 状态、收集使用反馈、追踪设计决策和发布记录。

## 当前状态

| 项目 | 内容 |
| --- | --- |
| Skill | [`SKILL.md`]({{REPO_URL}}/blob/main/SKILL.md) |
| EvoZeus 项目指针 | `~/.evozeus/.projects/{{REPO_NAME}}` |
| Repo | `{{REPO_NAME}}` |
| Visibility | `{{VISIBILITY}}` |
| 当前 Skill 版本 | `{{CURRENT_VERSION}}` |
| Wrapper harness 版本 | `{{WRAPPER_VERSION}}` |
| Dashboard deployment | `opt_in_github_pages`；未启用时为 repository-only |
| Wrapper manifest | `.evozeus-wrapper/wrapper.json` |
| Codex hook registration | `.codex/hooks.json` |
| Codex hook adapter | `.evozeus-wrapper/hooks/evozeus_wrapper_start_check.py` |
| Wrapper migrations | [`.evozeus-wrapper/docs/migrations/`](migrations/) |
| 安装与接入 | [`.evozeus-wrapper/docs/onboarding.md`](onboarding.html) |
| Changelog | [`.evozeus-wrapper/CHANGELOG.md`]({{REPO_URL}}/blob/main/.evozeus-wrapper/CHANGELOG.md) |
| Design docs | [`.evozeus-wrapper/docs/designs/`](designs/) |

## 反馈入口

如果使用中遇到 Skill 输出不满意，先完成业务纠正，再运行 feedback audit，并使用 Notice CLI 显示：

```text
💡 `EvoZeus · Lesson` 待记录
```

受信任的 global `UserPromptSubmit` watcher 会把普通 Chat 轮次接入 Core-owned runtime，无需先 `@Skill`。Core 读取注册目标并执行已验证的 Session Signal 方法；捕获阶段只接受模型侧规则，继续原业务且不执行外部写入。feedback audit JSON 保持内部使用。用户明确确认提交后，才创建 Skill Feedback Issue。Issue 需要包含：

- 不满意的 Skill 结果。
- 期望结果。
- 复现输入或场景。
- 证据边界。
- 影响程度。

创建 Issue 不授权修改 Skill、创建分支、设计文档或 PR；实现修复需要新的明确授权。

## 进化规则

诊断选定的 instruction surface 在业务主链路前保留一个不超过 8 行的强制 Read 块，统一加载 `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`。该 canonical Harness Skill 负责 Skill release、wrapper harness version、source contract、`identity --json`、Notice、授权边界、UAT、Release 与 rollback；业务入口继续承载目标业务规则。每次 Skill invocation 将 `runtime_identity.display_line` 原样放在第一条用户可见输出的第一行，只展示一次；下一次 invocation 再展示。如果当前只是 runtime-only install，安装副本仅用于运行，维护动作回 canonical repo 执行。

身份头固定使用 Unicode `🧙🏻‍♂️`，并显示 canonical repo、Skill release、Harness version 和 `开发版` / `UAT` / `正式版`。禁止使用 HTML、图片路径或自定义 shortcode。

`.evozeus-wrapper/wrapper.json` 分开记录 Skill invocation mode 与 capability：

- `repo_maintenance_hook`：project-local `SessionStart`，仅覆盖 canonical repo 维护。
- `global_session_dispatcher`：user-level `SessionStart`，任务启动时聚合检查全部 wrapped Skills。
- `global_prompt_lesson_watcher`：user-level `UserPromptSubmit`，CoEvolve 只负责 Core runtime 的 Hook 生命周期；Core 负责可信 transport，Session Signal 负责候选判断、目标选择与 guidance，不自动记录。
- `skill_entry_preflight`：Agent 选中 Skill 后按 instruction surface 检查，依赖 prompt compliance。
- `bootstrap_skill`：Plugin lifecycle 可加载控制 Skill，但不会新增 Skill invocation event。
- `manual_only`：只能手动运行 wrapper 命令。

当前 Codex 没有 `SkillInvoke` 事件。`UserPromptSubmit` 能观察 Skill 选择前的用户消息，无法证明精确 Skill invocation。project hook、global dispatcher、prompt watcher、Plugin lifecycle 和 Skill 入口 preflight 都不得描述成 native per-Skill invocation hook。新建或变更 hook 后，需要通过 `/hooks` 审核并单独记录 trust 状态。

安装、调用、初始化和子 Skill 接入以 `.evozeus-wrapper/wrapper.json` 的 `onboarding` 字段及 [onboarding 指南](onboarding.html) 为准。子 Skill 继承 Repo 根 Harness；独立运行入口仍需 consumer-project smoke test，成为独立 Git Repo 后才允许拥有单独 Harness。

push 和 workflow dispatch 始终运行 maintainer validation。只有在确认仓库支持 GitHub Pages，并设置 repository variable `EVOZEUS_PAGES_ENABLED=true` 后，workflow 才部署 dashboard；否则以 repository-only mode 成功结束。

Wrapper-managed Skill 的源头发现顺序固定：

1. 读取 `.evozeus-wrapper/wrapper.json`。
2. 检查 `~/.evozeus/.projects/{{REPO_NAME}}` 是否指向 canonical repo。
3. 验证 canonical repo 的 git origin / GitHub repo。
4. 检查 `~/.codex/skills/<skill-name>` 和 `~/.agents/skills/<skill-name>`，它们只能是 runtime pointer。
5. 只有 wrapper 状态无法确认时，才进入 GitHub user/org/public search。

每次运行 Skill 前，先检查 GitHub latest release 是否有新版本：

```bash
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo {{REPO_NAME}}
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo {{REPO_NAME}}
```

每次 Skill 更新必须先写 design doc，再开 PR。目标 Repo 必须在接入 Harness 前完成独立 GitHub Repo 建立；`~/.evozeus/.projects/{{REPO_NAME}}` 和 runtime 安装路径应指向同一个 canonical Repo，不保留 copied install 作为第二事实源，也不要直接修改 `.codex/skills/...` 或 `.agents/skills/...`。

EvoZeus-CoEvolve harness 升级时，不能重写目标 Skill 业务段落。先在 EvoZeus-CoEvolve repo 里生成迁移方案：

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-check --target /absolute/path/to/this-skill --json
python3 scripts/evozeus_wrapper.py harness upgrade --target /absolute/path/to/this-skill --latest-version <wrapper-version> --dry-run --json
```

迁移记录写入 `.evozeus-wrapper/docs/migrations/`，并记录 from/to wrapper version、planned files、canonical Harness Skill、compact entry、验证命令和回滚方案。存量 instruction surface 只删除具备 wrapper 所有权签名的历史段落，业务段与换行字节保持原样。wrapper harness version 的事实源是 `.evozeus-wrapper/wrapper.json`；Skill release 仍以 GitHub release 和 `.evozeus-wrapper/CHANGELOG.md` 为准。

Design doc 至少回答：

- 修复的 Issue 是什么。
- 优化目标是什么。
- 优化方向是什么。
- 怎么优化。
- 怎么验证。
- release 如何说明。

## Release 版本标准

使用 `vMAJOR.MINOR.PATCH`：

- `MAJOR`：不兼容的 Skill 行为或输出格式变化。
- `MINOR`：新增能力、必需证据规则或 harness 行为。
- `PATCH`：文案、示例、bug fix、校验修复或不破坏兼容性的澄清。

## 上传前检查

```bash
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo {{REPO_NAME}}
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo {{REPO_NAME}}
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py pr --design-doc .evozeus-wrapper/docs/designs/<design-doc>.md
python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py release --tag {{INITIAL_VERSION}} --release-notes release-notes.md
```
