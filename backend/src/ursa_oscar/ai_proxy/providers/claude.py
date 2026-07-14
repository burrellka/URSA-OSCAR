"""Claude adapter — Phase 5 Ticket 1C.

Native Anthropic Messages API with streaming + tool calling. The
``anthropic`` Python SDK does the protocol-level translation; this
adapter only translates between our normalized ``AiMessage`` shape
and Anthropic's native ``messages=[...]`` + ``tools=[...]`` shape.

Tool-call mapping:
  Anthropic emits ``tool_use`` content blocks inside an assistant
  message; we translate those into ``AiMessage.tool_calls``. Tool
  results come back in the next user message as ``tool_result``
  blocks; we send them as ``AiMessage(role="tool", tool_call_id=..., content=...)``
  and the adapter rebuilds the ``tool_result`` block at request time.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from .base import AiMessage, AiStreamEvent, ProviderAdapter, ProviderTestResult

logger = logging.getLogger(__name__)


class ClaudeAdapter(ProviderAdapter):
    """Anthropic Messages API client. Built on the official ``anthropic``
    SDK so we get protocol updates for free.

    The SDK is imported lazily inside ``chat()`` / ``test_connection()``
    so that importing this module in environments without the package
    installed (e.g., the MCP container) doesn't blow up at import time.
    The pyproject.toml does declare the dep, so production deploys are
    fine — this lazy import is for the seam between adapter loading
    and adapter use.
    """

    async def chat(
        self,
        messages: list[AiMessage],
        tools: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[AiStreamEvent]:
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            yield AiStreamEvent(
                event_type="error",
                payload={
                    "message": (
                        "anthropic SDK not installed. Add `anthropic>=0.40` "
                        "to backend/pyproject.toml and rebuild the api image."
                    ),
                    "code": "missing_dependency",
                },
            )
            return

        if not self.api_key:
            yield AiStreamEvent(
                event_type="error",
                payload={
                    "message": "Claude API key is not configured.",
                    "code": "unauthorized",
                },
            )
            return

        # 1.1.11 — operator-tunable request timeout. The Anthropic SDK
        # accepts a plain float (seconds) for the request timeout. See
        # ai_proxy.__init__ for the DEFAULT_TIMEOUT_SECONDS_* constants;
        # self.timeout_seconds is populated by build_adapter.
        client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.endpoint or None,
            timeout=float(self.timeout_seconds or 120.0),
        )

        try:
            request = self._build_request(messages, tools, system_prompt)
        except Exception as e:
            yield AiStreamEvent(
                event_type="error",
                payload={"message": f"Request build failed: {e}", "code": "bad_request"},
            )
            return

        # Track partial tool calls — Anthropic streams tool inputs in
        # ``input_json_delta`` events similar to OpenAI's argument
        # fragments. Buffer until ``content_block_stop`` for the block.
        tool_buffers: dict[int, dict] = {}
        emitted_starts: set[int] = set()
        stop_reason: str | None = None
        usage: dict | None = None

        try:
            async with client.messages.stream(**request) as stream:
                async for event in stream:
                    et = getattr(event, "type", None)

                    if et == "content_block_start":
                        block = getattr(event, "content_block", None)
                        idx = getattr(event, "index", 0)
                        if getattr(block, "type", None) == "tool_use":
                            tool_buffers[idx] = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "arguments_raw": "",
                            }
                            if idx not in emitted_starts:
                                emitted_starts.add(idx)
                                yield AiStreamEvent(
                                    event_type="tool_call_start",
                                    payload={
                                        "id": tool_buffers[idx]["id"],
                                        "name": tool_buffers[idx]["name"],
                                    },
                                )
                        continue

                    if et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", None)
                        idx = getattr(event, "index", 0)
                        if delta_type == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                yield AiStreamEvent(
                                    event_type="text",
                                    payload={"text": text},
                                )
                        elif delta_type == "input_json_delta":
                            partial = getattr(delta, "partial_json", "")
                            if idx in tool_buffers:
                                tool_buffers[idx]["arguments_raw"] += partial
                                yield AiStreamEvent(
                                    event_type="tool_call_input",
                                    payload={
                                        "id": tool_buffers[idx]["id"],
                                        "partial_input": partial,
                                    },
                                )
                        continue

                    if et == "message_delta":
                        delta = getattr(event, "delta", None)
                        sr = getattr(delta, "stop_reason", None)
                        if sr:
                            stop_reason = sr
                        # usage may land on the message_delta or final
                        # message_stop event — capture from either.
                        u = getattr(event, "usage", None)
                        if u is not None:
                            usage = _usage_dict(u)
                        continue

                    if et == "message_stop":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            u = getattr(msg, "usage", None)
                            if u is not None:
                                usage = _usage_dict(u)
                        continue

            # Stream ended. Emit a tool_call_complete for each buffered
            # tool call so the server-side executor has parsed args.
            for slot in tool_buffers.values():
                try:
                    args = (
                        json.loads(slot["arguments_raw"])
                        if slot["arguments_raw"] else {}
                    )
                except json.JSONDecodeError:
                    args = {"_raw_arguments": slot["arguments_raw"]}
                yield AiStreamEvent(
                    event_type="tool_call_complete",
                    payload={
                        "id": slot["id"],
                        "name": slot["name"],
                        "arguments": args,
                    },
                )

            # Phase 6.5 — log cache hit/miss at DEBUG so the operator
            # can verify caching is engaged. cache_read_input_tokens > 0
            # on the 2nd+ identical request means the prefix matched
            # and the cached portion was served at ~10% cost.
            if usage:
                logger.debug(
                    "claude.chat: usage input=%s output=%s "
                    "cache_creation=%s cache_read=%s",
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("cache_creation_input_tokens"),
                    usage.get("cache_read_input_tokens"),
                )

            yield AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": stop_reason, "usage": usage},
            )
        except Exception as e:
            logger.exception("claude.chat: error during stream")
            yield AiStreamEvent(
                event_type="error",
                payload={
                    "message": f"{type(e).__name__}: {e}",
                    "code": _classify_anthropic_error(e),
                },
            )

    async def test_connection(self) -> ProviderTestResult:
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            return ProviderTestResult(
                ok=False,
                error="anthropic SDK not installed in the API container.",
            )
        if not self.api_key:
            return ProviderTestResult(
                ok=False,
                error="Claude API key is not configured.",
            )

        # 1.1.11 — operator-tunable request timeout. The Anthropic SDK
        # accepts a plain float (seconds) for the request timeout. See
        # ai_proxy.__init__ for the DEFAULT_TIMEOUT_SECONDS_* constants;
        # self.timeout_seconds is populated by build_adapter.
        client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.endpoint or None,
            timeout=float(self.timeout_seconds or 120.0),
        )
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return ProviderTestResult(
                ok=True,
                model_info={
                    "model": resp.model,
                    "usage": _usage_dict(resp.usage) if resp.usage else None,
                },
            )
        except Exception as e:
            return ProviderTestResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
            )

    # ----- request shaping -----

    def _build_request(
        self,
        messages: list[AiMessage],
        tools: list[dict],
        system_prompt: str,
    ) -> dict:
        """Translate our normalized message list + tools into Anthropic's
        native shape. Drops the leading system role (Anthropic takes
        ``system=...`` as a separate parameter). Reformats tool-result
        messages into ``tool_result`` content blocks on the next user
        turn."""
        anthropic_messages: list[dict] = []
        # Anthropic doesn't accept a `system` role inside `messages` — it's
        # a top-level parameter. Hoist it out.
        for msg in messages:
            if msg.role == "system":
                continue

            if msg.role == "tool":
                # Tool results become a content block on a USER message.
                # If the previous Anthropic message is already a user
                # turn with tool_result blocks, append to it; otherwise
                # create a new user message.
                block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content,
                }
                if (
                    anthropic_messages
                    and anthropic_messages[-1]["role"] == "user"
                    and isinstance(anthropic_messages[-1]["content"], list)
                    and any(
                        b.get("type") == "tool_result"
                        for b in anthropic_messages[-1]["content"]
                    )
                ):
                    anthropic_messages[-1]["content"].append(block)
                else:
                    anthropic_messages.append({"role": "user", "content": [block]})
                continue

            if msg.role == "assistant" and msg.tool_calls:
                blocks: list[dict] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
                continue

            anthropic_messages.append({"role": msg.role, "content": msg.content})

        # Phase 6.5 — prompt caching for the system prompt + tools list.
        #
        # Anthropic's caching API: each cacheable segment is a content
        # block with cache_control={"type": "ephemeral"}. The marker
        # tells Anthropic "everything up to and including this block is
        # the cache prefix." Subsequent identical requests with the
        # same prefix get billed at ~10% for the cached portion.
        #
        # We cache:
        #   1. The system prompt — same across every turn of a session.
        #   2. The full tools list — same across every turn (operators
        #      don't re-register tools at runtime). Only the LAST tool
        #      block needs cache_control; Anthropic treats everything
        #      up to that marker as the prefix.
        # Messages stay unmarked — they grow per turn and shouldn't be
        # part of the cache prefix.
        anthropic_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]
        if anthropic_tools:
            anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}

        request = {
            "model": self.model,
            # 1.1.14 — respect the operator's max_output_tokens override
            # when set; otherwise keep the long-standing 4096 default.
            # Anthropic REQUIRES max_tokens (unlike the OpenAI-compat path
            # where it's optional), so there's always a concrete number
            # here — the operator knob just lets it be raised or lowered.
            "max_tokens": int(self.max_output_tokens) if self.max_output_tokens else 4096,
            # System prompt as a list of content blocks so we can
            # attach cache_control. The single-string form
            # (``system="..."``) is also valid Anthropic API but
            # can't carry cache markers.
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": anthropic_messages,
            "tools": anthropic_tools,
        }
        return request


def _usage_dict(usage_obj) -> dict:
    """Best-effort coercion of the Anthropic SDK's usage object to a
    plain dict. The SDK's pydantic models change shape over minor
    versions; use getattr + fallback."""
    if usage_obj is None:
        return {}
    out: dict = {}
    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens"):
        v = getattr(usage_obj, k, None)
        if v is not None:
            out[k] = v
    return out


def _classify_anthropic_error(e: Exception) -> str:
    name = type(e).__name__.lower()
    if "auth" in name or "401" in str(e) or "403" in str(e):
        return "unauthorized"
    if "rate" in name or "429" in str(e):
        return "rate_limit"
    if "invalid" in name or "400" in str(e):
        return "bad_request"
    return "upstream_error"
