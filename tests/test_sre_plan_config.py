from __future__ import annotations

from pathlib import Path

from scripts.sre_plan_config import (
    BROKER_TOOLS,
    EXPECTED_PLANS,
    build_broker_connector_parameters,
    build_handler_payloads,
    build_tool_parameters,
    load_config,
    validate_config,
)


def test_sre_incident_plans_use_bounded_automatic_broker():
    config = load_config()

    validate_config(config)

    assert config["broker"]["connector"] == "github-handoff-broker"
    assert set(config["broker"]["allowedTools"]) == BROKER_TOOLS
    assert config["broker"]["github"]["credentialBoundary"]["repositories"] == [
        "kohei3110/foundry-appinsights-llm-ops-demo"
    ]
    assert (
        config["broker"]["github"]["credentialBoundary"][
            "installationAccessTokenSupported"
        ]
        is False
    )
    assert (
        config["broker"]["github"]["trustedIssueCreator"]
        == config["broker"]["github"]["owner"]
    )
    permissions = config["toolAccessPolicy"]["permissions"]
    assert set(permissions["allow"]) == BROKER_TOOLS
    assert permissions["ask"] == []
    assert "github_*" in permissions["deny"]
    assert "github-direct_*" in permissions["deny"]
    assert "github-direct_issue_write" in permissions["deny"]
    assert {
        plan["name"]: plan["trigger-condition"] for plan in config["plans"]
    } == EXPECTED_PLANS
    assert all(plan["agent-mode"] == "review" for plan in config["plans"])


def test_sre_incident_plan_tool_parameters_exclude_local_metadata():
    parameters = build_tool_parameters(
        load_config(),
        subscription="sub",
        resource_group="rg",
        agent="sre-agent",
    )

    assert len(parameters) == 2
    assert all("issue-title-prefix" not in plan for plan in parameters)
    assert all(plan["subscription"] == "sub" for plan in parameters)
    assert all(plan["resource-group"] == "rg" for plan in parameters)
    assert all(plan["agent"] == "sre-agent" for plan in parameters)


def test_existing_plan_handler_payloads_preserve_safety_steps():
    payloads = build_handler_payloads(load_config())

    assert len(payloads) == 2
    for payload in payloads:
        body = payload["body"]
        incident_filter = payload["filter"]
        assert payload["handler-id"] == f"{body['name']}-handler"
        assert incident_filter["id"] == body["name"]
        assert incident_filter["titleContains"] in EXPECTED_PLANS.values()
        assert incident_filter["agentMode"] == "review"
        assert incident_filter["isEnabled"] is True
        assert incident_filter["mergeEnabled"] is False
        assert incident_filter["impactedService"] == "policy-agent"
        assert incident_filter["handlingAgent"] == ""
        assert body["incidentFilterId"] == body["name"]
        assert set(body["tools"]) == BROKER_TOOLS
        assert len(body["incidentProcessingGuide"]) == 6
        assert "broker, not SRE Agent" in body["customInstructions"]
        assert "Never call GitHub write tools" in body["customInstructions"]


def test_broker_connector_parameters_are_https_and_secret_indirect():
    parameters = build_broker_connector_parameters(
        load_config(),
        endpoint="https://broker.example.com",
    )

    assert parameters == {
        "name": "github-handoff-broker",
        "type": "http",
        "endpoint": (
            "https://broker.example.com/mcp/github-handoff"
        ),
        "auth-type": "BearerToken",
        "bearer-token-env": "SRE_GITHUB_HANDOFF_BEARER_TOKEN",
    }


def test_tool_failure_alert_matches_only_dependency_unavailability():
    root = Path(__file__).resolve().parents[1]
    for path in (
        root / "infra" / "modules" / "resources.bicep",
        root / "infra" / "extensions" / "sre-observability.bicep",
    ):
        content = path.read_text(encoding="utf-8")
        assert (
            'tostring(customDimensions["error.type"]) == '
            '"request_status_unavailable"'
        ) in content
        assert (
            'where success == false or '
            'isnotempty(customDimensions["error.type"])'
        ) not in content
