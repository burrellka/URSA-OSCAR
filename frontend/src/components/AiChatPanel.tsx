/**
 * Phase 5 Ticket 2 — slide-in chat panel for Daily View.
 *
 * Conversations are per-date (Decision 5): keyed in localStorage as
 * `ursa_oscar_chat_{YYYY-MM-DD}`. Switching to a different night
 * clears the visible panel; the prior conversation comes back if you
 * switch back.
 *
 * Streaming: each user message kicks off `api.chatStream` which yields
 * `AiStreamEvent` objects. We append text deltas to the current
 * assistant message, track tool calls in a per-message panel, and
 * stop at `complete` / `error`.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Bot, ChevronDown, ChevronRight, Send, Trash2, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, ApiError } from '../api/client';
import type {
  AiMaskedConfig,
  AiMessage,
  AiStreamEvent,
} from '../api/types';


// LocalStorage key shape. Per-date conversations.
function chatKey(date: string | null): string {
  return `ursa_oscar_chat_${date || 'overview'}`;
}


/** One assistant message can fan out into multiple tool calls + their results.
 *  We track these alongside the text so the UI can render them inline with
 *  the message they belong to. */
interface ToolCallDisplay {
  id: string;
  name: string;
  status: 'running' | 'complete' | 'error';
  arguments?: Record<string, unknown>;
  result_summary?: string;     // short one-line summary
  result_full?: unknown;       // expandable raw envelope
}


interface DisplayMessage {
  role: 'user' | 'assistant';
  content: string;
  tool_calls?: ToolCallDisplay[];
  in_flight?: boolean;
  /** Epoch ms when the assistant turn started streaming. Used to
   *  compute elapsed time. */
  started_at?: number;
  /** Seconds the turn took once complete. Persisted on the message
   *  for the "took 12s" footer on past responses. */
  elapsed_seconds?: number;
}


interface Props {
  open: boolean;
  onClose: () => void;
  /** ISO date of the Daily View currently showing. Used for context
   *  + localStorage scoping. Can be null on Overview. */
  currentDate: string | null;
  aiConfig: AiMaskedConfig | null;
}


