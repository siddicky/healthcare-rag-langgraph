from __future__ import annotations

import math
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter


class CalibrationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class CalibrationLane(StrEnum):
    QUERY = "query"
    SAFETY = "safety"


class CalibrationStatus(StrEnum):
    PASS = "PASS"
    INCONCLUSIVE = "INCONCLUSIVE"


class ChitchatFixture(CalibrationModel):
    id: str = Field(min_length=1)
    intent: Literal["greeting", "thanks", "goodbye", "capability"]
    question: str = Field(min_length=1)
    response: str = Field(min_length=1)
    acceptable: StrictBool


class SafetyDriftFixture(CalibrationModel):
    id: str = Field(min_length=1)
    turns: tuple[str, ...] = Field(min_length=2)
    expected_drift: StrictBool


class RoutingFixtures(CalibrationModel):
    chitchat: tuple[ChitchatFixture, ...]
    safety_drift: tuple[SafetyDriftFixture, ...]


class CalibrationScore(CalibrationModel):
    fixture_id: str = Field(min_length=1)
    lane: CalibrationLane
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class LaneCalibration(CalibrationModel):
    lane: CalibrationLane
    status: CalibrationStatus
    fixture_count: int
    failure_count: int
    failures: tuple[str, ...]


class CalibrationSummary(CalibrationModel):
    query: LaneCalibration
    safety: LaneCalibration


class CalibrationCounts(CalibrationModel):
    chitchat_acceptable: int
    chitchat_unacceptable: int
    safety_drift: int
    safety_safe: int


class CalibrationThresholds(CalibrationModel):
    chitchat_acceptable_min: float
    chitchat_unacceptable_max: float
    safety_drift: Literal["exact_binary"] = "exact_binary"


class RoutingCalibrationRun(CalibrationModel):
    candidate_output_used: Literal[False] = False
    counts: CalibrationCounts
    thresholds: CalibrationThresholds
    query: LaneCalibration
    safety: LaneCalibration


CALIBRATION_PATH = Path(__file__).with_name("routing_evaluator_calibration.json")
CHITCHAT_ACCEPTABLE_MIN = 0.8
CHITCHAT_UNACCEPTABLE_MAX = 0.4


def load_routing_fixtures(path: Path = CALIBRATION_PATH) -> RoutingFixtures:
    return TypeAdapter(RoutingFixtures).validate_json(path.read_bytes())


def _lane_result(
    lane: CalibrationLane,
    expected_ids: tuple[str, ...],
    failures: list[str],
) -> LaneCalibration:
    return LaneCalibration(
        lane=lane,
        status=CalibrationStatus.INCONCLUSIVE if failures else CalibrationStatus.PASS,
        fixture_count=len(expected_ids),
        failure_count=len(failures),
        failures=tuple(failures),
    )


def summarize_calibration(
    fixtures: RoutingFixtures,
    scores: tuple[CalibrationScore, ...],
) -> CalibrationSummary:
    by_key = {(score.lane, score.fixture_id): score for score in scores}
    query_failures: list[str] = []
    safety_failures: list[str] = []
    query_ids = tuple(case.id for case in fixtures.chitchat)
    safety_ids = tuple(case.id for case in fixtures.safety_drift)
    duplicate_counts = Counter((score.lane, score.fixture_id) for score in scores)
    for (lane, fixture_id), count in duplicate_counts.items():
        if count < 2:
            continue
        failure = f"{fixture_id}: duplicate score count={count}"
        if lane is CalibrationLane.QUERY:
            query_failures.append(failure)
        else:
            safety_failures.append(failure)
    for case in fixtures.chitchat:
        score = by_key.get((CalibrationLane.QUERY, case.id))
        if score is None:
            query_failures.append(f"{case.id}: missing query score")
            continue
        passed = (
            score.score >= CHITCHAT_ACCEPTABLE_MIN
            if case.acceptable
            else score.score <= CHITCHAT_UNACCEPTABLE_MAX
        )
        if not passed:
            query_failures.append(f"{case.id}: score={score.score}")
    for case in fixtures.safety_drift:
        score = by_key.get((CalibrationLane.SAFETY, case.id))
        if score is None:
            safety_failures.append(f"{case.id}: missing safety score")
            continue
        if score.score != float(case.expected_drift):
            safety_failures.append(f"{case.id}: score={score.score}")
    expected_by_lane = {
        CalibrationLane.QUERY: set(query_ids),
        CalibrationLane.SAFETY: set(safety_ids),
    }
    for score in scores:
        if (
            score.fixture_id not in expected_by_lane[score.lane]
            or not math.isfinite(score.score)
        ):
            if score.lane is CalibrationLane.QUERY:
                query_failures.append(f"{score.fixture_id}: unknown/non-finite")
            else:
                safety_failures.append(f"{score.fixture_id}: unknown/non-finite")
    return CalibrationSummary(
        query=_lane_result(CalibrationLane.QUERY, query_ids, query_failures),
        safety=_lane_result(CalibrationLane.SAFETY, safety_ids, safety_failures),
    )
