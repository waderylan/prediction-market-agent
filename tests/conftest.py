import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def load_fixture() -> Any:
    def load(provider: str, name: str) -> Any:
        path = Path(__file__).parent / "fixtures" / provider / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    return load
