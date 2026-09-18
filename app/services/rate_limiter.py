from typing import Optional

from redis.asyncio import Redis


class RateLimiter:
    """
    Fixed-window rate limiter for tool calls, keyed per user.

    The window starts on the first call and lasts `window_seconds`; the
    counter is never extended by later calls, so a busy user is unblocked
    at most one window after they hit the limit.
    """

    def __init__(
        self,
        redis: Optional[Redis] = None,
        calls_per_minute: int = 60,
    ) -> None:
        self.redis = redis
        self.calls_per_minute = calls_per_minute
        self.window_seconds = 60

    async def check_and_increment(self, user_id: str) -> tuple[bool, int]:
        """
        Check if user is within rate limit, and increment counter if allowed.

        Returns:
            (allowed, remaining_calls)
        """
        if self.redis is None:
            return True, self.calls_per_minute

        key = f"ratelimit:{user_id}"

        try:
            pipe = self.redis.pipeline()
            # Start the window only when the key is new; NX keeps the TTL of
            # an existing window untouched.
            pipe.set(key, 0, ex=self.window_seconds, nx=True)
            pipe.incr(key)
            results = await pipe.execute()

            call_count = int(results[1])
            remaining = max(0, self.calls_per_minute - call_count)

            return call_count <= self.calls_per_minute, remaining

        except Exception:
            return True, self.calls_per_minute

    async def get_remaining(self, user_id: str) -> int:
        """Get remaining calls for user without incrementing."""
        if self.redis is None:
            return self.calls_per_minute

        key = f"ratelimit:{user_id}"

        try:
            count = await self.redis.get(key)
            count = int(count) if count else 0
            return max(0, self.calls_per_minute - count)

        except Exception:
            return self.calls_per_minute

    async def reset(self, user_id: str) -> None:
        """Reset rate limit for user (admin operation)."""
        if self.redis is None:
            return

        try:
            await self.redis.delete(f"ratelimit:{user_id}")
        except Exception:
            pass
