"""`omge-mcp`: the agent-facing MCP surface over `graph`/`checks`. Deliberately thin.

The only module here on mcp 2.x; everything it calls is stdlib + pyyaml so the
hooks never pay for `mcp` on a tool call. Registered user-scope, stdio.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import traceback
from contextlib import closing
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import checks, i18n, library, store
from . import graph as graph_

mcp = MCPServer("wf")

#: wf_retro evidence past this goes to a file beside the structural context
#: and the reply keeps the head plus the path.
RETRO_EVIDENCE_MAX = 8000
# Recorded verdicts are improving-skills' trigger (`server.skills_nudge`) — without that
# line agents never make the pass. The engine's own skip (checks._retro_idle) records no
# verdict, so no nudge there.


def _tool(fn):
    """Register `fn`, keeping workflow refusals and unexpected failures visible to the agent."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except graph_.WfError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            traceback.print_exc()
            error_name = getattr(exc, "sqlite_errorname", None)
            suffix = f" [{error_name}]" if error_name else ""
            raise ToolError(f"{type(exc).__name__}: {exc}{suffix}") from exc

    return mcp.tool()(wrapper)


def _task(conn: sqlite3.Connection, issue_url: str | None, task: int | None) -> sqlite3.Row:
    """Every tool names its task by `issue_url` or, for any task, its id `task`."""
    if bool(issue_url) == (task is not None):
        raise graph_.WfError(i18n.t("server.one_of"))
    row = store.find(conn, issue_url, task)
    if row is not None:
        return row
    # A finished task still exists: say so instead of "no task".
    closed = (conn.execute("SELECT * FROM task WHERE issue_url = ? ORDER BY id DESC",
                           (issue_url,)).fetchone() if issue_url else store.get_task(conn, task))
    if closed is None:
        raise graph_.WfError(i18n.t("server.no_open_task"))
    retro = store.node_rows(conn, closed["id"]).get("retro")
    raise graph_.WfError(i18n.t(
        "server.closed", id=closed['id'], state=closed['state'],
        retro=(f"{retro['status']} {retro['evidence'] or ''}".strip() if retro
               else i18n.t("server.no_node"))))


@_tool
def wf_list_types(query: str = "") -> str:
    """Library types and their `match` sentences: the brief catalog, or — with a `query`
    that a type name or `match` mentions — the matching types' full lines and past titles."""
    with closing(store.connect()) as conn:
        return library.listing(conn, brief=not query, query=query)


@_tool
def wf_start(type: str, dir: str, issue_url: str | None = None, title: str | None = None,
             params: dict[str, str] | None = None, graph: dict | list | str | None = None,
             new_reason: str | None = None) -> str:
    """Start a task; `dir` is where checks run. Every non-question piece of work is a task:
    with no issue, omit `issue_url` and give `title` (the user's request in a line) — the
    returned `task #<id>` is then how every other wf tool names it. When no library type fits,
    `type="new"` with `graph`, a JSON object {nodes: [{id, do, check, on_fail, after, kind,
    timeout, skill, agent, agent_mode, carry, next: [{to, when, label, max}]}],
    params: {name: regex}}; `retro` is appended. `carry: code` re-passes a checked node while
    the code is unchanged (same AST) since it last passed. `agent` (a subagent_type, or a
    list) with `agent_mode: require` (default) closes the node only after that Agent call
    and blocks the main session's writes while it is ready; `recommend` only says so.
    Every node with a check needs `on_fail`: the earlier node to redo from when it fails
    (`wf_fail`), or `retry` to stay; `on_fail_max` (default 3) bounds the loop. Checks and
    `when`s run under bash in `dir` with `{param}` substituted and env WF_ISSUE,
    WF_ISSUE_NUMBER (both empty with no issue), WF_BRANCH, WF_DIR, WF_SINCE — use those,
    never this task's literal issue number, branch or path. A `new` graph's merge
    check must not delete the worktree or `dir` before it runs; if something else
    moved the worktree, `wf_set` a valid repo dir before that check. It matches
    the PR by the declared `pr` param, `{pr}`, or `WF_PR`, not by the checkout
    branch. A check that cannot fail
    (`true`) is refused. `new` is refused when a similar type exists unless `new_reason`
    says why not. The user already sees the drawn graph; do not repeat it. A library type is a starting point, not the user's plan: before the
    first node, every step the user asked for (a deploy, a runtime/e2e test, ...) must be
    some node's check, else add it with wf_revise — another node's `do` text is not one.
    When the user changes the request, wf_revise before the next node: pass only the
    nodes that change (`graph.nodes` as {id: fields}) and `title` if the summary changed."""
    with closing(store.connect()) as conn:
        tid = graph_.start(conn, type, dir, issue_url, params or {}, graph, new_reason, title)
        # `server.coverage` goes on wf_start only: the moment a template stands in for the request.
        return (graph_.status_text(conn, store.get_task(conn, tid), drawn=False) + "\n"
                + i18n.t("server.coverage"))


