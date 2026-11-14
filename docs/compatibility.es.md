[English](compatibility.md) | [Korean](compatibility.ko.md) | [Japanese](compatibility.ja.md) | [Chinese](compatibility.zh-CN.md) | [Spanish](compatibility.es.md)

# Funciona con tus plugins

omge no reemplaza tus agentes ni tus skills. Decide **qué paso se ejecuta y cuándo, y qué demuestra que está
hecho**; el trabajo lo hacen los agentes y skills que ya tienes. Tú solo diseñas el grafo.

## Cómo funciona

- Un nodo puede exigir cualquier agente (`agent:`) y/o skill (`skill:`) instalado.
- Exigir el agente es lo predeterminado (`agent_mode: require`); `agent_mode: recommend` solo lo sugiere.
- omge:wf bloquea el nodo hasta que ese lanzamiento de Agent o esa llamada a Skill ocurra mientras el nodo
  está listo. Una llamada a Skill anterior se rechaza y no cuenta.
- Una llamada a Skill hecha dentro de un subagente también cuenta: llega con la sesión del padre.
- `agent:` y `skill:` aceptan una lista; cualquiera de sus elementos la satisface.
- Los nombres deben coincidir exactamente. Usa el nombre `plugin:skill` que muestra tu lista de `/skills` (o el plugin).

## Sin escribir YAML

Al diseñar un grafo con `/omge:graph`, pídelo con palabras normales:

> ejecuta el nodo implement con omge:implementer usando superpowers:test-driven-development

Claude escribe `agent:` y `skill:` en el nodo.

## Ejemplo

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

Los nombres de skills de arriba son ejemplos; usa los nombres exactos que listan tus plugins instalados.

## Ponytail

`/omge:setup` ofrece instalarlo. Una vez instalado, basta con pedir "usa el skill ponytail instalado en los
nodos de código" (o poner `skill: ponytail:ponytail` en ellos). Sin configuración extra.

superpowers y ponytail son plugins de terceros que se instalan por separado; omge no los incluye.
