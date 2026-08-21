from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.routing_evaluators import (
    RoutingMetrics,
    RoutingRecord,
    evaluate_routing_records,
)
from evals.routing_provenance import RoutingProvenance
from evals.routing_report_io import ReportError, ReportPair, publish_report_pair

ROUTING_RESULTS_DIR: Final = Path(__file__).parent / "results"


class ReportModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class StageUsage(ReportModel):
    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0, allow_inf_nan=False)


class RoutingReportRow(ReportModel):
    row_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    lane: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    split: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    routing: RoutingRecord


class RoutingAggregates(ReportModel):
    overall: RoutingMetrics
    by_lane: dict[str, RoutingMetrics]
    by_category: dict[str, RoutingMetrics]
    by_split: dict[str, RoutingMetrics]


class RoutingReportPayload(ReportModel):
    provenance: RoutingProvenance
    aggregate: RoutingAggregates
    stage_usage: dict[str, StageUsage] = Field(min_length=1)
    rows: tuple[RoutingReportRow, ...]


class RoutingReportRequest(ReportModel):
    provenance: RoutingProvenance
    rows: tuple[RoutingReportRow, ...] = Field(min_length=1)
    stage_usage: dict[str, StageUsage]
    output_dir: Path = ROUTING_RESULTS_DIR

    @field_validator("rows")
    @classmethod
    def canonicalize_rows(
        cls, rows: tuple[RoutingReportRow, ...]
    ) -> tuple[RoutingReportRow, ...]:
        return tuple(sorted(rows, key=lambda row: row.row_id))

    @model_validator(mode="after")
    def require_manifest_row_binding(self) -> RoutingReportRequest:
        report_ids = tuple(sorted(row.row_id for row in self.rows))
        manifest_ids = tuple(sorted(self.provenance.rows.local_row_ids))
        if len(set(report_ids)) != len(report_ids) or report_ids != manifest_ids:
            msg = "report row IDs must exactly match the provenance manifest"
            raise ValueError(msg)
        if any(not name.replace("_", "").isalnum() for name in self.stage_usage):
            msg = "stage usage names must contain only letters, digits, and underscores"
            raise ValueError(msg)
        return self


def _aggregate(rows: Sequence[RoutingReportRow]) -> RoutingAggregates:
    groups: dict[str, dict[str, list[RoutingRecord]]] = {
        "lane": defaultdict(list),
        "category": defaultdict(list),
        "split": defaultdict(list),
    }
    records: list[RoutingRecord] = []
    for row in rows:
        records.append(row.routing)
        groups["lane"][row.lane].append(row.routing)
        groups["category"][row.category].append(row.routing)
        groups["split"][row.split].append(row.routing)
    return RoutingAggregates(
        overall=evaluate_routing_records(records),
        by_lane={
            key: evaluate_routing_records(value)
            for key, value in sorted(groups["lane"].items())
        },
        by_category={
            key: evaluate_routing_records(value)
            for key, value in sorted(groups["category"].items())
        },
        by_split={
            key: evaluate_routing_records(value)
            for key, value in sorted(groups["split"].items())
        },
    )


def _metric_table(groups: Mapping[str, RoutingMetrics]) -> list[str]:
    lines = [
        "| group | rows | action accuracy | classifier p50 (s) | classifier cost | whole p50 (s) | whole cost |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in groups.items():
        classifier = metrics.classifier_only
        whole = metrics.whole_query
        row = (
            f"| {name} | {metrics.row_count} "
            f"| {metrics.expected_effective_action_match_rate:.3f} "
            f"| {classifier.latency_p50_s or 0:.3f} "
            f"| ${classifier.cost_total_usd:.4f} "
            f"| {whole.latency_p50_s or 0:.3f} "
            f"| ${whole.cost_total_usd:.4f} |"
        )
        lines.append(row)
    return lines


def _markdown(request: RoutingReportRequest, aggregates: RoutingAggregates) -> str:
    provenance = request.provenance
    overall = aggregates.overall
    lines = [
        f"# Routing eval report — `{provenance.experiment_name}`",
        "",
        f"- Experiment: {provenance.experiment_url}",
        f"- Git: `{provenance.git_sha}` (`git_dirty=false`)",
        f"- Arms: `{provenance.arm_env.model_dump_json()}`",
        f"- Rows: {provenance.rows.local_row_count} local / {provenance.rows.langsmith_row_count} LangSmith",
        "",
        "## Operational metrics",
        "",
        "| scope | rows | p50 latency (s) | calls (LLM / embedding) | cost |",
        "|---|---:|---:|---:|---:|",
        f"| Classifier-only | {overall.classifier_only.row_count} | {overall.classifier_only.latency_p50_s or 0:.3f} | {overall.classifier_only.llm_calls} / {overall.classifier_only.embedding_calls} | ${overall.classifier_only.cost_total_usd:.4f} |",
        f"| Whole-query | {overall.whole_query.row_count} | {overall.whole_query.latency_p50_s or 0:.3f} | {overall.whole_query.llm_calls} / {overall.whole_query.embedding_calls} | ${overall.whole_query.cost_total_usd:.4f} |",
        "",
    ]
    for title, groups in (
        ("lane", aggregates.by_lane),
        ("category", aggregates.by_category),
        ("split", aggregates.by_split),
    ):
        lines.extend((f"## By {title}", "", *_metric_table(groups), ""))
    return "\n".join(lines)


def build_routing_report_pair(request: RoutingReportRequest) -> ReportPair:
    rows = tuple(sorted(request.rows, key=lambda row: row.row_id))
    canonical = request.model_copy(update={"rows": rows})
    try:
        aggregates = _aggregate(rows)
    except OverflowError as exc:
        raise ReportError("aggregate", exc) from exc
    payload = RoutingReportPayload(
        provenance=canonical.provenance,
        aggregate=aggregates,
        stage_usage=dict(sorted(canonical.stage_usage.items())),
        rows=rows,
    )
    try:
        serialized = json.dumps(
            payload.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except ValueError as exc:
        raise ReportError("serialization", exc) from exc
    stem = canonical.provenance.experiment_name
    pair = ReportPair(
        json_path=canonical.output_dir / f"{stem}.json",
        json_content=serialized + "\n",
        markdown_path=canonical.output_dir / f"{stem}.md",
        markdown_content=_markdown(canonical, aggregates),
    )
    return pair


def write_routing_report(request: RoutingReportRequest) -> tuple[Path, Path]:
    return publish_report_pair(build_routing_report_pair(request))