export default function AiChatPanel({ open, onClose, currentDate, aiConfig }: Props) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  // 0.9.1 — tick a clock once a second while streaming so the UI can
  // show an elapsed-time indicator. Pure cosmetic; setNow only fires
  // while streaming === true.
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (!streaming) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [streaming]);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Load conversation from localStorage when date or panel-open changes.
  useEffect(() => {
    if (!open) return;
    const raw = localStorage.getItem(chatKey(currentDate));
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as { messages: DisplayMessage[] };
        setMessages(parsed.messages || []);
      } catch {
        setMessages([]);
      }
    } else {
      setMessages([]);
    }
    setStreamError(null);
  }, [open, currentDate]);

  // Persist on every change. Cheap — localStorage write is <1ms.
  useEffect(() => {
    if (!open) return;
    const payload = {
      date: currentDate,
      messages,
      updated_at: new Date().toISOString(),
    };
    localStorage.setItem(chatKey(currentDate), JSON.stringify(payload));
  }, [messages, currentDate, open]);

  // Auto-scroll to bottom on new content.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streaming]);

  // Abort any in-flight stream on panel close / unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Date-aware suggested prompts. Replace on new conversation.
  const suggestedPrompts = useMemo(() => {
    const d = currentDate || 'this night';
    return [
      `How was my sleep on ${d}?`,
      `What's my trend over the last 7 days?`,
      `Did anything unusual happen on ${d}?`,
    ];
  }, [currentDate]);

  async function sendMessage(text: string) {
    if (!text.trim() || streaming) return;
    setStreamError(null);

    const userMsg: DisplayMessage = { role: 'user', content: text };
    const assistantMsg: DisplayMessage = {
      role: 'assistant',
      content: '',
      in_flight: true,
      tool_calls: [],
      started_at: Date.now(),
    };
    const nextMessages = [...messages, userMsg, assistantMsg];
    setMessages(nextMessages);
    setNow(Date.now());  // seed the ticker so the indicator starts at 0s
    setInput('');
    setStreaming(true);

    // Build the wire-format conversation: only role + content + tool_call shapes.
    const wireMessages: AiMessage[] = [];
    for (const m of [...messages, userMsg]) {
      wireMessages.push({ role: m.role, content: m.content });
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const stream = api.chatStream(
        wireMessages,
        {
          current_date: currentDate || undefined,
          include_profile: true,
        },
        ctrl.signal,
      );

      for await (const event of stream) {
        applyStreamEvent(setMessages, event);
        if (event.event_type === 'complete' || event.event_type === 'error') {
          if (event.event_type === 'error') {
            setStreamError((event.payload?.message as string) || 'Unknown error');
          }
          break;
        }
      }
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') {
        // intentional cancel — leave the partial message in place
      } else if (e instanceof ApiError) {
        setStreamError(`${e.message}${e.body ? ` — ${JSON.stringify(e.body)}` : ''}`);
      } else {
        setStreamError(String(e));
      }
    } finally {
      setStreaming(false);
      setMessages((cur) => cur.map((m, i) => {
        if (i !== cur.length - 1) return m;
        const elapsedMs = m.started_at ? Date.now() - m.started_at : 0;
        return {
          ...m,
          in_flight: false,
          elapsed_seconds: Math.round(elapsedMs / 100) / 10,  // tenths
        };
      }));
      abortRef.current = null;
    }
  }

  function clearConversation() {
    if (!confirm(
      "Clear this conversation? Your chat history is stored only in this browser and will be lost.",
    )) return;
    setMessages([]);
    localStorage.removeItem(chatKey(currentDate));
  }

  if (!open) return null;

  const providerLabel = formatProviderHeader(aiConfig);

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: '480px',
      maxWidth: '100vw',
      background: 'var(--bg-primary, #fff)',
      borderLeft: '1px solid var(--border-color, #e5e7eb)',
      boxShadow: '-8px 0 32px rgba(0,0,0,0.12)',
      zIndex: 100,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.75rem 1rem',
        borderBottom: '1px solid var(--border-color, #e5e7eb)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Bot size={18} color="var(--accent-primary, #2563eb)" />
          <div>
            <div style={{ fontWeight: 600, fontSize: '0.9375rem' }}>URSA</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted, #6b7280)' }}>
              {providerLabel}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {messages.length > 0 && (
            <button
              type="button"
              className="btn-secondary"
              onClick={clearConversation}
              title="Clear conversation"
              style={{ padding: '0.25rem 0.5rem' }}
            >
              <Trash2 size={14} />
            </button>
          )}
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            title="Close"
            style={{ padding: '0.25rem 0.5rem' }}
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        style={{
          flex: 1, overflowY: 'auto', padding: '1rem',
          display: 'flex', flexDirection: 'column', gap: '0.875rem',
        }}
      >
        {messages.length === 0 && (
          <SuggestedPrompts
            prompts={suggestedPrompts}
            onPick={(p) => { setInput(p); }}
            aiConfig={aiConfig}
          />
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} message={m} now={now} />
        ))}
        {streamError && (
          <div className="error-banner" style={{ fontSize: '0.8125rem' }}>
            {streamError}
          </div>
        )}
      </div>

      {/* Composer */}
      <div style={{
        padding: '0.75rem 1rem',
        borderTop: '1px solid var(--border-color, #e5e7eb)',
        display: 'flex', gap: '0.5rem', alignItems: 'flex-end',
      }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              sendMessage(input);
            }
          }}
          placeholder={
            aiConfig?.enabled
              ? "Ask URSA about your sleep…"
              : "AI Assistant is disabled. Configure it in Settings → AI Assistant."
          }
          disabled={!aiConfig?.enabled || streaming}
          rows={2}
          style={{
            flex: 1, resize: 'none',
            padding: '0.5rem 0.75rem',
            borderRadius: '6px',
            border: '1px solid var(--border-color, #e5e7eb)',
            fontFamily: 'inherit',
            fontSize: '0.875rem',
          }}
        />
        <button
          type="button"
          className="btn-primary"
          onClick={() => sendMessage(input)}
          disabled={!aiConfig?.enabled || streaming || !input.trim()}
          style={{ height: '36px', alignSelf: 'flex-end' }}
          title={streaming ? "Streaming…" : "Send"}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}


// ---------- Stream-event reducer ----------


