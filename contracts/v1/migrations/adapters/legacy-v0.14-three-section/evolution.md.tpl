## 自进化方法

本 Skill 已由 EvoZeus-CoEvolve 接入自进化闭环。后续任何行为改动都必须先留下可追踪证据，再进入实现。

源头发现顺序：

1. 先读取本 repo 的 `.evozeus-wrapper/wrapper.json`，以 `canonical_repo` 作为目标 repo。
2. 再检查 `~/.evozeus/.projects/{{REPO_NAME}}` 是否存在并指向 canonical repo。
3. 验证 canonical repo 的 git origin / GitHub repo 可访问。
4. 再检查 runtime install：`~/.codex/skills/<skill-name>`、`~/.agents/skills/<skill-name>`；它们只能是指向 canonical repo 的安装指针。
5. 只有 wrapper manifest 和 project pointer 都无法确认时，才进入 GitHub user/org/public search。

进化流程：

1. 使用中出现不满意、纠错、漏检或可复用机制缺陷时，先完成当前业务纠正，再运行 feedback audit，并通过 `--context` 提供一句脱敏 Lesson 摘要；原样显示返回的 `user_notice.display_text`，询问是否只记录到 Skill Feedback Issue。只有明确确认后才创建 Issue，修复继续要求单独授权。
2. 每次运行本 Skill 前，先执行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo {{REPO_NAME}}`，确认 wrapper source contract 成立。
3. 再执行 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo {{REPO_NAME}}`，确认 GitHub latest release 没有新版本。
4. 开始修改前，在 `.evozeus-wrapper/docs/designs/` 新建设计文档，明确 Related issue、优化目标、实现计划、验证计划和 release plan。
5. PR 必须同步更新 `SKILL.md` 与 `.evozeus-wrapper/CHANGELOG.md`，并通过 `python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure` 和 PR 检查。
6. 合并后用 `vMAJOR.MINOR.PATCH` release tag 和 release notes 固化本次进化，保留可回滚记录。

边界：不要把 raw private session、客户资料、secret、未脱敏商业上下文写入公开 Issue、docs 或 release notes；`~/.evozeus/.projects/{{REPO_NAME}}/` 应指向 canonical repo，runtime-only install 只能是指针，不能作为 copied install 或第二事实源直接修改。

Target repo: `{{REPO_NAME}}`
Visibility: `{{VISIBILITY}}`
Current Skill version: `{{CURRENT_VERSION}}`
Wrapper harness version: `{{WRAPPER_VERSION}}`

