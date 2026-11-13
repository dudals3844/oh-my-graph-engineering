"""Conditional `next`, loops back, transitive skips, and the drawn graph."""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest

from omge import checks, graph, store

from .conftest import close, reviewed_retro, start, state, statuses

BRANCHY = textwrap.dedent(
    """
    type: br
    match: branch test
    nodes:
      - id: first
        check: 'test -d .'
      - id: decide
        after: [first]
        check: 'test -d .'
        next:
          - {to: good, when: 'test "$(cat route)" = yes', label: good}
          - {to: bad, when: 'test "$(cat route)" = no'}
          - {to: first, when: 'test "$(cat route)" = again', max: 2}
      - id: good
        check: 'test -d .'
      - id: good_more
        after: [good]
        check: 'test -d .'
      - id: bad
        check: 'test -d .'
      - id: retro
        after: [good_more, bad]
    """
)


@pytest.fixture
def branchy(workflows, repo):
    (workflows / "br.yaml").write_text(BRANCHY)
    tid = start(repo, type_="br")
    close(tid, "first")
    return tid


def _route(repo: str, value: str) -> None:
    Path(repo, "route").write_text(value)


def test_first_matching_branch_wins_and_the_rest_are_skipped(branchy, repo):
    _route(repo, "yes")
    close(branchy, "decide")
    assert statuses(branchy) == {"first": "done", "decide": "done", "good": "pending",
                                 "good_more": "pending", "bad": "skipped", "retro": "pending"}
    assert store.node_rows(store.connect(), branchy)["decide"]["route"] == "good"
    assert "──► [▶ good] ✓" in _drawn(branchy)
    close(branchy, "good", "good_more")
    t = graph.parse(store.get_task(store.connect(), branchy)["graph"])
    assert [n["id"] for n in graph.ready(t, store.node_rows(store.connect(), branchy))] == ["retro"]


def test_skips_are_transitive(branchy, repo):
    _route(repo, "no")
    close(branchy, "decide")
    assert statuses(branchy)["good"] == statuses(branchy)["good_more"] == "skipped"
    close(branchy, "bad")
    assert statuses(branchy)["retro"] == "pending"
    assert [r[0] for r in store.connect().execute(
        "SELECT node FROM event WHERE kind = 'skip' ORDER BY rowid")] == ["good", "good_more"]


def test_two_slow_false_whens_share_one_deadline_and_the_else_edge_still_wins(workflows, repo):
    (workflows / "br.yaml").write_text(BRANCHY.replace(
        "test \"$(cat route)\" = yes", "sleep 2").replace(
        "test \"$(cat route)\" = no", "sleep 2").replace(
        "max: 2}", "max: 2}\n      - {to: bad, label: otherwise}"))
    tid = start(repo, type_="br")
    close(tid, "first")
    conn = store.connect()
    begun = time.monotonic()
    checks.close(conn, store.get_task(conn, tid), "decide", status="done",
                 done_by="check", evidence="", deadline=begun + 1)
    assert time.monotonic() - begun < 1.8
    assert statuses(tid)["decide"] == "done" and statuses(tid)["good"] == "skipped"


def test_no_matching_branch_refuses_completion(branchy, repo):
    _route(repo, "nothing")
    conn = store.connect()
    with pytest.raises(graph.WfError, match="no `next` when matches"):
        checks.complete(conn, store.get_task(conn, branchy), "decide", "ev")
    assert statuses(branchy)["decide"] == "pending"
    # The refusal is a failure like any check's, so Stop's stall path sees it.
    assert store.node_rows(conn, branchy)["decide"]["last_exit"] == 1


def test_a_loop_back_resets_the_subtree_until_max(branchy, repo):
    _route(repo, "again")
    for loops in (1, 2):
        close(branchy, "decide")
        assert statuses(branchy) == dict.fromkeys(
            ("first", "decide", "good", "good_more", "bad", "retro"), "pending")
        assert store.node_rows(store.connect(), branchy)["first"]["loops"] == loops
        close(branchy, "first")
    with pytest.raises(graph.WfError, match="max 2"):
        close(branchy, "decide")
    assert statuses(branchy)["decide"] == "pending"
    assert [r[0] for r in store.connect().execute(
        "SELECT detail FROM event WHERE kind = 'back'")] == ["first", "first"]


