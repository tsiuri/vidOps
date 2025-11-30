from __future__ import annotations

import os
from pathlib import Path

import pytest

from vidops import config as vidops_config
from tests.smoke.helpers import db_available


@pytest.fixture(scope="session", autouse=True)
def smoke_storage_root():
    """
    Ensure the storage root used by FilesystemCache points to a writable location.
    """
    root = Path("tmp/smoke_storage").resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["VIDOPS_CENTRAL_STORAGE"] = str(root)
    vidops_config._config_instance = None
    yield root
    vidops_config._config_instance = None


@pytest.fixture
def smoke_env(smoke_storage_root):
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(Path.cwd()))
    env["VIDOPS_CENTRAL_STORAGE"] = str(smoke_storage_root)
    env.setdefault("VIDOPS_WORKER_MAX_JOBS", "1")
    env.setdefault("VIDOPS_WORKER_HEARTBEAT_INTERVAL", "1")
    return env


def pytest_runtest_setup(item):
    if "smoke" in item.keywords and not db_available():
        pytest.skip("database not available for smoke tests")
