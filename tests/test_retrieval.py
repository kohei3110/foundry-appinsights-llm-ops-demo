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


def test_stale_scenario_never_selects_superseded_policy(settings):
    repository = PolicyRepository(settings.data_root)

    document = repository.retrieve("精算期限は？", Scenario.STALE_POLICY)

    assert document.version == "2026-07-01"
    assert document.status == "current"
    assert document.reimbursement_days == 30


def test_retrieval_fails_explicitly_without_current_policy(
    settings, tmp_path
):
    policies_root = tmp_path / "policies"
    policies_root.mkdir()
    index = json.loads(
        (settings.data_root / "policies" / "index.json").read_text(
            encoding="utf-8"
        )
    )
    superseded = [row for row in index if row["status"] == "superseded"]
    (policies_root / "index.json").write_text(
        json.dumps(superseded, ensure_ascii=False), encoding="utf-8"
    )
    for row in superseded:
        (policies_root / row["path"]).write_text(
            (settings.data_root / "policies" / row["path"]).read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
    repository = PolicyRepository(tmp_path)

    with pytest.raises(PolicyUnavailableError):
        repository.retrieve("精算期限は？", Scenario.STALE_POLICY)
