"""Scratch MCP client — probes tools/list against a live server over SSE+JSON-RPC.

Not committed (gitignored under tests/_scratch_*).
"""
import asyncio
import json
import os
import sys

import httpx


# Override via env var or local edit for your deployment
PUB_URL = "https://your-public-host.example.com"
BEARER = os.environ.get("URSA_OSCAR_MCP_BEARER_TOKEN", "")


async def probe(base_url: str, bearer: str) -> None:
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "text/event-stream"}
    timeout = httpx.Timeout(30.0, read=30.0)

    print(f"=== {base_url}/sse ===")
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", f"{base_url}/sse", headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                print(f"SSE GET {resp.status_code}: {body.decode(errors='replace')[:200]}")
                return

            session_endpoint = None
            current_event = None
            current_data_lines = []

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
                            asyncio.create_task(_call_tools_list(base_url, session_endpoint, bearer))
                        elif current_event == "message":
                            try:
                                d = json.loads(data)
                                if "result" in d and "tools" in d.get("result", {}):
                                    tools = d["result"]["tools"]
                                    print(f"tools/list: {len(tools)} tools")
                                    for t in tools:
                                        print(f"  - {t.get('name', '?')}")
                                    return
                            except json.JSONDecodeError:
                                pass
                    current_event = None
                    current_data_lines = []


async def _call_tools_list(base_url: str, session_endpoint: str, bearer: str) -> None:
    url = session_endpoint if session_endpoint.startswith("http") else f"{base_url}{session_endpoint}"
    h = {"Authorization": f"Bearer {bearer}"}
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(url, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.1"}}}, headers=h)
        await c.post(url, json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=h)
        await c.post(url, json={"jsonrpc":"2.0","id":2,"method":"tools/list"}, headers=h)


if __name__ == "__main__":
    if not BEARER:
        print("Set URSA_OSCAR_MCP_BEARER_TOKEN")
        sys.exit(1)
    target = sys.argv[1] if len(sys.argv) > 1 else PUB_URL
    try:
        asyncio.run(asyncio.wait_for(probe(target, BEARER), timeout=15.0))
    except asyncio.TimeoutError:
        print("(SSE closed after 15s)")
