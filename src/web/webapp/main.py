from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from policy_agent.config import Settings, get_settings
from policy_agent.models import (
    AnswerStatus,
    AskRequest,
    DecisionStatus,
    OperationsRequest,
    RuntimeMode,
)
from policy_agent.operations import (
    LiveOperationsProvider,
    OperationsService,
    SimulationOperationsProvider,
)
from policy_agent.providers import LiveFoundryProvider, SimulationProvider
from policy_agent.repository import PolicyRepository
from policy_agent.service import AnswerService
from policy_agent.telemetry import configure_telemetry
from policy_agent.tools import RequestStatusTool

_INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"


def create_app(
    settings: Settings | None = None,
    *,
    live_provider: LiveFoundryProvider | None = None,
    live_operations_provider: LiveOperationsProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_telemetry(settings)
    repository = PolicyRepository(settings.data_root)
    status_tool = RequestStatusTool(
        settings.data_root, settings.slow_tool_delay_seconds
    )
    simulation_provider = SimulationProvider(settings, repository, status_tool)
    live_provider = live_provider or LiveFoundryProvider(settings, repository)
    service = AnswerService(
        {
            RuntimeMode.SIMULATION: simulation_provider,
            RuntimeMode.LIVE: live_provider,
        }
    )
    operations_service = OperationsService(
        {
            RuntimeMode.SIMULATION: SimulationOperationsProvider(settings),
            RuntimeMode.LIVE: (
                live_operations_provider or LiveOperationsProvider(settings)
            ),
        }
    )

    app = FastAPI(
        title="Foundry + Application Insights LLM Ops Demo",
        version=settings.service_version,
    )
    app.state.settings = settings
    app.state.answer_service = service
    app.state.operations_service = operations_service

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return _INDEX_PATH.read_text(encoding="utf-8")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.application_name}

    @app.get("/readyz")
    async def readyz(
        mode: RuntimeMode = Query(default=RuntimeMode.SIMULATION),
    ):
        if mode is RuntimeMode.LIVE and not settings.live_configured:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "mode": mode.value,
                    "reason": (
                        "FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME "
                        "are required"
                    ),
                },
            )
        return {"status": "ready", "mode": mode.value}

    @app.post("/api/ask")
    async def ask(request: AskRequest):
        response = await service.answer(request)
        headers = {
            "X-Conversation-ID": response.conversation_id,
            "X-Response-ID": response.response_id,
            "X-Trace-ID": response.trace_id,
        }
        status_code = 200 if response.status is AnswerStatus.OK else 502
        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(mode="json"),
            headers=headers,
        )

    @app.get("/api/operations/readiness")
    async def operations_readiness(
        mode: RuntimeMode = Query(default=RuntimeMode.SIMULATION),
    ):
        if mode is RuntimeMode.LIVE and not settings.operations_live_configured:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "mode": mode.value,
                    "reason": (
                        "SRE_AGENT_ENDPOINT, SRE_AGENT_NAME, "
                        "AZURE_MONITOR_ACCOUNT_ID, and OBSERVABILITY_AGENT_NAME "
                        "are required"
                    ),
                },
            )
        return {"status": "ready", "mode": mode.value}

    @app.post("/api/operations/investigate")
    async def investigate_operations(request: OperationsRequest):
        response = await operations_service.investigate(request)
        headers = {
            "X-Conversation-ID": response.conversation_id,
            "X-Trace-ID": response.trace_id,
        }
        if response.status is AnswerStatus.ERROR:
            status_code = 502
        elif (
            response.decision
            and response.decision.status is DecisionStatus.AWAITING_ISSUE
        ):
            status_code = 202
        else:
            status_code = 200
        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(mode="json"),
            headers=headers,
        )

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="healthz,readyz",
    )
    return app


app = create_app()
