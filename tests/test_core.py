"""store / graph / checks: graph validation, ready nodes, params, timeouts, migration."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from omge import checks, graph, i18n, store

from .conftest import ISSUE, TEMPLATE, close, reviewed_retro, start, state, statuses


def _body(nodes: str, params: str = "{}") -> str:
    return f"type: x\nmatch: m\nparams: {params}\nnodes:\n{nodes}"


@pytest.mark.parametrize(
    ("nodes", "params", "message"),
    [
        ("  - {id: a, check: 'test -d .'}\n", "{}", "retro"),
        ("  - {id: retro}\n  - {id: a, check: 'test -d .'}\n", "{}", "retro"),
        ("  - {id: retro, check: 'test -d .'}\n", "{}", "wf supplies it"),
        ("  - {id: a}\n  - {id: retro}\n", "{}", "kind: human"),
        ("  - {id: a, check: 'test -d .', after: [retro]}\n  - {id: retro}\n", "{}", "earlier"),
        ("  - {id: a, check: 'test -n {nope}'}\n  - {id: retro}\n", "{}", "undeclared"),
        ("  - {id: retro}\n", "{p: '('}", "bad regex"),
        ("  - {id: a, check: 'test -d .'}\n  - {id: a, check: 'test -d .'}\n", "{}", "duplicated"),
        ("  - {id: a, check: 'test -d .', next: [{to: z}]}\n  - {id: retro}\n", "{}", "no node"),
        ("  - {id: a, check: 'test -d .', next: [{to: b, when: 'test {p}'}]}\n"
         "  - {id: b, check: 'test -d .'}\n  - {id: retro}\n", "{}", "undeclared"),
        ("  - {id: a, check: 'test -d .', next: [{to: retro, when: 5}]}\n  - {id: retro}\n",
         "{}", "cannot split the branches"),
        # Back to a node that is not an ancestor: a sideways jump, not a loop.
        ("  - {id: a, check: 'test -d .'}\n  - {id: b, check: 'test -d .'}\n"
         "  - {id: c, check: 'test -d .', after: [a], next: [{to: b}]}\n  - {id: retro}\n", "{}",
         "not to an ancestor"),
        ("  - {id: a, check: 'test -d .', skill: 5}\n  - {id: retro}\n", "{}", "skill"),
        ("  - {id: retro, skill: x}\n", "{}", "skill"),
    ],
)
def test_parse_rejects(nodes, params, message):
    with pytest.raises(graph.WfError, match=message):
        graph.parse(_body(nodes, params))


def test_parse_accepts_human_after_retro_and_bash_braces():
    t = graph.parse(_body(
        "  - {id: a, check: 'test -n ${HOME}{p}'}\n  - {id: retro}\n"
        "  - {id: z, kind: human, after: [retro]}\n",
        "{p: '^x$'}",
    ))
    assert [n["id"] for n in t["nodes"]] == ["a", "retro", "z"]
    assert t["nodes"][1]["check"] == graph.RETRO_CHECK


def test_forward_next_makes_its_source_a_predecessor():
    t = graph.parse(_body("  - {id: a, check: 'test -d .', next: [{to: b}, {to: c}]}\n"
                          "  - {id: b, check: 'test -d .'}\n  - {id: c, check: 'test -d .'}\n"
                          "  - {id: retro, after: [b, c]}\n"))
    assert [n["after"] for n in t["nodes"]] == [[], ["a"], ["a"], ["b", "c"]]
    assert graph.descendants(t, "a") == {"a", "b", "c", "retro"}


def test_submitted_graph_gets_a_retro_after_its_leaves():
    body = graph.submitted("nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n"
                           "  - {id: b, check: 'test -d .', on_fail: a, after: [a]}\n", "new",
                           "m")
    assert graph.parse(body)["nodes"][-1]["after"] == ["b"]
    looped = graph.submitted(  # a loop back, or onto itself, is no forward edge
        "nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n"
        "  - {id: work, check: 'test -d .', on_fail: retry, after: [a],"
        " next: [{to: work, when: 'false'}, {to: a, when: 'false'}]}\n", "new", "m")
    assert graph.parse(looped)["nodes"][-1]["after"] == ["work"]
    for empty in ("nodes: []", "nodes:\n  - {id: retro}\n", "just text"):
        with pytest.raises(graph.WfError, match="nodes"):
            graph.submitted(empty, "new", "m")
    with pytest.raises(graph.WfError, match="kind: human"):  # every node still needs a check
        graph.submitted("nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n"
                        "  - {id: b, do: x}\n",
                        "new", "m")
    with pytest.raises(graph.WfError, match="at least one node with a check"):  # waiting alone checks nothing
        graph.submitted("nodes:\n  - {id: a, kind: human, do: wait}\n", "new", "m")
    assert not graph._trivial("echo ok | grep -q ok") and not graph._trivial("test -f a")


def test_checks_see_when_their_task_started(workflows, repo):
    # The retro check counts only comments from after this task started, so a
    # second task on the same issue cannot pass on the first one's retro.
    conn = store.connect()
    task = store.get_task(conn, start(repo))
    since = checks._env(task)["WF_SINCE"]
    assert since == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(task["created"]))
    assert checks._env(task)["WF_DIR"] == repo
    assert checks._env(task)["WF_ISSUE_NUMBER"] == "1"


def test_ready_requires_every_predecessor_closed(workflows, repo):
    tid = start(repo)
    conn = store.connect()
    t = graph.parse(store.get_task(conn, tid)["graph"])
    assert [n["id"] for n in graph.ready(t, store.node_rows(conn, tid))] == ["a"]
    close(tid, "a")
    close(tid, "b", status="waived")
    assert [n["id"] for n in graph.ready(t, store.node_rows(conn, tid))] == ["h", "m"]


def test_render_validates_and_quotes():
    patterns = {"name": "^[a-z;]+$"}
    # The regex admits `;`, so only the quoting stands between it and the shell.
    assert checks.render("echo {name} ${name}", {"name": "a;b"}, patterns) == (
        'echo ${WF_P_name+"${WF_P_name}"} ${name}', {"WF_P_name": "a;b"})
    with pytest.raises(graph.WfError, match="is unset"):
        checks.render("echo {name}", {"name": ""}, patterns)
    with pytest.raises(graph.WfError, match="does not match"):
        checks.render("echo {name}", {"name": "A"}, patterns)


def test_a_bare_placeholder_stays_literal_when_run(tmp_path):
    value = 'x"; touch pwned; "'
    cmd, env = checks.render('printf %s {p} "$(printf %s {p})"', {"p": value}, {"p": ".*"})
    code, out = checks.run(cmd, str(tmp_path), env, 10)
    assert (code, out) == (0, value * 2) and not (tmp_path / "pwned").exists()


INJECTED = "$(printf INJECTED >&2)"


@pytest.mark.parametrize("shell", [
    "printf %s {p}", 'printf %s "{p}"', 'printf %s "$(printf "%s" "{p}")"',
    'printf %s "$(printf %s {p})"', "printf %s $\\\n'x{p}'", "cat <<EOF\n{p}\nEOF",
])
def test_a_param_value_is_never_run_as_code(tmp_path, shell):
    t = graph.parse(_body(f"  - {{id: a, check: {json.dumps(shell)}}}\n  - {{id: retro}}\n",
                          "{p: '.*'}"))
    cmd, env = checks.render(t["nodes"][0]["check"], {"p": INJECTED}, t["params"])
    code, out = checks.run(cmd, str(tmp_path), env, 10)
    assert code == 0 and "INJECTED" not in out.replace(INJECTED, "")


def test_a_param_value_in_a_when_is_never_run_as_code(workflows, repo):
    (workflows / "when.yaml").write_text(_body(
        "  - {id: a, check: 'test -d .',"
        " next: [{to: retro, when: 'exec 2>err; [ \"{p}\" != x ]'}]}\n"
        "  - {id: retro}\n", "{p: '.*'}").replace("type: x", "type: when"))
    tid = start(repo, type_="when", params={"p": INJECTED})
    conn = store.connect()
    checks.run_ready(conn, store.get_task(conn, tid), time.monotonic() + 30)
    assert statuses(tid)["a"] == "done"
    assert Path(repo, "err").read_text() == ""


def test_a_param_value_with_a_space_is_one_argument(tmp_path):
    cmd, env = checks.render("printf '<%s>' {p}", {"p": "a b"}, {"p": ".*"})
    assert checks.run(cmd, str(tmp_path), env, 10) == (0, "<a b>")


def test_a_numeric_regex_keeps_code_out_of_bash_arithmetic(tmp_path):
    # `[[ -eq ]]` evaluates its operands as arithmetic, so only the regex stops this value.
    t = graph.parse(_body("  - {id: a, check: '[[ {n} -eq 1 ]]'}\n  - {id: retro}\n",
                          "{n: '^[0-9]+$'}"))
    with pytest.raises(graph.WfError, match="does not match"):
        checks.render(t["nodes"][0]["check"], {"n": "a[$(touch marker)]"}, t["params"])
    cmd, env = checks.render(t["nodes"][0]["check"], {"n": "1"}, t["params"])
    assert checks.run(cmd, str(tmp_path), env, 10)[0] == 0
    assert not (tmp_path / "marker").exists()


def test_overlapping_connections_keep_wal_locked(monkeypatch):
    child = """
