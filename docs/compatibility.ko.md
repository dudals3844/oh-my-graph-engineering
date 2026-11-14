[English](compatibility.md) | [한국어](compatibility.ko.md) | [日本語](compatibility.ja.md) | [简体中文](compatibility.zh-CN.md) | [Español](compatibility.es.md)

# 설치한 플러그인과 함께 동작

omge는 그래프 엔지니어링 도구일 뿐입니다. 필요한 스킬과 에이전트는 사용자 작업에 맞게 직접 설계해서 사용하세요.

omge는 에이전트나 스킬을 대체하지 않습니다. omge는 **어떤 단계를 언제 실행하고 무엇으로 완료를
증명하는지**를 정하고, 일은 이미 가진 에이전트와 스킬이 합니다. 사용자는 그래프만 설계하면 됩니다.

![superpowers와 ponytail 스킬이 omge 노드를 닫는 모습](compat-demo.gif)

## YAML을 쓸 필요 없음

`/omge:graph`로 그래프를 설계할 때 평범한 말로 요청하세요.

> implement 노드는 superpowers:test-driven-development를 써서 omge:implementer로 실행해 줘

Claude가 노드에 `agent:`와 `skill:`을 적습니다.

## 예시

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

위 스킬 이름은 예시입니다. 설치한 플러그인이 보여 주는 정확한 이름을 쓰세요.

## Ponytail

`/omge:setup`이 설치를 제안합니다. 설치한 뒤에는 "코드 노드에는 설치된 ponytail 스킬을 써 줘"라고
요청하기만 하면 됩니다(또는 해당 노드에 `skill: ponytail:ponytail`을 넣으세요). 추가 설정은 없습니다.

superpowers와 ponytail은 따로 설치하는 서드파티 플러그인이며, omge에 포함되어 있지 않습니다.
