import json

import pytest

from policy_agent.models import Scenario
from policy_agent.repository import PolicyRepository, PolicyUnavailableError


def test_retrieval_selects_current_policy_by_default(settings):
    repository = PolicyRepository(settings.data_root)

    document = repository.retrieve("精算期限は？", Scenario.HEALTHY)

    assert document.version == "2026-07-01"
    assert document.status == "current"
    assert document.reimbursement_days == 30


def test_stale_scenario_selects_current_policy(settings):
    repository = PolicyRepository(settings.data_root)

    document = repository.retrieve("精算期限は？", Scenario.STALE_POLICY)

    assert document.version == "2026-07-01"
    assert document.status == "current"
    assert document.reimbursement_days == 30


def test_select_raises_when_no_current_policy_is_configured(tmp_path):
    policies_root = tmp_path / "policies"
    policies_root.mkdir()
    (policies_root / "index.json").write_text(
        json.dumps(
            [
                {
                    "document_id": "travel-expense-policy",
                    "title": "Superseded policy",
                    "version": "2025-04-01",
                    "effective_date": "2025-04-01",
                    "status": "superseded",
                    "path": "superseded.md",
                    "reimbursement_days": 14,
                }
            ]
        ),
        encoding="utf-8",
    )
    (policies_root / "superseded.md").write_text("superseded", encoding="utf-8")

    repository = PolicyRepository(tmp_path)

    with pytest.raises(PolicyUnavailableError, match="No current policy"):
        repository.select(Scenario.STALE_POLICY)
