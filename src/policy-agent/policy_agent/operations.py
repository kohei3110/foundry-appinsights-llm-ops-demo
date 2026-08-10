from __future__ import annotations

import asyncio
import json
import re
from time import perf_counter
from typing import Protocol
from uuid import uuid4

import httpx
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import ValidationError

from policy_agent.config import Settings
from policy_agent.credentials import build_credential
from policy_agent.models import (
    AnswerStatus,
    DecisionStatus,
    ErrorDetail,
    InvestigationEvidence,
    InvestigationScenario,
    ObservabilityDecision,
    OperationsRequest,
    OperationsResponse,
    RecommendedAction,
    RuntimeMode,
    SreInvestigationReport,
)
from policy_agent.providers import ProviderError
from policy_agent.telemetry import current_trace_id, get_tracer


class OperationsProvider(Protocol):
    mode: RuntimeMode

    async def investigate(
        self,
        request: OperationsRequest,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[SreInvestigationReport, ObservabilityDecision]: ...


class SimulationOperationsProvider:
    mode = RuntimeMode.SIMULATION

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def investigate(
        self,
        request: OperationsRequest,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[SreInvestigationReport, ObservabilityDecision]:
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "invoke_agent azure-sre-agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "azure-sre-agent",
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.provider.name": "llmops.simulation",
                "llmops.mode": self.mode.value,
                "llmops.scenario": request.scenario.value,
                "llmops.operations.stage": "investigation",
            },
        ):
            investigation = self._build_investigation(request, trace_id)

        with tracer.start_as_current_span(
            "invoke_agent azure-observability-agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "azure-observability-agent",
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.provider.name": "llmops.simulation",
                "llmops.mode": self.mode.value,
                "llmops.scenario": request.scenario.value,
                "llmops.operations.stage": "next_action",
                "llmops.human_approval_required": True,
            },
        ):
            decision = self._build_decision(request, investigation)
        return investigation, decision

    def _build_investigation(
        self, request: OperationsRequest, trace_id: str
    ) -> SreInvestigationReport:
        shared = {
            "investigation_id": f"sreinv_{uuid4().hex}",
            "thread_id": f"simthread_{uuid4().hex}",
            "affected_resources": [
                "policy-agent",
                "ca-web",
                "Application Insights",
            ],
            "portal_url": None,
        }
        if request.scenario is InvestigationScenario.STALE_POLICY:
            return SreInvestigationReport(
                **shared,
                summary="回答品質の低下は、廃止済み規程の選択と時間的に一致しています。",
                evidence=[
                    InvestigationEvidence(
                        signal="policy_version",
                        observation="retrieve_policy が 2025-04-01 版を選択",
                        source="Application Insights dependencies",
                        correlation_id=trace_id,
                    ),
                    InvestigationEvidence(
                        signal="policy_status",
                        observation="llmops.policy.status=superseded",
                        source="OpenTelemetry span attributes",
                        correlation_id=trace_id,
                    ),
                    InvestigationEvidence(
                        signal="model_health",
                        observation="chat スパンは成功し、モデルエラーなし",
                        source="Application Insights dependencies",
                        correlation_id=trace_id,
                    ),
                ],
                ruled_out_hypotheses=[
                    "モデルデプロイ障害",
                    "request-status ツール障害",
                    "Container Apps のリソース枯渇",
                ],
                probable_root_cause="検索時の現行版フィルターが適用されず、廃止済み規程が順位1位になった。",
                confidence=0.97,
            )
        if request.scenario is InvestigationScenario.SLOW_TOOL:
            return SreInvestigationReport(
                **shared,
                summary="エンドツーエンド遅延の大部分を request-status ツールが占めています。",
                evidence=[
                    InvestigationEvidence(
                        signal="execute_tool_duration",
                        observation="request_status の所要時間が約1.2秒",
                        source="Application Insights dependencies",
                        correlation_id=trace_id,
                    ),
                    InvestigationEvidence(
                        signal="model_duration",
                        observation="モデル呼び出しは通常範囲",
                        source="Application Insights dependencies",
                        correlation_id=trace_id,
                    ),
                    InvestigationEvidence(
                        signal="container_health",
                        observation="再起動、CPU、メモリ異常なし",
                        source="Azure Monitor metrics",
                    ),
                ],
                ruled_out_hypotheses=[
                    "モデル推論の性能低下",
                    "Container Apps のコールドスタート",
                    "検索処理の遅延",
                ],
                probable_root_cause="request-status 依存先の応答遅延がレイテンシ予算を超過した。",
                confidence=0.94,
            )
        return SreInvestigationReport(
            **shared,
            summary="request-status 依存先の制御された失敗が HTTP 502 と一致しています。",
            evidence=[
                InvestigationEvidence(
                    signal="dependency_failure",
                    observation="execute_tool が error.type=request_status_unavailable で失敗",
                    source="Application Insights dependencies",
                    correlation_id=trace_id,
                ),
                InvestigationEvidence(
                    signal="request_status",
                    observation="API が構造化エラー request_status_failure を返却",
                    source="Application Insights requests",
                    correlation_id=trace_id,
                ),
                InvestigationEvidence(
                    signal="platform_health",
                    observation="Container Apps リビジョンは稼働中",
                    source="Azure Resource Graph and Azure Monitor",
                ),
            ],
            ruled_out_hypotheses=[
                "Foundry モデル障害",
                "認証エラー",
                "Container Apps リビジョン障害",
            ],
            probable_root_cause="request-status 依存先が利用不能になり、必須ツール呼び出しが失敗した。",
            confidence=0.99,
        )

    def _build_decision(
        self,
        request: OperationsRequest,
        investigation: SreInvestigationReport,
    ) -> ObservabilityDecision:
        if request.scenario is InvestigationScenario.STALE_POLICY:
            actions = [
                RecommendedAction(
                    priority="P0",
                    title="現行規程のみを検索対象にする",
                    owner="Application team",
                    rationale="推定原因へ直接対応し、誤回答の再発を防ぐ。",
                    risk="検索結果が空になる可能性があるため、明示的な回答不能処理が必要。",
                    validation="stale_policy 回帰ケースと現行版選択率アラートを確認する。",
                ),
                RecommendedAction(
                    priority="P1",
                    title="廃止済み規程選択を品質アラート化する",
                    owner="SRE",
                    rationale="HTTP成功でも発生する品質劣化を早期検知する。",
                    risk="低トラフィック時の単発イベントで通知ノイズが出る。",
                    validation="最小件数条件と複数時間窓でアラートを検証する。",
                ),
            ]
            severity = "Sev2"
        elif request.scenario is InvestigationScenario.SLOW_TOOL:
            actions = [
                RecommendedAction(
                    priority="P1",
                    title="request-status にタイムアウトとレイテンシ予算を設定する",
                    owner="Integration team",
                    rationale="遅延を依存先境界で制限し、E2E SLOを保護する。",
                    risk="短いタイムアウトは正常な遅い応答を失敗扱いにする。",
                    validation="p95、timeout率、fallback率を同時に比較する。",
                ),
                RecommendedAction(
                    priority="P1",
                    title="execute_tool p95 の症状・原因アラートを追加する",
                    owner="SRE",
                    rationale="利用者影響と依存先遅延を同じインシデントへ関連付ける。",
                    risk="トラフィック不足時にパーセンタイルが不安定。",
                    validation="最小呼び出し件数を満たす負荷でアラートをテストする。",
                ),
            ]
            severity = "Sev3"
        else:
            actions = [
                RecommendedAction(
                    priority="P0",
                    title="request-status 依存先を切り離し、失敗を明示する",
                    owner="On-call",
                    rationale="障害を成功に見せず、影響範囲を限定する。",
                    risk="申請状況を含む回答が一時的に提供できない。",
                    validation="構造化502、相関ID、エラーメッセージを確認する。",
                ),
                RecommendedAction(
                    priority="P1",
                    title="依存先の復旧後に代表ケースと評価を再実行する",
                    owner="Application team",
                    rationale="可用性だけでなく回答品質とツール選択を確認する。",
                    risk="復旧直後の不安定な状態で評価が揺れる。",
                    validation="4回連続成功と評価器 pass を確認する。",
                ),
            ]
            severity = "Sev2"
        return ObservabilityDecision(
            status=DecisionStatus.RECOMMENDED,
            issue_id=f"simissue_{uuid4().hex}",
            issue_title=f"[policy-agent] {request.scenario.value} investigation",
            severity=severity,
            summary=(
                f"SRE Agent の証拠と推定原因を基に、{len(actions)}件の"
                "承認必須アクションを優先順位付けしました。"
            ),
            next_actions=actions,
            portal_url=None,
            autonomous=True,
        )


class SreAgentClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client
        self._credential = build_credential(settings)

    async def investigate(
        self, request: OperationsRequest, trace_id: str
    ) -> SreInvestigationReport:
        if (
            not self._settings.sre_agent_endpoint
            or not self._settings.sre_agent_name
        ):
            raise ProviderError(
                "sre_agent_not_configured",
                "Live operations require SRE_AGENT_ENDPOINT and SRE_AGENT_NAME",
            )
        token = await asyncio.to_thread(
            self._credential.get_token, "https://azuresre.dev/.default"
        )
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }
        prompt = self._build_prompt(request, trace_id)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                f"{self._settings.sre_agent_endpoint.rstrip('/')}"
                "/api/v1/threads",
                headers=headers,
                json={
                    "StartMessage": {
                        "Text": prompt,
                        "UserId": "llmops-web",
                        "DisplayName": "LLM Ops Web",
                        "Agent": self._settings.sre_agent_name,
                    }
                },
            )
            response.raise_for_status()
            thread_payload = response.json()
            thread_id = (
                thread_payload.get("Id") or thread_payload.get("id")
                if isinstance(thread_payload, dict)
                else None
            )
            if not thread_id:
                raise ProviderError(
                    "sre_agent_invalid_response",
                    "Azure SRE Agent did not return a thread ID",
                    retryable=True,
                )
            thread_id = str(thread_id)
            deadline = (
                asyncio.get_running_loop().time()
                + self._settings.operations_poll_timeout_seconds
            )
            while asyncio.get_running_loop().time() < deadline:
                messages = await client.get(
                    f"{self._settings.sre_agent_endpoint.rstrip('/')}"
                    f"/api/v1/threads/{thread_id}/messages",
                    headers=headers,
                )
                messages.raise_for_status()
                report = self._parse_report(messages.json(), thread_id)
                if report:
                    return report
                await asyncio.sleep(self._settings.operations_poll_interval_seconds)
            raise ProviderError(
                "sre_investigation_timeout",
                "Azure SRE Agent investigation did not complete before timeout",
                retryable=True,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "sre_agent_failure",
                f"Azure SRE Agent request failed: {exc}",
                retryable=True,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    def _build_prompt(self, request: OperationsRequest, trace_id: str) -> str:
        return (
            "Investigate the Microsoft Foundry policy-agent incident in read-only "
            "mode. Do not propose, approve, or execute write commands. "
            f"Scenario={request.scenario.value}; trace_id={trace_id}; "
            f"time_range_minutes={request.time_range_minutes}; "
            f"subscription_id={self._settings.azure_subscription_id or 'unknown'}; "
            f"resource_group={self._settings.azure_resource_group or 'unknown'}. "
            "First inspect the correlated Application Insights distributed trace. "
            "Then check the current Container App revision health only to rule out "
            "a platform failure. Use Activity Logs only if the trace indicates a "
            "platform or configuration change. Stop after collecting at most three "
            "direct evidence items. If evidence is unavailable, say so instead of "
            "guessing. Return one JSON object without Markdown, with "
            "summary, affected_resources, evidence (signal, observation, source, "
            "correlation_id), ruled_out_hypotheses, probable_root_cause, and "
            "confidence. Never include prompt or customer message content."
        )

    def _parse_report(
        self, payload: object, thread_id: str
    ) -> SreInvestigationReport | None:
        text = _latest_assistant_text(payload)
        if not text:
            return None
        data = _extract_json_object(text)
        if not data:
            return None
        data = _normalize_sre_report(data)
        data["investigation_id"] = data.get(
            "investigation_id", f"sreinv_{uuid4().hex}"
        )
        data["thread_id"] = thread_id
        data["portal_url"] = (
            "https://sre.azure.com"
            f"/#/agent/{self._settings.azure_subscription_id or ''}"
            f"/{self._settings.azure_resource_group or ''}"
            f"/{self._settings.sre_agent_name or ''}"
        )
        try:
            return SreInvestigationReport.model_validate(data)
        except ValidationError:
            return None


class ObservabilityIssueClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client
        self._credential = build_credential(settings)

    async def get_decision(
        self, request: OperationsRequest
    ) -> ObservabilityDecision:
        if not self._settings.azure_monitor_account_id:
            raise ProviderError(
                "observability_agent_not_configured",
                "Live operations require AZURE_MONITOR_ACCOUNT_ID",
            )
        token = await asyncio.to_thread(
            self._credential.get_token, "https://management.azure.com/.default"
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            next_url: str | None = (
                "https://management.azure.com"
                f"{self._settings.azure_monitor_account_id}/issues"
                "?api-version=2025-10-03"
            )
            matching_issue: dict | None = None
            while next_url:
                response = await client.get(
                    next_url,
                    headers={"Authorization": f"Bearer {token.token}"},
                )
                response.raise_for_status()
                page = response.json()
                for item in page.get("value", []):
                    title = str(
                        item.get("properties", {}).get("title", "")
                    ).lower()
                    if (
                        request.scenario.value.lower() in title
                        or "policy-agent" in title
                    ):
                        matching_issue = item
                        break
                if matching_issue:
                    break
                next_url = page.get("nextLink")
            if not matching_issue:
                return ObservabilityDecision(
                    status=DecisionStatus.AWAITING_ISSUE,
                    severity="Pending",
                    summary=(
                        "Observability Agent is correlating alerts. No matching "
                        "Azure Monitor issue is available yet. Open the linked "
                        "fired alert and select Investigate to run a human-initiated "
                        "deep investigation without changing workspace settings."
                    ),
                    next_actions=[],
                    portal_url=self._alerts_portal_url(),
                    autonomous=True,
                )
            issue = matching_issue
            properties = issue.get("properties", {})
            actions = _extract_recommended_actions(properties)
            return ObservabilityDecision(
                status=DecisionStatus.ISSUE_READY,
                issue_id=issue.get("id"),
                issue_title=properties.get("title"),
                severity=str(properties.get("severity", "Unknown")),
                summary=str(
                    properties.get("background")
                    or properties.get("summary")
                    or "Open the Azure Monitor issue to review the investigation."
                ),
                next_actions=actions,
                portal_url=self._portal_url(),
                autonomous=True,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "observability_agent_failure",
                f"Azure Monitor issue lookup failed: {exc}",
                retryable=True,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    def _portal_url(self) -> str:
        return (
            "https://portal.azure.com/#view/Microsoft_Azure_Monitoring/"
            "AzureMonitoringBrowseBlade/~/issues"
        )

    def _alerts_portal_url(self) -> str:
        return (
            "https://portal.azure.com/#view/Microsoft_Azure_Monitoring/"
            "AzureMonitoringBrowseBlade/~/alertsV2"
        )


class LiveOperationsProvider:
    mode = RuntimeMode.LIVE

    def __init__(
        self,
        settings: Settings,
        *,
        sre_client: SreAgentClient | None = None,
        observability_client: ObservabilityIssueClient | None = None,
    ) -> None:
        self._settings = settings
        self._sre_client = sre_client or SreAgentClient(settings)
        self._observability_client = (
            observability_client or ObservabilityIssueClient(settings)
        )

    async def investigate(
        self,
        request: OperationsRequest,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[SreInvestigationReport, ObservabilityDecision]:
        if not self._settings.operations_live_configured:
            raise ProviderError(
                "operations_live_not_configured",
                "Live operations require SRE Agent and Observability Agent settings",
            )
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "invoke_agent azure-sre-agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": self._settings.sre_agent_name or "",
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.provider.name": "azure.sre",
                "llmops.mode": self.mode.value,
                "llmops.scenario": request.scenario.value,
                "llmops.operations.stage": "investigation",
            },
        ) as span:
            investigation = await self._sre_client.investigate(request, trace_id)
            span.set_attribute("llmops.sre.confidence", investigation.confidence)
            span.set_attribute(
                "llmops.sre.investigation_id", investigation.investigation_id
            )

        with tracer.start_as_current_span(
            "invoke_agent azure-observability-agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": self._settings.observability_agent_name or "",
                "gen_ai.conversation.id": conversation_id,
                "gen_ai.provider.name": "azure.monitor",
                "llmops.mode": self.mode.value,
                "llmops.scenario": request.scenario.value,
                "llmops.operations.stage": "next_action",
                "llmops.human_approval_required": True,
            },
        ) as span:
            decision = await self._observability_client.get_decision(request)
            span.set_attribute(
                "llmops.observability.decision_status", decision.status.value
            )
            span.set_attribute(
                "llmops.observability.action_count", len(decision.next_actions)
            )
        return investigation, decision


class OperationsService:
    def __init__(self, providers: dict[RuntimeMode, OperationsProvider]) -> None:
        self._providers = providers

    async def investigate(self, request: OperationsRequest) -> OperationsResponse:
        conversation_id = request.conversation_id or f"opsconv_{uuid4().hex}"
        requested_trace_id = request.trace_id
        started = perf_counter()
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "llmops.operations.request",
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": "POST",
                "http.route": "/api/operations/investigate",
                "gen_ai.conversation.id": conversation_id,
                "llmops.mode": request.mode.value,
                "llmops.scenario": request.scenario.value,
                "llmops.human_approval_required": True,
            },
        ) as span:
            correlation_trace_id = requested_trace_id or current_trace_id()
            try:
                provider = self._providers[request.mode]
                investigation, decision = await provider.investigate(
                    request, conversation_id, correlation_trace_id
                )
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                span.set_attribute("llmops.elapsed_ms", elapsed_ms)
                span.set_attribute(
                    "llmops.sre.investigation_id",
                    investigation.investigation_id,
                )
                return OperationsResponse(
                    status=AnswerStatus.OK,
                    mode=request.mode,
                    scenario=request.scenario,
                    trace_id=correlation_trace_id,
                    conversation_id=conversation_id,
                    elapsed_ms=elapsed_ms,
                    investigation=investigation,
                    decision=decision,
                )
            except ProviderError as exc:
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                span.set_attribute("error.type", exc.code)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                return OperationsResponse(
                    status=AnswerStatus.ERROR,
                    mode=request.mode,
                    scenario=request.scenario,
                    trace_id=correlation_trace_id,
                    conversation_id=conversation_id,
                    elapsed_ms=elapsed_ms,
                    error=ErrorDetail(
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                    ),
                )


