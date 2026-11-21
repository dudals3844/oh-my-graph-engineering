"""Graph parsing/validation, ready nodes, rendering, and the task lifecycle.

A graph is validated at load, not at first use: a task copies the body it
started from, so a malformed graph caught only mid-task would strand the task
on something nobody can complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import textwrap
import unicodedata
from collections import Counter
from copy import deepcopy
from pathlib import Path

import yaml

from . import i18n, library, store

#: `{name}` in a check is a param. `${name}` is bash and is left alone.
PLACEHOLDER = re.compile(r"(?<!\$)\{([a-z_][a-z0-9_]*)\}")

#: The retro node's check, owned here and injected by `parse` — graphs leave
#: `retro` without one. `wf_retro` must also have been recorded (enforced in
#: `checks.attempt`, which is the only thing that can read the DB). Only a
#: comment made after this task started counts: two tasks on one issue must
#: each write their own. A task with no issue has nowhere to comment: the
#: `wf_retro` record alone closes it (also `checks.attempt`).
RETRO_CHECK = (
    # Unset, jq's `.created_at >= null` is true and every old comment counts.
    "[ -n \"$WF_SINCE\" ] && gh api --paginate \"repos/${WF_ISSUE#https://github.com/}/comments\""
    " --jq '.[] | select((.body | startswith(\"wf retro\")) and .created_at >= env.WF_SINCE)"
    " | .id' | grep -q ."
)
ISSUE_URL = re.compile(r"https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/issues/[0-9]+")
VERDICTS = ("no_change", "update_type", "new_type")
#: What a retro writes when the run followed the graph exactly.
NO_DEVIATION = re.compile(r"\s*(none|no deviations?|n/?a)[.\s]*", re.I)
SYMBOL = {"done": "✔", "waived": "✔", "skipped": "⊘"}
WIDTH = 100
#: A drawn graph goes into a terminal and a systemMessage: past these it breaks
#: there, so `bounded` draws only the part around the current nodes.
MAX_LINES = 40
MAX_CHARS = 2000
#: `on_fail` absent in a SUBMITTED graph is refused; one stored before on_fail was required reads as this.
RETRY = "retry"
#: `agent_mode`: a `require` node is a subagent's to do; `recommend` only says so.
AGENT_MODES = ("require", "recommend")
#: `box` (default) or `chain`, chosen at install. `WF_RENDER` wins when valid.
RENDER_FILE = Path.home() / ".local/share/omge/render"
RENDERS = ("box", "chain")


WfError = store.WfError
#: A check or `when` that cannot fail proves nothing: it is the lock released by
#: saying so, which is exactly what `new` was misused for.
TRIVIAL = {"", "true", ":", "exit 0", "test 1", "[ 1 ]", "/bin/true"}
#: `echo …` on its own always exits 0.
ECHO_ONLY = re.compile(r"echo(\s[^;&|]*)?")


def descendants(t: dict, nid: str) -> set[str]:
    """`nid` and everything after it. File order is a topological order."""
    out = {nid}
    for n in t["nodes"]:
        if out & set(n["after"]):
            out.add(n["id"])
    return out


def parse(body: str, strict: bool = False) -> dict:  # noqa: C901
    """`strict`: a graph being submitted now, where a checked node needs `on_fail`.
    A stored task graph or library type from before on_fail was required reads a missing one as retry."""
    t = yaml.safe_load(body)
    if not isinstance(t, dict) or not re.fullmatch(r"[a-z0-9-]+", str(t.get("type", ""))):
        raise WfError("graph needs a kebab-case `type`")
    if not isinstance(t.get("match"), str):
        raise WfError("graph needs a `match` sentence")
    if type(t.get("optional_issue", False)) is not bool:
        raise WfError("optional_issue must be a boolean")
    params = t["params"] = t.get("params") or {}
    for name, pattern in params.items():
        try:
            re.compile(pattern)
        except (re.error, TypeError) as exc:
            raise WfError(f"param {name}: bad regex: {exc}") from None
    nodes = t["nodes"] = t.get("nodes") or []
    seen: list[str] = []
    for node in nodes:
        nid = str(node.get("id", ""))  # YAML reads a bare `yes`/`no` as a bool
        if not re.fullmatch(r"[a-z_]+", nid) or nid in seen:
            raise WfError(f"node id {nid!r} is malformed or duplicated")
        # `after` may only name EARLIER nodes: that makes the forward graph acyclic
        # by construction and the file order a valid execution order. A forward
        # `next` adds its source here, so `next` targets wait on their decision.
        node["next"] = [dict(e) for e in node.get("next") or []]
        after = list(node.get("after") or [])
        after += [s["id"] for s in nodes[:len(seen)] if s["id"] not in after
                  and any(e.get("to") == nid for e in s["next"])]
        node["after"] = after
        if missing := [a for a in after if a not in seen]:
            raise WfError(f"node {nid}: `after` names no earlier node {missing}")
        if nid == "retro":
            if "check" in node:
                raise WfError("`retro` takes no check in the graph; omge:wf supplies it")
            node["check"] = RETRO_CHECK
            if node.get("auto_after") not in (True, "auto", "pinned"):
                node.pop("auto_after", None)
        elif "auto_after" in node:
            node.pop("auto_after")
        if isinstance(node.get("check"), str):  # a YAML trailing newline is no change
            node["check"] = node["check"].rstrip()
        if node.get("kind") not in (None, "human"):
            raise WfError(f"node {nid}: kind must be human or absent")
        if not node.get("check") and node.get("kind") != "human":
            raise WfError(f"node {nid}: a node without `check` must be `kind: human`")
        if "check" in node and nid != "retro" and _trivial(node["check"]):
            raise WfError(i18n.t("graph.check_trivial", nid=nid, check=repr(node["check"])))
        if node.get("check") and nid != "retro" and "on_fail" not in node:
            if strict:
                raise WfError(i18n.t("graph.on_fail_required", nid=nid, help=i18n.t("graph.on_fail_help")))
            node["on_fail"] = RETRY
        if "on_fail" in node:
            limit = node["on_fail_max"] = node.get("on_fail_max", 3)
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise WfError(i18n.t("graph.on_fail_max", nid=nid, limit=repr(limit)))
        if "skill" in node:  # one name, or a list of names any one of which will do
            names = [node["skill"]] if isinstance(node["skill"], str) else node["skill"]
            if nid == "retro" or not isinstance(names, list) or not names or not all(
                    isinstance(s, str) and s.strip() for s in names):
                raise WfError(i18n.t("graph.skill_field", nid=nid))
            node["skill"] = names
        if "agent" in node or "agent_mode" in node:  # a subagent_type, or any-of a list
            names = [node.get("agent")] if isinstance(node.get("agent"), str) else node.get("agent")
            mode = node.setdefault("agent_mode", "require")
            if nid == "retro" or not isinstance(names, list) or not names or not all(
                    isinstance(s, str) and s.strip() for s in names) or mode not in AGENT_MODES:
                raise WfError(i18n.t("graph.agent_field", nid=nid))
            node["agent"] = names
        # A receipt for code that has not changed since it passed carries over.
        if "carry" in node and (node["carry"] != "code" or nid == "retro" or not node.get("check")):
            raise WfError(i18n.t("graph.carry_field", nid=nid))
        # `floor` is a `protected` gate that is never waived and is re-run before anything
        # after it closes; a cached pass (`carry`) would defeat that.
        for key in ("protected", "floor"):
            if type(node.get(key, False)) is not bool:
                raise WfError(i18n.t("graph.gate_field", nid=nid))
            if not node.get(key):
                node.pop(key, None)
        if node.get("floor"):
            node["protected"] = True
        if node.get("protected") and (nid == "retro" or (node.get("floor") and (
                not node.get("check") or "carry" in node))):
            raise WfError(i18n.t("graph.gate_field", nid=nid))
        node["timeout"] = int(node.get("timeout", 120))
        node["pitfalls"] = node.get("pitfalls") or []
        seen.append(nid)
    told = ""  # every `do` so far: a param's contract is said there or earlier
    for node in nodes:
        shells = [str(sh or "")
                  for sh in [node.get("check"), *(e.get("when") for e in node["next"])]]
        used = {p for s in shells for p in PLACEHOLDER.findall(s)}
        if undeclared := used - set(params):
            raise WfError(f"node {node['id']}: uses undeclared params {sorted(undeclared)}")
        told += f"\n{node.get('do') or ''}"
        # A task sat on `inventory` for want of `{audit}`: the check read it, no `do` said so.
        if strict and (mute := sorted(p for p in used if not re.search(
                rf"(?<![A-Za-z0-9_]){p}(?![A-Za-z0-9_])", told))):
            raise WfError(i18n.t("graph.param_unsaid", nid=node["id"], params=mute))
        for e in node["next"]:
            if "when" in e and (not isinstance(e["when"], str) or _trivial(e["when"])):
                raise WfError(i18n.t("graph.when_trivial", nid=node["id"], when=repr(e["when"])))
            to = e.get("to")
            if to not in seen:
                raise WfError(f"node {node['id']}: `next` names no node {to!r}")
            # Backwards must be a loop to an ancestor, never a jump sideways.
            if seen.index(to) <= seen.index(node["id"]) and node["id"] not in descendants(t, to):
                raise WfError(f"node {node['id']}: `next` to {to} goes back but not to an ancestor")
            e["max"] = int(e.get("max", 1))
        to = node.get("on_fail", RETRY)
        if to != RETRY and (to not in seen or seen.index(to) >= seen.index(node["id"])
                            or node["id"] not in descendants(t, to)):
            raise WfError(i18n.t("graph.on_fail_not_ancestor", nid=node["id"], to=repr(to),
                            help=i18n.t("graph.on_fail_help")))
    # No agent work may slip past the retro. Human nodes may follow it.
    tail = nodes[seen.index("retro") + 1:] if "retro" in seen else [{}]
    if any(n.get("kind") != "human" for n in tail):
        raise WfError("a `retro` node is required, followed by nothing but human nodes")
    return t


def _trivial(shell: object) -> bool:
    if not isinstance(shell, str):
        return True
    norm = " ".join(shell.split()).rstrip("; ")
    return norm in TRIVIAL or bool(ECHO_ONLY.fullmatch(norm))


def _apply_node(by_id: dict, order: list, nid: object, fields: object) -> None:
    if not isinstance(nid, str) or not nid:
        raise WfError(f"node id {nid!r} is malformed or duplicated")
    if fields is None:
        by_id.pop(nid, None)
        if nid in order:
            order.remove(nid)
        return
    if not isinstance(fields, dict):
        raise WfError(i18n.t("graph.fields_type", nid=nid))
    if nid in by_id:
        by_id[nid].update(fields)
        return
    by_id[nid] = {"id": nid, **fields}
    order.insert(order.index("retro") if "retro" in order else len(order), nid)


def _patch(stored: dict, patch: dict) -> dict:
    """`nodes` as {id: fields|null} edits the stored graph; anything else is the
    whole graph. Omitted params and omitted fields stay. retro stays last."""
    nodes = patch.get("nodes")
    if not isinstance(nodes, dict):
        return patch
    by_id = {n["id"]: dict(n) for n in stored["nodes"]}
    order = [n["id"] for n in stored["nodes"]]
    for nid, fields in nodes.items():
        _apply_node(by_id, order, nid, fields)
    out = {k: v for k, v in stored.items() if k not in ("nodes", "params")}
    if "optional_issue" in patch:
        out["optional_issue"] = patch["optional_issue"]
    out["params"] = {**stored.get("params", {}), **(patch.get("params") or {})}
    out["nodes"] = [by_id[i] for i in order if i in by_id]
    for n in out["nodes"]:  # parse injects retro's check; submitting it back is refused
        if n.get("id") == "retro":
            n.pop("check", None)
            # auto_after is "auto" only when wf filled the leaves. "pinned" means the
            # caller named after. Neither mark: a leaf change must name after.
            pinned = (nodes.get("retro") or {}).get("after") if isinstance(
                nodes.get("retro"), dict) else None
            leaves = _leaves(out["nodes"])
            mark = n.get("auto_after")
            if pinned is not None:
                n["auto_after"] = "pinned"
            elif mark in (True, "auto"):
                n["after"] = leaves
                n["auto_after"] = "auto"
            elif mark != "pinned":
                old_leaves = _leaves(stored["nodes"])
                n["auto_after"] = None  # submitted must not read a missing mark as a new pin
                if leaves != old_leaves:
                    raise WfError(i18n.t("graph.retro_after_needed"))
    return out


def _leaves(nodes: list) -> list:
    """Nodes nothing waits on and with no forward edge: a loop back or onto itself
    still ends there."""
    ids = [n.get("id") for n in nodes]
    named = {a for n in nodes if n.get("id") != "retro" for a in n.get("after") or []}
    return [n.get("id") for i, n in enumerate(nodes) if n.get("id") != "retro"
            and n.get("id") not in named
            and not any(e.get("to") not in ids[:i + 1] for e in n.get("next") or [])]


def _check_contract(n: dict) -> tuple:
    """What a finished node was accepted against. `do` and the rest are not it.
    `next` destinations are: a done route is which edge was taken. A gate (agent, skill)
    or a predecessor it never waited on is too: its receipts predate the new contract."""
    return (n.get("check"), tuple((e.get("to"), e.get("when")) for e in n["next"]),
            n.get("on_fail"), n.get("kind"), n.get("agent"), n.get("agent_mode"),
            n.get("skill"), n["after"], n.get("protected"), n.get("floor"))


def _reject_ambiguous_comments(node: yaml.Node | None, source: str) -> None:
    structural = {"id", "after", "to", "on_fail", "kind", "timeout", "on_fail_max", "max",
                  "agent", "agent_mode", "skill", "carry", "floor", "protected", "auto_after",
                  "optional_issue", "type", "nodes", "next", "params"}
    comment = re.compile(r"[ \t]+#")
    comment_after_key = re.compile(r"[ \t]*:[ \t]+#")
    seen = set()

    def visit(node: yaml.Node, field: str = "", key_end: int | None = None) -> None:
        if isinstance(node, yaml.ScalarNode):
            if (node.style is None and field not in structural
                    and not (field == "pitfalls" and node.tag == "tag:yaml.org,2002:null")
                    and (comment.match(source, node.end_mark.index)
                         or (key_end is not None and comment_after_key.match(source, key_end)))):
                raise WfError(i18n.t("graph.yaml_comment"))
            return
        if (node, field) in seen:
            return
        seen.add((node, field))
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                child = key.value if field != "params" and isinstance(key, yaml.ScalarNode) else ""
                visit(value, child, key.end_mark.index)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                visit(value, field)

    if node is not None:
        visit(node)


def load(graph: str | dict | list, strict: bool = True) -> dict:
    """Load a submitted graph, refusing comments that may truncate plain free text."""
    if isinstance(graph, str):
        try:
            loaded = yaml.safe_load(graph)
            if strict and "#" in graph:
                _reject_ambiguous_comments(yaml.compose(graph, Loader=yaml.SafeLoader), graph)
        except yaml.YAMLError as exc:
            raise WfError(i18n.t("graph.yaml_unreadable", exc=exc)) from None
        graph = loaded
    if isinstance(graph, list):
        graph = {"nodes": graph}
    if not isinstance(graph, dict):
        raise WfError(i18n.t("graph.not_object"))
    return graph


def is_back(t: dict, src: str, to: str) -> bool:
    ids = [n["id"] for n in t["nodes"]]
    return ids.index(to) <= ids.index(src)


def submitted(graph: str | dict | list, type_: str, match: str, strict: bool = True) -> str:
    """The body for an agent-submitted graph, `retro` appended after the leaves."""
    g = load(graph, strict=strict)
    nodes = g.get("nodes")
    if not nodes or not isinstance(nodes, list) or [n.get("id") for n in nodes] == ["retro"]:
        raise WfError(i18n.t("graph.nodes_needed"))
    if strict and not any(n.get("check") for n in nodes if n.get("id") != "retro"):
        raise WfError(i18n.t("graph.check_needed"))
    # A caller-supplied retro with no `after` is the same append: fill the leaves and
    # mark auto_after. An explicit after, even the current leaves, stays pinned.
    retro = next((n for n in nodes if isinstance(n, dict) and n.get("id") == "retro"), None)
    if retro is None:
        nodes.append({"id": "retro", "do": i18n.t("graph.retro_do")})
        retro = nodes[-1]
    if "after" not in retro:
        retro["after"] = _leaves(nodes)
        # A plain key: PyYAML emits a Python `True` key as `true`, and the load drops it.
        retro["auto_after"] = "auto"
        retro.setdefault("do", i18n.t("graph.retro_do"))
    elif retro.get("auto_after") in (True, "auto", "pinned"):
        pass  # filled by wf, or already pinned; a later dump must not re-mark it
    elif retro.get("auto_after") is None and "auto_after" in retro:
        retro.pop("auto_after")  # an unmarked stored graph, sent through a patch
    elif "auto_after" not in retro:
        retro["auto_after"] = "pinned"  # the caller just named after
    else:
        retro.pop("auto_after", None)
    optional = {"optional_issue": g["optional_issue"]} if "optional_issue" in g else {}
    body = yaml.safe_dump({"type": type_, "match": match, **optional,
                           "params": g.get("params") or {},
                           "nodes": nodes}, allow_unicode=True, sort_keys=False)
    parse(body, strict=strict)
    return body


def pending(t: dict, rows: dict[str, sqlite3.Row]) -> list[dict]:
    return [n for n in t["nodes"] if rows[n["id"]]["status"] == "pending"]


def ready(t: dict, rows: dict[str, sqlite3.Row]) -> list[dict]:
    return [n for n in pending(t, rows)
            if all(rows[a]["status"] != "pending" for a in n["after"])]


_SHA = re.compile(r"Reviewed SHA: ([0-9a-f]{40})")
_VERDICT = re.compile(r'verdict\W*:\s*"?([^"\n]*)', re.I)
_BLOCK = re.compile(r"\b(?:P[01]|CRITICAL|MAJOR|BLOCKER)\b", re.I)


def verdict(text: str) -> tuple[str, str | None]:
    """(`pass` | `blocking` | `unknown`, reviewed SHA or None) of a reviewer reply.

    The contract is structural; anything outside it is `unknown`, which fails closed.
    The authoritative verdict is the reply's last two lines, exactly `Verdict: pass`
    then `Reviewed SHA: <40 hex>`, and it must be the only place the
    reply says verdict at all — a second declaration is a conflict. At most one
    final newline; a blank line between the two lines, or after them, is not a pass,
    and neither line may carry trailing whitespace. A reply with a code fence or an
    HTML tag is never a pass. A blocking word (P0, P1, CRITICAL, MAJOR, BLOCKER)
    anywhere, or any declared verdict whose whole value is not exactly the pass value
    (an empty one included), is blocking. Format characters are dropped first.
    """
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    values = [v.strip(" *").lower() for v in _VERDICT.findall(text)]
    blocking = bool(_BLOCK.search(text)) or any(v != "pass" for v in values)
    sha = m[1] if lines and (m := _SHA.fullmatch(lines[-1])) else None
    code = "```" in text or "~~~" in text or re.search(r"<[a-z!/]", text, re.I)
    single = len(re.findall(r"verdict", text, re.I)) == 1
    passed = len(lines) > 1 and lines[-2] == "Verdict: pass" and single and not code
    return ("blocking" if blocking else "pass" if passed and sha else "unknown"), sha


def ready_since(conn: sqlite3.Connection, task: sqlite3.Row, t: dict, node_id: str) -> float:
    """When `node_id` last became ready: task start, a predecessor close, a loop back onto
    it, or a revise that reopened it. A call from before that does not authorize the new
    contract."""
    since = task["created"]
    after = next(n["after"] for n in t["nodes"] if n["id"] == node_id)
    for ts, nid, kind, detail in conn.execute(
            "SELECT ts, node, kind, detail FROM event WHERE task = ?"
            " AND kind IN ('done', 'waive', 'back', 'reopen', 'revise_change')", (task["id"],)):
        if kind == "revise_change":
            reopened = nid == node_id
        elif kind in ("back", "reopen"):
            reopened = node_id in descendants(t, detail)
        else:
            reopened = nid in after  # a predecessor closing always starts a new ready
        if reopened:
            since = max(since, ts)
    return since


def review_receipts(conn: sqlite3.Connection, task: sqlite3.Row, node_id: str,
                     since: float) -> dict[str, dict]:
    """Each agent's latest `review` receipt on `node_id` since it became ready."""
    latest: dict[str, dict] = {}
    for detail, in conn.execute(
            "SELECT detail FROM event WHERE task = ? AND node = ? AND kind = 'review'"
            " AND ts >= ? ORDER BY rowid", (task["id"], node_id, since)):
        rec = json.loads(detail)
        latest[rec["agent"]] = rec
    return latest


