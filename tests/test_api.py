from fastapi.testclient import TestClient

from webapp.main import create_app


def test_root_page_loads_monitoring_ui(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "SRE Agent" in response.text
    assert "Observability Agent" in response.text


def test_health_and_simulation_readiness(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        ready = client.get("/readyz?mode=simulation")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "mode": "simulation"}


def test_live_readiness_fails_when_not_configured(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz?mode=live")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_api_returns_correlation_ids_in_body_and_headers(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/ask",
            json={
                "question": "精算期限は？",
                "mode": "simulation",
                "scenario": "healthy",
                "conversation_id": "conv_demo_fixed",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["conversation_id"] == "conv_demo_fixed"
    assert response.headers["X-Conversation-ID"] == "conv_demo_fixed"
    assert response.headers["X-Response-ID"] == payload["response_id"]
    assert response.headers["X-Trace-ID"] == payload["trace_id"]
    assert len(payload["trace_id"]) == 32
    assert int(payload["trace_id"], 16) != 0


def test_tool_failure_api_preserves_correlation_fields(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/ask",
            json={
                "question": "精算期限は？",
                "mode": "simulation",
                "scenario": "tool_failure",
            },
        )

    payload = response.json()
    assert response.status_code == 502
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "request_status_failure"
    assert payload["conversation_id"].startswith("conv_")
    assert payload["response_id"].startswith("errresp_")
    assert response.headers["X-Trace-ID"] == payload["trace_id"]


def test_operations_simulation_returns_evidence_and_actions(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/operations/investigate",
            json={
                "mode": "simulation",
                "scenario": "tool_failure",
                "trace_id": "1234567890abcdef1234567890abcdef",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["investigation"]["evidence"]
    assert payload["decision"]["next_actions"]
    assert all(
        action["approval_required"]
        for action in payload["decision"]["next_actions"]
    )
    assert response.headers["X-Trace-ID"] == payload["trace_id"]


def test_operations_live_readiness_fails_when_not_configured(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/operations/readiness?mode=live")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
