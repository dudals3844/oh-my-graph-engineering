"""The library loop: a retro commits to the local git library, the next similar
`new` is refused in favour of it, and every task's history feeds the next retro."""

from __future__ import annotations

import subprocess
import time

import pytest

from omge import graph, library, store

from .conftest import ISSUE, close, reviewed_retro, start

GRAPH = """
nodes:
  - {id: reproduce, check: 'test -d .', on_fail: retry, do: reproduce the crash}
  - {id: fix, check: 'test -d .', on_fail: reproduce, after: [reproduce]}
"""
ISSUES = {
    ISSUE: "widget crash on resize",
    ISSUE.replace("/1", "/2"): "widget crash on scroll",
    ISSUE.replace("/1", "/3"): "quarterly report wording",
}


@pytest.fixture
def titled(monkeypatch):
    monkeypatch.setattr(library, "issue_text", lambda url: (ISSUES[url], ISSUES[url] + "\nbody"))


def _log(*args: str) -> str:
    return subprocess.run(["git", "-C", str(library.root()), "log", *args], check=True,
                          capture_output=True, text=True).stdout


def _retro(tid: int, verdict: str, **kw) -> str:
    conn = store.connect()
    return reviewed_retro(conn, store.get_task(conn, tid), verdict, kw.pop("deviations", "-"),
                              **kw)


def test_the_library_gets_new_package_types_but_keeps_its_own_files(workflows):
    assert set(library.types()) == {"demo"}
    library.commit("demo", "type: demo\n# a retro's edit\n", "retro")
    (workflows / "demo.yaml").write_text("type: demo\n# the package moved on\n")
    (workflows / "late.yaml").write_text("type: late\n")
    assert set(library.types()) == {"demo", "late"}  # a reinstall brings new types in
    assert library.body("demo")[0] == "type: demo\n# a retro's edit\n"  # never overwritten
    assert _log("-1", "--format=%s").startswith("wf library seed: new types late.yaml")
    head = _log("--format=%H")
    library.types()
    assert _log("--format=%H") == head  # idempotent: nothing new, no commit


def test_new_needs_a_submitted_graph_and_ends_in_retro(workflows, repo, titled):
    with pytest.raises(graph.WfError, match="graph"):
        start(repo, type_="new")
    with pytest.raises(graph.WfError, match="nodes"):
        start(repo, type_="new", graph="nodes:\n  - {id: retro}\n")
    tid = start(repo, type_="new", graph=GRAPH)
    t = graph.parse(store.get_task(store.connect(), tid)["graph"])
    assert [n["id"] for n in t["nodes"]] == ["reproduce", "fix", "retro"]


def test_the_loop_new_then_reuse_then_update(workflows, repo, titled):
    # Session A: no type fits, so it submits a graph, runs it, and keeps it.
    a = start(repo, type_="new", graph=GRAPH)
    close(a, "reproduce", "fix")
    out = _retro(a, "new_type", name="widget-crash", match="reproduce, fix widget crash",
                 applied=["S1"])
    close(a, "retro")
    assert "widget-crash.yaml" in out and store.get_task(store.connect(), a)["state"] == "done"
    subject, body = _log("-1", "--format=%s%n%b").split("\n", 1)
    assert subject == f"wf retro new_type widget-crash: {ISSUE}" and "applied proposals: S1" in body
    assert store.get_task(store.connect(), a)["type"] == "widget-crash"  # its history counts there
    # Session B, a similar issue: `new` is refused and points at A's graph.
    b_issue = ISSUE.replace("/1", "/2")
    with pytest.raises(graph.WfError, match="widget-crash"):
        start(repo, type_="new", issue=b_issue, graph=GRAPH)
    b = start(repo, type_="widget-crash", issue=b_issue)
    assert "widget-crash" in library.listing(store.connect())
    listing = library.listing(store.connect())
    assert "past: widget crash on scroll" in listing
    assert "widget crash on resize" not in listing
    # B's retro adds a node it needed and commits the update for the session after it.
    close(b, "reproduce", "fix")
    revised = GRAPH + "  - {id: regress_test, check: 'test -d .', on_fail: fix, after: [fix]}\n"
    _retro(b, "update_type", graph=revised, deviations="regress_test was needed")
    assert _log("-1", "--format=%s").strip() == f"wf retro update_type widget-crash: {b_issue}"
    # The reviewed graph is the default for every following matching task.
    assert not (library.root() / "candidates" / "widget-crash.yaml").exists()
    nodes = graph.parse(library.body("widget-crash")[0])["nodes"]
    assert [n["id"] for n in nodes] == ["reproduce", "fix", "regress_test", "retro"]
    assert "regress_test" in library.body("widget-crash")[0]
    assert "widget-crash" in library.types() and len(library.types()) == len(
        list(library.root().glob("*.yaml")))