def _latest_assistant_text(payload: object) -> str | None:
    values = payload
    if isinstance(payload, dict):
        values = payload.get("value") or payload.get("messages")
        if values is None and isinstance(payload.get("results"), dict):
            values = payload["results"].get("messages")
    if not isinstance(values, list):
        return None
    for item in reversed(values):
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        author_role = author.get("role") if isinstance(author, dict) else None
        role = str(
            item.get("role") or item.get("sender") or author_role or ""
        ).lower()
        if role not in {"assistant", "agent", "sreagent"}:
            continue
        content = item.get("content") or item.get("message") or item.get("text")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
    return None


def _extract_json_object(text: str) -> dict | None:
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = match.group(1) if match else text[text.find("{") : text.rfind("}") + 1]
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _normalize_sre_report(data: dict) -> dict:
    normalized = dict(data)

    resources = normalized.get("affected_resources")
    if isinstance(resources, list):
        normalized["affected_resources"] = [
            label
            for item in resources
            if (
                label := (
                    item
                    if isinstance(item, str)
                    else (
                        item.get("resource_name")
                        or item.get("name")
                        or item.get("resource_id")
                        if isinstance(item, dict)
                        else None
                    )
                )
            )
        ]

    hypotheses = normalized.get("ruled_out_hypotheses")
    if isinstance(hypotheses, list):
        normalized_hypotheses = []
        for item in hypotheses:
            if isinstance(item, str):
                normalized_hypotheses.append(item)
            elif isinstance(item, dict):
                hypothesis = item.get("hypothesis") or item.get("title")
                reason = item.get("reason")
                if hypothesis:
                    normalized_hypotheses.append(
                        f"{hypothesis}: {reason}" if reason else str(hypothesis)
                    )
        normalized["ruled_out_hypotheses"] = normalized_hypotheses

    confidence = normalized.get("confidence")
    if isinstance(confidence, str):
        try:
            normalized["confidence"] = float(confidence)
        except ValueError:
            confidence_key = confidence.lower().replace("_", "-").replace(" ", "-")
            confidence_scores = {
                "very-high": 0.95,
                "high": 0.9,
                "medium-high": 0.8,
                "medium": 0.6,
                "medium-low": 0.4,
                "low": 0.25,
            }
            if confidence_key in confidence_scores:
                normalized["confidence"] = confidence_scores[confidence_key]

    return normalized


def _extract_recommended_actions(properties: dict) -> list[RecommendedAction]:
    candidates = properties.get("recommendedNextSteps", [])
    if not candidates and isinstance(properties.get("investigation"), dict):
        candidates = properties["investigation"].get("recommendedNextSteps", [])
    if not isinstance(candidates, list):
        return []
    actions = []
    for index, candidate in enumerate(candidates):
        if isinstance(candidate, str):
            candidate = {"title": candidate}
        if not isinstance(candidate, dict):
            continue
        actions.append(
            RecommendedAction(
                priority=str(candidate.get("priority", f"P{index + 1}")),
                title=str(candidate.get("title") or candidate.get("action") or ""),
                owner=str(candidate.get("owner", "On-call")),
                rationale=str(candidate.get("rationale", "Observability Agent finding")),
                risk=str(candidate.get("risk", "Review before execution")),
                validation=str(
                    candidate.get("validation", "Validate SLO and representative cases")
                ),
            )
        )
    return [action for action in actions if action.title]
