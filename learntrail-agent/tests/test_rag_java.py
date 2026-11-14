from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio
import respx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from learntrail_agent.clients.java import ArticleUnavailable, JavaArticleClient
from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings

URL = "https://java.example.test/ls/studio-articles/published/article-42"
ENABLED = "https://java.example.test/us/users/41/enabled"
pytestmark = pytest.mark.asyncio


def payload(**changes: object) -> dict[str, object]:
    return {"id": "1729000000000000000", "slug": "article-42", "title": "文章标题", "summary": "摘要",
            "body": "## 正文\n\n实际正文", "status": "published", "contentFormat": "markdown",
            "sourceType": "original", "sourceAuthor": None, "sourceUrl": None, "licenseNote": None, **changes}


@pytest_asyncio.fixture
async def java_client(settings: Settings, java_key: RSAPrivateKey) -> AsyncIterator[JavaArticleClient]:
    async with httpx.AsyncClient(follow_redirects=False) as http:
        yield JavaArticleClient(http, settings, java_key.public_key())


async def test_published_java_contract_and_forwarding(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str],
) -> None:
    respx_mock.get(ENABLED).respond(200, json={"code": 200, "data": True})
    route = respx_mock.get(URL).respond(200, json={"code": 200, "data": payload()})
    token = token_factory()
    result = await java_client.get_published_article("article-42", token)
    assert result.user_id == 41 and result.article.id == 1729000000000000000
    assert result.article.body.startswith("## 正文") and result.article.content_format == "markdown"
    assert result.article.source_author == ""
    assert route.calls[0].request.headers["authorization"] == token
    assert "user-info" not in route.calls[0].request.headers
    assert all(call.request.method == "GET" and "/read" not in call.request.url.path for call in respx_mock.calls)


@pytest.mark.parametrize("state", ["draft", "archived", "blocked"])
async def test_nonpublished_content_is_unavailable(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str], state: str,
) -> None:
    respx_mock.get(ENABLED).respond(200, json={"code": 200, "data": True})
    respx_mock.get(URL).respond(200, json={"code": 200, "data": payload(status=state)})
    with pytest.raises(ArticleUnavailable) as caught:
        await java_client.get_published_article("article-42", token_factory())
    assert caught.value.user_id == 41 and caught.value.slug == "article-42"


@pytest.mark.parametrize("http_status,business_code,expected", [
    (200, 400, "ARTICLE_NOT_AVAILABLE"), (200, 403, "ARTICLE_NOT_AVAILABLE"),
    (404, 404, "ARTICLE_NOT_AVAILABLE"), (200, 401, "INVALID_TOKEN"),
    (401, 401, "INVALID_TOKEN"), (500, 500, "JAVA_ARTICLE_UNAVAILABLE"),
])
async def test_java_http_and_business_failures_are_not_content(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str],
    http_status: int, business_code: int, expected: str,
) -> None:
    respx_mock.get(ENABLED).respond(200, json={"code": 200, "data": True})
    respx_mock.get(URL).respond(http_status, json={"code": business_code, "msg": "PRIVATE-UPSTREAM-BODY"})
    token = token_factory()
    with pytest.raises(AppError) as caught:
        await java_client.get_published_article("article-42", token)
    assert caught.value.code == expected
    assert token not in str(caught.value) and "PRIVATE-UPSTREAM-BODY" not in str(caught.value)


@pytest.mark.parametrize("data", [payload(id=True), payload(slug="wrong-article"), payload(body=None),
                                      payload(contentFormat="unknown")])
async def test_malformed_java_content_is_rejected(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str], data: dict,
) -> None:
    respx_mock.get(ENABLED).respond(200, json={"code": 200, "data": True})
    respx_mock.get(URL).respond(200, json={"code": 200, "data": data})
    with pytest.raises(AppError) as caught:
        await java_client.get_published_article("article-42", token_factory())
    assert caught.value.code == "JAVA_ARTICLE_INVALID_RESPONSE"


async def test_disabled_account_never_reads_article(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str],
) -> None:
    route = respx_mock.get(ENABLED).respond(200, json={"code": 200, "data": False})
    with pytest.raises(AppError) as caught:
        await java_client.get_published_article("article-42", token_factory())
    assert caught.value.code == "ACCOUNT_DISABLED" and len(respx_mock.calls) == 1 and route.called


async def test_expired_token_and_invalid_slug_never_contact_java(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str],
) -> None:
    with pytest.raises(AppError):
        await java_client.get_published_article("../manage", token_factory())
    with pytest.raises(AppError) as caught:
        await java_client.get_published_article("article-42", token_factory(exp=1))
    assert caught.value.code == "TOKEN_EXPIRED" and not respx_mock.calls


async def test_article_timeout_is_redacted(
    java_client: JavaArticleClient, respx_mock: respx.MockRouter, token_factory: Callable[..., str],
) -> None:
    respx_mock.get(ENABLED).respond(200, json={"code": 200, "data": True})
    respx_mock.get(URL).mock(side_effect=httpx.ReadTimeout("PRIVATE-UPSTREAM-BODY"))
    with pytest.raises(AppError) as caught:
        await java_client.get_published_article("article-42", token_factory())
    assert caught.value.code == "JAVA_ARTICLE_TIMEOUT" and "PRIVATE-UPSTREAM-BODY" not in str(caught.value)
