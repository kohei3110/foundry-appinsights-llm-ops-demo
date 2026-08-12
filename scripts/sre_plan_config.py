from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "infra" / "sre-agent" / "incident-plans.json"

EXPECTED_PLANS = {
    "llmops-stale-policy-review": "llmops-live-stale-policy",
    "llmops-tool-failure-review": "llmops-live-tool-failure",
}
BROKER_TOOLS = {
    "github-handoff-broker_submit_incident_handoff",
    "github-handoff-broker_get_handoff_status",
}
PLAN_TOOL_FIELDS = {
    "name",
    "severity",
    "trigger-condition",
    "services",
    "steps",
    "escalation",
    "runbook-url",
    "agent-mode",
}


class SrePlanConfigError(ValueError):
    pass


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schemaVersion") != 2:
        raise SrePlanConfigError("schemaVersion must be 2")

    broker = config.get("broker")
    if not isinstance(broker, dict):
        raise SrePlanConfigError("broker configuration is required")
    if broker.get("connector") != "github-handoff-broker":
        raise SrePlanConfigError("only the dedicated handoff broker is allowed")
    if broker.get("endpointPath") != "/mcp/github-handoff":
        raise SrePlanConfigError("broker endpoint path is invalid")
    if set(broker.get("allowedTools", [])) != BROKER_TOOLS:
        raise SrePlanConfigError("broker tools must match the bounded set")

    github = broker.get("github")
    if not isinstance(github, dict):
        raise SrePlanConfigError("broker GitHub boundary is required")
    repository = f"{github.get('owner')}/{github.get('repository')}"
    if github.get("trustedIssueCreator") != github.get("owner"):
        raise SrePlanConfigError(
            "trusted issue creator must be the configured repository owner"
        )
    boundary = github.get("credentialBoundary", {})
    if boundary.get("repositories") != [repository]:
        raise SrePlanConfigError(
            "GitHub credential must be scoped to the target repository"
        )
    if boundary.get("installationAccessTokenSupported") is not False:
        raise SrePlanConfigError(
            "Copilot assignment must reject installation access tokens"
        )
    required_permissions = {
        "metadata": "read",
        "actions": "write",
        "contents": "write",
        "issues": "write",
        "pullRequests": "write",
    }
    if boundary.get("permissions") != required_permissions:
        raise SrePlanConfigError(
            "GitHub credential permissions do not match Copilot requirements"
        )

    permissions = config.get("toolAccessPolicy", {}).get("permissions", {})
    if set(permissions.get("allow", [])) != BROKER_TOOLS:
        raise SrePlanConfigError(
            "only the broker submit and status tools may bypass approval"
        )
    if permissions.get("ask") != []:
        raise SrePlanConfigError("broker mode must not retain GitHub write asks")
    required_deny = {
        "github_*",
        "github-direct_*",
        "github-direct_issue_write",
        "github-direct_assign_copilot_to_issue",
        "github-direct_create_*",
        "github-direct_delete_*",
        "github-direct_merge_*",
        "github-direct_push_*",
        "github-direct_update_*",
    }
    if not required_deny.issubset(permissions.get("deny", [])):
        raise SrePlanConfigError(
            "direct GitHub write tools must remain globally denied"
        )

    plans = config.get("plans")
    if not isinstance(plans, list):
        raise SrePlanConfigError("plans must be a list")
    names = {plan.get("name") for plan in plans if isinstance(plan, dict)}
    if names != set(EXPECTED_PLANS):
        raise SrePlanConfigError("both expected incident plans are required")

    prefixes: set[str] = set()
    for plan in plans:
        if not isinstance(plan, dict):
            raise SrePlanConfigError("each plan must be an object")
        name = plan["name"]
        if plan.get("trigger-condition") != EXPECTED_PLANS[name]:
            raise SrePlanConfigError(
                f"{name} must match the Azure Monitor alert rule name"
            )
        if plan.get("severity") != "medium":
            raise SrePlanConfigError(f"{name} must match Sev2 alerts")
        if plan.get("agent-mode") != "review":
            raise SrePlanConfigError(f"{name} must keep Azure review mode")
        if plan.get("services") != ["policy-agent"]:
            raise SrePlanConfigError(f"{name} must target only policy-agent")
        if plan.get("handling-agent") != "":
            raise SrePlanConfigError(
                f"{name} must use the SRE meta-agent handler"
            )
        if plan.get("merge-enabled") is not False:
            raise SrePlanConfigError(
                f"{name} must create a fresh thread for each alert fire"
            )

        prefix = plan.get("issue-title-prefix")
        if not isinstance(prefix, str) or not prefix.startswith(
            "[SRE-AUTO][OPS-REVIEW]"
        ):
            raise SrePlanConfigError(f"{name} has an invalid issue title prefix")
        if prefix in prefixes:
            raise SrePlanConfigError("issue title prefixes must be unique")
        prefixes.add(prefix)

        steps = plan.get("steps")
        if not isinstance(steps, list) or not all(
            isinstance(step, str) for step in steps
        ):
            raise SrePlanConfigError(f"{name} steps must be a string list")
        instructions = "\n".join(steps)
        required_phrases = {
            "read-only mode",
            "at most three direct evidence items",
            "mode=live",
            "alert_id copied exactly from the Azure Monitor incident card",
            "broker-side Managed Identity verification",
            "first 12 lowercase hexadecimal characters of SHA-256",
            "comma-separated evidence_codes sorted lexically",
            "broker recomputes it",
            "no free-form public text",
            "evidence_codes",
            "ruled_out_codes",
            "root_cause_code",
            "server-side templates",
            "github-handoff-broker_submit_incident_handoff",
            "broker, not SRE Agent",
            "Never call a GitHub write tool directly",
            "github-handoff-broker_get_handoff_status",
            "at intervals of at least 30 seconds",
            "for at most 10 minutes",
            "pull_request_is_draft=true",
            "incomplete asynchronous handoff, not success",
            "merge a pull request",
            "deploy",
            "modify Azure",
            "uncreated issue",
            "unassigned Copilot task",
            "uncreated pull request",
        }
        missing = sorted(
            phrase for phrase in required_phrases if phrase not in instructions
        )
        if missing:
            raise SrePlanConfigError(
                f"{name} is missing safety instructions: {', '.join(missing)}"
            )