def test_an_unrelated_new_is_not_refused_and_a_reason_overrides(workflows, repo, titled):
    a = start(repo, type_="new", graph=GRAPH)
    close(a, "reproduce", "fix")
    _retro(a, "new_type", name="widget-crash", match="reproduce, fix widget crash")
    start(repo, type_="new", issue=ISSUE.replace("/1", "/3"), graph=GRAPH)
    tid = start(repo, type_="new", issue=ISSUE.replace("/1", "/2"), graph=GRAPH,
                new_reason="scroll is a different subsystem")
    assert store.get_task(store.connect(), tid)["new_reason"] == "scroll is a different subsystem"


def test_matching_fails_open_without_gh(workflows, repo):
    a = start(repo, type_="new", graph=GRAPH)
    close(a, "reproduce", "fix")
    _retro(a, "new_type", name="widget-crash", match="reproduce, fix widget crash")
    start(repo, type_="new", issue=ISSUE.replace("/1", "/2"), graph=GRAPH)  # no text: no match


def test_retro_verdict_rules(workflows, repo, titled):
    tid = start(repo)
    with pytest.raises(graph.WfError, match="not closed yet"):  # the real path is not over yet
        _retro(tid, "update_type")
    close(tid, "a", "b", "h", "m")
    with pytest.raises(graph.WfError, match="name"):
        _retro(tid, "new_type", name="Bad Name", match="m")
    with pytest.raises(graph.WfError, match="already"):
        _retro(tid, "new_type", name="demo", match="m")
    new = start(repo, type_="new", issue=ISSUE.replace("/1", "/3"), graph=GRAPH)
    close(new, "reproduce", "fix")
    with pytest.raises(graph.WfError, match="new_type"):
        _retro(new, "update_type")
    with pytest.raises(graph.WfError, match="leave its graph in the library"):  # must not vanish
        _retro(new, "no_change")
    head = _log("--format=%H")
    # A noted deviation cannot just be dropped: reflect it, or say why not.
    with pytest.raises(graph.WfError, match="grounds"):
        _retro(tid, "no_change", deviations="the do text carried a previous task's target")
    _retro(tid, "no_change", deviations="none")
    _retro(tid, "no_change", deviations="b was slow once", reason="one-off network delay")
    conn = store.connect()
    assert "reason to keep the structure: one-off" in conn.execute(
        "SELECT deviations FROM retro WHERE task = ?", (tid,)).fetchone()[0]
    assert _log("--format=%H") == head  # no_change commits nothing


