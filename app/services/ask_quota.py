from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from redis.asyncio import Redis


class AskQuota:
    """Per-user daily cap on /ask requests (each one costs LLM tokens)."""

    def __init__(self, redis: Optional[Redis], daily_limit: int) -> None:
        self.redis = redis
        self.daily_limit = daily_limit

    async def consume(self, user_id: str) -> None:
        if self.daily_limit <= 0 or self.redis is None:
            return

        now = datetime.now(timezone.utc)
        key = f"askquota:{user_id}:{now:%Y%m%d}"
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        ttl = int((midnight - now).total_seconds()) + 60

        try:
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
            count = int((await pipe.execute())[0])
        except Exception:
            return  # Redis down: don't block users

        if count > self.daily_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily limit of {self.daily_limit} questions reached. Resets at midnight UTC.",
                headers={"Retry-After": str(int((midnight - now).total_seconds()))},
            )
