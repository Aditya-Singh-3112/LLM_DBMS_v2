import pytest

from app.services.rate_limiter import RateLimiter


class FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def set(self, key, value, ex=None, nx=False):
        self.ops.append(("set", key, value, ex, nx))
        return self

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "set":
                _, key, value, ex, nx = op
                if nx and key in self.store:
                    results.append(None)
                else:
                    self.store[key] = {"value": value, "ttl": ex}
                    results.append(True)
            elif op[0] == "incr":
                _, key = op
                self.store[key]["value"] += 1
                results.append(self.store[key]["value"])
        return results


class FakeRedis:
    def __init__(self):
        self.store = {}

    def pipeline(self):
        return FakePipeline(self.store)

    async def get(self, key):
        entry = self.store.get(key)
        return None if entry is None else str(entry["value"])

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_blocks_after_limit_and_does_not_extend_window():
    redis = FakeRedis()
    limiter = RateLimiter(redis=redis, calls_per_minute=3)

    results = [await limiter.check_and_increment("u1") for _ in range(4)]
    assert [allowed for allowed, _ in results] == [True, True, True, False]
    assert [remaining for _, remaining in results] == [2, 1, 0, 0]

    # The window TTL was set exactly once (on the first call) and never re-armed.
    assert redis.store["ratelimit:u1"]["ttl"] == 60
    assert await limiter.get_remaining("u1") == 0


@pytest.mark.asyncio
async def test_users_are_independent_and_reset_clears():
    redis = FakeRedis()
    limiter = RateLimiter(redis=redis, calls_per_minute=1)

    assert (await limiter.check_and_increment("a"))[0] is True
    assert (await limiter.check_and_increment("a"))[0] is False
    assert (await limiter.check_and_increment("b"))[0] is True

    await limiter.reset("a")
    assert (await limiter.check_and_increment("a"))[0] is True


@pytest.mark.asyncio
async def test_without_redis_everything_is_allowed():
    limiter = RateLimiter(redis=None, calls_per_minute=1)
    for _ in range(5):
        assert (await limiter.check_and_increment("x"))[0] is True
