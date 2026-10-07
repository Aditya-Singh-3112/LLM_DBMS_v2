import asyncio

import httpx
from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.core.database import DatabaseManager

router = APIRouter(tags = ["health"])

CHECK_TIMEOUT_SECONDS = 2
# The API cannot serve anything without these; the rest degrade features.
CRITICAL = ("postgres", "mongo")


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """The process is up. For restart decisions; does not touch dependencies."""
    return {"status": "alive"}


@router.get("/health")
async def health_check(request: Request, response: Response) -> dict[str, str]:
    """
    Readiness: pings every dependency. 503 when Postgres or Mongo is down;
    "degraded" (still 200) when only Redis or the MCP server is, since most
    of the API keeps working without them.
    """
    database_manager: DatabaseManager = request.app.state.database_manager

    async def postgres() -> None:
        async with database_manager.postgres_connection() as connection:
            await connection.fetchval("SELECT 1")

    async def mongo() -> None:
        await database_manager.mongo_client.admin.command("ping")

    async def redis() -> None:
        await database_manager.redis.ping()

    async def mcp() -> None:
        url = httpx.URL(get_settings().mcp_server_url).copy_with(path="/health")
        transport = getattr(request.app.state, "mcp_transport", None)
        async with httpx.AsyncClient(transport=transport, timeout=CHECK_TIMEOUT_SECONDS) as client:
            (await client.get(url)).raise_for_status()

    checks = {"postgres": postgres, "mongo": mongo, "redis": redis, "mcp": mcp}
    results = await asyncio.gather(*(_probe(check) for check in checks.values()))
    report = dict(zip(checks, results))

    if any(report[name] != "ok" for name in CRITICAL):
        overall = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif any(result != "ok" for result in results):
        overall = "degraded"
    else:
        overall = "ok"
    return {"status": overall, **report}


async def _probe(check) -> str:
    try:
        await asyncio.wait_for(check(), CHECK_TIMEOUT_SECONDS)
        return "ok"
    except Exception:
        return "unavailable"
