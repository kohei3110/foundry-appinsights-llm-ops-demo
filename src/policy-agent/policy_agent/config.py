from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_default_data_root(module_file: Path) -> Path:
    service_root = module_file.resolve().parent.parent
    repository_data_root = service_root.parent.parent / "data"
    service_data_root = service_root / "data"
    return (
        repository_data_root
        if repository_data_root.exists()
        else service_data_root
    )


_DEFAULT_DATA_ROOT = _resolve_default_data_root(Path(__file__))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    application_name: str = "foundry-policy-llmops-demo"
    service_version: str = "0.1.0"
    default_mode: str = Field("simulation", validation_alias="LLMOPS_DEFAULT_MODE")
    capture_content: bool = Field(False, validation_alias="LLMOPS_CAPTURE_CONTENT")
    slow_tool_delay_seconds: float = Field(
        1.2, ge=0, le=30, validation_alias="LLMOPS_SLOW_TOOL_DELAY_SECONDS"
    )
    data_root: Path = Field(
        default=_DEFAULT_DATA_ROOT, validation_alias="LLMOPS_DATA_ROOT"
    )

    foundry_project_endpoint: str | None = Field(
        None, validation_alias="FOUNDRY_PROJECT_ENDPOINT"
    )
    foundry_agent_name: str | None = Field(
        None, validation_alias="FOUNDRY_AGENT_NAME"
    )
    foundry_agent_version: str | None = Field(
        None, validation_alias="FOUNDRY_AGENT_VERSION"
    )
    model_deployment_name: str = Field(
        "gpt-5.4-mini", validation_alias="AZURE_AI_MODEL_DEPLOYMENT_NAME"
    )

    azure_execution_environment: str | None = Field(
        None, validation_alias="AZURE_EXECUTION_ENVIRONMENT"
    )
    managed_identity_client_id: str | None = Field(
        None, validation_alias="AZURE_CLIENT_ID"
    )
    applicationinsights_connection_string: str | None = Field(
        None, validation_alias="APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    azure_subscription_id: str | None = Field(
        None, validation_alias="AZURE_SUBSCRIPTION_ID"
    )
    azure_resource_group: str | None = Field(
        None, validation_alias="AZURE_RESOURCE_GROUP"
    )
    sre_agent_name: str | None = Field(None, validation_alias="SRE_AGENT_NAME")
    sre_agent_endpoint: str | None = Field(
        None, validation_alias="SRE_AGENT_ENDPOINT"
    )
    azure_monitor_account_id: str | None = Field(
        None, validation_alias="AZURE_MONITOR_ACCOUNT_ID"
    )
    observability_agent_name: str | None = Field(
        None, validation_alias="OBSERVABILITY_AGENT_NAME"
    )
    operations_poll_interval_seconds: float = Field(
        1.0,
        ge=0.1,
        le=10,
        validation_alias="LLMOPS_OPERATIONS_POLL_INTERVAL_SECONDS",
    )
    operations_poll_timeout_seconds: float = Field(
        120,
        ge=5,
        le=600,
        validation_alias="LLMOPS_OPERATIONS_POLL_TIMEOUT_SECONDS",
    )

    @property
    def is_azure(self) -> bool:
        return (self.azure_execution_environment or "").lower() in {
            "azure",
            "containerapp",
            "foundry",
        }

    @property
    def live_configured(self) -> bool:
        return bool(self.foundry_project_endpoint and self.foundry_agent_name)

    @property
    def operations_live_configured(self) -> bool:
        return bool(
            self.sre_agent_endpoint
            and self.sre_agent_name
            and self.azure_monitor_account_id
            and self.observability_agent_name
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
