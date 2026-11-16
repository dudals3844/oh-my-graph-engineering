[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Español](README.es.md)

<h1 align="center">omge</h1>

<p align="center"><strong>oh-my-graph-engineering</strong>：把 Claude Code 的工作作为带校验的图来运行，让每个任务都改进下一个任务。</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/version-0.1.1-informational?style=flat-square" alt="Version 0.1.1">
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-d97757?style=flat-square" alt="Claude Code plugin">
</p>

![omge demo](docs/demo.gif)

**用自然语言定义你的工作流。**

> 帮我做一个图：plan → implement → review → tests。写测试之前先做 review，review 由我自己配置好的评审智能体完成，并且该智能体还必须运行 ponytail 的 review 技能。review 失败时回到 plan，而不是 implement。以后相关任务都套用这个图。

它会变成这个图：

```yaml
params:
  review: '^/[A-Za-z0-9._/-]+\.md$'
nodes:
  - id: plan
    do: Write the plan to PLAN.md.
    check: test -n "$(find PLAN.md -newermt "$WF_READY_SINCE")"
    on_fail: retry
  - id: implement
    after: [plan]
    do: Implement the plan and commit.
    check: test -n "$(git log --since="$WF_READY_SINCE")" && [ -z "$(git status --porcelain)" ]
    on_fail: retry
  - id: review
    after: [implement]
    agent: my-reviewer  # your own reviewer agent (~/.claude/agents/my-reviewer.md)
    skill: ponytail:ponytail-review
    do: >-
      Launch my-reviewer on HEAD and invoke ponytail:ponytail-review. The review goes to a file
      outside the repo ending in `Verdict: pass` and `Reviewed SHA: <sha>`; wf_set review=<absolute path>.
    check: |
      grep -qx 'Verdict: pass' {review} && grep -qx "Reviewed SHA: $(git rev-parse HEAD)" {review}
    on_fail: plan
  - id: tests
    after: [review]
    do: Write the tests and commit.
    check: uv run pytest -q
    on_fail: retry
  - id: retro
    after: [tests]
    do: Call wf_retro() and record a verdict.
```

![plan → implement → review → tests → retro 工作流，review 失败时回到 plan](docs/workflow.svg)

`agent:` 和 `skill:` 是两道独立的关卡：review 必须既启动了你自己的评审智能体（my-reviewer）、又调用了 ponytail:ponytail-review 才能关闭；plan 和 implement 只接受节点就绪之后完成的工作，review 只接受针对当前 HEAD 的评审。

“以后”由 retro 实现：retro 把这个图作为新类型（`new_type`，附一句 `match`）存入库中，之后与这句话匹配的请求都从这个图开始。

更长的流程也是同样的写法：

> 帮我做一个图：investigate → plan → implement，然后并行做 unit tests 和独立 review。测试失败回到 implement，review 失败回到 plan。两者都通过后跑 integration 测试，然后在 retro 之前等待我明确批准发布。

它会变成这个图：

```yaml
params:
  review: '^/[A-Za-z0-9._/-]+\.md$'
nodes:
  - id: investigate
    agent: my-researcher  # your own research agent that can write files (~/.claude/agents/my-researcher.md)
    do: 启动 my-researcher 梳理受影响的代码，把附出处的发现写入 FINDINGS.md。
    check: test -n "$(find FINDINGS.md -newermt "$WF_READY_SINCE")"
    on_fail: retry
  - id: plan
    after: [investigate]
    do: 根据 FINDINGS.md 把计划写入 PLAN.md。
    check: test -n "$(find PLAN.md -newermt "$WF_READY_SINCE")"
    on_fail: retry
  - id: implement
    after: [plan]
    do: 实现计划并提交。
    check: test -n "$(git log --since="$WF_READY_SINCE")" && [ -z "$(git status --porcelain)" ]
    on_fail: retry
  - id: unit_tests
    after: [implement]
    do: 运行 unit 测试。
    check: uv run pytest -q tests/unit
    on_fail: implement
  - id: review
    after: [implement]
    agent: my-reviewer
    do: >-
      对 HEAD 启动 my-reviewer。评审写入仓库外的文件，以
      `Verdict: pass` 和 `Reviewed SHA: <sha>` 结尾；wf_set review=<绝对路径>。
    check: |
      grep -qx 'Verdict: pass' {review} && grep -qx "Reviewed SHA: $(git rev-parse HEAD)" {review}
    on_fail: plan
  - id: integration
    after: [unit_tests, review]
    do: 运行 integration 测试。
    check: uv run pytest -q tests/integration
    on_fail: implement
  - id: release
    after: [integration]
    kind: human
    do: 请我批准发布，并把我的回复记录为证据。
  - id: retro
    after: [release]
    do: 调用 wf_retro() 并记录 verdict。
```

