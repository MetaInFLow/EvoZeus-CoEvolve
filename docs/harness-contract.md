# Harness Contract

## 核心判断

EvoZeus-CoEvolve 面向一个已经存在的独立 Skillware Git Repo。用户安装和启动 EvoZeus，由 EvoZeus 在该 Repo 需要反馈闭环、版本治理或 Harness 维护时路由到 CoEvolve attachment layer。

活动 Harness 只能位于独立 Git Repo 根目录的 `.evozeus-wrapper/`。Repo 内部 Skill、package、pack 和 app 继承根 Harness；普通文件夹和嵌套 Harness 不在合同范围内。任何 Harness 写入、升级或上传要求目标 GitHub Repo 的 `ADMIN` 权限。

边界决策见 [`specs/2026-07-30-independent-repo-harness-boundary.md`](specs/2026-07-30-independent-repo-harness-boundary.md)。

它把目标补齐成一个最小 Collaborative Evolution lifecycle harness：

```text
environment diagnosis
  -> target Skill diagnosis
  -> canonical Skill repo
  -> runtime symlink install
  -> SKILL.md
  -> feedback issue
  -> design doc
  -> PR
  -> CHANGELOG
  -> release
  -> repo dashboard + optional GitHub Pages deployment
```

## Before / After Contract

| 阶段 | 文件和能力 |
| --- | --- |
| Before | 本地 Skill 文件夹，至少包含 `SKILL.md` |
| After | canonical GitHub repo、repo dashboard、可选 GitHub Pages、`.evozeus-wrapper/` canonical harness、Issue template、PR template、host hook entrypoint、runtime symlink |

## Runtime Integration Contract

Wrapper 不能把所有启动检查都叫作 hook。`.evozeus-wrapper/wrapper.json` 必须记录当前事实级别：

| capability | 含义 |
| --- | --- |
| `repo_maintenance_hook` | target project-local `SessionStart`，只覆盖 canonical repository 维护。 |
| `global_session_dispatcher` | user-level `SessionStart`，每个任务启动时聚合检查全部 registered wrapped Skills。 |
| `skill_entry_preflight` | Agent 选中 Skill 后由 instruction surface 执行，基本绑定当前 Skill但依赖 prompt compliance。 |
| `plugin_lifecycle_hook` | Plugin 提供稳定 lifecycle 路径，但没有 Skill invocation event。 |
| `tool_gateway` | 只覆盖统一经过 MCP/tool 的执行路径，可由 `PreToolUse` 约束。 |
| `skill_invocation_hook` | 当前 unsupported；只有未来 `SkillInvoke` 或等价事件才能原生精确绑定 Skill 选择。 |

`integration.mode` 描述 Skill invocation 本身的 enforcement。仅有 project/global/plugin hook 时不得置为 native Skill invocation；Skill 入口检查继续使用 `prompt_runtime_check`。

Preflight 必须阻止能力夸大：project hook 的 scope 不是 `canonical_repository`、声称 `covers_skill_invocation=true`，或 manifest 没有真实 `SkillInvoke` 证据却声称 native invocation 时，结构检查必须失败。

### Runtime Authorization Contract

- 正常 Skill 调用只读取 Harness 状态，不授予升级、迁移、创建分支、创建 worktree 或其他维护写入权限。
- compatible Harness drift 返回 advisory warning 并继续 Skill 业务流程；输出聚合数量、当前已知 latest version 和明确的维护授权提示，不暴露目标名称或本地路径。
- 用户明确请求 Harness 维护或升级后，系统先生成 dry-run 方案；实际写入继续要求单独确认并执行完整 preflight、备份和回滚契约。
- source contract 损坏、manifest 无效、迁移冲突或已确认不兼容属于硬错误，可以阻塞当前业务流程并给出修复动作。
- Skill release 与 Harness version 始终作为两条版本轴呈现。

### Runtime Identity Contract

每次调用受管 Skill 时，目标 instruction surface 在入口 preflight 后读取 `runtime_identity.display_line`，并把它原样放在第一条用户可见输出的第一行。本次 invocation 的后续 commentary 与 final 不重复；下一次 invocation 再展示。