def test_a_task_local_param_needs_the_review_to_name_it(workflows, repo):
    # A param only this task's environment needed cannot be promoted to
    # the default; the review names it and no_change goes through. Unnamed, it refuses.
    tid = start(repo)
    default = library.body("demo")[0]
    revised = default.replace("  name: '^[a-z;]+$'\n", "  name: '^[a-z;]+$'\n"
                              "  replay: ^/[^ ]+[.]log$\n")
    assert revised != default
    graph.revise(store.connect(), store.get_task(store.connect(), tid), revised,
                 "this task checks only against a local repro log")
    close(tid, "a", "b", "h", "m")
    head = _log("--format=%H")
    named = [{"id": "S1", "axis": "rework",
              "evidence": "this task checked only the log the replay param leaves",
              "proposal": "keep the default type's test check and do not promote this param"}]
    with pytest.raises(graph.WfError, match=r"protected-condition change \['params.replay'\]"):
        _retro(tid, "no_change", graph=default, deviations="added the replay param",
               reason="only for this task's environment")
    other = [{**named[0], "evidence": "reviewed only the node order",
              "proposal": "keep the order"}]
    with pytest.raises(graph.WfError, match=r"protected-condition change \['params.replay'\]"):
        # A finding dismissed with a reason does not release it unless it names it
        _retro(tid, "no_change", graph=default, deviations="added the replay param",
               reason="only for this task's environment", findings=other,
               dismissed={"S1": "keep the structure"})
    with pytest.raises(graph.WfError, match=r"protected-condition change \['params.replay'\]"):
        # A longer name (replay.extra) does not name this param
        _retro(tid, "no_change", graph=default, deviations="added the replay param",
               reason="only for this task's environment",
               findings=[{**named[0], "evidence": "looked only at replay.extra"}],
               dismissed={"S1": "keep the structure"})
    _retro(tid, "no_change", graph=default, deviations="added the replay param",
           reason="only for this task's environment",
           # With the name left out of the evidence, this pass rests only on the boundary
           # before a Korean particle (U+B294) glued to the name.
           findings=[{**named[0], "evidence": "checked only against this log",
                      "proposal": "params.replay\ub294 stays out of the default type"}],
           dismissed={"S1": "no grounds to put it in the default type"})
    assert _log("--format=%H") == head  # no_change commits nothing


def test_changing_a_check_saves_with_a_reason(workflows, repo):
    # A reviewed update_type saves a gate change without user approval;
    # a weakened check still needs a reason, which the library commit records.
    tid = start(repo)
    close(tid, "a", "b", "h", "m")
    body = library.body("demo")[0].replace("check: test -f b.ok", "check: 'test -d .'")
    head = _log("--format=%H")
    with pytest.raises(graph.WfError, match=r"reason required"):
        _retro(tid, "update_type", graph=body)
    assert _log("--format=%H") == head
    _retro(tid, "update_type", graph=body, reason="b.ok is produced by CI now")
    saved = {n["id"]: n for n in graph.parse(library.body("demo")[0])["nodes"]}
    assert saved["b"]["check"] == "test -d ."
    assert not (library.root() / "candidates" / "demo.yaml").exists()
    assert "reason: b.ok is produced by CI now" in _log("-1", "--format=%b")


def test_a_task_that_revised_a_check_can_still_keep_the_default(workflows, repo):
    # The running graph's check differs from the default, but no_change
    # writes nothing, so it must not be refused as a protected change.
    tid = start(repo)
    default = library.body("demo")[0]
    revised = default.replace("check: test -f b.ok", "check: test -f b2.ok")
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, tid), revised, "this task makes b2.ok")
    close(tid, "a", "b", "h", "m")
    head = _log("--format=%H")
    _retro(tid, "no_change", graph=default, deviations="revised the b check for this task",
           reason="the revise is limited to this task")
    assert _log("--format=%H") == head
    # A gate added to an existing ungated node is still an added gate.
    tid = start(repo, issue=ISSUE.replace("/1", "/2"))
    gated = default.replace("do: a human step\n", "do: a human step\n    check: test -d .\n"
                            "    on_fail: retry\n", 1)
    assert gated != default
    graph.revise(conn, store.get_task(conn, tid), gated, "added a check to h")
    close(tid, "a", "b", "h", "m")
    with pytest.raises(graph.WfError, match=r"protected-condition change.*\['h'\]"):
        _retro(tid, "no_change", graph=default, deviations="added a check to h",
               reason="keep the default")


