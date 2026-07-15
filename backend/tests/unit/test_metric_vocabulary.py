"""Metric-vocabulary drift guard (1.1.15).

The bug this file exists to prevent, in full:

  metric_resolver.py held the real list of 25 valid nightly metrics.
  ai_proxy/tools.py declared every ``metric`` parameter as a bare
  ``{"type": "string"}`` with no enumeration. Two independently
  hand-maintained sources of truth that nothing kept in sync. Asked for an
  AHI trend, the model guessed the word every clinician uses -- "ahi" --
  the resolver rejected it (the real column is "total_ahi"), and the turn
  burned an entire extra tool round recovering. On a local reasoning model
  that is ~20 seconds of wall-clock spent on a guess the schema should
  never have permitted.

The fix is structural: the schema text is DERIVED from the resolver at
import time. These tests assert the derivation actually holds, so the two
can never drift apart again -- including when someone adds column #26 and
forgets this file exists.

If one of these fails, do NOT hand-edit the expected string. Fix the
derivation in tools._build_metric_vocabulary(), or add the new metric to
metric_resolver._NIGHTLY_NUMERIC_COLUMNS and let it flow.
"""
from __future__ import annotations

import json

import pytest

from ursa_oscar.ai_proxy.tools import METRIC_VOCABULARY, TOOL_DESCRIPTORS
from ursa_oscar.analytics.metric_resolver import (
    UnknownMetricError,
    known_log_types,
    known_metric_aliases,
    known_nightly_metrics,
    parse_metric_name,
)


# Every tool param that accepts a metric name. If a new tool takes one and
# isn't listed here, test_every_metric_param_is_documented will fail --
# which is the point.
_PRIMARY_METRIC_PARAMS = [
    ("get_trend", "metric"),
    ("compare_periods", "metrics"),
    ("analyze_correlation", "metric_a"),
    ("analyze_lag_correlation", "metric_a"),
    ("analyze_multivariate_correlation", "target_metric"),
    ("analyze_prediction", "target_metric"),
]

_SECONDARY_METRIC_PARAMS = [
    ("analyze_correlation", "metric_b"),
    ("analyze_lag_correlation", "metric_b"),
    ("analyze_multivariate_correlation", "predictor_metrics"),
    ("analyze_prediction", "predictor_metrics"),
]


def _param(tool_name: str, param_name: str) -> dict:
    for d in TOOL_DESCRIPTORS:
        fn = d["function"]
        if fn["name"] == tool_name:
            props = fn["parameters"]["properties"]
            assert param_name in props, (
                f"{tool_name} has no param '{param_name}' -- did the schema "
                f"change? Update _PRIMARY/_SECONDARY_METRIC_PARAMS."
            )
            return props[param_name]
    raise AssertionError(f"tool '{tool_name}' not found in TOOL_DESCRIPTORS")


def test_vocabulary_lists_every_known_nightly_metric():
    """The derived vocabulary must name ALL 25 metrics. Add column #26 to
    the resolver and this passes automatically; hand-type the list
    somewhere and it rots."""
    for metric in known_nightly_metrics():
        assert metric in METRIC_VOCABULARY, (
            f"Metric '{metric}' is valid per the resolver but absent from the "
            f"tool-schema vocabulary. The model can't use what it isn't told "
            f"about -- that is the 1.1.15 bug."
        )


def test_vocabulary_lists_every_known_log_type():
    for log_type in known_log_types():
        assert log_type in METRIC_VOCABULARY


def test_vocabulary_names_the_canonical_metric_for_each_alias():
    """The alias exists because the model guesses the natural word. The
    vocabulary must still steer it to the canonical name so the alias is a
    safety net, not the primary path."""
    for alias, canonical in known_metric_aliases().items():
        assert canonical in METRIC_VOCABULARY
        # And it should explicitly call out the wrong-but-tempting form.
        assert f"not '{alias}'" in METRIC_VOCABULARY


