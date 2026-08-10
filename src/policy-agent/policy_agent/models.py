from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RuntimeMode(str, Enum):
    SIMULATION = "simulation"
    LIVE = "live"


class Scenario(str, Enum):
    HEALTHY = "healthy"
    STALE_POLICY = "stale_policy"
    SLOW_TOOL = "slow_tool"
    TOOL_FAILURE = "tool_failure"


class InvestigationScenario(str, Enum):
    STALE_POLICY = "stale_policy"
    SLOW_TOOL = "slow_tool"
    TOOL_FAILURE = "tool_failure"


class AnswerStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class DecisionStatus(str, Enum):
    RECOMMENDED = "recommended"
    ISSUE_READY = "issue_ready"
    AWAITING_ISSUE = "awaiting_issue"


class Source(BaseModel):
    document_id: str
    title: str
    version: str
    status: str
    path: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: RuntimeMode = RuntimeMode.SIMULATION
    scenario: Scenario = Scenario.HEALTHY
    conversation_id: str | None = Field(default=None, max_length=128)
    request_id: str = Field(default="REQ-2026-0042", min_length=1, max_length=128)


class ProviderResult(BaseModel):
    answer: str
    sources: list[Source]
    response_id: str
    input_tokens: int = 0
    output_tokens: int = 0


class AskResponse(BaseModel):
    status: AnswerStatus
    answer: str | None
    sources: list[Source]
    elapsed_ms: float
    mode: RuntimeMode
    scenario: Scenario
    conversation_id: str
    response_id: str
    trace_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    error: ErrorDetail | None = None


class InvestigationEvidence(BaseModel):
    signal: str
    observation: str
    source: str
    correlation_id: str | None = None


class RecommendedAction(BaseModel):
    priority: str
    title: str
    owner: str
    rationale: str
    risk: str
    validation: str
    approval_required: bool = True


class SreInvestigationReport(BaseModel):
    investigation_id: str
    thread_id: str | None = None
    summary: str
    affected_resources: list[str]
    evidence: list[InvestigationEvidence]
    ruled_out_hypotheses: list[str]
    probable_root_cause: str
    confidence: float = Field(ge=0, le=1)
    portal_url: str | None = None


class ObservabilityDecision(BaseModel):
    status: DecisionStatus
    issue_id: str | None = None
    issue_title: str | None = None
    severity: str
    summary: str
    next_actions: list[RecommendedAction]
    portal_url: str | None = None
    autonomous: bool = False
    human_approval_required: bool = True


class OperationsRequest(BaseModel):
    mode: RuntimeMode = RuntimeMode.SIMULATION
    scenario: InvestigationScenario = InvestigationScenario.TOOL_FAILURE
    trace_id: str | None = Field(default=None, min_length=16, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=128)
    time_range_minutes: int = Field(default=30, ge=5, le=1440)


class OperationsResponse(BaseModel):
    status: AnswerStatus
    mode: RuntimeMode
    scenario: InvestigationScenario
    trace_id: str
    conversation_id: str
    elapsed_ms: float
    investigation: SreInvestigationReport | None = None
    decision: ObservabilityDecision | None = None
    error: ErrorDetail | None = None
