"""Read published articles through the existing Java gateway contract."""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Literal

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, TypeAdapter, ValidationError, field_validator

from learntrail_agent.errors import AppError
from learntrail_agent.security.auth import authenticate_token
from learntrail_agent.settings import Settings
from learntrail_agent.observability import observed


class PublishedArticle(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    id: int = Field(gt=0, le=9223372036854775807)
    slug: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=240)
    body: str = Field(min_length=1, max_length=200000, repr=False)
    status: str
    content_format: Literal["text", "markdown"] = Field(alias="contentFormat")
    source_type: Literal["original", "reprint", "link"] = Field(alias="sourceType")
    source_author: str = Field(default="", alias="sourceAuthor", max_length=100)
    source_url: str = Field(default="", alias="sourceUrl", max_length=2048)
    license_note: str = Field(default="", alias="licenseNote", max_length=500)

    @field_validator("id", mode="before")
    @classmethod
    def java_long(cls, value: object) -> int:
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
            return int(value)
        raise ValueError("Expected Java Long identity")

    @field_validator("summary", "source_author", "source_url", "license_note", mode="before")
    @classmethod
    def nullable_metadata(cls, value: object) -> object:
        return "" if value is None else value


@dataclass(frozen=True)
class AuthorizedArticle:
    user_id: int
    article: PublishedArticle = field(repr=False)


class ArticleUnavailable(AppError):
    def __init__(self, user_id: int, slug: str):
        super().__init__("ARTICLE_NOT_AVAILABLE", "文章不存在、未发布或当前不可访问。", 404)
        self.user_id = user_id
        self.slug = slug


class ArticleAccessDenied(ArticleUnavailable):
    """Java explicitly denied reading this article."""


class JavaEnvelope(BaseModel):
    code: StrictInt
    data: object = None


ArticleSort = Literal["newest", "likes", "views"]


class ArticleReference(BaseModel):
    id: int = Field(gt=0, le=9223372036854775807)
    slug: str = Field(pattern=r"^[a-z0-9-]{1,80}$")

    @field_validator("id", mode="before")
    @classmethod
    def java_long(cls, value: object) -> int:
        return PublishedArticle.java_long(value)


class ArticleSummary(ArticleReference):
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=240)
    status: Literal["published"]
    liked_times: StrictInt | None = Field(default=None, alias="likedTimes", ge=0, le=9223372036854775807)
    view_times: StrictInt | None = Field(default=None, alias="viewTimes", ge=0, le=9223372036854775807)

    @field_validator("liked_times", "view_times", mode="before")
    @classmethod
    def java_count(cls, value: object) -> int | None:
        return None if value is None else PublishedArticle.java_long(value)

    @field_validator("summary", mode="before")
    @classmethod
    def nullable_summary(cls, value: object) -> object:
        return PublishedArticle.nullable_metadata(value)


class JavaPage(BaseModel):
    total: StrictInt = Field(ge=0, le=9223372036854775807)
    pages: StrictInt = Field(ge=0, le=9223372036854775807)

    @field_validator("total", "pages", mode="before")
    @classmethod
    def java_count(cls, value: object) -> int:
        return PublishedArticle.java_long(value)


class ArticlePage(JavaPage):
    items: list[ArticleSummary] = Field(alias="list", max_length=30)


class FullTextArticle(ArticleReference):
    kind: Literal["article"]


class FullTextArticlePage(JavaPage):
    items: list[FullTextArticle] = Field(alias="list", max_length=20)


class StudioAccess(BaseModel):
    operator: StrictBool


async def read_user_data(
    http: httpx.AsyncClient, settings: Settings, key: RSAPublicKey | None,
    token: str, user_id: int, path: str, params: dict[str, object] | None = None,
) -> object:
    """Only source-reviewed, side-effect-free endpoints may use this helper."""
    if (path not in {"/ls/studio-articles/published", "/ls/studio-articles/published/links",
                     "/ls/studio-articles/access", "/ls/lessons/page", "/ss/content-search"}
            and not re.fullmatch(r"/ls/learning-records/course/[0-9]{1,19}", path)):
        raise ValueError("Java read endpoint is not allowed")
    identity = await authenticate_token(token, key, http, settings)
    if identity.user_id != user_id:
        raise AppError("INVALID_TOKEN", "登录身份不一致。", 401)
    try:
        async with asyncio.timeout(settings.java_timeout_seconds):
            response = await http.get(settings.java_gateway_base_url + path, params=params,
                                      headers={"Authorization":token}, timeout=settings.java_timeout_seconds)
            if response.status_code == 401:
                raise AppError("INVALID_TOKEN", "Java 拒绝了登录凭证。", 401)
            if response.status_code == 403:
                raise AppError("TOOL_FORBIDDEN", "Java 拒绝了该查询。", 403)
            if response.status_code != 200:
                raise AppError("JAVA_READ_UNAVAILABLE", "业务查询暂不可用。", 503, True)
            envelope = JavaEnvelope.model_validate(response.json())
            if envelope.code == 401:
                raise AppError("INVALID_TOKEN", "Java 拒绝了登录凭证。", 401)
            if envelope.code == 403:
                raise AppError("TOOL_FORBIDDEN", "Java 拒绝了该查询。", 403)
            if envelope.code != 200:
                raise AppError("JAVA_READ_UNAVAILABLE", "业务查询暂不可用。", 503, True)
            return envelope.data
    except (httpx.HTTPError, TimeoutError):
        raise AppError("JAVA_READ_UNAVAILABLE", "业务查询暂不可用。", 503, True) from None
    except (ValueError, ValidationError):
        raise AppError("JAVA_READ_INVALID_RESPONSE", "业务查询响应格式异常。", 502) from None


class JavaArticleClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings, key: RSAPublicKey | None):
        self.http, self.settings, self.key = http, settings, key

    async def can_operate(self, token: str, user_id: int) -> bool:
        data = await read_user_data(
            self.http, self.settings, self.key, token, user_id, "/ls/studio-articles/access",
        )
        try:
            return StudioAccess.model_validate(data).operator
        except ValidationError:
            raise AppError("JAVA_READ_INVALID_RESPONSE", "运营权限查询响应格式异常。", 502) from None

    @observed("java.article_search")
    async def search_published(
        self, token: str, user_id: int, keyword: str | None, page_no: int, page_size: int,
        *, sort: ArticleSort = "newest",
    ) -> ArticlePage:
        if not 1 <= page_no <= 100000 or not 1 <= page_size <= 30 or keyword is not None and len(keyword)>80:
            raise AppError("INVALID_TOOL_ARGUMENTS", "文章查询参数超出范围。", 422)
        params: dict[str, object] = {"pageNo":page_no, "pageSize":page_size}
        if keyword:
            params["q"] = keyword
        if sort != "newest":
            params["sort"] = sort
        data = await read_user_data(self.http,self.settings,self.key,token,user_id,
                                    "/ls/studio-articles/published",params)
        try:
            return ArticlePage.model_validate(data)
        except ValidationError:
            raise AppError("JAVA_READ_INVALID_RESPONSE", "文章分页响应格式异常。", 502) from None

    async def search_full_text(
        self, token: str, user_id: int, keyword: str, page_no: int, page_size: int,
    ) -> FullTextArticlePage:
        data = await read_user_data(
            self.http, self.settings, self.key, token, user_id, "/ss/content-search",
            {"q": keyword, "kind": "article", "pageNo": page_no, "pageSize": page_size},
        )
        try:
            return FullTextArticlePage.model_validate(data)
        except ValidationError:
            raise AppError("JAVA_READ_INVALID_RESPONSE", "全文搜索响应格式异常。", 502) from None

    async def published_links(self, token: str, user_id: int, ids: list[int]) -> set[tuple[int, str]]:
        data = await read_user_data(
            self.http, self.settings, self.key, token, user_id, "/ls/studio-articles/published/links",
            {"ids": ",".join(str(article_id) for article_id in ids)},
        )
        try:
            return {(item.id, item.slug) for item in TypeAdapter(list[ArticleReference]).validate_python(data)}
        except ValidationError:
            raise AppError("JAVA_READ_INVALID_RESPONSE", "公开文章引用响应格式异常。", 502) from None

    @observed("java.article_read")
    async def get_published_article(self, slug: str, token: str) -> AuthorizedArticle:
        if not re.fullmatch(r"[a-z0-9-]{1,80}", slug):
            raise AppError("INVALID_ARTICLE_SLUG", "文章标识格式不合法。", 422)
        user = await authenticate_token(token, self.key, self.http, self.settings)
        try:
            async with asyncio.timeout(self.settings.java_timeout_seconds):
                response = await self.http.get(
                    f"{self.settings.java_gateway_base_url}/ls/studio-articles/published/{slug}",
                    headers={"Authorization": token}, timeout=self.settings.java_timeout_seconds,
                )
                if response.status_code == 401:
                    raise AppError("INVALID_TOKEN", "Java 服务拒绝了登录凭证。", 401)
                if response.status_code == 403:
                    raise ArticleAccessDenied(user.user_id, slug)
                if response.status_code in {400, 404}:
                    raise ArticleUnavailable(user.user_id, slug)
                if response.status_code != 200:
                    raise AppError("JAVA_ARTICLE_UNAVAILABLE", "文章服务暂不可用。", 503, True)
                envelope = JavaEnvelope.model_validate(response.json())
                if envelope.code == 401:
                    raise AppError("INVALID_TOKEN", "Java 服务拒绝了登录凭证。", 401)
                if envelope.code == 403:
                    raise ArticleAccessDenied(user.user_id, slug)
                if envelope.code in {400, 404} or envelope.code == 200 and envelope.data is None:
                    raise ArticleUnavailable(user.user_id, slug)
                if envelope.code != 200:
                    raise AppError("JAVA_ARTICLE_UNAVAILABLE", "文章服务暂不可用。", 503, True)
                article = PublishedArticle.model_validate(envelope.data)
                if article.slug != slug:
                    raise ValueError("Mismatched article identity")
                if article.status != "published":
                    raise ArticleUnavailable(user.user_id, slug)
                if article.source_type == "reprint" and not article.license_note.strip():
                    raise ArticleUnavailable(user.user_id, slug)
                return AuthorizedArticle(user.user_id, article)
        except (httpx.TimeoutException, TimeoutError):
            raise AppError("JAVA_ARTICLE_TIMEOUT", "读取文章超时。", 504, True) from None
        except httpx.HTTPError:
            raise AppError("JAVA_ARTICLE_UNAVAILABLE", "文章服务暂不可用。", 503, True) from None
        except (ValueError, ValidationError):
            raise AppError("JAVA_ARTICLE_INVALID_RESPONSE", "文章服务响应格式异常。", 502) from None
