import json
import re
from pathlib import Path

from scripts.start_tool_aware_eval import build_testing_criteria
import json
from pathlib import Path

from scripts.run_evaluation import EvaluationSummary, compare_reports


ROOT = Path(__file__).resolve().parents[1]


def test_tool_aware_eval_maps_definitions_from_input_items() -> None:
    criteria = build_testing_criteria("gpt-5.4-mini")
    tool_criterion = next(
        item for item in criteria if item["name"] == "tool_call_accuracy"
    )

    assert tool_criterion["data_mapping"] == {
        "query": "{{item.query}}",
        "response": "{{sample.output_items}}",
        "tool_calls": "{{sample.tool_calls}}",
        "tool_definitions": "{{item.tool_definitions}}",
    }


def test_hosted_eval_items_include_tool_contract() -> None:
    path = ROOT / "data" / "evaluation" / "policy_agent_hosted_eval.jsonl"
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(cases) == 4
    for case in cases:
        assert "Scenario: healthy" in case["query"]
        assert re.search(r"Request ID: REQ-\d{4}-\d{4}", case["query"])
        tools = {tool["name"]: tool for tool in case["tool_definitions"]}
        assert set(tools) == {"retrieve_policy", "get_request_status"}
        assert tools["retrieve_policy"]["parameters"]["required"] == [
            "question"
        ]
        assert tools["get_request_status"]["parameters"]["required"] == [
            "request_id"
        ]


def test_before_after_comparison_reports_improvement():
    before = EvaluationSummary(
        phase="before",
        cases=4,
        success_rate=0.75,
        policy_version_accuracy=0.75,
        answer_accuracy=0.5,
        latency_compliance=0.5,
        average_latency_ms=900,
        p95_latency_ms=1400,
        total_tokens=300,
    )
    after = EvaluationSummary(
        phase="after",
        cases=4,
        success_rate=1,
        policy_version_accuracy=1,
        answer_accuracy=1,
        latency_compliance=1,
        average_latency_ms=80,
        p95_latency_ms=120,
        total_tokens=320,
    )

    comparison = compare_reports(before, after)

    assert comparison["success_rate_delta"] == 0.25
    assert comparison["policy_version_accuracy_delta"] == 0.25
    assert comparison["answer_accuracy_delta"] == 0.5
    assert comparison["p95_latency_ms_delta"] == -1280


def test_operations_evaluation_requires_evidence_and_approval():
    dataset = Path("data/evaluation/operations_investigation_eval.jsonl")
    rows = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert {row["scenario"] for row in rows} == {
        "stale_policy",
        "slow_tool",
        "tool_failure",
    }
    assert all(row["expected_evidence_signals"] for row in rows)
    assert all(row["approval_required"] is True for row in rows)
