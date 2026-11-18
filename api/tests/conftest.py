import os

import pytest

# Generous limits for the test client; production values come from the environment.
# Set before geodiff modules read settings.
os.environ.setdefault("RATE_LIMIT_WRITES_PER_MIN", "100000")
os.environ.setdefault("RATE_LIMIT_WORKSPACES_PER_HOUR", "100000")
os.environ.setdefault("MAX_ACTIVE_WORKSPACES", "100000")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set; PostGIS integration test skipped")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)
