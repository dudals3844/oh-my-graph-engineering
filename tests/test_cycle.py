"""Every checked node says where a failure goes (`on_fail`), `wf_fail` takes it,
the seeded types loop, and the drawn graph stays bounded."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from omge import checks, graph, hooks, library, store

from .conftest import ISSUE, reviewed_retro, run_hook, start, state, statuses

CYCLE = textwrap.dedent(
    """
    type: cy
    match: cycle test
    params:
      pr: '^[0-9]+$'
    nodes:
      - {id: implement, check: 'test -f impl.ok', on_fail: retry}
      - {id: test, after: [implement], check: 'test -f test.ok', on_fail: implement,
         on_fail_max: 2}
      - {id: pr, after: [test], check: 'test -n {pr}', on_fail: implement}
      - {id: retro, after: [pr]}
    """
)


@pytest.fixture
def cycle(workflows, repo):
    (workflows / "cy.yaml").write_text(CYCLE)
    Path(repo, "impl.ok").touch()
    tid = start(repo, type_="cy", params={"pr": "7"})
    checks.close(store.connect(), store.get_task(store.connect(), tid), "implement",
                 status="done", done_by="check", evidence="")
    return tid


def _fail(tid: int, node: str, reason: str = "red") -> str:
    conn = store.connect()
    return checks.fail(conn, store.get_task(conn, tid), node, reason)


# --- on_fail in the graph ----------------------------------------------------------

def test_a_submitted_checked_node_needs_on_fail_and_a_stored_one_reads_retry():
    nodes = "nodes:\n  - {id: a, check: 'test -d .'}\n"
    with pytest.raises(graph.WfError, match="needs `on_fail`.*retry"):
        graph.submitted(nodes, "new", "m")
    stored = graph.parse("type: x\nmatch: m\nnodes:\n  - {id: a, check: 'test -d .'}\n"
                         "  - {id: retro, after: [a]}\n")
    assert [(n["id"], n.get("on_fail"), n.get("on_fail_max")) for n in stored["nodes"]] == [
        ("a", "retry", 3), ("retro", None, None)]  # retro is wf's: exempt


@pytest.mark.parametrize("to", ["b", "c", "nowhere"])
def test_on_fail_must_name_an_ancestor_or_retry(to):
    body = ("nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n"
            "  - {id: b, check: 'test -d .', on_fail: retry}\n"
            f"  - {{id: c, after: [a], check: 'test -d .', on_fail: {to}}}\n")
    with pytest.raises(graph.WfError, match="ancestor"):
        graph.submitted(body, "new", "m")


def test_every_entry_point_that_submits_is_strict(workflows, repo):
    bare = {"nodes": [{"id": "a", "check": "test -d ."}]}
    with pytest.raises(graph.WfError, match="on_fail"):
        start(repo, type_="new", graph=bare, new_reason="x")
    tid = start(repo)  # the demo library type
    conn = store.connect()
    with pytest.raises(graph.WfError, match="on_fail"):
        graph.revise(conn, store.get_task(conn, tid), bare, "r")


def test_a_task_stored_before_on_fail_keeps_working_and_its_retro_must_add_it(cycle, repo):
    conn = store.connect()
    old = re.sub(r",?\s*on_fail(_max)?: \w+", "", CYCLE)
    conn.execute("UPDATE task SET graph = ? WHERE id = ?", (old, cycle))
    task = store.get_task(conn, cycle)
    assert "[▶ test]" in graph.status_text(conn, task)
    assert _fail(cycle, "test").startswith("`test` failure recorded (on_fail: retry)")
    Path(repo, "test.ok").touch()
    checks.run_ready(conn, task, 1e18)
    assert statuses(cycle)["retro"] == "pending"
    with pytest.raises(graph.WfError, match="on_fail"):
        reviewed_retro(conn, store.get_task(conn, cycle), "update_type", "none", name="cy")


# --- wf_fail -------------------------------------------------------------------------

def test_wf_fail_retry_records_and_stays(workflows, repo):
    (workflows / "cy.yaml").write_text(CYCLE)
    tid = start(repo, type_="cy")
    assert _fail(tid, "implement", "half done") == (
        "`implement` failure recorded (on_fail: retry) — redo it in place")
    assert statuses(tid)["implement"] == "pending" and state(tid) == "active"
    assert [tuple(r) for r in store.connect().execute(
        "SELECT kind, detail FROM event WHERE task = ? AND node = 'implement' ORDER BY rowid",
        (tid,))] == [("wf_fail", "retry: half done"), ("fail", "half done")]
    identity = store.connect().execute(
        "SELECT node, detail FROM event WHERE task = ? AND kind = 'git_identity'", (tid,)
    ).fetchone()
    assert identity["node"] == "@git"
    assert json.loads(identity["detail"]) == {"dir": repo, "branch": "demo-1"}
    with pytest.raises(graph.WfError, match="reason"):
        _fail(tid, "implement", " ")
    with pytest.raises(graph.WfError, match="is not ready"):
        _fail(tid, "test")


def test_retry_past_on_fail_max_stalls_like_a_back_edge(workflows, repo):
    body = textwrap.dedent(
        """
        nodes:
          - {id: a, check: 'test -f missing.ok', on_fail: retry, on_fail_max: 2}
          - {id: retro, after: [a]}
        """
    )
    tid = start(repo, type_="new", graph=body, new_reason="retry bound")
    for reason in ("again", "and again"):
        assert _fail(tid, "a", reason).startswith("`a` failure recorded (on_fail: retry)")
    with pytest.raises(graph.WfError, match="on_fail_max 2.*stalled.*PushNotification"):
        _fail(tid, "a", "once more")
    assert state(tid) == "stalled" and statuses(tid)["a"] == "pending"
    conn = store.connect()
    assert store.stop_streak(conn, f"task:{tid}") == (1, checks.EXHAUSTED)


def test_wf_fail_goes_back_reopens_and_clears_the_redone_params(cycle):
    conn = store.connect()
    out = _fail(cycle, "test", "3 tests red")
    assert out == "`test` failed → redo from `implement` (1/2)"
    assert statuses(cycle) == {"implement": "pending", "test": "pending", "pr": "pending",
                               "retro": "pending"}
    rows = store.node_rows(conn, cycle)
    assert rows["implement"]["loops"] == 1
    assert json.loads(store.get_task(conn, cycle)["params"]) == {}  # `pr` was the old attempt's
    kinds = [tuple(r) for r in conn.execute(
        "SELECT node, kind, detail FROM event WHERE task = ? AND kind IN ('wf_fail', 'back')",
        (cycle,))]
    assert kinds == [("test", "wf_fail", "implement: 3 tests red"), ("test", "back", "implement")]
    assert "[▶ implement ↺1]" in graph.render(store.get_task(conn, cycle),
                                              graph.parse(CYCLE), store.node_rows(conn, cycle))


def test_wf_fail_past_on_fail_max_stalls_and_stop_stays_quiet(cycle, repo):
    conn = store.connect()
    for _ in range(2):
        _fail(cycle, "test")
        checks.close(conn, store.get_task(conn, cycle), "implement", status="done",
                     done_by="check", evidence="")
    with pytest.raises(graph.WfError, match="on_fail_max 2.*stalled.*PushNotification"):
        _fail(cycle, "test")
    assert state(cycle) == "stalled" and statuses(cycle)["test"] == "pending"
    code, _ = hooks.stop(conn, store.get_task(conn, cycle), 1e18)
    assert code == 0  # announced by wf_fail; the user's next prompt or progress lifts it
    Path(repo, "test.ok").touch()  # progress lifts it like any stall
    checks.run_ready(conn, store.get_task(conn, cycle), 1e18)
    assert state(cycle) == "active"


def test_wf_fail_refuses_a_passing_check_but_not_a_human_nodes(workflows, repo):
    tid = start(repo)  # demo: m is human with `test -f merged.ok`, on_fail retry
    conn = store.connect()
    Path(repo, "a.ok").touch()
    with pytest.raises(graph.WfError, match="check passes — it is not a failure, wf_complete"):
        _fail(tid, "a")
    for n in ("a", "b"):
        checks.close(conn, store.get_task(conn, tid), n, status="done", done_by="check",
                     evidence="")
    Path(repo, "merged.ok").touch()
    assert "retry" in _fail(tid, "m", "changes requested")  # external outcome, not failure


def test_back_edges_count_per_edge_and_the_box_shows_the_total(cycle, repo):
    conn = store.connect()
    for _ in range(2):  # test → implement uses up its max 2
        _fail(cycle, "test")
        checks.close(conn, store.get_task(conn, cycle), "implement", status="done",
                     done_by="check", evidence="")
    Path(repo, "test.ok").touch()
    checks.close(conn, store.get_task(conn, cycle), "test", status="done", done_by="check",
                 evidence="")
    # pr → implement has its own max 3, though implement was already looped to twice.
    assert _fail(cycle, "pr", "no PR yet") == "`pr` failed → redo from `implement` (1/3)"
    assert store.loops(conn, cycle, "test", "implement") == 2
    assert store.loops(conn, cycle, "pr", "implement") == 1
    assert "[▶ implement ↺3]" in graph.render(store.get_task(conn, cycle), graph.parse(CYCLE),
                                              store.node_rows(conn, cycle))


@pytest.mark.parametrize("limit", ["0", "-1", "x", "true", "1.5"])
def test_on_fail_max_must_be_a_positive_int(limit):
    with pytest.raises(graph.WfError, match="on_fail_max"):
        graph.submitted(f"nodes:\n  - {{id: a, check: 'test -d .', on_fail: retry,"
                        f" on_fail_max: {limit}}}\n", "new", "m")


def test_a_node_without_on_fail_cannot_wf_fail(workflows, repo):
    tid = start(repo)
    conn = store.connect()
    for n in ("a", "b"):
        checks.close(conn, store.get_task(conn, tid), n, status="done", done_by="check",
                     evidence="")
    with pytest.raises(graph.WfError, match="has no on_fail"):
        _fail(tid, "h")  # human, no check


def test_stop_names_wf_fail_for_a_back_edge(cycle):
    conn = store.connect()
    code, text = hooks.stop(conn, store.get_task(conn, cycle), 1e18)
    assert code == 2 and "wf_fail(test, reason) → from `implement`" in text


def test_wf_fail_grants_ownership_and_shows_the_graph(cycle, repo):
    store.register(store.connect(), "s1")
    payload = {"cwd": repo, "session_id": "s1", "tool_name": "mcp__wf__wf_fail",
               "tool_input": {"issue_url": ISSUE, "node": "test", "reason": "red"}}
    assert run_hook("pre-tool-use", payload) == 0
    assert [t["id"] for t in store.owned(store.connect(), "s1")] == [cycle]
    shown = hooks.post_tool_use(store.connect(), "s1", "mcp__wf__wf_fail", payload["tool_input"])
    assert "[test] ─✗─► ↺ implement (max 2)" in shown["systemMessage"]


# --- the bounded drawing -------------------------------------------------------------

def _big(n: int = 50, name: str = "big") -> str:
    ids = [f"step_{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(n)]
    nodes = [{"id": ids[0], "check": "test -d .", "on_fail": "retry"}]
    nodes += [{"id": nid, "after": [ids[i]], "check": "test -d .", "on_fail": ids[i]}
              for i, nid in enumerate(ids[1:])]
    return graph.submitted({"nodes": nodes}, name, "m")


def test_a_long_graph_is_drawn_around_the_current_node(workflows, repo):
    (workflows / "big.yaml").write_text(_big())
    tid = start(repo, type_="big")
    conn = store.connect()
    for nid in [n["id"] for n in graph.parse(_big())["nodes"]][:20]:
        checks.close(conn, store.get_task(conn, tid), nid, status="done", done_by="check",
                     evidence="")
    task, t, rows = store.get_task(conn, tid), graph.parse(_big()), store.node_rows(conn, tid)
    text = graph.bounded(task, t, rows)
    lines = text.splitlines()
    assert len(lines) <= graph.MAX_LINES and len(text) <= graph.MAX_CHARS
    assert lines[1] == "✔ 19 done: step_aa → step_ab → … → step_as"
    assert "[▶ step_au]" in text and "[step_au] ─✗─► ↺ step_at (max 3)" in text
    assert "[· step_aw]" in text and "[· step_ax]" not in text  # two layers on
    assert lines[-3] == "… 28 more nodes after (full view in wf_status)"
    status = graph.status_text(conn, task)
    assert status.startswith(text + "\nnodes:\n[✔ step_aa]")
    assert "[· retro] ◄─ step_bx" in status and "… " not in status.split("nodes:")[1]
    assert "nodes:" not in graph.status_text(conn, task, listed=False)
    # Too many to list: the list is cut with a count, the graph part stays within MAX_CHARS.
    (workflows / "huge.yaml").write_text(_big(150, "huge"))
    huge = store.get_task(conn, start(repo, type_="huge", issue=ISSUE.replace("/1", "/2")))
    status = graph.status_text(conn, huge).split("\nparams=")[0]
    assert len(status) <= graph.MAX_CHARS and re.search(r"\n… \d+ more nodes$", status)


@pytest.mark.parametrize("mode", ["box", "chain"])
def test_bounded_keeps_every_ready_node(monkeypatch, mode):
    monkeypatch.setenv("WF_RENDER", mode)
    arm = [f"arm{side}{chr(97 + i)}" for side in "ab" for i in range(25)]
    left, right = arm[:25], arm[25:]
    nodes = [{"id": "root", "check": "test -d .", "on_fail": "retry"}]
    nodes += [{"id": left[0], "after": ["root"], "check": "test -d .", "on_fail": "retry"},
              {"id": right[0], "after": ["root"], "check": "test -d .", "on_fail": "retry"}]
    pairs = [(left[i], left[i + 1]) for i in range(24)]
    pairs += [(right[i], right[i + 1]) for i in range(24)]
    nodes += [{"id": nxt, "after": [cur], "check": "test -d .", "on_fail": "retry"}
              for cur, nxt in pairs]
    t = graph.parse(graph.submitted({"nodes": nodes}, "x", "m"))
    rows = {n["id"]: {"status": "done" if n["id"] == "root" else "pending", "loops": 0,
                      "route": None} for n in t["nodes"]}
    task = {"id": 1, "type": "x", "version": "", "state": "active",
            "issue_url": "https://github.com/example/demo/issues/1"}
    text = graph.bounded(task, t, rows)
    ready = {n["id"] for n in graph.ready(t, rows)}
    assert ready == {"armaa", "armba"} and ready <= set(re.findall(r"[a-z_]+", text))
    assert len(text.splitlines()) <= graph.MAX_LINES and len(text) <= graph.MAX_CHARS


# --- the seeded types ----------------------------------------------------------------

SHIPPED = library.WORKFLOWS  # before any test's `workflows` fixture swaps it


def _shipped(name: str) -> dict:
    return graph.parse((SHIPPED / f"{name}.yaml").read_text(), strict=True)


def test_issue_resolution_loops_back_to_implement():
    t = _shipped("issue-resolution")
    assert [(n["id"], n.get("on_fail")) for n in t["nodes"]] == [
        ("claim", "retry"), ("spec", "retry"), ("scope_review", "spec"),
        ("plan", "spec"), ("implement", "spec"),
        ("test", "implement"),
        ("review", "implement"), ("pr", "retry"), ("ci", "implement"),
        ("merge", "implement"), ("cleanup", "implement"),
        ("retro", None)]
    assert next(n for n in t["nodes"] if n["id"] == "merge")["kind"] == "human"
    text = " ".join(str(n.get("check")) for n in t["nodes"] if n["id"] != "retro")
    assert "example/" not in text and "{WF_ISSUE" in text and "$WF_BRANCH" in text


def test_issue_resolution_pr_failure_retries_at_pr(repo):
    tid = start(repo, type_="issue-resolution", params={"complexity": "simple"})
    conn = store.connect()
    for nid in ("claim", "spec", "scope_review", "implement", "test", "review"):
        checks.close(conn, store.get_task(conn, tid), nid, status="done", done_by="check",
                     evidence="")
    assert "on_fail: retry" in _fail(tid, "pr", "API error")
    assert statuses(tid)["pr"] == "pending" and statuses(tid)["implement"] == "done"
    assert store.loops(conn, tid, "pr", "implement") == 0


def test_investigation_starts_without_an_issue_and_verify_goes_back_to_collect(repo, tmp_path):
    t = _shipped("investigation")
    assert [(n["id"], n.get("on_fail")) for n in t["nodes"]] == [
        ("collect", "retry"), ("verify", "collect"), ("report", "retry"), ("retro", None)]
    tid = start(repo, type_="investigation", issue=None, title="investigate the cause")
    assert store.get_task(store.connect(), tid)["type"] == "investigation"
    with pytest.raises(graph.WfError, match="needs an issue"):
        start(repo, type_="issue-resolution", issue=None, title="x")


def _run(t: dict, node_id: str, notes: Path, cwd: str) -> int:
    node = next(n for n in t["nodes"] if n["id"] == node_id)
    cmd, env = checks.render(node["check"], {"notes": str(notes)}, t["params"])
    return checks.run(cmd, cwd, env, 30)[0]


def test_investigation_checks_never_import_from_the_checkout(repo, tmp_path):
    t = _shipped("investigation")
    marker = tmp_path / "imported"
    Path(repo, "re.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    notes = tmp_path / "notes.md"
    notes.write_text("- re.py:1 x\n- https://example.com/a\n- https://example.com/b\n")
    assert [_run(t, nid, notes, repo) for nid in ("collect", "verify")] == [0, 0]
    assert not marker.exists()


def test_investigation_checks_read_the_notes(repo, tmp_path):
    t = _shipped("investigation")
    notes = tmp_path / "notes.md"
    Path(repo, "mod.py").write_text("a\nb\nc\n")
    notes.write_text("- mod.py:2 says b\n- https://example.com/x\n- host 10.0.0.1:5432\n")
    assert _run(t, "collect", notes, repo) != 0  # two sources; an address is none
    notes.write_text("- mod.py:2 says b\n- https://example.com/x\n- mod.py:9 says z\n")
    assert _run(t, "collect", notes, repo) == 0
    assert _run(t, "verify", notes, repo) != 0  # mod.py has 3 lines
    assert _run(t, "verify", tmp_path / "absent.md", repo) != 0
    (tmp_path / "bare.md").write_text("it is b\n")
    assert _run(t, "verify", tmp_path / "bare.md", repo) != 0  # no citation at all
    notes.write_text("- mod.py:2 says b\n- https://example.com/x\n- mod.py:3 says c\n"
                     "- the db at 10.0.0.1:5432 and localhost:8080 refused\n")
    assert _run(t, "verify", notes, repo) == 0  # an address is not a citation
    notes.write_text(notes.read_text() + "- missing.py3:1 says d\n")
    assert _run(t, "collect", notes, repo) == 0  # collect counts it as a source
    assert _run(t, "verify", notes, repo) != 0  # verify rejects the same match
    assert _run(t, "report", notes, repo) != 0
    notes.write_text(notes.read_text() + "## Conclusion\n\nit is b.\n")
    assert _run(t, "report", notes, repo) == 0


@pytest.mark.parametrize(("cite", "ok"), [
    ("Dockerfile:1", True),  # extensionless file, in range
    ("Dockerfile:999", False),  # extensionless, past the end
    ("src/x.py:2", True),
    ("src/x.py:0", False),  # lines start at 1
    ("src/gone.py:1", False),  # a cited path that does not exist
    ("https://host:443/a/b.py:9", True),  # a URL with a port is not a path
    ("001.sql:1", True),  # a digit-led name, existing
    ("002.sql:1", False),  # a digit-led name, missing
    (".env:1", False),  # a dot-led name, missing
    ("missing.py3:1", False),  # a digit in the extension, missing
    ("src/x.py:9223372036854775808", False),  # past any machine integer
    ("src/x.py:" + "0" * 4300 + "1", True),  # leading zeros, line 1
    ("src/x.py:" + "9" * 5000, False),  # past int()'s digit limit
    ("src/x.py:\uff11", False),  # fullwidth digits are no citation, so no source
    ("src/x.py:\uff11 and https://example.com/x", True),  # the URL is the source
])
def test_investigation_verify_bounds_every_citation(repo, tmp_path, cite, ok):
    t = _shipped("investigation")
    Path(repo, "Dockerfile").write_text("FROM x\n")
    Path(repo, "src").mkdir(exist_ok=True)
    Path(repo, "src", "x.py").write_text("a\nb\n")
    Path(repo, "001.sql").write_text("select 1;\n")
    notes = tmp_path / "notes.md"
    notes.write_text(f"- mod at {cite} says so\n")
    assert (_run(t, "verify", notes, repo) == 0) is ok


def test_issue_resolution_implement_check_wants_a_clean_commit_ahead(tmp_path):
    t = _shipped("issue-resolution")
    node = next(n for n in t["nodes"] if n["id"] == "implement")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    subprocess.run([*git, "push", "-q", "origin", "HEAD:main"], check=True)
    subprocess.run([*git, "fetch", "-q"], check=True)

    def run(complexity: str = "simple") -> int:
        cmd, env = checks.render(node["check"], {"complexity": complexity}, t["params"])
        return checks.run(cmd, str(work), env, 30)[0]

    assert run() != 0  # nothing ahead
    Path(work, "f").write_text("x")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "change"], check=True)
    assert run() == 0
    Path(work, "f").write_text("y")
    assert run() != 0  # dirty
    subprocess.run([*git, "commit", "-qam", "y"], check=True)
    for i in range(5):  # 6 files: too big to have been simple
        Path(work, f"g{i}").write_text("x")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "wide"], check=True)
    assert run() != 0 and run("complex") == 0
    subprocess.run([*git, "reset", "-q", "--hard", "HEAD~1"], check=True)
    Path(work, "f").write_text("x\n" * 301)
    subprocess.run([*git, "commit", "-qam", "long"], check=True)
    assert run() != 0 and run("complex") == 0


@pytest.mark.parametrize(("text", "expected"), [
    ("PR fixing the login bug — implement, test, review, merge after CI passes", "issue-resolution"),
    ("investigate the cause of listener latency and report", "investigation"),
])
def test_typical_requests_find_the_seeded_types(text, expected):
    assert expected in library.similar(store.connect(), text)


def _issue_check(node_id: str, cwd: str, tmp_path: Path, gh: dict[str, str], branch: str,
                 params: dict | None = None) -> int:
    """Run an issue-resolution check with a fake `gh` that prints, per endpoint (or
    `pr_checks`), what the real one's `--jq` would, exiting with `<endpoint>.rc` if given.
    An `<endpoint>.json` answer is the API's JSON instead, run through the check's own
    `--jq` filter by jq. Every call is appended to `gh_log`."""
    t = _shipped("issue-resolution")
    node = next(n for n in t["nodes"] if n["id"] == node_id)
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (tmp_path / "gh_out").mkdir(exist_ok=True)
    for endpoint, out in gh.items():
        (tmp_path / "gh_out" / endpoint.replace("/", "_")).write_text(out)
    fake = bin_ / "gh"
    fake.write_text(f"""#!/bin/bash
