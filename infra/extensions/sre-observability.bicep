targetScope = 'resourceGroup'

@minLength(1)
param environmentName string

@description('Existing Application Insights resource ID in this resource group.')
param applicationInsightsResourceId string

@description('Existing Log Analytics workspace resource ID used by Application Insights.')
param logAnalyticsWorkspaceResourceId string

@description('Existing Container App name whose managed identity invokes SRE Agent.')
param containerAppName string

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

param tags object = {
  'azd-env-name': environmentName
  workload: 'foundry-llmops-demo'
  extension: 'sre-observability'
}

var suffix = take(uniqueString(subscription().id, resourceGroup().id, environmentName), 6)
var sreAgentName = take('sre-${environmentName}-${suffix}', 32)
var sreIdentityName = take('id-sre-${environmentName}-${suffix}', 64)
var azureMonitorAccountName = take('amw-${environmentName}-${suffix}', 63)
var observabilityAgentName = take('obs-${environmentName}-${suffix}', 63)
var observabilityIdentityName = take('id-obs-${environmentName}-${suffix}', 64)
var readerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'acdd72a7-3385-48ef-bd42-f606fba81ae7'
)
var monitoringReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
)
var logAnalyticsReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '73c42c96-874c-492b-b04d-ab87d138a893'
)
var sreAgentStandardUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '2d84a65a-63b2-4343-bbb6-31105d857bc1'
)
var issueContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '8d7ecc5c-f27b-43cf-883f-46409d445502'
)

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: last(split(applicationInsightsResourceId, '/'))
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: last(split(logAnalyticsWorkspaceResourceId, '/'))
}

resource webApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: containerAppName
}

resource sreIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: sreIdentityName
  location: sreAgentLocation
  tags: tags
  properties: {
    isolationScope: 'Regional'
  }
}

#disable-next-line BCP081
resource sreAgent 'Microsoft.App/agents@2025-05-01-preview' = {
  name: sreAgentName
  location: sreAgentLocation
  tags: union(tags, {
    'hidden-link: /app-insights-resource-id': applicationInsights.id
  })
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${sreIdentity.id}': {}
    }
  }
  properties: {
    knowledgeGraphConfiguration: {
      managedResources: [
        resourceGroup().id
      ]
      identity: sreIdentity.id
    }
    actionConfiguration: {
      mode: 'ReadOnly'
      identity: sreIdentity.id
      accessLevel: 'Low'
    }
    mcpServers: []
    logConfiguration: {
      applicationInsightsConfiguration: {
        appId: applicationInsights.properties.AppId
        connectionString: applicationInsights.properties.ConnectionString
      }
    }
    upgradeChannel: 'Stable'
  }
}

resource sreReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, sreIdentityName, 'reader')
  properties: {
    principalId: sreIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleId
  }
}

resource sreMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, sreIdentityName, 'monitoring-reader')
  properties: {
    principalId: sreIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringReaderRoleId
  }
}

resource sreLogAnalyticsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalytics.id, sreIdentityName, 'log-analytics-reader')
  scope: logAnalytics
  properties: {
    principalId: sreIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logAnalyticsReaderRoleId
  }
}

resource webSreAgentUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sreAgent.id, containerAppName, 'sre-agent-standard-user')
  scope: sreAgent
  properties: {
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: sreAgentStandardUserRoleId
  }
}

resource azureMonitorAccount 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: azureMonitorAccountName
  location: observabilityAgentLocation
  tags: tags
  properties: {}
}

resource observabilityIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: observabilityIdentityName
  location: observabilityAgentLocation
  tags: tags
  properties: {
    isolationScope: 'Regional'
  }
}

resource observabilityIssueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(azureMonitorAccount.id, observabilityIdentityName, 'issue-contributor')
  scope: azureMonitorAccount
  properties: {
    principalId: observabilityIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: issueContributorRoleId
  }
}

resource webIssueReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(azureMonitorAccount.id, containerAppName, 'issue-reader')
  scope: azureMonitorAccount
  properties: {
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleId
  }
}

module observabilityMonitoringReader '../modules/subscription-monitoring-reader.bicep' = {
  name: 'observability-monitoring-reader-${suffix}'
  scope: subscription()
  params: {
    principalId: observabilityIdentity.properties.principalId
  }
}

resource observabilityAgent 'Microsoft.Monitor/observabilityAgents@2026-05-01-preview' = {
  name: observabilityAgentName
  location: observabilityAgentLocation
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${observabilityIdentity.id}': {}
    }
  }
  properties: {
    monitoringAccountId: azureMonitorAccount.id
    enabled: true
    operations: [
      {
        type: 'IssueCreation'
        mode: 'Auto'
        instructions: 'Correlate only alerts for the policy-agent workload. Keep simulation and live alerts separate. Always create an issue for live stale-policy retrieval or request-status tool failure. Include [OPS-REVIEW] in the issue title. Treat policy-agent as customer-facing. Never include prompt content, output content, credentials, connection strings, or customer data.'
      }
      {
        type: 'Investigation'
        mode: 'Auto'
      }
    ]
  }
  dependsOn: [
    observabilityIssueContributor
    observabilityMonitoringReader
  ]
}

resource observabilityMonitoredApp 'Microsoft.Monitor/observabilityAgents/monitoredResources@2026-05-01-preview' = {
  parent: observabilityAgent
  name: 'policy-agent-appinsights'
  properties: {
    resourceId: applicationInsights.id
    enabled: true
    isAutonomous: true
  }
}

resource stalePolicyAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'llmops-live-stale-policy'
  location: resourceGroup().location
  tags: tags
  properties: {
    displayName: 'LLM Ops live stale policy retrieval'
    description: 'Detects live requests that selected a superseded policy.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      applicationInsights.id
    ]
    criteria: {
      allOf: [
        {
          query: 'dependencies | where timestamp > ago(5m) | where tostring(customDimensions["gen_ai.operation.name"]) == "retrieve" | where tostring(customDimensions["llmops.mode"]) == "live" | where tostring(customDimensions["llmops.policy.status"]) == "superseded"'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    skipQueryValidation: true
  }
}

resource toolFailureAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'llmops-live-tool-failure'
  location: resourceGroup().location
  tags: tags
  properties: {
    displayName: 'LLM Ops live request-status tool failure'
    description: 'Detects live request-status dependency failures.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      applicationInsights.id
    ]
    criteria: {
      allOf: [
        {
          query: 'dependencies | where timestamp > ago(5m) | where tostring(customDimensions["gen_ai.operation.name"]) == "execute_tool" | where tostring(customDimensions["gen_ai.tool.name"]) == "request_status" | where tostring(customDimensions["llmops.mode"]) == "live" | where tostring(customDimensions["error.type"]) == "request_status_unavailable"'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    skipQueryValidation: true
  }
}

output SRE_AGENT_NAME string = sreAgent.name
output SRE_AGENT_ENDPOINT string = sreAgent.properties.agentEndpoint
output AZURE_MONITOR_ACCOUNT_ID string = azureMonitorAccount.id
output OBSERVABILITY_AGENT_NAME string = observabilityAgent.name
