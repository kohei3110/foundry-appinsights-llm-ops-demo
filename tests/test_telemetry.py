import logging
import os

import pytest

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from policy_agent.models import AskRequest, RuntimeMode, Scenario
from policy_agent.providers import SimulationProvider
from policy_agent.repository import PolicyRepository
from policy_agent.service import AnswerService
from policy_agent.telemetry import configure_content_logging, configure_telemetry
from policy_agent.tools import RequestStatusTool, RequestStatusToolError


def test_framework_content_logging_follows_capture_setting(settings):
    framework_logger = logging.getLogger("agent_framework")
    original_level = framework_logger.level
    env_names = (
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
        "ENABLE_SENSITIVE_DATA",
        "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED",
    )
    original_env = {name: os.environ.get(name) for name in env_names}
    try:
        configure_content_logging(
            settings.model_copy(update={"capture_content": False})
        )
        assert not framework_logger.isEnabledFor(logging.INFO)
        assert framework_logger.isEnabledFor(logging.WARNING)
        assert all(os.environ[name] == "false" for name in env_names)

        configure_content_logging(settings.model_copy(update={"capture_content": True}))
        assert framework_logger.isEnabledFor(logging.INFO)
        assert all(os.environ[name] == "true" for name in env_names)
    finally:
        framework_logger.setLevel(original_level)
        for name, value in original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


async def test_genai_span_attributes_and_content_default(settings):
    configure_telemetry(settings)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider = trace.get_tracer_provider()
    provider.add_span_processor(processor)

    repository = PolicyRepository(settings.data_root)
    service = AnswerService(
        {
            RuntimeMode.SIMULATION: SimulationProvider(
                settings,
                repository,
                RequestStatusTool(settings.data_root, 0),
            )
        }
    )

    response = await service.answer(
        AskRequest(
            question="精算期限は？",
            scenario=Scenario.HEALTHY,
            conversation_id="conv_telemetry",
        )
    )
    spans = {span.name: span for span in exporter.get_finished_spans()}

    assert {
        "llmops.request",
        "invoke_agent policy-agent",
        "retrieve_policy",
        "chat gpt-5.4-mini",
        "execute_tool request_status",
    }.issubset(spans)
    assert spans["retrieve_policy"].attributes["gen_ai.operation.name"] == "retrieve"
    assert (
        spans["execute_tool request_status"].attributes["gen_ai.operation.name"]
        == "execute_tool"
    )
    chat_attributes = spans["chat gpt-5.4-mini"].attributes
    assert chat_attributes["gen_ai.response.id"] == response.response_id
    assert chat_attributes["gen_ai.usage.input_tokens"] > 0
    assert "gen_ai.input.messages" not in chat_attributes
    assert "gen_ai.output.messages" not in chat_attributes


async def test_live_tool_failure_span_records_dependency_error_type(settings):
    configure_telemetry(settings)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    trace.get_tracer_provider().add_span_processor(processor)

    tool = RequestStatusTool(settings.data_root, 0)
    with pytest.raises(RequestStatusToolError) as failure:
        await tool.execute(
            "REQ-2026-0042", Scenario.TOOL_FAILURE, RuntimeMode.LIVE
        )

    assert failure.value.code == "request_status_unavailable"
    assert "simulated" not in str(failure.value)
    spans = {span.name: span for span in exporter.get_finished_spans()}
    attributes = spans["execute_tool request_status"].attributes
    assert attributes["error.type"] == "request_status_unavailable"
    assert attributes["llmops.mode"] == "live"
