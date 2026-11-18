import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from omge import graph, server
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
