from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from github_broker.azure import AlertVerifierProtocol
from github_broker.config import BrokerSettings
from github_broker.github import GitHubClientProtocol
from github_broker.models import (
    AlertRule,
    COPILOT_RESPONSE_IDENTITIES,
    EvidenceCode,
    GitHubIssue,
    GitHubPullRequest,
    HandoffResult,
    HandoffState,
    HypothesisCode,
    RootCauseCode,
    SreHandoffRequest,
)

_LOGGER = logging.getLogger("llmops_github_broker")

_ISSUE_TITLE_PREFIXES = {
    AlertRule.STALE_POLICY: (
        "[SRE-AUTO][OPS-REVIEW] Fix stale-policy selection"
    ),
    AlertRule.TOOL_FAILURE: (
        "[SRE-AUTO][OPS-REVIEW] Fix request-status dependency failure"
    ),
}
_EXPECTED_CODE_CHANGES = {
    AlertRule.STALE_POLICY: (
        "Prevent superseded policies from being selected and preserve an "
        "explicit answer-unavailable path when no current policy exists."
    ),
    AlertRule.TOOL_FAILURE: (
        "Handle request-status dependency failures explicitly without "
        "inventing status or falling back from live mode to simulation."
    ),
}
_SUMMARIES = {
    AlertRule.STALE_POLICY: (
        "A live request selected a superseded policy while the model and "
        "application platform remained available."
    ),
    AlertRule.TOOL_FAILURE: (
        "A live request failed because the request-status dependency was "
        "unavailable while the model and application platform remained available."
    ),
}
_EVIDENCE_TEXT = {
    EvidenceCode.SUPERSEDED_POLICY_SELECTED: (
        "`policy_version` (Application Insights): Retrieval selected a "
        "superseded policy document."
    ),
    EvidenceCode.MODEL_CALL_SUCCEEDED: (
        "`model_health` (Application Insights): The correlated model call "
        "completed successfully."
    ),
    EvidenceCode.CONTAINER_REVISION_HEALTHY: (
        "`container_health` (Azure Monitor): The current Container App "
        "revision remained healthy."
    ),
    EvidenceCode.REQUEST_STATUS_DEPENDENCY_FAILED: (
        "`dependency_failure` (Application Insights): The request-status "
        "dependency call failed."
    ),
    EvidenceCode.STRUCTURED_FAILURE_RETURNED: (
        "`structured_failure` (Application Insights): The API returned the "
        "expected structured dependency failure."
    ),
}
_HYPOTHESIS_TEXT = {
    HypothesisCode.MODEL_FAILURE: "Foundry model failure",
    HypothesisCode.CONTAINER_PLATFORM_FAILURE: "Container Apps platform failure",
    HypothesisCode.AUTHENTICATION_FAILURE: "Authentication failure",
    HypothesisCode.REQUEST_STATUS_FAILURE: "Request-status dependency failure",
    HypothesisCode.RETRIEVAL_FAILURE: "Policy retrieval failure",
}
_ROOT_CAUSE_TEXT = {
    RootCauseCode.STALE_POLICY_FILTER_MISSING: (
        "The current-policy filter was not enforced before ranking policy documents."
    ),
    RootCauseCode.REQUEST_STATUS_DEPENDENCY_UNAVAILABLE: (
        "The required request-status dependency was unavailable."
    ),
}
_VALIDATIONS = {
    AlertRule.STALE_POLICY: (
        "Run stale-policy retrieval and live-provider regression coverage; "
        "verify current-policy selection and structured unavailability."
    ),
    AlertRule.TOOL_FAILURE: (
        "Run tool-failure and telemetry regression coverage; verify the "
        "structured failure, correlation fields, and truthful live behavior."
    ),
}


