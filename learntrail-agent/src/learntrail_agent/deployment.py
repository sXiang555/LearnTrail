"""Deployment entry point: expose authenticated APIs, never development chat."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from learntrail_agent.main import app as application


class DeploymentApi:
    """Keep the development entry point local without duplicating authentication."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            path = scope["path"]
            allowed = path == "/v1/health" or any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in ("/v1/sessions", "/v1/skills", "/v1/memories", "/mcp")
            )
            if not allowed:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    response = JSONResponse(
                        {"error": {"code": "NOT_FOUND", "message": "接口不存在。", "retryable": False}},
                        status_code=404,
                    )
                    await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = DeploymentApi(application)