echo "$*" >> "{tmp_path}/gh_log"
for a; do case $a in repos/*) p=$a;; esac; done
[ "$1 $2" = "pr checks" ] && p=pr_checks
f="{tmp_path}/gh_out/${{p//\\//_}}"
while [ $# -gt 0 ]; do [ "$1" = --jq ] && q=$2; shift; done
[ -e "$f.json" ] && exec jq -r "$q" "$f.json"
[ -e "$f" ] || {{ echo "gh: Not Found (HTTP 404)" >&2; exit 1; }}
cat "$f"
exit "$(cat "$f.rc" 2>/dev/null || echo 0)"
""")
    fake.chmod(0o755)
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", "WF_BRANCH": branch,
           "WF_ISSUE": "https://github.com/example/demo/issues/5"}
    cmd, values = checks.render(node["check"], params or {"pr": "7", "pr_repo": ""}, t["params"])
    return checks.run(cmd, cwd, {**env, **values}, 30)[0]


@pytest.mark.parametrize(("branch", "code"), [
    ("dudals3844/demo-5", 0), ("demo-5-fix", 0), ("demo-50", 1), ("demo-51x", 1)])
def test_claim_wants_the_issue_number_not_a_longer_one(repo, tmp_path, branch, code):
    gh = {"repos/example/demo/issues/5": "true"}
    assert bool(_issue_check("claim", repo, tmp_path, gh, branch)) == bool(code)


NO_CHECKS = "no checks reported on the 'demo-5' branch"


@pytest.mark.parametrize(("out", "rc", "age", "code"), [
    ("lint\tpass", 0, None, 0),
    ("lint\tpending", 8, None, 75),  # gh pr checks exits 8 while checks are pending
    ("lint\tfail", 1, None, 1),
    ("HTTP 502: Bad Gateway", 1, None, 1),  # a gh error is not "no checks"
    (NO_CHECKS, 1, "60", 75),  # a young head: CI may not have registered yet
    (NO_CHECKS, 1, "600", 0),  # still none after the grace: the repo has no CI for this PR
])
def test_ci_maps_gh_pr_checks(repo, tmp_path, out, rc, age, code):
    sha = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    gh = {"repos/example/demo/pulls/7": sha, "pr_checks": out, "pr_checks.rc": str(rc)}
    if age is not None:
        gh[f"repos/example/demo/commits/{sha}"] = age
    assert _issue_check("ci", repo, tmp_path, gh, "demo-5") == code


def test_cleanup_checks_only_the_branch_and_issue_not_merge_commit_ci(repo, tmp_path):
    # The PR's head ref "m": no such branch, locally or on origin.
    gh = {"repos/example/demo/pulls/7.json": json.dumps({"head": {"ref": "m"},
                                                         "body": "Closes #5"}),
          "repos/example/demo/issues/5.json": json.dumps({"state": "closed"})}
    subprocess.run(["git", "-C", repo, "remote", "set-url", "origin", repo], check=True)
    assert _issue_check("cleanup", repo, tmp_path, gh, "demo-5") == 0
    calls = (tmp_path / "gh_log").read_text()
    assert "check-runs" not in calls and "/status" not in calls and "pr checks" not in calls


@pytest.mark.parametrize(("body", "state", "code"), [
    ("Closes https://github.com/example/demo/issues/5", "open", 1),
    ("Closes https://github.com/example/demo/issues/5", "closed", 0),
    ("FIXES example/demo#5", "open", 1),
    ("resolved: other/lib#3", "open", 1),
    ("resolved: other/lib#3", "closed", 0),
    ("Closes #5", "open", 1),
    ("Part of #5, see `closes #5`", "open", 0),  # partial: no closing keyword
])
def test_cleanup_wants_every_issue_the_pr_closes_closed(repo, tmp_path, body, state, code):
    gh = {"repos/example/demo/pulls/7.json": json.dumps({"head": {"ref": "m"}, "body": body}),
          "repos/example/demo/issues/5.json": json.dumps({"state": state}),
          "repos/other/lib/issues/3.json": json.dumps({"state": state})}
    subprocess.run(["git", "-C", repo, "remote", "set-url", "origin", repo], check=True)
    assert _issue_check("cleanup", repo, tmp_path, gh, "demo-5") == code


def test_optional_params_survive_a_loop_back(repo, tmp_path, workflows):
    Path(repo, "tests").mkdir()
    Path(repo, ".venv").symlink_to(sys.prefix, target_is_directory=True)
    assert _issue_check("test", repo, tmp_path, {}, "demo-5", {"test_scope": "tests"}) == 5
    # A setting is not an attempt's proof: going back keeps it, unlike `pr`.
    (workflows / "cy.yaml").write_text(CYCLE.replace(
        "pr: '^[0-9]+$'", "pr: '^[0-9]+$'\n  cmd: '^$|^[a-z ]+$'").replace(
        "check: 'test -f test.ok'", "check: 'test -f test.ok{cmd}'"))
    tid = start(repo, type_="cy", params={"pr": "7", "cmd": "x"})
    conn = store.connect()
    checks.close(conn, store.get_task(conn, tid), "implement", status="done", done_by="check",
                 evidence="")
    _fail(tid, "test")
    assert json.loads(store.get_task(conn, tid)["params"]) == {"cmd": "x"}


def test_review_verdict_must_end_its_line():
    check = next(n["check"] for n in _shipped("issue-resolution")["nodes"] if n["id"] == "review")
    assert "export HEAD_SHA=$(git rev-parse HEAD)" in check
    assert 'startswith($s)' in check
    # The jq regex, read with Python's: Oniguruma's `$` is end of line, like re.M.
    rx = re.compile(re.search(r'test\("(verdict.*?)"; "i"\)', check)[1].replace("\\n", "\n"),
                    re.I | re.M)
    assert rx.search("review abc1234\nVerdict: pass\n") and rx.search("verdict: PASS")
    assert not rx.search("Verdict: pass — 2 blockers") and not rx.search("verdict: passable")


def test_wf_complete_set_fail_return_a_header_not_the_drawing(cycle, repo):
    from omge import server

    def call(name, **kw):
        return server.mcp._tool_manager._tools[name].fn(task=cycle, **kw)

    outs = [call("wf_set", key="pr", value="8"), call("wf_fail", node="test", reason="red")]
    outs.append(call("wf_complete", node="implement", evidence=""))
    for out in outs:
        assert not set("┌┏─│▼") & set(out) and "- ready `" in out
    assert "failed" in outs[1] and "implement" in outs[2]
    assert "─" in call("wf_status")


def test_a_closed_task_is_named_with_its_state_not_reported_missing(cycle):
    from omge import server
    from mcp.server.mcpserver.exceptions import ToolError

    tools = server.mcp._tool_manager._tools
    store.set_state(store.connect(), cycle, "dropped")
    for name, kw in [("wf_retro", {"task": cycle}), ("wf_status", {"issue_url": ISSUE})]:
        with pytest.raises(ToolError, match=rf"task #{cycle} is already closed as dropped .* retro: pending"):
            tools[name].fn(**kw)
    with pytest.raises(ToolError, match="no open task"):
        tools["wf_status"].fn(task=cycle + 1)
