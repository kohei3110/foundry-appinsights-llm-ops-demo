from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from github_broker.azure import AzureAlertVerifier, HandoffVerificationError
from github_broker.config import BrokerSettings
from github_broker.github import GitHubClient
from github_broker.main import create_app
from github_broker.models import (
    AlertRule,
    GitHubIssue,
    GitHubPullRequest,
    HandoffState,
    SreHandoffRequest,
)
from github_broker.service import HandoffBroker


def _settings() -> BrokerSettings:
    return BrokerSettings(
        _env_file=None,
        enabled=True,
        bearer_token="handoff-secret",
        github_token="github-token",
        azure_subscription_id="11111111-1111-4111-8111-111111111111",
        azure_resource_group="rg-llmops",
        applicationinsights_resource_id=(
            "/subscriptions/11111111-1111-4111-8111-111111111111/"
            "resourceGroups/rg-llmops/providers/Microsoft.Insights/"
            "components/appi-llmops"
        ),
        applicationinsights_connection_string=None,
    )


def _handoff_payload() -> dict[str, object]:
    alert_rule = "llmops-live-tool-failure"
    root_cause = "request_status_dependency_unavailable"
    evidence_codes = [
        "request_status_dependency_failed",
        "structured_failure_returned",
        "container_revision_healthy",
    ]
    fingerprint_input = "|".join(
        [
            alert_rule,
            root_cause,
            ",".join(sorted(evidence_codes)),
        ]
    )
    return {
        "mode": "live",
        "alert_id": (
            "/subscriptions/11111111-1111-4111-8111-111111111111/"
            "resourceGroups/rg-llmops/providers/Microsoft.Insights/"
            "components/appi-llmops/providers/"
            "Microsoft.AlertsManagement/alerts/"
            "22222222-2222-4222-8222-222222222222"
        ),
        "alert_rule": alert_rule,
        "investigation_fingerprint": sha256(
            fingerprint_input.encode("ascii")
        ).hexdigest()[:12],
        "evidence_codes": evidence_codes,
        "ruled_out_codes": [
            "model_failure",
            "authentication_failure",
            "container_platform_failure",
        ],
        "root_cause_code": root_cause,
        "confidence_band": "high",
    }


def test_github_actions_repository_env_does_not_override_broker_target(
    monkeypatch,
):
    monkeypatch.setenv(
        "GITHUB_REPOSITORY",
        "kohei3110/foundry-appinsights-llm-ops-demo",
    )

    settings = BrokerSettings(_env_file=None)

    assert settings.target_repository == "foundry-appinsights-llm-ops-demo"


class FakeGitHubClient:
    def __init__(self) -> None:
        self.issue: GitHubIssue | None = None
        self.pull_request: GitHubPullRequest | None = None
        self.create_calls = 0
        self.assign_calls = 0
        self.created_body = ""

    async def find_open_issue(self, title: str) -> GitHubIssue | None:
        if self.issue and self.issue.title == title:
            return self.issue
        return None

    async def create_issue(self, title: str, body: str) -> GitHubIssue:
        self.create_calls += 1
        self.created_body = body
        self.issue = GitHubIssue(
            number=17,
            html_url="https://github.com/example/repo/issues/17",
            title=title,
            user={"login": "kohei3110"},
        )
        return self.issue

    async def get_issue(self, issue_number: int) -> GitHubIssue:
        assert self.issue and self.issue.number == issue_number
        return self.issue

    async def assign_copilot(
        self,
        issue_number: int,
        custom_instructions: str,
    ) -> GitHubIssue:
        assert self.issue and self.issue.number == issue_number
        assert "draft pull request only" in custom_instructions
        self.assign_calls += 1
        self.issue = self.issue.model_copy(
            update={"assignees": [{"login": "Copilot"}]}
        )
        return self.issue

    async def find_linked_copilot_pr(
        self,
        issue_number: int,
    ) -> GitHubPullRequest | None:
        return self.pull_request

    async def get_pull_request(
        self,
        pull_request_number: int,
    ) -> GitHubPullRequest:
        assert self.pull_request
        assert self.pull_request.number == pull_request_number
        return self.pull_request


class FakeAlertVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AlertRule]] = []

    async def verify(self, alert_id: str, alert_rule: AlertRule) -> None:
        self.calls.append((alert_id, alert_rule))


async def test_broker_creates_issue_assigns_copilot_and_is_idempotent():
    github = FakeGitHubClient()
    verifier = FakeAlertVerifier()
    broker = HandoffBroker(_settings(), github, verifier)
    handoff = SreHandoffRequest.model_validate(_handoff_payload())

    first = await broker.submit(handoff)
    second = await broker.submit(handoff)

    assert first.state is HandoffState.COPILOT_ASSIGNED
    assert first.issue_created is True
    assert second.state is HandoffState.COPILOT_ALREADY_ASSIGNED
    assert second.issue_created is False
    assert github.create_calls == 1
    assert github.assign_calls == 1
    assert len(verifier.calls) == 2
    assert (
        str(_handoff_payload()["investigation_fingerprint"])
        in github.issue.title
    )
    assert "/subscriptions/" not in github.created_body
    assert str(_handoff_payload()["alert_id"]) not in github.created_body
    assert "live-to-simulation fallback" in github.created_body


async def test_broker_reports_linked_draft_pull_request():
    github = FakeGitHubClient()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())
    submitted = await broker.submit(
        SreHandoffRequest.model_validate(_handoff_payload())
    )
    github.pull_request = GitHubPullRequest(
        number=21,
        html_url="https://github.com/example/repo/pull/21",
        draft=True,
        state="open",
        user={"login": "Copilot"},
        base={
            "ref": "main",
            "repo": {
                "full_name": "kohei3110/foundry-appinsights-llm-ops-demo"
            },
        },
    )

    status = await broker.get_status(submitted.issue_number)

    assert status.state is HandoffState.DRAFT_PR_READY
    assert status.pull_request_number == 21
    assert status.pull_request_is_draft is True


def test_handoff_schema_rejects_simulation_and_restricted_content():
    simulation = {**_handoff_payload(), "mode": "simulation"}
    restricted = {**_handoff_payload(), "summary": "untrusted free-form text"}
    restricted_values = [
        "AccountKey=not-public",
        "Request failed with sig=not-public",
        "Span 0123456789abcdef failed",
        "Subscription 33333333-3333-4333-8333-333333333333 failed",
        "See https://example.com/private",
    ]

    with pytest.raises(ValidationError):
        SreHandoffRequest.model_validate(simulation)
    with pytest.raises(ValidationError):
        SreHandoffRequest.model_validate(restricted)
    for value in restricted_values:
        with pytest.raises(ValidationError):
            SreHandoffRequest.model_validate(
                {**_handoff_payload(), "summary": value}
            )

    with pytest.raises(ValidationError, match="does not match"):
        SreHandoffRequest.model_validate(
            {
                **_handoff_payload(),
                "alert_rule": "llmops-live-stale-policy",
            }
        )

    with pytest.raises(ValidationError, match="not deterministic"):
        SreHandoffRequest.model_validate(
            {
                **_handoff_payload(),
                "investigation_fingerprint": "abc123def456",
            }
        )

    canonical_alert = {
        **_handoff_payload(),
        "alert_id": (
            "/subscriptions/11111111-1111-4111-8111-111111111111/"
            "providers/Microsoft.AlertsManagement/alerts/"
            "22222222-2222-4222-8222-222222222222"
        ),
    }
    assert (
        SreHandoffRequest.model_validate(canonical_alert).alert_rule
        is AlertRule.TOOL_FAILURE
    )


