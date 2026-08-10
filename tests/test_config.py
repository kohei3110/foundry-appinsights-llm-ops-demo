from pathlib import Path

from policy_agent.config import _resolve_default_data_root


def test_default_data_root_uses_repository_data(tmp_path: Path) -> None:
    module_file = tmp_path / "src" / "policy-agent" / "policy_agent" / "config.py"
    repository_data = tmp_path / "data"
    repository_data.mkdir()

    assert _resolve_default_data_root(module_file) == repository_data


def test_default_data_root_supports_container_layout(tmp_path: Path) -> None:
    module_file = tmp_path / "app" / "policy_agent" / "config.py"
    service_data = tmp_path / "app" / "data"
    service_data.mkdir(parents=True)

    assert _resolve_default_data_root(module_file) == service_data