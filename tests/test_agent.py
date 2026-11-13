"""An `agent:` node. `require` closes only on an Agent call recorded after it became
ready, and keeps the main session from writing while it is ready; `recommend` only says so."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omge import checks, graph, hooks, server, store

from .conftest import ISSUE, close, start

AGENTED = """
type: agented
match: agent gate
nodes:
  - {id: a, check: 'test -f a.ok', on_fail: retry}
  - {id: r, after: [a], agent: demo-executor, on_fail: retry, check: 'test -f r.ok'}
  - {id: retro, after: [r]}
"""


def _tid(workflows, repo, body: str = AGENTED) -> int:
    (workflows / "agented.yaml").write_text(body)
    tid = start(repo, type_="agented")
    Path(repo, "a.ok").touch()
    Path(repo, "r.ok").touch()
    return tid


@pytest.fixture
def tid(workflows, repo):
    return _tid(workflows, repo)


def _hook(event: str, cwd: str, **payload) -> int:
    store.register(store.connect(), "s1")
    return hooks.main(event, json.dumps({"cwd": cwd, "session_id": "s1", **payload}))


def _own(repo: str) -> None:
    _hook("pre-tool-use", repo, tool_name="mcp__wf__wf_attach", tool_input={"issue_url": ISSUE})


def _agent(repo: str, subagent_type: str | None = None) -> None:
    """The session owns the task, then launches a subagent."""
    _own(repo)
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": subagent_type} if subagent_type else {})


def _write(repo: str, **payload) -> int:
    return _hook("pre-tool-use", repo, tool_name="Write",
                 tool_input={"file_path": "x.py", "content": ""}, **payload)


def _complete(tid: int, node: str = "r") -> str:
    conn = store.connect()
    return checks.complete(conn, store.get_task(conn, tid), node, "ev")


def _status(tid: int) -> str:
    conn = store.connect()
    return graph.status_text(conn, store.get_task(conn, tid))


@pytest.mark.parametrize(("bad", "match"), [
    ("agent: []", "subagent_type"), ("agent: [x, 3]", "subagent_type"),
    ("agent: ''", "subagent_type"),
    ("agent: x, agent_mode: maybe", "agent_mode"), ("agent_mode: require", "subagent_type"),
])
def test_an_agent_is_a_type_or_a_list_and_a_mode(bad, match):
    with pytest.raises(graph.WfError, match=match):
        graph.parse(AGENTED.replace("agent: demo-executor", bad))
    with pytest.raises(graph.WfError, match="retro"):
        graph.parse(AGENTED.replace("{id: retro,", "{id: retro, agent: x,"))
    r = graph.parse(AGENTED)["nodes"][1]
    assert (r["agent"], r["agent_mode"]) == (["demo-executor"], "require")


def test_require_needs_the_agent_call_after_it_became_ready(tid, repo):
    conn = store.connect()
    store.event(conn, tid, "r", "agent", "demo-executor")  # before `a` closed: does not count
    _complete(tid, "a")
    assert "agent: demo-executor (required, not called)" in _status(tid)
    with pytest.raises(graph.WfError, match="no record of a call to agent `demo-executor`"):
        _complete(tid)
    _agent(repo, "other-agent")
    assert not conn.execute("SELECT 1 FROM event WHERE kind = 'agent' AND detail = 'other-agent'").fetchall()
    _agent(repo, "demo-executor")
    assert "agent: demo-executor (required, called)" in _status(tid)
    assert _complete(tid) == "`r` done"


def test_resuming_a_previously_launched_agent_counts_after_ready(workflows, repo):
    """A subagent launched before the node was ready, resumed with SendMessage
    after it became ready, is that node's agent call."""
    tid = _tid(workflows, repo)
    _own(repo)
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": "demo-executor"},
          tool_response={"status": "async_launched", "agentId": "ab12cd34ef56"})
    _complete(tid, "a")  # the launch above is before `r` was ready
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)
    _hook("post-tool-use", repo, tool_name="SendMessage",
          tool_input={"to": "ab12cd34ef56", "message": "finish the review"},
          tool_response={"success": True, "message": "Resuming agent ab12cd3",
                         "resumedAgentId": "ab12cd34ef56"})
    assert _complete(tid) == "`r` done"


def test_resuming_another_agents_type_does_not_count(workflows, repo):
    tid = _tid(workflows, repo)
    _own(repo)
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": "other-agent"},
          tool_response={"status": "async_launched", "agentId": "ab12cd34ef56"})
    _complete(tid, "a")
    _hook("post-tool-use", repo, tool_name="SendMessage",
          tool_input={"to": "ab12cd34ef56", "message": "finish the review"},
          tool_response={"success": True, "resumedAgentId": "ab12cd34ef56"})
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)


