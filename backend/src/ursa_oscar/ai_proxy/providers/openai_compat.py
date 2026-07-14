"""OpenAI-compatible chat-completions adapter — Phase 5 Ticket 1D.

Covers FIVE provider presets behind one implementation:
  - OpenAI         — native /v1/chat/completions
  - Google Gemini  — Google's OpenAI compat layer
  - OpenRouter     — multi-model proxy
  - Groq           — fast inference
  - Local LLM      — LocalAI / Ollama / llama.cpp / vLLM / LM Studio
  - Custom         — anything else compatible

All differ only in (endpoint URL, auth header format, model list). The
adapter itself sends the same wire shape: chat-completions with streaming
chunks and OpenAI-style function-calling.

Tool-calling normalization: provider speaks
``{"tool_calls": [{"id", "type": "function", "function": {"name", "arguments"}}]}``.
We map to/from ``AiMessage.tool_calls`` at the adapter boundary so the
shared executor and UI don't care which provider is on the other side.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from .base import AiMessage, AiStreamEvent, AiToolCall, ProviderAdapter, ProviderTestResult

logger = logging.getLogger(__name__)


class OpenAiCompatAdapter(ProviderAdapter):
    """One adapter, N providers. Constructor takes:
      ``api_key``   — operator-supplied; optional for some local servers
      ``endpoint``  — base URL ending at ``/v1`` (e.g.,
                      ``https://api.openai.com/v1``,
                      ``http://localrecall:8080/v1``).
                      MUST NOT include trailing ``/chat/completions``.
      ``model``     — model identifier the provider recognizes
      ``extra_headers`` — auth header dict from
                          ``presets.build_auth_header()``; the adapter
                          adds it verbatim to each request
    """

    async def chat(
        self,
        messages: list[AiMessage],
        tools: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[AiStreamEvent]:
        body = self._build_request(messages, tools, system_prompt)
        url = self.endpoint.rstrip("/") + "/chat/completions"

        headers = {"Content-Type": "application/json", **self.extra_headers}

        # 1.1.3 — stream read timeout was bumped from 120s to 300s for
        # thinking-mode models (Qwen3, DeepSeek-R1, GPT-OSS, Gemma-4)
        # which can spend 90+ seconds on the chain-of-thought before
        # emitting the first content token. Connect / write / pool stay
        # short because those legs shouldn't ever be slow.
        # 1.1.11 — operator-tunable via Settings → AI Assistant →
        # Request timeout. Defaults live in ai_proxy.__init__:
        # DEFAULT_TIMEOUT_SECONDS_LOCAL / _CLOUD. self.timeout_seconds
        # is populated by build_adapter; None here would only happen in
        # tests that skip build_adapter.
        read_timeout = float(self.timeout_seconds or 300.0)
        timeout = httpx.Timeout(
            connect=10.0, read=read_timeout, write=30.0, pool=10.0,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        yield AiStreamEvent(
                            event_type="error",
                            payload={
                                "message": (
                                    f"Upstream returned {resp.status_code}: "
                                    f"{text.decode('utf-8', errors='replace')[:500]}"
                                ),
                                "code": _classify_http_error(resp.status_code),
                            },
                        )
                        return

                    async for event in self._consume_sse(resp):
                        yield event
        except httpx.RequestError as e:
            yield AiStreamEvent(
                event_type="error",
                payload={"message": f"Network error: {e}", "code": "network_error"},
            )

    async def test_connection(self) -> ProviderTestResult:
        """Probe with a 1-token completion. Cheap, doesn't burn a real
        round-trip on the user's expensive tier. Most OpenAI-compat
        providers honor ``max_tokens=1``."""
        url = self.endpoint.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", **self.extra_headers}
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        # 1.1.11 — Test connection is a 1-token probe; use a short
        # timeout (30s or the operator's setting, whichever is smaller)
        # so the Settings-page test button doesn't hang for 5 minutes
        # against an unreachable endpoint.
        test_timeout = min(30.0, float(self.timeout_seconds or 30.0))
        try:
            async with httpx.AsyncClient(timeout=test_timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    return ProviderTestResult(
                        ok=False,
                        error=(
                            f"HTTP {resp.status_code}: "
                            f"{resp.text[:300]}"
                        ),
                    )
                data = resp.json()
                return ProviderTestResult(
                    ok=True,
                    model_info={
                        "model": data.get("model") or self.model,
                        "usage": data.get("usage"),
                    },
                )
        except Exception as e:
            return ProviderTestResult(ok=False, error=f"{type(e).__name__}: {e}")

    # ----- request shaping -----

    def _build_request(
        self,
        messages: list[AiMessage],
        tools: list[dict],
        system_prompt: str,
    ) -> dict:
        """Translate our normalized ``AiMessage`` list into OpenAI's
        chat-completions message shape."""
        out_messages: list[dict] = []
        if system_prompt:
            out_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg.role == "system":
                # Skip — system handled separately above. (Some shapes
                # send a leading system message in the array; we hoist it.)
                continue
            if msg.role == "tool":
                out_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "content": msg.content,
                })
                continue
            if msg.role == "assistant" and msg.tool_calls:
                out_messages.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })
                continue
            out_messages.append({"role": msg.role, "content": msg.content})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": out_messages,
            "stream": True,
            "tools": tools,
            # Letting the model choose freely. The system prompt + tool
            # descriptions steer it; "auto" is the OpenAI default but
            # explicit here for cross-provider clarity (some compat
            # layers default differently).
            "tool_choice": "auto",
            # 1.1.14 — ask the server to emit a usage object on the
            # terminating chunk of a STREAMED completion. Without this,
            # LocalAI / llama.cpp / vLLM stream tokens but never report
            # prompt/completion token counts, so the per-turn observability
            # line stays blank on exactly the local models where it's most
            # useful. OpenAI/Gemini/Groq honor it too; harmless where a
            # server already sends usage unprompted.
            "stream_options": {"include_usage": True},
        }
        # 1.1.14 — the empty-answer-trap fix. On a local reasoning model a
        # too-small server default for max_tokens gets eaten by the hidden
        # reasoning channel before the answer starts → blank 200. We send a
        # generous cap for the local family (resolved in build_adapter).
        # For cloud providers max_output_tokens is None here, so we OMIT
        # max_tokens entirely and let the provider apply its own large
        # default — sending a small number would truncate long cloud
        # answers, a regression. See ai_proxy._effective_max_tokens.
        if self.max_output_tokens is not None:
            body["max_tokens"] = int(self.max_output_tokens)
        return body

    # ----- streaming response decode -----

    async def _consume_sse(
        self, resp: httpx.Response,
    ) -> AsyncIterator[AiStreamEvent]:
        """Decode OpenAI-style ``data: {...}\\n\\n`` chunks into our
        normalized stream events."""
        # Track partial tool calls — function arguments stream in
        # multiple chunks; we buffer until the call completes.
        partial_tool_calls: dict[int, dict] = {}
        emitted_starts: set[int] = set()
        finish_reason: str | None = None
        usage: dict | None = None

        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload_str = line[6:].strip()
            if payload_str == "[DONE]":
                break
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.debug("openai_compat: skipping malformed SSE line: %s", payload_str[:120])
                continue

            # Some providers send usage as a separate "final" chunk
            # without choices. Capture and continue.
            if not payload.get("choices"):
                if payload.get("usage"):
                    usage = payload["usage"]
                continue

            choice = payload["choices"][0]
            delta = choice.get("delta") or {}

            # Text delta.
            if delta.get("content"):
                yield AiStreamEvent(
                    event_type="text",
                    payload={"text": delta["content"]},
                )

            # 1.1.3 — reasoning delta. Thinking-mode models (Qwen3,
            # DeepSeek-R1, etc.) emit chain-of-thought in a separate
            # field. The naming varies by provider/server:
            #   - Qwen3 via LocalAI/Ollama:  delta.reasoning
            #   - DeepSeek / Alibaba native: delta.reasoning_content
            # Surface as a distinct `reasoning` event so the UI can
            # render it visually separate from the final answer (and
            # so subsequent chat turns don't carry the chain-of-thought
            # back into the conversation context).
            reasoning_text = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning_text:
                yield AiStreamEvent(
                    event_type="reasoning",
                    payload={"text": reasoning_text},
                )

            # Tool-call deltas. OpenAI streams partial arguments — each
            # chunk has an index into a tool-call array and a partial
            # JSON string fragment.
            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                slot = partial_tool_calls.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""},
                )
                if tc_delta.get("id"):
                    slot["id"] = tc_delta["id"]
                fn = tc_delta.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

                # Emit a start event the first time we see id + name.
                if idx not in emitted_starts and slot["id"] and slot["name"]:
                    emitted_starts.add(idx)
                    yield AiStreamEvent(
                        event_type="tool_call_start",
                        payload={"id": slot["id"], "name": slot["name"]},
                    )
                # And an input delta for the UI to show the args streaming in.
                if fn.get("arguments"):
                    yield AiStreamEvent(
                        event_type="tool_call_input",
                        payload={
                            "id": slot["id"],
                            "partial_input": fn["arguments"],
                        },
                    )

            fin = choice.get("finish_reason")
            if fin:
                finish_reason = fin

        # End of stream. Emit tool_call_complete for each tool call we
        # buffered (with parsed arguments) — these are what the executor
        # consumes server-side after the stream.
        for slot in partial_tool_calls.values():
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw_arguments": slot["arguments"]}
            yield AiStreamEvent(
                event_type="tool_call_complete",
                payload={
                    "id": slot["id"],
                    "name": slot["name"],
                    "arguments": args,
                },
            )

        yield AiStreamEvent(
            event_type="complete",
            payload={"stop_reason": finish_reason, "usage": usage},
        )


def _classify_http_error(status: int) -> str:
    if status == 401 or status == 403:
        return "unauthorized"
    if status == 429:
        return "rate_limit"
    if 400 <= status < 500:
        return "bad_request"
    return "upstream_error"