def test_a_check_differing_only_in_trailing_whitespace_is_the_default(workflows, repo):
    # A candidate written with a trailing newline in a check ran the task;
    # keeping it is keeping the default, not a changed gate.
    tid = start(repo)
    default = library.body("demo")[0]
    padded = default.replace("check: test -f b.ok", "check: \"test -f b.ok\\n\\n\"")
    assert padded != default
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, tid), padded, "run as the candidate")
    close(tid, "a", "b", "h", "m")
    head = _log("--format=%H")
    _retro(tid, "no_change", graph=padded, deviations="none")
    assert _log("--format=%H") == head


# --- evidence across tasks ---------------------------------------------------------

def _task(conn, version: str | None = None, state: str = "done", hours: float = 1) -> int:
    """A demo task; `version` defaults to the current template, as a fresh task's would."""
    tid = store.create_task(conn, nodes=[], type="demo", dir="/", issue_url=f"u{time.time_ns()}",
                            version=version or library.body("demo")[1], graph="",
                            params="{}")
    store.set_state(conn, tid, state)
    conn.execute("UPDATE task SET created = updated - ? WHERE id = ?", (hours * 3600, tid))
    return tid


def test_evidence_aggregates_and_suggests(workflows):
    conn = store.connect()
    t1, t2, t3 = _task(conn), _task(conn), _task(conn)
    for t in (t1, t2):
        store.event(conn, t, "b", "attempt", seconds=4)
        store.event(conn, t, "b", "fail", "error: missing b.ok")
        store.event(conn, t, "h", "waive", "not needed")
        store.event(conn, t, "lint", "revise_add", "forgot lint")
        store.event(conn, t, "m", "back", "a")
    store.event(conn, t3, "b", "attempt", seconds=10)
    store.event(conn, t3, "b", "fail", "error: once only")
    text = library.evidence(conn, "demo")
    assert "Type demo: 3 past tasks" in text
    assert "- b: failed 3/3 · avg 6s · waived 0 · skip 0 · back 0" in text
    assert "`h` waived in 2/3 tasks — removal candidate" in text
    assert "`b`: same failure in 2 tasks: 'error: missing b.ok' — pitfall/check candidate" in text
    assert "once only" not in text.split("Suggestions:")[1]
    assert "`lint` added by wf_revise recurs in 2 tasks — promote it into the template" in text
    assert "`m` → `a` went back 2 times in 2 tasks — add a check before `a`" in text
    assert ("`b` (on_fail: retry) failed 3 times — add an edge back to an earlier node"
            " (on_fail: <earlier node>)") in text
    assert "S5." in text


def test_repeated_revisions_of_a_node_are_suggested(workflows):
    # 38 tasks re-scoped `test` by hand and no retro was told to.
    conn = store.connect()
    for t in (_task(conn), _task(conn)):
        store.event(conn, t, "b", "revise_change", "fit the b check to this repository")
    assert "2 tasks fixed `b` with wf_revise" in library.evidence(conn, "demo")


def test_a_failure_an_older_template_had_is_not_suggested(workflows):
    # Once the template changes, its predecessor's failures are history.
    conn = store.connect()
    for t in (_task(conn, "old"), _task(conn, "old")):
        store.event(conn, t, "b", "fail", "error: missing b.ok")
    text = library.evidence(conn, "demo")
    assert "- b: failed 2/0" in text and "Suggestions: none" in text
    for t in (_task(conn), _task(conn)):
        store.event(conn, t, "b", "fail", "error: missing b.ok")
    assert "same failure in 2 tasks" in library.evidence(conn, "demo")
    # The task being retro'd counts even if it started on the older template.
    mine = store.get_task(conn, _task(conn, "old"))
    store.event(conn, mine["id"], "c", "fail", "error: c")
    store.event(conn, _task(conn), "c", "fail", "error: c")
    assert "`c`: same failure in 2 tasks" in library.evidence(conn, "demo", mine)


