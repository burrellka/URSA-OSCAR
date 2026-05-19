# AI assistant not responding

When the chat panel is configured but conversations don't work as expected.

## Symptom 1 — Chat panel doesn't appear at all

The Daily View page renders but there's no chat panel on the right.

**Cause:** No AI provider is configured.

**Fix:** Settings → AI Assistant → pick a provider preset → paste your API key → save. The panel appears immediately on the next Daily View load.

## Symptom 2 — "AI provider not configured" error in chat

You opened the chat panel and submitted a message, but the response is an error message about provider configuration.

**Cause:** the secrets store has no key for the active provider preset, or the active preset is set to one whose key got removed.

**Fix:**

- Confirm in Settings → AI Assistant which preset is "active"
- Confirm a key is set for that preset (the UI shows `••• (set)` when present, `not set` otherwise)
- If unset: paste the key and save
- If set but invalid: re-save with a fresh key from the provider's console

## Symptom 3 — "Unauthorized" / 401 from the provider

Chat returns an error like "Anthropic API returned 401" or "OpenAI 401: invalid API key."

**Cause:** the API key URSA-OSCAR has is wrong, expired, or revoked.

**Fix:**

- Generate a new key from the provider's console (Anthropic console, OpenAI platform, etc.)
- Paste it into Settings → AI Assistant
- Save

If you suspect the secrets store is corrupted (key was set, save succeeded, but the provider still rejects), regenerate `/data/secrets.enc`:

```bash
# Stop the api container so it doesn't write to the file while we're working
docker compose stop ursa-oscar-api

# Move the existing secrets file aside (don't delete; you might want it back)
mv /opt/ursa-oscar/data/secrets.enc /opt/ursa-oscar/data/secrets.enc.bak

# Restart api
docker compose start ursa-oscar-api
```

Then re-enter your API keys via Settings → AI Assistant. The fresh secrets.enc is empty until you do.

## Symptom 4 — Slow first response, then stream stalls

The chat panel shows "thinking..." for a long time, then a partial response that stops mid-word.

**Cause:** SSE connection dropped. URSA-OSCAR streams responses via Server-Sent Events; if the connection between browser and api breaks, the stream ends prematurely.

**Common causes:**

- Cloudflare tunnel timing out (the default tunnel idle timeout is short — increase it for SSE traffic)
- Reverse proxy buffering (nginx default buffering breaks SSE; set `proxy_buffering off;` for the `/api/v1/ai/chat` path)
- Browser closed the tab or navigated away mid-stream

**Fix per cause:**

- Cloudflare: in the tunnel config, set `--no-tls-verify` and `--no-chunked-encoding`. Or use a non-tunnel proxy.
- nginx: `proxy_buffering off; proxy_cache off; proxy_read_timeout 600s;` for the chat path.
- Caddy: SSE is generally fine out-of-the-box; check `flush_interval -1` is set (Caddy default for SSE).

## Symptom 5 — Tool calls fail silently

The AI claims it called a tool, but no data appears, or the response says "the tool returned no data."

**Cause:** the AI provider tool-calling implementation didn't actually emit the tool call in a form URSA-OSCAR can decode. Different providers handle tool calls differently:

- **Claude (native Anthropic)** — best tool-calling reliability. If you're getting tool issues with Claude, file an issue.
- **OpenAI** — solid tool-calling, very close to Claude in reliability.
- **Gemini / OpenRouter / Groq via OpenAI-compat** — sometimes models don't emit tool_call format correctly; the AI's response may include a JSON blob in the message body that URSA-OSCAR doesn't parse as a tool call.
- **Local LLMs** — varies enormously by model. Some local LLMs claim OpenAI compatibility but don't emit proper tool_calls. Test with a known-good model first.

**Fix:**

- Switch to Claude or OpenAI to confirm whether the issue is the provider or URSA-OSCAR
- If it works on Claude but not on a smaller model, that's a model-capability issue. Use a stronger model.
- The system prompt template can be edited to be more explicit about tool format; sometimes that helps marginal models.

## Symptom 6 — Conversation history disappears

You came back to a date you previously chatted about, but the chat is empty.

**Cause:** conversation history lives in browser localStorage, keyed by date. If you cleared browser data, switched browsers, or are using a different device, the history is gone.

**Fix:** there's no fix. This is by design (Phase 5 Decision 5 — no server-side conversation memory). Plan accordingly:

- For important conversations you want to reference later, copy them to a manual log entry or note
- A future feature (Future Direction page) will add an "export conversation" affordance

## Symptom 7 — System prompt seems wrong

The AI is responding in a way that suggests it's missing context (asking about your diagnosis, asking what your treatment goals are, etc.) even though you have a profile filled in.

**Cause:** the system prompt template might not be reading your profile correctly, OR the template you saved doesn't include `{user_profile_summary}` in the right place.

**Fix:**

- Settings → AI Assistant → System Prompt Template → check that the template includes `{user_profile_summary}` somewhere
- If your custom template removed it, click "Reset to factory default" to restore the bundled DEFAULT_TEMPLATE
- Confirm Profile actually has data: visit the Profile page and check the fields are populated

## Symptom 8 — Provider returns "context length exceeded"

The chat fails with a token-limit error after a long conversation.

**Cause:** every turn of the conversation grows the context. After 50+ turns, you're hitting the provider's context window limit.

**Fix:**

- Start a new conversation by switching to a different date and back
- For Claude users: prompt caching (Phase 6.5+) reduces *cost* but doesn't change the context window; long conversations still exceed limits
- The system prompt + tool definitions take ~3-5K tokens; each turn adds ~500-2000. A 100K-context model handles ~50-100 turns; a 200K-context model handles more.

A future feature will add a "summarize and continue" affordance for very long conversations. Not yet implemented.

## Symptom 9 — Local LLM endpoint not reachable

You configured a local LLM (LM Studio, Ollama, vLLM) and chat fails with a connection error.

**Cause:** the URL you provided in Settings → AI Assistant isn't reachable from inside the api container.

**Fix:**

- If your LLM is running on the same docker host as URSA-OSCAR: use `host.docker.internal` instead of `localhost` (e.g., `http://host.docker.internal:11434/v1` for Ollama)
- If your LLM is on another machine on the LAN: use the LAN IP (e.g., `http://192.168.1.50:11434/v1`)
- `docker exec ursa-oscar-api curl <your-url>/models` to test reachability from inside the container

## When to check the api container logs

```bash
docker logs ursa-oscar-api 2>&1 | grep -E "ai_proxy|chat|provider" | tail -50
```

Most AI-chat issues land in those log lines. Exceptions, provider error responses, and tool-call decoding issues are all visible there.
