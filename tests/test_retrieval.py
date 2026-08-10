from policy_agent.models import Scenario
from policy_agent.repository import PolicyRepository


def test_retrieval_selects_current_policy_by_default(settings):
    repository = PolicyRepository(settings.data_root)

    document = repository.retrieve("精算期限は？", Scenario.HEALTHY)

    assert document.version == "2026-07-01"
    assert document.status == "current"
    assert document.reimbursement_days == 30


def test_stale_scenario_selects_superseded_policy(settings):
    repository = PolicyRepository(settings.data_root)

    document = repository.retrieve("精算期限は？", Scenario.STALE_POLICY)

    assert document.version == "2025-04-01"
    assert document.status == "superseded"
    assert document.reimbursement_days == 14
