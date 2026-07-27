# EvoZeus 调用身份头与反馈授权实施计划

状态：执行中

依据：`docs/superpowers/specs/2026-07-27-evozeus-runtime-identity-header-design.md`

## 目标

让每个接入 EvoZeus-CoEvolve 的 Skill 在每次 invocation 的第一条用户可见输出中显示一次可信身份头，并把反馈捕获、Issue 提交、修复执行分成独立授权状态。

## Task 1：身份事实与三态渠道

修改：

- `scripts/evozeus_wrapper_preflight.py`
- `tests/test_evozeus_wrapper_lifecycle.py`

步骤：

1. 先新增失败测试，覆盖正式版、UAT、开发分支、dirty worktree、未发布 Skill 和 GitHub release 不可用。
2. 新增纯渠道分类函数，机器值固定为 `development | uat | stable`。
3. 新增 runtime identity 构建函数，读取 canonical repo、Skill release、Harness version、Git branch/HEAD/status 和 GitHub latest release。
4. 新增 `identity --target <path> --json` 命令；输出版本化 `runtime_identity`，包含可直接展示的 `display_line`。
5. 无法验证 manifest、origin 或 canonical repo 时沿用硬错误；其他不确定性统一降级为开发版。

验收：身份头以 `🧙🏻‍♂️` 开头，GitHub 地址可点击，双版本轴分离，渠道不会误报。

## Task 2：每次调用一次的入口合同

修改：

- `scripts/evozeus_wrapper_lifecycle.py`
- `scripts/evozeus_wrapper_bootstrap.py`
- `scripts/evozeus_wrapper_preflight.py`
- `tests/test_evozeus_wrapper_lifecycle.py`

步骤：

1. 先为 generated status section 增加失败测试。
2. 更新 Skill 入口状态段：完成 doctor/version 后运行 identity，并把 `runtime_identity.display_line` 原样放在首次用户可见输出第一行。
3. 明确同一 invocation 后续 commentary/final 不重复；下一次 invocation 再展示。
4. 保持 `prompt_runtime_check` 事实，不夸大为原生 `SkillInvoke` enforcement。
5. 更新结构检查关键词，确保新建和升级后的目标 Skill 都保留身份头合同。

验收：新建目标、旧布局迁移和重复升级均生成同一入口合同，目标业务内容字节保持不变。

## Task 3：反馈捕获与三级授权

修改：

- `scripts/evozeus_wrapper_lifecycle.py`
- `skills/evolution-loop/SKILL.md`
- `templates/target/.evozeus_evoinfra/audit-rule.md`
- `templates/target/.evozeus_evoinfra/feedback-policy.json`
- `tests/test_evozeus_wrapper_lifecycle.py`

步骤：

1. 先新增失败测试，要求捕获结果包含短 signal id、Unicode marker、`LOCAL_PENDING_CONFIRMATION` 和 `writes=false`。
2. `plan_feedback_audit` 只生成当前 invocation 内的结构化捕获结果，不返回可直接执行的 Issue 写入动作。
3. 默认 `next_action` 改为继续业务并等待用户确认是否提交反馈。
4. 固定授权状态机：捕获 -> Issue 明确授权 -> 修复新的明确授权 -> design/PR。
5. 更新 target policy 与 evolution-loop 指令，禁止把 Issue 授权扩张为修复或 PR 授权。

验收：捕获阶段无文件、GitHub、branch、design 或 PR 写入。

## Task 4：版本、合同包与发布记录

修改：

- `scripts/evozeus_wrapper_bootstrap.py`
- `CHANGELOG.md`
- `contracts/v1/target-template-inventory.json`
- `contracts/v1/manifest.json`
- `tests/test_contract_bundle.py`

步骤：

1. 把新生成 Harness version 设为计划中的 `v0.12.1`。
2. 在 `Unreleased` 记录身份头、三态渠道和反馈授权修复。
3. 模板变化后重新计算 governed template tree hash 与 contract manifest 文件 hash。
4. 将 contract source revision 和对应测试更新为 `v0.12.1`，保留 bundle/schema 兼容版本。

验收：contract bundle 的文件集合与哈希门禁通过，Stable README 在正式 release 前不提前宣称 `v0.12.1` 已发布。

## Task 5：完整验证与交付

验证：

```text
python3 -m pytest -q
python3 -m py_compile scripts/evozeus_wrapper.py scripts/evozeus_wrapper_bootstrap.py scripts/evozeus_wrapper_global_hook.py scripts/evozeus_wrapper_lifecycle.py scripts/evozeus_wrapper_preflight.py templates/global/evozeus_wrapper_dispatcher.py templates/target/.codex/hooks/evozeus_wrapper_start_check.py
python3 scripts/evozeus_wrapper_preflight.py structure
git diff --check
```

使用临时 target 执行 bootstrap、identity、structure 和重复 migration smoke。确认：

- 正式版身份头与 UAT/开发版判定正确。
- 每次调用展示一次的 instruction surface 存在。
- 捕获审计不产生外部写入。
- managed files 更新可重复执行。
- 原有 134 项回归保持通过，新增用例全部通过。

## 提交边界

实施保持单主题分支。完成后提交代码与验证记录，停在 UAT 准备状态；不创建正式 release，不更新 Stable，不批量升级已接入 Skill。
