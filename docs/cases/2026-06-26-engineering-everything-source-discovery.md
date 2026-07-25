# Case: Engineering Everything source discovery and dependency gate

## 背景

在 `engineering-everything` 自进化过程中，wrapper 机制暴露了两个缺口：

- agent 先修改了本地安装副本，再回头找 GitHub repo。
- agent 先做公开 GitHub / Web 搜索，漏掉了用户自己的 repo。

这个问题不是 `engineering-everything` 的孤立问题，而是所有 GitHub-backed Skill wrapper 都会遇到的源头治理问题。

## 错误路径

```text
local installed Skill copy
  -> direct edit
  -> public web / repo search
  -> discover user repo late
  -> migrate changes back manually
```

风险：

- 安装副本变成事实源头，GitHub repo 落后。
- 公开搜索结论误导 agent，以为没有 canonical source。
- `gh` 缺失或未登录时，agent 可能仍继续假设 GitHub 可访问。

## 正确 wrapper 流程

```text
request to evolve Skill
  -> dependency doctor: git + gh + gh auth
  -> source discovery:
       current git origin
       current gh user repo
       current gh org repos
       public repo search only as last resort
  -> GitHub issue intake for lesson candidates
  -> design doc / PR / CHANGELOG / release
  -> install or sync local Skill copy
```

## Wrapper 规则

1. `doctor` 必须先跑；缺 `git`、缺 `gh`、`gh auth status` 失败时停止。
2. 如果目标目录已有 git origin，origin 必须是可访问的 GitHub repo。
3. 如果目标目录不是 git repo，必须显式传 `--repo OWNER/REPO`，或者从当前 `gh` 用户 / org 搜到候选 repo；bootstrap 阶段目标 repo 尚未创建时，使用 `--allow-missing-repo`。
4. lesson candidate 先进入 GitHub Issue，不直接写入 `lessons.md`。
5. 本地安装副本只是部署目标；GitHub repo clone 才能作为 canonical source。

## 对 EvoZeus-CoEvolve 的改动

- `scripts/evozeus_wrapper_preflight.py doctor`
- `scripts/evozeus_wrapper_bootstrap.py` 在 bootstrap 前检查 `gh auth`
- `docs/harness-contract.md` 增加 dependency/source discovery contract
- target templates 的 Local Checks 增加 doctor

## 验证

```bash
python3 scripts/evozeus_wrapper_preflight.py doctor --repo MetaInFLow/EvoZeus-CoEvolve
python3 scripts/evozeus_wrapper_preflight.py structure --target /tmp/evozeus-wrapper-target
python3 scripts/evozeus_wrapper_preflight.py issue --file /tmp/evozeus-wrapper-issue.md
python3 scripts/evozeus_wrapper_preflight.py pr --target /tmp/evozeus-wrapper-target --design-doc /tmp/evozeus-wrapper-target/docs/designs/2026-06-26-wrapper-doctor.md
python3 scripts/evozeus_wrapper_preflight.py release --target /tmp/evozeus-wrapper-target --tag v0.1.0 --release-notes /tmp/evozeus-wrapper-release.md --skip-gh
```
