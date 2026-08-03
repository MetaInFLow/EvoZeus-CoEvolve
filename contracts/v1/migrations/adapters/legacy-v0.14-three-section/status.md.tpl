## EvoZeus-CoEvolve 状态检查

本段是 Skill 入口 preflight。Agent 选中本 Skill 后、进入业务主链路前执行；它基本绑定当前 Skill，但依赖 instruction compliance，不是 native Skill invocation hook。

`.evozeus-wrapper/wrapper.json` 分开记录 capability：`repo_maintenance_hook` 只在 canonical repository 作为活动项目时原生触发；`global_session_dispatcher` 在每个任务启动时聚合检查全部 wrapped Skills；本入口仍记录为 `prompt_runtime_check`。当前 Codex 没有 `SkillInvoke` 事件，不得把前两者描述成 per-Skill native invocation hook。

若当前只是 runtime-only install，缺少维护资产时不要把安装副本当作事实源，回 canonical repo 处理 wrapper harness 或 Skill release。

1. Skill release 状态
   - 当前记录版本：`{{CURRENT_VERSION}}`
   - 检查命令：`python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version --repo {{REPO_NAME}}`
   - 如果 GitHub latest release 更新：先更新 canonical repo，并确认 runtime install 仍指向 canonical repo。
   - 如果本地版本领先 GitHub release：先完成 changelog、验证和 `vMAJOR.MINOR.PATCH` release，再把它当作稳定运行版本。
2. Wrapper harness 状态
   - 当前 wrapper 版本：`{{WRAPPER_VERSION}}`
   - 事实源：`.evozeus-wrapper/wrapper.json`
   - 检查命令：在 EvoZeus-CoEvolve repo 运行 `python3 scripts/evozeus_wrapper.py harness upgrade-check --target <this-skill-repo> --json`
   - 如果 wrapper 落后且 `upgrade-check` 未发现冲突或不兼容：报告当前与最新版本；兼容的旧 wrapper 只作为维护提醒，不阻塞业务主链路。
   - 普通 Skill 调用不授权 Harness 升级或其他维护写入。只有用户明确请求 Harness 维护或升级后，才运行 `harness upgrade --dry-run` 生成方案；实际写入仍需单独确认。
3. Source contract 状态
   - 检查命令：`python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor --repo {{REPO_NAME}}`
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

