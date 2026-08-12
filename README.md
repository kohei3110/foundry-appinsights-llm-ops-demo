# Microsoft Foundry + Application Insights LLM Operations Demo

This project demonstrates an end-to-end LLM operations loop with a Japanese policy agent:

1. Run a healthy request.
2. Inject stale retrieval, tool latency, or a tool failure.
3. Correlate Conversation, Response, and Trace IDs.
4. Diagnose request, retrieval, model, and tool spans in Application Insights.
5. Re-run the same evaluation cases and compare before/after quality and latency.
6. Ask Azure SRE Agent to investigate the correlated Azure evidence.
7. Send a strict sanitized contract to the deterministic handoff broker.
8. Create a GitHub issue and assign Copilot automatically.
9. Use Azure Copilot Observability Agent issues to prioritize other human-reviewed next actions.

The repository is safe to run without Azure in `simulation` mode. `live` mode calls a Microsoft Foundry hosted agent and **never falls back** to simulation.

## Architecture

| Component | Implementation |
|---|---|
| Hosted agent | Python 3.13, Microsoft Agent Framework, Foundry Responses protocol 2.0 |
| Browser/API | FastAPI on Azure Container Apps |
| Model | `gpt-5.4-mini` version `2026-03-17`, GlobalStandard capacity 10 |
| Telemetry | OpenTelemetry GenAI semantic conventions to Application Insights |
| SRE investigation | Azure SRE Agent in read-only mode |
| Code remediation | Deterministic broker, automatic GitHub issue, Copilot draft PR |
| Operational next action | Azure Copilot Observability Agent issue and deep investigation |
| Infrastructure | AZD + Bicep, Japan East, AVM modules |
| Data | Synthetic Japanese policies and request records |

Raw prompt, response, policy, and tool content is not added to telemetry by default. `LLMOPS_CAPTURE_CONTENT=true` is intended only for explicit local demonstrations with synthetic data.

## Local simulation

Python 3.13 is required.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
uvicorn webapp.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The API is also available directly:

```bash
curl -sS http://127.0.0.1:8000/healthz
curl -sS -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"国内出張の精算期限は？","mode":"simulation","scenario":"healthy"}'
```

The supported scenarios are:

| Scenario | Behavior | Expected signal |
|---|---|---|
| `healthy` | Current policy and normal tool | Correct 30-day answer |
| `stale_policy` | Superseded policy selected | Old 14-day answer and version regression |
| `slow_tool` | Request-status delay | Long `execute_tool` span |
| `tool_failure` | Controlled dependency failure | HTTP 502, failed span, structured error |

## Live mode

Copy `.env.example` to `.env` and set:

```text
FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
FOUNDRY_AGENT_NAME=policy-agent
FOUNDRY_AGENT_VERSION=<optional-version>
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5.4-mini
```

Local development uses `DefaultAzureCredential`; sign in through your normal developer tool before starting the application. Azure uses `ManagedIdentityCredential` when `AZURE_EXECUTION_ENVIRONMENT=containerapp` or `foundry`.

The hosted-agent source is `src/policy-agent/main.py`. It uses the current Agent Framework `FoundryChatClient` and `ResponsesHostServer`, with `store=False` because the hosted runtime manages conversation history.

The live operations workflow also requires `SRE_AGENT_ENDPOINT`, `SRE_AGENT_NAME`, `AZURE_MONITOR_ACCOUNT_ID`, and `OBSERVABILITY_AGENT_NAME`. SRE Agent uses the `https://azuresre.dev` data-plane audience. The web managed identity has only SRE Agent Standard User access; the SRE identity has read-only monitoring roles on the workload resource group.

## SRE and Observability workflow

The operations API is separate from policy Q&A:

```bash
python scripts/run_operations_demo.py \
  --mode simulation \
  --scenario tool_failure \
  --trace-id <trace-id>
```

`POST /api/operations/investigate` returns:

- SRE investigation ID and thread ID
- affected resources and correlated evidence
- ruled-out hypotheses, probable root cause, and confidence
- Azure Monitor issue status
- Observability Agent next actions with priority, owner, risk, and validation
- `approval_required=true` for every proposed action

In `live` mode, the API calls the real SRE Agent thread and queries the Azure Monitor workspace for an Observability Agent issue. Issue creation and deep investigation are asynchronous. HTTP 202 with `awaiting_issue` means the agent is still correlating alerts; the application never replaces missing live evidence with a simulation result.