def test_repeated_loops_in_one_task_and_wf_fail_counts(workflows):
    conn = store.connect()
    t1 = _task(conn)
    for _ in range(3):
        store.event(conn, t1, "b", "wf_fail", "a: tests red")
        store.event(conn, t1, "b", "back", "a")
    store.event(conn, t1, "a", "fail", "x")
    text = library.evidence(conn, "demo")
    assert "· back 1 · revised 0 · wf_fail 3" in text
    assert "`b` → `a` went back 3 times in 1 tasks — add a check before `a`" in text
    assert "(on_fail: retry)" not in text  # one failure is not a pattern


def test_a_single_task_suggests_nothing(workflows):
    conn = store.connect()
    store.event(conn, _task(conn), "h", "waive", "x")
    assert "Suggestions: none" in library.evidence(conn, "demo")


def test_attempts_and_failures_are_recorded_by_running_checks(workflows, repo):
    from omge import hooks
    tid = start(repo)
    conn = store.connect()
    hooks.stop(conn, store.get_task(conn, tid), time.monotonic() + 30)
    kinds = [tuple(r) for r in conn.execute("SELECT node, kind, detail FROM event")]
    assert ("a", "attempt", "") in kinds and any(k[:2] == ("a", "fail") and k[2] for k in kinds)


def test_an_unchanged_repeated_failure_is_not_recorded_again(workflows, repo):
    from omge import checks
    tid = start(repo)
    conn = store.connect()
    node = next(n for n in graph.parse(store.get_task(conn, tid)["graph"])["nodes"]
                if n["id"] == "a")

    def go():
        checks.attempt(conn, store.get_task(conn, tid), node, time.monotonic() + 30)
        return [r[0] for r in conn.execute(
            "SELECT kind FROM event WHERE task = ? AND node = 'a' AND kind IN ('attempt', 'fail')",
            (tid,))]

    assert go() == ["attempt", "fail"]
    assert go() == ["attempt", "fail"]  # identical: nothing new
    row = conn.execute("SELECT attempts, last_output_tail FROM node_state"
                       " WHERE task = ? AND node = 'a'", (tid,)).fetchone()
    assert row[0] == 2  # node_state still counts it
    store.event(conn, tid, "a", "back", "x")  # anything in between breaks the streak
    assert go() == ["attempt", "fail"] * 2
    conn.execute("UPDATE event SET detail = 'old' WHERE kind = 'fail'")
    assert go() == ["attempt", "fail"] * 3  # a different reason is recorded


def _versions(conn) -> tuple[str, str]:
    body = library.body("demo")[0]
    library.commit("demo", body + "# v2\n", "v2")
    cur, prev = _log("--format=%H", "--", "demo.yaml").split()[:2]
    return cur, prev


def test_versions_compare_and_a_worse_one_suggests_revert(workflows):
    conn = store.connect()
    cur, prev = _versions(conn)
    for _ in range(3):
        _task(conn, prev)
    for state in ("done", "stalled", "stalled"):
        tid = _task(conn, cur, state)
        if state == "stalled":
            store.event(conn, tid, "", "stall")
    text = library.evidence(conn, "demo")
    assert f"current {cur[:7]}: score 0.58 ± 0.29 (n=3)\n    started 3 · done 1 · stall 2" in text
    assert f"previous {prev[:7]}: score 1.00 ± 0.00 (n=3)\n    started 3 · done 3 · stall 0" in text
    assert f"revert {cur[:7]}" in text


def test_versions_need_enough_closed_tasks_and_a_real_drop(workflows):
    conn = store.connect()
    cur, prev = _versions(conn)
    for _ in range(3):
        _task(conn, prev)
        _task(conn, cur)
    _task(conn, cur, "active")  # still running: not counted against the version
    assert "revert" not in library.evidence(conn, "demo")
    conn.execute("DELETE FROM task WHERE version = ?", (prev,))
    _task(conn, prev, "stalled")
    assert "revert" not in library.evidence(conn, "demo")  # one closed task is no evidence