def test_an_edge_without_when_is_the_else(workflows, repo):
    (workflows / "br.yaml").write_text(BRANCHY.replace(
        "{to: bad, when: 'test \"$(cat route)\" = no'}", "{to: bad}"))
    tid = start(repo, type_="br")
    close(tid, "first")
    _route(repo, "anything")
    close(tid, "decide")
    assert store.node_rows(store.connect(), tid)["decide"]["route"] == "bad"


def test_a_waived_decision_needs_its_route(branchy):
    with pytest.raises(graph.WfError, match="decide>good"):
        close(branchy, "decide", status="waived")
    conn = store.connect()
    checks.close(conn, store.get_task(conn, branchy), "decide", status="waived", done_by="user",
                 evidence="x", route="bad")
    assert (statuses(branchy)["good"], statuses(branchy)["good_more"],
            statuses(branchy)["bad"]) == ("skipped", "skipped", "pending")


def test_a_skipped_branch_does_not_hold_the_task_open(branchy, repo):
    _route(repo, "no")
    close(branchy, "decide")
    conn = store.connect()
    close(branchy, "bad")
    reviewed_retro(conn, store.get_task(conn, branchy), "no_change", "none")
    close(branchy, "retro")
    assert state(branchy) == "done"


LOOP = textwrap.dedent(
    """
    type: lp
    match: loop test
    params:
      pr: '^[0-9]+$'
      pkg: '^[a-z]+$'
    nodes:
      - {id: reserve, check: 'test -n {pkg}'}
      - {id: package, after: [reserve], check: 'test -n {pkg}'}
      - {id: pr, after: [package], check: 'test -n {pr}'}
      - id: validate
        after: [pr]
        check: 'test -d .'
        next:
          - {to: package, when: 'test -f again', max: 2}
          - {to: retro}
      - {id: retro}
    """
)


def test_one_run_ready_takes_at_most_one_loop_and_clears_the_redone_params(workflows, repo):
    (workflows / "lp.yaml").write_text(LOOP)
    Path(repo, "again").touch()
    tid = start(repo, type_="lp", params={"pr": "7", "pkg": "x"})
    conn = store.connect()
    checks.run_ready(conn, store.get_task(conn, tid), time.monotonic() + 60)
    rows = store.node_rows(conn, tid)
    assert rows["package"]["loops"] == 1 and rows["package"]["status"] == "pending"
    # `pr` is read only by the redone part: the next round needs a new PR number;
    # `pkg` is also read by `reserve`, which stands.
    assert json.loads(store.get_task(conn, tid)["params"]) == {"pkg": "x"}


def test_when_runs_within_the_stop_deadline(branchy, repo):
    conn = store.connect()
    task = store.get_task(conn, branchy)
    with conn:
        conn.execute("UPDATE task SET graph = replace(graph, 'test \"$(cat route)\" = yes',"
                     " 'sleep 30') WHERE id = ?", (branchy,))
    _route(repo, "nothing")
    begun = time.monotonic()
    with pytest.raises(graph.WfError, match="no `next` when"):
        checks.close(conn, store.get_task(conn, task["id"]), "decide", status="done",
                     done_by="check", evidence="", deadline=time.monotonic() + 1)
    assert time.monotonic() - begun < 10


def test_closing_an_already_closed_node_is_refused(branchy):
    with pytest.raises(graph.WfError, match="already closed"):
        close(branchy, "first")


# --- the drawn graph -------------------------------------------------------------

def _drawn(tid: int) -> str:
    conn = store.connect()
    task = dict(store.get_task(conn, tid))
    task["version"] = task["version"] and "a1b2c3d4e5f6"  # the library sha differs per run
    return graph.render(task, graph.parse(task["graph"]), store.node_rows(conn, tid))


ISSUE_RES = "https://github.com/example/demo/issues/7"


@pytest.fixture
def issue_res(repo):
    return start(repo, type_="issue-resolution", issue=ISSUE_RES)


def test_render_issue_resolution_at_start(issue_res):
    assert _drawn(issue_res) == SNAP_START


