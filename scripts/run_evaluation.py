from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import httpx


@dataclass(frozen=True)
class EvaluationSummary:
    phase: str
    cases: int
    success_rate: float
    policy_version_accuracy: float
    answer_accuracy: float
    latency_compliance: float
    average_latency_ms: float
    p95_latency_ms: float
    total_tokens: int


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile_value)
    return ordered[index]


def summarize(phase: str, rows: list[dict[str, Any]]) -> EvaluationSummary:
    count = len(rows)
    successful = [row for row in rows if row["http_status"] == 200]
    return EvaluationSummary(
        phase=phase,
        cases=count,
        success_rate=sum(row["http_status"] == 200 for row in rows) / count,
        policy_version_accuracy=sum(
            row["policy_version_correct"] for row in rows
        )
        / count,
        answer_accuracy=sum(row["answer_correct"] for row in rows) / count,
        latency_compliance=sum(row["latency_compliant"] for row in rows) / count,
        average_latency_ms=round(
            mean(row["elapsed_ms"] for row in rows), 2
        ),
        p95_latency_ms=round(
            percentile([row["elapsed_ms"] for row in rows], 0.95), 2
        ),
        total_tokens=sum(
            row.get("input_tokens", 0) + row.get("output_tokens", 0)
            for row in successful
        ),
    )


def compare_reports(
    before: EvaluationSummary, after: EvaluationSummary
) -> dict[str, float]:
    return {
        "success_rate_delta": round(
            after.success_rate - before.success_rate, 4
        ),
        "policy_version_accuracy_delta": round(
            after.policy_version_accuracy - before.policy_version_accuracy, 4
        ),
        "answer_accuracy_delta": round(
            after.answer_accuracy - before.answer_accuracy, 4
        ),
        "latency_compliance_delta": round(
            after.latency_compliance - before.latency_compliance, 4
        ),
        "average_latency_ms_delta": round(
            after.average_latency_ms - before.average_latency_ms, 2
        ),
        "p95_latency_ms_delta": round(
            after.p95_latency_ms - before.p95_latency_ms, 2
        ),
        "total_tokens_delta": after.total_tokens - before.total_tokens,
    }


def load_dataset(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def run_phase(
    client: httpx.AsyncClient,
    cases: list[dict[str, Any]],
    *,
    phase: str,
    mode: str,
) -> tuple[EvaluationSummary, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        response = await client.post(
            "/api/ask",
            json={
                "question": case["query"],
                "request_id": case["request_id"],
                "mode": mode,
                "scenario": case[f"{phase}_scenario"],
            },
        )
        payload = response.json()
        source_versions = [
            source["version"] for source in payload.get("sources", [])
        ]
        answer = payload.get("answer") or ""
        rows.append(
            {
                "id": case["id"],
                "phase": phase,
                "scenario": case[f"{phase}_scenario"],
                "http_status": response.status_code,
                "policy_version_correct": (
                    case["expected_policy_version"] in source_versions
                ),
                "answer_correct": (
                    case["expected_answer_contains"] in answer
                ),
                "latency_compliant": (
                    payload["elapsed_ms"] <= case["max_latency_ms"]
                ),
                "elapsed_ms": payload["elapsed_ms"],
                "input_tokens": payload.get("input_tokens", 0),
                "output_tokens": payload.get("output_tokens", 0),
                "conversation_id": payload["conversation_id"],
                "response_id": payload["response_id"],
                "trace_id": payload["trace_id"],
                "error": payload.get("error"),
            }
        )
    return summarize(phase, rows), rows


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_dataset(args.dataset)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout
    ) as client:
        before, before_rows = await run_phase(
            client, cases, phase="before", mode=args.mode
        )
        after, after_rows = await run_phase(
            client, cases, phase="after", mode=args.mode
        )
    return {
        "before": asdict(before),
        "after": asdict(after),
        "comparison": compare_reports(before, after),
        "results": before_rows + after_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic before/after policy-agent evaluations."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/evaluation/policy_eval.jsonl"),
    )
    parser.add_argument("--mode", choices=["simulation", "live"], default="simulation")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/evaluation-comparison.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(evaluate(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
    print(f"Full report: {args.output}")


if __name__ == "__main__":
    main()
