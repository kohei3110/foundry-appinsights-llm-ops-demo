from __future__ import annotations

from typing import Protocol

import httpx

from github_broker.config import BrokerSettings
from github_broker.models import (
    COPILOT_RESPONSE_IDENTITIES,
    GitHubIssue,
    GitHubPullRequest,
)

_COPILOT_LOGIN = "copilot-swe-agent[bot]"


class GitHubBrokerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class GitHubClientProtocol(Protocol):
    async def find_open_issue(self, title: str) -> GitHubIssue | None: ...

    async def create_issue(self, title: str, body: str) -> GitHubIssue: ...

    async def get_issue(self, issue_number: int) -> GitHubIssue: ...

    async def assign_copilot(
        self,
        issue_number: int,
        custom_instructions: str,
    ) -> GitHubIssue: ...

    async def find_linked_copilot_pr(
        self,
        issue_number: int,
    ) -> GitHubPullRequest | None: ...

    async def get_pull_request(
        self,
        pull_request_number: int,
    ) -> GitHubPullRequest: ...


class GitHubClient:
    def __init__(
        self,
        settings: BrokerSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not settings.github_token:
            raise ValueError("GITHUB_BROKER_TOKEN is required")
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=settings.github_request_timeout_seconds
        )
        self._headers = {
            "Authorization": (
                f"Bearer {settings.github_token.get_secret_value()}"
            ),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sre-github-handoff-broker",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._settings.github_api_url.rstrip('/')}{path}",
                headers=self._headers,
                params=params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise GitHubBrokerError(
                "github_unavailable",
                "GitHub request failed before a response was received",
                retryable=True,
            ) from exc
        if response.is_success:
            return response
        if response.status_code in {401, 403, 404, 422}:
            raise GitHubBrokerError(
                "github_request_rejected",
                f"GitHub rejected the broker request with HTTP {response.status_code}",
                retryable=False,
            )
        raise GitHubBrokerError(
            "github_unavailable",
            f"GitHub returned HTTP {response.status_code}",
            retryable=True,
        )

    async def find_open_issue(self, title: str) -> GitHubIssue | None:
        page = 1
        while True:
            response = await self._request(
                "GET",
                (
                    f"/repos/{self._settings.github_owner}/"
                    f"{self._settings.github_repository}/issues"
                ),
                params={"state": "open", "per_page": 100, "page": page},
            )
            items = response.json()
            if not isinstance(items, list):
                raise GitHubBrokerError(
                    "github_invalid_response",
                    "GitHub issue list response was not an array",
                    retryable=True,
                )
            for item in items:
                if not isinstance(item, dict):
                    continue
                creator = item.get("user")
                creator_login = (
                    str(creator.get("login", "")).lower()
                    if isinstance(creator, dict)
                    else ""
                )
                if (
                    "pull_request" not in item
                    and item.get("title") == title
                    and creator_login == self._settings.github_owner.lower()
                ):
                    return GitHubIssue.model_validate(item)
            if "next" not in response.links:
                return None
            page += 1

    async def create_issue(self, title: str, body: str) -> GitHubIssue:
        response = await self._request(
            "POST",
            (
                f"/repos/{self._settings.github_owner}/"
                f"{self._settings.github_repository}/issues"
            ),
            json_body={"title": title, "body": body},
        )
        issue = GitHubIssue.model_validate(response.json())
        if issue.creator_login != self._settings.github_owner.lower():
            raise GitHubBrokerError(
                "github_untrusted_issue_creator",
                "Created issue does not have the configured trusted creator",
                retryable=False,
            )
        return issue

    async def get_issue(self, issue_number: int) -> GitHubIssue:
        response = await self._request(
            "GET",
            (
                f"/repos/{self._settings.github_owner}/"
                f"{self._settings.github_repository}/issues/{issue_number}"
            ),
        )
        return GitHubIssue.model_validate(response.json())

    async def assign_copilot(
        self,
        issue_number: int,
        custom_instructions: str,
    ) -> GitHubIssue:
        response = await self._request(
            "POST",
            (
                f"/repos/{self._settings.github_owner}/"
                f"{self._settings.github_repository}/issues/"
                f"{issue_number}/assignees"
            ),
            json_body={
                "assignees": [_COPILOT_LOGIN],
                "agent_assignment": {
                    "target_repo": self._settings.repository_full_name,
                    "base_branch": self._settings.github_base_branch,
                    "custom_instructions": custom_instructions,
                    "custom_agent": "",
                    "model": "",
                },
            },
        )
        return GitHubIssue.model_validate(response.json())

    async def find_linked_copilot_pr(
        self,
        issue_number: int,
    ) -> GitHubPullRequest | None:
        page = 1
        while True:
            response = await self._request(
                "GET",
                (
                    f"/repos/{self._settings.github_owner}/"
                    f"{self._settings.github_repository}/issues/"
                    f"{issue_number}/timeline"
                ),
                params={"per_page": 100, "page": page},
            )
            events = response.json()
            if not isinstance(events, list):
                raise GitHubBrokerError(
                    "github_invalid_response",
                    "GitHub issue timeline response was not an array",
                    retryable=True,
                )
            for event in events:
                if not isinstance(event, dict):
                    continue
                source = event.get("source", {})
                source_issue = (
                    source.get("issue", {}) if isinstance(source, dict) else {}
                )
                if not isinstance(source_issue, dict):
                    continue
                author = source_issue.get("user", {})
                author_login = (
                    str(author.get("login", "")).lower()
                    if isinstance(author, dict)
                    else ""
                )
                source_repository = source_issue.get("repository")
                source_repository_name = (
                    str(source_repository.get("full_name", "")).lower()
                    if isinstance(source_repository, dict)
                    else ""
                )
                repository_url = str(source_issue.get("repository_url", ""))
                expected_repository = self._settings.repository_full_name.lower()
                repository_matches = (
                    source_repository_name == expected_repository
                    or repository_url.lower().endswith(
                        f"/repos/{expected_repository}"
                    )
                )
                if (
                    source_issue.get("pull_request")
                    and author_login in COPILOT_RESPONSE_IDENTITIES
                    and repository_matches
                ):
                    return GitHubPullRequest.model_validate(source_issue)
            if "next" not in response.links:
                return None
            page += 1

    async def get_pull_request(
        self,
        pull_request_number: int,
    ) -> GitHubPullRequest:
        response = await self._request(
            "GET",
            (
                f"/repos/{self._settings.github_owner}/"
                f"{self._settings.github_repository}/pulls/"
                f"{pull_request_number}"
            ),
        )
        return GitHubPullRequest.model_validate(response.json())