from contextlib import closing
from omge import store

with closing(store.connect()) as conn:
    conn.execute("INSERT INTO repro VALUES (2)")
"""
    opened = []
    original_open = os.open

    def record_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if path == Path(os.environ["WF_DB"]):
            opened.append(path)
        return fd

    with closing(store.connect()) as first:
        monkeypatch.setattr(os, "open", record_open)
        first.execute("CREATE TABLE repro (value INTEGER)")
        first.execute("INSERT INTO repro VALUES (1)")
        for count in (1, 3):
            with closing(store.connect()) as second:
                assert second.execute("SELECT count(*) FROM repro").fetchone()[0] == count
                subprocess.run([sys.executable, "-c", child], check=True, timeout=10)
                assert first.execute("SELECT count(*) FROM repro").fetchone()[0] == count + 1
                first.execute("INSERT INTO repro VALUES (3)")
    assert not opened  # SQLite versions that recover still must not reopen the DB outside SQLite.


def test_state_is_owner_only_under_umask_022(workflows, repo, tmp_path, monkeypatch):
    from omge import library, server

    db = tmp_path / "state" / "wf.db"
    db.parent.mkdir(mode=0o755)  # existing state is tightened too
    monkeypatch.setenv("WF_DB", str(db))
    old = os.umask(0o022)
    try:
        with closing(store.connect()) as conn:
            tid = graph.start(conn, "demo", repo, None, {}, title="t")
            out = server.mcp._tool_manager._tools["wf_retro"].fn(task=tid)
            lib = library.root()
            retro = next((db.parent / "retro").iterdir())
            assert str(retro) in out
            private = {db.parent: 0o700, db.parent / "retro": 0o700, lib: 0o700,
                       db: 0o600, db.parent / "wf.db-wal": 0o600,
                       db.parent / "wf.db-shm": 0o600, retro: 0o600}
            assert {p: stat.S_IMODE(p.stat().st_mode) for p in private} == private
    finally:
        os.umask(old)


def test_run_timeout_kills_the_whole_process_group(tmp_path):
    begun = time.monotonic()
    # The backgrounded grandchild holds stdout open; killing only bash would hang.
    code, tail = checks.run("sleep 30 & sleep 30", str(tmp_path), {}, 1)
    assert code == 124 and "timeout" in tail
    assert time.monotonic() - begun < 10


def test_run_timeout_bounds_a_pipe_held_outside_the_group(tmp_path):
    begun = time.monotonic()
    # `setsid` escapes the group kill and keeps stdout open for 8 s.
    code, _ = checks.run("setsid sleep 8 & sleep 30", str(tmp_path), {}, 1)
    assert code == 124
    assert time.monotonic() - begun < 4


def test_start_refuses_duplicates_bad_issue_dir_and_params(workflows, repo, tmp_path):
    with pytest.raises(graph.WfError, match="issue_url"):
        start(repo, issue="https://example.com/someone/x/issues/1")
    with pytest.raises(graph.WfError, match="dir"):
        start(str(tmp_path / "nowhere"))
    with pytest.raises(graph.WfError, match="does not match"):
        start(repo, params={"name": "UPPER"})
    with pytest.raises(graph.WfError, match="unknown workflow"):
        start(repo, type_="../demo")
    with pytest.raises(graph.WfError, match="graph"):
        start(repo, type_="new")
    tid = start(repo)
    with pytest.raises(graph.WfError, match=f"#{tid}"):
        start(repo, type_="new", graph="nodes: [{id: a, check: 'test -d .'}]")
    # The same dir can hold another issue's task: the key is the issue, not the place.
    start(repo, issue=ISSUE.replace("/1", "/2"))


def test_start_race_is_a_refusal(workflows, repo, monkeypatch):
    start(repo)
    monkeypatch.setattr(store, "open_task", lambda *_: None)  # lost the check-then-insert
    with pytest.raises(graph.WfError, match="just opened"):
        start(repo)


def test_start_records_the_library_version(workflows, repo):
    task = store.get_task(store.connect(), start(repo))
    assert task["version"] == graph.library.body("demo")[1] and len(task["version"]) == 40


def test_complete_runs_the_check(workflows, repo):
    conn = store.connect()
    task = store.get_task(conn, start(repo))
    with pytest.raises(graph.WfError, match="ready"):
        checks.complete(conn, task, "b", "ev")
    with pytest.raises(graph.WfError, match="exit=1"):
        checks.complete(conn, task, "a", "ev")
    Path(repo, "a.ok").touch()
    checks.complete(conn, task, "a", "made it")
    row = store.node_rows(conn, task["id"])["a"]
    assert (row["status"], row["done_by"], row["evidence"], row["attempts"]) == (
        "done", "check", "made it", 2)


@pytest.mark.parametrize(("target", "after"), [
    ("fresh", "dropped"), ("older", "active"), ("closed", "done"), ("parent", "active")])
def test_a_passing_check_drops_only_the_fresh_open_e2e_task_it_cited(workflows, repo, target,
                                                                      after):
    conn = store.connect()
    ids = {"older": start(repo, issue=ISSUE.replace("/1", "/2"))}
    ids["parent"] = start(repo, type_="new", new_reason="e2e", graph={
        "params": {"e2e_task": "^[0-9]+$"},
        "nodes": [{"id": "prep", "check": "test -d .", "on_fail": "retry"},
                  {"id": "live", "do": "wf_set e2e_task=<id>", "check": "test {e2e_task} -gt 0",
                   "on_fail": "retry", "after": ["prep"]}]})
    ids["fresh"] = start(repo, issue=ISSUE.replace("/1", "/3"))
    ids["closed"] = start(repo, issue=ISSUE.replace("/1", "/4"))
    with conn:
        store.set_state(conn, ids["closed"], "done")
    graph.set_param(conn, store.get_task(conn, ids["parent"]), "e2e_task", str(ids[target]))
    close(ids["parent"], "prep")  # a check that does not cite it leaves it alone
    assert state(ids["fresh"]) == "active"
    checks.complete(conn, store.get_task(conn, ids["parent"]), "live", "ok")
    assert state(ids[target]) == after
    assert (store.last_event(conn, ids[target], "") or ("",))[0] == (
        "drop" if after == "dropped" else "")


def test_the_agent_closes_a_checkless_node_on_evidence_only(workflows, repo):
    tid = start(repo)
    close(tid, "a", "b")
    conn = store.connect()
    with pytest.raises(graph.WfError, match="evidence"):
        checks.complete(conn, store.get_task(conn, tid), "h", "  ")
    checks.complete(conn, store.get_task(conn, tid), "h", "asked in the issue, answered yes")
    row = store.node_rows(conn, tid)["h"]
    assert (row["status"], row["done_by"], row["evidence"]) == (
        "done", "agent", "asked in the issue, answered yes")
    # A node WITH a check still closes only when it passes, evidence or not.
    with pytest.raises(graph.WfError, match="exit=1"):
        checks.complete(conn, store.get_task(conn, tid), "m", "merged, trust me")
    assert store.node_rows(conn, tid)["m"]["status"] == "pending"


def test_a_silent_failing_check_still_gets_a_reason(workflows, repo):
    tid = start(repo)
    conn = store.connect()
    close(tid, "a", "b")
    task = store.get_task(conn, tid)
    for node, word in (("a", "`do`"), ("m", "ask the user")):
        n = next(x for x in graph.parse(task["graph"])["nodes"] if x["id"] == node)
        code, tail = checks.attempt(conn, task, n, time.monotonic() + 30)
        row = store.node_rows(conn, tid)[node]
        fail = conn.execute("SELECT detail FROM event WHERE task=? AND node=? AND kind='fail'",
                            (tid, node)).fetchall()[-1][0]
        assert code and word in tail and row["last_output_tail"] == tail and fail
    assert "ask the user" not in store.node_rows(conn, tid)["a"]["last_output_tail"]


def test_the_agent_skips_a_ready_retro_with_a_reason_and_records_no_retro(workflows, repo):
    tid = start(repo)
    conn = store.connect()
    with pytest.raises(graph.WfError, match="ready"):
        checks.skip_retro(conn, store.get_task(conn, tid), "nothing to improve")
    close(tid, "a", "b", "h", "m")
    with pytest.raises(graph.WfError, match="reason"):
        checks.skip_retro(conn, store.get_task(conn, tid), " ")
    checks.skip_retro(conn, store.get_task(conn, tid), "one-off")
    row = store.node_rows(conn, tid)["retro"]
    assert (row["status"], row["done_by"], state(tid)) == ("done", "agent", "done")
    assert "one-off" in row["evidence"]
    assert not conn.execute("SELECT 1 FROM retro WHERE task = ?", (tid,)).fetchone()


def test_a_repeated_same_type_revise_blocks_the_retro_skip(workflows, repo):
    # Hand-revising the same node set into a second task of the type is the
    # repetition update_type exists to fold into the default; skipping on top of
    # it would serve the third task the same broken graph again.
    def revised(node: str) -> str:
        probe = f"  - {{id: {node}, check: 'test -d .', on_fail: retry, after: [a]}}\n"
        return TEMPLATE.replace("  - id: retro", probe + "  - id: retro")

    a = start(repo)
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, a), revised("probe"), "probe was needed")
    close(a, "a", "probe", "b", "h", "m")
    checks.skip_retro(conn, store.get_task(conn, a), "one-off")  # first hand-revise: still fine
    b = start(repo, issue=ISSUE.replace("/1", "/2"))
    graph.revise(conn, store.get_task(conn, b), revised("probe"), "probe was needed again")
    close(b, "a", "probe", "b", "h", "m")
    with pytest.raises(graph.WfError, match="update_type"):
        checks.skip_retro(conn, store.get_task(conn, b), "done it again")
    # A different node set is a different revision, not a repeat.
    c = start(repo, issue=ISSUE.replace("/1", "/3"))
    graph.revise(conn, store.get_task(conn, c), revised("scan"), "scan was needed")
    close(c, "a", "scan", "b", "h", "m")
    checks.skip_retro(conn, store.get_task(conn, c), "another fix")


def test_retro_needs_the_record_and_the_task_ends_at_once(workflows, repo):
    tid = start(repo)
    close(tid, "a", "b", "h", "m")
    conn = store.connect()
    task = store.get_task(conn, tid)
    with pytest.raises(graph.WfError, match="wf_retro"):
        checks.complete(conn, task, "retro", "ev")
    with pytest.raises(graph.WfError, match="verdict"):
        graph.record_retro(conn, task, "template_pr", "none")
    with pytest.raises(graph.WfError, match="deviations"):
        graph.record_retro(conn, task, "no_change", " ")
    reviewed_retro(conn, task, "no_change", "none")
    checks.complete(conn, task, "retro", "ev")
    # Nothing pending: done there and then, not at the next Stop.
    assert state(tid) == "done"


CARRY = """
type: carry
match: carry test
nodes:
  - id: review
    check: test -f receipt
    on_fail: retry
    carry: code
  - id: plain
    after: [review]
    check: test -f receipt
    on_fail: retry
  - id: retro
    after: [plain]
