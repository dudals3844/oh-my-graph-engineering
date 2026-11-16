"""Run a node's `check` — the only thing that can mark a non-human node done.

Completion is decided by running the command, never by the agent saying so.
"""

from __future__ import annotations

import ast
import json
import os
import re
import signal
import sqlite3
import subprocess
import time

from . import graph, i18n, store

TAIL_CHARS = 2000
#: The Stop signature of a task stalled by `wf_fail` running out of `on_fail_max`: Stop
#: stays quiet on it like on an announced stall, until the user or progress lifts it.
EXHAUSTED = "wf_fail: on_fail_max"
#: A check exits 75 when it waits on something external (CI queued/running) — not a failure.
WAIT_EXIT = 75
#: After SIGKILL to the group, how long to wait for the pipe to drain.
DRAIN_SECONDS = 2
#: A type's clean tasks still get a retro this often, to catch slow drift.
RETRO_EVERY = 10


def normalise(line: str) -> str:
    """Strip what changes between identical failures, so "same error again" is
    recognised: tmp paths, timestamps, hex ids, then any remaining number."""
    line = re.sub(r"/tmp/\S*", "/tmp/<tmp>", line)
    line = re.sub(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?\S*", "<time>", line)
    line = re.sub(r"\d\d:\d\d:\d\d(\.\d+)?", "<time>", line)
    line = re.sub(r"\b(0x)?[0-9a-f]{7,}\b", "<hex>", line)
    return re.sub(r"\d+", "<n>", line).strip()


def first_error_line(tail: str) -> str:
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    hit = next((ln for ln in lines if re.search(r"error|fail|traceback|exception", ln, re.I)),
               None)
    return hit or (lines[-1] if lines else "")


def render(check: str, params: dict[str, str],
           patterns: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Replace `{param}` with `${WF_P_param+"${WF_P_param}"}` and return the values as
    that env.

    The value never enters shell source: bash expands it but never re-parses the
    result, so it stays data. The `+` form is one quoted word both bare and inside
    "..." (a plain `"${X}"` there would close the outer quotes and split), in `$(...)`
    and a heredoc. The regex is the content contract (a `pkg_dir` must look like a
    package dir). PLACEHOLDER only matches identifiers, so every name is a valid
    variable name.
    """
    env = {}

    def sub(m: re.Match) -> str:
        name = m[1]
        value = params.get(name, "")
        # A param whose regex takes "" is an optional setting; any other must be set.
        if not value and not re.fullmatch(patterns[name], ""):
            raise graph.WfError(i18n.t("checks.param_unset", name=name))
        if not re.fullmatch(patterns[name], value):
            raise graph.WfError(i18n.t("checks.param_mismatch", name=name, value=repr(value),
                                        pattern=repr(patterns[name])))
        env[f"WF_P_{name}"] = value
        return f'${{WF_P_{name}+"${{WF_P_{name}}}"}}'

    return graph.PLACEHOLDER.sub(sub, check), env


def run(cmd: str, cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str]:
    # Own process group: `subprocess.run(timeout=)` kills only bash, and a
    # grandchild (pytest, gh) still holding the pipe makes the follow-up
    # `communicate()` wait forever — the timeout would not bound anything.
    try:
        proc = subprocess.Popen(
            ["bash", "-c", cmd], cwd=cwd, env={**os.environ, **env}, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
    except OSError:  # the task dir is gone: fail (and stall) like any check, never crash
        return 127, i18n.t("checks.no_dir", cwd=cwd)
    try:
        out, _ = proc.communicate(timeout=max(timeout, 0.1))
        code = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        try:
            out, _ = proc.communicate(timeout=DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            # Something left the group (`setsid`) and still holds the pipe.
            proc.stdout.close()
            proc.wait()
            out = b""
        code, out = 124, out + f"\n[omge:wf] timeout after {timeout:.0f}s".encode()
    return code, out.decode(errors="replace")[-TAIL_CHARS:]


def _env(task: sqlite3.Row, node: dict | None = None) -> dict[str, str]:
    branch = subprocess.run(
        ["git", "-C", task["dir"], "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not branch or branch in ("main", "master"):
        conn = store.connect()
        try:
            rows = conn.execute(
                "SELECT detail FROM event WHERE task = ? AND kind = 'git_identity'"
                " ORDER BY rowid DESC", (task["id"],)).fetchall()
        finally:
            conn.close()
        for row in rows:
            recorded = (json.loads(row[0]) or {}).get("branch", "") if row[0] else ""
            if recorded and recorded not in ("main", "master"):
                branch = recorded
                break
    # GitHub's `created_at` format, so a check can compare it as a string.
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(task["created"]))
    issue = task["issue_url"] if store.has_issue(task) else ""  # empty with no issue
    params = json.loads(task["params"])
    pattern = graph.parse(task["graph"])["params"].get("pr", "")
    pr = params.get("pr", "")
    pr = pr if pattern and re.fullmatch(pattern, pr) and re.fullmatch(r"[1-9][0-9]*", pr) else ""
    ready = task["created"]
    if node:
        t = graph.parse(task["graph"])
        conn = store.connect()
        try:
            ready = graph.ready_since(conn, task, t, node["id"])
        finally:
            conn.close()
    return {"WF_BRANCH": branch, "WF_DIR": task["dir"], "WF_ISSUE": issue,
            "WF_ISSUE_NUMBER": issue.rsplit("/", 1)[-1], "WF_SINCE": since,
            "WF_TASK_ID": str(task["id"]), "WF_PR": pr,
            "WF_READY_SINCE": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ready))}


def _uses(nodes: list[dict]) -> set[str]:
    """Params the checks and `when`s of `nodes` read."""
    shells = [sh for n in nodes for sh in [n.get("check"), *(e.get("when") for e in n["next"])]]
    return {p for sh in shells if sh for p in graph.PLACEHOLDER.findall(sh)}


def close(conn: sqlite3.Connection, task: sqlite3.Row, node_id: str, *, status: str,  # noqa: C901
          done_by: str, evidence: str, deadline: float | None = None,
          route: str | None = None, head: str = "") -> None:
    """Close a node and take its route. A `done` decision node runs its `when`s top
    down and follows the first that exits 0 (no `when` = else); none matching, or a
    loop out of turns, raises and the node stays pending. A waived decision takes the
    forward `route` the user named."""
    t = graph.parse(task["graph"])
    node = next(n for n in t["nodes"] if n["id"] == node_id)
    if status == "waived" and node.get("floor"):
        raise graph.WfError(i18n.t("checks.floor_waive", node=node_id))
    if status == "waived":  # agent `wf_waive` and user `wf waive` both stand on the receipts
        receipts = graph.review_receipts(conn, task, node_id,
                                         graph.ready_since(conn, task, t, node_id))
        if any(r["verdict"] == "blocking" for r in receipts.values()):
            raise graph.WfError(i18n.t("checks.review_blocking"))
        if node.get("protected") and not any(
                r["verdict"] == "pass" and r["sha"] and r["sha"] == r.get("head")
                == _head(task["dir"]) for r in receipts.values()):
            raise graph.WfError(i18n.t("checks.protected_waive", node=node_id))
    if stale := _floor_stale(conn, task, t, node_id):
        raise graph.WfError(i18n.t("checks.floor_stale", node=node_id, floor=stale))
    tid = task["id"]
    edge = back = None
    forward = [e for e in node["next"] if not graph.is_back(t, node_id, e["to"])]
    if status == "done" and node["next"]:
        params = json.loads(task["params"])

        def limit() -> float:
            left = node["timeout"] if deadline is None else deadline - time.monotonic()
            return min(node["timeout"], left)

        edge = None
        for e in node["next"]:
            if not e.get("when"):
                edge = e
                break
            if limit() <= 0:
                continue  # the budget is spent: this `when` cannot match
            cmd, values = render(e["when"], params, t["params"])
            if run(cmd, task["dir"], {**_env(task, node), **values}, limit())[0] == 0:
                edge = e
                break
        if edge is None:
            raise graph.WfError(i18n.t("checks.no_when", node=node_id))
        back = graph.is_back(t, node_id, edge["to"])
    elif forward:
        edge = next((e for e in forward if e["to"] == route), None)
        if edge is None:
            raise graph.WfError(i18n.t("checks.branch_route", node=node_id, to=forward[0]["to"],
                                       options=", ".join(e["to"] for e in forward)))
    head = head if head and head == _head(task["dir"]) else ""  # moved under the check: no receipt
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        # Re-read under the lock: the `when`s ran outside it, for minutes perhaps.
        rows = store.node_rows(conn, tid)
        if rows[node_id]["status"] != "pending":
            raise graph.WfError(i18n.t("checks.already_closed", node=node_id))
        if back and store.loops(conn, tid, node_id, edge["to"]) >= edge["max"]:
            raise graph.WfError(i18n.t("checks.loop_max", node=node_id, to=edge["to"],
                                       max=edge["max"]))
        store.set_node(conn, tid, node_id, status=status, done_by=done_by, evidence=evidence,
                       route=edge and edge["to"])
        store.event(conn, tid, node_id, "done" if status == "done" else "waive", evidence)
        # The e2e task a passing check cited is a fixture opened after this task; dropping
        # it needs no user. An older task is never a fixture.
        e2e = json.loads(task["params"]).get("e2e_task", "")
        if status == "done" and e2e.isdigit() and "{e2e_task}" in (node.get("check") or ""):
            fixture = store.get_task(conn, int(e2e))
            if fixture and fixture["created"] > task["created"] \
                    and fixture["state"] in store.OPEN_STATES:
                store.set_state(conn, fixture["id"], "dropped")
                store.event(conn, fixture["id"], "", "drop",
                            i18n.t("checks.e2e_drop", tid=tid, node=node_id))
        if head:
            store.event(conn, tid, node_id, "head", head)
        store.progress(conn, tid)
        if edge:
            store.event(conn, tid, node_id, "route", edge["to"])
        if back:
            _go_back(conn, t, tid, node_id, edge["to"])
        _settle(conn, t, tid)


def _check_cmd(conn: sqlite3.Connection, task: sqlite3.Row, t: dict, node: dict
               ) -> tuple[str, dict[str, str]]:
    """`node`'s check rendered, and the env it runs in. Raises on an unset param."""
    cmd, values = render(node["check"], json.loads(task["params"]), t["params"])
    env = {**_env(task, node), **values}
    if node.get("skill") and (at := graph.skill_at(conn, task, t, node)):
        # GitHub's `created_at` format: a receipt must postdate the call
        env["WF_SKILL_AT"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
    return cmd, env


def _floor_stale(conn: sqlite3.Connection, task: sqlite3.Row, t: dict, node_id: str) -> str | None:
    """The first `floor` ancestor of `node_id` that is not done, or whose check fails when run
    again now (then reopened with everything after it). A stored pass is never trusted; the
    reopen is no loop, so it spends no edge's `max`."""
    rows = store.node_rows(conn, task["id"])
    for f in t["nodes"]:
        if not f.get("floor") or f["id"] == node_id or node_id not in graph.descendants(t, f["id"]):
            continue
        if rows[f["id"]]["status"] != "done":  # pending, or skipped by a route: never measured
            return f["id"]
        try:
            cmd, env = _check_cmd(conn, task, t, f)
        except graph.WfError:
            cmd = None  # an unset param: the check cannot pass
        if cmd and run(cmd, task["dir"], env, f["timeout"])[0] == 0:
            continue
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            for nid in graph.descendants(t, f["id"]):
                store.set_node(conn, task["id"], nid, status="pending", route=None)
            store.event(conn, task["id"], f["id"], "reopen", f["id"])
        return f["id"]
    return None


def _go_back(conn: sqlite3.Connection, t: dict, tid: int, src: str, to: str) -> None:
    """Reopen `to` and everything after it, one more loop on `to`. Inside the caller's
    transaction."""
    reset = graph.descendants(t, to)
    for nid in reset:
        store.set_node(conn, tid, nid, status="pending", route=None)
    conn.execute("UPDATE node_state SET loops = loops + 1 WHERE task = ? AND node = ?",
                 (tid, to))
    store.event(conn, tid, src, "back", to)
    # A param only the redone part reads (the PR number, the run id) belongs
    # to the attempt that failed; keeping it would pass the redo on old proof.
    # An optional param (its regex takes "") is a setting, not proof: it stays.
    stale = {p for p in _uses([n for n in t["nodes"] if n["id"] in reset]) - _uses(
        [n for n in t["nodes"] if n["id"] not in reset]) if not re.fullmatch(t["params"][p], "")}
    params = json.loads(store.get_task(conn, tid)["params"])
    store.update_task(conn, tid, params={k: v for k, v in params.items() if k not in stale})


def _settle(conn: sqlite3.Connection, t: dict, tid: int) -> None:
    """Skip what no route reaches any more; a task with nothing pending is done."""
    rows = store.node_rows(conn, tid)
    targets = {n["id"]: {e["to"] for e in n["next"]} for n in t["nodes"]}
    skipped: set[str] = set()
    # A predecessor is dead to `nid` if skipped, or if it routed elsewhere. One
    # pass in file order is transitive: `after` only names earlier nodes.
    for n in t["nodes"]:
        nid = n["id"]
        dead = [p for p in n["after"] if p in skipped or rows[p]["status"] == "skipped"
                or (rows[p]["route"] and rows[p]["route"] != nid and nid in targets[p])]
        if rows[nid]["status"] == "pending" and n["after"] and len(dead) == len(n["after"]):
            skipped.add(nid)
            store.set_node(conn, tid, nid, status="skipped")
            store.event(conn, tid, nid, "skip")
    rows = store.node_rows(conn, tid)
    # A `floor` graph's retro closes only through `close` or `skip_retro`, which re-run it.
    if any(n["id"] == "retro" for n in graph.ready(t, rows)) \
            and not any(n.get("floor") for n in t["nodes"]) and (why := _retro_idle(conn, tid)):
        store.set_node(conn, tid, "retro", status="done", done_by="wf", evidence=why)
        store.event(conn, tid, "retro", "done", why)
    if not graph.pending(t, store.node_rows(conn, tid)):
        store.set_state(conn, tid, "done")


def _retro_idle(conn: sqlite3.Connection, tid: int) -> str | None:
    """Why this task's retro has nothing to improve, or None when it must run:
    a revise, waiver or stall; a node failed by `wf_fail` twice; a `new` graph; or every
    RETRO_EVERY-th clean task of the type since the last. One `wf_fail` is not a signal:
    its retros ended `no_change`. Nor is a check's own `fail` (mostly waits on CI
    or a merge), a route's loop, or being the first task on a new version."""
    task = store.get_task(conn, tid)
    if task["type"] == "new" or conn.execute(
            "SELECT 1 FROM event WHERE task = ? AND kind IN ('revise', 'waive', 'stall')"
            " UNION ALL SELECT 1 FROM event WHERE task = ? AND kind = 'wf_fail'"
            " GROUP BY node HAVING count(*) >= 2", (tid, tid)).fetchone():
        return None
    clean = conn.execute(
        "SELECT count(*) FROM task WHERE type = ? AND state = 'done' AND id != ? AND id >"
        " (SELECT coalesce(max(t.id), 0) FROM task t JOIN retro r ON r.task = t.id"
        "  WHERE t.type = ?)", (task["type"], tid, task["type"])).fetchone()[0]
    if clean + 1 >= RETRO_EVERY:
        return None
    return i18n.t("checks.retro_idle", count=clean + 1, every=RETRO_EVERY)


def _same_revise_again(conn: sqlite3.Connection, task: sqlite3.Row) -> tuple[int, set] | None:
    """Another task of this type hand-revised the same node set into its graph. That
    repetition is what a retro's update_type folds into the type's default once; a
    skip on top of it leaves the default broken for the next task."""
    added = {r[0] for r in conn.execute(
        "SELECT node FROM event WHERE task=? AND kind='revise_add'", (task["id"],))}
    if not added:
        return None
    for (tid,) in conn.execute(
        "SELECT DISTINCT e.task FROM event e JOIN task t ON t.id=e.task"
        " WHERE t.type=? AND e.task<>? AND e.kind='revise_add'", (task["type"], task["id"])):
        prev = {r[0] for r in conn.execute(
            "SELECT node FROM event WHERE task=? AND kind='revise_add'", (tid,))}
        if prev == added:
            return tid, added
    return None


def skip_retro(conn: sqlite3.Connection, task: sqlite3.Row, reason: str) -> None:
    """The agent's call that this retro has nothing to improve. It closes retro as
    `_retro_idle` does and records no retro, so the RETRO_EVERY count goes on. Waiving
    any other node stays the user's."""
    if not (reason or "").strip():
        raise graph.WfError(i18n.t("checks.skip_reason"))
    if (again := _same_revise_again(conn, task)) is not None:
        tid, added = again
        nodes = ", +".join(sorted(added))
        raise graph.WfError(i18n.t("checks.skip_again", tid=tid, nodes=nodes))
    t = graph.parse(task["graph"])
    tid = task["id"]
    if stale := _floor_stale(conn, task, t, "retro"):
        raise graph.WfError(i18n.t("checks.floor_stale", node="retro", floor=stale))
    why = i18n.t("checks.skipped", reason=reason.strip())
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not any(n["id"] == "retro" for n in graph.ready(t, store.node_rows(conn, tid))):
            raise graph.WfError(i18n.t("checks.retro_not_ready"))
        store.set_node(conn, tid, "retro", status="done", done_by="agent", evidence=why)
        store.event(conn, tid, "retro", "done", why)
        _settle(conn, t, tid)


def fail(conn: sqlite3.Connection, task: sqlite3.Row, node_id: str, reason: str) -> str:  # noqa: C901
    """The agent declares a ready node failed and takes its `on_fail`: `retry` records it
    and stays; a node id reopens that node and everything after it. Out of
    `on_fail_max` turns the task stalls — the user decides."""
    t = graph.parse(task["graph"])
    tid = task["id"]
    node = next((n for n in graph.ready(t, store.node_rows(conn, tid)) if n["id"] == node_id),
                None)
    if node is None:
        raise graph.WfError(i18n.t("checks.not_ready", node=node_id))
    if "on_fail" not in node:
        raise graph.WfError(i18n.t("checks.no_on_fail", node=node_id))
    if not reason.strip():
        raise graph.WfError(i18n.t("checks.fail_reason"))
    # A passing check is not a failure to go back on. A human node's check is the
    # external outcome (merged); a changes-requested review fails it without failing it.
    if node.get("check") and node.get("kind") != "human":
        try:
            cmd, values = render(node["check"], json.loads(task["params"]), t["params"])
        except graph.WfError:
            cmd = None  # an unset param: the check cannot pass
        if cmd and run(cmd, task["dir"], {**_env(task, node), **values},
                       node["timeout"])[0] == 0:
            raise graph.WfError(i18n.t("checks.check_passes", node=node_id))
    to, limit = node["on_fail"], node["on_fail_max"]
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = store.node_rows(conn, tid)
        if rows[node_id]["status"] != "pending":
            raise graph.WfError(i18n.t("checks.already_closed", node=node_id))
        store.event(conn, tid, node_id, "wf_fail", f"{to}: {reason.strip()}")
        if to == graph.RETRY:
            done = store.retries(conn, tid, node_id) - 1  # this call's wf_fail is already in
            exhausted = done >= limit
            if not exhausted:
                store.event(conn, tid, node_id, "fail", reason.strip())
                return i18n.t("checks.retry_recorded", node=node_id)
        else:
            done = store.loops(conn, tid, node_id, to)
            exhausted = done >= limit
        if not exhausted:
            _go_back(conn, t, tid, node_id, to)
            _settle(conn, t, tid)
    if exhausted:
        store.set_state(conn, tid, "stalled")
        store.event(conn, tid, node_id, "stall", EXHAUSTED)
        store.set_stop_streak(conn, f"task:{tid}", 1, EXHAUSTED)
        raise graph.WfError(i18n.t("checks.fail_exhausted", node=node_id, to=to, limit=limit,
                                   tid=tid))
    return i18n.t("checks.fail_back", node=node_id, to=to, count=done + 1, limit=limit)


def attempt(  # noqa: C901
    conn: sqlite3.Connection, task: sqlite3.Row, node: dict, deadline: float,
    evidence: str = "check passed",
) -> tuple[int, str]:
    """Run one node's check against `deadline` (monotonic). Records the attempt."""
    t = graph.parse(task["graph"])
    begun = time.monotonic()
    remaining = deadline - begun
    done_by, head = "check", node.get("carry") and _head(task["dir"])
    if carried := _carried(conn, task, node):
        code, tail, done_by, evidence = 0, "", "wf", carried
    elif missing := _unreceipted(conn, task, t, node):
        code, tail = 1, missing
    elif node["id"] == "retro" and store.get_retro(conn, task["id"]) is None:
        code, tail = 1, i18n.t("checks.no_retro")
    elif node["id"] == "retro" and not store.has_issue(task):
        code, tail = 0, ""  # no issue to comment on: the record (and its commit) is the retro
    elif remaining <= 0:
        # Not run is not passed: an exhausted budget leaves the node failing.
        code, tail = 124, i18n.t("checks.unrun")
    else:
        try:
            cmd, env = _check_cmd(conn, task, t, node)
        except graph.WfError as exc:
            code, tail = 1, f"[omge:wf] {exc}"
        else:
            code, tail = run(cmd, task["dir"], env, min(node["timeout"], remaining))
    if code and not tail.strip():  # a silent failure would leave the agent no reason at all
        tail = i18n.t("checks.silent_human" if node.get("kind") == "human" else "checks.silent",
                      code=code)
    if code == 0:
        try:
            close(conn, task, node["id"], status="done", done_by=done_by, evidence=evidence,
                  deadline=deadline, head=head or "")
        except graph.WfError as exc:  # a refused route fails like a check: Stop can stall on it
            code, tail = 1, f"[omge:wf] {exc}"
    store.record_attempt(conn, task["id"], node["id"], code, tail)
    if tail != i18n.t("checks.unrun"):
        detail = normalise(first_error_line(tail)) if code else ""
        # A hook re-running an unchanged failure is not new retro evidence; a
        # WAIT_EXIT wait is not a failure at all, so it joins neither the dedup nor it.
        if code and code != WAIT_EXIT \
                and store.last_event(conn, task["id"], node["id"]) == ("fail", detail):
            return code, tail
        store.event(conn, task["id"], node["id"], "attempt", seconds=time.monotonic() - begun)
        if code and code != WAIT_EXIT:
            store.event(conn, task["id"], node["id"], "fail", detail)
    return code, tail


def _head(cwd: str) -> str:
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _carried(conn: sqlite3.Connection, task: sqlite3.Row, node: dict) -> str | None:
    """A `carry: code` node that passed at commit S in this task passes again while S..HEAD
    changes no code: a lint-only commit must not buy another review. A `wf_fail` or a
    revise of the node since S ends the carry."""
    if not node.get("carry"):
        return None
    row = conn.execute("SELECT rowid, detail FROM event WHERE task = ? AND node = ?"
                       " AND kind = 'head' ORDER BY rowid DESC LIMIT 1",
                       (task["id"], node["id"])).fetchone()
    if not row or conn.execute(
            "SELECT 1 FROM event WHERE task = ? AND node = ? AND rowid > ?"
            " AND kind IN ('wf_fail', 'revise_add', 'revise_change')",
            (task["id"], node["id"], row[0])).fetchone():
        return None
    return i18n.t("checks.carried", sha=row[1][:7]) if _same_code(task["dir"], row[1]) else None


def _same_code(cwd: str, sha: str) -> bool:
    """True when every change from `sha` to HEAD is a modified `.py` file with the same AST."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True)

    diff = git("diff", "--name-status", "--no-renames", sha, "HEAD")
    if diff.returncode:
        return False
    for line in diff.stdout.decode(errors="replace").splitlines():
        status, path = line.split("\t", 1)
        if status != "M" or not path.endswith(".py"):
            return False
        old, new = git("show", f"{sha}:{path}"), git("show", f"HEAD:{path}")
        try:
            if old.returncode or new.returncode or \
                    ast.dump(ast.parse(old.stdout)) != ast.dump(ast.parse(new.stdout)):
                return False
        except SyntaxError:
            return False
    return True


def _unreceipted(conn: sqlite3.Connection, task: sqlite3.Row, t: dict, node: dict) -> str | None:
    """Why a `skill:` or `agent_mode: require` node cannot close yet: no call recorded since it
    became ready. A check-less node is held to it too."""
    if node.get("skill") and not graph.skill_at(conn, task, t, node):
        return i18n.t("checks.no_skill", skills=" | ".join(node["skill"]))
    if node.get("agent_mode") == "require" and not graph.skill_at(conn, task, t, node, "agent"):
        return i18n.t("checks.no_agent", agents=" | ".join(node["agent"]))
    return None


def run_ready(
    conn: sqlite3.Connection, task: sqlite3.Row, deadline: float, *, human_only: bool = False
) -> tuple[dict[str, tuple[int, str]], bool]:
    """Re-run READY pending nodes with a check, to a fixpoint.

    Returns the failures and whether any node closed.

    Only ready nodes: running `pr` before `regen` passed would test things in
    the wrong order and burn the budget on checks that cannot pass yet. Looping
    lets a node made ready by a pass in this same call be checked now rather
    than block one more Stop for nothing.
    """
    t = graph.parse(task["graph"])
    failed: dict[str, tuple[int, str]] = {}
    tried: set[str] = set()
    closed = False
    while True:
        rows = store.node_rows(conn, task["id"])
        # Each node at most once per call: a loop back reopens nodes that already
        # passed here, and re-running them would spend the loop's `max` unseen.
        todo = [n for n in graph.ready(t, rows)
                if n.get("check") and n["id"] not in tried
                and (n.get("kind") == "human" or not human_only)]
        if not todo:
            return failed, closed
        for node in todo:
            tried.add(node["id"])
            task = store.get_task(conn, task["id"])  # params may have been cleared
            code, tail = attempt(conn, task, node, deadline)
            if code:
                failed[node["id"]] = (code, tail)
            closed = closed or not code


def complete(conn: sqlite3.Connection, task: sqlite3.Row, node_id: str, evidence: str) -> str:
    t = graph.parse(task["graph"])
    node = next((n for n in graph.ready(t, store.node_rows(conn, task["id"]))
                 if n["id"] == node_id), None)
    if node is None:
        raise graph.WfError(i18n.t("checks.not_ready", node=node_id))
    if not node.get("check"):
        # Nothing to run: the agent's account of what happened is the record.
        if missing := _unreceipted(conn, task, t, node):
            raise graph.WfError(missing)
        if not evidence.strip():
            raise graph.WfError(i18n.t("checks.no_check", node=node_id))
        close(conn, task, node_id, status="done", done_by="agent", evidence=evidence.strip())
        return f"`{node_id}` done (evidence)"
    code, tail = attempt(conn, task, node, time.monotonic() + node["timeout"], evidence)
    if code:
        raise graph.WfError(i18n.t("checks.check_failed", node=node_id, code=code, tail=tail))
    return f"`{node_id}` done"
