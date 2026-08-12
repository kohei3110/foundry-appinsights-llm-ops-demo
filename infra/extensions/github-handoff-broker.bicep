targetScope = 'resourceGroup'

@minLength(1)
param environmentName string

@description('Existing Container Apps managed environment name.')
param managedEnvironmentName string

@description('Existing Azure Container Registry name.')
param containerRegistryName string

@description('Existing Application Insights resource ID monitored by the broker.')
param applicationInsightsResourceId string

@description('Existing Log Analytics workspace resource ID for broker diagnostics.')
param logAnalyticsWorkspaceResourceId string

@description('Enable only after both required broker secrets exist in the generated Key Vault.')
param githubHandoffEnabled bool = false

@description('Broker image to preserve when reconciling infrastructure after application deployment.')
param githubBrokerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

param location string = resourceGroup().location

param tags object = {
  'azd-env-name': environmentName
  workload: 'foundry-llm-ops-demo'
  extension: 'github-handoff-broker'
}

var suffix = take(uniqueString(subscription().id, resourceGroup().id, environmentName), 6)
var normalizedName = toLower(replace(environmentName, '-', ''))
var githubBrokerAppName = 'ca-ghbroker-${take(normalizedName, 11)}-${suffix}'
var githubBrokerIdentityName = take('id-github-broker-${environmentName}-${suffix}', 64)
var githubBrokerKeyVaultName = 'kv-${take(normalizedName, 14)}-${suffix}'
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource managedEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: managedEnvironmentName
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: containerRegistryName
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: last(split(applicationInsightsResourceId, '/'))
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: last(split(logAnalyticsWorkspaceResourceId, '/'))
}

resource githubBrokerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: githubBrokerIdentityName
  location: location
  tags: tags
}

resource githubBrokerKeyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: githubBrokerKeyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    accessPolicies: []
    publicNetworkAccess: 'Enabled'
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

resource githubBrokerKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(githubBrokerKeyVault.id, githubBrokerIdentityName, 'secrets-user')
  scope: githubBrokerKeyVault
  properties: {
    principalId: githubBrokerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

module githubBrokerApp 'br/public:avm/res/app/container-app:0.23.0' = {
  name: 'github-broker-container-app'
  params: {
    name: githubBrokerAppName
    location: location
    environmentResourceId: managedEnvironment.id
    tags: union(tags, {
      'azd-service-name': 'github-broker'
    })
    managedIdentities: {
      systemAssigned: true
      userAssignedResourceIds: [
        githubBrokerIdentity.id
      ]
    }
    secrets: githubHandoffEnabled
      ? [
          {
            name: 'github-handoff-bearer-token'
            keyVaultUrl: '${githubBrokerKeyVault.properties.vaultUri}secrets/github-handoff-bearer-token'
            identity: githubBrokerIdentity.id
          }
          {
            name: 'github-broker-token'
            keyVaultUrl: '${githubBrokerKeyVault.properties.vaultUri}secrets/github-broker-token'
            identity: githubBrokerIdentity.id
          }
        ]
      : []
    containers: [
      {
        name: 'github-broker'
        image: githubBrokerImage
        env: concat(
          [
            {
              name: 'GITHUB_HANDOFF_ENABLED'
              value: string(githubHandoffEnabled)
            }
            {
              name: 'AZURE_EXECUTION_ENVIRONMENT'
              value: 'containerapp'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: githubBrokerIdentity.properties.clientId
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
              name: 'APPLICATIONINSIGHTS_RESOURCE_ID'
              value: applicationInsights.id
            }
            {
              name: 'GITHUB_BROKER_OWNER'
              value: 'kohei3110'
            }
            {
              name: 'GITHUB_BROKER_REPOSITORY'
              value: 'foundry-appinsights-llm-ops-demo'
            }
            {
              name: 'GITHUB_BROKER_BASE_BRANCH'
              value: 'main'
            }
          ],
          githubHandoffEnabled
            ? [
                {
                  name: 'GITHUB_HANDOFF_BEARER_TOKEN'
                  secretRef: 'github-handoff-bearer-token'
                }
                {
                  name: 'GITHUB_BROKER_TOKEN'
                  secretRef: 'github-broker-token'
                }
              ]
            : []
        )
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
              path: '/healthz'
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
      minReplicas: 1
      maxReplicas: 1
    }
    diagnosticSettings: [
      {
        name: 'github-broker-to-log-analytics'
        workspaceResourceId: logAnalytics.id
      }
    ]
    enableTelemetry: false
  }
  dependsOn: [
    githubBrokerKeyVaultSecretsUser
  ]
}

resource githubBrokerAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, githubBrokerIdentityName, 'acr-pull')
  scope: containerRegistry
  properties: {
    principalId: githubBrokerApp.outputs.systemAssignedMIPrincipalId!
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

module githubBrokerMonitoringReader '../modules/subscription-monitoring-reader.bicep' = {
  name: 'github-broker-monitoring-reader-${suffix}'
  scope: subscription()
  params: {
    principalId: githubBrokerIdentity.properties.principalId
  }
}

output GITHUB_HANDOFF_BROKER_URL string = 'https://${githubBrokerApp.outputs.fqdn}'
output GITHUB_HANDOFF_KEY_VAULT_NAME string = githubBrokerKeyVault.name
output GITHUB_BROKER_CONTAINER_APP_NAME string = githubBrokerApp.outputs.name
output GITHUB_BROKER_IDENTITY_CLIENT_ID string = githubBrokerIdentity.properties.clientId