function applyStreamEvent(
  setMessages: React.Dispatch<React.SetStateAction<DisplayMessage[]>>,
  event: AiStreamEvent,
) {
  setMessages((current) => {
    if (current.length === 0) return current;
    // Always mutate the LAST message (the in-flight assistant turn).
    const idx = current.length - 1;
    const next = [...current];
    const msg = { ...next[idx], tool_calls: [...(next[idx].tool_calls || [])] };

    switch (event.event_type) {
      case 'text': {
        const text = String(event.payload?.text || '');
        msg.content = msg.content + text;
        break;
      }
      case 'tool_call_start': {
        const id = String(event.payload?.id || '');
        const name = String(event.payload?.name || 'unknown');
        if (!msg.tool_calls!.find((tc) => tc.id === id)) {
          msg.tool_calls!.push({ id, name, status: 'running' });
        }
        break;
      }
      case 'tool_call_complete': {
        const id = String(event.payload?.id || '');
        const args = event.payload?.arguments as Record<string, unknown>;
        msg.tool_calls = msg.tool_calls!.map((tc) =>
          tc.id === id ? { ...tc, status: 'running' as const, arguments: args } : tc,
        );
        break;
      }
      case 'tool_result': {
        const id = String(event.payload?.id || '');
        const result = event.payload?.result as { ok?: boolean; data?: unknown };
        msg.tool_calls = msg.tool_calls!.map((tc) =>
          tc.id === id
            ? {
                ...tc,
                status: result?.ok === false ? 'error' as const : 'complete' as const,
                result_full: result,
                result_summary: summarizeToolResult(tc.name, tc.arguments, result),
              }
            : tc,
        );
        break;
      }
      // 'tool_call_input' streams partial JSON. We don't render the input
      // mid-stream; the 'arguments' on `tool_call_complete` is enough.
      // 'complete' and 'error' don't mutate the message.
    }

    next[idx] = msg;
    return next;
  });
}


function summarizeToolResult(
  name: string,
  _args: Record<string, unknown> | undefined,
  result: { ok?: boolean; data?: unknown },
): string {
  if (result?.ok === false) {
    const err = (result as Record<string, unknown>).error;
    return `error — ${typeof err === 'string' ? err.slice(0, 80) : 'unknown'}`;
  }
  const data = result?.data as Record<string, unknown> | undefined;
  if (!data) return 'no data';
  // Per-tool one-liners. Falls back to a generic key=value tail for any
  // tool we haven't special-cased.
  if (name === 'get_nightly_summary') {
    if (Array.isArray(data)) return `${data.length} night(s)`;
    const d = data as { date?: string; total_ahi?: number; session_count?: number };
    return `${d.date} · AHI ${d.total_ahi?.toFixed?.(2) ?? '—'} · ${d.session_count ?? '?'} sessions`;
  }
  if (name === 'get_ahi_breakdown') {
    const cn = (data as { central?: { count?: number } }).central?.count;
    const ob = (data as { obstructive?: { count?: number } }).obstructive?.count;
    return `central=${cn ?? '?'} obstructive=${ob ?? '?'}`;
  }
  if (name === 'list_available_nights') {
    const n = (data as { nights?: unknown[] }).nights?.length;
    return `${n ?? 0} nights`;
  }
  if (name === 'analyze_correlation') {
    const r = (data as { pearson_r?: number; n_pairs?: number }).pearson_r;
    const n = (data as { n_pairs?: number }).n_pairs;
    return `r=${r?.toFixed?.(2) ?? '—'} n=${n ?? '?'}`;
  }
  if (name === 'get_trend') {
    const interp = (data as { interpretation?: string }).interpretation;
    return interp || 'computed';
  }
  // Generic fallback: pluck up-to-3 leaf scalars.
  const pairs: string[] = [];
  for (const [k, v] of Object.entries(data).slice(0, 3)) {
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      pairs.push(`${k}=${v}`);
    }
  }
  return pairs.join(' · ') || 'ok';
}


// ---------- Sub-components ----------


