targetScope = 'subscription'

@minLength(1)
@maxLength(24)
param environmentName string

@allowed([
  'japaneast'
])
param location string = 'japaneast'

@allowed([
  'eastus2'
  'swedencentral'
  'australiaeast'
])
param sreAgentLocation string = 'eastus2'

@allowed([
  'eastasia'
  'australiaeast'
  'canadacentral'
  'centralus'
  'eastus'
  'southcentralus'
  'uksouth'
  'westcentralus'
  'westeurope'
])
param observabilityAgentLocation string = 'eastasia'

@description('Existing or new resource group name for the environment.')
param resourceGroupName string = 'rg-${environmentName}'

@description('Model deployments marshalled from services.ai-project.deployments by the Foundry azd extension.')
param aiProjectDeploymentsJson string = '[{"name":"gpt-5.4-mini","model":{"format":"OpenAI","name":"gpt-5.4-mini","version":"2026-03-17"},"sku":{"name":"GlobalStandard","capacity":10}}]'

@description('Enables the broker after both required secrets are populated in its Key Vault.')
param githubHandoffEnabled bool = false

var tags = {
  'azd-env-name': environmentName
  workload: 'foundry-llmops-demo'
  environment: environmentName
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources './modules/resources.bicep' = {
  name: 'resources-${environmentName}'
  scope: resourceGroup
  params: {
    name: environmentName
    location: location
    sreAgentLocation: sreAgentLocation
    observabilityAgentLocation: observabilityAgentLocation
    tags: tags
    aiProjectDeployments: json(aiProjectDeploymentsJson)
    githubHandoffEnabled: githubHandoffEnabled
  }
}

output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_LOCATION string = location
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.containerRegistryEndpoint
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.containerRegistryName
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = resources.outputs.logAnalyticsWorkspaceId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.applicationInsightsConnectionString
output APPLICATIONINSIGHTS_RESOURCE_ID string = resources.outputs.applicationInsightsResourceId
output AZURE_AI_ACCOUNT_NAME string = resources.outputs.foundryAccountName
output AZURE_AI_PROJECT_NAME string = resources.outputs.foundryProjectName
output AZURE_AI_PROJECT_ID string = resources.outputs.foundryProjectId
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output FOUNDRY_AGENT_NAME string = 'policy-agent'
output SRE_AGENT_NAME string = resources.outputs.sreAgentName
output SRE_AGENT_ENDPOINT string = resources.outputs.sreAgentEndpoint
output AZURE_MONITOR_ACCOUNT_ID string = resources.outputs.azureMonitorAccountId
output OBSERVABILITY_AGENT_NAME string = resources.outputs.observabilityAgentName
output WEB_URL string = resources.outputs.webUrl
output GITHUB_HANDOFF_BROKER_URL string = resources.outputs.githubBrokerUrl
output GITHUB_HANDOFF_KEY_VAULT_NAME string = resources.outputs.githubBrokerKeyVaultName
