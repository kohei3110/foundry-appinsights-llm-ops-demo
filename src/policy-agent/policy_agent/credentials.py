from __future__ import annotations

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from policy_agent.config import Settings


def build_credential(
    settings: Settings,
) -> DefaultAzureCredential | ManagedIdentityCredential:
    if settings.is_azure:
        return ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    return DefaultAzureCredential()