function SuggestedPrompts({
  prompts, onPick, aiConfig,
}: { prompts: string[]; onPick: (p: string) => void; aiConfig: AiMaskedConfig | null }) {
  if (!aiConfig?.enabled) {
    return (
      <div style={{
        padding: '1.5rem 1rem', textAlign: 'center',
        color: 'var(--text-muted, #6b7280)', fontSize: '0.875rem',
      }}>
        <Bot size={32} style={{ opacity: 0.4, marginBottom: '0.5rem' }} />
        <div>AI Assistant is not configured.</div>
        <div style={{ marginTop: '0.5rem' }}>
          <a href="/settings/ai" style={{ color: 'var(--accent-primary, #2563eb)' }}>
            Configure it in Settings → AI Assistant
          </a>
        </div>
      </div>
    );
  }
  return (
    <div>
      <div style={{
        fontSize: '0.8125rem', color: 'var(--text-muted, #6b7280)',
        marginBottom: '0.5rem',
      }}>
        Suggested prompts:
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
        {prompts.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPick(p)}
            style={{
              textAlign: 'left',
              padding: '0.5rem 0.75rem',
              border: '1px solid var(--border-color, #e5e7eb)',
              borderRadius: '6px',
              background: 'transparent',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontFamily: 'inherit',
              color: 'var(--text-primary, #111)',
            }}
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}


function MessageBubble({ message, now }: { message: DisplayMessage; now: number }) {
  const isUser = message.role === 'user';

  // Elapsed-time text. Two cases:
  // 1. In flight — recompute every render (the parent ticks `now` once a
  //    second while streaming so this re-renders).
  // 2. Completed — show the recorded `elapsed_seconds`.
  let elapsedText: string | null = null;
  if (!isUser) {
    if (message.in_flight && message.started_at) {
      const elapsed = Math.max(0, Math.round((now - message.started_at) / 100) / 10);
      elapsedText = `Thinking… ${elapsed.toFixed(1)}s`;
    } else if (message.elapsed_seconds != null) {
      elapsedText = `${message.elapsed_seconds.toFixed(1)}s`;
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
      <div style={{
        alignSelf: isUser ? 'flex-end' : 'flex-start',
        maxWidth: '85%',
        padding: '0.5rem 0.75rem',
        borderRadius: isUser ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
        background: isUser ? 'var(--accent-primary, #2563eb)' : 'var(--bg-secondary, #f3f4f6)',
        color: isUser ? '#fff' : 'var(--text-primary, #111)',
        fontSize: '0.875rem',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}>
        {isUser ? (
          message.content
        ) : (
          <div className="markdown-body">
            {message.content || (
              message.in_flight ? <em style={{ opacity: 0.6 }}>thinking…</em> : null
            )}
            {message.content && (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            )}
          </div>
        )}
      </div>
      {(message.tool_calls || []).map((tc) => (
        <ToolCallChip key={tc.id} call={tc} />
      ))}
      {elapsedText && (
        <div
          style={{
            alignSelf: 'flex-start',
            fontSize: '0.6875rem',
            color: 'var(--text-muted, #6b7280)',
            fontVariantNumeric: 'tabular-nums',
            marginTop: '-0.125rem',
            paddingLeft: '0.25rem',
          }}
          aria-live={message.in_flight ? 'polite' : 'off'}
        >
          {elapsedText}
        </div>
      )}
    </div>
  );
}


function ToolCallChip({ call }: { call: ToolCallDisplay }) {
  const [expanded, setExpanded] = useState(false);
  const color =
    call.status === 'error' ? 'var(--ahi-bad, #dc2626)' :
    call.status === 'complete' ? 'var(--text-secondary, #4b5563)' :
    'var(--accent-primary, #2563eb)';
  const argsPreview = call.arguments
    ? Object.entries(call.arguments)
        .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
        .slice(0, 2)
        .join(', ')
    : '';
  return (
    <div style={{
      alignSelf: 'flex-start',
      maxWidth: '85%',
      fontSize: '0.75rem',
      color,
      border: `1px solid ${color}`,
      borderRadius: '6px',
      padding: '0.25rem 0.5rem',
      background: 'var(--bg-primary, #fff)',
    }}>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', cursor: 'pointer' }}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span style={{ fontFamily: 'var(--font-mono, ui-monospace, monospace)' }}>
          {call.name}
        </span>
        {call.status === 'running' && <em style={{ opacity: 0.6 }}>running…</em>}
        {call.status !== 'running' && call.result_summary && (
          <span style={{ marginLeft: '0.25rem', opacity: 0.85 }}>
            — {call.result_summary}
          </span>
        )}
      </div>
      {expanded && (
        <div style={{ marginTop: '0.375rem', fontSize: '0.6875rem' }}>
          {argsPreview && (
            <div style={{ color: 'var(--text-muted, #6b7280)' }}>
              args: {argsPreview}
            </div>
          )}
          {call.result_full !== undefined && (
            <pre style={{
              marginTop: '0.25rem',
              maxHeight: '12rem',
              overflowY: 'auto',
              padding: '0.25rem',
              background: 'var(--bg-secondary, #f3f4f6)',
              borderRadius: '4px',
              fontFamily: 'var(--font-mono, ui-monospace, monospace)',
              fontSize: '0.6875rem',
              whiteSpace: 'pre-wrap',
            }}>
              {JSON.stringify(call.result_full, null, 2).slice(0, 2000)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}


function formatProviderHeader(config: AiMaskedConfig | null): string {
  if (!config?.enabled) return 'AI Assistant disabled';
  if (!config.provider_id) return 'No provider configured';
  const labels: Record<string, string> = {
    claude: 'Claude',
    openai: 'OpenAI',
    gemini: 'Gemini',
    openrouter: 'OpenRouter',
    groq: 'Groq',
    local: 'Local LLM',
    custom: 'Custom',
  };
  const provider = labels[config.provider_id] || config.provider_id;
  return config.model ? `${provider} · ${config.model}` : provider;
}