def test_shared_step_ids_do_not_force_an_unrelated_retro_type(workflows, repo):
    restart = """
nodes:
  - {id: inventory, check: 'test -d .', on_fail: retry}
  - {id: restart, check: 'test -d .', on_fail: retry, after: [inventory]}
  - {id: verify, check: 'test -d .', on_fail: retry, after: [restart]}
"""
    a = start(repo, type_="new", issue=None, title="Restart stale workflow MCP processes",
              graph=restart)
    close(a, "inventory", "restart", "verify")
    _retro(a, "new_type", name="wf-mcp-restart", match="Restart stale workflow MCP processes")
    close(a, "retro")
    original = library.body("wf-mcp-restart")
    cleanup = """
nodes:
  - {id: inventory, check: 'test -d .', on_fail: retry}
  - {id: approve, kind: human, after: [inventory]}
  - {id: cleanup, check: 'test -d .', on_fail: retry, after: [approve]}
  - {id: verify, check: 'test -d .', on_fail: retry, after: [cleanup]}
"""
    b = start(repo, type_="new", issue=None, title="Remove obsolete catalog entries",
              graph=cleanup)
    close(b, "inventory", "approve", "cleanup", "verify")
    _retro(b, "new_type", name="catalog-prune",
           match="Remove obsolete catalog entries", graph=cleanup)
    close(b, "retro")
    assert store.get_task(store.connect(), b)["state"] == "done"
    assert store.get_task(store.connect(), b)["type"] == "catalog-prune"
    assert "catalog-prune" in library.types()
    assert library.body("wf-mcp-restart") == original
    assert not (library.root() / "candidates" / "wf-mcp-restart.yaml").exists()


def test_a_near_duplicate_new_type_is_refused_even_with_a_reason(workflows, repo, titled):
    a = start(repo, type_="new", graph=GRAPH)
    close(a, "reproduce", "fix")
    _retro(a, "new_type", name="widget-crash", match="reproduce, fix widget crash")
    b = start(repo, type_="new", issue=ISSUE.replace("/1", "/2"), graph=GRAPH,
              new_reason="a stricter variant")  # starting as new is allowed...
    close(b, "reproduce", "fix")
    with pytest.raises(graph.WfError, match="update_type\\(name='widget-crash'\\)"):
        _retro(b, "new_type", name="widget-crash-strict", match="strictly fix widget crash")
    # The reviewer-selected full graph can associate this task with the existing type.
    before = library.body("widget-crash")
    _retro(b, "update_type", name="widget-crash", graph=GRAPH)
    assert store.get_task(store.connect(), b)["type"] == "widget-crash"
    assert library.body("widget-crash") == before


@pytest.mark.parametrize("literal", ["fix #1 again", "git log demo-1", "{dir}/x"])
def test_a_template_must_not_hardcode_this_task(workflows, repo, literal):
    bad = GRAPH.replace("reproduce the crash", "'" + literal.format(dir=repo) + "'")
    tid = start(repo, type_="new", graph=bad)
    close(tid, "reproduce", "fix")
    with pytest.raises(graph.WfError, match="reproduce.*WF_ISSUE_NUMBER"):
        _retro(tid, "new_type", name="kind", match="some kind", graph=bad)
    fixed = GRAPH.replace("reproduce the crash", "'fix #$WF_ISSUE_NUMBER again'")
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, tid), fixed, "generalized the task-specific literal")
    _retro(tid, "new_type", name="kind", match="some kind", graph=fixed)


def test_reviewed_unchanged_library_needs_no_fake_update(workflows, repo):
    tid = start(repo)
    close(tid, "a", "b", "h", "m")
    library.commit("demo", library.body("demo")[0] + "# moved on\n", "someone else's retro")
    head = _log("--format=%H")
    with pytest.raises(graph.WfError, match="semantic noop"):
        _retro(tid, "update_type")
    _retro(tid, "no_change", deviations="none")
    assert _log("--format=%H") == head


