from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from github_broker.azure import (
    AzureAlertVerifier,
    HandoffVerificationError,
    build_credential,
)
from github_broker.config import BrokerSettings, get_settings
from github_broker.github import GitHubBrokerError, GitHubClient
from github_broker.mcp import McpHandler
from github_broker.models import HandoffState, SreHandoffRequest
from github_broker.service import HandoffBroker
from github_broker.telemetry import configure_telemetry


class _BodyTooLargeError(RuntimeError):
    pass


class HandoffSecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: BrokerSettings,
        max_body_bytes: int = 32_768,
    ) -> None:
        self._app = app
        self._settings = settings
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not _is_protected_path(scope["path"]):
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        authorization_error = _authorization_error(
            headers.get("Authorization", ""),
            self._settings,
        )
        if authorization_error:
            await authorization_error(scope, receive, send)
            return
        content_length = headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > self._max_body_bytes:
                    await self._too_large(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length"},
                )
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body_bytes:
                    raise _BodyTooLargeError
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await self._too_large(scope, receive, send)

    async def _too_large(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"},
        )
        await response(scope, receive, send)


def create_app(
    settings: BrokerSettings | None = None,
    *,
    broker: HandoffBroker | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_telemetry(settings)

    github_client = None
    alert_verifier = None
    if broker is None and settings.configured:
        github_client = GitHubClient(settings)
        alert_verifier = AzureAlertVerifier(
            settings,
            build_credential(settings),
        )
        broker = HandoffBroker(settings, github_client, alert_verifier)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if github_client:
            await github_client.aclose()
        if alert_verifier:
            await alert_verifier.aclose()

    app = FastAPI(
        title="SRE GitHub Handoff Broker",
        version=settings.service_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.broker = broker
    app.state.github_client = github_client
    app.state.mcp_handler = McpHandler(broker) if broker else None
    app.add_middleware(HandoffSecurityMiddleware, settings=settings)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.application_name}

    @app.get("/readyz")
    async def readyz():
        if not settings.configured or broker is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": (
                        "GITHUB_HANDOFF_ENABLED, GITHUB_HANDOFF_BEARER_TOKEN, "
                        "GITHUB_BROKER_TOKEN, AZURE_SUBSCRIPTION_ID, "
                        "AZURE_RESOURCE_GROUP, and "
                        "APPLICATIONINSIGHTS_RESOURCE_ID are required"
                    ),
                },
            )
        return {"status": "ready"}

    @app.post("/api/handoffs")
    async def submit_handoff(handoff: SreHandoffRequest):
        active_broker = _require_broker(broker)
        try:
            result = await active_broker.submit(handoff)
        except (GitHubBrokerError, HandoffVerificationError) as exc:
            raise HTTPException(
                status_code=503 if exc.retryable else 502,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        status_code = (
            200 if result.state is HandoffState.DRAFT_PR_READY else 202
        )
        return JSONResponse(
            status_code=status_code,
            content=result.model_dump(mode="json"),
        )

    @app.get("/api/handoffs/issues/{issue_number}")
    async def get_handoff_status(issue_number: int):
        active_broker = _require_broker(broker)
        try:
            result = await active_broker.get_status(issue_number)
        except GitHubBrokerError as exc:
            raise HTTPException(
                status_code=503 if exc.retryable else 502,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        status_code = (
            200 if result.state is HandoffState.DRAFT_PR_READY else 202
        )
        return JSONResponse(
            status_code=status_code,
            content=result.model_dump(mode="json"),
        )

    @app.post("/mcp/github-handoff")
    async def mcp_endpoint(request: Request):
        handler = app.state.mcp_handler
        if handler is None:
            raise HTTPException(status_code=503, detail="Broker is not configured")
        payload = await request.json()
        response = await handler.handle(payload)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(content=response)

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="healthz,readyz",
    )
    return app


def _is_protected_path(path: str) -> bool:
    return path == "/api/handoffs" or path.startswith(
        "/api/handoffs/"
    ) or path == "/mcp/github-handoff"


def _authorization_error(
    authorization: str,
    settings: BrokerSettings,
) -> JSONResponse | None:
    if not settings.configured or not settings.bearer_token:
        return JSONResponse(
            status_code=503,
            content={"detail": "Broker is not configured"},
        )
    scheme, _, supplied = authorization.partition(" ")
    expected = settings.bearer_token.get_secret_value()
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
        )
    return None


def _require_broker(broker: HandoffBroker | None) -> HandoffBroker:
    if broker is None:
        raise HTTPException(status_code=503, detail="Broker is not configured")
    return broker


app = create_app()