```text
🧙🏻‍♂️ `EvoZeus · 受管 Skill` [OWNER/REPO](https://github.com/OWNER/REPO) · Skill vX.Y.Z · Harness vA.B.C · `渠道：开发版/UAT/正式版`
```

- 身份头使用 Unicode `🧙🏻‍♂️`，禁止 HTML、图片路径和自定义 shortcode。
- `canonical_repo` 与 Harness version 来自 wrapper manifest；Skill release 来自目标 release/CHANGELOG；渠道来自 Git branch/HEAD/status 与 GitHub latest release 的交叉验证。
- clean 受控 `uat/*` 来源显示 `UAT`；clean HEAD 与 latest release commit 完全一致时显示 `正式版`；其他情况 fail closed 为 `开发版`。
- 当前平台没有原生 `SkillInvoke` 事件，展示由 `prompt_runtime_check` instruction surface 约束，不能声明 native enforcement。

### Target Skill Notice Contract

每个受管目标 Skill 必须包含：

- `.evozeus-wrapper/policies/notice-policy.json`：目标本地、可配置、升级时受冲突保护的视觉合同。
- `.evozeus-wrapper/scripts/evozeus_notice.py`：只读 Notice CLI，输出文本或 JSON，始终返回 `writes=false`。

统一语法：语义 Emoji + `EvoZeus · <event>` Tag + 状态。普通业务进度不加 EvoZeus Tag。Lesson 在业务纠正完成后显示，并只询问是否记录 Feedback Issue；记录、修复、UAT 和发布沿用各自授权门。默认 event 为 `skill`、`lesson`、`evolution`、`maintenance`、`advisory`、`blocked`、`uat` 和 `release`。

## Single Source Contract

同一个 Skill 在本地只允许一个 physical GitHub repo clone。

| 路径 | 语义 |
| --- | --- |
| canonical repo | 唯一事实源 |
| `~/.evozeus/.projects/OWNER/REPO` | 指向 canonical repo 的 EvoZeus 项目指针 |
| `~/.codex/skills/<skill-name>` | 指向 canonical repo 的 Codex runtime 指针 |
| `~/.agents/skills/<skill-name>` | 可选 Agents runtime 指针，保留时也必须指向 canonical repo |

安装副本只是部署入口，不能成为第二事实源。旧 real-directory 安装副本如果与 canonical repo 不一致，必须先 diff、归档或让用户确认。

## Source Discovery Contract

wrapper-managed Skill 的源头发现顺序固定，不允许跳过：

1. 读取目标 repo 的 `.evozeus-wrapper/wrapper.json`；发现旧 manifest 时先执行布局迁移，不允许 fallback 运行。
2. 用 manifest 的 `canonical_repo` 推导 `~/.evozeus/.projects/OWNER/REPO`。
3. 检查该 project pointer 必须是 symlink，并 resolve 到 canonical repo。
4. 验证 canonical repo 的 git origin 与 `canonical_repo` 一致，且 GitHub repo 可访问。
5. 再检查 `.codex/skills/<skill-name>` 和 `.agents/skills/<skill-name>`；这些路径只能是 runtime pointer，不是可直接修改的事实源。
6. 只有 wrapper manifest 和 project pointer 都不存在时，才允许进入当前用户 repo、用户 org repo、public GitHub search 的 fallback。

如果 project pointer 缺失、不是 symlink、或与 canonical repo 不一致，preflight 必须失败或给出高优先级错误。runtime real-directory copy 必须报告 warning 或 error，不能被描述为 source。

## 生成后的目标 repo 必须回答