class HandoffBroker:
    def __init__(
        self,
        settings: BrokerSettings,
        github: GitHubClientProtocol,
        verifier: AlertVerifierProtocol,
    ) -> None:
        self._settings = settings
        self._github = github
        self._verifier = verifier
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def submit(self, request: SreHandoffRequest) -> HandoffResult:
        await self._verifier.verify(request.alert_id, request.alert_rule)
        title = self._issue_title(request)
        async with self._locks[title]:
            issue = await self._github.find_open_issue(title)
            issue_created = issue is None
            if issue is None:
                issue = await self._github.create_issue(
                    title,
                    self._issue_body(request),
                )

            linked_pr = await self._github.find_linked_copilot_pr(issue.number)
            if linked_pr:
                linked_pr = await self._github.get_pull_request(linked_pr.number)
                return self._result(
                    issue,
                    issue_created=issue_created,
                    copilot_assigned=True,
                    linked_pr=linked_pr,
                )

            current_issue = await self._github.get_issue(issue.number)
            if current_issue.copilot_assigned:
                return self._result(
                    current_issue,
                    issue_created=issue_created,
                    copilot_assigned=False,
                )

            assigned_issue = await self._github.assign_copilot(
                issue.number,
                self._copilot_instructions(issue.number),
            )
            if not assigned_issue.copilot_assigned:
                _LOGGER.warning(
                    "GitHub did not confirm Copilot assignment for issue %s",
                    assigned_issue.number,
                )
                return self._result(
                    assigned_issue,
                    issue_created=issue_created,
                    copilot_assigned=False,
                )
            _LOGGER.info(
                "Assigned Copilot to sanitized issue %s",
                assigned_issue.number,
            )
            return self._result(
                assigned_issue,
                issue_created=issue_created,
                copilot_assigned=True,
            )

    async def get_status(self, issue_number: int) -> HandoffResult:
        issue = await self._github.get_issue(issue_number)
        linked_pr = await self._github.find_linked_copilot_pr(issue_number)
        if linked_pr:
            linked_pr = await self._github.get_pull_request(linked_pr.number)
        return self._result(
            issue,
            issue_created=False,
            copilot_assigned=False,
            linked_pr=linked_pr,
        )

    def _issue_title(self, request: SreHandoffRequest) -> str:
        return (
            f"{_ISSUE_TITLE_PREFIXES[request.alert_rule]} "
            f"[{request.investigation_fingerprint}]"
        )

    def _issue_body(self, request: SreHandoffRequest) -> str:
        evidence = "\n".join(
            f"- {_EVIDENCE_TEXT[code]}" for code in request.evidence_codes
        )
        ruled_out = "\n".join(
            f"- {_HYPOTHESIS_TEXT[code]}" for code in request.ruled_out_codes
        )
        return (
            "## SRE Agent provenance\n\n"
            "A live, read-only Azure SRE Agent investigation completed before "
            "this issue was created. This public handoff contains only the "
            "validated sanitized contract.\n\n"
            f"- Alert rule: `{request.alert_rule.value}`\n"
            f"- Investigation fingerprint: `{request.investigation_fingerprint}`\n"
            f"- Confidence band: `{request.confidence_band.value}`\n"
            f"- Evidence items: {len(request.evidence_codes)}\n"
            f"- Hypotheses ruled out: {len(request.ruled_out_codes)}\n\n"
            "## Summary\n\n"
            f"{_SUMMARIES[request.alert_rule]}\n\n"
            "## Sanitized evidence\n\n"
            f"{evidence}\n\n"
            "## Ruled-out hypotheses\n\n"
            f"{ruled_out}\n\n"
            "## Probable root cause\n\n"
            f"{_ROOT_CAUSE_TEXT[request.root_cause_code]}\n\n"
            "## Expected code change\n\n"
            f"{_EXPECTED_CODE_CHANGES[request.alert_rule]}\n\n"
            "## Validation\n\n"
            f"{_VALIDATIONS[request.alert_rule]}\n\n"
            "## Safety boundary\n\n"
            "Copilot may create a draft pull request. It must not merge, "
            "deploy, trigger workflows, modify Azure, weaken live/simulation "
            "separation, add a live-to-simulation fallback, enable content "
            "capture, or treat missing live evidence as simulated success. A "
            "human reviews every code and environment change."
        )

    def _copilot_instructions(self, issue_number: int) -> str:
        return (
            f"Resolve issue #{issue_number}. Preserve explicit simulation and "
            "live modes and never add live-to-simulation fallback. Keep GenAI "
            "content capture disabled by default. Do not modify Azure, workflow "
            "permissions, branch protection, or deployment configuration. Add "
            "targeted tests and create a draft pull request only; do not merge "
            "or deploy."
        )

    def _result(
        self,
        issue: GitHubIssue,
        *,
        issue_created: bool,
        copilot_assigned: bool,
        linked_pr: GitHubPullRequest | None = None,
    ) -> HandoffResult:
        if linked_pr and self._is_ready_copilot_pr(linked_pr):
            return HandoffResult(
                state=HandoffState.DRAFT_PR_READY,
                issue_number=issue.number,
                issue_url=issue.html_url,
                issue_created=issue_created,
                copilot_assigned=copilot_assigned,
                pull_request_number=linked_pr.number,
                pull_request_url=linked_pr.html_url,
                pull_request_is_draft=linked_pr.draft,
            )
        if copilot_assigned:
            state = HandoffState.COPILOT_ASSIGNED
        elif issue.copilot_assigned:
            state = HandoffState.COPILOT_ALREADY_ASSIGNED
        else:
            state = HandoffState.INCOMPLETE
        return HandoffResult(
            state=state,
            issue_number=issue.number,
            issue_url=issue.html_url,
            issue_created=issue_created,
            copilot_assigned=copilot_assigned,
        )

    def _is_ready_copilot_pr(self, pull_request: GitHubPullRequest) -> bool:
        return (
            pull_request.state == "open"
            and pull_request.draft
            and pull_request.author_login in COPILOT_RESPONSE_IDENTITIES
            and pull_request.base_repository
            == self._settings.repository_full_name.lower()
            and pull_request.base_ref == self._settings.github_base_branch
        )