def test_render_issue_resolution_mid_run(issue_res):
    close(issue_res, "claim", "spec")
    assert _drawn(issue_res) == SNAP_MID


def test_render_submitted_graph(workflows, repo):
    tid = start(repo, type_="new", new_reason="first of its kind", graph=textwrap.dedent("""
        nodes:
          - {id: survey, check: 'test -d .', on_fail: retry}
          - {id: fix_a, check: 'test -d .', on_fail: retry, after: [survey]}
          - {id: fix_b, check: 'test -d .', on_fail: retry, after: [survey]}
          - id: verify
            check: 'test -d .'
            on_fail: survey
            after: [fix_a, fix_b]
            next: [{to: ship, when: 'test -d .'}]
          - {id: ship, check: 'test -d .', on_fail: retry}
        """))
    assert _drawn(tid) == SNAP_NEW


def test_render_stays_within_width(issue_res):
    assert max(map(graph._w, _drawn(issue_res).splitlines())) <= graph.WIDTH


def test_a_wide_chain_stays_within_width():
    names = [chr(97 + i) + "x" * 14 for i in range(8)]
    nodes = [{"id": "root", "check": "test -d .", "on_fail": "retry"}]
    nodes += [{"id": n, "check": "test -d .", "on_fail": "retry", "after": ["root"]} for n in names]
    t = graph.parse(graph.submitted({"nodes": nodes}, "x", "m"))
    rows = {n["id"]: {"status": "pending", "loops": 0, "route": None} for n in t["nodes"]}
    task = {"id": 1, "type": "x", "version": "", "state": "active",
            "issue_url": "https://github.com/example/demo/issues/1"}
    text = graph.render(task, t, rows)
    assert max(map(graph._w, text.splitlines())) <= graph.WIDTH


SNAP_START = """\
issue-resolution @a1b2c3d · demo#7 · task #1 active
[▶ claim] ──► [· spec] ──► [· scope_review]
                                   │
                                   ├─ complex → plan ──► [· plan]
                                   └─ simple ──────────► [· implement] ◄─ plan
[· test] ◄─ implement
    ▼
[· review] ──► [· pr] ──► [· ci] ──► [· merge] ──► [· cleanup] ──► [· retro]
[scope_review] ─✗─► ↺ spec (max 3)
[plan] ─✗─► ↺ spec (max 3)
[implement] ─✗─► ↺ spec (max 2)
[test] ─✗─► ↺ implement (max 3)
[review] ─✗─► ↺ implement (max 3)
[ci] ─✗─► ↺ implement (max 3)
[merge] ─✗─► ↺ implement (max 3)
[cleanup] ─✗─► ↺ implement (max 3)
current: claim
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail"""

SNAP_MID = """\
issue-resolution @a1b2c3d · demo#7 · task #1 active
[✔ claim] ──► [✔ spec] ──► [▶ scope_review]
                                   │
                                   ├─ complex → plan ──► [· plan]
                                   └─ simple ──────────► [· implement] ◄─ plan
[· test] ◄─ implement
    ▼
[· review] ──► [· pr] ──► [· ci] ──► [· merge] ──► [· cleanup] ──► [· retro]
[scope_review] ─✗─► ↺ spec (max 3)
[plan] ─✗─► ↺ spec (max 3)
[implement] ─✗─► ↺ spec (max 2)
[test] ─✗─► ↺ implement (max 3)
[review] ─✗─► ↺ implement (max 3)
[ci] ─✗─► ↺ implement (max 3)
[merge] ─✗─► ↺ implement (max 3)
[cleanup] ─✗─► ↺ implement (max 3)
current: scope_review
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail"""


SNAP_NEW = """\
new · demo#1 · task #1 active
[▶ survey]
     │
    ┌┴──────────┐
    ▼           ▼
[· fix_a]   [· fix_b]
    └─────┬─────┘
          ▼
     [· verify]
          │
          └─ test -d . ──► [· ship]
[· retro] ◄─ ship
[verify] ─✗─► ↺ survey (max 3)
current: survey
✔ done ▶ ready · pending ⊘ skipped ⏸ waiting (external) ↺ back ✗ on fail"""

