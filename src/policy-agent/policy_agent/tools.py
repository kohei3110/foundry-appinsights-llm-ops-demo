from __future__ import annotations

import asyncio
import json
from pathlib import Path

from opentelemetry.trace import SpanKind, Status, StatusCode

from policy_agent.models import RuntimeMode, Scenario
from policy_agent.telemetry import get_tracer

REQUEST_STATUS_UNAVAILABLE = "request_status_unavailable"
REQUEST_STATUS_NOT_FOUND = "request_status_not_found"


class RequestStatusToolError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = REQUEST_STATUS_UNAVAILABLE,
        retryable: bool = True,
    ) -> None:
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
                qualifier = (
                    "simulated " if mode is RuntimeMode.SIMULATION else ""
                )
                raise self._fail(
                    span,
                    REQUEST_STATUS_UNAVAILABLE,
                    f"The {qualifier}request-status dependency is unavailable",
                )

            try:
                statuses = json.loads(
                    self._statuses_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise self._fail(
                    span,
                    REQUEST_STATUS_UNAVAILABLE,
                    "The request-status dependency is unavailable: "
                    f"{type(exc).__name__}",
                ) from exc

            if request_id not in statuses:
                raise self._fail(
                    span,
                    REQUEST_STATUS_NOT_FOUND,
                    f"Request status not found for {request_id}",
                    retryable=False,
                )

            result = statuses[request_id]
            span.set_attribute("llmops.tool.result_status", result["status"])
            return result

    @staticmethod
    def _fail(
        span,
        code: str,
        message: str,
        *,
        retryable: bool = True,
    ) -> RequestStatusToolError:
        error = RequestStatusToolError(
            f"[{code}] {message}", code=code, retryable=retryable
        )
        span.set_attribute("error.type", code)
        span.set_attribute("llmops.tool.error.retryable", retryable)
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.record_exception(error)
        return error