def test_broker_api_requires_auth_and_returns_assignment():
    github = FakeGitHubClient()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())
    with TestClient(create_app(_settings(), broker=broker)) as client:
        unauthorized = client.post("/api/handoffs", json=_handoff_payload())
        response = client.post(
            "/api/handoffs",
            headers={"Authorization": "Bearer handoff-secret"},
            json=_handoff_payload(),
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 202
    assert response.json()["state"] == "copilot_assigned"


def test_broker_readiness_is_disabled_without_secrets():
    settings = BrokerSettings(
        _env_file=None,
        applicationinsights_connection_string=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_mcp_endpoint_lists_and_calls_broker_tools():
    github = FakeGitHubClient()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())
    headers = {"Authorization": "Bearer handoff-secret"}
    with TestClient(create_app(_settings(), broker=broker)) as client:
        initialized = client.post(
            "/mcp/github-handoff",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
        )
        tools = client.post(
            "/mcp/github-handoff",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        called = client.post(
            "/mcp/github-handoff",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "submit_incident_handoff",
                    "arguments": _handoff_payload(),
                },
            },
        )

    assert initialized.json()["result"]["protocolVersion"] == "2025-06-18"
    assert [tool["name"] for tool in tools.json()["result"]["tools"]] == [
        "submit_incident_handoff",
        "get_handoff_status",
    ]
    assert (
        called.json()["result"]["structuredContent"]["state"]
        == "copilot_assigned"
    )


async def test_github_client_uses_official_copilot_assignment_shape():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "number": 17,
                "html_url": "https://github.com/example/repo/issues/17",
                "title": "Issue",
                "assignees": [{"login": "Copilot"}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(_settings(), client=http_client)
        issue = await client.assign_copilot(17, "Create a draft PR only.")

    assert issue.copilot_assigned is True
    assert captured == {
        "method": "POST",
        "path": (
            "/repos/kohei3110/foundry-appinsights-llm-ops-demo/"
            "issues/17/assignees"
        ),
        "body": {
            "assignees": ["copilot-swe-agent[bot]"],
            "agent_assignment": {
                "target_repo": (
                    "kohei3110/foundry-appinsights-llm-ops-demo"
                ),
                "base_branch": "main",
                "custom_instructions": "Create a draft PR only.",
                "custom_agent": "",
                "model": "",
            },
        },
    }


async def test_github_client_finds_linked_copilot_draft_pr():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/issues/17/timeline")
        return httpx.Response(
            200,
            json=[
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "number": 99,
                            "html_url": (
                                "https://github.com/external/repo/pull/99"
                            ),
                            "title": "Untrusted external Copilot PR",
                            "draft": True,
                            "pull_request": {
                                "url": "https://api.github.com/pulls/99"
                            },
                            "user": {
                                "login": "Copilot"
                            },
                            "repository": {
                                "full_name": "external/repo"
                            },
                        }
                    },
                },
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "number": 23,
                            "html_url": (
                                "https://github.com/example/repo/pull/23"
                            ),
                            "title": "Copilot fix",
                            "draft": True,
                            "pull_request": {
                                "url": "https://api.github.com/pulls/23"
                            },
                            "user": {
                                "login": "Copilot"
                            },
                            "repository": {
                                "full_name": (
                                    "kohei3110/"
                                    "foundry-appinsights-llm-ops-demo"
                                )
                            },
                        }
                    },
                }
            ],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(_settings(), client=http_client)
        pull_request = await client.find_linked_copilot_pr(17)

    assert pull_request is not None
    assert pull_request.number == 23
    assert pull_request.draft is True


async def test_github_client_ignores_untrusted_duplicate_issue_creator():
    expected_title = "[SRE-AUTO] trusted"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "number": 99,
                    "html_url": "https://github.com/example/repo/issues/99",
                    "title": expected_title,
                    "user": {"login": "untrusted-user"},
                },
                {
                    "number": 17,
                    "html_url": "https://github.com/example/repo/issues/17",
                    "title": expected_title,
                    "user": {"login": "kohei3110"},
                },
            ],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(_settings(), client=http_client)
        issue = await client.find_open_issue(expected_title)

    assert issue is not None
    assert issue.number == 17
    assert issue.creator_login == "kohei3110"


async def test_broker_rejects_unconfirmed_assignment():
    class IgnoredAssignmentGitHub(FakeGitHubClient):
        async def assign_copilot(
            self,
            issue_number: int,
            custom_instructions: str,
        ) -> GitHubIssue:
            assert self.issue
            self.assign_calls += 1
            return self.issue

    github = IgnoredAssignmentGitHub()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())

    result = await broker.submit(
        SreHandoffRequest.model_validate(_handoff_payload())
    )

    assert result.state is HandoffState.INCOMPLETE
    assert result.copilot_assigned is False


