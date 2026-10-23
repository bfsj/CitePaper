from pathlib import Path

import pytest

from cite_paper.store import Store


@pytest.fixture
def store(tmp_path: Path):
    return Store(tmp_path / "test.sqlite3")