def skill_at(conn: sqlite3.Connection, task: sqlite3.Row, t: dict, node: dict,
             field: str = "skill") -> float | None:
    """When `node`'s `skill` (or `agent`) was first called (PostToolUse event) since the node
    became ready: its predecessors' last close, a loop back that reopened it, or a revise that
    put it back to pending. The first call, not the last: another task's call recorded here
    later must not push a receipt already written after a real call back to "before the call"
."""
    since = ready_since(conn, task, t, node["id"])
    names = [*node[field], *(f"{s} (agent)" for s in node[field])]
    return conn.execute(
        "SELECT MIN(ts) FROM event WHERE task = ? AND node = ? AND kind = ?"
        f" AND detail IN ({', '.join('?' * len(names))}) AND ts >= ?",
        (task["id"], node["id"], field, *names, since)).fetchone()[0]


def unset_note(t: dict, node: dict, params: dict[str, str]) -> str | None:
    """Name unset params that prevent a ready node's check from passing."""
    shells = [node.get("check"), *(e.get("when") for e in node["next"])]
    missing = sorted({p for sh in shells if isinstance(sh, str) for p in PLACEHOLDER.findall(sh)
                      if not params.get(p) and not re.fullmatch(t["params"][p], "")})
    if node["id"] == "retro" or not missing:
        return None
    return i18n.t("graph.unset_note", params=", ".join(f"`{p}`" for p in missing))