async def test_broker_rejects_non_draft_or_closed_pull_request():
    github = FakeGitHubClient()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())
    submitted = await broker.submit(
        SreHandoffRequest.model_validate(_handoff_payload())
    )
    github.pull_request = GitHubPullRequest(
        number=24,
        html_url="https://github.com/example/repo/pull/24",
        draft=False,
        state="open",
        user={"login": "Copilot"},
        base={
            "ref": "main",
            "repo": {
                "full_name": "kohei3110/foundry-appinsights-llm-ops-demo"
            },
        },
    )

    result = await broker.get_status(submitted.issue_number)

    assert result.state is HandoffState.COPILOT_ALREADY_ASSIGNED
    assert result.pull_request_url is None


def test_unauthenticated_malformed_body_is_rejected_before_parsing():
    github = FakeGitHubClient()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())
    with TestClient(create_app(_settings(), broker=broker)) as client:
        response = client.post(
            "/api/handoffs",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 401


def test_authenticated_oversized_body_is_rejected_before_parsing():
    github = FakeGitHubClient()
    broker = HandoffBroker(_settings(), github, FakeAlertVerifier())
    with TestClient(create_app(_settings(), broker=broker)) as client:
        response = client.post(
            "/api/handoffs",
            content=b"x" * 40_000,
            headers={
                "Authorization": "Bearer handoff-secret",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 413


class FakeAccessToken:
    token = "azure-token"


class FakeAzureCredential:
    def get_token(self, *scopes, **kwargs):
        return FakeAccessToken()

    def close(self) -> None:
        return None


async def test_alert_verifier_requires_fired_matching_application_alert():
    target = _settings().applicationinsights_resource_id

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer azure-token"
        assert request.url.path == (
            "/subscriptions/11111111-1111-4111-8111-111111111111/"
            "providers/Microsoft.AlertsManagement/alerts/"
            "22222222-2222-4222-8222-222222222222"
        )
        return httpx.Response(
            200,
            json={
                "properties": {
                    "essentials": {
                        "alertRule": (
                            "/subscriptions/sub/resourceGroups/rg/providers/"
                            "microsoft.insights/scheduledqueryrules/"
                            "llmops-live-tool-failure"
                        ),
                        "monitorCondition": "Fired",
                        "severity": "Sev2",
                        "startDateTime": datetime.now(timezone.utc).isoformat(),
                        "targetResourceIds": [target],
                    }
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        verifier = AzureAlertVerifier(
            _settings(),
            FakeAzureCredential(),
            client=http_client,
        )
        await verifier.verify(
            str(_handoff_payload()["alert_id"]),
            AlertRule.TOOL_FAILURE,
        )


async def test_alert_verifier_rejects_rule_mismatch():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "properties": {
                    "essentials": {
                        "alertRule": "another-rule",
                        "monitorCondition": "Fired",
                        "severity": "Sev2",
                        "startDateTime": datetime.now(timezone.utc).isoformat(),
                        "targetResourceIds": [
                            _settings().applicationinsights_resource_id
                        ],
                    }
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        verifier = AzureAlertVerifier(
            _settings(),
            FakeAzureCredential(),
            client=http_client,
        )
        with pytest.raises(
            HandoffVerificationError,
            match="does not match",
        ):
            await verifier.verify(
                str(_handoff_payload()["alert_id"]),
                AlertRule.TOOL_FAILURE,
            )


async def test_alert_verifier_uses_recent_resolution_time():
    now = datetime.now(timezone.utc)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "properties": {
                    "essentials": {
                        "alertRule": "llmops-live-tool-failure",
                        "monitorCondition": "Resolved",
                        "severity": "Sev2",
                        "startDateTime": "2026-01-01T00:00:00Z",
                        "monitorConditionResolvedDateTime": now.isoformat(),
                        "targetResource": (
                            _settings().applicationinsights_resource_id
                        ),
                    }
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        verifier = AzureAlertVerifier(
            _settings(),
            FakeAzureCredential(),
            client=http_client,
        )
        await verifier.verify(
            str(_handoff_payload()["alert_id"]),
            AlertRule.TOOL_FAILURE,
        )
