import hashlib
import json
from typing import Any, Optional

from redis.asyncio import Redis


class CacheService:
    """
    Caches tool results by content hash.
    
    For MCP tools: key = hash(database_id + tool_name + args), TTL 1h
    For RAG lookups: key = hash(query + k), TTL 24h
    """

    MCP_CACHE_TTL = 3600  # 1 hour
    RAG_CACHE_TTL = 86400  # 24 hours

    def __init__(self, redis: Optional[Redis] = None) -> None:
        self.redis = redis

    async def get_mcp_result(
        self,
        database_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> Optional[Any]:
        """Get cached MCP tool result."""
        if self.redis is None:
            return None

        key = self._mcp_cache_key(database_id, tool_name, args)

        try:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

        return None

    async def set_mcp_result(
        self,
        database_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        """Cache MCP tool result."""
        if self.redis is None:
            return

        key = self._mcp_cache_key(database_id, tool_name, args)

        try:
            await self.redis.setex(
                key,
                self.MCP_CACHE_TTL,
                json.dumps(result, default=str),
            )
        except Exception:
            pass

    async def invalidate_mcp_cache(
        self,
        database_id: str,
    ) -> None:
        """Invalidate all MCP cache for a database (on mutation)."""
        if self.redis is None:
            return

        pattern = f"mcp_cache:{database_id}:*"

        try:
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
        except Exception:
            pass

    async def get_rag_result(
        self,
        query: str,
        k: int,
    ) -> Optional[Any]:
        """Get cached RAG retrieval result."""
        if self.redis is None:
            return None

        key = self._rag_cache_key(query, k)

        try:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

        return None

    async def set_rag_result(
        self,
        query: str,
        k: int,
        result: Any,
    ) -> None:
        """Cache RAG retrieval result."""
        if self.redis is None:
            return

        key = self._rag_cache_key(query, k)

        try:
            await self.redis.setex(
                key,
                self.RAG_CACHE_TTL,
                json.dumps(result, default=str),
            )
        except Exception:
            pass

    @staticmethod
    def _mcp_cache_key(
        database_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        """Generate MCP cache key from database, tool, and args."""
        normalized_args = json.dumps(args, sort_keys=True, default=str)
        args_hash = hashlib.md5(normalized_args.encode()).hexdigest()
        return f"mcp_cache:{database_id}:{tool_name}:{args_hash}"

    @staticmethod
    def _rag_cache_key(query: str, k: int) -> str:
        """Generate RAG cache key from query and k."""
        normalized = query.lower().strip()
        query_hash = hashlib.md5(normalized.encode()).hexdigest()
        return f"rag_cache:{query_hash}:{k}"