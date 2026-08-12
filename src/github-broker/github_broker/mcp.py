from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from github_broker.azure import HandoffVerificationError
from github_broker.github import GitHubBrokerError
from github_broker.models import SreHandoffRequest
from github_broker.service import HandoffBroker


class HandoffStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_number: int = Field(gt=0)


class McpHandler:
    protocol_version = "2025-06-18"

    def __init__(self, broker: HandoffBroker) -> None:
        self._broker = broker

    async def handle(self, payload: object) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return self._error(None, -32600, "Invalid Request")
        request_id = payload.get("id")
        method = payload.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._success(
                request_id,
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "sre-github-handoff-broker",
                        "version": "0.1.0",
                    },
                    "instructions": (
                        "Submit only sanitized live Azure SRE Agent findings. "
                        "The broker creates an issue, assigns Copilot, and never "
                        "merges or deploys."
                    ),
                },
            )
        if method == "ping":
            return self._success(request_id, {})
        if method == "tools/list":
            return self._success(request_id, {"tools": self._tools()})
        if method == "tools/call":
            return await self._call_tool(request_id, payload.get("params"))
        return self._error(request_id, -32601, "Method not found")

    async def _call_tool(
        self,
        request_id: object,
        params: object,
    ) -> dict[str, Any]:
        if not isinstance(params, dict) or not isinstance(
            params.get("arguments"), dict
        ):
            return self._error(request_id, -32602, "Invalid params")
        name = params.get("name")
        try:
            if name == "submit_incident_handoff":
                handoff = SreHandoffRequest.model_validate(params["arguments"])
                result = await self._broker.submit(handoff)
            elif name == "get_handoff_status":
                status_request = HandoffStatusRequest.model_validate(
                    params["arguments"]
                )
                result = await self._broker.get_status(
                    status_request.issue_number
                )
            else:
                return self._error(request_id, -32602, "Unknown tool")
        except ValidationError:
            return self._tool_error(request_id, "Invalid sanitized handoff payload")
        except (GitHubBrokerError, HandoffVerificationError) as exc:
            return self._tool_error(
                request_id,
                f"{exc.code}: {exc}",
            )
        structured = result.model_dump(mode="json")
        return self._success(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": result.model_dump_json(),
                    }
                ],
                "structuredContent": structured,
                "isError": False,
            },
        )

    def _tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "submit_incident_handoff",
                "description": (
                    "Create or reuse one sanitized GitHub issue for a completed "
                    "live read-only SRE investigation and assign Copilot to "
                    "produce a draft pull request."
                ),
                "inputSchema": SreHandoffRequest.model_json_schema(),
                "annotations": {
                    "title": "Submit sanitized incident handoff",
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            },
            {
                "name": "get_handoff_status",
                "description": (
                    "Read the GitHub issue and linked Copilot draft pull request "
                    "status for a previously submitted handoff."
                ),
                "inputSchema": HandoffStatusRequest.model_json_schema(),
                "annotations": {
                    "title": "Get incident handoff status",
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            },
        ]

    def _success(self, request_id: object, result: object) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(
        self,
        request_id: object,
        code: int,
        message: str,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def _tool_error(self, request_id: object, message: str) -> dict[str, Any]:
        return self._success(
            request_id,
            {
                "content": [{"type": "text", "text": message}],
                "isError": True,
            },
        )
