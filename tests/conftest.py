from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = ""

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "policy-agent"))
sys.path.insert(0, str(ROOT / "src" / "web"))
sys.path.insert(0, str(ROOT))

from policy_agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        data_root=ROOT / "data",
        slow_tool_delay_seconds=0.01,
        applicationinsights_connection_string=None,
    )
