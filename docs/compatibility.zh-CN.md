[English](compatibility.md) | [Korean](compatibility.ko.md) | [Japanese](compatibility.ja.md) | [Chinese](compatibility.zh-CN.md) | [Spanish](compatibility.es.md)

# 与已安装的插件协同工作

omge 不替代你的智能体或技能。omge 决定**哪一步何时运行、用什么证明它已完成**，具体工作由你已有的
智能体和技能完成。你只需设计图。

## 工作原理

- 节点可以要求任意已安装的智能体（`agent:`）和/或技能（`skill:`）。
- 智能体强制是默认行为（`agent_mode: require`）；`agent_mode: recommend` 只做建议。
- omge:wf 会拦住该节点，直到节点就绪期间发生了对应的 Agent 启动或 Skill 调用。在此之前的 Skill 调用会被拒绝，不计数。
- 在子智能体中发起的 Skill 调用同样计数：它以父会话的身份到达。
- `agent:` 和 `skill:` 也接受列表，满足其中任意一个即可。
- 名称必须完全一致。请使用 `/skills` 列表（或插件）显示的 `plugin:skill` 名称。

## 无需编写 YAML

用 `/omge:graph` 设计图时，直接用自然语言提出：

> implement 节点用 omge:implementer 运行，并使用 superpowers:test-driven-development

Claude 会把 `agent:` 和 `skill:` 写进节点。

## 示例

```yaml
nodes:
  - id: investigate
    skill: superpowers:systematic-debugging
    do: Find the root cause of the failing test and write it to notes.md.
    check: test -s notes.md
    on_fail: retry
  - id: implement
    after: [investigate]
    agent: omge:implementer
    skill: superpowers:test-driven-development
    do: Write a failing test, make it pass, and commit.
    check: uv run pytest -q && [ -z "$(git status --porcelain)" ]
    on_fail: retry
  - id: simplify
    after: [implement]
    skill: ponytail:ponytail
    do: Cut what the change does not need, and commit.
    check: uv run pytest -q && [ -z "$(git status --porcelain)" ]
    on_fail: retry
  - id: retro
    after: [simplify]
    do: Call wf_retro() and record a verdict.
```

上面的技能名称只是示例，请使用你已安装的插件所列出的准确名称。

## Ponytail

`/omge:setup` 会提议安装它。安装后，只需说“代码节点使用已安装的 ponytail 技能”（或在这些节点上写
`skill: ponytail:ponytail`）。无需额外配置。

superpowers 和 ponytail 是需要单独安装的第三方插件，omge 不包含它们。
