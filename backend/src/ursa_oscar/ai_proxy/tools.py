"""LLM-facing tool descriptors + in-process executor.

Phase 5 Ticket 1E — Decision 4 in the work order: the AI proxy and the
MCP server expose the SAME analytical surface, but where the MCP server
wraps each tool in an ``@mcp.tool()`` decorator and serves over SSE,
the AI proxy here defines tool descriptors (JSON Schema) for the LLM
and dispatches via in-process httpx calls against the API's own
endpoints. Same underlying implementations (the API endpoints + their
repositories + analytics modules), two presentation layers.

We deliberately do NOT import from ``ursa_oscar_mcp`` here — that
package isn't part of the API container's runtime. The MCP server's
tool wrappers are kept in sync by convention; the descriptions
mirror each other.

Eleven tools. Same names + parameter shapes as the MCP tool surface.
The descriptions tell the LLM "use this when the user asks..." — the
single most important factor in correct tool routing.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Tool descriptors — OpenAI function-calling shape. The Claude adapter
# transforms these into Anthropic's native ``tools=[{name, description,
# input_schema}]`` format at the adapter boundary.
# -------------------------------------------------------------------------


TOOL_DESCRIPTORS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_nightly_summary",
            "description": (
                "Return the nightly summary (AHI, pressure, leak, equipment "
                "settings) for one date or a date range. Use when the user "
                "asks 'how was last night', 'show me my CPAP data for X', "
                "'compare last night to the night before'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": (
                            "Optional end date in YYYY-MM-DD. When set, returns "
                            "a list of summaries for the inclusive range."
                        ),
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ahi_breakdown",
            "description": (
                "Per-event-type AHI decomposition for a single night: central "
                "vs obstructive vs hypopnea vs RERA counts + per-hour indices "
                "+ TECSA-likely heuristic. Use when the user asks 'what kind "
                "of apneas did I have', 'are these central or obstructive'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Night date in YYYY-MM-DD.",
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_nights",
            "description": (
                "List the dates the user has imported CPAP data for. Optional "
                "filter expression (e.g., 'AHI < 5'). Use when the user asks "
                "'what nights do I have data for', 'find me good nights', "
                "'show me my worst nights this month'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_expression": {
                        "type": "string",
                        "description": (
                            "Optional SQL-style filter on summary fields, e.g., "
                            "'AHI < 5' or 'central_ahi > 3'."
                        ),
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD lower bound.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD upper bound.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_periods",
            "description": (
                "Compare metric means / medians / std between two date ranges "
                "(period A vs period B) with absolute + relative deltas and a "
                "direction-aware interpretation. Use for 'compare this week vs "
                "last week', 'how am I trending vs last month'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period_a_start": {"type": "string"},
                    "period_a_end": {"type": "string"},
                    "period_b_start": {"type": "string"},
                    "period_b_end": {"type": "string"},
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of metric names. Defaults to a "
                            "sensible set if omitted. Examples: total_ahi, "
                            "median_pressure, p95_leak."
                        ),
                    },
                },
                "required": [
                    "period_a_start",
                    "period_a_end",
                    "period_b_start",
                    "period_b_end",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_correlation",
            "description": (
                "Pearson correlation between two metrics over a date range, "
                "with optional time lag. Use when the user asks 'is X "
                "correlated with Y', 'does my AHI change when I take "
                "melatonin'. Returns r, p-value, n, and a plain-language "
                "interpretation. Surfaces a sample-size warning when n<30."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_a": {
                        "type": "string",
                        "description": (
                            "Bare nightly_summary column (e.g., 'total_ahi') "
                            "OR 'log_type:filter:field' for manual logs "
                            "(e.g., 'medication:melatonin:taken')."
                        ),
                    },
                    "metric_b": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "lag_days": {
                        "type": "integer",
                        "description": (
                            "Optional: shift metric_b by N days before "
                            "correlating. Default 0."
                        ),
                    },
                },
                "required": ["metric_a", "metric_b", "start_date", "end_date"],
            },
        },
    },
    {
        # Phase 6 Ticket 6.1 Item 2.
        "type": "function",
        "function": {
            "name": "analyze_multivariate_correlation",
            "description": (
                "Partial correlation of each predictor with a target metric, "
                "controlling for the other predictors. Use when the user "
                "asks 'is X really driving Y after accounting for Z' "
                "questions — e.g., 'is doxepin really helping my AHI or "
                "is it the pressure changes?' Pairwise correlation can't "
                "disentangle multiple candidate causes; this can. Returns "
                "per-predictor partial r + bootstrap 95% CI + p-value + "
                "confidence level. REFUSES if n < 15 (returns ok=false "
                "with INSUFFICIENT_DATA). Surfaces the confidence_level "
                "field; the assistant should mention it when relaying "
                "results ('moderate confidence — 47 observations'). "
                "If a predictor's CI spans zero, say the effect isn't "
                "statistically distinguishable from noise."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_metric": {
                        "type": "string",
                        "description": (
                            "Outcome to explain. Same naming as "
                            "analyze_correlation — bare nightly column "
                            "(e.g., 'total_ahi') OR 'log_type:filter:field'."
                        ),
                    },
                    "predictor_metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "2-5 candidate predictors to test simultaneously. "
                            "Same naming as target_metric."
                        ),
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "recompute": {
                        "type": "boolean",
                        "description": (
                            "Optional: bypass the cache and force a fresh "
                            "computation. Default false."
                        ),
                    },
                },
                "required": [
                    "target_metric", "predictor_metrics",
                    "start_date", "end_date",
                ],
            },
        },
    },
    {
        # Phase 6 Ticket 6.3 — provider PDF reports.
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": (
                "Generate a multi-page PDF report for the user's CPAP "
                "data + analytical findings + methodology disclosures. "
                "Three templates: 'full_clinical_report' (8-12 pages, "
                "annual review), 'summary_report' (2-3 pages, routine "
                "follow-up), 'analytical_report' (4-6 pages, focused "
                "on multivariate + lag + predictions). Use when the "
                "user asks for a 'report for my doctor', 'summary for "
                "my appointment', 'PDF I can bring' or similar. "
                "In-app chat returns METADATA only (sections, methods, "
                "page count, confidence); the PDF binary is downloaded "
                "from the Reports page (/reports). After surfacing the "
                "metadata, tell the user to open Reports to download. "
                "Never summarize PDF contents verbatim — the PDF is "
                "authoritative; use other tools (analyze_prediction, "
                "analyze_multivariate_correlation, etc.) for "
                "conversational follow-ups."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "enum": [
                            "full_clinical_report",
                            "summary_report",
                            "analytical_report",
                        ],
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                },
                "required": ["template", "start_date", "end_date"],
            },
        },
    },
    {
        # Phase 6 Ticket 6.2 — predictive modeling + counterfactuals.
        "type": "function",
        "function": {
            "name": "analyze_prediction",
            "description": (
                "Predict a metric tonight + optionally answer 'what if' "
                "counterfactual questions. Use for 'what's my AHI likely "
                "to be tonight?', 'if I take doxepin tonight what does "
                "AHI change to?', 'what if I bump pressure max from 12 "
                "to 14?', 'predict my morning alertness if the room is "
                "darker'. Method: ridge regression with cross-validated "
                "alpha plus four quantile regressors for prediction "
                "intervals. Returns point estimate + 50% and 95% "
                "prediction intervals (NEVER quote the point estimate "
                "without its interval). When counterfactual_inputs is "
                "provided, also returns a counterfactual block with "
                "baseline vs counterfactual prediction + delta. REFUSES "
                "if training set has < 30 nights (stricter than "
                "correlation's n<15 because prediction needs more data "
                "than correlation). Surface confidence_level + cross-"
                "validation R² naturally; mention if R² < 0.4 ('the "
                "model fits the data poorly')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_metric": {
                        "type": "string",
                        "description": (
                            "Outcome to predict. Bare nightly column "
                            "OR 'log_type:filter:field' for manual logs."
                        ),
                    },
                    "predictor_metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "2-6 factors. Same naming as target_metric."
                        ),
                    },
                    "training_start_date": {"type": "string"},
                    "training_end_date": {"type": "string"},
                    "counterfactual_inputs": {
                        "type": "object",
                        "description": (
                            "Optional. Map predictor name -> hypothetical "
                            "value. Predictors not in this dict default "
                            "to their training-window median. If absent "
                            "entirely, only the baseline prediction is "
                            "computed."
                        ),
                    },
                    "recompute": {
                        "type": "boolean",
                        "description": "Bypass cache. Default false.",
                    },
                },
                "required": [
                    "target_metric", "predictor_metrics",
                    "training_start_date", "training_end_date",
                ],
            },
        },
    },
    {
        # Phase 6 Ticket 6.1 Item 3.
        "type": "function",
        "function": {
            "name": "analyze_lag_correlation",
            "description": (
                "Time-shifted Pearson correlation across a lag window "
                "(default -3 to +7 days) with bootstrap 95% CIs at each "
                "lag. Use when the user asks about DELAYED effects: "
                "'how long after I take doxepin does it start working?', "
                "'does last night's alcohol still affect tonight's AHI?', "
                "'when does a pressure change actually take effect?'. "
                "Returns one row per lag plus a peak_lag_days summary. "
                "Negative lags are sanity checks (effect before cause). "
                "CIs that span zero mean no real effect at that lag. "
                "REFUSES if n < 15 (returns ok=false with INSUFFICIENT_DATA)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_a": {
                        "type": "string",
                        "description": (
                            "Hypothesized cause. Same naming as "
                            "analyze_correlation."
                        ),
                    },
                    "metric_b": {
                        "type": "string",
                        "description": "Hypothesized effect.",
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "lag_range_days": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "[lo, hi] lag window. Default [-3, 7]. "
                            "Span <= 60 days."
                        ),
                    },
                    "bootstrap_samples": {
                        "type": "integer",
                        "description": "Default 1000.",
                    },
                    "recompute": {
                        "type": "boolean",
                        "description": "Bypass cache. Default false.",
                    },
                },
                "required": ["metric_a", "metric_b", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trend",
            "description": (
                "Linear-regression trend of one metric over a date range with "
                "R², slope-per-day, projection, and improving/worsening label. "
                "Use for 'what's the trend', 'is my AHI getting better', "
                "'project where I'll be in 30 days'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "projection_days": {
                        "type": "integer",
                        "description": (
                            "Optional forward-projection horizon. Default 7."
                        ),
                    },
                },
                "required": ["metric", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_manual_log_summary",
            "description": (
                "Aggregate the user's manual logs (medications, symptoms, "
                "alertness, sleep environment, notes) over a date range. Use "
                "for 'what did I take last week', 'show me my mood logs', "
                "'how often have I logged anxiety'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Single-date shortcut."},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "log_type": {
                        "type": "string",
                        "enum": ["medication", "symptom", "alertness",
                                 "sleep_environment", "note"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": (
                "Return the user's clinical profile (diagnoses, active "
                "medications, treatment goals, allergies). Use ONCE at the "
                "start of a conversation if you need clinical context, OR "
                "when the user asks 'what's in my profile', 'what meds am "
                "I on'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_distribution_by_hour",
            "description": (
                "Hour-of-night histogram of respiratory events for a single "
                "night. Use when the user asks 'when did my events happen', "
                "'are they clustered early or late'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "event_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional filter: ['ClearAirway', 'Obstructive', "
                            "'Hypopnea', 'Apnea', 'RERA', 'LargeLeak']."
                        ),
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pressure_profile",
            "description": (
                "Pressure statistics + delivered-pressure distribution for a "
                "single night. Median / p95 / p99.5 pressure, EPAP companions, "
                "and a settings comparison. Use for 'what pressure did I "
                "actually run at', 'was my pressure too high'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"date": {"type": "string"}},
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_leak_profile",
            "description": (
                "Leak statistics for a single night: median + p95 + p99.5 "
                "leak, minutes-over-redline, large-leak %, seal-quality "
                "label. Use for 'did my mask leak last night', 'how's my "
                "seal'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"date": {"type": "string"}},
                "required": ["date"],
            },
        },
    },
]


# -------------------------------------------------------------------------
# Executor — runs a tool by name against the in-process API endpoints.
# -------------------------------------------------------------------------


# Maps tool name → (HTTP method, API path template, arg-builder function).
# arg_builder takes the LLM-supplied ``arguments`` dict and returns a tuple
# of ``(query_params, path_format_vars, request_body)``.
def _no_body(args: dict) -> tuple[dict, dict, dict | None]:
    return (dict(args), {}, None)


def _path_only(arg_names: list[str]):
    """Build a function that puts the named args into the URL path
    rather than as query params."""
    def _builder(args: dict) -> tuple[dict, dict, dict | None]:
        path_vars = {n: args.pop(n, None) for n in arg_names}
        return (args, path_vars, None)
    return _builder


def _body_only(args: dict) -> tuple[dict, dict, dict | None]:
    """Builder for POST endpoints — all args land in the JSON body."""
    return ({}, {}, dict(args))


_TOOL_ROUTING: dict[str, dict] = {
    "get_nightly_summary": {
        "method": "GET",
        # Caller passes ``date`` (required) and optional ``end_date``.
        # When end_date is set, hit GET /nights with start+end; otherwise
        # GET /night/{date}.
        "router": "_route_nightly_summary",
    },
    "get_ahi_breakdown": {
        "method": "GET",
        # The MCP tool calls a derived endpoint. For now we route through
        # the analytics surface: GET /events?date=X → derive counts.
        # We'll wrap that into an analytics endpoint in a follow-up if
        # needed; for v1 the LLM-facing path goes through events.
        "router": "_route_ahi_breakdown",
    },
    "list_available_nights": {
        "method": "GET",
        "path": "/api/v1/nights",
        "builder": _no_body,
    },
    "compare_periods": {
        "method": "GET",
        "path": "/api/v1/analytics/compare-periods",
        "builder": _no_body,
    },
    "analyze_correlation": {
        "method": "GET",
        "path": "/api/v1/analytics/correlation",
        "builder": _no_body,
    },
    # Phase 6 Ticket 6.1 Item 2 — multivariate (partial) correlation.
    # POST body shape; arguments forwarded verbatim into the JSON body.
    # The endpoint already returns {ok, data} so execute_tool's _ok()
    # wrap reads as a passthrough.
    "analyze_multivariate_correlation": {
        "method": "POST",
        "path": "/api/v1/analytics/multivariate-correlation",
        "builder": _body_only,
    },
    # Phase 6 Ticket 6.1 Item 3 — time-shifted lag correlation.
    "analyze_lag_correlation": {
        "method": "POST",
        "path": "/api/v1/analytics/lag-correlation",
        "builder": _body_only,
    },
    # Phase 6 Ticket 6.2 — predictive modeling + counterfactuals.
    "analyze_prediction": {
        "method": "POST",
        "path": "/api/v1/analytics/predict",
        "builder": _body_only,
    },
    # Phase 6 Ticket 6.3 — provider PDF reports. The AI proxy's
    # in-app chat path doesn't deliver PDF bytes inline — the LLM
    # surfaces the report metadata + an instruction to use the
    # Reports page. So we route to /preview-metadata (GET, cheap)
    # rather than /generate (POST, expensive WeasyPrint render).
    # The user reads the metadata in chat and clicks through to
    # /reports for the actual download.
    "generate_report": {
        "method": "GET",
        "path": "/api/v1/reports/preview-metadata",
        "builder": _no_body,
    },
    "get_trend": {
        "method": "GET",
        "path": "/api/v1/analytics/trend",
        "builder": _no_body,
    },
    "get_manual_log_summary": {
        "method": "GET",
        "path": "/api/v1/analytics/manual-log-summary",
        "builder": _no_body,
    },
    "get_user_profile": {
        "method": "GET",
        "path": "/api/v1/profile",
        "builder": _no_body,
    },
    "get_event_distribution_by_hour": {
        "method": "GET",
        "router": "_route_event_distribution",
    },
    "get_pressure_profile": {
        "method": "GET",
        "router": "_route_pressure_profile",
    },
    "get_leak_profile": {
        "method": "GET",
        "router": "_route_leak_profile",
    },
}


async def execute_tool(
    tool_name: str,
    arguments: dict,
    api_base_url: str = "http://127.0.0.1:8000",
    auth_header: str | None = None,
) -> dict:
    """Execute a tool by name and return its ``{ok, data, ...}`` envelope.

    The envelope shape mirrors the MCP tool surface so the LLM sees
    consistent responses regardless of which presentation layer it's
    talking to. Failures are caught and turned into
    ``{ok: false, code: ..., error: ...}`` envelopes — never raised —
    so the LLM can decide what to tell the user.

    ``api_base_url`` defaults to localhost-loopback because the AI proxy
    runs inside the API container. The endpoint is overridable for tests.

    ``auth_header`` — 1.1.1 fix. Phase 6.4 added ``_AUTH_REQUIRED`` to
    every API router; this loopback path was missed. The chat endpoint
    now forwards the operator's incoming Authorization header so the
    loopback call carries proper bearer auth. Without it, every tool
    call 401s with "Not authenticated".
    """
    if tool_name not in _TOOL_ROUTING:
        return _err("UNKNOWN_TOOL", f"No tool named '{tool_name}'")

    route = _TOOL_ROUTING[tool_name]
    method = route["method"]

    headers = {"Authorization": auth_header} if auth_header else None

    try:
        async with httpx.AsyncClient(
            base_url=api_base_url, timeout=30.0, headers=headers,
        ) as client:
            # Custom routers cover the cases where the path depends on the
            # arguments themselves (e.g., /night/{date} vs /nights?start=).
            if "router" in route:
                resp = await _CUSTOM_ROUTERS[route["router"]](client, arguments)
            else:
                params, _path_vars, body = route["builder"](dict(arguments))
                path = route["path"]
                if method == "GET":
                    resp = await client.get(path, params=params)
                else:
                    resp = await client.request(method, path, json=body, params=params)
            resp.raise_for_status()
            return _ok(resp.json())
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 404:
            return _err("NOT_FOUND", f"{tool_name}: {e.response.text}")
        if 400 <= status < 500:
            return _err("INVALID_INPUT", f"{tool_name}: {e.response.text}")
        return _err("UPSTREAM_ERROR", f"{tool_name}: {status} {e.response.text}")
    except httpx.RequestError as e:
        return _err("NETWORK_ERROR", f"{tool_name}: {e!s}")
    except Exception as e:
        logger.exception("execute_tool: unexpected error in %s", tool_name)
        return _err("INTERNAL_ERROR", f"{tool_name}: {type(e).__name__}: {e}")


# -------------------------------------------------------------------------
# Custom routers — for tools whose URL shape depends on arguments.
# -------------------------------------------------------------------------


async def _route_nightly_summary(
    client: httpx.AsyncClient, args: dict,
) -> httpx.Response:
    """``get_nightly_summary(date)`` → /night/{date}.
    ``get_nightly_summary(date, end_date)`` → /nights?start=X&end=Y."""
    date = args.get("date")
    end = args.get("end_date")
    if end:
        return await client.get(
            "/api/v1/nights", params={"start": date, "end": end},
        )
    return await client.get(f"/api/v1/night/{date}")


async def _route_ahi_breakdown(
    client: httpx.AsyncClient, args: dict,
) -> httpx.Response:
    """No dedicated /ahi-breakdown endpoint yet — compose from
    /night + /events. Returns the same shape the MCP tool emits so
    the LLM sees consistent envelopes."""
    date = args.get("date")
    night_resp = await client.get(f"/api/v1/night/{date}")
    night_resp.raise_for_status()
    night = night_resp.json()
    events_resp = await client.get("/api/v1/events", params={"date": date})
    events_resp.raise_for_status()
    events = events_resp.json()

    # Per-type counts.
    counts: dict[str, int] = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1

    total_mins = night.get("total_time_minutes") or 0
    hours = total_mins / 60.0 if total_mins else 0.0

    def per_hour(n: int) -> float | None:
        return n / hours if hours > 0 else None

    central_ct = counts.get("ClearAirway", 0)
    obs_ct = counts.get("Obstructive", 0)
    hyp_ct = counts.get("Hypopnea", 0)
    apnea_ct = counts.get("Apnea", 0)
    rera_ct = counts.get("RERA", 0)

    # TECSA-likely heuristic: central count >= 5/hr AND > 50% of all apneas.
    total_apneas = central_ct + obs_ct + apnea_ct
    central_index = per_hour(central_ct) or 0.0
    tecsa_likely = (
        central_index >= 5.0
        and total_apneas > 0
        and (central_ct / total_apneas) > 0.5
    )

    return _FakeResponse(
        status_code=200,
        body={
            "date": date,
            "total_ahi": night.get("total_ahi"),
            "central": {"count": central_ct, "index": per_hour(central_ct)},
            "obstructive": {"count": obs_ct, "index": per_hour(obs_ct)},
            "hypopnea": {"count": hyp_ct, "index": per_hour(hyp_ct)},
            "apnea_unclassified": {"count": apnea_ct, "index": per_hour(apnea_ct)},
            "rera": {"count": rera_ct, "index": per_hour(rera_ct)},
            "interpretation": {
                "tecsa_likely": tecsa_likely,
                "tecsa_reason": (
                    f"Central index {central_index:.1f}/hr; "
                    f"{central_ct}/{total_apneas} apneas are central"
                    if total_apneas > 0 else
                    "No apneas recorded — heuristic not meaningful"
                ),
            },
        },
    )


async def _route_event_distribution(
    client: httpx.AsyncClient, args: dict,
) -> httpx.Response:
    """Bucket events by hour-of-night. Source events from
    /events?date=X&event_type=Y."""
    date = args.get("date")
    event_types = args.get("event_types") or []
    params: dict[str, Any] = {"date": date}
    if event_types:
        params["event_type"] = event_types
    events_resp = await client.get("/api/v1/events", params=params)
    events_resp.raise_for_status()
    events = events_resp.json()

    by_hour: dict[int, dict[str, int]] = {}
    for e in events:
        ts = e["timestamp"]  # naive ISO; parse the hour piece directly
        hour = int(ts[11:13])
        by_hour.setdefault(hour, {})
        by_hour[hour][e["event_type"]] = by_hour[hour].get(e["event_type"], 0) + 1

    hours = [
        {"hour": h, "counts": counts}
        for h, counts in sorted(by_hour.items())
    ]
    return _FakeResponse(
        status_code=200,
        body={"date": date, "hours": hours, "total_events": len(events)},
    )


async def _route_pressure_profile(
    client: httpx.AsyncClient, args: dict,
) -> httpx.Response:
    """Pressure stats from the nightly summary — no need to scan the
    timeseries; the summary already holds median/p95/p99.5."""
    date = args.get("date")
    resp = await client.get(f"/api/v1/night/{date}")
    resp.raise_for_status()
    night = resp.json()
    return _FakeResponse(
        status_code=200,
        body={
            "date": date,
            "median_pressure": night.get("median_pressure"),
            "p95_pressure": night.get("p95_pressure"),
            "p995_pressure": night.get("p995_pressure"),
            "median_epap": night.get("median_epap"),
            "p95_epap": night.get("p95_epap"),
            "p995_epap": night.get("p995_epap"),
            "min_pressure_setting": night.get("min_pressure_setting"),
            "max_pressure_setting": night.get("max_pressure_setting"),
            "epr_level": night.get("epr_level"),
        },
    )


async def _route_leak_profile(
    client: httpx.AsyncClient, args: dict,
) -> httpx.Response:
    """Leak stats from the nightly summary + a seal-quality label
    derived from large_leak_pct."""
    date = args.get("date")
    resp = await client.get(f"/api/v1/night/{date}")
    resp.raise_for_status()
    night = resp.json()

    large_pct = night.get("large_leak_pct") or 0.0
    seal = "good" if large_pct < 1.0 else "marginal" if large_pct < 5.0 else "poor"

    return _FakeResponse(
        status_code=200,
        body={
            "date": date,
            "median_leak": night.get("median_leak"),
            "p95_leak": night.get("p95_leak"),
            "p995_leak": night.get("p995_leak"),
            "minutes_over_redline": night.get("minutes_over_leak_redline"),
            "large_leak_pct": large_pct,
            "mask_type": night.get("mask_type"),
            "interpretation": {
                "seal_quality": seal,
                "summary": (
                    f"Seal {seal}; {large_pct:.2f}% of recording over the leak redline"
                ),
            },
        },
    )


_CUSTOM_ROUTERS = {
    "_route_nightly_summary": _route_nightly_summary,
    "_route_ahi_breakdown": _route_ahi_breakdown,
    "_route_event_distribution": _route_event_distribution,
    "_route_pressure_profile": _route_pressure_profile,
    "_route_leak_profile": _route_leak_profile,
}


# -------------------------------------------------------------------------
# Envelope helpers + fake httpx.Response (for custom-router responses we
# build in-process without an upstream HTTP roundtrip).
# -------------------------------------------------------------------------


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _err(code: str, message: str) -> dict:
    return {"ok": False, "code": code, "error": message}


class _FakeResponse:
    """httpx.Response stand-in for custom-router composed responses.
    We only need raise_for_status() + json() — the executor doesn't
    use anything else."""
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} composed response",
                request=None,  # type: ignore
                response=self,  # type: ignore
            )

    def json(self) -> Any:
        return self._body

    @property
    def text(self) -> str:
        return str(self._body)
