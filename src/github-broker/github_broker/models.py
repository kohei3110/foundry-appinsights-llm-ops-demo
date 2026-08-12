from __future__ import annotations

from hashlib import sha256
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

COPILOT_RESPONSE_IDENTITIES = {
    "copilot",
    "app/copilot-swe-agent",
    "copilot-swe-agent",
    "copilot-swe-agent[bot]",
}


class AlertRule(str, Enum):
    STALE_POLICY = "llmops-live-stale-policy"
    TOOL_FAILURE = "llmops-live-tool-failure"


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceCode(str, Enum):
    SUPERSEDED_POLICY_SELECTED = "superseded_policy_selected"
    MODEL_CALL_SUCCEEDED = "model_call_succeeded"
    CONTAINER_REVISION_HEALTHY = "container_revision_healthy"
    REQUEST_STATUS_DEPENDENCY_FAILED = "request_status_dependency_failed"
    STRUCTURED_FAILURE_RETURNED = "structured_failure_returned"


class HypothesisCode(str, Enum):
    MODEL_FAILURE = "model_failure"
    CONTAINER_PLATFORM_FAILURE = "container_platform_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    REQUEST_STATUS_FAILURE = "request_status_failure"
    RETRIEVAL_FAILURE = "retrieval_failure"


class RootCauseCode(str, Enum):
    STALE_POLICY_FILTER_MISSING = "stale_policy_filter_missing"
    REQUEST_STATUS_DEPENDENCY_UNAVAILABLE = (
        "request_status_dependency_unavailable"
    )


_ROOT_CAUSE_BY_RULE = {
    AlertRule.STALE_POLICY: RootCauseCode.STALE_POLICY_FILTER_MISSING,
    AlertRule.TOOL_FAILURE: RootCauseCode.REQUEST_STATUS_DEPENDENCY_UNAVAILABLE,
}
_ROOT_HYPOTHESIS = {
    RootCauseCode.STALE_POLICY_FILTER_MISSING: HypothesisCode.RETRIEVAL_FAILURE,
    RootCauseCode.REQUEST_STATUS_DEPENDENCY_UNAVAILABLE: (
        HypothesisCode.REQUEST_STATUS_FAILURE
    ),
}
_EVIDENCE_BY_RULE = {
    AlertRule.STALE_POLICY: {
        EvidenceCode.SUPERSEDED_POLICY_SELECTED,
        EvidenceCode.MODEL_CALL_SUCCEEDED,
        EvidenceCode.CONTAINER_REVISION_HEALTHY,
    },
    AlertRule.TOOL_FAILURE: {
        EvidenceCode.REQUEST_STATUS_DEPENDENCY_FAILED,
        EvidenceCode.STRUCTURED_FAILURE_RETURNED,
        EvidenceCode.CONTAINER_REVISION_HEALTHY,
    },
}


class SreHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["live"]
    alert_id: str = Field(
        min_length=80,
        max_length=700,
        pattern=(
            r"(?i)^/subscriptions/[0-9a-f-]+/(?:.+/)?"
            r"providers/Microsoft\.AlertsManagement/alerts/[0-9a-f-]+$"
        ),
    )
    alert_rule: AlertRule
    investigation_fingerprint: str = Field(pattern=r"^[0-9a-f]{12}$")
    evidence_codes: list[EvidenceCode] = Field(min_length=1, max_length=3)
    ruled_out_codes: list[HypothesisCode] = Field(min_length=1, max_length=3)
    root_cause_code: RootCauseCode
    confidence_band: ConfidenceBand

    @model_validator(mode="after")
    def validate_code_consistency(self) -> "SreHandoffRequest":
        if self.root_cause_code is not _ROOT_CAUSE_BY_RULE[self.alert_rule]:
            raise ValueError("root cause code does not match the alert rule")
        if not set(self.evidence_codes).issubset(
            _EVIDENCE_BY_RULE[self.alert_rule]
        ):
            raise ValueError("evidence code does not match the alert rule")
        if _ROOT_HYPOTHESIS[self.root_cause_code] in self.ruled_out_codes:
            raise ValueError("root cause cannot also be ruled out")
        fingerprint_input = "|".join(
            [
                self.alert_rule.value,
                self.root_cause_code.value,
                ",".join(sorted(code.value for code in self.evidence_codes)),
            ]
        )
        expected_fingerprint = sha256(
            fingerprint_input.encode("ascii")
        ).hexdigest()[:12]
        if self.investigation_fingerprint != expected_fingerprint:
            raise ValueError("investigation fingerprint is not deterministic")
        return self


class HandoffState(str, Enum):
    COPILOT_ASSIGNED = "copilot_assigned"
    COPILOT_ALREADY_ASSIGNED = "copilot_already_assigned"
    DRAFT_PR_READY = "draft_pr_ready"
    INCOMPLETE = "incomplete"


class HandoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: HandoffState
    issue_number: int
    issue_url: str
    issue_created: bool
    copilot_assigned: bool
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    pull_request_is_draft: bool | None = None


class GitHubIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    html_url: str
    title: str
    body: str | None = None
    user: dict[str, object] = Field(default_factory=dict)
    assignees: list[dict[str, object]] = Field(default_factory=list)
    pull_request: dict[str, object] | None = None

    @property
    def copilot_assigned(self) -> bool:
        logins = {
            str(assignee.get("login", "")).lower()
            for assignee in self.assignees
        }
        return bool(COPILOT_RESPONSE_IDENTITIES & logins)

    @property
    def creator_login(self) -> str:
        return str(self.user.get("login", "")).lower()


class GitHubPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    html_url: str
    draft: bool = True
    state: Literal["open", "closed"] = "open"
    user: dict[str, object] = Field(default_factory=dict)
    base: dict[str, object] = Field(default_factory=dict)

    @property
    def author_login(self) -> str:
        return str(self.user.get("login", "")).lower()

    @property
    def base_ref(self) -> str:
        return str(self.base.get("ref", ""))

    @property
    def base_repository(self) -> str:
        repository = self.base.get("repo")
        return (
            str(repository.get("full_name", "")).lower()
            if isinstance(repository, dict)
            else ""
        )
