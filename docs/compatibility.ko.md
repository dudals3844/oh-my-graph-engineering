[English](compatibility.md) | [Korean](compatibility.ko.md) | [Japanese](compatibility.ja.md) | [Chinese](compatibility.zh-CN.md) | [Spanish](compatibility.es.md)

# 설치한 플러그인과 함께 동작

omge는 에이전트나 스킬을 대체하지 않습니다. omge는 **어떤 단계를 언제 실행하고 무엇으로 완료를
증명하는지**를 정하고, 일은 이미 가진 에이전트와 스킬이 합니다. 사용자는 그래프만 설계하면 됩니다.

## 동작 방식

- 노드는 설치된 어떤 에이전트(`agent:`)나 스킬(`skill:`)이든, 또는 둘 다 요구할 수 있습니다.
- 에이전트 강제는 기본값(`agent_mode: require`)입니다. `agent_mode: recommend`는 권장만 합니다.
- omge:wf는 노드가 준비된 동안 그 Agent 실행이나 Skill 호출이 일어날 때까지 노드를 막습니다. 그보다
  먼저 한 Skill 호출은 거부되며 인정되지 않습니다.
- 서브에이전트 안에서 한 Skill 호출도 인정됩니다. 부모 세션으로 들어오기 때문입니다.
- `agent:`와 `skill:`은 목록도 받습니다. 그중 하나면 충족됩니다.
- 이름은 정확히 일치해야 합니다. `/skills` 목록(또는 플러그인)이 보여 주는 `plugin:skill` 이름을 쓰세요.

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
