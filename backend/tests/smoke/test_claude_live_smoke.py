"""Live Claude API smoke test — Phase 5 acceptance.

Single end-to-end query against the real Claude API:
  1. Boots a temp API with the 4-night fixture
  2. Configures the AI proxy via /ai/config with the operator's key
  3. Sends one chat request: "How was my sleep on 2026-05-10?"
  4. Streams the response, asserts the model called get_nightly_summary
     with date="2026-05-10"
  5. Asserts the model used the tool result rather than hallucinating

Why not in tests/integration/: this file deliberately requires a live
network call + a real API key. It runs only when CLAUDE_API_KEY_LIVE
env var is set. Default test suite skips it.

Budget: 1 short user message + 1 tool call round-trip ≈ 1-2k tokens
total ≈ $0.01-0.02 on Sonnet 4.5. Cheap and safe.

Usage:
    set CLAUDE_API_KEY_LIVE=sk-ant-...
    pytest backend/tests/smoke/test_claude_live_smoke.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet


CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY_LIVE")
SKIP_REASON = "CLAUDE_API_KEY_LIVE not set; live-LLM smoke test skipped"
pytestmark = pytest.mark.skipif(not CLAUDE_KEY, reason=SKIP_REASON)


def _allocate_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_api(tmp_path, monkeypatch):
    """Boot the API in a background thread, seeded with the 4-night
    fixture, with a fresh Fernet key. Returns the base URL."""
    import ursa_oscar.config as _config_mod
    import uvicorn
    from ursa_oscar.ingestion.importer import import_path
    from ursa_oscar.main import create_app
    from ursa_oscar.storage.db import DuckDBManager
    from ursa_oscar.storage.migrations import apply_migrations

    db_file = tmp_path / "smoke.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    monkeypatch.setenv("URSA_OSCAR_SECRET_KEY", Fernet.generate_key().decode("ascii"))
    _config_mod._settings = None

    fixture_root = (
        Path(__file__).resolve().parents[1]
        / "regression" / "fixtures" / "nights" / "oscar-reference"
    )
    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    import_path(fixture_root, seeder, skip_existing=False)
    seeder.close()

    port = _allocate_port()
    base_url = f"http://127.0.0.1:{port}"
    app = create_app()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", lifespan="on", access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30.0
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError("test API didn't come up")

    yield base_url

    server.should_exit = True
    thread.join(timeout=10.0)
    _config_mod._settings = None


@pytest.mark.asyncio
async def test_claude_routes_get_nightly_summary_with_correct_date(live_api):
    """End-to-end: the model receives a date-specific question, picks
    the right tool, passes the right date, and uses the result.

    This is the smallest live-API test that validates the full chat
    pipeline: SecretStore round-trip, adapter dispatch, tool execution,
    tool result fed back into conversation, second-turn text response.
    """
    # Configure AI proxy.
    async with httpx.AsyncClient(base_url=live_api, timeout=60.0) as client:
        resp = await client.post("/api/v1/ai/config", json={
            "enabled": True,
            "provider_id": "claude",
            "model": "claude-sonnet-4-5-20250929",
            "api_key": CLAUDE_KEY,
        })
        resp.raise_for_status()

        # Smoke test the connection probe first — cheap.
        test_resp = await client.post(
            "/api/v1/ai/test", json={"provider_id": "claude"},
        )
        test_resp.raise_for_status()
        test_body = test_resp.json()
        assert test_body["ok"], f"test_connection failed: {test_body}"
        print(f"[live smoke] test_connection OK: model={test_body.get('model_info')}")

        # Single chat request — pick a date the LLM cannot already know
        # exists (it's fictional); will need to call get_nightly_summary.
        chat_body = {
            "messages": [
                {"role": "user", "content": "How was my sleep on 2026-05-10? Keep your answer to one sentence."},
            ],
            "context": {"current_date": "2026-05-10", "include_profile": True},
        }

        events = []
        async with client.stream(
            "POST", "/api/v1/ai/chat", json=chat_body,
        ) as stream_resp:
            stream_resp.raise_for_status()
            async for line in stream_resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if not payload:
                    continue
                event = json.loads(payload)
                events.append(event)
                # Print as we go so a live run is visible in -s output.
                etype = event["event_type"]
                if etype == "text":
                    print(event["payload"]["text"], end="", flush=True)
                elif etype == "tool_call_start":
                    print(f"\n[tool] starting {event['payload']['name']}", flush=True)
                elif etype == "tool_call_complete":
                    print(f"\n[tool] args: {event['payload']['arguments']}", flush=True)
                elif etype == "tool_result":
                    summary = (event["payload"]["result"] or {}).get("data", {})
                    if isinstance(summary, dict):
                        print(f"\n[tool] result: AHI={summary.get('total_ahi')} sessions={summary.get('session_count')}", flush=True)
                elif etype == "complete":
                    usage = event["payload"].get("usage")
                    print(f"\n[done] stop={event['payload'].get('stop_reason')} usage={usage}", flush=True)
                elif etype == "error":
                    print(f"\n[err] {event['payload']}", flush=True)

    # ---- Assertions ----

    # The model must have called at least one tool.
    tool_starts = [e for e in events if e["event_type"] == "tool_call_start"]
    assert tool_starts, "no tool calls — model failed to use available tools"

    # The first tool called should be get_nightly_summary.
    first_tool = tool_starts[0]["payload"]["name"]
    assert first_tool == "get_nightly_summary", (
        f"Wrong first tool: got {first_tool!r}, expected get_nightly_summary. "
        f"Acceptance matrix Q1 fails."
    )

    # The tool's arguments should contain the right date.
    completes = [e for e in events if e["event_type"] == "tool_call_complete"]
    assert completes, "no tool_call_complete event"
    args = completes[0]["payload"]["arguments"]
    assert args.get("date") == "2026-05-10", f"wrong date: {args}"

    # The tool should have returned successfully.
    results = [e for e in events if e["event_type"] == "tool_result"]
    assert results, "no tool_result event"
    assert results[0]["payload"]["result"]["ok"] is True

    # The model must have produced text after the tool result —
    # i.e., it used the tool data rather than just calling and stopping.
    final_complete = next((e for e in events if e["event_type"] == "complete"), None)
    assert final_complete, "no complete event"
    text_events_after_tool = [
        e for e in events[events.index(results[0]):]
        if e["event_type"] == "text"
    ]
    assert text_events_after_tool, "model didn't produce text after tool result"

    # Sanity check: the text mentions the date.
    final_text = "".join(e["payload"]["text"] for e in text_events_after_tool)
    assert "2026-05-10" in final_text or "May 10" in final_text or "5/10" in final_text, (
        f"final answer doesn't reference the date: {final_text!r}"
    )
    print(f"\n[smoke] passed: {first_tool} called with correct date; "
          f"{len(text_events_after_tool)} text deltas in final answer")
