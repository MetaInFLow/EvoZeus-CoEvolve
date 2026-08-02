# Canonical Harness Skill 与紧凑业务入口｜Issue #38 实施设计｜Active

日期：2026-07-31

关联 Issue：<https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/38>

关联治理项：<https://github.com/MetaInFLow/EvoZeus-CoEvolve/issues/36>

## 一句话定性

每个受管 Repo 只保留一个 wrapper-owned Harness Skill；业务 instruction surface 只放一个不可跳过、可验证、最多八行的相对路径加载块。

## 真问题与成功标准

当前 Harness 把状态、身份、Notice、反馈、维护、升级和发布规则直接注入业务 `SKILL.md`、`AGENTS.md` 或控制 Skill。规则重复、升级膨胀和业务语义污染来自同一个根因：业务入口同时承担业务规则与 Harness 协议真相源。

完成后必须满足：

1. canonical Harness Skill 固定为 `.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md`。
2. 业务 instruction surface 只保留一个四行 wrapper-owned 激活块，frontmatter、trigger、工作流、输出合同和示例保持业务所有权。
3. `wrapper.json` 记录 Harness Skill 路径、版本和 managed identity；入口引用必须与 manifest 完全一致。
4. structure / doctor 在读取前拒绝 absolute path、Windows absolute path、`..`、路径错配和 symlink escape。
5. 存量 migration discovery 只读；删除、移动和替换必须由版本化 profile、adapter identity、stable block identity 与 exact preimage hash 共同授权。证据不足时 `writes=false`，目标业务字节和换行原样保留。
6. 正常调用按 `integration.mode` 与 `integration.capabilities` 的事实解释覆盖范围；instruction-surface preflight 继续依赖 prompt compliance，不新增 native `SkillInvoke` 声明。兼容旧 Harness 保持 advisory / fail-open；确定性的 manifest、路径或 source contract 错误继续阻断。

## PSPS

| Persona | Scenario | Pain | Solution Surface |
| --- | --- | --- | --- |
| Skillware 使用者 | Host 选中受管 Skill 后进入业务流程 | 正常调用加载约百行低频维护规则 | 四行强制加载块 + Harness Skill 的最小运行路径 |
| Skillware 维护者 | attach、repair、upgrade 或 release | 业务规则与 Harness 规则难以区分 | wrapper-owned Skill、manifest identity、迁移记录 |
| EvoZeus 维护者 | 批量升级多个目标 Repo | 追加式 Prompt 漂移，难以验证和回滚 | 版本化 profile、确定性 write set、digest approval、事务回滚 |

## 架构决策

### 1. 唯一入口

canonical path 固定为：

```text
.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md
```

路径不允许由目标 Repo 自由配置。manifest 只记录并证明该固定事实；篡改到其他路径时 preflight 直接失败，避免把 manifest 变成任意文件读取入口。

Harness Skill 使用独立合同版本 `v1.1.0`。该版本描述 Prompt / frontmatter 合同，与 migration protocol、profile、adapter、Skillware Release 和 wrapper release 版本轴分离。

### 2. 业务激活块

统一激活块：

```markdown
<!-- evozeus-harness-entry:v1 -->
**CRITICAL — 进入业务主链路前 MUST 使用 Read 工具读取并执行
[.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md](.evozeus-wrapper/skills/using-evozeus-harness/SKILL.md)。**
<!-- /evozeus-harness-entry -->
```

- 有 YAML frontmatter 时紧随 frontmatter。
- 无 frontmatter 且以 H1 开头时紧随 H1，保留标准文档标题。
- root `SKILL.md`、root `AGENTS.md` 与 hook/plugin 选中的控制 Skill 使用同一块。
- marker 同时提供 managed identity 与安全迁移边界。

### 3. Harness Skill 分层

正常业务路径只执行：

1. 读取 `wrapper.json` 并校验 Harness Skill identity。
2. 在 manifest 所属的本地 Repo 根运行 `structure --target .`、`doctor --target .`、`identity --target . --json`；`canonical_repo` 的 OWNER/REPO slug 不作为本地路径参数。
3. 按真实状态显示一次 identity，普通业务输出不刷 Notice。
4. 进入目标业务主链路。

低频意图按需路由：

- 反馈与 Lesson：feedback policy、audit rule、Issue 授权门。
- 修复与 Issue-to-PR：design doc、PR preflight，并引用未来统一 branch contract；本 PR 不实现 #36。
- Harness 维护：WRAPPER guide、upgrade dry-run、独立写入授权。
- UAT / Release / rollback：CHANGELOG、release preflight 与现有生命周期门禁。

所有 repo、Skill release、wrapper version、integration mode 和路径事实来自 `wrapper.json` 与实际 Repo；模板不写入目标特定值，也不把任一 integration mode 硬编码为当前目标事实。

### 4. Manifest 合同

新增字段：

```json
{
  "harness_skill_path": ".evozeus-wrapper/skills/using-evozeus-harness/SKILL.md",
  "harness_skill_version": "v1.1.0",
  "harness_skill_managed": true
}
```

`managed_files` 同时包含该文件。`instruction_surface` 继续记录业务入口，`integration.mode` 继续为现有事实值；该变更不会声明 native `SkillInvoke`。

### 5. 兼容状态

| 状态 | structure | doctor / 正常运行 | 修复路径 |
| --- | --- | --- | --- |
| canonical v1.1 | 严格通过 | 正常继续 | 无 |
| canonical v1.0 exact artifact | 要求迁移 | apply 前阻断 | plan 后批准 exact digest |
| compatible legacy Prompt | manual migration | advisory / fail-open | 只读 discovery + reviewed adapter backlog |
| manifest 声明 canonical 但文件缺失或损坏 | 阻断 | 阻断 | approved repair / versioned profile |
| 路径错配、absolute、traversal、symlink escape | 阻断 | 阻断 | 恢复 canonical path 后重跑 |
| Harness Skill major version 不兼容 | 阻断 | 阻断 | 使用兼容 release 迁移 |