@_tool
def wf_attach(issue_url: str | None = None, task: int | None = None) -> str:
    """Take over an open task whose owner session went away. The user already sees the
    drawn graph; do not repeat it."""
    with closing(store.connect()) as conn:
        return graph_.status_text(conn, _task(conn, issue_url, task), drawn=False)


@_tool
def wf_status(issue_url: str | None = None, task: int | None = None) -> str:
    """The task graph, ready nodes with their `do`/`pitfalls`, and the last failure output.
    The user already sees the drawn graph; do not repeat it."""
    with closing(store.connect()) as conn:
        return graph_.status_text(conn, _task(conn, issue_url, task), drawn=False)


@_tool
def wf_set(key: str, value: str, issue_url: str | None = None, task: int | None = None) -> str:
    """Set a graph param (validated against its regex), or `dir` to move the task.
    To set a param a check needs, pass `params` to wf_complete instead of a separate call.
    Call it in the same turn as your next work tool call, not in a turn of its own."""
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        graph_.set_param(conn, row, key, value)
        return graph_.status_text(conn, store.get_task(conn, row["id"]), drawn=False)


@_tool
def wf_complete(node: str | list[str], evidence: str, issue_url: str | None = None,
                task: int | None = None, params: dict[str, str] | None = None) -> str:
    """Close a ready node. One with a check is done only if the check exits 0; one without
    (a `kind: human` node — it may wait on something external) is done on your non-empty
    `evidence`. A decision node also needs one `next.when` to match.
    `params` are set first so no separate wf_set turn is needed. A list of nodes closes
    in order and stops at the first that does not close. Call it in the same turn as
    your next work tool call, not in a turn of its own — a wf-only turn rereads the
    whole context."""
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        for key, value in (params or {}).items():
            graph_.set_param(conn, store.get_task(conn, row["id"]), key, value)
        results = []
        for nid in [node] if isinstance(node, str) else node:
            try:
                results.append(
                    checks.complete(conn, store.get_task(conn, row["id"]), nid, evidence))
            except graph_.WfError as exc:
                if not results:
                    raise
                raise graph_.WfError("\n".join([*results, str(exc)])) from exc
        return (graph_.status_text(conn, store.get_task(conn, row["id"]), drawn=False)
                + "\n" + "\n".join(results))


@_tool
def wf_fail(node: str, reason: str, issue_url: str | None = None, task: int | None = None
            ) -> str:
    """Declare a ready node failed — its check failed, or a reviewer found blockers — and
    take its `on_fail`: `retry` records the failure and stays; an earlier node reopens that
    node and everything after it. Past `on_fail_max` the task stalls: tell the user."""
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        result = checks.fail(conn, row, node, reason)
        return (graph_.status_text(conn, store.get_task(conn, row["id"]), drawn=False)
                + "\n" + result)


@_tool
def wf_revise(graph: dict | list | str, reason: str, issue_url: str | None = None,
              task: int | None = None, title: str | None = None) -> str:
    """Replace the running graph when the real path or the user's request changed;
    every change is a deviation recorded with `reason`. `graph.nodes` may be
    {id: fields|null} to change only those nodes (null deletes; a new id is added;
    omitted nodes, fields and params stay) — send the full list only for a rewrite.
    A pending node with a check cannot be removed — waive it with wf_waive. A node
    already done is redone when its check, when, on_fail or kind changes. `title`
    replaces the one-line summary. The reply names the change and the ready nodes."""
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        graph_.revise(conn, row, graph, reason, title)
        return graph_.revise_text(conn, row)


