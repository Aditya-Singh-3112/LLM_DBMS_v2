"""
Prometheus metrics. Each process (API, MCP server) exposes its own /metrics.

Routes are labelled by their template ("/databases/{database_id}/ask"),
never the raw path, so label cardinality stays bounded.
"""
import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
ASK_REQUESTS = Counter("ask_requests_total", "Questions answered by the agent", ["outcome"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens used", ["kind"])
TOOL_CALLS = Counter("tool_calls_total", "Database tool calls", ["tool", "status"])


class MetricsMiddleware:
    """Pure ASGI middleware, so streaming responses are timed to their end."""

    def __init__(self, app, known_paths: frozenset[str] = frozenset()) -> None:
        self.app = app
        # Plain Starlette apps don't record the matched route in the scope;
        # these fixed paths are safe to use as labels as-is.
        self.known_paths = known_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or (
                scope.get("path") if scope.get("path") in self.known_paths else "unmatched"
            )
            if template != "/metrics":
                method = scope.get("method", "")
                HTTP_REQUESTS.labels(method, template, str(status_code)).inc()
                HTTP_LATENCY.labels(method, template).observe(time.perf_counter() - start)


async def metrics_endpoint(request: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
