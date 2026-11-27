---
name: graph
description: Use when the user runs /omge:graph or asks how to design, write or change an omge workflow graph (an omge:wf type, a one-off graph for wf_start type="new", or a library type changed through wf_retro).
---

# Designing an omge graph

Answer in the user's language. A graph is a list of nodes whose checks must pass; the hooks keep the
session from skipping them. Shipped templates (`src/omge/workflows/*.yaml` in the plugin) are good models:
`code-implementation`, `investigation`, `issue-resolution`.

## Shape

```yaml
type: lint-fix                 # kebab-case name
match: fix lint errors and commit   # one sentence; wf_start/wf_list_types match requests against it
optional_issue: true           # optional bool: the type may run with no GitHub issue
params:                        # name: regex; a value is validated against it when set
  review: '^/[A-Za-z0-9._/-]+\.md$'
nodes:
  - id: fix
    do: Fix every error `ruff check .` reports and commit.
    check: uvx ruff check . && [ -z "$(git status --porcelain)" ]
    on_fail: retry
  - id: review
    after: [fix]
    agent: omge:reviewer
    agent_mode: require
    carry: code
    on_fail: fix
    do: >-
      Delegate an independent review of HEAD. It writes the review to a file outside the repo, ending
      with `Verdict: pass` and `Reviewed SHA: <sha>`. wf_set review=<absolute path>.
    check: |
      grep -qx 'Verdict: pass' {review} && grep -qx "Reviewed SHA: $(git rev-parse HEAD)" {review}
  - id: retro
    after: [review]
    do: Call wf_retro(), then record wf_retro(verdict=...) (verdict="skip" if nothing to improve).
```

A `retro` node is required and must be last (only `kind: human` nodes may follow it); it takes no
`check` — omge:wf supplies it. In a graph given to wf_start it is appended for you.

## Node fields

- `id`: lowercase letters and `_`, unique.
- `do`: what to do, shown when the node is ready. Every param a check or `when` reads must be named in
  this node's or an earlier node's `do` (say what to wf_set and in what form).
- `check`: bash, run in the task dir; exit 0 closes the node. A check that cannot fail (`true`, `:`,
  `exit 0`, a bare `echo`) is refused. Exit 75 means "still waiting on something external" (CI
  running), not a failure. A node without `check` must be `kind: human`.
- `on_fail`: required on a checked node — `retry` to stay, or an earlier ancestor node id to redo from
  there (wf_fail). `on_fail_max` (default 3) bounds the loop; past it the task stalls.
- `after`: earlier node ids only (file order is the execution order).
- `kind: human`: closes on non-empty evidence (or its check), for waiting on a person or something
  external, e.g. a merge.
- `agent`: a subagent_type (or a list, any one will do) that must be launched with the Agent tool while
  the node is ready. `agent_mode: require` (default) also blocks the main session's writes while the node
  is ready; `recommend` only says so.
- `skill`: a skill name (or a list) that must be invoked while the node is ready.
- `carry: code`: on a checked node, a pass carries over to a later commit whose code is unchanged (same
  AST).
- `next`: a decision — a list of `{to, when, label, max}`. `when` is bash; the first matching edge wins;
  an edge without `when` is the else. An edge back must go to an ancestor; `max` (default 1) bounds how
  often it is taken.
