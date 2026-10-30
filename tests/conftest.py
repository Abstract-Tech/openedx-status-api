import pytest

from openedx_status_api.config import Settings
from openedx_status_api.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


@pytest.fixture
def settings(tmp_path):
    return Settings(checks_file=str(tmp_path / "checks.yml"), data_dir=str(tmp_path / "data"), fail_threshold=3)