"""


def _commit(repo: str, name: str, text: str) -> None:
    Path(repo, name).write_text(text)
    for args in (["add", name],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name]):
        subprocess.run(["git", "-C", repo, *args], check=True)


def test_a_carry_node_passes_again_while_the_code_is_the_same(workflows, repo):
    (workflows / "carry.yaml").write_text(CARRY)
    conn = store.connect()
    _commit(repo, "m.py", "x = 1\n")
    tid = start(repo, type_="carry")
    Path(repo, "receipt").touch()
    checks.run_ready(conn, store.get_task(conn, tid), time.monotonic() + 30)
    assert statuses(tid)["plain"] == "done"
    Path(repo, "receipt").unlink()  # a receipt names the old HEAD: it no longer counts

    def redo() -> dict[str, str]:
        for n in ("review", "plain"):
            store.set_node(conn, tid, n, status="pending")
        checks.run_ready(conn, store.get_task(conn, tid), time.monotonic() + 30)
        return statuses(tid)

    _commit(repo, "m.py", "x  =  1  # lint only\n")
    st = redo()
    assert st["review"] == "done" and st["plain"] == "pending"  # carry only where asked
    row = store.node_rows(conn, tid)["review"]
    assert row["done_by"] == "wf" and "pass carried over" in row["evidence"]
    _commit(repo, "m.py", "x = 2\n")
    assert redo()["review"] == "pending"  # the code changed: a new review
    Path(repo, "receipt").touch()
    assert redo()["review"] == "done"
    Path(repo, "receipt").unlink()
    _commit(repo, "notes.md", "a non-python file is never the same code\n")
    assert redo()["review"] == "pending"


@pytest.mark.parametrize("kind", ["wf_fail", "revise_change"])
def test_a_failed_or_revised_node_does_not_carry(workflows, repo, kind):
    (workflows / "carry.yaml").write_text(CARRY)
    conn = store.connect()
    _commit(repo, "m.py", "x = 1\n")
    tid = start(repo, type_="carry")
    Path(repo, "receipt").touch()
    checks.run_ready(conn, store.get_task(conn, tid), time.monotonic() + 30)
    Path(repo, "receipt").unlink()
    store.event(conn, tid, "review", kind, "")
    _commit(repo, "m.py", "x  =  1\n")
    for n in ("review", "plain"):
        store.set_node(conn, tid, n, status="pending")
    checks.run_ready(conn, store.get_task(conn, tid), time.monotonic() + 30)
    assert statuses(tid)["review"] == "pending"


def test_same_code_reads_any_encoding(repo):
    def commit(body: bytes) -> str:
        Path(repo, "l.py").write_bytes(b"# -*- coding: latin-1 -*-\n" + body)
        subprocess.run(["git", "-C", repo, "add", "l.py"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "l"], check=True)
        return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()

    sha = commit(b"s = '\xe9'\n")
    commit(b"s  =  '\xe9'\n")
    assert checks._same_code(repo, sha)
    commit(b"s = '\xe8'\n")
    assert not checks._same_code(repo, sha)


@pytest.mark.parametrize("node", ["{id: a, carry: code, kind: human}",
                                  "{id: a, check: 'true x', carry: yes, on_fail: retry}"])
def test_carry_is_code_on_a_checked_node(node):
    with pytest.raises(graph.WfError, match="carry"):
        graph.parse(f"type: t\nmatch: m\nnodes:\n  - {node}\n  - {{id: retro, after: [a]}}\n")


def test_retro_runs_only_on_a_signal_or_every_nth_clean_task(workflows, repo, monkeypatch):
    monkeypatch.setattr(checks, "RETRO_EVERY", 3)
    conn = store.connect()

    def run(n, *signals):
        tid = start(repo, issue=f"https://github.com/example/demo/issues/{n}")
        store.event(conn, tid, "m", "fail", "waiting on the merge")  # a wait is no signal
        for node, kind in signals:
            store.event(conn, tid, node, kind, "boom")
        close(tid, "a", "b", "h", "m")
        return tid

    def retro(tid):
        reviewed_retro(conn, store.get_task(conn, tid), "no_change", "none")
        checks.complete(conn, store.get_task(conn, tid), "retro", "ev")

    clean = run(1)  # the first task on its template version is no signal either
    assert state(clean) == "done" and statuses(clean)["retro"] == "done"
    row = store.node_rows(conn, clean)["retro"]
    assert row["done_by"] == "wf" and "retro skipped" in row["evidence"]
    assert state(run(2)) == "done"
    failed = run(3, ("a", "wf_fail"), ("a", "back"), ("a", "wf_fail"))  # a node failed twice
    assert statuses(failed)["retro"] == "pending"
    retro(failed)
    # One wf_fail, or one each on two nodes, is no signal; nor is a loop's `back`.
    assert state(run(4, ("a", "wf_fail"), ("a", "back"))) == "done"
    assert state(run(5, ("a", "wf_fail"), ("b", "wf_fail"))) == "done"
    assert statuses(run(6))["retro"] == "pending"  # the third clean task since the last retro
    for n, kind in enumerate(("revise", "waive", "stall"), 7):
        assert statuses(run(n, ("a", kind)))["retro"] == "pending"


def test_exhausted_budget_is_a_failure_not_a_pass(workflows, repo):
    conn = store.connect()
    task = store.get_task(conn, start(repo))
    Path(repo, "a.ok").touch()
    failed, closed = checks.run_ready(conn, task, time.monotonic() - 1)
    assert failed["a"] == (124, i18n.t("checks.unrun")) and not closed
    assert store.node_rows(conn, task["id"])["a"]["status"] == "pending"


def test_run_ready_reaches_a_fixpoint_in_order(workflows, repo):
    conn = store.connect()
    task = store.get_task(conn, start(repo))
    Path(repo, "a.ok").touch()
    Path(repo, "b.ok").touch()
    failed, closed = checks.run_ready(conn, task, time.monotonic() + 30)
    assert closed and failed["m"][0] == 1 and "ask the user" in failed["m"][1] and len(failed) == 1
    rows = store.node_rows(conn, task["id"])
    assert (rows["a"]["status"], rows["b"]["status"], rows["retro"]["attempts"]) == (
        "done", "done", 0)


def test_set_param_validates(workflows, repo):
    conn = store.connect()
    task = store.get_task(conn, start(repo))
    with pytest.raises(graph.WfError, match="is not a param"):
        graph.set_param(conn, task, "other", "x")
    graph.set_param(conn, task, "name", "ok")
    assert store.get_task(conn, task["id"])["params"] == '{"name": "ok"}'


def _demo_nodes(**change) -> list[dict]:
    nodes = [{"id": "a", "check": "test -f a.ok", "on_fail": "retry"},
             {"id": "b", "after": ["a"], "check": "test -f b.ok", "on_fail": "retry"},
             {"id": "h", "kind": "human", "after": ["b"]},
             {"id": "m", "kind": "human", "after": ["b"], "check": "test -f merged.ok",
              "on_fail": "retry"},
             {"id": "retro", "after": ["h", "m"]}]
    return [{**n, **change.get(n["id"], {})} for n in nodes if change.get(n["id"]) != "drop"]


def test_revise_keeps_state_and_records_every_change(workflows, repo):
    tid = start(repo)
    close(tid, "a")
    conn = store.connect()

    def revise(nodes: list[dict], reason: str = "real path changed") -> str:
        return graph.revise(conn, store.get_task(conn, tid),
                            {"nodes": nodes, "params": {"name": "^[a-z;]+$"}}, reason)

    with pytest.raises(graph.WfError, match="reason"):
        revise(_demo_nodes(), " ")
    # A pending check is waived, not deleted.
    with pytest.raises(graph.WfError, match=r"\['b'\].*wf_waive"):
        revise(_demo_nodes(b="drop", h={"after": ["a"]}, m={"after": ["a"]}))
    with pytest.raises(graph.WfError, match="cannot fail"):
        revise(_demo_nodes(b={"check": "true"}))
    # A pending check may be replaced, never removed — the latter is the user's waiver.
    with pytest.raises(graph.WfError, match=r"cannot remove .*\['m'\]"):
        revise([{k: v for k, v in n.items() if (n["id"], k) != ("m", "check")}
                for n in _demo_nodes()])
    nodes = _demo_nodes(b={"check": "test -f b2.ok"}, h="drop", retro={"after": ["m", "c"]})
    nodes.insert(-1, {"id": "c", "after": ["a"], "check": "test -f c.ok", "on_fail": "a"})
    summary = revise(nodes, "needed c")
    assert summary == "changed: +c ~b, ~retro -h — needed c"
    assert {k: r["status"] for k, r in store.node_rows(conn, tid).items()} == {
        "a": "done", "b": "pending", "m": "pending", "c": "pending", "retro": "pending"}
    assert [tuple(e) for e in conn.execute(
        "SELECT node, kind, detail FROM event WHERE kind LIKE 'revise%' ORDER BY rowid")] == [
        ("c", "revise_add", "needed c"), ("b", "revise_change", "needed c"),
        ("retro", "revise_change", "needed c"), ("", "revise", summary)]


def test_a_node_patch_edits_only_those_nodes_and_redoes_a_changed_check(workflows, repo):
    tid = start(repo)
    close(tid, "a")
    conn = store.connect()
    task = store.get_task(conn, tid)
    before = task["graph"]
    params = task["params"]

    def patch(nodes, **kw):
        return graph.revise(conn, store.get_task(conn, tid), {"nodes": nodes}, "req", **kw)

    with pytest.raises(graph.WfError, match="retro.after"):  # dropping b changes the leaves
        patch({"b": None})
    assert store.get_task(conn, tid)["graph"] == before  # refusal writes nothing
    with pytest.raises(graph.WfError, match="malformed"):
        patch({"": {"do": "x"}})
    assert store.get_task(conn, tid)["graph"] == before
    # do-only leaves a finished node finished; omitted params stay.
    patch({"a": {"do": "say what changed"}})
    assert store.node_rows(conn, tid)["a"]["status"] == "done"
    close(tid, "b")
    patch({"a": {"check": "test -f a2.ok"}})
    assert store.node_rows(conn, tid)["a"]["status"] == "pending"
    assert store.node_rows(conn, tid)["b"]["status"] == "pending"  # proof was the old check
    close(tid, "a", "b")
    assert store.get_task(conn, tid)["params"] == params
    assert "b2" not in store.get_task(conn, tid)["graph"]
    patch({"b": {"check": "test -f b2.ok"}, "z": {"after": ["a"], "check": "test -f z.ok",
                                                   "on_fail": "retry"},
           "retro": {"after": ["h", "m", "z"]}})
    rows = store.node_rows(conn, tid)
    assert rows["a"]["status"] == "done" and rows["b"]["status"] == "pending"
    assert rows["z"]["status"] == "pending"
    body = graph.parse(store.get_task(conn, tid)["graph"])
    assert [n["id"] for n in body["nodes"]][-1] == "retro"
    assert "z" in [n["id"] for n in body["nodes"]]
    # b's other fields survived the one-field patch
    b = next(n for n in body["nodes"] if n["id"] == "b")
    assert b["after"] == ["a"] and b["on_fail"] == "retry"
    assert "test -f b2.ok" in next(n["check"] for n in body["nodes"] if n["id"] == "b")
    assert graph.revise(conn, store.get_task(conn, tid),
                        {"nodes": [{"id": "a", "check": "test -f a.ok", "on_fail": "retry"},
                                   {"id": "b", "after": ["a"], "check": "test -f b2.ok",
                                    "on_fail": "retry"},
                                   {"id": "h", "kind": "human", "after": ["b"]},
                                   {"id": "m", "kind": "human", "after": ["b"],
                                    "check": "test -f merged.ok", "on_fail": "retry"},
                                   {"id": "z", "after": ["a"], "check": "test -f z.ok",
                                    "on_fail": "retry"},
                                   {"id": "retro", "after": ["h", "m", "z"]}]},
                        "full still works", title="renamed")
    assert store.get_task(conn, tid)["title"] == "renamed"
    text = graph.revise_text(conn, store.get_task(conn, tid))
    assert text.startswith("changed:") and "ready:" in text and "[▶" not in text


def test_a_new_agent_skill_or_after_reopens_a_done_node(workflows, repo):
    body = ("nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n"
            "  - {id: work, check: 'test -d .', on_fail: retry}\n")
    for i, fields in enumerate([{"agent": "omge:reviewer", "skill": "omge:graph"},
                                {"agent_mode": "recommend", "agent": "x"}, {"after": ["a"]}]):
        tid = start(repo, type_="new", new_reason="gate", graph=body,
                    issue=ISSUE.replace("/1", f"/{20 + i}"))
        conn = store.connect()
        checks.complete(conn, store.get_task(conn, tid), "work", "ev")
        summary = graph.revise(conn, store.get_task(conn, tid), {"nodes": {"work": fields}},
                               "gate")
        assert "~work" in summary
        assert store.node_rows(conn, tid)["work"]["status"] == "pending"


def test_a_changed_route_reopens_and_a_new_leaf_holds_retro(workflows, repo):
    body = ("nodes:\n  - {id: a, check: 'test -d .', on_fail: retry, next: [{to: b}]}\n"
            "  - {id: b, check: 'test -d .', on_fail: retry}\n")
    tid = start(repo, type_="new", new_reason="route", graph=body)
    conn = store.connect()
    checks.complete(conn, store.get_task(conn, tid), "a", "ev")
    assert store.node_rows(conn, tid)["a"]["route"] == "b"
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"a": {"next": [{"to": "c"}]},
                            "c": {"check": "test -d .", "on_fail": "retry"}}}, "route")
    rows = store.node_rows(conn, tid)
    assert rows["a"]["status"] == "pending" and rows["a"]["route"] is None
    # a new leaf after the only root: auto retro waits on it, so it is not ready with the leaf
    tid = start(repo, type_="new", new_reason="leaf", issue=ISSUE.replace("/1", "/8"),
                graph="nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n")
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"b": {"after": ["a"], "check": "test -d .", "on_fail": "retry"}}},
                 "leaf")
    checks.complete(conn, store.get_task(conn, tid), "a", "ev")
    ready = [n["id"] for n in graph.ready(graph.parse(store.get_task(conn, tid)["graph"]),
                                          store.node_rows(conn, tid))]
    assert ready == ["b"]
    # an explicit retro with a human after it keeps that after through an unrelated patch
    tid = start(repo, type_="new", new_reason="explicit", issue=ISSUE.replace("/1", "/9"),
                graph={"nodes": [{"id": "a", "check": "test -d .", "on_fail": "retry"},
                                 {"id": "retro", "after": ["a"]},
                                 {"id": "approve", "kind": "human", "after": ["retro"]}]})
    graph.revise(conn, store.get_task(conn, tid), {"nodes": {"a": {"do": "note"}}}, "do")
    body = graph.parse(store.get_task(conn, tid)["graph"])
    assert next(n["after"] for n in body["nodes"] if n["id"] == "retro") == ["a"]
    assert next(n["after"] for n in body["nodes"] if n["id"] == "approve") == ["retro"]
    # pinning retro.after, even to the current leaves, survives a later unrelated patch
    tid = start(repo, type_="new", new_reason="pin", issue=ISSUE.replace("/1", "/10"),
                graph="nodes:\n  - {id: a, check: 'test -d .', on_fail: retry}\n"
                      "  - {id: b, check: 'test -d .', on_fail: retry}\n")
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"retro": {"after": ["a"]}}}, "pin retro")
    graph.revise(conn, store.get_task(conn, tid), {"nodes": {"b": {"do": "note"}}}, "do")
    assert next(n["after"] for n in graph.parse(store.get_task(conn, tid)["graph"])["nodes"]
                if n["id"] == "retro") == ["a"]
    # a stored graph from before auto_after: adding a leaf must say retro.after
    raw = graph.parse(store.get_task(conn, tid)["graph"])
    retro = next(n for n in raw["nodes"] if n["id"] == "retro")
    retro.pop("auto_after", None)
    retro.pop("check", None)
    legacy = yaml.safe_dump({"type": raw["type"], "match": raw["match"], "params": raw["params"],
                             "nodes": raw["nodes"]}, allow_unicode=True, sort_keys=False)
    store.update_task(conn, tid, graph=legacy)
    before = store.get_task(conn, tid)["graph"]
    with pytest.raises(graph.WfError, match="retro.after"):
        graph.revise(conn, store.get_task(conn, tid),
                     {"nodes": {"c": {"after": ["a"], "check": "test -d .", "on_fail": "retry"}}},
                     "leaf")
    assert store.get_task(conn, tid)["graph"] == before
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"c": {"after": ["a"], "check": "test -d .", "on_fail": "retry"},
                            "retro": {"after": ["c"]}}}, "leaf")
    task = store.get_task(conn, tid)
    checks.complete(conn, task, "a", "ev")
    ready = [n["id"] for n in graph.ready(graph.parse(task["graph"]), store.node_rows(conn, tid))]
    assert "retro" not in ready and "c" in ready


def test_submitted_retro_without_after_follows_the_leaves_and_keeps_an_explicit_one():
    nodes = [{"id": "a", "check": "test -d .", "on_fail": "retry"},
             {"id": "b", "check": "test -d .", "on_fail": "retry", "after": ["a"]},
             {"id": "retro"}]
    body = graph.parse(graph.submitted({"nodes": nodes}, "new", "m"))
    retro = next(n for n in body["nodes"] if n["id"] == "retro")
    assert retro["after"] == ["b"] and retro["auto_after"] == "auto"
    # an explicit after stays, even when it is not the computed leaves
    pinned = graph.parse(graph.submitted(
        {"nodes": [{"id": "a", "check": "test -d .", "on_fail": "retry"},
                   {"id": "b", "check": "test -d .", "on_fail": "retry", "after": ["a"]},
                   {"id": "retro", "after": ["a"]}]}, "new", "m"))
    assert next(n["after"] for n in pinned["nodes"] if n["id"] == "retro") == ["a"]
    assert next(n["auto_after"] for n in pinned["nodes"] if n["id"] == "retro") == "pinned"


def test_omitted_retro_after_follows_a_leaf_added_later_and_an_explicit_one_stays(
        workflows, repo):
    nodes = [{"id": "a", "check": "test -d .", "on_fail": "retry"},
             {"id": "retro"}]
    tid = start(repo, type_="new", new_reason="omitted retro", graph={"nodes": nodes},
                issue=ISSUE.replace("/1", "/11"))
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"b": {"after": ["a"], "check": "test -d .", "on_fail": "retry"}}},
                 "leaf")
    body = graph.parse(store.get_task(conn, tid)["graph"])
    assert next(n["after"] for n in body["nodes"] if n["id"] == "retro") == ["b"]
    checks.complete(conn, store.get_task(conn, tid), "a", "ev")
    ready = [n["id"] for n in graph.ready(body, store.node_rows(conn, tid))]
    assert ready == ["b"]
    pinned = start(repo, type_="new", new_reason="pinned retro", issue=ISSUE.replace("/1", "/12"),
                   graph={"nodes": [{"id": "a", "check": "test -d .", "on_fail": "retry"},
                                    {"id": "b", "check": "test -d .", "on_fail": "retry",
                                     "after": ["a"]},
                                    {"id": "retro", "after": ["a"]}]})
    graph.revise(conn, store.get_task(conn, pinned),
                 {"nodes": {"c": {"after": ["b"], "check": "test -d .", "on_fail": "retry"}}},
                 "another leaf")
    assert next(n["after"] for n in graph.parse(store.get_task(conn, pinned)["graph"])["nodes"]
                if n["id"] == "retro") == ["a"]
    # a second revise that does not touch retro must not pin an auto list or an unmarked one
    graph.revise(conn, store.get_task(conn, tid), {"nodes": {"a": {"do": "note"}}}, "do")
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"d": {"after": ["b"], "check": "test -d .", "on_fail": "retry"}}},
                 "another auto leaf")
    body = graph.parse(store.get_task(conn, tid)["graph"])
    assert next(n["after"] for n in body["nodes"] if n["id"] == "retro") == ["d"]
    assert next(n["auto_after"] for n in body["nodes"] if n["id"] == "retro") == "auto"
    raw = graph.parse(store.get_task(conn, tid)["graph"])
    retro = next(n for n in raw["nodes"] if n["id"] == "retro")
    retro.pop("auto_after", None)
    retro.pop("check", None)
    legacy = yaml.safe_dump({"type": raw["type"], "match": raw["match"], "params": raw["params"],
                             "nodes": raw["nodes"]}, allow_unicode=True, sort_keys=False)
    store.update_task(conn, tid, graph=legacy)
    graph.revise(conn, store.get_task(conn, tid), {"nodes": {"a": {"do": "again"}}}, "unrelated")
    before = store.get_task(conn, tid)["graph"]
    assert "auto_after" not in before
    with pytest.raises(graph.WfError, match="retro.after"):
        graph.revise(conn, store.get_task(conn, tid),
                     {"nodes": {"e": {"after": ["d"], "check": "test -d .", "on_fail": "retry"}}},
                     "legacy leaf")
    assert store.get_task(conn, tid)["graph"] == before


def test_graph_strings_that_yaml_would_truncate_are_refused():
    with pytest.raises(graph.WfError, match="#"):
        graph.load("nodes:\n  - id: a\n    do: fix the bug from #12 then test\n")
    with pytest.raises(graph.WfError, match="YAML"):
        graph.load("nodes: [")
    assert graph.load("nodes:\n  - {id: a, do: 'quoted #12 survives'}\n")["nodes"][0]["do"] \
        == "quoted #12 survives"
    assert graph.load([{"id": "a"}]) == {"nodes": [{"id": "a"}]}


@pytest.mark.parametrize("shell", ["true", ":", " exit 0 ", "test 1", "true;", " true ; ",
                                   "[ 1 ]", "/bin/true", "echo ok", "echo"])
def test_checks_and_whens_that_cannot_fail_are_refused(shell):
    with pytest.raises(graph.WfError, match="cannot fail"):
        graph.parse(_body(f"  - {{id: a, check: '{shell}'}}\n  - {{id: retro}}\n"))
    with pytest.raises(graph.WfError, match="cannot split the branches"):
        edge = f"[{{to: retro, when: '{shell}'}}]"
        graph.parse(_body(f"  - {{id: a, check: 'test -d .', next: {edge}}}\n  - {{id: retro}}\n"))


def test_set_dir_moves_the_task_and_a_missing_dir_fails_like_a_check(workflows, repo, tmp_path):
    conn = store.connect()
    task = store.get_task(conn, start(repo))
    assert checks.run("true", str(tmp_path / "gone"), {}, 5)[0] == 127
    with pytest.raises(graph.WfError, match="does not exist"):
        graph.set_param(conn, task, "dir", str(tmp_path / "gone"))
    graph.set_param(conn, task, "dir", str(tmp_path))
    assert store.get_task(conn, task["id"])["dir"] == str(tmp_path.resolve())


V0 = """
CREATE TABLE instance (id INTEGER PRIMARY KEY, type TEXT NOT NULL, worktree TEXT NOT NULL,
    issue_url TEXT NOT NULL, template_sha256 TEXT NOT NULL, template_body TEXT NOT NULL,
    params TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX instance_open_worktree ON instance(worktree)
    WHERE state IN ('active', 'waiting_human', 'stalled', 'paused');
CREATE TABLE node_state (instance INTEGER NOT NULL, node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, last_exit INTEGER,
    last_output_tail TEXT, evidence TEXT, done_by TEXT, PRIMARY KEY (instance, node));
CREATE TABLE stop_block (worktree TEXT PRIMARY KEY, streak INTEGER NOT NULL,
    signature TEXT NOT NULL);
CREATE TABLE session_touch (session_id TEXT NOT NULL, instance INTEGER NOT NULL,
    PRIMARY KEY (session_id, instance));
CREATE TABLE retro (instance INTEGER PRIMARY KEY, deviations TEXT NOT NULL,
    verdict TEXT NOT NULL, link TEXT);
"""


def test_migration_from_worktree_instances(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    monkeypatch.setenv("WF_DB", str(db))
    old = sqlite3.connect(db)
    old.executescript(V0)
    body = _body("  - {id: a, check: 'test -d .'}\n  - {id: retro}\n")
    for iid, wt, issue, st in [(1, "/w1", ISSUE, "active"), (2, "/w2", ISSUE, "active"),
                               (3, "/w3", ISSUE + "0", "waiting_human"), (4, "/w4", "x", "done")]:
        old.execute("INSERT INTO instance VALUES (?, 'x', ?, ?, 'sha', ?, '{}', ?, 1, 2)",
                    (iid, wt, issue, body, st))
        old.execute("INSERT INTO node_state (instance, node, status) VALUES (?, 'a', ?)",
                    (iid, "done" if iid == 3 else "pending"))
        old.execute("INSERT INTO node_state (instance, node, status) VALUES (?, 'retro', ?)",
                    (iid, "done" if iid == 3 else "pending"))
    # A `new` that only ever owed its retro: the unlock-by-new misuse, not open work.
    old.execute("INSERT INTO instance VALUES (5, 'new', '/w5', ?, 'sha', ?, '{}', 'active', 1, 2)",
                (ISSUE + "5", body))
    old.execute("INSERT INTO node_state (instance, node, status) VALUES (5, 'a', 'done')")
    old.execute("INSERT INTO node_state (instance, node, status) VALUES (5, 'retro', 'pending')")
    old.execute("INSERT INTO stop_block VALUES ('/w1', 2, 's')")
    old.execute("INSERT INTO session_touch VALUES ('s', 1)")
    old.commit()
    old.close()
    conn = store.connect()
    states = dict(conn.execute("SELECT id, state FROM task").fetchall())
    # Duplicate issue: newest stays; nothing pending: done; no owners at all.
    assert states == {1: "dropped", 2: "active", 3: "done", 4: "done", 5: "abandoned"}
    assert store.open_task(conn, ISSUE)["dir"] == "/w2"
    assert [t["id"] for t in store.ownerless(conn)] == [2]
    assert store.node_rows(conn, 2)["a"]["loops"] == 0
    assert conn.execute("SELECT COUNT(*) FROM stop_block").fetchone()[0] == 0
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'session_touch'").fetchone()
    # Idempotent: a second connect is a no-op, and the open-per-issue rule now holds.
    store.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE task SET state = 'active' WHERE id = 1")


def test_migration_ignores_v0_tables_a_stale_server_recreated(tmp_path, monkeypatch):
    # A pre-v1 wf-mcp re-creates empty v0 tables next to `task`; the v1→v2
    # step must not try to rename them over it (that jammed every Stop).
    db = tmp_path / "ghost.db"
    monkeypatch.setenv("WF_DB", str(db))
    store.connect().close()  # current schema, with tasks to keep
    conn = store.connect()
    conn.execute("INSERT INTO task (type, dir, issue_url, version, graph, params, state, created,"
                 " updated) VALUES ('x', '/w', 'u', '', 'g', '{}', 'done', 1, 1)")
    conn.close()
    old = sqlite3.connect(db)
    # What the old server's `CREATE ... IF NOT EXISTS` adds next to the v1 tables.
    ghost = [s for s in V0.split(";") if "instance (" in s or "instance(" in s
             or "session_touch" in s]
    old.executescript(";".join(ghost) + "; PRAGMA user_version = 1;")
    old.close()
    conn = store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "instance" not in names and "session_touch" not in names
    assert conn.execute("SELECT COUNT(*) FROM task").fetchone()[0] == 1


def test_migration_from_v1_adds_the_listed_flag(tmp_path, monkeypatch):
    db = tmp_path / "v1.db"
    monkeypatch.setenv("WF_DB", str(db))
    old = sqlite3.connect(db)
    old.executescript("CREATE TABLE session (session_id TEXT PRIMARY KEY,"
                      " registered INTEGER NOT NULL, noticed INTEGER NOT NULL DEFAULT 0);"
                      "INSERT INTO session VALUES ('s', 1, 0); PRAGMA user_version = 1;")
    old.close()
    conn = store.connect()
    assert store.registered(conn, "s")
    assert conn.execute("SELECT listed FROM session WHERE session_id = 's'").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION == 3
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'agent_launch'").fetchone()


@pytest.mark.parametrize("verdict", ["no_change", "update_type", "new_type"])
def test_shared_retro_api_cannot_write_without_independent_receipt(workflows, repo, verdict):
    tid = start(repo)
    close(tid, "a", "b", "h", "m")
    conn = store.connect()
    with pytest.raises(graph.WfError, match="receipt required"):
        graph.record_retro(conn, store.get_task(conn, tid), verdict, "none", reason="sound",
                           name="other", match="other task type")
    assert store.get_retro(conn, tid) is None


@pytest.mark.parametrize("value", ['"true"', '"false"', "null", "1", "[]", "{}"])
def test_optional_issue_metadata_rejects_non_boolean(value):
    body = _body("  - {id: a, check: 'test -d .'}\n  - {id: retro}\n")
    with pytest.raises(graph.WfError, match="optional_issue"):
        graph.parse(body + f"optional_issue: {value}\n")


def test_optional_issue_survives_serialization_and_requires_review_for_changes():
    body = graph.submitted({"optional_issue": True, "nodes": [
        {"id": "a", "check": "test -d .", "on_fail": "retry"}]}, "new", "test")
    optional = graph.parse(body)
    required = graph.parse(body.replace("optional_issue: true", "optional_issue: false"))
    assert optional["optional_issue"] is True
    assert graph._reuse_shape(optional) != graph._reuse_shape(required)
    assert "optional_issue" in graph._protected_changes(required, optional)
    assert "optional_issue" in graph._weakened(required, optional)