def _check_param(t: dict, key: str, value: str) -> None:
    if key not in t["params"]:
        raise WfError(i18n.t("graph.not_param", name=key, params=sorted(t["params"])))
    if not re.fullmatch(t["params"][key], value):
        raise WfError(i18n.t("graph.param_mismatch", name=key, value=repr(value),
                        pattern=repr(t["params"][key])))


def start(conn: sqlite3.Connection, type_: str, dir_: str, issue_url: str | None,  # noqa: C901
          params: dict[str, str], graph: str | dict | list | None = None,
          new_reason: str | None = None, title: str | None = None) -> int:
    """`issue_url=None` starts a task keyed by its own id; `title` then says what it is."""
    if issue_url:
        if task := store.open_task(conn, issue_url):
            raise WfError(i18n.t("graph.task_open", id=task["id"], type=task["type"]))
        if not ISSUE_URL.fullmatch(issue_url):
            raise WfError(i18n.t("graph.issue_url", url=repr(issue_url)))
    elif not (title or "").strip():
        raise WfError(i18n.t("graph.title_needed"))
    if not Path(dir_).is_dir():
        raise WfError(i18n.t("graph.dir_missing", dir=repr(dir_)))
    title, text = library.issue_text(issue_url) if issue_url else (title.strip(), title.strip())
    if type_ == "new":
        if not graph:
            raise WfError(i18n.t("graph.new_needs_graph"))
        if not new_reason and (hits := library.similar(conn, text)):
            raise WfError(i18n.t("graph.similar_type", hits=", ".join(hits)))
        body, version = submitted(graph, "new", i18n.t("graph.new_match")), ""
    elif graph:
        raise WfError(i18n.t("graph.graph_only_new"))
    else:  # only the reviewed default is reused; a leftover candidate never runs
        lib = library.root()
        with library._locked(lib):
            found = library.body(type_, seed=False)
            if found is None:
                raise WfError(f"unknown workflow type {type_!r}; see wf_list_types")
            body, version = found
    t = parse(body)
    # A library type written for an issue would run its checks against an empty one.
    if not issue_url and not t.get("optional_issue", False) and any(
            "WF_ISSUE" in str(sh) for n in t["nodes"] if n["id"] != "retro"
            for sh in [n.get("check"), *(e.get("when") for e in n["next"])]):
        raise WfError(i18n.t("graph.issue_required"))
    for key, value in params.items():
        _check_param(t, key, value)
    try:
        return store.create_task(
            conn, type=type_, dir=str(Path(dir_).resolve()), issue_url=issue_url or None,
            version=version,
            graph=body, params=json.dumps(params), title=title, new_reason=new_reason,
            nodes=[n["id"] for n in t["nodes"]])
    except sqlite3.IntegrityError:  # lost a race with a concurrent wf_start
        raise WfError(i18n.t("graph.task_race")) from None


def set_param(conn: sqlite3.Connection, task: sqlite3.Row, key: str, value: str) -> None:
    """Every param write goes through here (wf_set, wf_complete params)."""
    t = parse(task["graph"])
    if key == "dir":  # move the task: where its checks run, not what it is
        if not Path(value).is_dir():
            raise WfError(i18n.t("graph.dir_absent", dir=repr(value)))
        store.update_task(conn, task["id"], dir=str(Path(value).resolve()))
    else:
        _check_param(t, key, value)
        store.update_task(conn, task["id"], params={**json.loads(task["params"]), key: value})


def _shape(n: dict) -> tuple:
    """What a node demands, as far as changing it can weaken it."""
    edges = tuple((e["to"], e.get("when"), e["max"]) for e in n["next"])
    return (n.get("check"), edges, tuple(n["after"]), n.get("kind"), n["timeout"],
            n.get("on_fail"), n.get("on_fail_max"), n.get("skill"), n.get("agent"),
            n.get("agent_mode"), n.get("carry"), n.get("protected"), n.get("floor"))


def revise(conn: sqlite3.Connection, task: sqlite3.Row, graph: str | dict | list,  # noqa: C901
           reason: str, title: str | None = None) -> str:
    """Swap the running graph. Surviving nodes keep their state; every change is a
    deviation, recorded with the reason and shown to the user as `changed:`."""
    if not reason.strip():
        raise WfError(i18n.t("graph.revise_reason"))
    old = parse(task["graph"])
    if isinstance(graph, dict) and isinstance(graph.get("nodes"), dict):
        graph = _patch(old, graph)
    body = submitted(graph, old["type"], old["match"])
    new = parse(body)
    rows = store.node_rows(conn, task["id"])
    olds, news = {n["id"]: n for n in old["nodes"]}, {n["id"]: n for n in new["nodes"]}
    # A running task's gate is its snapshot's: a stronger or weaker one comes with a new
    # task or library type, never by revising this one.
    for p in [i for i in olds if olds[i].get("protected")]:
        if p not in news or (_shape(olds[p]), olds[p].get("do")) != (
                _shape(news[p]), news[p].get("do")) \
                or not descendants(old, p) <= descendants(new, p):
            raise WfError(i18n.t("graph.revise_protected", nid=p))
    removed = [i for i in olds if i not in news]
    if held := [i for i in removed if rows[i]["status"] == "pending" and (olds[i].get("check")
                or olds[i].get("skill") or olds[i].get("agent_mode") == "require")]:
        raise WfError(i18n.t("graph.revise_held", ids=held))
    # Replacing a wrong check is the agent's to do (with a reason); dropping one is not.
    if bare := [i for i in olds if i in news and rows[i]["status"] == "pending"
                and olds[i].get("check") and not news[i].get("check")]:
        raise WfError(i18n.t("graph.revise_bare", ids=bare))
    # A required skill is waived with `wf_waive`, like a check.
    if lost := [i for i in olds if i in news and rows[i]["status"] == "pending"
                and olds[i].get("skill") and news[i].get("skill") != olds[i]["skill"]]:
        raise WfError(i18n.t("graph.revise_skill", ids=lost))
    if lost := [i for i in olds if i in news and rows[i]["status"] == "pending"
                and olds[i].get("agent") and (news[i].get("agent"), news[i].get("agent_mode"))
                != (olds[i]["agent"], olds[i]["agent_mode"])]:
        raise WfError(i18n.t("graph.revise_agent", ids=lost))
    added = [i for i in news if i not in olds]
    changed = [i for i in news if i in olds and rows[i]["status"] == "pending"
               and _shape(olds[i]) != _shape(news[i])]
    # A finished node stays finished unless the check that accepted it changed.
    # Its descendants reopen with it: their proof was the old contract.
    invalidated = [i for i in news if i in olds and rows[i]["status"] != "pending"
                   and _check_contract(olds[i]) != _check_contract(news[i])]
    invalidated += [i for i in news if i in olds and i not in invalidated
                    and rows[i]["status"] != "pending"
                    and any(i in descendants(old, src) and i != src for src in invalidated)]
    changed += invalidated
    changed += [f"params.{p}" for p in old["params"] if new["params"].get(p) != old["params"][p]]
    if old.get("optional_issue", False) != new.get("optional_issue", False):
        changed.append("optional_issue")
    parts = [f"+{', +'.join(added)}" if added else "", f"~{', ~'.join(changed)}" if changed else "",
             f"-{', -'.join(removed)}" if removed else ""]
    summary = i18n.t("graph.revise_summary", parts=" ".join(p for p in parts if p) or i18n.t("graph.none"),
                reason=reason)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for nid in removed:
            conn.execute("DELETE FROM node_state WHERE task = ? AND node = ?", (task["id"], nid))
        for nid in added:
            conn.execute("INSERT INTO node_state (task, node) VALUES (?, ?)", (task["id"], nid))
            store.event(conn, task["id"], nid, "revise_add", reason)
        for nid in changed:
            if nid in invalidated:
                store.set_node(conn, task["id"], nid, status="pending", route=None)
            if not nid.startswith("params.") and nid != "optional_issue":
                store.event(conn, task["id"], nid, "revise_change", reason)
        store.event(conn, task["id"], "", "revise", summary)
        fields = {"graph": body}
        if title is not None:
            fields["title"] = title
        store.update_task(conn, task["id"], **fields)
    return summary


def revise_text(conn: sqlite3.Connection, task: sqlite3.Row) -> str:
    """What wf_revise returns and the hook shows: the change, not the drawing."""
    task = store.get_task(conn, task["id"])
    t, rows = parse(task["graph"]), store.node_rows(conn, task["id"])
    last = conn.execute(
        "SELECT detail FROM event WHERE task = ? AND kind = 'revise' ORDER BY rowid DESC",
        (task["id"],)).fetchone()
    ready_ids = [n["id"] for n in ready(t, rows)]
    return "\n".join([last[0] if last else "",
                      "ready: " + (", ".join(ready_ids) or i18n.t("graph.none")),
                      f"params={task['params']} dir={task['dir']}"])


