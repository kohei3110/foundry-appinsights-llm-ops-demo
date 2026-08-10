targetScope = 'resourceGroup'

@minLength(1)
param name string

param location string = resourceGroup().location

param sreAgentLocation string = 'eastus2'

param observabilityAgentLocation string = 'eastasia'

param tags object = {}

param aiProjectDeployments array

var suffix = take(uniqueString(subscription().id, resourceGroup().id, name, location), 6)
var normalizedName = toLower(replace(name, '-', ''))
var foundryAccountName = take('aif${normalizedName}${suffix}', 64)
var foundryProjectName = take('proj-${name}-${suffix}', 64)
var logAnalyticsName = take('log-${name}-${suffix}', 63)
var applicationInsightsName = take('appi-${name}-${suffix}', 260)
var containerRegistryName = take('cr${normalizedName}${suffix}', 50)
var managedEnvironmentName = take('cae-${name}-${suffix}', 60)
var webAppName = take('ca-web-${name}-${suffix}', 32)
var sreAgentName = take('sre-${name}-${suffix}', 32)
var sreIdentityName = take('id-sre-${name}-${suffix}', 64)
var azureMonitorAccountName = take('amw-${name}-${suffix}', 63)
var observabilityAgentName = take('obs-${name}-${suffix}', 63)
var observabilityIdentityName = take('id-obs-${name}-${suffix}', 64)
var foundryUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '53ca6127-db72-4b80-b1b0-d745d6d5456d'
)
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
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

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.16.0' = {
  name: 'log-analytics'
  params: {
    name: logAnalyticsName
    location: location
    tags: tags
    skuName: 'PerGB2018'
    dataRetention: 30
    dailyQuotaGb: '0.5'
    forceCmkForQuery: false
    enableTelemetry: false
  }
}

module applicationInsights 'br/public:avm/res/insights/component:0.8.0' = {
  name: 'application-insights'
  params: {
    name: applicationInsightsName
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.outputs.resourceId
    applicationType: 'web'
    disableIpMasking: true
    disableLocalAuth: false
    retentionInDays: 30
    samplingPercentage: 100
    enableTelemetry: false
  }
}

module foundryAccount 'br/public:avm/res/cognitive-services/account:0.17.0' = {
  name: 'foundry-account'
  params: {
    name: foundryAccountName
    location: location
    tags: tags
    kind: 'AIServices'
    sku: 'S0'
    customSubDomainName: foundryAccountName
    allowProjectManagement: true
    disableLocalAuth: true
    dynamicThrottlingEnabled: true
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: false
    managedIdentities: {
      systemAssigned: true
    }
    deployments: aiProjectDeployments
    diagnosticSettings: [
      {
        name: 'foundry-to-log-analytics'
        workspaceResourceId: logAnalytics.outputs.resourceId
      }
    ]
    enableTelemetry: false
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  name: '${foundryAccountName}/${foundryProjectName}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'LLM Operations Demo'
    description: 'Synthetic Japanese policy agent for LLM operations demonstrations.'
  }
}

module containerRegistry 'br/public:avm/res/container-registry/registry:0.12.1' = {
  name: 'container-registry'
  params: {
    name: containerRegistryName
    location: location
    tags: tags
    acrSku: 'Basic'
    acrAdminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
    diagnosticSettings: [
      {
        name: 'acr-to-log-analytics'
        workspaceResourceId: logAnalytics.outputs.resourceId
      }
    ]
    enableTelemetry: false
  }
}

module managedEnvironment 'br/public:avm/res/app/managed-environment:0.15.0' = {
  name: 'container-apps-environment'
  params: {
    name: managedEnvironmentName
    location: location
    tags: tags
    appInsightsConnectionString: applicationInsights.outputs.connectionString
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsWorkspaceResourceId: logAnalytics.outputs.resourceId
    }
    openTelemetryConfiguration: {
      logsConfiguration: {
        destinations: [
          'appInsights'
        ]
      }
      tracesConfiguration: {
        destinations: [
          'appInsights'
        ]
      }
    }
    publicNetworkAccess: 'Enabled'
    zoneRedundant: false
    diagnosticSettings: [
      {
        name: 'container-environment-to-log-analytics'
        workspaceResourceId: logAnalytics.outputs.resourceId
      }
    ]
    enableTelemetry: false
  }
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
    'hidden-link: /app-insights-resource-id': resourceId(
      'Microsoft.Insights/components',
      applicationInsightsName
    )
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
        appId: applicationInsights.outputs.applicationId
        connectionString: applicationInsights.outputs.connectionString
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

resource existingLogAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: logAnalyticsName
}

resource sreLogAnalyticsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingLogAnalytics.id, sreIdentityName, 'log-analytics-reader')
  scope: existingLogAnalytics
  properties: {
    principalId: sreIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logAnalyticsReaderRoleId
  }
  dependsOn: [
    logAnalytics
  ]
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
  name: guid(azureMonitorAccount.id, webAppName, 'issue-reader')
  scope: azureMonitorAccount
  properties: {
    principalId: webApp.outputs.systemAssignedMIPrincipalId!
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleId
  }
}

module observabilityMonitoringReader './subscription-monitoring-reader.bicep' = {
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
    resourceId: applicationInsights.outputs.resourceId
    enabled: true
    isAutonomous: true
  }
}

