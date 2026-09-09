from fastapi import APIRouter, Request

router = APIRouter(tags = ["health"])

@router.get("/health")
async def health_check(request: Request) -> dict[str, str]:
    database_manager = request.app.state.database_manager

    postgres_status = "unknown"
    redis_status = "unknown"
    mongo_status = "unknown"

    if database_manager.postgres_pool is not None:
        postgres_status = "connected"

    if database_manager.redis is not None:
        try:
            await database_manager.redis.ping()
            redis_status = "connected"
        except Exception:
            redis_status = "unavailable"

    if database_manager.mongo_client is not None:
        try:
            await database_manager.mongo_client.admin.command("ping")
            mongo_status = "connected"
        except Exception:
            mongo_status = "unavailable"

    return {
        "status": "ok",
        "postgres": postgres_status,
        "redis": redis_status,
        "mongo": mongo_status
    }