| 问题 | 产物 |
| --- | --- |
| 用户在哪里看这个 Skill 的当前状态？ | `.evozeus-wrapper/docs/index.md`；确认能力后可启用 GitHub Pages |
| 使用中结果不满意怎么反馈？ | `.github/ISSUE_TEMPLATE/skill-feedback.yml` |
| 修改 Skill 前怎么想清楚？ | `.evozeus-wrapper/docs/design-doc-template.md` + `.evozeus-wrapper/docs/designs/*.md` |
| 每次迭代怎么留下记录？ | `.evozeus-wrapper/CHANGELOG.md` |
| wrapper harness 怎么迁移？ | `.evozeus-wrapper/docs/migrations/` + `.evozeus-wrapper/wrapper.json` |
| 上传前怎么挡住低质量变更？ | `.evozeus-wrapper/scripts/evozeus_wrapper_preflight.py` + GitHub Actions |
| release 是否有说明？ | `.evozeus-wrapper/CHANGELOG.md` tag entry + GitHub release notes |

## Visibility Contract

创建目标 GitHub repo 前，必须让用户选择：

- `public`：适合公开 Skill、公开问题反馈；确认 Pages 已启用后设置 `EVOZEUS_PAGES_ENABLED=true`。
- `private`：适合内部 Skill、客户相关 Skill 或尚未审查的 Skill。

不要默认选择。`public/private` 是产品边界，不是技术细节。

注意：private repo 的 GitHub Pages 可用性取决于 GitHub plan。默认保持 repository-only mode，不设置 `EVOZEUS_PAGES_ENABLED=true`；maintainer validation 仍必须运行并成功。Pages 发布面可能被外部访问，敏感内容不能进入 `.evozeus-wrapper/docs/`。

## Evolution Contract

一次 Skill 迭代必须经过：

1. 反馈 Issue：说明不满意结果、期望结果、复现场景、证据边界和影响程度。
2. Design doc：说明修复的 Issue、优化目标、优化方向、怎么优化、怎么验证、release plan。
3. PR：引用 design doc，更新 `SKILL.md` 和 `.evozeus-wrapper/CHANGELOG.md`。
4. Release：tag 与 `.evozeus-wrapper/CHANGELOG.md` 对齐，release description 非空。

## Preflight Contract

`.evozeus-wrapper/scripts/evozeus_wrapper_preflight.py` 至少支持五类检查：

- `doctor`：本地依赖与源头检查。必须确认 `git`、`gh`、`gh auth status`。旧 manifest 只触发 migration-required 错误；迁移完成后才验证 canonical manifest、project pointer、origin 和 runtime pointer。
- `structure`：目标 repo 是否包含驾驶舱必需文件。
- `issue`：Issue 内容是否满足反馈模板字段。
- `pr`：PR 是否有 design doc，且 changelog 有记录。
- `release`：release tag 是否在 changelog 中，release notes 是否非空。

## Lifecycle CLI Contract

`scripts/evozeus_wrapper.py` 负责把运行过程显式拆成阶段：

```bash
python3 scripts/evozeus_wrapper.py env diagnose --json
python3 scripts/evozeus_wrapper.py skill diagnose --target /absolute/path/to/skill --repo OWNER/REPO --json
python3 scripts/evozeus_wrapper.py skill transform --mode bootstrap --target /absolute/path/to/skill --repo OWNER/REPO --visibility private --dry-run --json
python3 scripts/evozeus_wrapper.py publish reinstall --skill-name skill-name --canonical-path /absolute/path/to/repo --target codex --dry-run --json
python3 scripts/evozeus_wrapper.py publish reinstall --skill-name skill-name --canonical-path /absolute/path/to/repo --target codex --json
python3 scripts/evozeus_wrapper.py hook global plan --json
python3 scripts/evozeus_wrapper.py hook global install --approve --json
python3 scripts/evozeus_wrapper.py hook global status --json
python3 scripts/evozeus_wrapper.py loop lesson --dry-run --json
python3 scripts/evozeus_wrapper.py loop audit --target /absolute/path/to/skill --user-input "<input>" --json
python3 scripts/evozeus_wrapper.py loop issue-to-pr --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-check --target /absolute/path/to/skill --json
python3 scripts/evozeus_wrapper.py harness migrate-layout --target /absolute/path/to/skill --latest-version v0.14.0 --dry-run --json
python3 scripts/evozeus_wrapper.py harness migrate-layout --target /absolute/path/to/skill --latest-version v0.14.0 --approve-plan 'sha256:<exact-plan-digest>' --json
python3 scripts/evozeus_wrapper.py harness rollback-migration --target /absolute/path/to/skill --snapshot /absolute/path/to/trusted-snapshot --approve --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.14.0 --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.14.0 --approve --approve-plan 'sha256:<exact-batch-plan-digest>' --json
```

