from typing import Optional

from fastapi import HTTPException, status
from redis.asyncio import Redis


class LoginThrottle:
    """
    Locks out an email after too many failed logins, and a client IP after
    too many failures across any emails. Checked before the password is
    verified, so a locked-out guesser costs no hashing either.

    Fails open if Redis is unavailable, like the other limiters.
    """

    def __init__(
        self,
        redis: Optional[Redis],
        max_failures: int,
        max_failures_per_ip: int,
        window_seconds: int,
    ) -> None:
        self.redis = redis
        self.max_failures = max_failures
        self.max_failures_per_ip = max_failures_per_ip
        self.window_seconds = window_seconds

    async def check(self, email: str, client_ip: str | None) -> None:
        if self.redis is None:
            return
        try:
            for key, limit in self._keys(email, client_ip):
                count = int(await self.redis.get(key) or 0)
                if count >= limit:
                    ttl = await self.redis.ttl(key)
                    retry_after = max(int(ttl), 1)
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=(
                            "Too many failed sign-in attempts. "
                            f"Try again in {max(retry_after // 60, 1)} minute(s), or reset your password."
                        ),
                        headers={"Retry-After": str(retry_after)},
                    )
        except HTTPException:
            raise
        except Exception:
            return

    async def record_failure(self, email: str, client_ip: str | None) -> None:
        if self.redis is None:
            return
        try:
            pipe = self.redis.pipeline()
            for key, _ in self._keys(email, client_ip):
                pipe.incr(key)
                pipe.expire(key, self.window_seconds, nx=True)
            await pipe.execute()
        except Exception:
            return

    async def clear(self, email: str) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.delete(self._email_key(email))
        except Exception:
            return

    def _keys(self, email: str, client_ip: str | None) -> list[tuple[str, int]]:
        keys = [(self._email_key(email), self.max_failures)]
        if client_ip:
            keys.append((f"loginfail:ip:{client_ip}", self.max_failures_per_ip))
        return keys

    @staticmethod
    def _email_key(email: str) -> str:
        return f"loginfail:email:{email.lower()}"