When autonomous issue creation remains pending, the UI links to the Azure Monitor fired-alert view. Open the relevant alert, select **Investigate**, and start an on-demand Observability Agent deep investigation. This is the documented portal workflow for obtaining findings and recommended next steps without changing the subscription's shared Azure Monitor workspace setting. There is no documented public REST operation for starting this deep-investigation workflow.

An Observability Agent's `monitoringAccountId` identifies the Azure Monitor workspace where that agent stores issues, so multiple agents and workspaces can coexist in one subscription. The separate `Microsoft.Monitor/settings/default` resource is a fixed-name subscription singleton and cannot point to two workspaces simultaneously. Never overwrite it for this demo without the subscription/workspace owner's explicit approval.

Both Azure SRE Agent and Azure Copilot Observability Agent remain human-controlled. SRE Agent is configured `ReadOnly`; Observability Agent creates issues and recommendations but does not restart resources or change configuration.

The complete Japanese presenter runbook is available at [`docs/demo-script.md`](docs/demo-script.md). It includes pre-demo checks, expected results for every scenario, live SRE/Observability steps, the on-demand investigation fallback, and a shortened 12–15 minute flow.

## Automatic GitHub remediation handoff

Azure Monitor uses the scheduled query rule name in the incident title, not the rule `displayName`. The versioned response plans therefore match `llmops-live-stale-policy` and `llmops-live-tool-failure`. Their definitions are in [`infra/sre-agent/incident-plans.json`](infra/sre-agent/incident-plans.json), and the complete cutover runbook is in [`docs/sre-github-handoff.md`](docs/sre-github-handoff.md).

Azure SRE Agent stays `ReadOnly` and both plans stay in `Review` mode. It can call only two pre-authorized tools on the dedicated broker:

- `github-handoff-broker_submit_incident_handoff`
- `github-handoff-broker_get_handoff_status`

The broker is an isolated FastAPI/MCP Container App under [`src/github-broker/`](src/github-broker/). It accepts only bearer-authenticated, Azure-verified `live` alerts and fixed enum codes; rejects all free-form public prose and extra fields; generates the issue from server-side templates; creates an idempotent fingerprinted issue; and invokes GitHub's official Copilot issue-assignment API. SRE Agent never receives a GitHub write credential and direct GitHub write tools remain globally denied.

`GITHUB_BROKER_TOKEN` must be a fine-grained PAT or GitHub App **user access token** scoped only to this repository with Metadata read and Actions, Contents, Issues, and Pull requests write permissions. GitHub App installation access tokens are not supported by the Copilot assignment API.

Issue creation and Copilot assignment require no per-incident human approval after this bounded broker is enabled. The resulting pull request remains Draft; required CI, review, merge, and deployment remain human-controlled.

Validate and render the post-deployment response-plan parameters without changing Azure:

```bash
python scripts/sre_plan_config.py
python scripts/sre_plan_config.py --emit-global-policy
python scripts/sre_plan_config.py \
  --emit-broker-connector \
  --broker-endpoint "$GITHUB_HANDOFF_BROKER_URL"
python scripts/sre_plan_config.py \
  --emit-tool-parameters \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --agent "$SRE_AGENT_NAME"
```

Apply the rendered objects only after the broker is deployed, `/readyz` succeeds, and the repository-scoped credential is verified. Connectors, Tool Access Policies, and response plans are SRE Agent data-plane configuration and are not represented by the `Microsoft.App/agents` ARM resource.

For an existing plan, `sreagent_incidents_plans_create` returns a conflict rather than updating it. Render the handler update bodies with `python scripts/sre_plan_config.py --emit-handler-payloads`, then update the existing handlers as described in the handoff runbook.

## Telemetry

The application creates spans with these operations:

- Inbound request: `llmops.request`
- Agent call: `invoke_agent policy-agent`
- Retrieval: `retrieve_policy`
- Model call: `chat gpt-5.4-mini`
- Tool call: `execute_tool request_status`
- Operations request: `llmops.operations.request`
- SRE investigation: `invoke_agent azure-sre-agent`
- Next-action handoff: `invoke_agent azure-observability-agent`

