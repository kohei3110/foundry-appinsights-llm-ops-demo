from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from policy_agent.models import (
    AnswerStatus,
    DecisionStatus,
    InvestigationEvidence,
    InvestigationScenario,
    ObservabilityDecision,
    OperationsRequest,
    RecommendedAction,
    RuntimeMode,
    SreInvestigationReport,
)
from policy_agent.operations import (
    LiveOperationsProvider,
    ObservabilityIssueClient,
    OperationsService,
    SreAgentClient,
    SimulationOperationsProvider,
)
from policy_agent.providers import ProviderError
from policy_agent.telemetry import configure_telemetry


async def test_simulation_operations_cover_every_incident(settings):
    service = OperationsService(
        {RuntimeMode.SIMULATION: SimulationOperationsProvider(settings)}
    )

    for scenario in InvestigationScenario:
        response = await service.investigate(
            OperationsRequest(mode=RuntimeMode.SIMULATION, scenario=scenario)
        )

        assert response.status is AnswerStatus.OK
        assert response.investigation.confidence >= 0.9
        assert response.investigation.evidence
        assert response.investigation.ruled_out_hypotheses
        assert response.decision.status is DecisionStatus.RECOMMENDED
        assert response.decision.next_actions
        assert all(
            action.approval_required for action in response.decision.next_actions
        )


class FakeSreClient:
    async def investigate(self, request, trace_id):
        return SreInvestigationReport(
            investigation_id="sreinv_live",
            thread_id="thread_live",
            summary="Live evidence collected",
            affected_resources=["policy-agent"],
            evidence=[
                InvestigationEvidence(
                    signal="dependency_failure",
                    observation="request-status failed",
                    source="Application Insights",
                    correlation_id=trace_id,
                )
            ],
            ruled_out_hypotheses=["model failure"],
            probable_root_cause="request-status unavailable",
            confidence=0.98,
        )


class FakeObservabilityClient:
    async def get_decision(self, request):
        return ObservabilityDecision(
            status=DecisionStatus.ISSUE_READY,
            issue_id="issue_live",
            issue_title="[policy-agent] tool failure",
            severity="Sev2",
            summary="Human review required",
            next_actions=[
                RecommendedAction(
                    priority="P0",
                    title="Isolate dependency",
                    owner="On-call",
                    rationale="Limit impact",
                    risk="Status unavailable",
                    validation="Run representative case",
                )
            ],
            autonomous=True,
        )


async def test_live_operations_return_real_agent_results(settings):
    configured = settings.model_copy(
        update={
            "sre_agent_name": "llmops-sre",
            "sre_agent_endpoint": "https://example.eastus2.azuresre.ai",
            "azure_monitor_account_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Monitor/accounts/amw"
            ),
            "observability_agent_name": "llmops-observability",
        }
    )
    provider = LiveOperationsProvider(
        configured,
        sre_client=FakeSreClient(),
        observability_client=FakeObservabilityClient(),
    )
    service = OperationsService({RuntimeMode.LIVE: provider})

    response = await service.investigate(
        OperationsRequest(
            mode=RuntimeMode.LIVE,
            scenario=InvestigationScenario.TOOL_FAILURE,
            trace_id="1234567890abcdef1234567890abcdef",
        )
    )

    assert response.status is AnswerStatus.OK
    assert response.investigation.thread_id == "thread_live"
    assert response.decision.issue_id == "issue_live"
    assert response.decision.next_actions[0].approval_required is True


class FailingSreClient:
    async def investigate(self, request, trace_id):
        raise ProviderError(
            "sre_agent_failure", "synthetic SRE outage", retryable=True
        )


class UnexpectedObservabilityClient:
    called = False

    async def get_decision(self, request):
        self.called = True
        raise AssertionError("Observability Agent must not run without SRE evidence")


async def test_live_sre_failure_never_uses_simulation(settings):
    configured = settings.model_copy(
        update={
            "sre_agent_name": "llmops-sre",
            "sre_agent_endpoint": "https://example.eastus2.azuresre.ai",
            "azure_monitor_account_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Monitor/accounts/amw"
            ),
            "observability_agent_name": "llmops-observability",
        }
    )
    observability = UnexpectedObservabilityClient()
    provider = LiveOperationsProvider(
        configured,
        sre_client=FailingSreClient(),
        observability_client=observability,
    )
    service = OperationsService({RuntimeMode.LIVE: provider})

    response = await service.investigate(
        OperationsRequest(mode=RuntimeMode.LIVE)
    )

    assert response.status is AnswerStatus.ERROR
    assert response.error.code == "sre_agent_failure"
    assert "synthetic SRE outage" in response.error.message
    assert observability.called is False


async def test_operations_emit_agent_stage_attributes(settings):
    configure_telemetry(settings)
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    service = OperationsService(
        {RuntimeMode.SIMULATION: SimulationOperationsProvider(settings)}
    )

    await service.investigate(
        OperationsRequest(
            scenario=InvestigationScenario.STALE_POLICY,
            conversation_id="opsconv_telemetry",
        )
    )

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "llmops.operations.request" in spans
    assert "invoke_agent azure-sre-agent" in spans
    assert "invoke_agent azure-observability-agent" in spans
    assert (
        spans["invoke_agent azure-sre-agent"].attributes[
            "llmops.operations.stage"
        ]
        == "investigation"
    )
    assert (
        spans["invoke_agent azure-observability-agent"].attributes[
            "llmops.human_approval_required"
        ]
        is True
    )


def test_malformed_sre_json_is_treated_as_incomplete(settings):
    client = SreAgentClient(settings)

    report = client._parse_report(
        {
            "value": [
                {
                    "role": "assistant",
                    "content": '{"summary":"still building report"}',
                }
            ]
        },
        "thread_incomplete",
    )

    assert report is None