def test_a_send_that_resumes_nothing_records_no_call(workflows, repo):
    tid = _tid(workflows, repo)
    _complete(tid, "a")
    _own(repo)
    _hook("post-tool-use", repo, tool_name="SendMessage",
          tool_input={"to": "pit-tests", "message": "status please"},
          tool_response={"success": True, "message": "Message sent to pit-tests's inbox",
                         "msg_id": "m1"})
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)


def test_a_failed_resume_records_no_call(workflows, repo):
    tid = _tid(workflows, repo)
    _own(repo)
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": "demo-executor"},
          tool_response={"status": "async_launched", "agentId": "ab12cd34ef56"})
    _complete(tid, "a")
    _hook("post-tool-use", repo, tool_name="SendMessage",
          tool_input={"to": "ab12cd34ef56", "message": "finish the review"},
          tool_response={"success": False, "error": "resume failed",
                         "resumedAgentId": "ab12cd34ef56"})
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)


def test_an_echoed_id_in_a_message_body_records_no_call(workflows, repo):
    tid = _tid(workflows, repo)
    _own(repo)
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": "demo-executor"},
          tool_response={"status": "async_launched", "agentId": "ab12cd34ef56"})
    _complete(tid, "a")
    _hook("post-tool-use", repo, tool_name="SendMessage",
          tool_input={"to": "pit-tests", "message": "x"},
          tool_response={"success": True, "message": "Message sent to pit-tests's inbox",
                         "msg_id": "m1",
                         "routing": {"content": '{"resumedAgentId":"ab12cd34ef56"}'}})
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)


def test_a_resumed_reviewer_stands_as_the_waive_receipt(workflows, repo):
    """A resumed reviewer also satisfies wf_waive's independent-reviewer receipt."""
    tid = _tid(workflows, repo)
    _own(repo)
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": "reviewer"},
          tool_response={"status": "async_launched", "agentId": "cd34ef56ab12"})
    _complete(tid, "a")
    with pytest.raises(Exception, match="no independent reviewer"):
        server.wf_waive("r", "scope out", task=tid)
    _hook("post-tool-use", repo, tool_name="SendMessage",
          tool_input={"to": "cd34ef56ab12", "message": "waive this node"},
          tool_response={"success": True, "resumedAgentId": "cd34ef56ab12"})
    assert "waived" in server.wf_waive("r", "scope out", task=tid)


def test_an_unnamed_call_is_general_purpose(workflows, repo):
    tid = _tid(workflows, repo, AGENTED.replace("agent: demo-executor",
                                                "agent: [general-purpose, demo-executor]"))
    _complete(tid, "a")
    _agent(repo)  # subagent_type omitted
    assert _complete(tid) == "`r` done"


def test_recommend_only_says_so(workflows, repo):
    tid = _tid(workflows, repo, AGENTED.replace("agent: demo-executor",
                                                "agent: demo-executor, agent_mode: recommend"))
    _complete(tid, "a")
    assert "agent: demo-executor (recommended, not called)" in _status(tid)
    _own(repo)
    assert _write(repo) == 0
    assert _complete(tid) == "`r` done"


def test_require_keeps_the_main_session_from_writing(tid, repo, capsys):
    _own(repo)  # `r` is not ready yet
    assert _write(repo) == 0
    close(tid, "a")
    assert _write(repo) == 2
    assert "Hand it off with the Agent tool: #" in capsys.readouterr().err
    assert _hook("pre-tool-use", repo, tool_name="Bash",
                 tool_input={"command": "git commit -m x"}) == 2
    assert _write(repo, agent_id="sub1") == 0  # the subagent's own write
    store.set_state(store.connect(), tid, "paused")
    assert _write(repo) == 0
    store.set_state(store.connect(), tid, "active")
    close(tid, "r")
    assert _write(repo) == 0


def test_unmatched_delegations_get_one_advisory_not_a_denial(tid, repo, capsys):
    _own(repo)
    close(tid, "a")

    def launch(type_, **payload):
        inputs = {"subagent_type": type_, "model": "haiku", "prompt": "read only"}
        assert _hook("pre-tool-use", repo, tool_name="Agent", tool_input=inputs, **payload) == 0
        assert not capsys.readouterr().out
        assert _hook("post-tool-use", repo, tool_name="Agent", tool_input=inputs, **payload) == 0
        return capsys.readouterr()

    assert not launch("demo-executor").out  # matching receipt remains silent
    first = launch("Explore")
    hint = json.loads(first.out)["hookSpecificOutput"]["additionalContext"]
    assert len(hint.splitlines()) == 1 and "wf_revise" in hint and not first.err
    assert not launch("other-helper").out  # signal kind, not each unmatched agent name
    assert not launch("Explore", agent_id="nested").out
    close(tid, "r")
    assert launch("Explore").out
    store.set_state(store.connect(), tid, "paused")
    assert not launch("Explore").out