def render_mode() -> str:
    """`WF_RENDER`, else the file install.sh writes, else box. Hooks run under Claude,
    whose env does not reliably carry the shell's, so the file is the setting."""
    if (env := os.environ.get("WF_RENDER")) in RENDERS:
        return env
    try:
        value = RENDER_FILE.read_text().strip()
    except OSError:
        return "box"
    return value if value in RENDERS else "box"


def _w(text: str) -> int:
    """Monospace columns: Hangul and other wide characters take two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _at(items: list[tuple[int, str]]) -> str:
    """Texts placed at display columns, left to right."""
    out, width = "", 0
    for col, text in items:
        pad = col - width if col > width else (1 if out else 0)
        out += " " * pad + text
        width += pad + _w(text)
    return out


def _rule(marks: dict[int, str]) -> str:
    """A horizontal connector between the outermost marks."""
    lo, hi = min(marks), max(marks)
    return " " * lo + "".join(marks.get(c, "─") for c in range(lo, hi + 1))


def _marks() -> str:
    """A box label's optional marks after the id, in any locale's wording."""
    def alt(*keys: str) -> str:
        return "|".join(re.escape(v) for k in keys for v in i18n.variants(k))
    return (f"(?: 🔧(?:{alt('graph.skill')}))?(?: 🤖(?:{alt('graph.required', 'graph.recommended')}))?"
            r"(?: ↺\d+)?(?: waived)?")


def box_re() -> re.Pattern:
    """A box in the chain drawing (`[▶ id]`)."""
    return re.compile(r"\[\S+ ([a-z_]+)" + _marks() + r"\]")


def box_line_re() -> re.Pattern:
    """A box's middle line in the box drawing; FOLD is a folded straight run (`┆ a → b`)."""
    return re.compile(r"[│┃] \S ([a-z_]+)" + _marks() + r" *[│┃]")


FOLD = re.compile(r"┆ (.*)")


def _legend() -> str:
    return i18n.t("graph.legend") + (i18n.t("graph.legend_box") if render_mode() == "box" else "")


def drawn_ids(text: str) -> Counter:
    """Every node a drawing names: chain tokens, box middle lines, folded runs."""
    return Counter([*box_re().findall(text), *box_line_re().findall(text),
                    *(i for f in FOLD.findall(text) for i in re.findall(r"[a-z_]+", f))])


def _clip(line: str) -> str:
    while _w(line) > WIDTH:
        line = line[:-2] + "…"
    return line


def _label(e: dict) -> str:
    return e.get("label") or (e["when"].strip().splitlines()[0][:30] if e.get("when") else "else")


def _boxer(t: dict, rows: dict[str, sqlite3.Row], ready_ids: list[str]):
    human = {n["id"] for n in t["nodes"] if n.get("kind") == "human"}

    def box(nid: str) -> str:
        r = rows[nid]
        sym = SYMBOL.get(r["status"]) or (("⏸" if nid in human else "▶") if nid in ready_ids
                                          else "·")
        extra = (f" ↺{r['loops']}" if r["loops"] else "") + (
            " waived" if r["status"] == "waived" else "")
        return f"[{sym} {nid}{extra}]"

    return box


def _drawing(t: dict, rows: dict[str, sqlite3.Row], box) -> list[str]:
    """Boxes and arrows, or one line per node when that cannot be drawn right; then
    each `on_fail` back-edge as `[node] ─✗─► ↺ target`."""
    lines = _layout(t, rows, box)
    drawn = Counter(m for line in lines for m in box_re().findall(line))
    if max(map(_w, lines), default=0) > WIDTH or drawn != Counter(n["id"] for n in t["nodes"]):
        lines = []
        for n in t["nodes"]:
            lines.append(box(n["id"]) + (f" ◄─ {', '.join(n['after'])}" if n["after"] else ""))
            for i, e in enumerate(n["next"]):
                corner = "└" if i == len(n["next"]) - 1 else "├"
                to = (f"↺ {e['to']} {i18n.t('graph.max', n=e['max'])}" if is_back(t, n["id"], e["to"])
                      else e["to"])
                mark = " ✓" if rows[n["id"]]["route"] == e["to"] else ""
                lines.append(f"    {corner}─ {_label(e)} ──► {to}{mark}")
    return lines + [f"[{n['id']}] ─✗─► ↺ {n['on_fail']} {i18n.t('graph.max', n=n['on_fail_max'])}"
                    for n in t["nodes"] if n.get("on_fail", RETRY) != RETRY]


