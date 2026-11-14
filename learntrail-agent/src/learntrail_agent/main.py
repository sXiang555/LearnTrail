"""FastAPI composition and ownership of the shared outbound HTTP client."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from learntrail_agent.api import router
from learntrail_agent.api_sessions import router as sessions_router
from learntrail_agent.api_rag import router as rag_router
from learntrail_agent.api_agent import router as agent_router
from learntrail_agent.api_capabilities import router as capabilities_router
from learntrail_agent.agent.learntrail_tools import LearnTrailTools
from learntrail_agent.clients.learning import JavaLearningClient
from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.clients.embedding import EmbeddingClient
from learntrail_agent.clients.java import JavaArticleClient
from learntrail_agent.clients.reranker import RerankerClient
from learntrail_agent.errors import AppError
from learntrail_agent.security.auth import load_public_key
from learntrail_agent.services.chat import ChatService
from learntrail_agent.services.agent import AgentService
from learntrail_agent.services.capabilities import AdvancedRuntime
from learntrail_agent.mcp_server import AuthenticatedMCP
from learntrail_agent.rag.indexer import ArticleIndexer
from learntrail_agent.rag.retriever import Retriever
from learntrail_agent.rag.qa import RagService
from learntrail_agent.settings import Settings
from learntrail_agent.storage.pool import open_pool
from learntrail_agent.storage.repositories import SessionRepository
from learntrail_agent.storage.vector_repository import VectorRepository
from learntrail_agent.storage.search_repository import SearchRepository
from learntrail_agent.storage.agent_repository import AgentRepository
from learntrail_agent.storage.checkpoints import CheckpointCipher, load_checkpoint_key
from learntrail_agent.observability import Telemetry, TraceMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, telemetry: Telemetry | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings if settings is not None else Settings()
        app.state.settings = config
        app.state.telemetry = None
        app.state.jwt_public_key = await asyncio.to_thread(load_public_key, config)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                config.llm_timeout_seconds, connect=config.llm_connect_timeout_seconds,
            ),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        ) as http:
            app.state.http = http
            app.state.llm = LLMClient(http, config)
            app.state.java_articles = JavaArticleClient(http, config, app.state.jwt_public_key)
            app.state.embedding = EmbeddingClient(http, config)
            async with open_pool(config) as pool:
                app.state.pool = pool
                app.state.sessions = SessionRepository(pool)
                app.state.chat_service = ChatService(app.state.sessions, app.state.llm, config)
                app.state.vector_repository = VectorRepository(pool)
                app.state.article_indexer = ArticleIndexer(
                    app.state.java_articles, app.state.embedding, app.state.vector_repository, config,
                )
                app.state.reranker = RerankerClient(http, config)
                app.state.retriever = Retriever(SearchRepository(pool), app.state.vector_repository,
                    app.state.java_articles, app.state.article_indexer, app.state.embedding, app.state.reranker, config)
                app.state.rag_service = RagService(app.state.retriever, app.state.llm, app.state.sessions, config)
                app.state.agent_tools = LearnTrailTools(app.state.java_articles,
                    JavaLearningClient(http,config,app.state.jwt_public_key),app.state.retriever)
                app.state.checkpointer = None
                app.state.advanced = None
                if pool is not None:
                    if not config.checkpoint_key_file.exists():
                        async with pool.connection() as connection:
                            for name in ("checkpoints","ai_approvals"):
                                table = await (await connection.execute("SELECT to_regclass(%s) AS name",(name,))).fetchone()
                                if table["name"]:
                                    # Only fixed server-owned identifiers, never request input.
                                    existing = await (await connection.execute(f"SELECT EXISTS(SELECT 1 FROM {name}) AS present")).fetchone()
                                    if existing["present"]:
                                        raise RuntimeError("Checkpoint/approval key is missing; restore the original private key before starting")
                    key = await asyncio.to_thread(load_checkpoint_key,config.checkpoint_key_file)
                    cipher=CheckpointCipher(key)
                    app.state.checkpointer = AsyncPostgresSaver(pool,serde=cipher)
                    app.state.advanced = AdvancedRuntime(app.state.sessions,cipher,config)
                app.state.agent_service = AgentService(app.state.sessions,AgentRepository(app.state.sessions),
                    app.state.agent_tools,app.state.llm,app.state.chat_service,config,app.state.checkpointer,app.state.advanced)
                app.state.telemetry = telemetry if telemetry is not None else Telemetry(config)
                try:
                    async with mcp_app.lifespan():
                        yield
                finally:
                    if telemetry is None:await asyncio.to_thread(app.state.telemetry.close)

    app = FastAPI(title=settings.app_name if settings else "LearnTrail AI", lifespan=lifespan)
    app.add_middleware(TraceMiddleware)
    app.include_router(router)
    app.include_router(sessions_router)
    app.include_router(rag_router)
    app.include_router(agent_router)
    app.include_router(capabilities_router)
    mcp_app=AuthenticatedMCP(app)
    app.mount("/mcp",mcp_app)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
        return JSONResponse(status_code=error.status_code, content=error.payload(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "error": {"code": "INVALID_REQUEST", "message": "请求字段或消息格式不合法。", "retryable": False},
        })

    @app.exception_handler(Exception)
    async def handle_internal_error(request: Request, error: Exception) -> JSONResponse:
        logger.error("Request failed: %s", type(error).__name__)
        return JSONResponse(status_code=500, content={
            "error": {"code": "INTERNAL_ERROR", "message": "服务内部处理失败。", "retryable": False},
        })

    return app


app = create_app()
