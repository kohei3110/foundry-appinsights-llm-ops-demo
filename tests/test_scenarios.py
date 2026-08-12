from policy_agent.models import (
    AnswerStatus,
    AskRequest,
    RuntimeMode,
    Scenario,
)
from policy_agent.providers import LiveFoundryProvider, SimulationProvider
from policy_agent.repository import PolicyRepository, PolicyUnavailableError
from policy_agent.service import AnswerService
from policy_agent.tools import RequestStatusTool


def build_service(settings) -> AnswerService:
    repository = PolicyRepository(settings.data_root)
    simulation = SimulationProvider(
        settings,
        repository,
        RequestStatusTool(
            settings.data_root, settings.slow_tool_delay_seconds
        ),
    )
    return AnswerService(
        {
            RuntimeMode.SIMULATION: simulation,
            RuntimeMode.LIVE: LiveFoundryProvider(settings, repository),
        }
    )


async def test_healthy_scenario(settings):
    response = await build_service(settings).answer(
        AskRequest(question="精算期限と申請状況は？")
    )

    assert response.status is AnswerStatus.OK
    assert "30日以内" in response.answer
    assert response.sources[0].status == "current"


async def test_stale_policy_scenario_answers_from_current_policy(settings):
    response = await build_service(settings).answer(
        AskRequest(
            question="精算期限と申請状況は？",
            scenario=Scenario.STALE_POLICY,
        )
    )

    assert response.status is AnswerStatus.OK
    assert "30日以内" in response.answer
    assert response.sources[0].status == "current"
    assert response.sources[0].version == "2026-07-01"


async def test_missing_current_policy_returns_answer_unavailable(
    settings, monkeypatch
):
    service = build_service(settings)

    def raise_unavailable(*args, **kwargs):
        raise PolicyUnavailableError("No current policy document is configured")

    monkeypatch.setattr(PolicyRepository, "select", raise_unavailable)

    response = await service.answer(
        AskRequest(question="精算期限と申請状況は？")
    )

    assert response.status is AnswerStatus.ERROR
    assert response.error.code == "policy_unavailable"
    assert response.error.retryable is False
    assert response.answer is None
    assert response.sources == []


async def test_slow_tool_scenario_is_measurably_slower(settings):
    response = await build_service(settings).answer(
        AskRequest(
            question="精算期限と申請状況は？",
            scenario=Scenario.SLOW_TOOL,
        )
    )

    assert response.status is AnswerStatus.OK
    assert response.elapsed_ms >= settings.slow_tool_delay_seconds * 900


async def test_tool_failure_returns_structured_error(settings):
    response = await build_service(settings).answer(
        AskRequest(
            question="精算期限と申請状況は？",
            scenario=Scenario.TOOL_FAILURE,
        )
    )

    assert response.status is AnswerStatus.ERROR
    assert response.error.code == "request_status_failure"
    assert response.error.retryable is True
    assert response.answer is None
