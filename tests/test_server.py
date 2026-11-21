import asyncio
import sqlite3
from contextlib import closing

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from omge import graph, server, store
from tests.conftest import start


def test_wf_start_accepts_yaml_graph_comments(repo):
    result = asyncio.run(server.mcp.call_tool("wf_start", {
        "type": "new", "dir": repo, "title": "commented graph", "new_reason": "regression",
        "graph": """\
# The graph starts here.
params: null # No parameters
nodes: # Workflow steps
  # Keep the literal hashes in the values below.
  - id: verify # A checked step
    do: "Open #12, then verify" # Display text
    check: |- # Shell command follows
      # Keep this line in the command.
      test -f "#marker"
    on_fail: retry # Retry the check
    next: # No conditional routes
    pitfalls: null # No pitfalls
# The graph ends here.
""",
    }))
    assert "task #1" in result.content[0].text
    with closing(store.connect()) as conn:
        node = graph.parse(store.get_task(conn, 1)["graph"])["nodes"][0]
    assert node["do"] == "Open #12, then verify"
    assert node["check"] == '# Keep this line in the command.\ntest -f "#marker"'


@pytest.mark.parametrize("body", [
    "    check: test -f .ok #marker\n",
    "    do: #12 must be checked\n    check: test -f .ok\n",
    "    check: #marker\n      test -f .ok\n",
])
def test_wf_start_refuses_ambiguous_plain_text(repo, body):
    with pytest.raises(ToolError, match="YAML cut everything after `#`"):
        asyncio.run(server.mcp.call_tool("wf_start", {
            "type": "new", "dir": repo, "title": "ambiguous check", "new_reason": "regression",
            "graph": f"nodes:\n  - id: verify\n{body}    on_fail: retry\n",
        }))


def test_wf_status_surfaces_unexpected_errors(workflows, repo, monkeypatch, capsys):
    task = start(repo)

    def broken_status(*args, **kwargs):
        raise RuntimeError("status rendering broke")

    monkeypatch.setattr(graph, "status_text", broken_status)
    with pytest.raises(ToolError, match="RuntimeError: status rendering broke"):
        asyncio.run(server.mcp.call_tool("wf_status", {"task": task}))
    captured = capsys.readouterr()
    assert "Traceback (most recent call last):" in captured.err
    assert "RuntimeError: status rendering broke" in captured.err
    assert not captured.out

    with pytest.raises(ToolError, match="give exactly one of issue_url and task"):
        asyncio.run(server.mcp.call_tool("wf_status", {}))
    assert not capsys.readouterr().err


@pytest.mark.parametrize("fail", [False, True])
def test_wf_status_closes_connection(workflows, repo, monkeypatch, fail):
    with closing(store.connect()) as conn:
        task = graph.start(conn, "demo", repo, None, {}, title="t")
        monkeypatch.setattr(store, "connect", lambda: conn)
        if fail:
            def broken_status(conn, *args, **kwargs):
                conn.execute("CREATE TABLE repro (value INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO repro VALUES (1), (1)")

            monkeypatch.setattr(graph, "status_text", broken_status)
            with pytest.raises(ToolError, match=r"\[SQLITE_CONSTRAINT_PRIMARYKEY\]"):
                server.mcp._tool_manager._tools["wf_status"].fn(task=task)
        else:
            server.mcp._tool_manager._tools["wf_status"].fn(task=task)
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
