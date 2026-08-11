from policy_agent.models import AnswerStatus, AskRequest, RuntimeMode, Scenario
from policy_agent.providers import (
    LiveFoundryProvider,
    SimulationProvider,
)
from policy_agent.repository import PolicyRepository
from policy_agent.service import AnswerService
from policy_agent.tools import RequestStatusTool


class FailingResponses:
    def create(self, **_kwargs):
        raise RuntimeError("synthetic Foundry outage")


class FailingClient:
    responses = FailingResponses()


class SuccessfulResponse:
    id = "resp_test"
    output_text = "国内出張の精算期限は30日以内です。"
    usage = None


class RecordingResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SuccessfulResponse()


class RecordingClient:
    def __init__(self):
        self.responses = RecordingResponses()


class CountingSimulationProvider(SimulationProvider):
    calls = 0

    async def answer(self, request, conversation_id):
        self.calls += 1
        return await super().answer(request, conversation_id)


def test_live_client_targets_hosted_agent_endpoint(settings, monkeypatch):
    configured = settings.model_copy(
        update={
            "foundry_project_endpoint": (
                "https://example.services.ai.azure.com/api/projects/demo"
            ),
            "foundry_agent_name": "policy-agent",
            "foundry_agent_version": "7",
        }
    )
    repository = PolicyRepository(configured.data_root)
    client = RecordingClient()
    credential = object()
    calls = {}

    class RecordingProjectClient:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def get_openai_client(self, **kwargs):
            calls["get_openai_client"] = kwargs
            return client

    monkeypatch.setattr(
        "azure.ai.projects.AIProjectClient", RecordingProjectClient
    )
    monkeypatch.setattr(
        "policy_agent.providers.build_credential", lambda _settings: credential
    )

    provider = LiveFoundryProvider(configured, repository)

    assert provider._get_client() is client
    assert calls["init"] == {
        "endpoint": configured.foundry_project_endpoint,
        "credential": credential,
        "allow_preview": True,
    }
    assert calls["get_openai_client"] == {"agent_name": "policy-agent"}


async def test_live_invocation_uses_hosted_agent_client(settings):
    configured = settings.model_copy(
        update={
            "foundry_project_endpoint": (
                "https://example.services.ai.azure.com/api/projects/demo"
            ),
            "foundry_agent_name": "policy-agent",
            "foundry_agent_version": "7",
        }
    )
    repository = PolicyRepository(configured.data_root)
    client = RecordingClient()
    provider = LiveFoundryProvider(configured, repository, client=client)

    result = await provider.answer(
        AskRequest(question="精算期限は？", mode=RuntimeMode.LIVE),
        "conv_test",
    )

    assert result.response_id == "resp_test"
    assert result.sources[0].status == "current"
    assert client.responses.kwargs == {
        "input": (
            "Conversation ID: conv_test\n"
            "Scenario: healthy\n"
            "Request ID: REQ-2026-0042\n"
            "Question: 精算期限は？"
        )
    }


async def test_live_stale_scenario_still_uses_current_policy_source(settings):
    configured = settings.model_copy(
        update={
            "foundry_project_endpoint": (
                "https://example.services.ai.azure.com/api/projects/demo"
            ),
            "foundry_agent_name": "policy-agent",
        }
    )
    repository = PolicyRepository(configured.data_root)
    provider = LiveFoundryProvider(
        configured, repository, client=RecordingClient()
    )

    result = await provider.answer(
        AskRequest(
            question="精算期限は？",
            mode=RuntimeMode.LIVE,
            scenario=Scenario.STALE_POLICY,
        ),
        "conv_test",
    )

    assert result.sources[0].status == "current"


async def test_live_failure_never_falls_back_to_simulation(settings):
    configured = settings.model_copy(
        update={
            "foundry_project_endpoint": (
                "https://example.services.ai.azure.com/api/projects/demo"
            ),
            "foundry_agent_name": "policy-agent",
        }
    )
    repository = PolicyRepository(configured.data_root)
    simulation = CountingSimulationProvider(
        configured,
        repository,
        RequestStatusTool(configured.data_root, 0),
    )
    service = AnswerService(
        {
            RuntimeMode.SIMULATION: simulation,
            RuntimeMode.LIVE: LiveFoundryProvider(
                configured, repository, client=FailingClient()
            ),
        }
    )

    response = await service.answer(
        AskRequest(
            question="精算期限は？",
            mode=RuntimeMode.LIVE,
        )
    )

    assert response.status is AnswerStatus.ERROR
    assert response.error.code == "live_provider_failure"
    assert "synthetic Foundry outage" in response.error.message
    assert simulation.calls == 0


async def test_live_returns_structured_unavailability_when_no_current_policy(
    settings,
):
    configured = settings.model_copy(
        update={
            "foundry_project_endpoint": (
                "https://example.services.ai.azure.com/api/projects/demo"
            ),
            "foundry_agent_name": "policy-agent",
        }
    )
    repository = PolicyRepository(configured.data_root)
    repository._documents = [
        document
        for document in repository._documents
        if document.status == "superseded"
    ]
    service = AnswerService(
        {
            RuntimeMode.LIVE: LiveFoundryProvider(
                configured, repository, client=RecordingClient()
            )
        }
    )

    response = await service.answer(
        AskRequest(
            question="精算期限は？",
            mode=RuntimeMode.LIVE,
        )
    )

    assert response.status is AnswerStatus.ERROR
    assert response.error.code == "answer_unavailable"
    assert "No current policy document is configured" in response.error.message
