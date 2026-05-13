"""Scratch: call a single MCP tool via SSE+JSON-RPC and print the result."""
import asyncio
import json
import os
import sys
from typing import Any

import httpx


async def probe(base_url: str, bearer: str, tool: str, arguments: dict[str, Any]) -> None:
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "text/event-stream"}
    timeout = httpx.Timeout(300.0, read=300.0)

    print(f"=== {base_url}/sse — calling {tool}({arguments}) ===", flush=True)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", f"{base_url}/sse", headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                print(f"SSE GET {resp.status_code}: {body.decode(errors='replace')[:300]}", flush=True)
                return

            session_endpoint = None
            current_event = None
            current_data_lines: list[str] = []

            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    current_data_lines.append(line[5:].strip())
                elif line == "":
                    if current_event and current_data_lines:
                        data = "\n".join(current_data_lines)
                        if current_event == "endpoint" and not session_endpoint:
                            session_endpoint = data
                            asyncio.create_task(_call_tool(base_url, session_endpoint, bearer, tool, arguments))
                        elif current_event == "message":
                            try:
                                d = json.loads(data)
                                if d.get("id") == 2:
                                    print(json.dumps(d, indent=2)[:4000], flush=True)
                                    return
                            except json.JSONDecodeError:
                                pass
                    current_event = None
                    current_data_lines = []


async def _call_tool(base_url: str, session_endpoint: str, bearer: str, tool: str, arguments: dict[str, Any]) -> None:
    url = session_endpoint if session_endpoint.startswith("http") else f"{base_url}{session_endpoint}"
    h = {"Authorization": f"Bearer {bearer}"}
    async with httpx.AsyncClient(timeout=300.0) as c:
        await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "scratch", "version": "0.1"}}}, headers=h)
        await c.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
        await c.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}, headers=h)


def parse_args(argv: list[str]) -> tuple[str, str, dict[str, Any]]:
    base_url = argv[1]
    tool = argv[2]
    args: dict[str, Any] = {}
    i = 3
    while i < len(argv):
        if argv[i] == "--arg" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            if v.lower() in ("true", "false"):
                args[k] = v.lower() == "true"
            else:
                try:
                    args[k] = int(v)
                except ValueError:
                    args[k] = v
            i += 2
        else:
            i += 1
    return base_url, tool, args


if __name__ == "__main__":
    bearer = os.environ.get("URSA_OSCAR_MCP_BEARER_TOKEN", "")
    if not bearer:
        print("Set URSA_OSCAR_MCP_BEARER_TOKEN")
        sys.exit(1)
    base_url, tool, arguments = parse_args(sys.argv)
    try:
        asyncio.run(asyncio.wait_for(probe(base_url, bearer, tool, arguments), timeout=300.0))
    except asyncio.TimeoutError:
        print("(tool call timed out after 300s)", flush=True)
