from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start an existing Foundry evaluation with hosted-agent tool "
            "descriptions attached to the target."
        )
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--deployment-name", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--evaluation-name", required=True)
    parser.add_argument("--run-name", required=True)
    return parser.parse_args()


def load_dataset(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_testing_criteria(deployment_name: str) -> list[dict[str, object]]:
    criteria: list[dict[str, object]] = []
    for name in (
        "relevance",
        "task_adherence",
        "intent_resolution",
        "tool_call_accuracy",
    ):
        mapping = {
            "query": "{{item.query}}",
            "response": "{{sample.output_items}}",
        }
        if name != "relevance":
            mapping.update(
                {
                    "tool_calls": "{{sample.tool_calls}}",
                    "tool_definitions": "{{item.tool_definitions}}",
                }
            )
        criteria.append(
            {
                "type": "azure_ai_evaluator",
                "name": name,
                "evaluator_name": f"builtin.{name}",
                "initialization_parameters": {
                    "deployment_name": deployment_name,
                },
                "data_mapping": mapping,
            }
        )
    return criteria


def main() -> None:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    args = parse_args()
    cases = load_dataset(args.dataset)
    if not cases:
        raise ValueError("Evaluation dataset is empty")

    first_tools = cases[0].get("tool_definitions")
    if not isinstance(first_tools, list) or not first_tools:
        raise ValueError("Evaluation dataset must include tool_definitions")
    tool_descriptions = [
        {
            "name": str(tool["name"]),
            "description": str(tool.get("description", "")),
        }
        for tool in first_tools
        if isinstance(tool, dict) and tool.get("name")
    ]

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
    )
    try:
        client = project_client.get_openai_client()
        evaluation = client.evals.create(
            name=args.evaluation_name,
            data_source_config={
                "type": "custom",
                "item_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "expected_behavior": {"type": "string"},
                        "tool_definitions": {"type": "array"},
                    },
                    "required": ["query", "tool_definitions"],
                },
                "include_sample_schema": True,
            },
            testing_criteria=build_testing_criteria(args.deployment_name),
            metadata={
                "agent_name": args.agent_name,
                "agent_version": args.agent_version,
                "source": "eval-yaml-tool-aware",
            },
        )
        run = client.evals.runs.create(
            eval_id=evaluation.id,
            name=args.run_name,
            data_source={
                "type": "azure_ai_target_completions",
                "source": {
                    "type": "file_content",
                    "content": [{"item": case} for case in cases],
                },
                "target": {
                    "type": "azure_ai_agent",
                    "name": args.agent_name,
                    "version": args.agent_version,
                    "tool_descriptions": tool_descriptions,
                },
                "input_messages": {
                    "type": "template",
                    "template": [
                        {
                            "role": "user",
                            "content": "{{item.query}}",
                            "type": "message",
                        }
                    ],
                },
                "item_generation_params": {
                    "type": "target_completions",
                    "max_concurrency": 1,
                },
            },
        )
    finally:
        project_client.close()
        credential.close()

    print(
        json.dumps(
            {
                "evalId": evaluation.id,
                "evalRunId": run.id,
                "name": run.name,
                "status": run.status,
                "reportUrl": run.report_url,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
