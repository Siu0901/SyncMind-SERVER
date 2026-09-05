from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def patch_pings(monkeypatch):
    def _patch(db: bool, redis: bool, qdrant: bool):
        monkeypatch.setattr("app.main.db_ping", AsyncMock(return_value=db))
        monkeypatch.setattr("app.main.redis_ping", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.main.qdrant_ping", AsyncMock(return_value=qdrant))

    return _patch


class TestHealthCheck:
    async def test_all_healthy(self, client, patch_pings):
        patch_pings(db=True, redis=True, qdrant=True)

        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "database": True,
            "redis": True,
            "qdrant": True,
        }

    @pytest.mark.parametrize(
        "db, redis, qdrant",
        [
            (False, True, True),
            (True, False, True),
            (True, True, False),
        ],
    )
    async def test_any_dependency_down_marks_error(
        self, client, patch_pings, db, redis, qdrant
    ):
        patch_pings(db=db, redis=redis, qdrant=qdrant)

        response = await client.get("/health")

        body = response.json()
        assert body["status"] == "error"
        assert (body["database"], body["redis"], body["qdrant"]) == (db, redis, qdrant)
