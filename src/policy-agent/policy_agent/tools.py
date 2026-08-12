from __future__ import annotations

import asyncio
import json
from pathlib import Path

from opentelemetry.trace import SpanKind, Status, StatusCode

from policy_agent.models import RuntimeMode, Scenario
from policy_agent.telemetry import get_tracer


class RequestStatusToolError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RequestStatusTool:
    def __init__(self, data_root: Path, slow_delay_seconds: float) -> None:
        self._statuses_path = data_root / "requests" / "statuses.json"
        self._slow_delay_seconds = slow_delay_seconds

    async def execute(
        self,
        request_id: str,
        scenario: Scenario,
        mode: RuntimeMode = RuntimeMode.SIMULATION,
    ) -> dict[str, str]:
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "execute_tool request_status",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "request_status",
                "gen_ai.tool.type": "function",
                "llmops.scenario": scenario.value,
                "llmops.mode": mode.value,
                "llmops.request.id": request_id,
            },
        ) as span:
            if scenario is Scenario.SLOW_TOOL:
                await asyncio.sleep(self._slow_delay_seconds)

            if scenario is Scenario.TOOL_FAILURE:
                error = RequestStatusToolError(
                    "request_status_failure",
                    "The request-status dependency is unavailable",
                    retryable=True,
                )
                span.set_attribute("error.type", error.code)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                span.record_exception(error)
                raise error

            statuses = json.loads(self._statuses_path.read_text(encoding="utf-8"))
            if request_id not in statuses:
                error = RequestStatusToolError(
                    "request_status_not_found",
                    f"Request status not found for {request_id}",
                    retryable=False,
                )
                span.set_attribute("error.type", error.code)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                span.record_exception(error)
                raise error

            result = statuses[request_id]
            span.set_attribute("llmops.tool.result_status", result["status"])
            return result