@_tool
def wf_retro(issue_url: str | None = None, task: int | None = None,
             verdict: str | None = None, deviations: str = "",
             name: str | None = None, match: str | None = None,
             graph: dict | list | str | None = None,
             reason: str | None = None, applied: list[str] | None = None,
             structural_review: dict | None = None, review_id: str | None = None,
             dismissed: dict[str, str] | None = None) -> str:
    """Read first, then delegate an independent reviewer for order/duplication/missing/rework.
    The reviewer submits structural_review here with reuse_graph (the selected complete graph,
    "default" to keep the type's default, or {"base": "default", "nodes": {id: fields|null},
    "params": {name: regex|null}} to change only those of it) and adopted finding IDs; its
    successful PostToolUse hook records a snapshot-bound receipt. The review input is written
    to a file.
    Parent finalization must use that exact graph. Adopted improvements cannot be no_change;
    safe update_type saves the default immediately and retires stale candidates so every next
    matching task reuses it. No-op edits are refused. no_change requires the same reviewed
    default and structural grounds; rejected findings need dismissed {finding_id: reason}, and
    a protected change (a params.* or gate the task added) no_change would drop needs a review
    finding that names it, dismissed with a reason.
    Reviewed update_type/new_type save gate changes immediately; a weakening needs a reason
    recorded in the library commit. new_type needs name and match
    and refuses similar types. Past completed retros and legacy score analysis remain readable.
    verdict="skip" with a reason closes retro without a review when there is nothing to
    improve; only retro can be skipped this way.
    """
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        if verdict == "skip":
            checks.skip_retro(conn, row, reason or "")
            return (i18n.t("server.retro_skipped", id=row['id'], reason=(reason or '').strip())
                    + i18n.t("server.skills_nudge"))
        if structural_review is not None:
            if verdict is not None:
                raise graph_.WfError("review submission and final verdict are separate calls")
            return json.dumps(graph_.structural_ack(conn, row, structural_review),
                              ensure_ascii=False)
        evidence = library.evidence(conn, row["type"], row)
        if len(evidence) > RETRO_EVIDENCE_MAX:
            # Same dir as the structural context file: full text on disk, the
            # reply keeps the head and names it.
            path = Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "retro" / (
                f"{row['id']}-evidence.txt")
            store.write_private(path, evidence)
            evidence = evidence[:RETRO_EVIDENCE_MAX] + i18n.t("server.evidence_full", path=path)
        if verdict is None:
            # The input is ~60KB the parent only passes on: a file, not this reply.
            context = graph_.structural_context(conn, row)
            path = Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "retro" / (
                f"{row['id']}-{context['snapshot'][:12]}.json")
            store.write_private(path, json.dumps(context, ensure_ascii=False))
            return evidence + i18n.t("server.review_prompt", path=path, id=row['id'],
                                     snapshot=context['snapshot'])
        if not review_id:
            raise graph_.WfError("successful independent structural_review hook receipt required")
        return (evidence + "\n\n" + graph_.record_retro(
            conn, row, verdict, deviations, name, match, graph, reason, applied, review_id, dismissed)
            + i18n.t("server.skills_nudge"))


@_tool
def wf_waive(node: str, reason: str, issue_url: str | None = None, task: int | None = None) -> str:
    """Waive a pending node. `reason` is required. A decision node names its route as
    `node>route`. Succeeds only after an independent reviewer (any subagent launched with
    the Agent tool, not a fork) was called since the node became ready. A `protected` node
    also needs that reviewer's pass for the current HEAD; a `floor` node is never waived."""
    if not reason.strip():
        raise graph_.WfError(i18n.t("server.waive_reason"))
    node_id, _, route = node.partition(">")
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        t = graph_.parse(row["graph"])
        target = next((n for n in t["nodes"] if n["id"] == node_id), None)
        if target is None or store.node_rows(conn, row["id"])[node_id]["status"] != "pending":
            raise graph_.WfError(i18n.t("server.not_pending", node=node_id))
        if not conn.execute(
                "SELECT 1 FROM event WHERE task = ? AND node = ? AND kind = 'reviewer' AND ts >= ?",
                (row["id"], node_id, graph_.ready_since(conn, row, t, node_id))).fetchone():
            raise graph_.WfError(i18n.t("server.no_reviewer"))
        checks.close(conn, row, node_id, status="waived", done_by="agent", evidence=reason.strip(),
                     route=route or None)
        return (graph_.status_text(conn, store.get_task(conn, row["id"]), drawn=False)
                + f"\n`{node_id}` waived (agent): {reason.strip()}")


@_tool
def wf_drop(task: int, reason: str) -> str:
    """Drop any open task, the caller's own or an orphan. `reason` is required."""
    if not reason.strip():
        raise graph_.WfError(i18n.t("server.drop_reason"))
    with closing(store.connect()) as conn:
        row = store.get_task(conn, task)
        if row is None or row["state"] not in store.OPEN_STATES:
            raise graph_.WfError(i18n.t("server.not_open", task=task))
        store.set_state(conn, row["id"], "dropped")
        store.event(conn, row["id"], "", "drop", reason.strip())
        return f"omge:wf #{row['id']}: dropped — {reason.strip()}"


@_tool
def wf_pause(issue_url: str | None = None, task: int | None = None) -> str:
    """Pause the task. The next ordinary user prompt makes it active again."""
    with closing(store.connect()) as conn:
        row = _task(conn, issue_url, task)
        store.set_state(conn, row["id"], "paused")
        store.set_stop_streak(conn, f"task:{row['id']}", 0, "")
        return i18n.t("server.paused", id=row['id'])


@_tool
def wf_type_stats(type: str) -> str:
    """A type's history: conformance, per-node failures/waivers/skips/loops, suggestions,
    and the template versions by task score — current and previous."""
    with closing(store.connect()) as conn:
        return library.evidence(conn, type)


def main() -> None:
    mcp.run()
