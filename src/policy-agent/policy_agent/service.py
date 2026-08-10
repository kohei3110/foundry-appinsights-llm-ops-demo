from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from opentelemetry.trace import SpanKind, Status, StatusCode

from policy_agent.models import (
    AnswerStatus,
    AskRequest,
    AskResponse,
    ErrorDetail,
    RuntimeMode,
)
from policy_agent.providers import AnswerProvider, ProviderError
from policy_agent.repository import PolicyUnavailableError
from policy_agent.telemetry import current_trace_id, get_tracer
from policy_agent.tools import RequestStatusToolError


class AnswerService:
    def __init__(self, providers: dict[RuntimeMode, AnswerProvider]) -> None:
        self._providers = providers

    async def answer(self, request: AskRequest) -> AskResponse:
        conversation_id = request.conversation_id or f"conv_{uuid4().hex}"
        started = perf_counter()
        tracer = get_tracer()

        with tracer.start_as_current_span(
            "llmops.request",
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": "POST",
                "http.route": "/api/ask",
                "gen_ai.conversation.id": conversation_id,
                "llmops.mode": request.mode.value,
                "llmops.scenario": request.scenario.value,
            },
        ) as span:
            try:
                provider = self._providers[request.mode]
                result = await provider.answer(request, conversation_id)
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                span.set_attribute("gen_ai.response.id", result.response_id)
                span.set_attribute("llmops.elapsed_ms", elapsed_ms)
                return AskResponse(
                    status=AnswerStatus.OK,
                    answer=result.answer,
                    sources=result.sources,
                    elapsed_ms=elapsed_ms,
                    mode=request.mode,
                    scenario=request.scenario,
                    conversation_id=conversation_id,
                    response_id=result.response_id,
                    trace_id=current_trace_id(),
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            except (
                ProviderError,
                PolicyUnavailableError,
                RequestStatusToolError,
            ) as exc:
                response_id = f"errresp_{uuid4().hex}"
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                if isinstance(exc, ProviderError):
                    code = exc.code
                    retryable = exc.retryable
                elif isinstance(exc, PolicyUnavailableError):
                    code = "policy_unavailable"
                    retryable = False
                else:
                    code = "request_status_failure"
                    retryable = True
                span.set_attribute("gen_ai.response.id", response_id)
                span.set_attribute("error.type", code)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                return AskResponse(
                    status=AnswerStatus.ERROR,
                    answer=None,
                    sources=[],
                    elapsed_ms=elapsed_ms,
                    mode=request.mode,
                    scenario=request.scenario,
                    conversation_id=conversation_id,
                    response_id=response_id,
                    trace_id=current_trace_id(),
                    error=ErrorDetail(
                        code=code,
                        message=str(exc),
                        retryable=retryable,
                    ),
                )