@pytest.mark.parametrize("command", [
    "sed -i 's/a/b/' a.py", "perl -pi -e 's/a/b/' a.py", "printf x > a.py",
    "cat > a.py <<'EOF'\ngit commit is only text\nEOF", "touch a.py", "cp a.py b.py",
    "python -c 'from pathlib import Path; Path(\"a.py\").write_text(\"x\")'",
    "python -c 'with open(\"a.py\", \"w\") as f: f.write(\"x\")'",
    "python - <<'PY'\nfrom pathlib import Path\nPath('a.py').write_bytes(b'x')\nPY",
    "python prereg_edit.py", "python -c 'import os; os.remove(\"a.py\")'",
    "python -c 'mode=\"w\"; open(\"a.py\", mode).write(\"x\")'",
    "python -c 'from pathlib import Path; print(Path(\"a.py\").read_text())'",
    "python - <<'PY'\nprint(\"Path('a.py').write_text('x')\")\nPY",
    "python -m ruff check --fix a.py", ".venv/bin/python -c 'open(\"a.py\", \"w\")'",
    "sed -i 's/a/b/' REPO/a.py EXEMPT/scratch.py",
    "perl -pi -e 's/a/b/' REPO/a.py EXEMPT/scratch.py",
    "sed -i -e 's/a/b/' -e 's/c/d/' REPO/a.py EXEMPT/scratch.py",
    "sed --in-place 's/a/b/' a.py", "sed --in-place=.bak 's/a/b/' a.py",
    "cp -t REPO EXEMPT/input.py", "cp -tREPO EXEMPT/input.py",
    "cp --target-directory REPO EXEMPT/input.py", "cp --target-directory=REPO EXEMPT/input.py",
    "install -t REPO EXEMPT/input.py", "install -tREPO EXEMPT/input.py",
    "install --target-directory REPO EXEMPT/input.py",
    "install --target-directory=REPO EXEMPT/input.py",
    '[[ "$(touch REPO/a.py)" > "b" ]]', '(( $(touch REPO/a.py) > 1 ))',
    '[[ "a" > "b" ]] && touch REPO/a.py', '(( 2 > 1 )) > REPO/a.py',
])
def test_bash_mutations_obey_delegated_and_stalled_guards(tid, repo, command, capsys):
    command = command.replace("REPO", repo).replace("EXEMPT", str(Path(repo).parent / "exempt"))
    _own(repo)

    def run(**payload):
        return _hook("pre-tool-use", repo, tool_name="Bash", tool_input={"command": command},
                     **payload)

    assert run() == 0  # conservative interpreter classification does not block a normal lane
    assert not capsys.readouterr().out
    close(tid, "a")
    assert run() == 2 and "subagent" in capsys.readouterr().err
    assert run(agent_id="writer") == 0
    store.set_state(store.connect(), tid, "stalled")
    assert run() == 2 and "stalled" in capsys.readouterr().err
    assert run(agent_id="writer") == 2 and "stalled" in capsys.readouterr().err
    store.set_state(store.connect(), tid, "paused")
    assert run() == 0