resource stalePolicyAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'llmops-live-stale-policy'
  location: location
  tags: tags
  properties: {
    displayName: 'LLM Ops live stale policy retrieval'
    description: 'Detects live requests that selected a superseded policy.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      applicationInsights.outputs.resourceId
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
  location: location
  tags: tags
  properties: {
    displayName: 'LLM Ops live request-status tool failure'
    description: 'Detects live request-status dependency failures.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      applicationInsights.outputs.resourceId
    ]
    criteria: {
      allOf: [
        {
          query: 'dependencies | where timestamp > ago(5m) | where tostring(customDimensions["gen_ai.operation.name"]) == "execute_tool" | where tostring(customDimensions["llmops.mode"]) == "live" | where success == false or isnotempty(customDimensions["error.type"])'
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

module webApp 'br/public:avm/res/app/container-app:0.23.0' = {
  name: 'web-container-app'
  params: {
    name: webAppName
    location: location
    environmentResourceId: managedEnvironment.outputs.resourceId
    tags: union(tags, {
      'azd-service-name': 'web'
    })
    managedIdentities: {
      systemAssigned: true
    }
    registries: [
      {
        server: containerRegistry.outputs.loginServer
        identity: 'system'
      }
    ]
    secrets: [
      {
        name: 'appinsights-connection-string'
        value: applicationInsights.outputs.connectionString
      }
    ]
    containers: [
      {
        name: 'web'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        env: [
          {
            name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
            secretRef: 'appinsights-connection-string'
          }
          {
            name: 'AZURE_EXECUTION_ENVIRONMENT'
            value: 'containerapp'
          }
          {
            name: 'FOUNDRY_PROJECT_ENDPOINT'
            value: 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}'
          }
          {
            name: 'FOUNDRY_AGENT_NAME'
            value: 'policy-agent'
          }
          {
            name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
            value: 'gpt-5.4-mini'
          }
          {
            name: 'LLMOPS_CAPTURE_CONTENT'
            value: 'false'
          }
          {
            name: 'LLMOPS_DATA_ROOT'
            value: '/app/data'
          }
          {
            name: 'AZURE_SUBSCRIPTION_ID'
            value: subscription().subscriptionId
          }
          {
            name: 'AZURE_RESOURCE_GROUP'
            value: resourceGroup().name
          }
          {
            name: 'SRE_AGENT_NAME'
            value: sreAgent.name
          }
          {
            name: 'SRE_AGENT_ENDPOINT'
            value: sreAgent.properties.agentEndpoint
          }
          {
            name: 'AZURE_MONITOR_ACCOUNT_ID'
            value: azureMonitorAccount.id
          }
          {
            name: 'OBSERVABILITY_AGENT_NAME'
            value: observabilityAgent.name
          }
        ]
        probes: [
          {
            type: 'Liveness'
            httpGet: {
              path: '/healthz'
              port: 8000
            }
            initialDelaySeconds: 5
            periodSeconds: 10
          }
          {
            type: 'Readiness'
            httpGet: {
              path: '/readyz?mode=simulation'
              port: 8000
            }
            initialDelaySeconds: 5
            periodSeconds: 10
          }
        ]
        resources: {
          cpu: json('0.5')
          memory: '1Gi'
        }
      }
    ]
    ingressExternal: true
    ingressAllowInsecure: false
    ingressTargetPort: 8000
    ingressTransport: 'auto'
    activeRevisionsMode: 'Single'
    scaleSettings: {
      minReplicas: 0
      maxReplicas: 1
    }
    diagnosticSettings: [
      {
        name: 'web-to-log-analytics'
        workspaceResourceId: logAnalytics.outputs.resourceId
      }
    ]
    enableTelemetry: false
  }
}

resource existingRegistry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: containerRegistryName
}

resource webAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, webAppName, 'acr-pull')
  scope: existingRegistry
  properties: {
    principalId: webApp.outputs.systemAssignedMIPrincipalId!
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource webFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, webAppName, 'foundry-user')
  scope: foundryProject
  properties: {
    principalId: webApp.outputs.systemAssignedMIPrincipalId!
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRoleId
  }
}

resource projectIdentityFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, 'project-identity', 'foundry-user')
  scope: foundryProject
  properties: {
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRoleId
  }
}

resource webSreAgentUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sreAgent.id, webAppName, 'sre-agent-standard-user')
  scope: sreAgent
  properties: {
    principalId: webApp.outputs.systemAssignedMIPrincipalId!
    principalType: 'ServicePrincipal'
    roleDefinitionId: sreAgentStandardUserRoleId
  }
}

output containerRegistryEndpoint string = containerRegistry.outputs.loginServer
output containerRegistryName string = containerRegistry.outputs.name
output logAnalyticsWorkspaceId string = logAnalytics.outputs.logAnalyticsWorkspaceId
output applicationInsightsConnectionString string = applicationInsights.outputs.connectionString
output applicationInsightsResourceId string = applicationInsights.outputs.resourceId
output foundryAccountName string = foundryAccount.outputs.name
output foundryProjectName string = foundryProjectName
output foundryProjectId string = foundryProject.id
output foundryProjectEndpoint string = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}'
output webUrl string = 'https://${webApp.outputs.fqdn}'
output sreAgentName string = sreAgent.name
output sreAgentEndpoint string = sreAgent.properties.agentEndpoint
output azureMonitorAccountId string = azureMonitorAccount.id
output observabilityAgentName string = observabilityAgent.name