Correlation attributes include `gen_ai.conversation.id`, `gen_ai.response.id`, `llmops.scenario`, policy version, token counts, and `error.type`. The API returns these IDs in the JSON body and in `X-Conversation-ID`, `X-Response-ID`, and `X-Trace-ID` headers.

Use the queries under `monitoring/kql/` in this order:

1. `01-overview.kql`
2. `02-failures.kql`
3. `03-latency.kql`
4. `04-token-usage.kql`
5. `05-evaluation-correlation.kql`
6. `06-conversation-detail.kql`
7. `07-sre-investigation-evidence.kql`
8. `08-observability-alert-signals.kql`

## Traffic and evaluation

With the local web server running:

```bash
python scripts/generate_traffic.py --requests 16
python scripts/run_evaluation.py
```

The evaluation runner executes the same JSONL cases with each row's `before_scenario` and `after_scenario`, then writes `artifacts/evaluation-comparison.json`. It compares success rate, policy-version accuracy, answer accuracy, latency compliance, p95 latency, and token use.

`src/policy-agent/eval.yaml` and `.foundry/agent-metadata.yaml` preserve Foundry evaluation intent and local cache locations. Remote Foundry evaluation suite IDs are intentionally not fabricated before deployment.

## Tests

```bash
python -m pytest
```

Focused coverage includes version selection, all four scenarios, correlation headers, strict live failure behavior, telemetry attributes and content defaults, health/readiness, and evaluation comparison.

Pull requests also run [`.github/workflows/ci.yml`](.github/workflows/ci.yml), which executes the Python suite, validates the SRE incident-plan contract, and builds both Bicep entry points. Configure the `main` branch ruleset to require the `Python tests` and `Bicep build` checks plus one approving review before merge.

## Docker

```bash
docker build -f src/web/Dockerfile -t foundry-llmops-demo:local .
docker run --rm -p 8000:8000 foundry-llmops-demo:local
docker build -f src/github-broker/Dockerfile -t sre-github-broker:local .
```

## Azure artifacts

- `azure.yaml` defines the `azure.ai.project`, `azure.ai.agent`, and Container Apps services.
- `infra/main.bicep` is the subscription-scope entry point.
- `infra/modules/resources.bicep` uses pinned Azure Verified Modules for the Foundry account/model, Log Analytics, Application Insights, ACR, managed environment, and Container App.
- The web Container App scales from zero to one replica. The broker keeps one warm replica for alert-time handoffs and uses system- plus user-assigned managed identities.
- ACR admin access is disabled.
- Foundry User and AcrPull are scoped to the web application identity.
- The same Application Insights connection string is injected into the web and hosted-agent services.
- Azure SRE Agent runs in East US 2 and observes the Japan East workload through Reader, Monitoring Reader, and Log Analytics Reader roles.
- Azure Copilot Observability Agent and its Azure Monitor workspace run in East Asia. The monitored resource is the workload Application Insights component.
- The Observability Agent identity has subscription-scope Monitoring Reader to enumerate fired alerts and Issue Contributor only on its Azure Monitor workspace.
- Live stale-policy retrieval and request-status failures create scheduled-query alerts for autonomous issue correlation.
- The GitHub broker is disabled until its dedicated Key Vault contains both secrets and `githubHandoffEnabled=true`.
- SRE and Observability features are preview services. Automatic Observability deep investigations are billable and should be enabled only after cost review.

The Foundry policy agent, SRE/Observability extension, and automatic GitHub broker are deployed. The broker uses Key Vault secrets, the live SRE plans expose only its two bounded tools, and direct GitHub tools are denied. A controlled live test created Issue #7 and Copilot Draft PR #8 automatically. Live SRE investigations return real correlated evidence; missing broker evidence or an uncreated PR is never replaced with simulated success.

## Validation

Safe local validation commands:

```bash
python -m pytest
python scripts/sre_plan_config.py
az bicep build --file infra/main.bicep
az bicep build --file infra/extensions/sre-observability.bicep
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd config show
git grep -nEi '(api[_-]?key|client[_-]?secret|password|connectionstring)[[:space:]]*[:=][[:space:]]*[^<$[:space:]]'
```

Azure deployment commands are deliberately excluded from validation. If deployment is approved later, follow the `microsoft-foundry`, `azure-validate`, and `azure-deploy` workflows rather than running ad hoc commands.