`unit_tests` 和 `review` 都只等待 `implement`，因此会同时就绪；`integration` 等待两者。`release` 是没有 check 的 `kind: human` 节点，凭记录的证据（即你的批准）关闭。

**你只需专注于图工程。** 设计步骤和校验，omge 负责强制执行，具体工作由你已有的智能体和技能（superpowers、ponytail、你自己的）完成。

- 节点可以要求任意已安装的智能体（`agent:`）或技能（`skill:`），omge 会拦住节点直到该调用发生。
- 无需编写 YAML：用自然语言告诉 `/omge:graph` 节点该用哪个智能体和技能。
- 详见 [与已安装的插件协同工作](docs/compatibility.zh-CN.md)。

omge 只是一个图工程工具。工作所需的技能和智能体，请按你的任务自行设计，再接入图中使用。

![superpowers 和 ponytail 技能完成 omge 节点](docs/compat-demo.gif)

> 运行时消息为英文（默认）或韩文，通过 `OMGE_LANG` 切换。

## 为什么

长任务容易走偏：测试步骤被跳过，没有评审者的评审被标记为“完成”，会话半途停下。
omge 把任务变成一组节点，每个节点都有一个必须通过才能关闭的 shell `check`。
钩子阻止 Claude 跳过节点或提前停止，任务结束时的复盘（retro）把经验写回可复用的工作流类型库。

## 功能

- **带校验的节点**：带 shell `check` 的节点只有在 check 以 0 退出时才能用 `wf_complete` 关闭；没有 check 的 `kind: human` 节点凭提交的证据关闭。失败后的处理由 `on_fail` 决定。
- **强制执行图的钩子**：SessionStart、UserPromptSubmit、Stop、PreToolUse 和 PostToolUse 钩子阻止跳步和提前停止。
- **复盘驱动的库**：任务末尾运行 `retro` 节点，可以把改进后的图保存为库类型，并用 git 进行版本管理；没有可改进之处时可能被跳过或自动关闭。
- **角色代理**：explorer、implementer、reviewer 和 planner，各自绑定一个可替换的模型别名。
- **内置技能**：`/omge:setup`、`/omge:graph`、`omge:writing-a-plan` 和 `omge:improving-skills`。
- **英文或韩文** 运行时消息（`OMGE_LANG`）。

## 快速开始

在 Claude Code 会话中：

```text
/plugin marketplace add dudals3844/oh-my-graph-engineering
/plugin install omge@oh-my-graph-engineering
/omge:setup
```

如果 `/omge:setup` 尚未出现在列表中，请先运行 `/reload-plugins`。

或者在 shell 中：

```bash
claude plugin marketplace add dudals3844/oh-my-graph-engineering
claude plugin install omge@oh-my-graph-engineering
claude "/omge:setup"
```

