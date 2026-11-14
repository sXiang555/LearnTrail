"""RAG API authentication and independently mocked reranker protocol."""

import json
from collections.abc import Callable
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from learntrail_agent.clients.reranker import RerankerClient
from learntrail_agent.errors import AppError
from learntrail_agent.rag.qa import RagService
from learntrail_agent.rag.retriever import Retriever
from learntrail_agent.rag.schemas import RagResponse
from learntrail_agent.settings import Settings
from test_retrieval import hit, response


@pytest.mark.asyncio
async def test_rag_requires_bearer_auth(api_client: httpx.AsyncClient) -> None:
    r = await api_client.post("/v1/rag/search", json={"query":"钱包"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_rag_api_uses_signed_identity_and_rejects_browser_owner(
    api_client: httpx.AsyncClient, app: FastAPI, java_key: RSAPrivateKey,
    token_factory: Callable[..., str], respx_mock: respx.MockRouter,
) -> None:
    app.state.jwt_public_key = java_key.public_key()
    respx_mock.get("https://java.example.test/us/users/41/enabled").respond(200, json={"code":200,"data":True})
    headers = {"Authorization":"Bearer " + token_factory(), "userId":"999", "role":"admin"}
    retriever = AsyncMock(spec=Retriever)
    retriever.search.return_value = response(hit())
    app.state.retriever = retriever
    r = await api_client.post("/v1/rag/search", headers=headers, json={"query":"钱包", "mode":"bm25"})
    assert r.status_code == 200 and retriever.search.await_args.args[0] == 41
    assert isinstance(r.json()["results"][0]["article_id"], str)
    for invalid in ({"query":"钱包","userId":999}, {"query":"钱包","top_k":0},
                    {"query":"钱包","article_slugs":["../draft"]}, {"query":"  "}):
        r = await api_client.post("/v1/rag/search", headers=headers, json=invalid)
        assert r.status_code == 422
    service = AsyncMock(spec=RagService)
    sid = uuid4()
    service.answer.return_value = RagResponse(request_id=uuid4(), session_id=sid, status="insufficient_context",
        answer="内容不足", citations=[], retrieval=response(), duration_ms=1)
    app.state.rag_service = service
    r = await api_client.post(f"/v1/sessions/{sid}/rag", headers=headers, json={"query":"钱包"})
    assert r.status_code == 200 and service.answer.await_args.args[:2] == (41, sid)


@pytest.mark.asyncio
async def test_reranker_missing_configuration_is_explicit(settings: Settings) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await RerankerClient(http, settings).rerank("问题", ["内容"])
    assert caught.value.code == "RERANKER_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_reranker_sends_independent_model_key_and_parses_order(settings: Settings, respx_mock: respx.MockRouter) -> None:
    from pydantic import SecretStr
    settings.reranker_base_url = "https://rerank.example.test/v2"
    settings.reranker_api_key = SecretStr("test-rerank-key")
    settings.reranker_model = "test-rerank-model"
    route = respx_mock.post("https://rerank.example.test/v2/rerank").respond(200, json={"results":[
        {"index":0,"relevance_score":.1},{"index":1,"relevance_score":.9}]})
    async with httpx.AsyncClient() as http:
        result = await RerankerClient(http, settings).rerank("问题", ["无关", "相关"])
    assert [r.index for r in result] == [1, 0]
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-rerank-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,code", [
    (401, {"secret":"do-not-expose"}, "RERANKER_AUTH_ERROR"),
    (429, {}, "RERANKER_RATE_LIMITED"), (503, {}, "RERANKER_UNAVAILABLE"),
    (200, {"results":[{"index":0,"relevance_score":.8},{"index":0,"relevance_score":.7}]}, "RERANKER_INVALID_RESPONSE"),
    (200, {"results":[{"index":0,"relevance_score":2}]}, "RERANKER_INVALID_RESPONSE"),
])
async def test_reranker_errors_are_sanitized(
    settings: Settings, respx_mock: respx.MockRouter, status: int, body: dict, code: str,
) -> None:
    from pydantic import SecretStr
    settings.reranker_base_url = "https://rerank.example.test/v2"
    settings.reranker_api_key = SecretStr("test-rerank-key")
    settings.reranker_model = "test-rerank-model"
    respx_mock.post("https://rerank.example.test/v2/rerank").respond(status, json=body)
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await RerankerClient(http, settings).rerank("问题", ["内容", "其他"])
    assert caught.value.code == code and "do-not-expose" not in str(caught.value)


@pytest.mark.asyncio
async def test_dashscope_native_protocol_and_timeout(settings: Settings, respx_mock: respx.MockRouter) -> None:
    from pydantic import SecretStr
    settings.reranker_api_key = SecretStr("test-native-key")
    settings.reranker_base_url = "https://workspace.example.test/api/v1"
    settings.reranker_path = "/services/rerank/text-rerank/text-rerank"
    settings.reranker_protocol = "dashscope"
    settings.reranker_model = "test-native-model"
    route = respx_mock.post(settings.reranker_base_url + settings.reranker_path).respond(200, json={
        "output":{"results":[{"index":0,"relevance_score":.7}]},"usage":{"total_tokens":9}})
    async with httpx.AsyncClient() as http:
        result = await RerankerClient(http, settings).rerank("问题", ["内容"])
        assert result[0].relevance_score == .7
        payload = json.loads(route.calls[0].request.content)
        assert payload["input"] == {"query":"问题", "documents":["内容"]}
        assert payload["parameters"]["top_n"] == 1
        route.mock(side_effect=httpx.ReadTimeout("PRIVATE-DETAILS"))
        with pytest.raises(AppError) as caught:
            await RerankerClient(http, settings).rerank("问题", ["内容"])
        assert caught.value.code == "RERANKER_TIMEOUT"
