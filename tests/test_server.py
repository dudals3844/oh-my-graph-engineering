import asyncio
import sqlite3
from contextlib import closing

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from omge import graph, server, store
from tests.conftest import start


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
