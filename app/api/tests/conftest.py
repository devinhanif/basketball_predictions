from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.main import create_app, get_store


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    get_store.cache_clear()
    with TestClient(create_app()) as c:
        yield c
