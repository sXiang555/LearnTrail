"""Optional Cohere/DashScope rerank adapter; independent credentials/model.

No SDK, local heuristic, or silent fallback. Other provider protocols can replace
this client through its rerank(query, documents) boundary.
"""

import asyncio

import httpx
from pydantic import BaseModel, Field, StrictInt, ValidationError

from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings
from learntrail_agent.observability import observed


class RerankScore(BaseModel):
    index: StrictInt = Field(ge=0)
    relevance_score: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


class RerankResult(BaseModel):
    results: list[RerankScore] = Field(min_length=1, max_length=100)


class RerankerClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http, self.settings = http, settings

    def check_configuration(self) -> None:
        if not self.settings.reranker_configured:
            raise AppError("RERANKER_NOT_CONFIGURED", "尚未配置独立的 Reranker 服务。", 503)

    @observed("rag.rerank")
    async def rerank(self, query: str, documents: list[str]) -> list[RerankScore]:
        self.check_configuration()
        if not documents:
            return []
        if len(documents) > 100:
            raise AppError("RERANKER_INPUT_LIMIT", "精排候选超出限制。", 422)
        s = self.settings
        payload = ({"model": s.reranker_model, "input": {"query": query, "documents": documents},
                    "parameters": {"top_n": len(documents)}} if s.reranker_protocol == "dashscope" else
                   {"model": s.reranker_model, "query": query, "documents": documents, "top_n": len(documents)})
        try:
            async with asyncio.timeout(s.reranker_timeout_seconds):
                response = await self.http.post(
                    s.reranker_base_url + s.reranker_path,
                    headers={"Authorization": "Bearer " + s.reranker_api_key.get_secret_value()},
                    json=payload,
                    timeout=httpx.Timeout(s.reranker_timeout_seconds, connect=s.llm_connect_timeout_seconds),
                )
                if response.status_code in {401, 403}:
                    raise AppError("RERANKER_AUTH_ERROR", "精排服务认证失败。", 502)
                if response.status_code == 429:
                    raise AppError("RERANKER_RATE_LIMITED", "精排服务限流。", 429, True)
                if response.status_code >= 500:
                    raise AppError("RERANKER_UNAVAILABLE", "精排服务暂不可用。", 503, True)
                if not 200 <= response.status_code < 300:
                    raise AppError("RERANKER_REJECTED", "精排服务拒绝请求。", 502)
                body = response.json()
                if s.reranker_protocol == "dashscope":
                    body = body.get("output") if isinstance(body, dict) else None
                parsed = RerankResult.model_validate(body)
                if sorted(r.index for r in parsed.results) != list(range(len(documents))):
                    raise ValueError("Invalid result indices")
                return sorted(parsed.results, key=lambda r: (-r.relevance_score, r.index))
        except (httpx.TimeoutException, TimeoutError):
            raise AppError("RERANKER_TIMEOUT", "精排服务超时。", 504, True) from None
        except httpx.HTTPError:
            raise AppError("RERANKER_CONNECTION_ERROR", "无法连接精排服务。", 502, True) from None
        except (ValueError, ValidationError):
            raise AppError("RERANKER_INVALID_RESPONSE", "精排服务响应无效。", 502) from None
