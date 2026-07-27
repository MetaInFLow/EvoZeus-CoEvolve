# 正常 Skill 调用与 Harness 维护授权分离｜Issue #24 修复设计｜对内已通过

## 目标

修复正常 Skill 调用被旧 Harness 错误升级为维护门禁的问题。用户运行业务 Skill 时可以继续获得业务结果，Harness 状态检查保持只读，所有维护写入均需要明确用户意图和独立确认。

关联 Issue：<https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/24>

## 已验证事实

- `awesome-ai-book-writing` Skill release 为 `v0.1.0`，与 latest release 一致。
- 该 Skill Harness 为 `v0.11.4`，检查时 latest 为 `v0.12.0`。
- source contract 正常，且无迁移冲突。
- 真实 consumer 运行将其描述为“前置门槛”，并把不升级归因于用户声明只读。
- 运行前后 canonical repo 保持 clean。

## 根因

| 层级 | 原行为 | 影响 |
| --- | --- | --- |
| project maintenance hook | advisory 模式允许兼容更新继续 | 符合预期 |
| global dispatcher | 任意版本落后均返回 `continue=false` | 把维护提醒提升为全局业务门禁 |
| Skill-entry preflight | 要求先迁移再进入业务主链路 | 旧入口指令继续放大阻塞语义 |

三层策略缺少统一的运行授权契约，Agent 只能从用户是否写了“只读”推断升级权限。

## 统一契约

1. 正常 Skill 调用只读检查 Harness 状态。
2. compatible Harness drift 返回 advisory warning，并继续业务主链路。
3. 正常 Skill 调用不授权升级、迁移、创建分支、创建 worktree 或其他 Harness 写入。
4. 用户明确请求 Harness 维护或升级后，先生成 dry-run；实际写入继续要求单独确认。
5. source contract 损坏、manifest 无效、迁移冲突或已确认不兼容可以阻塞。
6. Skill release 与 Harness version 分开呈现。

## 修改面

- `templates/global/evozeus_wrapper_dispatcher.py`：版本落后分支改为 advisory allow，并注入明确的无写入授权上下文。
- `scripts/evozeus_wrapper_lifecycle.py`：刷新目标 Skill 的状态段，移除兼容更新的业务门禁措辞。
- lifecycle tests：覆盖聚合 warning、consumer workspace、显式维护授权文案和 source contract 硬错误。
- README、Harness Contract 与相关设计文档：统一运行和维护语义。

## 验收

- compatible old Harness：`continue=true`，业务流程继续。
- warning：包含落后数量、latest version、用户明确授权要求，不包含目标名称和本地路径。
- normal invocation：canonical repo 无文件变化，无 branch/worktree 创建。
- hard source error：`continue=false`，返回 repair next action。
- Harness migration refresh：目标入口获得新授权契约，业务内容字节保持不变。
- 完整测试和 Python 编译检查通过。

## 发布边界

本补丁进入唯一 `uat/coevolve-slice-01` 分支，作为 `v0.12.1` 候选。UAT 验证完成前不创建正式 release，不更新 Stable。
