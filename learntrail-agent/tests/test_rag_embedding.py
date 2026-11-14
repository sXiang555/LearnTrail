import json

import httpx
import pytest
import respx

from learntrail_agent.clients.embedding import EmbeddingClient
from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings

URL = "https://embedding.example.test/v1/embeddings"
pytestmark = pytest.mark.asyncio


def config(**changes: object) -> Settings:
    return Settings(_env_file=None, **{"embedding_base_url": "https://embedding.example.test/v1",
                    "embedding_api_key": "test-only-embedding-key", "embedding_model": "test-embedding",
                    "embedding_dimensions": 3, "embedding_batch_size": 2, **changes})


def reply(vectors: list[list[float]], usage: bool = True) -> dict:
    result = {"model": "resolved-embedding", "data": [{"index": index, "embedding": values} for index, values in enumerate(vectors)]}
    if usage:
        result["usage"] = {"prompt_tokens": len(vectors), "total_tokens": len(vectors)}
    return result


async def test_batch_order_splitting_usage_and_no_optional_parameters(respx_mock: respx.MockRouter) -> None:
    first = reply([[1, 2, 3], [4, 5, 6]])
    first["data"].reverse()
    route = respx_mock.post(URL).mock(side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=reply([[7, 8, 9]]))])
    async with httpx.AsyncClient() as http:
        result = await EmbeddingClient(http, config()).embed(["第一块", "第二块", "第三块"])
    assert result.vectors == [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert result.usage.total_tokens == 3 and len(route.calls) == 2
    payload = json.loads(route.calls[0].request.content)
    assert payload == {"model": "test-embedding", "input": ["第一块", "第二块"]}
    assert route.calls[0].request.headers["authorization"] == "Bearer test-only-embedding-key"


async def test_single_input_and_explicit_optional_dimensions(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL).respond(200, json=reply([[1, 2, 3]], usage=False))
    async with httpx.AsyncClient() as http:
        result = await EmbeddingClient(http, config(embedding_send_dimensions=True)).embed("单条文本")
    assert result.usage is None and len(result.vectors) == 1
    assert json.loads(route.calls[0].request.content)["dimensions"] == 3


@pytest.mark.parametrize("vectors", [[[1, 2]], [[1, 2, 3, 4]], [[0, 0, 0]],
                                     [[True, 1, 2]], [[1e100, 1, 2]], [[float('nan'), 1, 2]]])
async def test_invalid_dimensions_and_values_are_rejected(respx_mock: respx.MockRouter, vectors: list) -> None:
    respx_mock.post(URL).respond(200, content=json.dumps(reply(vectors)))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await EmbeddingClient(http, config()).embed("文本")
    assert caught.value.code == "EMBEDDING_INVALID_RESPONSE"


@pytest.mark.parametrize("status,code", [(401, "EMBEDDING_AUTH_ERROR"), (403, "EMBEDDING_AUTH_ERROR"),
                                        (429, "EMBEDDING_RATE_LIMITED"), (500, "EMBEDDING_UNAVAILABLE"),
                                        (400, "EMBEDDING_REQUEST_REJECTED")])
async def test_http_errors_are_redacted(respx_mock: respx.MockRouter, status: int, code: str) -> None:
    respx_mock.post(URL).respond(status, content="PRIVATE-UPSTREAM-BODY")
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await EmbeddingClient(http, config()).embed("PRIVATE-INPUT-TEXT")
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value) and "test-only-embedding-key" not in str(caught.value)


async def test_timeout_and_bad_json(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL).mock(side_effect=httpx.ReadTimeout("PRIVATE-UPSTREAM-BODY"))
    async with httpx.AsyncClient() as http:
        client = EmbeddingClient(http, config())
        with pytest.raises(AppError) as caught:
            await client.embed("文本")
        assert caught.value.code == "EMBEDDING_TIMEOUT"
        route.respond(200, content="not-json")
        with pytest.raises(AppError) as caught:
            await client.embed("文本")
        assert caught.value.code == "EMBEDDING_INVALID_RESPONSE"


async def test_duplicate_response_indices_are_rejected(respx_mock: respx.MockRouter) -> None:
    body = reply([[1, 2, 3], [4, 5, 6]])
    body["data"][1]["index"] = 0
    respx_mock.post(URL).respond(200, json=body)
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError):
            await EmbeddingClient(http, config()).embed(["第一块", "第二块"])


async def test_missing_usage_from_one_batch_is_not_reported_as_complete(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(side_effect=[httpx.Response(200, json=reply([[1, 2, 3], [4, 5, 6]])),
                                         httpx.Response(200, json=reply([[7, 8, 9]], usage=False))])
    async with httpx.AsyncClient() as http:
        assert (await EmbeddingClient(http, config()).embed(["一", "二", "三"])).usage is None


async def test_chat_configuration_does_not_configure_embeddings(respx_mock: respx.MockRouter) -> None:
    settings = Settings(_env_file=None, llm_api_key="test-chat-key", llm_model="chat", llm_base_url="https://chat.example.test/v1")
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await EmbeddingClient(http, settings).embed("文本")
    assert caught.value.code == "EMBEDDING_NOT_CONFIGURED" and not respx_mock.calls