def build_tool_parameters(
    config: dict[str, Any],
    *,
    subscription: str,
    resource_group: str,
    agent: str,
) -> list[dict[str, Any]]:
    validate_config(config)
    context = {
        "subscription": subscription,
        "resource-group": resource_group,
        "agent": agent,
    }
    return [
        {
            **{key: value for key, value in plan.items() if key in PLAN_TOOL_FIELDS},
            **context,
        }
        for plan in config["plans"]
    ]


def build_handler_payloads(
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    validate_config(config)
    connector = config["broker"]["connector"]
    broker_tools = sorted(config["broker"]["allowedTools"])
    return [
        {
            "filter": {
                "id": plan["name"],
                "name": plan["name"],
                "impactedService": plan["services"][0],
                "handlingAgent": plan["handling-agent"],
                "priorities": ["P2", "P3"],
                "titleContains": plan["trigger-condition"],
                "isEnabled": True,
                "agentMode": plan["agent-mode"],
                "maxAutomatedInvestigationAttempts": 3,
                "mergeEnabled": plan["merge-enabled"],
                "mergeWindowHours": 3,
            },
            "handler-id": f"{plan['name']}-handler",
            "body": {
                "id": f"{plan['name']}-handler",
                "name": plan["name"],
                "description": (
                    "Read-only Azure investigation with deterministic "
                    "automatic GitHub broker handoff"
                ),
                "incidentFilterId": plan["name"],
                "customInstructions": (
                    "Investigate Azure only in read-only mode. Send only the "
                    f"strict sanitized contract through {connector}. The broker, "
                    "not SRE Agent, owns GitHub writes. Never call GitHub write "
                    "tools, merge, deploy, trigger workflows, modify Azure, or "
                    "substitute simulation evidence."
                ),
                "incidentProcessingGuide": plan["steps"],
                "tools": broker_tools,
            },
        }
        for plan in config["plans"]
    ]


def build_broker_connector_parameters(
    config: dict[str, Any],
    *,
    endpoint: str,
) -> dict[str, Any]:
    validate_config(config)
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise SrePlanConfigError("broker endpoint must be an HTTPS origin")
    broker = config["broker"]
    return {
        "name": broker["connector"],
        "type": "http",
        "endpoint": (
            f"{endpoint.rstrip('/')}{broker['endpointPath']}"
        ),
        "auth-type": "BearerToken",
        "bearer-token-env": broker["bearerTokenEnvironment"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and render Azure SRE Agent incident plans."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--emit-tool-parameters", action="store_true")
    output_group.add_argument("--emit-handler-payloads", action="store_true")
    output_group.add_argument("--emit-global-policy", action="store_true")
    output_group.add_argument("--emit-broker-connector", action="store_true")
    parser.add_argument("--subscription")
    parser.add_argument("--resource-group")
    parser.add_argument("--agent")
    parser.add_argument("--broker-endpoint")
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config)
    if args.emit_global_policy:
        print(json.dumps(config["toolAccessPolicy"], ensure_ascii=True, indent=2))
        return
    if args.emit_handler_payloads:
        print(
            json.dumps(
                build_handler_payloads(config),
                ensure_ascii=True,
                indent=2,
            )
        )
        return
    if args.emit_broker_connector:
        if not args.broker_endpoint:
            parser.error("--emit-broker-connector requires --broker-endpoint")
        print(
            json.dumps(
                build_broker_connector_parameters(
                    config,
                    endpoint=args.broker_endpoint,
                ),
                ensure_ascii=True,
                indent=2,
            )
        )
        return
    if not args.emit_tool_parameters:
        print(f"Valid SRE incident plan configuration: {args.config}")
        return

    missing = [
        name
        for name in ("subscription", "resource_group", "agent")
        if not getattr(args, name)
    ]
    if missing:
        parser.error(
            "--emit-tool-parameters requires --subscription, --resource-group, "
            "and --agent"
        )
    parameters = build_tool_parameters(
        config,
        subscription=args.subscription,
        resource_group=args.resource_group,
        agent=args.agent,
    )
    print(json.dumps(parameters, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