def test_every_primary_metric_param_carries_the_full_vocabulary():
    """The regression that started it all: get_trend.metric was
    {"type": "string"} with no description at all."""
    for tool_name, param_name in _PRIMARY_METRIC_PARAMS:
        p = _param(tool_name, param_name)
        desc = p.get("description", "")
        assert desc, f"{tool_name}.{param_name} has NO description"
        assert METRIC_VOCABULARY in desc, (
            f"{tool_name}.{param_name} does not carry the derived vocabulary. "
            f"A model calling it has to guess the metric name."
        )


def test_every_secondary_metric_param_points_at_its_primary():
    """Secondary params don't repeat the ~175-token vocabulary (both params
    of a tool always load together, so once is enough) -- but they must at
    least tell the model where to look."""
    for tool_name, param_name in _SECONDARY_METRIC_PARAMS:
        p = _param(tool_name, param_name)
        desc = p.get("description", "")
        assert desc, f"{tool_name}.{param_name} has NO description"
        assert "vocabulary as" in desc, (
            f"{tool_name}.{param_name} should reference its primary param's "
            f"vocabulary (e.g. 'Same vocabulary as metric_a')."
        )
        # Guard the token budget: don't let someone paste the full list in.
        assert METRIC_VOCABULARY not in desc, (
            f"{tool_name}.{param_name} repeats the full vocabulary; that's "
            f"~175 wasted tokens since the primary param already carries it."
        )


def test_every_metric_param_is_documented():
    """Catch a NEW tool that takes a metric-ish param without being added to
    the lists above. Heuristic on the param name, deliberately broad."""
    known = {(t, p) for t, p in _PRIMARY_METRIC_PARAMS + _SECONDARY_METRIC_PARAMS}
    for d in TOOL_DESCRIPTORS:
        fn = d["function"]
        for param_name in fn["parameters"].get("properties", {}):
            if "metric" not in param_name:
                continue
            assert (fn["name"], param_name) in known, (
                f"{fn['name']}.{param_name} looks like a metric param but "
                f"isn't covered by this drift test. Add it to "
                f"_PRIMARY_METRIC_PARAMS or _SECONDARY_METRIC_PARAMS so its "
                f"vocabulary is guarded."
            )


def test_every_metric_named_in_a_tool_schema_actually_resolves():
    """Any concrete metric name used as an EXAMPLE in any tool schema must
    be one the resolver accepts. Stops a plausible-looking example (like
    'ahi', or a since-renamed column) from teaching the model to make a
    call that 400s."""
    blob = json.dumps(TOOL_DESCRIPTORS)
    for candidate in known_nightly_metrics():
        if candidate in blob:
            parse_metric_name(candidate)  # must not raise

    # The examples the vocabulary itself offers must parse.
    for example in [
        "medication:melatonin:dose",
        "symptom:headache:severity",
        "alertness:morning:score",
    ]:
        assert example in METRIC_VOCABULARY
        parse_metric_name(example)  # must not raise


def test_alias_resolves_the_word_the_model_actually_guessed():
    """The live failure: Gemma called get_trend(metric='ahi')."""
    assert parse_metric_name("ahi") == ("total_ahi", None, None)
    assert parse_metric_name("AHI") == ("total_ahi", None, None)
    assert parse_metric_name("  Ahi ") == ("total_ahi", None, None)
    # Canonical names still work unchanged.
    assert parse_metric_name("total_ahi") == ("total_ahi", None, None)


def test_aliases_stay_unambiguous():
    """Guard the judgment call: only add an alias when the word has exactly
    one sane meaning. 'leak'/'pressure' are ambiguous (median vs p95) and
    silently choosing one for the caller would be a data-integrity bug."""
    for ambiguous in ["leak", "pressure", "epap"]:
        assert ambiguous not in known_metric_aliases(), (
            f"'{ambiguous}' is ambiguous (median vs p95 vs p995). Aliasing it "
            f"would silently pick a metric the user didn't ask for."
        )
        with pytest.raises(UnknownMetricError):
            parse_metric_name(ambiguous)


def test_unknown_metric_still_raises_with_the_valid_list():
    """The error message is the model's recovery path when it does guess
    wrong -- it must keep naming the valid options."""
    with pytest.raises(UnknownMetricError) as exc:
        parse_metric_name("definitely_not_a_metric")
    msg = str(exc.value)
    assert "total_ahi" in msg and "central_ahi" in msg
