[English](compatibility.md) | [한국어](compatibility.ko.md) | [日本語](compatibility.ja.md) | [简体中文](compatibility.zh-CN.md) | [Español](compatibility.es.md)

# 与已安装的插件协同工作

omge 只是一个图工程工具。工作所需的技能和智能体，请按你的任务自行设计，再接入图中使用。

omge 不替代你的智能体或技能。omge 决定**哪一步何时运行、用什么证明它已完成**，具体工作由你已有的
智能体和技能完成。你只需设计图。

![superpowers 和 ponytail 技能完成 omge 节点](compat-demo.gif)

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
