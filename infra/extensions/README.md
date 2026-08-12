# SRE and Observability in-place extension

Use this resource-group-scoped Bicep entry point to add Azure SRE Agent and Azure Copilot Observability Agent to an existing deployed demo without recreating the base Foundry, Container Apps, ACR, Application Insights, or Log Analytics resources.

Required AZD environment values:

```bash
azd env set WEB_SERVICE_NAME <existing-container-app-name>
azd env set AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID <workspace-resource-id>
```

`APPLICATIONINSIGHTS_RESOURCE_ID` and `AZURE_RESOURCE_GROUP` are already outputs of the base deployment. Get the workspace resource ID from the existing Application Insights `WorkspaceResourceId` property.

Validate without changing Azure:

```bash
az deployment group validate \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file infra/extensions/sre-observability.bicep \
  --parameters \
    environmentName="$AZURE_ENV_NAME" \
    applicationInsightsResourceId="$APPLICATIONINSIGHTS_RESOURCE_ID" \
    logAnalyticsWorkspaceResourceId="$AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID" \
    containerAppName="$WEB_SERVICE_NAME"

az deployment group what-if \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file infra/extensions/sre-observability.bicep \
  --parameters \
    environmentName="$AZURE_ENV_NAME" \
    applicationInsightsResourceId="$APPLICATIONINSIGHTS_RESOURCE_ID" \
    logAnalyticsWorkspaceResourceId="$AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID" \
    containerAppName="$WEB_SERVICE_NAME"
```

Deployment is intentionally not automated from `azure.yaml`. Azure SRE Agent and Observability Agent are preview, billable services. Deploy this extension only after explicit cost, RBAC, and preview-feature approval.

After deployment, capture the four outputs and configure the web Container App with `SRE_AGENT_NAME`, `SRE_AGENT_ENDPOINT`, `AZURE_MONITOR_ACCOUNT_ID`, and `OBSERVABILITY_AGENT_NAME`. Every proposed remediation remains human-approved.

## Post-deployment GitHub handoff

The broker Container App is part of the main infrastructure, not this in-place SRE/Observability extension. Deploy and verify it before changing response plans.

The broker MCP connector, Tool Access Policy, and incident response plans are SRE Agent data-plane configuration rather than ARM child resources:

1. Keep the agent `ReadOnly` and both response plans in `Review` mode.
2. Configure only `github-handoff-broker_submit_incident_handoff` and `github-handoff-broker_get_handoff_status`.
3. Pre-authorize those bounded broker tools and globally deny every direct GitHub write tool.
4. Create missing plans with `sreagent_incidents_plans_create`; render filter/handler updates with `--emit-handler-payloads`.
5. Verify the filters contain `llmops-live-stale-policy` and `llmops-live-tool-failure`.
6. Disable/delete the default quickstart plan and hide legacy GitHub connector tools.

See `docs/sre-github-handoff.md` for the required deployment and cutover order. Never store GitHub or broker tokens in source, ordinary Bicep parameter files, AZD outputs, logs, or issue content.
