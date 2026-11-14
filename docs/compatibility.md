[English](compatibility.md) | [Korean](compatibility.ko.md) | [Japanese](compatibility.ja.md) | [Chinese](compatibility.zh-CN.md) | [Spanish](compatibility.es.md)

# Works with your plugins

omge is just a graph engineering tool. Design the skills and agents your work needs yourself and plug them into the graph.

omge does not replace your agents or skills. It decides **which step runs when and what proves it is
done**; the agents and skills you already have do the work. You only design the graph.

![superpowers and ponytail skills closing omge nodes](compat-demo.gif)

## No YAML needed

When designing a graph with `/omge:graph`, ask in plain words:

> run the implement node with omge:implementer using superpowers:test-driven-development

Claude writes `agent:` and `skill:` into the node.

## Example

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

The skill names above are examples; use the exact names your installed plugins list.

## Ponytail

`/omge:setup` offers to install it. Once installed, just ask "use the installed ponytail skill for
code nodes" (or put `skill: ponytail:ponytail` on them). No extra configuration.

superpowers and ponytail are third-party plugins, installed separately; omge does not bundle them.
