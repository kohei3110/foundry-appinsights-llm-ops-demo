from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol

import httpx
from azure.core.credentials import TokenCredential
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from github_broker.config import BrokerSettings
from github_broker.models import AlertRule


class HandoffVerificationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AlertVerifierProtocol(Protocol):
    async def verify(self, alert_id: str, alert_rule: AlertRule) -> None: ...


def build_credential(settings: BrokerSettings) -> TokenCredential:
    if settings.is_azure:
        return ManagedIdentityCredential(
            client_id=settings.managed_identity_client_id
        )
    return DefaultAzureCredential()


class AzureAlertVerifier:
    def __init__(
        self,
        settings: BrokerSettings,
        credential: TokenCredential,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        await asyncio.to_thread(self._credential.close)

    async def verify(self, alert_id: str, alert_rule: AlertRule) -> None:
        expected_prefix = (
            f"/subscriptions/{self._settings.azure_subscription_id}/"
        ).lower()
        if not alert_id.lower().startswith(expected_prefix):
            raise HandoffVerificationError(
                "alert_scope_mismatch",
                "Alert is outside the configured subscription",
                retryable=False,
            )
        try:
            token = await asyncio.to_thread(
                self._credential.get_token,
                "https://management.azure.com/.default",
            )
            alert_uuid = alert_id.rsplit("/", 1)[-1]
            canonical_url = (
                "https://management.azure.com/subscriptions/"
                f"{self._settings.azure_subscription_id}/providers/"
                "Microsoft.AlertsManagement/alerts/"
                f"{alert_uuid}"
            )
            response = await self._client.get(
                canonical_url,
                params={"api-version": "2019-05-05-preview"},
                headers={"Authorization": f"Bearer {token.token}"},
            )
        except (httpx.HTTPError, AzureError) as exc:
            raise HandoffVerificationError(
                "alert_verification_unavailable",
                "Azure Monitor alert verification failed",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise HandoffVerificationError(
                "alert_verification_rejected",
                f"Azure Monitor returned HTTP {response.status_code}",
                retryable=response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HandoffVerificationError(
                "alert_invalid_response",
                "Azure Monitor alert response is not valid JSON",
                retryable=True,
            ) from exc
        essentials = (
            payload.get("properties", {}).get("essentials", {})
            if isinstance(payload, dict)
            else {}
        )
        if not isinstance(essentials, dict):
            raise HandoffVerificationError(
                "alert_invalid_response",
                "Azure Monitor alert essentials are missing",
                retryable=True,
            )
        returned_rule = str(essentials.get("alertRule", ""))
        condition = str(essentials.get("monitorCondition", ""))
        severity = str(essentials.get("severity", ""))
        started_at = str(essentials.get("startDateTime", ""))
        resolved_at = str(
            essentials.get("monitorConditionResolvedDateTime", "")
        )
        target_ids = essentials.get("targetResourceIds", [])
        if not isinstance(target_ids, list):
            target_ids = []
        target_resource = essentials.get("targetResource")
        if isinstance(target_resource, str):
            target_ids.append(target_resource)
        normalized_rule = returned_rule.lower()
        expected_rule_name = alert_rule.value.lower()
        if (
            normalized_rule != expected_rule_name
            and not normalized_rule.endswith(
                f"/scheduledqueryrules/{expected_rule_name}"
            )
        ):
            raise HandoffVerificationError(
                "alert_rule_mismatch",
                "Azure Monitor alert rule does not match the handoff",
                retryable=False,
            )
        if (
            condition.lower() not in {"fired", "resolved"}
            or severity.lower() != "sev2"
        ):
            raise HandoffVerificationError(
                "alert_not_actionable",
                "Azure Monitor alert is not a Sev2 incident",
                retryable=False,
            )
        provenance_time = (
            resolved_at if condition.lower() == "resolved" else started_at
        )
        try:
            started = datetime.fromisoformat(
                provenance_time.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HandoffVerificationError(
                "alert_invalid_response",
                "Azure Monitor alert start time is invalid",
                retryable=True,
            ) from exc
        age_seconds = (datetime.now(timezone.utc) - started).total_seconds()
        if age_seconds < 0 or age_seconds > (
            self._settings.max_alert_age_minutes * 60
        ):
            raise HandoffVerificationError(
                "alert_expired",
                "Azure Monitor alert is outside the broker time window",
                retryable=False,
            )
        expected_target = (
            self._settings.applicationinsights_resource_id or ""
        ).lower()
        expected_resource_group_segment = (
            f"/resourcegroups/{self._settings.azure_resource_group}/"
        ).lower()
        if expected_resource_group_segment not in expected_target:
            raise HandoffVerificationError(
                "broker_scope_invalid",
                "Configured Application Insights resource group is inconsistent",
                retryable=False,
            )
        normalized_targets = {
            str(resource_id).lower()
            for resource_id in target_ids
            if isinstance(resource_id, str)
        }
        if expected_target not in normalized_targets:
            raise HandoffVerificationError(
                "alert_target_mismatch",
                "Azure Monitor alert does not target the configured application",
                retryable=False,
            )
