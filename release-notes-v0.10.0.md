# EvoZeus-CoEvolve v0.10.0

本版本修复 [Issue #12](https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/12)：target repo 内的 Codex project hook 不再被描述成“任意 workspace 中调用 Skill 时都会触发”的 native hook。

## 能力边界

- project-local `SessionStart` hook 现在明确为 `repo_maintenance_hook`，作用域仅是 canonical repository。
- user-level global dispatcher 在每个 Codex task 的 `SessionStart` 聚合检查全部 registered wrapped Skills；它是原生强制的 session gate，但不是 per-Skill invocation hook。
- `SKILL.md` 入口 preflight 在 Agent 选中 Skill 后按指令校验该 Skill，是当前最接近精确 Skill 绑定的 fallback，但不是宿主原生强制。
- 当前 Codex 没有 `SkillInvoke` 生命周期事件，因此本版本不声称提供 native per-Skill invocation hook。

## 安装全局 Dispatcher

先查看计划，再显式安装：

```bash
python3 scripts/evozeus_wrapper.py hook global plan --json
python3 scripts/evozeus_wrapper.py hook global install --approve --json
```

安装后通过 Codex `/hooks` 检查并信任 registration，再记录 trust 状态。文件已安装与宿主已信任是两个独立状态。

```bash
python3 scripts/evozeus_wrapper.py hook global trust --status trusted --approve --json
python3 scripts/evozeus_wrapper.py hook global status --json
```

## 严格阻断与升级全部

只要 authoritative latest version 已知且存在落后的 harness，global dispatcher 会在 task 启动时阻断，只提示落后数量和最新版本，不输出 Skill 名称、本地路径或业务数据。

用户确认“升级全部”后，先运行全量 dry-run：

```bash
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.10.0 --dry-run --json
python3 scripts/evozeus_wrapper.py harness upgrade-all --latest-version v0.10.0 --approve --json
```

升级会先确认显式 latest version 与 dispatcher cache、环境 override 或 GitHub release 一致，再预检全部 targets。任何 target 无法验证 clean Git、dirty、冲突或不可写都会保持零写入；应用中任一步失败会恢复本事务的完整 write-set snapshots。升级不会自动提交目标 repo 的文件。

## 网络与缓存

- 每个 `SessionStart` 最多请求一次 authoritative latest release。
- 远端失败时优先使用有效缓存；只有 bounded stale cache 时会明确标记来源。
- 远端失败且没有可用缓存时警告并放行，避免网络故障永久锁死 Codex。
- manifest 或 project pointer 等确定性本地错误仍会阻断并要求 repair。

## 回滚

```bash
python3 scripts/evozeus_wrapper.py hook global uninstall --approve --json
```

global hook lifecycle 会在修改前备份现有配置和 EvoZeus-owned runtime files，并在中途失败时完整恢复。target harness upgrade 会备份所有可能被改写、移动或删除的文件；legacy migration 可能更新 target-owned 文件中的旧 wrapper 路径引用，但不得改变 Skill 业务语义。
