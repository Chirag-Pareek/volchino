"""Test cache stage TTL behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.pipeline import cache as cache_stage
from server.pipeline.cache import Cache
from server.pipeline.types import Outcome


class FakeClock:
    def __init__(self):
        self.now_val = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now_val

    def advance(self, seconds: float):
        self.now_val += timedelta(seconds=seconds)


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def cache(db, clock):
    return Cache(db, now=clock)


async def test_set_and_get(cache):
    await cache.set("k1", {"x": 1}, ttl_s=60)
    assert await cache.get("k1") == {"x": 1}


async def test_expired(cache, clock):
    await cache.set("k1", {"x": 1}, ttl_s=10)
    clock.advance(11)
    assert await cache.get("k1") is None


async def test_not_expired(cache, clock):
    await cache.set("k1", {"x": 1}, ttl_s=10)
    clock.advance(5)
    assert await cache.get("k1") == {"x": 1}


async def test_purge(cache, clock):
    await cache.set("a", 1, ttl_s=5)
    await cache.set("b", 2, ttl_s=20)
    clock.advance(10)
    n = await cache.purge_expired()
    assert n == 1
    assert await cache.get("a") is None
    assert await cache.get("b") == 2


async def test_lookup_and_store(cache):
    assert await cache_stage.lookup(cache, "test") is None
    outcome = Outcome(
        text="Battery 42%.", stage="tool", intent="get_battery_status", tool="get_battery_status"
    )
    await cache_stage.store(cache, "test", outcome, 30)
    hit = await cache_stage.lookup(cache, "test")
    assert hit is not None
    assert hit.text == "Battery 42%."
    assert hit.stage == "cache"