- `protected: true`: an independent-review gate. `wf_waive` (and the user's `wf waive`) needs a reviewer's
  `Verdict: pass` whose `Reviewed SHA` is the current HEAD, recorded since the node became ready.
- `floor: true`: a measurement/evidence gate; implies `protected`, needs a `check` and no `carry`. It is
  never waived. No node after it closes (a waive included) unless it is done and its check passes when
  run again: a failing one reopens with everything after it. A floor a route skipped blocks everything
  after it, so place a floor on every path that reaches what it guards.
- `timeout`: check timeout in seconds (default 120).
- `pitfalls`: a list of short warnings shown with `do`.

`agent`, `skill`, `carry`, `protected` and `floor` cannot go on `retro`.

A running task keeps its gates as started: `wf_revise` cannot delete a `protected`/`floor` node, change
any of its fields (`do`, `check`, `after`, `next`, gates, the flags themselves), or detach a node after
it. It may add a gate. Dropping or loosening one is a change to the type (`update_type` with a reason,
`new_type`) or a new one-off graph, and takes effect for the next task.

## Params and quoting

- `{name}` in a check or `when` is replaced by `${WF_P_name+"${WF_P_name}"}`, and the param's regex-checked value
  is passed in that env variable, so it is never parsed as shell source: write it bare (`f={review}`), in
  `"..."` or in `$(...)`. Do not put a placeholder inside `'...'`: it stays literal text there.
  `${name}` is bash and is left alone.
- Exception: bash itself evaluates a value in arithmetic (`$(( ))`, `(( ))`, `let`, `[[ ... -eq ... ]]`
  and the other `[[ ]]` numeric operators, array subscripts) and in `eval`/`bash -c`, so
  `a[$(cmd)]` runs `cmd` there. A param used as a number needs a numeric regex (`^[0-9]+$`). A param
  meant to be a command (`test_cmd`) is code by design: set it only from the graph author or user,
  never from outside text (issue, PR, web page).
- A param whose regex accepts `""` is optional; any other must be set (wf_set, or `params` on
  wf_complete) before its check runs.
- A literal `{x}` that is not a param must be escaped: git's `@{u}` is written `@\{u\}`.
- Never hard-code this task's issue number, branch or path; use a param or the env below.

## Check environment

| Variable | Value |
|---|---|
| `WF_ISSUE`, `WF_ISSUE_NUMBER` | issue URL and number (empty with no issue) |
| `WF_BRANCH` | the task dir's branch |
| `WF_DIR` | the task dir |
| `WF_SINCE` | task start, GitHub `created_at` format |
| `WF_READY_SINCE` | when this node became ready (count only comments made after it) |
| `WF_SKILL_AT` | on a `skill:` node, when the skill was invoked |
| `WF_PR` | the `pr` param, when declared and set to a number |
| `WF_TASK_ID` | the task id |

## One-off graph: wf_start type="new"

When no library type fits, pass the graph as JSON (or a YAML string):

```
wf_start(type="new", dir=<repo>, title=<request in one line>,
         graph={"params": {...}, "nodes": [{"id": "fix", "do": "...", "check": "...", "on_fail": "retry"}]})
```

It is refused when a similar library type exists unless `new_reason` says why that type does not fit.
At the end, the retro saves it to the library: `wf_retro()` returns the evidence and a file for an
independent reviewer, the reviewer submits `structural_review`, then the parent records
`wf_retro(verdict="new_type", name=<kebab-case>, match=<sentence>, review_id=...)`. `no_change` is refused
for a `new` graph: save it as a new type (or fold it into an existing one with `update_type`).

## Changing a library type

- During a task: `wf_revise(graph={"nodes": {<id>: <fields> | null}}, reason=...)` changes only the running
  task (null deletes a node; a new id goes before `retro`).
- For every later task: the retro's `update_type` verdict saves the reviewed graph as the type's new
  default. A weakened check needs a `reason`. Library files live in `~/.local/share/omge/library`
  (`WF_LIBRARY` overrides it) and every save is a git commit there.

## Agents and model aliases

The plugin ships `omge:explorer` (haiku, read-only), `omge:implementer` (sonnet), `omge:reviewer`
(opus) and `omge:planner` (fable). To run a node on another model, copy the agent file into
`~/.claude/agents/` (user) or `.claude/agents/` (project) under a new `name`, change its `model:` line, and
name that agent in the graph:

```yaml
# ~/.claude/agents/reviewer-sonnet.md: frontmatter name: reviewer-sonnet, model: sonnet
  - id: review
    agent: reviewer-sonnet          # a copied agent has no omge: prefix
```

`agent` matches the Agent call's `subagent_type` exactly; a list accepts any of them
(`agent: [omge:reviewer, reviewer-sonnet]`).
