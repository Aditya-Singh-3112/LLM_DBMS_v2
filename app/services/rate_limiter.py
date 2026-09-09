from typing import Optional

from redis.asyncio import Redis


class RateLimiter:
    """
    Sliding window rate limiter for tool calls.
    
    Uses Redis to track tool calls per user.
    All tool calls (MCP and RAG) count against the same budget.
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
            pipe.incr(key)
            pipe.expire(key, self.window_seconds)
            results = await pipe.execute()

            call_count = results[0]
            remaining = max(0, self.calls_per_minute - call_count)

            if call_count > self.calls_per_minute:
                return False, remaining

            return True, remaining

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
            remaining = max(0, self.calls_per_minute - count)
            return remaining

        except Exception:
            return self.calls_per_minute

    async def reset(self, user_id: str) -> None:
        """Reset rate limit for user (admin operation)."""
        if self.redis is None:
            return

        key = f"ratelimit:{user_id}"

        try:
            await self.redis.delete(key)
        except Exception:
            pass