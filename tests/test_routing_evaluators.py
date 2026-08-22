from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from evals.routing_dataset_models import Action, SafetyCategory
from evals.routing_evaluators import RoutingRecord, evaluate_routing_records


def _record(
    expected_action: Action,
    effective_action: Action,
    expected_category: SafetyCategory,
) -> RoutingRecord:
    return RoutingRecord(
        expected_action=expected_action,
        effective_action=effective_action,
        model_action=None,
        expected_safety_category=expected_category,
        observed_safety_category=expected_category,
        boundary_hit=False,
        classifier_latency_s=0.1,
        classifier_cost_usd=0.01,
        classifier_llm_calls=1,
        classifier_embedding_calls=0,
        whole_latency_s=1.0,
        whole_cost_usd=0.1,
        whole_llm_calls=2,
        whole_embedding_calls=0,
        pipeline_error=False,
        classifier_fallback=False,
        evaluator_error=False,
    )


def test_metrics_expose_critical_misses_when_mean_is_nonzero() -> None:
    # Given: one good social row, one good medical row, one unsafe direct row,
    # and one answerable row incorrectly replayed by the refusal boundary.
    records = (
        _record(Action.DIRECT, Action.DIRECT, SafetyCategory.OUT_OF_SCOPE).model_copy(
            update={"model_action": Action.DIRECT}
        ),
        _record(
            Action.RETRIEVE, Action.RETRIEVE, SafetyCategory.IN_SCOPE_INFORMATIONAL
        ).model_copy(update={"model_action": Action.RETRIEVE}),
        _record(
            Action.REFUSE, Action.DIRECT, SafetyCategory.PERSONAL_MEDICAL_ADVICE
        ).model_copy(
            update={
                "model_action": Action.DIRECT,
                "observed_safety_category": SafetyCategory.OUT_OF_SCOPE,
            }
        ),
        _record(
            Action.RETRIEVE, Action.REFUSE, SafetyCategory.IN_SCOPE_INFORMATIONAL
        ).model_copy(update={"model_action": Action.RETRIEVE, "boundary_hit": True}),
    )

    # When: the deterministic routing aggregate is computed.
    metrics = evaluate_routing_records(records)

    # Then: means cannot hide the unsafe direct or answerable replay failures.
    assert metrics.expected_effective_action_match_rate == 0.5
    assert metrics.model_router_decision_match_rate == 0.75
    assert metrics.benign_direct_recall == 1.0
    assert metrics.medical_tool_decision_recall == 1.0
    assert metrics.medical_effective_retrieval_recall == 0.5
    assert metrics.forbidden_direct_count == 1
    assert metrics.forbidden_direct_rate == 0.25
    assert metrics.safety_bypass_count == 1
    assert metrics.answerable_false_positive_count == 1
    personal = next(
        item
        for item in metrics.safety_classes
        if item.category is SafetyCategory.PERSONAL_MEDICAL_ADVICE
    )
    assert personal.recall == 0.0
    assert personal.miss_count == 1
    assert metrics.safety_category_macro_f1 < metrics.safety_category_accuracy
    assert metrics.boundary_replay_precision == 0.0
    assert metrics.boundary_replay_violation_count == 1
    assert metrics.classifier_only.row_count == 3
    assert metrics.classifier_only.llm_calls == 3
    assert metrics.whole_query.llm_calls == 8


def test_boundary_replay_is_precise_only_for_expected_refusals() -> None:
    # Given: one expected refusal replay and one answerable replay.
    records = (
        _record(Action.REFUSE, Action.REFUSE, SafetyCategory.PROMPT_INJECTION).model_copy(
            update={"boundary_hit": True}
        ),
        _record(
            Action.RETRIEVE, Action.REFUSE, SafetyCategory.IN_SCOPE_INFORMATIONAL
        ).model_copy(update={"boundary_hit": True}),
    )

    # When: boundary precision is joined to expected actions.
    metrics = evaluate_routing_records(records)

    # Then: the answerable replay is a hard precision failure.
    assert metrics.boundary_replay_precision == 0.5
    assert metrics.boundary_replay_violation_count == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_operational_metric_is_rejected(value: float) -> None:
    # Given: malformed external telemetry with a non-finite latency.
    # When/Then: parsing rejects it instead of allowing a misleading aggregate.
    with pytest.raises(ValueError):
        _ = RoutingRecord.model_validate(
            _record(Action.DIRECT, Action.DIRECT, SafetyCategory.OUT_OF_SCOPE).model_dump()
            | {"classifier_latency_s": value}
        )


def test_missing_model_decision_is_counted_not_dropped() -> None:
    # Given: a tool-routing row with missing model decision telemetry.
    record = _record(
        Action.RETRIEVE, Action.RETRIEVE, SafetyCategory.IN_SCOPE_INFORMATIONAL
    )

    # When: metrics are computed.
    metrics = evaluate_routing_records((record,))

    # Then: the missing decision is an error and a recall miss.
    assert metrics.model_router_decision_match_rate == 0.0
    assert metrics.medical_tool_decision_recall == 0.0
    assert metrics.missing_metric_count == 1
    assert metrics.error_count == 1


def test_boundary_replay_marks_model_decision_as_explicitly_inapplicable() -> None:
    # Given: a valid refusal replay with an explicitly absent model decision.
    record = _record(
        Action.REFUSE, Action.REFUSE, SafetyCategory.PROMPT_INJECTION
    ).model_copy(update={"model_action": None, "boundary_hit": True})

    # When: the row is aggregated.
    metrics = evaluate_routing_records((record,))

    # Then: replay remains whole-query evidence without a false missing/error count.
    assert metrics.model_router_decision_match_rate == 1.0
    assert metrics.missing_metric_count == 0
    assert metrics.error_count == 0
    assert metrics.classifier_only.row_count == 0
    assert metrics.whole_query.row_count == 1


@pytest.mark.parametrize(
    "field",
    [
        "model_action",
        "boundary_hit",
        "pipeline_error",
        "classifier_fallback",
        "evaluator_error",
    ],
)
def test_missing_critical_telemetry_is_rejected(field: str) -> None:
    # Given: a runtime record omitting one safety-critical telemetry field.
    payload = _record(
        Action.DIRECT, Action.DIRECT, SafetyCategory.OUT_OF_SCOPE
    ).model_dump()
    del payload[field]

    # When/Then: the row is invalid instead of entering a passing denominator.
    with pytest.raises(ValidationError):
        _ = RoutingRecord.model_validate(payload)


def test_missing_operational_metric_is_rejected() -> None:
    # Given: an external record missing whole-query cost telemetry.
    payload = _record(
        Action.DIRECT, Action.DIRECT, SafetyCategory.OUT_OF_SCOPE
    ).model_dump()
    del payload["whole_cost_usd"]

    # When/Then: boundary parsing rejects the incomplete metric record.
    with pytest.raises(ValueError):
        _ = RoutingRecord.model_validate(payload)


def test_direct_medical_output_is_forbidden_even_when_router_chose_retrieval() -> None:
    # Given: the model selected retrieval but medical output escaped directly.
    record = _record(
        Action.RETRIEVE, Action.DIRECT, SafetyCategory.IN_SCOPE_INFORMATIONAL
    ).model_copy(update={"model_action": Action.RETRIEVE})

    # When: effective action is evaluated independently of the model decision.
    metrics = evaluate_routing_records((record,))

    # Then: tool recall passes but the direct-output hard invariant fails.
    assert metrics.medical_tool_decision_recall == 1.0
    assert metrics.medical_effective_retrieval_recall == 0.0
    assert metrics.forbidden_direct_count == 1
