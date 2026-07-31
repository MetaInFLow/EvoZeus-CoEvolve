# EvoZeus 目标 Skill Notice 系统

状态：对内-已通过，已由普通 Chat Lesson watcher 扩展

Related issue: https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/27

## 1. 目标

让每个受管目标 Skill 都具备一致、可配置、可验证的 EvoZeus 用户可见通知能力。普通业务输出保持原业务表达；只有 EvoZeus 生命周期事件显示语义 Emoji 与 `EvoZeus · <事件>` Tag。

用户可观察结果：

- 调用开始能区分目标 Skill、双版本轴和开发 / UAT / 正式渠道。
- 发现可复用纠偏时，在完成当前业务纠正后展示 Lesson，并询问是否只记录反馈。
- Lesson 记录、Skill 进化、Harness 维护、UAT 覆盖、正式发布、兼容提醒和硬阻塞有稳定视觉语法。
- Notice 渲染不产生外部写入，也不扩张任何授权。

## 2. 架构决策

### Owning layer

| 层 | 职责 |
|---|---|
| EvoZeus-CoEvolve | Notice schema、默认 policy、渲染器、目标模板、升级与契约测试 |
| 目标 Skill `.evozeus-wrapper/` | 保存本地 policy 与 CLI；目标调用按状态段规则展示 Notice |
| 目标 Skill 业务内容 | 继续负责业务判断，不拼接 EvoZeus Tag |
| Global SessionStart dispatcher | 只负责会话级健康与版本提示，不伪造目标 Skill invocation Notice |
| Global UserPromptSubmit watcher | 注册目标 inventory、可信 Session Signal companion transport；零持久化、fail-open、不证明精确 Skill invocation |

### 目标 Skill 文件

```text
.evozeus-wrapper/
├── policies/
│   └── notice-policy.json
└── scripts/
    └── evozeus_notice.py
```

`notice-policy.json` 是目标本地配置和默认视觉合同。它属于 Harness managed files；目标 owner 可以修改，后续 Harness 升级发现差异时必须进入 merge review，禁止静默覆盖。

`evozeus_notice.py` 是纯只读渲染器。它读取 policy、校验 kind/state/input，输出人类可读文本或 JSON，不创建 Issue、不改 Skill、不切分支、不进入 UAT、不发布 Stable。

## 3. 用户可见语法

```text
<semantic emoji> `EvoZeus · <event>` <state label>

<message>

<optional single action>
```

默认事件：

| kind | Emoji | Tag | states |
|---|---|---|---|
| `skill` | 🧙🏻‍♂️ | `EvoZeus · 受管 Skill` | `active` |
| `lesson` | 💡 / 📝 | `EvoZeus · Lesson` | `pending` / `recorded` |
| `evolution` | 🛠️ | `EvoZeus · Evolution` | `authorized` / `running` / `verified` |
| `maintenance` | 🔧 | `EvoZeus · Maintenance` | `pending` / `running` / `completed` |
| `advisory` | ⚠️ | `EvoZeus · Advisory` | `continue` |
| `blocked` | 🛑 | `EvoZeus · Blocked` | `blocked` |
| `uat` | 🧪 | `EvoZeus · UAT` | `replaced` / `passed` / `failed` |
| `release` | 🚀 | `EvoZeus · Release` | `published` |

Signal ID 默认不在用户文案中展示；结构化 JSON 保留它用于诊断和 Issue 关联。

## 4. CLI 合同

```bash
python3 .evozeus-wrapper/scripts/evozeus_notice.py render \
  --kind lesson \
  --state pending \
  --message "项目巡检必须校验未关闭任务负责人和实时在职状态。" \
  --action "是否记录到 Skill Feedback Issue？仅记录，不启动修复。"
```

默认输出文本。追加 `--json` 后输出：

```json
{
  "schema_version": "v1",
  "kind": "lesson",
  "state": "pending",
  "icon": "💡",
  "tag": "EvoZeus · Lesson",
  "state_label": "待记录",
  "message": "项目巡检必须校验未关闭任务负责人和实时在职状态。",
  "action": "是否记录到 Skill Feedback Issue？仅记录，不启动修复。",
  "display_text": "...",
  "writes": false
}
```

## 5. Lesson 触发与展示

同时满足以下条件才生成 Lesson Notice：

1. 可复用：未来调用可能再次发生。
2. 可归因：可定位到 Skill、Harness、数据源或流程规则。
3. 可行动：能够形成规则、测试或修复。

典型信号包括用户明确不满意、纠错、指出漏检，或 Agent 在复盘中发现可复用机制缺陷。临时网络错误、单次业务数据变化和纯表达偏好不自动生成 Lesson。

展示顺序：先完成当前业务纠正，再在同一响应末尾展示独立 Lesson Notice。`pending` 状态只询问是否记录；Issue 提交与修复授权保持分离。

## 6. Scope

- 新增 policy、renderer、目标模板复制和升级刷新。
- `runtime_identity.display_line` 使用新的受管 Skill Tag，并继续展示 canonical repo、双版本轴和渠道。
- feedback audit 增加结构化 `user_notice`，保留现有字段兼容旧调用方。
- 目标 Skill 状态段、自进化方法和 wrapper 文档引用本地 CLI。

## 7. Non-goals

- 不新增宿主原生 `SkillInvoke` Hook。
- 不建立跨任务 pending Lesson 数据库。
- 不自动创建 Issue、分支、worktree、PR、UAT 或 Stable release。
- 不允许目标 Skill 将业务进度伪装为 EvoZeus 生命周期事件。
- 不引入外部依赖或图形资源。

## 8. 验证

1. Notice renderer 各 kind/state 精确输出，非法输入失败。
2. 自定义 policy 能改变允许的展示字段，默认合同保持一致。
3. renderer 的文本与 JSON 均声明 `writes=false`。
4. bootstrap 与 Harness refresh 把 policy 和 CLI 安装到目标 Skill。
5. structure/doctor 把缺少 Notice 能力判为 Harness 结构缺口。
6. feedback audit 产生 Lesson `user_notice`，默认隐藏 signal ID，询问 record-only consent。
7. runtime identity 以 `🧙🏻‍♂️` 开始，Tag 为 `EvoZeus · 受管 Skill`，渠道仍 fail closed。
8. 对临时目标 Skill 跑通 bootstrap、structure、identity、notice、feedback audit。
9. 完整 pytest 和 Python compile 通过。

## 9. 发布链

```text
codex/dev/20260727-target-skill-notice-system
  -> tests / target smoke
  -> overwrite single uat/current
  -> user UAT
  -> later Stable release by separate approval
```

回滚：`uat/current` 可覆盖回上一已知良好 commit；Stable 在本轮保持不变。

## 10. 开发验证结果

- Notice taxonomy、配置覆盖、非法输入、CLI JSON/text 与无写入合同通过自动化测试。
- bootstrap、managed-file inventory、structure gate、feedback audit 和 runtime identity 已覆盖回归测试。
- 真实大兴二期目标 Skill 的临时 clone 已完成 `v0.12.1 -> v0.13.0` Harness refresh，新增 policy/CLI 且 structure 通过。
- 身份头在干净 `uat/current` 目标分支显示 `渠道：UAT`；Lesson Notice 默认隐藏 signal ID，并明确“仅记录、不启动修复”。
- 目标 preflight 不再生成 Notice bytecode cache。
