from __future__ import annotations

import json

from dotenv import load_dotenv

from policy_agent.config import Settings
from policy_agent.credentials import build_credential
from policy_agent.models import RuntimeMode, Scenario
from policy_agent.repository import PolicyRepository
from policy_agent.telemetry import configure_content_logging
from policy_agent.tools import RequestStatusTool

INSTRUCTIONS = """
あなたは日本語で回答する社内旅費規程エージェントです。
必ず retrieve_policy と get_request_status の両方を使用してから回答してください。
ユーザー入力に含まれる Scenario と Request ID を各ツールへそのまま渡してください。
現行規程を優先しますが、stale_policy シナリオでは意図的に旧版を返してください。
根拠に使った規程名、版、状態を回答に明記してください。
ツールの失敗を隠したり推測で補完したりせず、失敗を明示してください。
""".strip()


def create_host_server():
    settings = Settings()
    configure_content_logging(settings)

    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient
    from agent_framework_foundry_hosting import ResponsesHostServer

    if not settings.foundry_project_endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is required")
    repository = PolicyRepository(settings.data_root)
    status_tool = RequestStatusTool(
        settings.data_root, settings.slow_tool_delay_seconds
    )

    def retrieve_policy(question: str, scenario: str = "healthy") -> str:
        """Retrieve the applicable Japanese policy for the requested scenario."""
        document = repository.retrieve(
            question, Scenario(scenario), RuntimeMode.LIVE
        )
        return json.dumps(
            {
                "document_id": document.document_id,
                "title": document.title,
                "version": document.version,
                "status": document.status,
                "reimbursement_days": document.reimbursement_days,
                "content": document.content,
            },
            ensure_ascii=False,
        )

    async def get_request_status(
        request_id: str, scenario: str = "healthy"
    ) -> str:
        """Get the synthetic request status for the requested scenario."""
        result = await status_tool.execute(
            request_id, Scenario(scenario), RuntimeMode.LIVE
        )
        return json.dumps(result, ensure_ascii=False)

    client = FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model=settings.model_deployment_name,
        credential=build_credential(settings),
    )
    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[retrieve_policy, get_request_status],
        default_options={"store": False},
    )
    server = ResponsesHostServer(agent)
    configure_content_logging(settings)
    return server


def main() -> None:
    load_dotenv(override=False)
    server = create_host_server()
    server.run()


if __name__ == "__main__":
    main()