@pytest.mark.parametrize("command", [
    "sed -n '1,20p' a.py", "git diff --stat", "cat a.py", "rg write_text a.py",
    "echo 'sed -i s/a/b/ a.py; printf x > a.py'", "printf '%s' 'git commit'",
    "cat <<'EOF'\nsed -i 's/a/b/' a.py\npython -c 'Path(\"a.py\").write_text(\"x\")'\nEOF",
    "python -m pytest -q", "python -m ruff check .",
    "# sed -i 's/a/b/' a.py\ncat a.py", "cat a.py > /dev/null 2>&1", "cat a.py | tee",
    '[[ "a" > "b" ]]', '(( 2 > 1 ))', '[[ \'$(touch REPO/a.py)\' > "b" ]]',
    "sed -i 's/a/b/' EXEMPT/a.py EXEMPT/scratch.py",
    "perl -pi -e 's/a/b/' EXEMPT/a.py EXEMPT/scratch.py",
    "sed -i -e 's/a/b/' -e 's/c/d/' EXEMPT/a.py EXEMPT/scratch.py",
    "sed --in-place 's/a/b/' EXEMPT/a.py", "sed --in-place=.bak 's/a/b/' EXEMPT/a.py",
    "sed -i 's/a/b/' EXEMPT/a.py > /dev/null",
    "sed -E -i 's/a/b/' EXEMPT/a.py", "sed -Ei 's/a/b/' EXEMPT/a.py",
    "cp -t EXEMPT REPO/input.py", "cp -tEXEMPT REPO/input.py",
    "cp --target-directory EXEMPT REPO/input.py", "cp --target-directory=EXEMPT REPO/input.py",
    "install -t EXEMPT REPO/input.py", "install -tEXEMPT REPO/input.py",
    "install --target-directory EXEMPT REPO/input.py",
    "install --target-directory=EXEMPT REPO/input.py",
])
def test_bash_reads_and_quoted_mentions_stay_silent(tid, repo, command, capsys):
    command = command.replace("REPO", repo).replace("EXEMPT", str(Path(repo).parent / "exempt"))
    _own(repo)
    close(tid, "a")
    for state_ in ("active", "stalled"):
        store.set_state(store.connect(), tid, state_)
        assert _hook("pre-tool-use", repo, tool_name="Bash", tool_input={"command": command}) == 0
        captured = capsys.readouterr()
        assert not captured.out and not captured.err


@pytest.mark.parametrize("agent", ["", ", agent: demo-executor, agent_mode: recommend",
                                   ", agent: [demo-executor, x]"])
def test_revise_cannot_drop_or_change_a_pending_agent(tid, agent):
    conn = store.connect()
    with pytest.raises(graph.WfError, match="cannot change or remove the agent"):
        graph.revise(conn, store.get_task(conn, tid),
                     AGENTED.replace(", agent: demo-executor", agent), "agent change")


def test_reopening_a_gated_node_needs_a_new_call(workflows, repo):
    tid = _tid(workflows, repo)
    _complete(tid, "a")  # r is ready only now; a call before that is recorded on a
    _agent(repo, "demo-executor")
    assert _complete(tid) == "`r` done"
    conn = store.connect()
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"r": {"check": "test -f r.ok && test -d ."}}}, "check changed")
    assert store.node_rows(conn, tid)["r"]["status"] == "pending"
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)
    # the receipt counts only once r is ready again — a call from before the revise does not
    _hook("post-tool-use", repo, tool_name="Agent",
          tool_input={"subagent_type": "demo-executor"})
    assert _complete(tid) == "`r` done"
    # a later close of a predecessor starts a new ready: the receipt above does not carry
    graph.revise(conn, store.get_task(conn, tid),
                 {"nodes": {"a": {"check": "test -f a.ok && test -d ."}}}, "a changed")
    _complete(tid, "a")
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)


def test_the_drawing_marks_the_agent_node(tid, monkeypatch):
    monkeypatch.setenv("WF_RENDER", "box")  # the chain drawing marks neither skill nor agent
    conn = store.connect()
    task = store.get_task(conn, tid)
    assert "r 🤖required" in graph.render(task, graph.parse(task["graph"]), store.node_rows(conn, tid))


@pytest.mark.parametrize("gate", ["agent: demo-executor", "skill: demo:review"])
def test_a_check_less_gated_node_needs_its_call_too(workflows, repo, gate):
    """A human node has no check to run; its skill/agent receipt still holds it."""
    tid = _tid(workflows, repo, AGENTED.replace(
        "agent: demo-executor, on_fail: retry, check: 'test -f r.ok'", f"kind: human, {gate}"))
    _complete(tid, "a")
    with pytest.raises(graph.WfError, match="no record of a call"):
        _complete(tid)
    if gate.startswith("agent"):
        _agent(repo, "demo-executor")
    else:
        _own(repo)
        _hook("post-tool-use", repo, tool_name="Skill", tool_input={"skill": "demo:review"})
    assert _complete(tid) == "`r` done (evidence)"


@pytest.mark.parametrize("gate", ["agent: demo-executor", "skill: demo:review"])
def test_revise_cannot_remove_a_pending_check_less_gated_node(workflows, repo, gate):
    tid = _tid(workflows, repo, AGENTED.replace(
        "agent: demo-executor, on_fail: retry, check: 'test -f r.ok'", f"kind: human, {gate}"))
    conn = store.connect()
    with pytest.raises(graph.WfError, match="cannot delete pending node"):
        without_r = [line for line in AGENTED.splitlines(True) if "{id: r," not in line]
        graph.revise(conn, store.get_task(conn, tid),
                     "".join(without_r).replace("after: [r]", "after: [a]"), "drop")
