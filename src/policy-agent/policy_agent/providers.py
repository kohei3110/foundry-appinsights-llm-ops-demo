from __future__ import annotations

import asyncio
import json
from typing import Protocol
from uuid import uuid4

from opentelemetry.trace import SpanKind, Status, StatusCode

from policy_agent.config import Settings
from policy_agent.credentials import build_credential
from policy_agent.models import AskRequest, ProviderResult, RuntimeMode, Source
from policy_agent.repository import PolicyRepository, PolicyUnavailableError
from policy_agent.telemetry import get_tracer
from policy_agent.tools import RequestStatusTool


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AnswerProvider(Protocol):
    mode: RuntimeMode

    async def answer(
        self, request: AskRequest, conversation_id: str
    ) -> ProviderResult: ...


class SimulationProvider:
    mode = RuntimeMode.SIMULATION

    def __init__(
        self,
        settings: Settings,
        repository: PolicyRepository,
        status_tool: RequestStatusTool,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._status_tool = status_tool

    async def answer(
        self, request: AskRequest, conversation_id: str
    ) -> ProviderResult:
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "invoke_agent policy-agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "policy-agent",
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.provider.name": "llmops.simulation",
                "llmops.mode": self.mode.value,
                "llmops.scenario": request.scenario.value,
            },
        ) as invoke_span:
            try:
                document = self._repository.retrieve(
                    request.question, request.scenario, self.mode
                )
            except PolicyUnavailableError as exc:
                raise ProviderError(
                    "policy_unavailable",
                    "No current policy document is available to answer the question",
                ) from exc
            status = await self._status_tool.execute(
                request.request_id, request.scenario, self.mode
            )
            response_id = f"simresp_{uuid4().hex}"

            with tracer.start_as_current_span(
                f"chat {self._settings.model_deployment_name}",
                kind=SpanKind.CLIENT,
                attributes={
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": "llmops.simulation",
                    "gen_ai.request.model": self._settings.model_deployment_name,
                    "gen_ai.conversation.id": conversation_id,
                    "gen_ai.response.id": response_id,
                    "llmops.mode": self.mode.value,
                    "llmops.scenario": request.scenario.value,
                },
            ) as chat_span:
                answer = (
                    f"国内出張の精算期限は、出張終了日の翌日から"
                    f"{document.reimbursement_days}日以内です。"
                    f"申請 {request.request_id} の現在の状況は"
                    f"「{status['status']}」です。"
                )
                input_tokens = max(24, len(request.question) // 2 + 20)
                output_tokens = max(32, len(answer) // 2)
                chat_span.set_attribute("gen_ai.response.model", "simulation-v1")
                chat_span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                chat_span.set_attribute(
                    "gen_ai.usage.output_tokens", output_tokens
                )
                chat_span.set_attribute(
                    "gen_ai.response.finish_reasons", json.dumps(["stop"])
                )
                if self._settings.capture_content:
                    chat_span.set_attribute(
                        "gen_ai.input.messages",
                        json.dumps(
                            [{"role": "user", "content": request.question}],
                            ensure_ascii=False,
                        ),
                    )
                    chat_span.set_attribute(
                        "gen_ai.output.messages",
                        json.dumps(
                            [{"role": "assistant", "content": answer}],
                            ensure_ascii=False,
                        ),
                    )

            invoke_span.set_attribute("gen_ai.response.id", response_id)
            invoke_span.set_attribute("llmops.policy.version", document.version)
            invoke_span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            invoke_span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            return ProviderResult(
                answer=answer,
                sources=[document.to_source()],
                response_id=response_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )


class LiveFoundryProvider:
    mode = RuntimeMode.LIVE

    def __init__(
        self,
        settings: Settings,
        repository: PolicyRepository,
        *,
        client=None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._settings.live_configured:
            raise ProviderError(
                "live_not_configured",
                "Live mode requires FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME",
            )

        from azure.ai.projects import AIProjectClient

        project_client = AIProjectClient(
            endpoint=self._settings.foundry_project_endpoint,
            credential=build_credential(self._settings),
            allow_preview=True,
        )
        self._client = project_client.get_openai_client(
            agent_name=self._settings.foundry_agent_name,
        )
        return self._client

    async def answer(
        self, request: AskRequest, conversation_id: str
    ) -> ProviderResult:
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "invoke_agent policy-agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": self._settings.foundry_agent_name
                or "policy-agent",
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.provider.name": "azure.ai.foundry",
                "llmops.mode": self.mode.value,
                "llmops.scenario": request.scenario.value,
            },
        ) as span:
            prompt = (
                f"Conversation ID: {conversation_id}\n"
                f"Scenario: {request.scenario.value}\n"
                f"Request ID: {request.request_id}\n"
                f"Question: {request.question}"
            )
            if self._settings.capture_content:
                span.set_attribute(
                    "gen_ai.input.messages",
                    json.dumps(
                        [{"role": "user", "content": prompt}], ensure_ascii=False
                    ),
                )

            try:
                client = self._get_client()
                response = await asyncio.to_thread(
                    client.responses.create,
                    input=prompt,
                )
            except ProviderError:
                raise
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise ProviderError(
                    "live_provider_failure",
                    f"Foundry live invocation failed: {exc}",
                    retryable=True,
                ) from exc

            response_id = str(response.id)
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            answer = str(response.output_text)
            try:
                document = self._repository.select(request.scenario)
            except PolicyUnavailableError as exc:
                span.set_attribute("error.type", "policy_unavailable")
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise ProviderError(
                    "policy_unavailable",
                    "No current policy document is available to answer the question",
                ) from exc

            span.set_attribute("gen_ai.response.id", response_id)
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            if self._settings.capture_content:
                span.set_attribute(
                    "gen_ai.output.messages",
                    json.dumps(
                        [{"role": "assistant", "content": answer}],
                        ensure_ascii=False,
                    ),
                )

            return ProviderResult(
                answer=answer,
                sources=[
                    Source(
                        document_id=document.document_id,
                        title=document.title,
                        version=document.version,
                        status=document.status,
                        path=f"data/policies/{document.path}",
                    )
                ],
                response_id=response_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
