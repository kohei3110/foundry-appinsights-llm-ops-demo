from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download item-level Microsoft Foundry evaluation results."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--eval-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _result_counts(run: Any) -> dict[str, int]:
    counts = run.result_counts
    return {
        "total": counts.total,
        "passed": counts.passed,
        "failed": counts.failed,
        "errored": counts.errored,
    }


def _criteria_results(run: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": result.testing_criteria,
            "passed": result.passed,
            "failed": result.failed,
        }
        for result in run.per_testing_criteria_results or []
    ]


def main() -> None:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    args = parse_args()
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
    )
    try:
        client = project_client.get_openai_client()
        run = client.evals.runs.retrieve(
            eval_id=args.eval_id,
            run_id=args.run_id,
        )
        output_items = [
            item.model_dump(mode="json")
            for item in client.evals.runs.output_items.list(
                eval_id=args.eval_id,
                run_id=args.run_id,
            )
        ]
    finally:
        project_client.close()
        credential.close()

    result = {
        "evalId": args.eval_id,
        "evalRunId": args.run_id,
        "name": run.name,
        "status": run.status,
        "reportUrl": run.report_url,
        "resultCounts": _result_counts(run),
        "perCriteria": _criteria_results(run),
        "outputItems": output_items,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": run.status,
                "resultCounts": result["resultCounts"],
                "outputItems": len(output_items),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
