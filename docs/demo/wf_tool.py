"""Demo driver: call one wf MCP tool the way Claude Code would, hooks included.

    wf_tool.py <tool> key=value ...   (values are JSON-decoded when they parse)

PreToolUse can refuse the call; PostToolUse grants the session its new task.
"""

import json
import os
import subprocess
import sys

SESSION = os.environ.get("DEMO_SESSION", "demo")


def hook(event: str, **data) -> subprocess.CompletedProcess:
    data = {"session_id": SESSION, "cwd": os.getcwd(), **data}
    return subprocess.run(["wf", "hook", event], input=json.dumps(data), text=True,
                          capture_output=True)


def call(tool: str, args: dict) -> tuple[str, bool]:
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "demo", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": tool, "arguments": args}}]
    # stdin stays open until the reply: the server drops a call still running at EOF.
    with subprocess.Popen(["wf-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True) as server:
        server.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))
        server.stdin.flush()
        reply = next(r for r in map(json.loads, server.stdout) if r.get("id") == 2)["result"]
        server.stdin.close()
    error = bool(reply.get("isError"))  # an error's last item is the bare message
    return (reply["content"][-1]["text"] if error
            else "\n".join(c["text"] for c in reply["content"])), error


def main() -> int:
    tool, args = sys.argv[1], {}
    for arg in sys.argv[2:]:
        key, _, value = arg.partition("=")
        try:
            args[key] = json.loads(value)
        except ValueError:
            args[key] = value
    name = f"mcp__plugin_omge_wf__{tool}"
    pre = hook("pre-tool-use", tool_name=name, tool_input=args)
    if pre.returncode == 2:
        print(f"\033[31m[PreToolUse blocked]\033[0m {pre.stderr.strip()}")
        return 2
    text, error = call(tool, args)
    print(f"\033[31m{text}\033[0m" if error else text)
    hook("post-tool-use", tool_name=name, tool_input=args, tool_response=text)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