def test_weakening_covers_more_than_checks():
    base = graph.parse(TEMPLATE_WITH_EDGES)
    cases = {
        "check": TEMPLATE_WITH_EDGES.replace("test -f b.ok", "test -f b2.ok"),
        "after": TEMPLATE_WITH_EDGES.replace("after: [b]\n    check: test -f c.ok",
                                             "check: test -f c.ok"),
        "timeout": TEMPLATE_WITH_EDGES.replace("timeout: 30", "timeout: 300"),
        "human": TEMPLATE_WITH_EDGES.replace("  - id: b\n", "  - id: b\n    kind: human\n"),
        "when": TEMPLATE_WITH_EDGES.replace("test -f go", "test -f went"),
        "param": TEMPLATE_WITH_EDGES.replace("'^[a-z;]+$'", "'.*'"),
    }
    expected = {"check": ["b"], "after": ["c"], "timeout": ["a"], "human": ["b"], "when": ["a"],
                "param": ["params.name"]}
    for name, body in cases.items():
        assert graph._weakened(base, graph.parse(body)) == expected[name], name
    assert graph._weakened(base, base) == []


TEMPLATE_WITH_EDGES = """
type: demo
match: m
params:
  name: '^[a-z;]+$'
nodes:
  - id: a
    timeout: 30
    check: test -f a.ok
    next:
      - {to: b, when: test -f go}
      - {to: retro}
  - id: b
    after: [a]
    do: make b.ok
    check: test -f b.ok
  - id: c
    after: [b]
    check: test -f c.ok
  - id: retro
    after: [c]
"""


def test_a_later_wait_does_not_hide_an_earlier_failure(workflows):
    """node_state.last_exit is one cell. It cannot say which fail events were the wait."""
    conn = store.connect()
    for _ in range(2):
        t = _task(conn)
        store.event(conn, t, "m", "attempt")
        store.event(conn, t, "m", "fail", "merge check failed")
        store.event(conn, t, "m", "attempt")
        conn.execute("INSERT INTO node_state (task, node, last_exit) VALUES (?, 'm', 75)", (t,))
    text = library.evidence(conn, "demo")
    assert "same failure in 2 tasks: 'merge check failed'" in text.split("Suggestions:")[1]


def test_skips_alone_never_suggest_removal(workflows):
    conn = store.connect()
    for t in (_task(conn), _task(conn), _task(conn)):
        store.event(conn, t, "report_reject", "skip")
    assert "removal candidate" not in library.evidence(conn, "demo")


def test_library_commit_is_atomic_and_idempotent(workflows):
    text = library.body("demo")[0]
    assert library.commit("demo", text, "same") is None  # nothing to commit: no-op
    hook = library.root() / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(graph.WfError, match="commit"):
        library.commit("demo", text + "# x\n", "refused")
    with pytest.raises(graph.WfError):
        library.commit("fresh", "type: fresh\n", "refused")
    assert library.body("demo")[0] == text and library.body("fresh") is None
    assert "fresh.yaml" not in _log("--name-only", "--format=")


def test_a_half_seeded_library_is_seeded_again(workflows, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path / "library")], check=True)
    assert set(library.types()) == {"demo"} and "seed" in _log("--format=%s")


def test_the_loop_without_an_issue_matches_on_the_title(workflows, repo):
    # An investigation nobody filed: started, kept as a type, and found again by its title.
    a = start(repo, type_="new", issue=None, title="widget crash on resize", graph=GRAPH)
    close(a, "reproduce", "fix")
    with pytest.raises(graph.WfError, match="new_type"):
        _retro(a, "no_change")  # a `new` still has to keep its graph
    _retro(a, "new_type", name="widget-crash", match="reproduce, fix widget crash")
    close(a, "retro")
    assert store.get_task(store.connect(), a)["state"] == "done"
    assert _log("-1", "--format=%s").strip() == f"wf retro new_type widget-crash: task:{a}"
    with pytest.raises(graph.WfError, match="widget-crash"):
        start(repo, type_="new", issue=None, title="widget crash on scroll", graph=GRAPH)
    b = start(repo, type_="widget-crash", issue=None, title="widget crash on scroll")
    assert store.get_task(store.connect(), b)["type"] == "widget-crash"