`loop audit` 默认不写 GitHub；它输出 `should_capture`、短 signal id、结构化 Lesson `user_notice`、`route`、`severity` 和脱敏 Issue body。默认下一步是完成业务纠正、展示 Lesson，并等待用户确认是否记录，不返回可直接执行的 Issue 命令。提交 Issue 与启动修复是两次独立授权；Issue 授权不扩张为分支、design doc 或 PR 授权。`publish reinstall` 先完整预校验；真实目录只有在 `--approve-archive` 下才会归档并替换。写入、发布、创建 Issue、创建 PR、启用 Pages 都必须在诊断报告之后进入用户确认。

## Harness Version Contract

目标 Skill release 和 wrapper harness version 是两条版本轴。

- Skill release 描述目标 Skill 行为版本。
- Wrapper harness version 描述 EvoZeus-CoEvolve 注入的模板、脚本和治理逻辑版本。
- `.evozeus-wrapper/wrapper.json` 必须记录 `layout_version=2`、`wrapper_repo`、`wrapper_version`、`canonical_repo`、`managed_files`、`install_links`、`integration.mode`、`integration.capabilities`、`onboarding`、`instruction_surface`、`harness_skill_path`、`harness_skill_version` 和 `harness_skill_managed=true`。
- `onboarding` 必须覆盖 canonical symlink 安装、目标 Skill 调用、目标所有的初始化，以及子 Skill 继承 Repo 根 Harness后的独立运行验证。
- `dashboard.deployment_mode=opt_in_github_pages`；workflow validation 不依赖 Pages，部署由 `EVOZEUS_PAGES_ENABLED=true` 显式开启。
- 最新 wrapper 版本默认取 GitHub latest release；来源不可用时必须返回 `latest_unknown` 和查询证据，不能回退为当前版本。
- wrapper major upgrade 必须用户确认。
- consolidated layout 的版本刷新只改 profile 明确授权且具备 exact preimage hash 的 harness-managed files，不改目标 Skill 业务规则。
- legacy layout、heading、frontmatter、terminal signature、regex 与旧路径只产生 read-only discovery candidates；`legacy-scattered-to-canonical-v1.0`、`prerelease-ambiguous-to-manual-review` 和 unknown profile 固定返回 `writes=false`。其中 prerelease profile 覆盖缺少 exact migration contract / managed-block receipt 的 v1.1 目标。
- `migrate-layout` 和 `upgrade-all` 都必须拒绝绝对路径、`..` 越界和任何包含 symlink component 的 write path；manifest-selected instruction surface 同样必须是 target 内部的普通文件。
- wrapper upgrade 必须生成带 `migration_protocol_version`、profile/adapter 独立身份、from/to、write/delete/move set、每个 preimage/postimage、受保护业务面、验证、回滚和 `plan_sha256` 的迁移方案；有冲突时不得写入。
- apply 必须接收 `--approve-plan sha256:<digest>` 并与重算计划完全一致。GitHub `ADMIN` 只证明 Repo 权限，不替代计划批准。批量升级同时要求 `--approve` 与 exact `batch_plan_sha256`，并保存每个 target 的计划 digest 逐目标传入；任何 batch/target replan digest 变化都会阻断写入。
- 自动迁移当前仅覆盖 `canonical-v1.0-to-v1.1@v1.0.0` exact artifact profile。该 profile 保持 compact entry 与完整 instruction surface 字节不变，只刷新合同列出的 Harness Skill、preflight、migration contract、manifest 和 migration record。
- apply 前必须验证官方 origin、clean source worktree、`HEAD==release tag commit==official remote tag commit`、tagged manifest/contract 与所有实际 postimage source bytes。未发布 source 允许 plan，apply 固定 `source_unreleased`、`writes=false`。
- `upgrade-all` 的显式 latest version 必须与 dispatcher cache、环境 override 或 GitHub latest release 一致；该校验必须发生在“已是最新”判断前。每个 target 必须是可验证的 clean Git worktree，write set 及其父目录可写，且任何写路径不得经过 symlink。
- canonical Harness Skill 固定为 `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`，独立契约版本为 `v1.1.0`。其 frontmatter name、版本、普通调用只读边界、Feedback Issue、Issue-to-PR、Harness 维护、UAT、Release 与 rollback 路由必须通过 structure 校验。
- 目标 instruction surface 在业务主链路前必须出现且只出现一次 compact activation block；该块不超过 8 行，Markdown label 与 relative link 都必须等于 manifest 的 canonical path。
- `structure` 严格要求新契约；`doctor` 对完整 legacy manifest 给出 `migrate-layout` 提醒，对路径越界、symlink、文件缺失、frontmatter 损坏、版本不兼容或 entry/manifest 不一致直接失败。
- destructive authority 只能来自 manifest/stable block identity、版本化 profile/adapter 和 exact preimage hash。历史候选没有该证据链时进入 manual review，任何目标字节保持原样。
- 每次 apply 在目标 Repo 外创建 receipt-bound snapshot。rollback 在首个恢复写入前完成 schema、transaction、plan digest、descriptor digest、backup-set digest、全部备份 hash、路径类型与目标当前状态校验；手工 rollback 必须传 `--approve`。
- 目标 Harness 必须安装并校验 Notice policy 与 CLI；canonical Harness Skill 统一声明 Lesson 捕获和 EvoZeus 生命周期 Tag 的展示规则。
- `.evozeus-wrapper/docs/migrations/` 是 wrapper harness 迁移账本；`.evozeus-wrapper/CHANGELOG.md` 仍主要记录目标 Skill 行为 release。