def test_sre_report_parser_supports_live_message_envelope(settings):
    client = SreAgentClient(settings)

    report = client._parse_report(
        {
            "threadId": "thread_live",
            "messages": [
                {
                    "author": {"role": "SREAgent"},
                    "text": (
                        '{"summary":"Live evidence collected",'
                        '"affected_resources":[{"resource_name":"policy-agent",'
                        '"resource_id":"/subscriptions/sub/policy-agent"}],'
                        '"evidence":[{"signal":"dependency_failure",'
                        '"observation":"request-status failed",'
                        '"source":"Application Insights"}],'
                        '"ruled_out_hypotheses":[{"hypothesis":"model failure",'
                        '"reason":"model span succeeded"}],'
                        '"probable_root_cause":"request-status unavailable",'
                        '"confidence":"medium-high"}'
                    ),
                    "isComplete": True,
                }
            ],
        },
        "thread_live",
    )

    assert report is not None
    assert report.thread_id == "thread_live"
    assert report.affected_resources == ["policy-agent"]
    assert report.evidence[0].signal == "dependency_failure"
    assert report.ruled_out_hypotheses == [
        "model failure: model span succeeded"
    ]
    assert report.confidence == 0.8


class FakeToken:
    token = "test-token"


class FakeCredential:
    def get_token(self, scope):
        return FakeToken()


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class SreHttpClient:
    def __init__(self):
        self.post_url = None
        self.post_headers = None
        self.post_body = None

    async def post(self, url, headers, json):
        self.post_url = url
        self.post_headers = headers
        self.post_body = json
        return FakeResponse({"Id": "338535a2-4f3f-4f2a-83d5-8797bdb63aeb"})

    async def get(self, url, headers):
        return FakeResponse(
            {
                "messages": [
                    {
                        "author": {"role": "SREAgent"},
                        "text": (
                            '{"summary":"Live evidence collected",'
                            '"affected_resources":["policy-agent"],'
                            '"evidence":[{"signal":"dependency_failure",'
                            '"observation":"request-status failed",'
                            '"source":"Application Insights"}],'
                            '"ruled_out_hypotheses":["model failure"],'
                            '"probable_root_cause":"request-status unavailable",'
                            '"confidence":0.98}'
                        ),
                    }
                ]
            }
        )


async def test_sre_client_creates_thread_with_supported_contract(settings):
    configured = settings.model_copy(
        update={
            "sre_agent_endpoint": "https://example.azuresre.ai",
            "sre_agent_name": "llmops-sre",
        }
    )
    http_client = SreHttpClient()
    client = SreAgentClient(configured, client=http_client)
    client._credential = FakeCredential()

    report = await client.investigate(
        OperationsRequest(scenario=InvestigationScenario.TOOL_FAILURE),
        "1234567890abcdef1234567890abcdef",
    )

    assert report.summary == "Live evidence collected"
    assert http_client.post_url == "https://example.azuresre.ai/api/v1/threads"
    assert http_client.post_body == {
        "StartMessage": {
            "Text": client._build_prompt(
                OperationsRequest(
                    scenario=InvestigationScenario.TOOL_FAILURE
                ),
                "1234567890abcdef1234567890abcdef",
            ),
            "UserId": "llmops-web",
            "DisplayName": "LLM Ops Web",
            "Agent": "llmops-sre",
        }
    }
    assert (
        http_client.post_headers["Authorization"]
        == "Bearer test-token"
    )


class PaginatedIssueHttpClient:
    def __init__(self):
        self.urls = []
        self.headers = []

    async def get(self, url, headers):
        self.urls.append(url)
        self.headers.append(headers)
        if len(self.urls) == 1:
            return FakeResponse(
                {
                    "value": [],
                    "nextLink": "https://management.azure.com/next-page",
                }
            )
        return FakeResponse(
            {
                "value": [
                    {
                        "id": "issue-page-2",
                        "properties": {
                            "title": "[policy-agent] tool_failure",
                            "severity": "Sev2",
                            "status": "New",
                        },
                    }
                ]
            }
        )


async def test_observability_issue_lookup_follows_next_link(settings):
    configured = settings.model_copy(
        update={
            "azure_monitor_account_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Monitor/accounts/amw"
            )
        }
    )
    http_client = PaginatedIssueHttpClient()
    client = ObservabilityIssueClient(configured, client=http_client)
    client._credential = FakeCredential()

    decision = await client.get_decision(
        OperationsRequest(scenario=InvestigationScenario.TOOL_FAILURE)
    )

    assert decision.status is DecisionStatus.ISSUE_READY
    assert decision.issue_id == "issue-page-2"
    assert len(http_client.urls) == 2
    assert all(
        headers["Authorization"] == "Bearer test-token"
        for headers in http_client.headers
    )


async def test_missing_issue_links_to_on_demand_investigation(settings):
    configured = settings.model_copy(
        update={
            "azure_monitor_account_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Monitor/accounts/amw"
            )
        }
    )
    http_client = PaginatedIssueHttpClient()

    async def empty_get(url, headers):
        http_client.urls.append(url)
        http_client.headers.append(headers)
        return FakeResponse({"value": []})

    http_client.get = empty_get
    client = ObservabilityIssueClient(configured, client=http_client)
    client._credential = FakeCredential()

    decision = await client.get_decision(
        OperationsRequest(scenario=InvestigationScenario.TOOL_FAILURE)
    )

    assert decision.status is DecisionStatus.AWAITING_ISSUE
    assert decision.next_actions == []
    assert decision.portal_url.endswith("/~/alertsV2")
    assert "human-initiated deep investigation" in decision.summary