RECEIPT = """
type: rev
match: review receipt type
nodes:
  - id: lint_review
    check: 'h=$(git rev-parse HEAD); gh api "repos/x/issues/1/comments" | grep -q "$h"'
    on_fail: retry
  - id: review
    after: [lint_review]
    check: 'h=$(git rev-parse HEAD); gh api "repos/x/issues/1/comments" | grep -q "$h"'
    on_fail: retry
  - id: retro
    after: [review]
"""


def test_retro_suggests_carry_and_parallel_reviews_for_head_receipts(workflows):
    (workflows / "rev.yaml").write_text(RECEIPT)
    text = library.evidence(store.connect(), "rev")
    assert "['lint_review', 'review'] lack `carry: code`" in text
    assert "['review'] run serially after another receipt node" in text
    (workflows / "fixed.yaml").write_text(  # a library file is never overwritten: a new type
        RECEIPT.replace("type: rev", "type: fixed")
        .replace("on_fail: retry", "on_fail: retry\n    carry: code")
        .replace("    after: [lint_review]\n", ""))
    assert "HEAD receipt" not in library.evidence(store.connect(), "fixed")


@pytest.mark.parametrize("length", [59, 60, 61, 100])
def test_listing_clips_only_the_latest_past_title(workflows, repo, length):
    for i in range(5):
        start(repo, issue=None, title=f"older title {i}")
    title = chr(0xAC00) * length  # a multibyte title
    tid = start(repo, issue=None, title=title)
    conn = store.connect()
    assert library.titles(conn, "demo") == [title, *[f"older title {i}" for i in range(4, 0, -1)]]
    assert library.titles(conn, "demo", tid) == [f"older title {i}" for i in range(4, -1, -1)]
    expected = title[:60] + ("…" if length > 60 else "")
    assert library.listing(conn).splitlines()[-1] == "    past: " + expected


def test_the_brief_listing_names_every_type_without_past_titles(workflows, repo):
    (workflows / "long.yaml").write_text(
        "type: long\nmatch: " + "x" * 300 + "\nnodes:\n"
        "  - {id: a, check: 'test -f a', on_fail: retry}\n  - {id: retro, after: [a]}\n")
    start(repo, issue=None, title="a past title")
    conn = store.connect()
    full, brief = library.listing(conn), library.listing(conn, brief=True)
    assert "a past title" in full and "a past title" not in brief
    assert all(f"- {n}:" in brief for n in library.types())
    assert "x" * 101 not in brief and "x" * 100 + "…" in brief


def test_a_query_keeps_the_matching_types_full_lines_and_names_a_miss(workflows, repo):
    (workflows / "long.yaml").write_text(
        "type: long\nmatch: " + "x" * 300 + "\nnodes:\n"
        "  - {id: a, check: 'test -f a', on_fail: retry}\n  - {id: retro, after: [a]}\n")
    start(repo, issue=None, title="a past title")
    conn = store.connect()
    hit = library.listing(conn, query="long")
    assert "- long:" in hit and "- demo:" not in hit
    assert "x" * 300 in hit  # the few matches, full detail
    assert "past: a past title" in library.listing(conn, query="demo")
    miss = library.listing(conn, query="nothing matches this")
    assert "nothing matches this" in miss and "- " not in miss


def test_git_identity_is_not_reported_as_a_workflow_node(workflows, repo):
    tid = start(repo)
    conn = store.connect()
    text = library.evidence(conn, "demo", store.get_task(conn, tid))
    assert "- @git:" not in text
    assert conn.execute("SELECT COUNT(*) FROM event WHERE task = ? AND kind = 'git_identity'",
                        (tid,)).fetchone()[0] == 1
