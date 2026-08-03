## EvoZeus-CoEvolve

本区由 EvoZeus-CoEvolve 追加，用来说明本 Skill 的 wrapper harness 路由、版本记录和迁移规则。它不覆盖原 Skill 的业务规则；涉及业务行为变化时，仍必须走 Issue、design doc、PR、CHANGELOG 和 release。

调用 wrapper 的场景：

1. 本 Skillware Repo 需要 attach/adopt/repair Harness，或确认 canonical source。
2. `.evozeus-wrapper/wrapper.json` 中的 wrapper harness version 落后于 EvoZeus-CoEvolve 最新版本。
3. `~/.evozeus/.projects/{{REPO_NAME}}`、`.codex` 或 `.agents` runtime install 疑似不是同一个 source of truth。
4. 使用反馈先进入当前 invocation 的本地待确认状态；用户明确确认后才提交 Skill Feedback Issue；另获修复授权后才能进入 design doc、PR、CHANGELOG、release 的自进化闭环。
5. 目标 GitHub repo、release tag、GitHub Pages 或 preflight check 需要创建、诊断或修复。

路由规则：

- 目标 Skill 行为问题：先捕获为本地待确认信号；用户确认提交后才创建 Skill Feedback Issue，修复和 PR 继续要求单独授权。
- 源头/安装问题：先运行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo {{REPO_NAME}}`。
- 结构问题：运行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure`。
- Skill release 问题：运行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo {{REPO_NAME}}`。
- wrapper harness 升级：回到 EvoZeus-CoEvolve repo，运行 `python3 scripts/evozeus_wrapper.py harness upgrade-check --target <this-skill-repo> --json`，再用检查结果中的最新版本运行 `harness upgrade --dry-run` 生成迁移方案。

Append-only 迁移规则：

- wrapper 升级必须保留 frontmatter 后的状态检查；其他 `SKILL.md` wrapper 内容只能追加本区缺失内容或 migration note，不要重写原 Skill 业务段落。
- 如果本区已存在，升级时追加 migration note，不改写旧文本。
- 每次 wrapper 升级必须记录 from/to wrapper version、planned files、验证命令、回滚方案和是否需要人工 merge review。
- wrapper version 事实源是 `.evozeus-wrapper/wrapper.json` 的 `wrapper_version`；Skill release 仍以 GitHub release / `.evozeus-wrapper/CHANGELOG.md` 为准。

Wrapper harness version: `{{WRAPPER_VERSION}}`
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
