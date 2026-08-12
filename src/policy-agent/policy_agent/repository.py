from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from opentelemetry.trace import SpanKind, Status, StatusCode

from policy_agent.models import RuntimeMode, Scenario, Source
from policy_agent.telemetry import get_tracer

CURRENT_STATUS = "current"


class PolicyUnavailableError(LookupError):
    """Raised when no current policy document can be selected."""


@dataclass(frozen=True)
class PolicyDocument:
    document_id: str
    title: str
    version: str
    effective_date: str
    status: str
    path: str
    reimbursement_days: int
    content: str

    def to_source(self) -> Source:
        return Source(
            document_id=self.document_id,
            title=self.title,
            version=self.version,
            status=self.status,
            path=f"data/policies/{self.path}",
        )


class PolicyRepository:
    def __init__(self, data_root: Path) -> None:
        self._policies_root = data_root / "policies"
        self._documents = self._load_documents()

    def _load_documents(self) -> list[PolicyDocument]:
        index_path = self._policies_root / "index.json"
        rows = json.loads(index_path.read_text(encoding="utf-8"))
        return [
            PolicyDocument(
                **row,
                content=(self._policies_root / row["path"]).read_text(
                    encoding="utf-8"
                ),
            )
            for row in rows
        ]

    def select(self, scenario: Scenario) -> PolicyDocument:
        matching = [
            document
            for document in self._documents
            if document.status == CURRENT_STATUS
        ]
        if not matching:
            raise PolicyUnavailableError(
                "No current policy document is configured"
            )
        return max(matching, key=lambda document: document.effective_date)

    def retrieve(
        self,
        question: str,
        scenario: Scenario,
        mode: RuntimeMode = RuntimeMode.SIMULATION,
    ) -> PolicyDocument:
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "retrieve_policy",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "retrieve",
                "gen_ai.provider.name": "local.policy_index",
                "llmops.scenario": scenario.value,
                "llmops.mode": mode.value,
                "llmops.retrieval.query_length": len(question),
            },
        ) as span:
            try:
                document = self.select(scenario)
            except PolicyUnavailableError as exc:
                span.set_attribute("llmops.retrieval.result_count", 0)
                span.set_attribute("error.type", "policy_unavailable")
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise
            span.set_attribute("llmops.policy.document_id", document.document_id)
            span.set_attribute("llmops.policy.version", document.version)
            span.set_attribute("llmops.policy.status", document.status)
            span.set_attribute("llmops.retrieval.result_count", 1)
            return document
