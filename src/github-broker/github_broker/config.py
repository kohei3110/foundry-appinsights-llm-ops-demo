from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    application_name: str = "sre-github-handoff-broker"
    service_version: str = "0.1.0"
    enabled: bool = Field(False, validation_alias="GITHUB_HANDOFF_ENABLED")
    bearer_token: SecretStr | None = Field(
        None,
        validation_alias="GITHUB_HANDOFF_BEARER_TOKEN",
    )
    github_token: SecretStr | None = Field(
        None,
        validation_alias="GITHUB_BROKER_TOKEN",
    )
    github_owner: Literal["kohei3110"] = Field(
        "kohei3110",
        validation_alias="GITHUB_BROKER_OWNER",
    )
    github_repository: Literal[
        "foundry-appinsights-llm-ops-demo"
    ] = Field(
        "foundry-appinsights-llm-ops-demo",
        validation_alias="GITHUB_BROKER_REPOSITORY",
    )
    github_base_branch: Literal["main"] = Field(
        "main",
        validation_alias="GITHUB_BROKER_BASE_BRANCH",
    )
    github_api_url: Literal["https://api.github.com"] = Field(
        "https://api.github.com",
        validation_alias="GITHUB_API_URL",
    )
    github_request_timeout_seconds: float = Field(
        30,
        gt=0,
        le=120,
        validation_alias="GITHUB_BROKER_REQUEST_TIMEOUT_SECONDS",
    )
    max_alert_age_minutes: int = Field(
        60,
        ge=5,
        le=1440,
        validation_alias="GITHUB_BROKER_MAX_ALERT_AGE_MINUTES",
    )
    azure_execution_environment: str | None = Field(
        None,
        validation_alias="AZURE_EXECUTION_ENVIRONMENT",
    )
    managed_identity_client_id: str | None = Field(
        None,
        validation_alias="AZURE_CLIENT_ID",
    )
    azure_subscription_id: str | None = Field(
        None,
        validation_alias="AZURE_SUBSCRIPTION_ID",
    )
    azure_resource_group: str | None = Field(
        None,
        validation_alias="AZURE_RESOURCE_GROUP",
    )
    applicationinsights_resource_id: str | None = Field(
        None,
        validation_alias="APPLICATIONINSIGHTS_RESOURCE_ID",
    )
    applicationinsights_connection_string: str | None = Field(
        None,
        validation_alias="APPLICATIONINSIGHTS_CONNECTION_STRING",
    )

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.bearer_token
            and self.github_token
            and self.azure_subscription_id
            and self.azure_resource_group
            and self.applicationinsights_resource_id
        )

    @property
    def repository_full_name(self) -> str:
        return f"{self.github_owner}/{self.github_repository}"

    @property
    def is_azure(self) -> bool:
        return (self.azure_execution_environment or "").lower() in {
            "azure",
            "containerapp",
            "foundry",
        }


@lru_cache(maxsize=1)
def get_settings() -> BrokerSettings:
    return BrokerSettings()