`/omge:setup` 会检查是否已安装 [uv](https://pypi.org/project/uv/)
（钩子和 `omge:wf` MCP 服务器都通过 uv 运行），并提供安装可选的 ponytail 插件。

首次使用时，uv 会按已提交的 `uv.lock` 从 PyPI 获取 omge 的 Python 依赖。
如果没有安装 Python 3.12 或更高版本，uv 还会下载一个。
除此之外不会下载任何内容，omge 也不发送任何遥测数据。
恢复已有任务（SessionStart）时，可能会自动重新运行该任务的人工节点检查，这些检查可能会从 GitHub 读取信息（例如 PR 的合并状态）；这属于工作流检查，而不是遥测。

> [!WARNING]
> **重复的钩子：** 插件会注册自己的 SessionStart、UserPromptSubmit、Stop、PreToolUse 和 PostToolUse 钩子。
> 如果你还在 `settings.json` 中配置了 `omge hook ...` 命令，请删除它们，否则每个钩子都会运行两次。
>
> **如果 omge 无法启动**（uv 或 Python 失败），PreToolUse 会放行工具调用，因为阻止会拒绝所有会话（无论有无任务）的每次工具调用；
> Stop 仍会阻止，因此任务不会在钩子损坏时结束。

## 图示例

```yaml
type: lint-fix
match: fix lint errors and commit
optional_issue: true
nodes:
  - id: fix
    do: Fix every error `ruff check .` reports and commit.
    check: uvx ruff check . && [ -z "$(git status --porcelain)" ]
    on_fail: retry
  - id: review
    after: [fix]
    agent: omge:reviewer
    do: Delegate an independent review of HEAD.
    kind: human
  - id: retro
    after: [review]
    do: Call wf_retro() and record a verdict.
```

Claude 用 `wf_start` 开始任务（库类型，或带一次性图的 `type="new"`），用 `wf_complete` 关闭节点，
最后用 `wf_retro` 结束。`/omge:graph` 说明了所有节点字段、params、check 环境变量，
以及一次性图如何成为库类型。

## 内置模板

| 类型 | 用途 |
|---|---|
| `code-implementation` | plan → implement → test → review → push/PR，有无 issue 均可 |
| `investigation` | 收集带出处的证据 → 核实引用 → 报告结论 |
| `issue-resolution` | 一个 GitHub issue 走完 spec、scope review、plan、implement、test、review、PR、CI、merge、cleanup |

## 代理

| 代理 | 模型别名 | 角色 |
|---|---|---|
| `omge:explorer` | `haiku` | 带 `path:line` 引用的只读搜索 |
| `omge:implementer` | `sonnet` | 实现并提交计划中的变更 |
| `omge:reviewer` | `opus` | 以 `Verdict: pass` / `Reviewed SHA: <sha>` 结尾的独立评审 |
| `omge:planner` | `fable` | 编写实现计划 |

要换用其他模型，把 `agents/<name>.md` 复制到 `~/.claude/agents/` 或项目的 `.claude/agents/`，
设置新的 `name` 和另一个 `model:` 别名，然后在节点的 `agent:` 字段中引用它（例如 `agent: reviewer-sonnet`）。

## 技能

| 技能 | 用途 |
|---|---|
| `/omge:setup` | 选择消息语言，检查 uv，提示重复钩子，提供 ponytail |
| `/omge:graph` | 编写图和库类型的参考 |
| `omge:writing-a-plan` | 供 plan 节点使用 |
| `omge:improving-skills` | 根据复盘证据修改 `~/.claude/skills` 中的技能 |

## 配置

| 变量 | 作用 |
|---|---|
| `OMGE_LANG` | 消息语言：`en`（默认）或 `ko`；未设置时从 `LC_ALL`、`LC_MESSAGES` 或 `LANG` 读取区域设置（例如 `ko_KR.UTF-8`）；可通过 `/omge:setup` 设置 |
| `WF_DB` | 任务数据库路径（默认 `~/.local/share/omge/wf.db`） |
| `WF_LIBRARY` | 类型库路径，是一个 git 仓库（默认 `~/.local/share/omge/library/`） |

## 开发

```bash
uv sync --locked --extra dev && uv run pytest -q
```

## 许可证

MIT，见 [LICENSE](LICENSE)。
