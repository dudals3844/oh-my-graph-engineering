[English](compatibility.md) | [Korean](compatibility.ko.md) | [Japanese](compatibility.ja.md) | [Chinese](compatibility.zh-CN.md) | [Spanish](compatibility.es.md)

# 手持ちのプラグインと連携

omge はグラフエンジニアリングのツールにすぎません。必要なスキルとエージェントは作業に合わせて自分で設計し、グラフに組み込んでください。

omge はエージェントやスキルを置き換えません。omge が決めるのは**どのステップをいつ実行し、何で完了を
証明するか**で、作業はすでに持っているエージェントとスキルが行います。あなたはグラフを設計するだけです。

![superpowers と ponytail のスキルが omge のノードを閉じる様子](compat-demo.gif)

## YAML は不要

`/omge:graph` でグラフを設計するときに、普通の言葉で頼みます。

> implement ノードは superpowers:test-driven-development を使って omge:implementer で実行して

Claude がノードに `agent:` と `skill:` を書き込みます。

## 例

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

上のスキル名は例です。インストールしたプラグインが示す正確な名前を使ってください。

## Ponytail

`/omge:setup` がインストールを提案します。インストール後は「コードのノードにはインストール済みの ponytail
スキルを使って」と頼むだけです（または該当ノードに `skill: ponytail:ponytail` を書きます）。追加の設定はありません。

superpowers と ponytail は別途インストールするサードパーティのプラグインで、omge には同梱されていません。