## 版本化迁移协议

1. **Inspect**：解析 manifest、canonical marker 与历史候选。Regex、frontmatter、heading、terminal signature、旧路径和 Markdown 边界只生成 diagnostic candidates，`destructive_authority=false`。
2. **Profile**：`legacy-scattered-to-canonical-v1.0@v1.0.0`、`prerelease-ambiguous-to-manual-review@v1.0.0` 与 unknown profile 固定进入 manual review；其中 prerelease profile 专门隔离缺少 exact migration contract / managed-block receipt 的 v1.1 目标。当前唯一 automatic profile 是 `canonical-v1.0-to-v1.1@v1.0.0`。
3. **Evidence**：automatic profile 同时要求 manifest identity、唯一 stable marker block、frozen v1.0 Harness Skill artifact hash、exact v1.0 preflight hash、adapter id/version/digest。任一证据缺失、重复 marker 或额外 legacy candidate 都降级为 manual、`writes=false`。
4. **Plan**：输出 migration protocol、contract、profile、adapter、from/to、write/delete/move set、protected business surfaces、每个 preimage/postimage、source release attestation、validation、rollback 与 self-excluding `plan_sha256`。
5. **Approve**：用户批准 exact `plan_sha256`；apply 使用 `--approve-plan` 进行 compare-and-swap。GitHub `ADMIN` 仍需验证，但不构成 plan approval。
6. **Source trust**：要求 official origin、clean worktree、`HEAD`、local release tag commit 与 official remote tag commit 四者一致；tagged manifest 绑定 contract，实际 Harness/preflight postimage bytes 与 tag 逐字节一致。未发布 branch 保持 `source_unreleased`、`writes=false`。
7. **Snapshot/apply**：在 target Repo 外创建带 descriptor digest receipt 与 backup-set digest 的完整 snapshot；复验所有 target preimages 和 protected surface CAS；预先 staging 全部 postimage；只写批准集合。
8. **Verify/rollback**：验证全部 postimage、instruction surface byte-exact 与 structure。任何失败先完整校验 snapshot metadata、receipt、备份 hash、路径类型和当前状态，再执行恢复。手工 rollback 还需显式 `--approve`。

Fresh attach 只在 instruction surface 同时满足“零 canonical markers、零 historical candidates”时 additive 插入四行块。预存未知 managed destination 文件会被保留并阻断，即使调用 `--force`。

## 写集

| 路径 | 责任 |
| --- | --- |
| `templates/target/.evozeus_evoinfra/skills/using-evozeus-harness/SKILL.md` | canonical Harness Skill 模板 |
| `scripts/evozeus_wrapper_bootstrap.py` | fresh attach 模板映射与紧凑入口注入 |
| `scripts/evozeus_wrapper_lifecycle.py` | manifest、迁移识别、write set、业务字节保护 |
| `scripts/evozeus_wrapper_preflight.py` | structure / doctor 的路径、frontmatter、版本和引用校验 |
| `scripts/evozeus_wrapper.py` | attach / adopt / repair dry-run 的新 write set |
| `skills/` 与 target references | 新入口和按意图路由口径 |
| `contracts/v1/` | hash-bound template inventory |
| `tests/` | fresh、migration、security、target shape 与 smoke 回归 |
| `CHANGELOG.md` | Unreleased 行为与验证记录 |

`README.md`、`README.zh-CN.md` 和 Issue #37 的 README 一致性测试不在本写集。

## 可验证切片

| Slice | 可观察行为 | 最小门禁 | Review |
| --- | --- | --- | --- |
| A | fresh attach 只注入四行块并生成 Harness Skill | bootstrap + contract unit tests | Eng / DX |
| B | structure / doctor 拒绝损坏与越界合同 | security matrix tests | Code / Security |
| C | legacy/ambiguous 候选零写入，exact v1.0 profile 可验证迁移 | LF/CRLF、fenced code、missing terminal、unknown layout、rollback tests | Eng / QA |
| D | single / AGENTS / hooked bundle 使用同一模式 | parameterized structure tests | QA / DX |
| E | 全量回归和真实公开 Repo 只读副本 smoke | pytest、py_compile、preflight、diff check | Release |

## 变更规模

本项跨模板、迁移和验证三层，预计行为代码与文档较集中，测试矩阵会贡献主要增量。若总 diff 超过 1000 行，单 PR 仍保持一个不可分割的迁移合同；PR 会单列机械 fixture / 参数化安全测试行数，并确保生产逻辑可独立审查。出现可独立发布的第二行为主题时再拆 stacked PR。

## 回滚

- CoEvolve Repo：revert 本 PR。
- 目标 Repo：使用受信 snapshot 和显式 `rollback-migration --approve`；验证失败自动恢复。
- 兼容 fallback：旧 Harness 继续使用已有 Prompt 与 advisory 规则，直到版本化 adapter 具备完整证据链或 owner 完成人工审查。
- 回滚不会修改目标 Skillware Release，也不会自动创建 branch、PR、UAT 或 Release。

## 非目标

- 不创建 native `SkillInvoke` 能力。
- 不实现 #36 contributor branch contract。
- 不改变 Feedback Issue、修复、Harness 写入、UAT 与 Release 的分阶段授权。
- 不修改目标 Skill 的业务规则或输出合同。
- 不修改 README 与用户可见事件词典。