def _boxes(t: dict, rows: dict[str, sqlite3.Row], ready_ids: list[str], stalled: bool = False,  # noqa: C901
           folds: int = 0) -> list[str] | None:
    """Boxes top to bottom: a spine of boxes, branches side by side, back-edges on
    left rails. `folds`: how many straight runs (longest first) to fold to one line.
    None when the result would not name every node once within WIDTH (`_drawing` then)."""
    nodes = {n["id"]: n for n in t["nodes"]}
    ids = list(nodes)
    succ = {i: [n["id"] for n in t["nodes"] if i in n["after"]] for i in ids}
    # (source, target, mark, label, max): every edge that goes back up
    back = [(n["id"], n["on_fail"], "✗", "", n["on_fail_max"]) for n in t["nodes"]
            if n.get("on_fail", RETRY) != RETRY]
    back += [(n["id"], e["to"], "↺", _label(e), e["max"]) for n in t["nodes"] for e in n["next"]
             if is_back(t, n["id"], e["to"])]

    def sym(i: str) -> str:
        return SYMBOL.get(rows[i]["status"]) or (
            ("⛔" if stalled else "⏸" if nodes[i].get("kind") == "human" else "▶")
            if i in ready_ids else "·")

    def lab(i: str) -> str:
        r = rows[i]
        mode = {"require": f" 🤖{i18n.t('graph.required')}", "recommend": f" 🤖{i18n.t('graph.recommended')}"
                }.get(nodes[i].get("agent_mode"), "")
        return (f"{sym(i)} {i}" + (f" 🔧{i18n.t('graph.skill')}" if nodes[i].get("skill") else "") + mode
                + (f" ↺{r['loops']}" if r["loops"] else "")
                + (" waived" if r["status"] == "waived" else ""))

    def edge(src: str | None, to: str) -> str:
        """A forward `next` label; the branch not taken says (⊘)."""
        e = next((e for e in nodes[src]["next"] if e["to"] == to), None) if src else None
        if e is None:
            return ""
        route = rows[src]["route"]
        return _label(e) + ("(⊘)" if route and route != to else "")

    def why(i: str) -> str:
        said = [x for x in (rows[i]["last_output_tail"] or "").splitlines() if x.strip()]
        return said[-1].strip() if said else i18n.t("graph.why_exhausted")

    def tail(s: str, to: str, mark: str, label: str, most: int) -> str:
        return (f"[{s}] ─✗─► ↺ {to} {i18n.t('graph.max', n=most)}" if mark == "✗"
                else f"[{s}] ─ {label} ─► ↺ {to} {i18n.t('graph.max', n=most)}")

    stuck = [i for i in ids if stalled and i in ready_ids]
    layer: dict[str, int] = {}
    for n in t["nodes"]:
        layer[n["id"]] = 1 + max((layer[a] for a in n["after"]), default=-1)
    width = {i: _w(lab(i)) + 4 for i in ids}

    def place(row: list[str], labels: list[str]) -> tuple[list[int], list[int]]:
        """Side by side: box lefts and centres, each label clear of the next centre."""
        lefts = [0]
        for k in range(1, len(row)):
            gap = lefts[-1] + width[row[k - 1]] + 3
            if labels[k - 1]:
                gap = max(gap, lefts[-1] + width[row[k - 1]] // 2 + _w(labels[k - 1]) + 4
                          - width[row[k]] // 2)
            lefts.append(gap)
        return lefts, [x + width[i] // 2 for x, i in zip(lefts, row, strict=True)]

    specs: list[dict] = []
    prev: dict | None = None  # the last spec on the spine
    detour: tuple[str, str, str] | None = None  # (decision, side target, joining target)
    for d in range(max(layer.values()) + 1):
        row = [i for i in ids if layer[i] == d]
        top = prev["id"] if prev and prev["kind"] == "box" else None
        if len(row) == 1:
            i, after = row[0], nodes[row[0]]["after"]
            fw = [e["to"] for e in nodes[top]["next"]
                  if not is_back(t, top, e["to"])] if top else []
            join = next((j for j in fw if j != i), None)
            if detour and i == detour[2] and set(after) == {detour[0], detour[1]}:
                spec = {"kind": "box", "id": i, "down": True, "others": [], "edge": "", "ret": 1}
            elif top and after == [top] and not succ[i] and succ[top] == [i] \
                    and not prev["others"] and not prev.get("ret"):
                prev["side"] = i  # the last node sits beside its predecessor
                continue
            elif top and after == [top] and len(fw) == 2 and i in fw and succ[i] == [join] \
                    and set(nodes[join]["after"]) == {top, i} and layer[join] == d + 1 \
                    and [x for x in ids if layer[x] == d + 1] == [join]:
                detour = (top, i, join)
                specs.append({"kind": "detour", "id": i, "D": top, "J": join})
                continue
            elif prev and prev["kind"] == "group" and set(after) == set(prev["ids"]):
                spec = {"kind": "box", "id": i, "down": True, "merge": prev["centres"],
                        "others": [], "edge": ""}
            else:
                down = top in after
                spec = {"kind": "box", "id": i, "down": down,
                        "others": [a for a in after if a != top or not down],
                        "edge": edge(top, i) if down else ""}
        elif top and all(nodes[i]["after"] == [top] for i in row):
            labels = [edge(top, i) for i in row]
            lefts, centres = place(row, labels)
            end = max(lefts[-1] + width[row[-1]], centres[-1] + 2 + _w(labels[-1]))
            spec = {"kind": "group", "ids": row, "labels": labels, "lefts": lefts,
                    "centres": centres}
            if end > WIDTH - 8:  # too wide beside the rails: this layer as chain lines
                spec = {"kind": "chain", "ids": row}
        else:
            spec = {"kind": "chain", "ids": row}
        specs.append(spec)
        prev = spec

    # Fold the longest straight runs: plain boxes nothing else needs to see.
    targets = {to for _, to, *_ in back}
    near = set(ready_ids) | {x for r in ready_ids if r in nodes
                             for x in [*nodes[r]["after"], *succ[r]]}

    def plain(sp: dict) -> bool:
        i = sp.get("id")
        return (sp["kind"] == "box" and sp["down"] and not sp["others"] and not sp["edge"]
                and not sp.get("side") and not sp.get("merge") and not sp.get("ret")
                and len(nodes[i]["after"]) == 1 and len(succ[i]) == 1 and not nodes[i]["next"]
                and not nodes[i].get("skill") and not nodes[i].get("agent")
                and nodes[i].get("kind") != "human"
                and i not in near and i not in targets)

    runs, k = [], 0
    while k < len(specs):
        j = k
        while j < len(specs) and plain(specs[j]):
            j += 1
        if j - k >= 2:
            runs.append((k, j))
        k = max(j, k + 1)
    for k, j in sorted(sorted(runs, key=lambda r: r[0] - r[1])[:folds], reverse=True):
        specs[k:j] = [{"kind": "fold", "ids": [sp["id"] for sp in specs[k:j]]}]

    spine = [sp["id"] for sp in specs if sp["kind"] == "box"]
    inner = max((_w(lab(i)) + 2 for i in spine), default=0)  # spine boxes share one width
    S = max([(inner + 2) // 2] + [(sp["centres"][0] + sp["centres"][-1]) // 2
                                  for sp in specs if sp["kind"] == "group"])
    left = S - (inner + 2) // 2

    def box(i: str, down: bool, cont: bool, w: int) -> list[str]:
        h, v, cs = ("━", "┃", "┏┓┗┛") if i in ready_ids else ("─", "│", "┌┐└┘")
        c = (w + 2) // 2
        top, bot = cs[0] + h * w + cs[1], cs[2] + h * w + cs[3]
        if down:
            top = top[:c] + "▼" + top[c + 1:]
        if cont:
            bot = bot[:c] + ("┯" if i in ready_ids else "┬") + bot[c + 1:]
        b = lab(i)
        return [top, f"{v} {b}{' ' * (w - 2 - _w(b))} {v}", bot]

    def hangs(k: int) -> bool:
        """Whether the spec below hangs off spec k's bottom."""
        nx = specs[k + 1] if k + 1 < len(specs) else None
        if nx is None:
            return False
        if specs[k]["kind"] == "group":
            return nx["kind"] == "box" and bool(nx.get("merge"))
        return nx["kind"] in ("fold", "detour", "group") or (nx["kind"] == "box" and nx["down"])

    out: list[str] = []
    attach: dict[str, list[int]] = {}  # node -> lines a rail may join: middle, then bottom
    extra: dict[int, list[str]] = {}
    now: set[int] = set()
    mids: dict[str, int] = {}
    ret = 0
    for k, sp in enumerate(specs):
        cont = hangs(k)
        if sp["kind"] == "box":
            i = sp["id"]
            if sp.get("merge"):
                cs = sp["merge"]
                marks = {x: "┴" for x in cs}
                marks[min(cs)], marks[max(cs)] = "└", "┘"
                marks[S] = "┼" if S in cs else "┬"
                out.append(_rule(marks))
            top, mid, bot = (" " * left + x for x in box(i, sp["down"], cont, inner))
            if j := sp.get("side"):
                st, sm, sb = box(j, False, False, _w(lab(j)) + 2)
                top, mid, bot = top + "   " + st, mid + "──►" + sm, bot + "   " + sb
            if sp["edge"]:  # on the bottom the edge leaves from
                out[-1] += " " + sp["edge"]
            if sp["others"]:
                mid += " ◄─ " + ", ".join(sp["others"])
            if sp.get("ret"):
                top += " " * (ret - _w(top)) + "│"
                mid += "◄" + "─" * (ret - _w(mid) - 1) + "┘"
            out += [top, mid, bot]
            mids[i] = len(out) - 2
            attach[i] = [len(out) - 2, len(out) - 1]
            if {i, sp.get("side")} & set(ready_ids):
                now.add(len(out) - 2)
            out += [_at([(S, "│"), (S + 2, f"└ {why(i)}")]) if cont
                    else _at([(left + 2, f"└ {why(i)}")]) for _ in [i] if i in stuck]
        elif sp["kind"] == "detour":
            T, m = sp["id"], mids[sp["D"]]
            pre, label = f"── {edge(sp['D'], T)} ", edge(sp["D"], sp["J"])
            tw = width[T]
            ret = max(_w(out[m]) + _w(pre) + 2, S + 4 + _w(label) + tw // 2)
            out[m] += pre + "─" * (ret - _w(out[m]) - _w(pre)) + "┐"
            out[m + 1] += " " * (ret - _w(out[m + 1])) + "│"
            tl = ret - tw // 2
            tt, tm, tb = box(T, True, True, tw - 2)
            out += [_at([(S, "│"), (tl, tt)]), _at([(S, f"│ {label}" if label else "│"), (tl, tm)]),
                    _at([(S, "│"), (tl, tb)])]
            if T in ready_ids:
                now.add(len(out) - 2)
            out += [_at([(S, "│"), (tl + 2, f"└ {why(T)}")]) for _ in [T] if T in stuck]
        elif sp["kind"] == "group":
            row, labels = sp["ids"], sp["labels"]
            shift = S - (sp["centres"][0] + sp["centres"][-1]) // 2
            lefts = [x + shift for x in sp["lefts"]]
            centres = sp["centres"]
            centres[:] = [x + shift for x in centres]  # the merge below holds this list
            marks = {x: "┬" for x in centres}
            marks[min(centres)], marks[max(centres)] = "┌", "┐"
            marks[S] = "┼" if S in centres else "┴"
            out.append(_rule(marks))
            if any(labels):
                out.append(_at([(c, f"│ {x}" if x else "│")
                                for c, x in zip(centres, labels, strict=True)]))
            drawn = [box(i, True, cont, width[i] - 2) for i in row]
            out += [_at([(x, b[r]) for x, b in zip(lefts, drawn, strict=True)]) for r in range(3)]
            attach[row[0]] = [len(out) - 2, len(out) - 1]
            if set(row) & set(ready_ids):
                now.add(len(out) - 2)
            out += [_at([(x + 2, f"└ {why(i)}")]) for x, i in zip(lefts, row, strict=True)
                    if i in stuck]
        elif sp["kind"] == "fold":
            run = sp["ids"]
            same = {sym(i) for i in run}
            text = (f"{same.pop()} {' → '.join(run)}" if len(same) == 1
                    else " → ".join(f"{sym(i)} {i}" for i in run))
            out.append(" " * S + "┆ " + text)
            attach.update({i: [len(out) - 1] for i in run})
        else:
            for i in sp["ids"]:
                after = nodes[i]["after"]
                out.append(f"[{lab(i)}]" + (f" ◄─ {', '.join(after)}" if after else ""))
                attach[i] = [len(out) - 1]
                if i in ready_ids:
                    now.add(len(out) - 1)
                out += [f"  └ {why(i)}" for _ in [i] if i in stuck]

    # Rails: the two targets most edges go back to; every other back-edge is a tail line.
    reach = Counter(to for s, to, *_ in back if s in attach and to in attach
                    and attach[to][0] < attach[s][-1])
    rails = sorted(sorted(reach, key=lambda x: (-reach[x], ids.index(x)))[:2],
                   key=lambda x: attach[x][0])
    events: dict[int, tuple[str, str, str]] = {}  # line -> (rail target, T|S, mark)
    span = {to: [attach[to][0], attach[to][0]] for to in rails}
    for to in rails:
        events[attach[to][0]] = (to, "T", "")
    tails = []
    for s, to, mark, label, most in back:
        line = next((x for x in attach.get(s, []) if to in rails and x > attach[to][0]
                     and events.get(x, (to, "S"))[:2] == (to, "S")), None)
        if line is None:
            tails.append(tail(s, to, mark, label, most))
            continue
        old = events.get(line, ("", "", mark))[2]
        events[line] = (to, "S", "✗" if "✗" in (mark, old) else "↺")
        span[to][1] = max(span[to][1], line)
        if mark == "↺":
            extra.setdefault(line, []).append(f"↺ {label} {i18n.t('graph.max', n=most)}")
    live = [to for to in rails if span[to][1] > span[to][0]]
    col = {to: 1 + 2 * n for n, to in enumerate(live)}
    P = 2 * len(live) + 4 if live else 0
    lines = []
    for L, content in enumerate(out):
        # The marker before the labels: `_clip` cuts the tail of a long line, never the marker.
        content += (i18n.t("graph.now") if L in now else "") + "".join(f" {x}" for x in extra.get(L, []))
        ch = [" "] * P
        for to in live:
            if span[to][0] < L < span[to][1]:
                ch[col[to]] = "│"
        to, kind, mark = events.get(L, ("", "", ""))
        if to in live:
            c = col[to]
            for x in range(c + 1, P):
                ch[x] = "┼" if ch[x] == "│" else "─"
            body = content.lstrip(" ")
            n = len(content) - len(body)
            if kind == "T":
                ch[c] = "┌"
                content = "".join(ch[:-1]) + "─" * n + "►" + body
            else:
                ch[c], ch[P - 3] = ("└" if span[to][1] == L else "├"), mark
                content = "".join(ch) + "─" * n + body
        else:
            content = "".join(ch) + content
        lines.append(_clip(content))
    lines += map(_clip, tails)
    return lines if drawn_ids("\n".join(lines)) == Counter(ids) else None


def _draw(task: sqlite3.Row, t: dict, rows: dict[str, sqlite3.Row], ready_ids: list[str],
          folds: int = 0) -> list[str]:
    """The drawing `render_mode` picks; box falls back to chain when it cannot draw."""
    box = _boxer(t, rows, ready_ids)
    if render_mode() == "box":
        return (_boxes(t, rows, ready_ids, task["state"] == "stalled", folds)
                or _drawing(t, rows, box))
    return _drawing(t, rows, box)


def _head(task: sqlite3.Row) -> str:
    version = f" @{task['version'][:7]}" if task["version"] else ""
    if store.has_issue(task):
        repo, num = task["issue_url"].split("/")[-3], task["issue_url"].split("/")[-1]
        return f"{task['type']}{version} · {repo}#{num} · task #{task['id']} {task['state']}"
    # The id first: PostToolUse reads the first `task #<id>` to grant ownership.
    return f"{task['type']}{version} · task #{task['id']} {task['state']} · {task['title']}"


def _fits(lines: list[str]) -> bool:
    return len(lines) <= MAX_LINES and len("\n".join(lines)) <= MAX_CHARS


def render(task: sqlite3.Row, t: dict, rows: dict[str, sqlite3.Row], folds: int = 0) -> str:
    """The whole graph. Every node's box appears exactly once and no line exceeds WIDTH.
    `folds`: box mode folds that many straight runs."""
    ready_ids = [n["id"] for n in ready(t, rows)]
    tail = [i18n.t("graph.current", ids=", ".join(ready_ids) or i18n.t("graph.no_nodes_left")), _legend()]
    return "\n".join(map(_clip, [_head(task), *_draw(task, t, rows, ready_ids, folds), *tail]))


def bounded(task: sqlite3.Row, t: dict, rows: dict[str, sqlite3.Row]) -> str:
    """`render` when it fits MAX_LINES/MAX_CHARS. Otherwise the closed prefix on one line,
    the ready nodes with their predecessors, branch/`on_fail` targets and two layers of
    successors drawn, and a count of the rest. The ready nodes are always named."""
    full = render(task, t, rows)
    if _fits(full.splitlines()):
        return full
    if render_mode() == "box":  # fold straight runs one at a time until it fits
        folds, last = 1, full
        while (text := render(task, t, rows, folds)) != last:
            if _fits(text.splitlines()):
                return text
            folds, last = folds + 1, text
    nodes = {n["id"]: n for n in t["nodes"]}
    ready_ids = [n["id"] for n in ready(t, rows)]
    focus = set(ready_ids)
    for r in ready_ids:
        focus |= {*nodes[r]["after"], *(e["to"] for e in nodes[r]["next"])}
        focus |= {nodes[r].get("on_fail", RETRY)} - {RETRY}
    layer = set(ready_ids)
    for _ in range(2):
        layer = {n["id"] for n in t["nodes"] if layer & set(n["after"])}
        focus |= layer
    sub = {**t, "nodes": [{**n, "after": [a for a in n["after"] if a in focus],
                           "next": [e for e in n["next"] if e["to"] in focus]}
                          for n in t["nodes"] if n["id"] in focus]}
    closed = [n["id"] for n in t["nodes"]
              if n["id"] not in focus and rows[n["id"]]["status"] != "pending"]
    rest = [i for i in nodes if i not in focus and rows[i]["status"] == "pending"]
    shown = closed if len(closed) <= 4 else [*closed[:2], "…", closed[-1]]
    prefix = [i18n.t("graph.done_prefix", n=len(closed), path=" → ".join(shown))] if closed else []
    more = [i18n.t("graph.more_after", n=len(rest))] if rest else []
    now = textwrap.wrap(i18n.t("graph.current", ids=", ".join(ready_ids) or i18n.t("graph.no_nodes_left")),
                        WIDTH - 4)
    drawn = _draw(task, sub, rows, ready_ids) if focus else []
    lines = [_clip(x) for x in [_head(task), *prefix, *drawn, *more]]
    legend = _legend()
    if not _fits(lines + now + [legend]):
        lines = [_clip(x) for x in [_head(task), *prefix, *more]]
    lines += [*now, legend]
    while not _fits(lines):  # ponytail: only hundreds of ready nodes get here
        del lines[-2]
    return "\n".join(lines)


def _layout(t: dict, rows: dict[str, sqlite3.Row], box) -> list[str]:  # noqa: C901
    """Boxes and arrows, layered by longest forward path. Straight chains share a
    row (`──►`), one-to-many and many-to-one get box connectors, a decision lists
    its `next` edges under itself. An edge that cannot be drawn straight is written
    on its target as `◄─ preds` rather than drawn wrongly."""
    nodes = {n["id"]: n for n in t["nodes"]}
    layer: dict[str, int] = {}
    for n in t["nodes"]:
        layer[n["id"]] = 1 + max((layer[a] for a in n["after"]), default=-1)
    lines: list[str] = []
    drawn: set[str] = set()
    cur: list[tuple[str, int | None]] = []  # the last drawn row: (node, centre column)
    chain: list[tuple[int, str]] = []  # the row being built

    def flush() -> None:
        if chain:
            lines.append(_at(chain))
            chain.clear()

    def branches(nid: str, c: int) -> list[tuple[str, None]]:
        items, targets = [], []
        labels = [_label(e) for e in nodes[nid]["next"]]
        fill = max(map(_w, labels))
        for i, (e, label) in enumerate(zip(nodes[nid]["next"], labels, strict=True)):
            if is_back(t, nid, e["to"]):
                target = f"↺ {e['to']} {i18n.t('graph.max', n=e['max'])}"
            elif e["to"] in drawn:  # another decision already drew it
                target = e["to"]
            else:
                others = [a for a in nodes[e["to"]]["after"] if a != nid]
                target = box(e["to"]) + (f" ◄─ {', '.join(others)}" if others else "")
                drawn.add(e["to"])
                targets.append((e["to"], None))
            mark = " ✓" if rows[nid]["route"] == e["to"] else ""
            corner = "└" if i == len(labels) - 1 else "├"
            items.append(f"{corner}─ {label} {'─' * (fill - _w(label))}──► {target}{mark}")
        indent = max(0, min(c, WIDTH - max(map(_w, items))))
        lines.append(" " * c + "│")
        if indent < c:
            lines.append(_rule({indent: "┌", c: "┘"}))
        lines.extend(" " * indent + s for s in items)
        return targets

    for depth in range(max(layer.values()) + 1):
        row = [n for n in t["nodes"] if layer[n["id"]] == depth and n["id"] not in drawn]
        if not row:
            continue
        ids = [n["id"] for n in row]
        solo = len(cur) == 1 and cur[0][1] is not None
        if len(row) == 1 and solo and row[0]["after"] == [cur[0][0]]:
            b = box(ids[0])
            width = _w(_at(chain))
            if chain and width + 5 + _w(b) <= WIDTH:
                chain += [(width + 1, "──►"), (width + 5, b)]
                cur = [(ids[0], width + 5 + _w(b) // 2)]
            else:  # below the previous box
                flush()
                lines.append(" " * cur[0][1] + "▼")
                start = max(0, min(cur[0][1] - _w(b) // 2, WIDTH - _w(b)))
                chain[:] = [(start, b)]
                cur = [(ids[0], start + _w(b) // 2)]
        elif len(row) > 1 and solo and all(n["after"] == [cur[0][0]] for n in row):
            flush()
            c, boxes = cur[0][1], [box(i) for i in ids]
            total = sum(map(_w, boxes)) + 3 * (len(boxes) - 1)
            col = max(0, min(c - total // 2, WIDTH - total))
            centres = []
            for b in boxes:
                chain.append((col, b))
                centres.append(col + _w(b) // 2)
                col += _w(b) + 3
            marks = {x: "┬" for x in centres}
            marks[min(marks)], marks[max(marks)] = "┌", "┐"
            marks[c] = "┼" if c in marks else "┴"
            lines += [" " * c + "│", _rule(marks), _at([(x, "▼") for x in centres])]
            cur = list(zip(ids, centres, strict=True))
        elif len(row) == 1 and len(cur) > 1 and all(c is not None for _, c in cur) \
                and set(row[0]["after"]) == {i for i, _ in cur}:
            flush()
            centres = [c for _, c in cur]
            mid = (min(centres) + max(centres)) // 2
            marks = {x: "┴" for x in centres}
            marks[min(marks)], marks[max(marks)] = "└", "┘"
            marks[mid] = "┼" if mid in marks else "┬"
            b = box(ids[0])
            start = max(0, mid - _w(b) // 2)
            lines += [_rule(marks), " " * mid + "▼"]
            chain[:] = [(start, b)]
            cur = [(ids[0], start + _w(b) // 2)]
        elif len(row) == 1 and not row[0]["after"]:  # a root starts a row
            flush()
            chain[:] = [(0, box(ids[0]))]
            cur = [(ids[0], _w(box(ids[0])) // 2)]
        else:
            flush()
            for n in row:
                lines.append(box(n["id"]) + (f" ◄─ {', '.join(n['after'])}" if n["after"] else ""))
            cur = [(ids[0], _w(box(ids[0])) // 2)] if len(row) == 1 else [(i, None) for i in ids]
        placed = [i for i, _ in cur if i in ids and nodes[i]["next"]]
        if placed:
            flush()
            centres = dict(cur)
            cur = [x for nid in placed for x in branches(nid, centres[nid] or 0)]
    flush()
    return lines


def status_text(conn: sqlite3.Connection, task: sqlite3.Row, listed: bool = True,
                drawn: bool = True, brief: bool = False) -> str:
    """The graph as `bounded` draws it, then the ready nodes. `listed`: when it had to be
    cut, also every node on a line of its own, kept within MAX_CHARS with the graph.
    `drawn=False`: the header line alone in place of the drawing. `brief`: the header
    and the ready nodes' `do`, without the drawing, pitfalls or failure tails."""
    t = parse(task["graph"])
    rows = store.node_rows(conn, task["id"])
    view = _head(task) if brief or not drawn else bounded(task, t, rows)
    if drawn and listed and not brief and view != render(task, t, rows):
        box = _boxer(t, rows, [n["id"] for n in ready(t, rows)])
        items = [box(n["id"]) + (f" ◄─ {', '.join(n['after'])}" if n["after"] else "")
                 for n in t["nodes"]]
        listing = [_clip(x) for x in items]
        while listing and len("\n".join([view, i18n.t("graph.nodes_header"), *listing])) > MAX_CHARS:
            listing = (listing[:-2] + [i18n.t("graph.more_nodes", n=len(items) - len(listing) + 2)]
                       if len(listing) > 1 else [])
        if listing:
            view = "\n".join([view, i18n.t("graph.nodes_header"), *listing])
    lines = [view, f"params={task['params']} dir={task['dir']}"]
    for n in ready(t, rows):
        r = rows[n["id"]]
        kind = " (external)" if n.get("kind") else ""
        lines.append(f"- ready `{n['id']}`{kind}: {n.get('do', '')}")
        if n.get("skill"):
            called = i18n.t("graph.called" if skill_at(conn, task, t, n) else "graph.not_called")
            lines.append(i18n.t("graph.status_skill", names=" | ".join(n["skill"]), called=called))
        if n.get("agent"):
            need = i18n.t("graph.required" if n["agent_mode"] == "require" else "graph.recommended")
            called = i18n.t("graph.called" if skill_at(conn, task, t, n, "agent") else "graph.not_called")
            lines.append(i18n.t("graph.status_agent", names=" | ".join(n["agent"]), need=need,
                           called=called))
        if drawn and not brief and n["pitfalls"]:
            lines.append("  pitfalls: " + json.dumps(n["pitfalls"], ensure_ascii=False))
        if note := unset_note(t, n, json.loads(task["params"])):
            lines.append(note)
        if drawn and not brief and r["last_exit"] not in (None, 0):
            lines.append(i18n.t("graph.last_failure", code=r["last_exit"], tail=r["last_output_tail"]))
    return "\n".join(lines)


def _weakened(old: dict, new: dict) -> list[str]:
    """What an update loosens: a node removed, or its check, `next`, `after`, timeout,
    `skill`, `agent`, `protected` or `floor` changed, or it turned human; a param regex changed.
    Stronger and weaker cannot be told apart mechanically, so every such change counts and
    needs a reason."""
    news = {n["id"]: n for n in new["nodes"]}
    out = []
    for n in old["nodes"]:
        m = news.get(n["id"])
        if n["id"] == "retro":  # wf owns its check; its `after` only follows the leaves
            continue
        if m is None or _shape(n)[:3] != _shape(m)[:3] or m["timeout"] != n["timeout"] \
                or (m.get("kind") == "human" and n.get("kind") != "human") \
                or m.get("skill") != n.get("skill") or m.get("agent") != n.get("agent") \
                or m.get("agent_mode") != n.get("agent_mode") \
                or (m.get("protected"), m.get("floor")) != (n.get("protected"), n.get("floor")):
            out.append(n["id"])
    if old.get("optional_issue", False) != new.get("optional_issue", False):
        out.append("optional_issue")
    return out + [f"params.{p}" for p, rx in old["params"].items() if new["params"].get(p) != rx]


def _literals(task: sqlite3.Row, t: dict) -> list[str]:
    """Nodes whose check/when/do hardcode this task's issue number, branch or dir —
    a template must say `{param}` or `$WF_ISSUE_NUMBER`/`$WF_BRANCH`/`$WF_DIR`."""
    branch = subprocess.run(["git", "-C", task["dir"], "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    pats = [re.escape(task["dir"])]
    if store.has_issue(task):
        pats.append(rf"(?<![\w]){task['issue_url'].rsplit('/', 1)[1]}(?![\w])")
    if branch not in ("", "HEAD", "main", "master"):
        pats.append(re.escape(branch))
    hit = re.compile("|".join(pats))
    def texts(n: dict) -> list:
        return [n.get("check"), n.get("do"), *(e.get("when") for e in n["next"])]

    return [n["id"] for n in t["nodes"]
            if n["id"] != "retro" and any(hit.search(str(s)) for s in texts(n) if s)]


def structural_context(conn: sqlite3.Connection, task: sqlite3.Row) -> dict:
    """The review input. The snapshot binds only this task and its own type's body, so a
    retro commit to another type or the Stop hook's retro check, stall and the user's
    release of it don't stale it, nor does a scope_hint; other types are one
    `match` line each, not their bodies."""
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM event WHERE task = ? AND kind NOT IN ('structural_review', 'scope_hint')"
        " AND node != 'retro' AND NOT (node = '' AND kind = 'stall') ORDER BY rowid",
        (task["id"],))]
    lib = library.root(seed=False)
    own = [f"{task['type']}.yaml", f"candidates/{task['type']}.yaml"]
    context = {"task": task["id"], "type": task["type"],
               "dir": task["dir"],
               "graph": task["graph"], "params": task["params"],
               "version": task["version"],
               "prior_changes": library.prior_changes(conn, task),
               "library_bodies": {f: (lib / f).read_text() for f in own
                                  if (lib / f).is_file()},
               "nodes": [dict(r) for n, r in store.node_rows(conn, task["id"]).items()
                         if n != "retro"],
               "events": events}
    context["snapshot"] = hashlib.sha256(json.dumps(
        context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    context["state"] = task["state"]
    context["library_types"] = {p.stem: (yaml.safe_load(p.read_text()) or {}).get("match", "")
                                for p in sorted(lib.glob("*.yaml"))}
    return context


def _reuse_body(task: sqlite3.Row, review: dict) -> str | dict | list:  # noqa: C901
    """The selected graph; `"default"` is the type's library default, so a reviewer keeping
    it need not retype the whole graph. `{"base": "default", "nodes": {id: fields |
    null}, "params": {name: regex | null}}` changes only the named nodes and params of it:
    fields merge, a null removes, a new node goes before `retro`, and the nodes are then
    stably reordered so each follows its `after`. The snapshot covers that body, so a library
    change between review and verdict is refused as stale."""
    reuse = review["reuse_graph"]
    patch = isinstance(reuse, dict) and "base" in reuse
    if reuse != "default" and not patch:
        return reuse
    if task["type"] == "new" or (found := library.body(task["type"])) is None:
        raise WfError(i18n.t("graph.reuse_default_only"))
    if not patch:
        return found[0]
    if not set(reuse) <= {"base", "nodes", "params"} or reuse["base"] != "default" or not all(
            isinstance(reuse.get(k, {}), dict) for k in ("nodes", "params")):
        raise WfError(i18n.t("graph.reuse_patch_shape"))
    default = load(found[0], strict=False)
    nodes = {n["id"]: n for n in default.get("nodes") or []}
    params = dict(default.get("params") or {})
    for nid, fields in reuse.get("nodes", {}).items():
        if fields is not None and not isinstance(fields, dict) or (
                fields is None and nid not in nodes):
            raise WfError(i18n.t("graph.reuse_patch_node", nid=repr(nid)))
        if fields is None:
            del nodes[nid]
            continue
        if nid not in nodes and "retro" in nodes:  # a new node goes before `retro`
            nodes = {k: v for k, v in nodes.items() if k != "retro"} | {
                nid: {}, "retro": nodes["retro"]}
        merged = {**nodes.get(nid, {}), **fields, "id": nid}
        nodes[nid] = {k: v for k, v in merged.items() if v is not None}
    for name, rx in reuse.get("params", {}).items():
        if rx is None and name not in params:
            raise WfError(i18n.t("graph.reuse_patch_param", name=repr(name)))
        if rx is None:
            del params[name]
        else:
            params[name] = rx
    # Stable: a node moves only when its `after`, or a forward `next` into it, forces it.
    pos = {nid: i for i, nid in enumerate(nodes)}
    deps: dict[str, set] = {nid: set() for nid in nodes}
    for nid, n in nodes.items():
        after = n.get("after") or []
        if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
            raise WfError(i18n.t("graph.reuse_patch_after", nid=repr(nid)))
        deps[nid] |= set(after) & set(nodes)  # an unknown name is left for parse to report
        for e in n.get("next") or []:
            if isinstance(e, dict) and pos.get(e.get("to"), -1) > pos[nid]:
                deps[e["to"]].add(nid)
    ordered: list[str] = []
    while len(ordered) < len(nodes):
        nxt = next((nid for nid in nodes if nid not in ordered and deps[nid] <= set(ordered)),
                   None)
        if nxt is None:
            raise WfError(i18n.t("graph.reuse_patch_cycle",
                            ids=[nid for nid in nodes if nid not in ordered]))
        ordered.append(nxt)
    optional = {"optional_issue": default["optional_issue"]} if "optional_issue" in default else {}
    return {"nodes": [nodes[nid] for nid in ordered], "params": params, **optional}


def structural_ack(conn: sqlite3.Connection, task: sqlite3.Row, review: dict) -> dict:  # noqa: C901
    """Validate a submission; only a successful independent hook records its receipt."""
    context = structural_context(conn, task)
    if not isinstance(review, dict) or set(review) != {
            "task", "snapshot", "axes", "findings", "reuse_graph", "adopted", "prior_changes"}:
        raise WfError("structural_review needs task, snapshot, axes, findings,"
                      " reuse_graph, adopted, prior_changes")
    if type(review["task"]) is not int or review["task"] != task["id"] or (
            review["snapshot"] != context["snapshot"]):
        raise WfError("structural_review task/snapshot is stale or belongs to another task")
    axes = review["axes"]
    if not isinstance(axes, dict) or set(axes) != {"order", "duplication", "missing", "rework"} \
            or not all(isinstance(v, str) and v.strip() for v in axes.values()):
        raise WfError("structural_review needs evidence on all four axes")
    prior = review["prior_changes"]
    if not isinstance(prior, list):
        raise WfError("structural_review prior_changes must be a list")
    seen = set()
    for change in prior:
        if not isinstance(change, dict) or set(change) != {"node", "verdict", "evidence"} \
                or not isinstance(change["node"], str) or change["node"] in seen \
                or change["verdict"] not in ("helped", "hurt", "no observed effect", "confounded") \
                or not isinstance(change["evidence"], str) or not change["evidence"].strip():
            raise WfError("structural_review prior_changes needs unique node, verdict "
                          "(helped/hurt/no observed effect/confounded), non-empty evidence")
        seen.add(change["node"])
    if seen != set(context["prior_changes"]):
        raise WfError("structural_review prior_changes nodes must match "
                      "context prior_changes exactly")
    findings = review["findings"]
    if not isinstance(findings, list):
        raise WfError("structural_review findings must be a list")
    ids = set()
    for f in findings:
        if not isinstance(f, dict) or set(f) != {"id", "axis", "evidence", "proposal"} \
                or not all(isinstance(v, str) and v.strip() for v in f.values()) \
                or f["axis"] not in axes or f["id"] in ids:
            raise WfError("structural_review finding needs unique id, axis, evidence, proposal")
        ids.add(f["id"])
    adopted = review["adopted"]
    if not isinstance(adopted, list) or not all(isinstance(i, str) and i in ids for i in adopted) \
            or len(adopted) != len(set(adopted)):
        raise WfError("structural_review adopted must name unique finding IDs")
    submitted(deepcopy(_reuse_body(task, review)), task["type"],
              parse(task["graph"])["match"], strict=False)
    digest = hashlib.sha256(json.dumps(review, sort_keys=True, ensure_ascii=False).encode())
    return {"status": "accepted", "task": task["id"], "snapshot": context["snapshot"],
            "review_id": digest.hexdigest()}


def structural_receipt(conn: sqlite3.Connection, task: sqlite3.Row, review_id: str) -> dict:
    for row in conn.execute("SELECT detail FROM event WHERE task = ?"
                            " AND kind = 'structural_review' ORDER BY rowid DESC", (task["id"],)):
        receipt = json.loads(row[0])
        if receipt["review_id"] == review_id and receipt.get("agent_id"):
            ack = structural_ack(conn, task, receipt["review"])
            if ack["review_id"] == review_id:
                return receipt["review"]
    raise WfError("successful independent structural_review hook receipt required")


def _protected_changes(old: dict, new: dict) -> list[str]:
    """Keep gates and their downstream actions; moving a gate earlier is safe."""
    news = {n["id"]: n for n in new["nodes"]}
    old_ids = {n["id"] for n in old["nodes"]}
    changed = [n["id"] for n in new["nodes"] if n["id"] not in old_ids and n["next"]]
    if not (descendants(new, "retro") - {"retro"}) <= descendants(old, "retro"):
        changed.append("retro")
    for n in old["nodes"]:
        if n["id"] == "retro":
            continue
        m = news.get(n["id"])
        fields = ("do", "check", "skill", "kind", "timeout", "on_fail", "on_fail_max", "next",
                  "protected", "floor")
        if n.get("agent_mode") == "require":
            fields += ("agent", "agent_mode")
        if m is None or any(m.get(k) != n.get(k) for k in fields) or (
                (n.get("check") or n.get("skill") or n.get("kind") == "human") and not (
                    descendants(old, n["id"]) - {"retro"}) <= descendants(new, n["id"])):
            changed.append(n["id"])
    if old.get("optional_issue", False) != new.get("optional_issue", False):
        changed.append("optional_issue")
    return changed + [f"params.{p}" for p, rx in old["params"].items()
                      if new["params"].get(p) != rx]


def _reuse_shape(t: dict) -> tuple:
    return ([(n["id"], n.get("do"), n["pitfalls"], _shape(n)) for n in t["nodes"]],
            t["params"], t.get("optional_issue", False))


def record_retro(conn: sqlite3.Connection, task: sqlite3.Row, verdict: str, deviations: str,  # noqa: C901
                 name: str | None = None, match: str | None = None,
                 graph: str | dict | list | None = None, reason: str | None = None,
                 applied: list[str] | None = None, review_id: str | None = None,
                 dismissed: dict[str, str] | None = None) -> str:
    if verdict not in VERDICTS:
        raise WfError(i18n.t("graph.verdict_choice", choices=" | ".join(VERDICTS)))
    if not deviations.strip():
        raise WfError(i18n.t("graph.deviations_needed"))
    if conn.in_transaction:
        raise WfError("retro requires an autocommit connection; caller transaction is active")
    task = store.get_task(conn, task["id"])
    if not review_id:
        raise WfError("successful independent structural_review hook receipt required")
    review = structural_receipt(conn, task, review_id)
    t = parse(task["graph"])
    after_retro = descendants(t, "retro")
    if open_ := [n["id"] for n in pending(t, store.node_rows(conn, task["id"]))
                 if n["id"] not in after_retro]:
        raise WfError(i18n.t("graph.retro_open", ids=open_))
    if verdict == "no_change" and not (reason or "").strip():
        raise WfError(i18n.t("graph.no_change_reason"))
    reuse = deepcopy(load(_reuse_body(task, review), strict=False))
    selected = parse(submitted(deepcopy(reuse), task["type"], t["match"], strict=False))
    if graph is not None and _reuse_shape(selected) != _reuse_shape(parse(submitted(
            deepcopy(graph), task["type"], t["match"], strict=False))):
        raise WfError("final graph differs from receipt-bound reuse_graph")
    if verdict == "no_change" and review["adopted"]:
        raise WfError(i18n.t("graph.adopted_no_change"))

    dismissed = dismissed or {}

    def reviewed(p: str) -> bool:
        """A dismissed finding names this protected item: no_change may drop it."""
        # `params.replay` and bare `replay` both name it; `replay.extra` does not.
        # ASCII: a Korean particle right after the name is not part of it, and `ı`/`İ`
        # must not fold onto `i`.
        named = "|".join(rf"(?<![\w.-]){re.escape(f)}(?![\w.-])"
                         for f in sorted({p, p.removeprefix("params.")}))
        return any(f["id"] in dismissed and re.search(
            named, f"{f['evidence']} {f['proposal']}", re.I | re.ASCII)
            for f in review["findings"])
    if set(applied or []) & set(dismissed):
        raise WfError("findings cannot be both applied and dismissed")
    ids = {f["id"] for f in review["findings"]}
    if set(applied or []) & ids != set(review["adopted"]):
        raise WfError("applied cannot replace the review adopted selection")
    if not isinstance(dismissed, dict) or not set(dismissed) <= ids or not all(
            isinstance(r, str) and r.strip() for r in dismissed.values()):
        raise WfError("dismissed needs a reason for each rejected finding")
    unaddressed = ids - set(applied or []) - set(dismissed)
    if unaddressed or (verdict == "no_change" and ids - set(dismissed)):
        raise WfError("structural findings must be applied or dismissed with reasons")
    deviations += i18n.t("graph.dev_review", id=review_id)
    if verdict == "no_change":
        deviations += i18n.t("graph.dev_keep", reason=reason.strip())
    if dismissed:
        deviations += i18n.t("graph.dev_dismissed", dismissed=json.dumps(dismissed, ensure_ascii=False))
    if verdict == "no_change" and task["type"] == "new":
        raise WfError(i18n.t("graph.new_must_save"))
    if verdict in {"no_change", "update_type"} and task["type"] != "new" and (
            name is not None and name != task["type"]):
        raise WfError("reviewed reusable graph must target this task's type")
    if verdict == "no_change":
        name = name or task["type"]
        found = library.body(name)
        if found is None or _reuse_shape(selected) != _reuse_shape(parse(found[0])):
            raise WfError("no_change requires the exact reviewed reusable default graph")
    if verdict != "no_change":
        source = deepcopy(reuse)
        if verdict == "new_type":
            if not (name and re.fullmatch(r"[a-z0-9-]+", name) and match):
                raise WfError(i18n.t("graph.new_type_fields"))
            if name == "new" or library.body(name):
                raise WfError(i18n.t("graph.type_exists", name=repr(name)))
            seen = None
            body = submitted(source, name, match)
            if hits := library.similar(conn, f"{match}\n{task['title']}", exclude=task["id"]):
                raise WfError(i18n.t("graph.similar_exists", hits=", ".join(hits), first=repr(hits[0])))
            message = f"wf retro new_type {name}: {task['issue_url']}"
        else:
            name = name or task["type"]
            found = library.body(name)
            if found is None:
                raise WfError(i18n.t("graph.type_missing", name=repr(name)))
            seen = found[0]
            old = parse(found[0])
            body = submitted(source, name, match or old["match"])
            if _reuse_shape(parse(body)) == _reuse_shape(old):
                if review["adopted"] or task["type"] != "new":
                    raise WfError(i18n.t("graph.semantic_noop"))
                body = found[0]
            weakened = _weakened(old, parse(body))
            if weakened and not (reason or "").strip():
                raise WfError(i18n.t("graph.weakened_reason", ids=weakened))
            message = f"wf retro update_type {name}: {task['issue_url']}"
            message += i18n.t("graph.msg_saved")
            if weakened:
                message += i18n.t("graph.msg_check_change", ids=weakened, reason=reason)
        if hardcoded := _literals(task, parse(body)):
            raise WfError(i18n.t("graph.hardcoded", ids=hardcoded))
        message += i18n.t("graph.msg_deviations", deviations=deviations)
        if applied:
            message += i18n.t("graph.msg_applied", applied="; ".join(applied))
    owned = False

    def before_write() -> None:
        nonlocal owned
        conn.execute("BEGIN IMMEDIATE")
        owned = True
        try:
            current = store.get_task(conn, task["id"])
            structural_receipt(conn, current, review_id)
            # The snapshot binds only the task's own type: re-read the file this
            # verdict writes, so a concurrent retro to another type is not overwritten.
            if verdict != "no_change":
                target = library.root(seed=False) / f"{name}.yaml"
                if (target.read_text(encoding="utf-8") if target.is_file() else None) != seen:
                    raise WfError(i18n.t("graph.type_stale", name=repr(name)))
            # A reviewed update_type/new_type saves gate changes without approval;
            # no_change may still not drop a gate the task added.
            if verdict == "no_change":
                base = (library.root(seed=False) / f"{name}.yaml").read_text()
                if _reuse_shape(selected) != _reuse_shape(parse(base)):
                    raise WfError("no_change requires the exact reviewed reusable default graph")
                cur = parse(current["graph"])
                ran = {n["id"]: n for n in cur["nodes"]}
                kept = {n["id"]: n for n in selected["nodes"]}
                protected = [p for p in _protected_changes(cur, selected)
                             if p not in kept or any(ran.get(p, {}).get(k) and not kept[p].get(k)
                                                     for k in ("check", "skill"))]
                if undismissed := [p for p in protected if not reviewed(p)]:
                    raise WfError(i18n.t("graph.protected", ids=undismissed))
        except Exception:
            conn.rollback()
            raise

    def after_write(sha: str | None) -> None:
        with conn:
            if verdict != "no_change" and (verdict == "new_type" or task["type"] == "new"):
                store.update_task(conn, task["id"], type=name)
            store.set_retro(conn, task["id"], deviations, verdict, sha)

    changes = {f"candidates/{name}.yaml": None}
    if verdict != "no_change":
        changes[f"{name}.yaml"] = body
    try:
        sha = library._write(changes, "wf retro no_change" if verdict == "no_change" else message,
                             before_write=before_write, after_write=after_write)
    finally:
        if owned and conn.in_transaction:
            conn.rollback()
    diff = library.show(sha) if sha else i18n.t("graph.no_library_change")
    if not store.has_issue(task):
        return i18n.t("graph.retro_no_issue", verdict=verdict, diff=diff)
    return i18n.t("graph.retro_issue", verdict=verdict, diff=diff)