## Case: GitHub-backed Skill already exists

`engineering-everything` 自进化时暴露了一个 wrapper 级反模式：agent 先修改安装副本，再查公开 GitHub，最后才发现用户自己的 repo 已存在。正确机制必须进入 wrapper，而不是只留在具体 Skill case 中：

1. 先检查 `git` / `gh` / `gh auth status`。
2. 当前目录必须解析到独立 Git Repo 根目录；验证 `origin` 与目标 GitHub Repo 匹配。Repo 不存在时先创建 Repo，不能预先写入 Harness。
3. 当前目录只是安装副本时，先查当前 `gh` 用户 repo，再查用户所属 org repo，最后才扩大到公开 repo 搜索。
4. lesson 候选先进入 GitHub Issue 队列，再决定是否写入 Skill 内部 lesson / pattern。
5. 安装副本只作为部署目标；GitHub repo clone 才是 canonical source。
6. 已有 repo 的 Skill release 不能重置为 wrapper 的 `v0.1.0`。版本来源顺序是：
   - GitHub latest release tag。
   - `.evozeus-wrapper/CHANGELOG.md` 最新 `vMAJOR.MINOR.PATCH` 条目，并先为该 tag 创建或确认 GitHub release。
   - 两者都没有时停止，让 owner 选择首个 Skill version。
7. 接入 Harness 不重置目标 Repo 的版本；wrapper harness version 另由 `.evozeus-wrapper/wrapper.json` 记录。

## Data Policy

- Public repo 只保存公开或脱敏信息。
- raw private session、客户资料、secret、商业上下文不入仓。
- 需要保留来源时，记录来源类型、时间、路径或公开 URL；不要复制敏感全文。
- 如果 GitHub Pages 可能暴露敏感信息，停止发布 Pages。

## Non-goals

- 不做通用 prompt 管理平台。
- 不做 agent runtime。
- 不做 EvoZeus 的平级入口。
- 不做自动评分神谕。
- 不替代 human review。
- 不把所有 Skill 都迁到一个 repo。
