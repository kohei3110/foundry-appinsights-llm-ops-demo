# Automatic Azure SRE Agent to GitHub handoff

This runbook covers the alert-driven path from Azure Monitor to a GitHub issue and GitHub Copilot draft pull request without per-incident approval.

## Architecture and safety boundary

```text
Azure Monitor alert
  -> Azure SRE Agent read-only investigation
  -> strict sanitized MCP payload
  -> deterministic GitHub handoff broker
  -> GitHub issue
  -> Copilot assignment
  -> Draft pull request
  -> human CI review, merge, and deploy
```

- Keep Azure SRE Agent action configuration `ReadOnly`.
- Keep each incident response plan in `Review` mode for Azure operations.
- Pre-authorize only the broker's `submit_incident_handoff` and `get_handoff_status` tools.
- Globally deny direct GitHub issue writes, Copilot assignment, repository writes, PR writes, merges, workflow dispatch, push, update, and delete tools.
- The broker owns the only GitHub write credential. SRE Agent never receives it.
- The broker accepts only `mode=live`, the two allowlisted alert rules, allowlisted evidence/hypothesis/root-cause enum codes, a confidence band, and a 12-character fingerprint. It accepts no free-form public prose.
- The structured `alert_id` is never published. The broker identity has subscription-scope Monitoring Reader only so it can query the canonical Alerts Management endpoint, then verifies the rule, Sev2 state, exact target Application Insights resource, and maximum alert age before any GitHub call.
- Summary, evidence text, excluded-hypothesis text, root cause, expected change, validation, and safety text are generated only from broker-owned templates.
- Never send trace IDs, resource IDs, raw telemetry, prompts, output content, customer data, credentials, tokens, or connection strings.
- Never replace missing evidence, broker failure, an uncreated issue, an unassigned Copilot task, or an uncreated pull request with simulation success.
- Pull requests remain Draft. Review, Ready state, merge, and deployment always require a human.

## GitHub credential

Use either:

- a fine-grained PAT; or
- a GitHub App **user access token**.

Scope it only to `kohei3110/foundry-appinsights-llm-ops-demo` with:

- Metadata: read;
- Actions: read/write;
- Contents: read/write;
- Issues: read/write;
- Pull requests: read/write.

The user access token must create issues as `kohei3110`; broker retries reuse only issues with that trusted creator.

GitHub App installation access tokens are explicitly unsupported by the Copilot assignment API. Do not use a classic `repo` token for the broker.

Store the token only as `github-broker-token` in the dedicated broker Key Vault. Store a separate high-entropy bearer value as `github-handoff-bearer-token` in the same vault and in the SRE MCP connector. Container App secrets use versionless Key Vault references through a dedicated user-assigned identity. Secret values never cross Bicep/AVM parameters or nested deployment history. Never place either value in source, Bicep parameter files, AZD outputs, logs, issue bodies, or test data.

## Broker contract

The broker exposes:

| Endpoint/tool | Purpose |
|---|---|
| `GET /healthz` | Process health |
| `GET /readyz` | Secret/configuration readiness |
| `POST /api/handoffs` | Authenticated direct handoff |
| `GET /api/handoffs/issues/{number}` | Authenticated issue/PR status |
| `POST /mcp/github-handoff` | Streamable HTTP MCP endpoint |
| `submit_incident_handoff` | Idempotently create the issue and assign Copilot |
| `get_handoff_status` | Verify the linked Draft PR |

Issue titles include a broker-verified deterministic fingerprint: the first 12 hex characters of SHA-256 over the alert rule, root-cause code, and sorted evidence codes. Retries therefore reuse the same open issue while distinct classifications create distinct work items. The broker reuses only issues whose creator is the configured repository owner; a public user cannot pre-create a trusted work item by copying the title. A single broker replica and per-title lock serialize concurrent retries. Current `Fired` and recently `Resolved` alerts are accepted only within the configured 60-minute provenance window; resolved alerts use their resolution time.

The assignment uses GitHub's official public-preview Issues API extension:

```text
POST /repos/{owner}/{repo}/issues/{issue_number}/assignees
assignees = ["copilot-swe-agent[bot]"]
agent_assignment.target_repo = "kohei3110/foundry-appinsights-llm-ops-demo"
agent_assignment.base_branch = "main"
```

## Deployment and cutover order

The broker is disabled by default. Do not change the live SRE plans until every prior step passes.

1. Review cost, preview API, GitHub token, and secret-handling requirements.
2. Set `GITHUB_HANDOFF_ENABLED=false`, then provision the broker Container App, user-assigned identity, and dedicated Key Vault.
3. With explicit secret-owner approval, populate `github-handoff-bearer-token` and `github-broker-token` in `GITHUB_HANDOFF_KEY_VAULT_NAME`. Do not pass their values to Bicep or AZD.
4. Set `GITHUB_HANDOFF_ENABLED=true`, provision the Key Vault references/RBAC, and deploy the `github-broker` service.
5. Verify `/healthz` and authenticated `/readyz`.
6. Validate the versioned configuration:

   ```bash
   python scripts/sre_plan_config.py
   ```

7. Set `SRE_GITHUB_HANDOFF_BEARER_TOKEN` in the operator environment and render the connector:

   ```bash
   python scripts/sre_plan_config.py \
     --emit-broker-connector \
     --broker-endpoint "$GITHUB_HANDOFF_BROKER_URL"
   ```

8. Create/test the `github-handoff-broker` MCP connector and verify only the two broker tools are visible.
9. Render and reconcile the global Tool Access Policy:

   ```bash
   python scripts/sre_plan_config.py --emit-global-policy
   ```

   Use `PUT /api/v2/agent/settings/global` with the current `ETag` in `If-Match`.

10. Render missing response plans:

   ```bash
   python scripts/sre_plan_config.py \
     --emit-tool-parameters \
     --subscription "$AZURE_SUBSCRIPTION_ID" \
     --resource-group "$AZURE_RESOURCE_GROUP" \
     --agent "$SRE_AGENT_NAME"
   ```

11. For existing plans, render and POST the filter and handler updates:

    ```bash
    python scripts/sre_plan_config.py --emit-handler-payloads
    ```

12. Hide all tools on legacy broad/direct GitHub connectors.
13. Disable response-plan thread merging for these low-volume demo alerts. Broker fingerprinting provides GitHub idempotency, while a fresh SRE thread ensures current connector tools are loaded.
14. Verify only the two intended response plans match the alert rules; disable/delete the default quickstart plan.
15. Fire a controlled `live` test alert. Verify Issue -> Copilot assignment -> Draft PR. Do not merge or deploy during the test.

No `azd provision`, `azd deploy`, or live cutover is performed by repository validation.

## Expected asynchronous states

- `copilot_assigned`: issue exists and Copilot assignment was accepted.
- `copilot_already_assigned`: a safe retry found the existing assignment.
- `draft_pr_ready`: a linked Copilot PR exists and is Draft.
- `incomplete`: the issue exists but no Copilot assignment or Draft PR can be verified.

The SRE plan polls no faster than every 30 seconds for at most 10 minutes. Timeout is an incomplete asynchronous handoff, not success.

## GitHub quality gate

The `main` branch requires:

- pull requests;
- one approving review;
- `Python tests`;
- `Bicep build`;
- resolved review conversations;
- blocked force pushes and branch deletion.

The broker cannot bypass these gates.
