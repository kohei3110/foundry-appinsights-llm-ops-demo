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
