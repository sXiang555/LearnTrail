"""Three registered read-only LearnTrail tools and server-verified execution context."""

import hashlib
import json
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from learntrail_agent.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from learntrail_agent.clients.java import AuthorizedArticle, JavaArticleClient
from learntrail_agent.clients.learning import JavaLearningClient
from learntrail_agent.errors import AppError
from learntrail_agent.rag.retriever import Retriever
from learntrail_agent.rag.schemas import SearchRequest
from learntrail_agent.security.auth import authenticate_token


class KnowledgeArgs(SearchRequest):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    top_k: int = Field(default=3, ge=1, le=5)


class ArticleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    keyword: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, min_length=1, max_length=80,
                               description="当前 Java 无分类字段；非空时明确返回不支持，不代替关键词查询。")
    page_no: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=5, ge=1, le=10)


class ProgressArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    course_id: int | None = Field(default=None, gt=0, le=9223372036854775807,
                                 description="从本人课表获取的课程 ID，不提供则查询本人课表分页。")
    page_no: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=5, ge=1, le=10)
    record_limit: int = Field(default=50, ge=1, le=100)


def snapshot(data: object) -> str:
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(",",":"),
                                     allow_nan=False).encode()).hexdigest()


class LearnTrailTools:
    def __init__(self, articles: JavaArticleClient, learning: JavaLearningClient, retriever: Retriever) -> None:
        self.articles, self.learning, self.retriever = articles, learning, retriever

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(Tool("search_knowledge_base", "检索当前用户已索引且经 Java 确认可读的文章知识。返回引用原文和位置；无结果不代表已搜索所有未索引文章。",
                               KnowledgeArgs,self.knowledge,"knowledge:read",60))
        registry.register(Tool("search_articles", "按关键词和分页查询真实已发布文章，仅返回标题和摘要。Java 暂无分类筛选；category 非空将返回不支持。",
                               ArticleArgs,self.articles_page,"articles:read",30))
        registry.register(Tool("get_learning_progress", "查询当前登录用户本人课表或指定课程进度。先查本人课表再使用其 course_id，不猜测课程或用户身份；不写学习记录。",
                               ProgressArgs,self.progress,"learning:read",15))
        return registry

    async def context(self, token: str, session_id: UUID | None = None, run_id: UUID | None = None) -> ToolContext:
        a = self.articles
        identity = await authenticate_token(token,a.key,a.http,a.settings)

        async def verify_identity() -> None:
            fresh = await authenticate_token(token,a.key,a.http,a.settings)
            if fresh.user_id != identity.user_id:
                raise AppError("INVALID_TOKEN", "登录身份不一致。", 401)

        async def verify_sources(evidence: list[dict[str, object]]) -> None:
            for item in {snapshot(item):item for item in evidence}.values():
                if item["kind"] == "article":
                    source = await a.get_published_article(cast(str,item["slug"]),token)
                    current = self.article_evidence(source)
                    if source.user_id != identity.user_id or current != item:
                        raise AppError("AGENT_SOURCE_CHANGED", "文章来源已变化。", 409, True)
                elif item["kind"] == "learning_page":
                    current = await self.learning.lessons(token,identity.user_id,cast(int,item["page_no"]),cast(int,item["page_size"]))
                    if snapshot(current.model_dump(mode="json")) != item["snapshot"]:
                        raise AppError("AGENT_SOURCE_CHANGED", "学习状态已变化。", 409, True)
                elif item["kind"] == "learning_progress":
                    current = await self.learning.progress(token,identity.user_id,int(cast(str,item["course_id"])))
                    if snapshot(current.model_dump(mode="json") if current else None) != item["snapshot"]:
                        raise AppError("AGENT_SOURCE_CHANGED", "学习状态已变化。", 409, True)
                else:
                    raise AppError("AGENT_SOURCE_CHANGED", "查询依据无法验证。", 409)

        return ToolContext(identity.user_id,SecretStr(token),frozenset({"knowledge:read","articles:read","learning:read"}),
                           verify_identity,session_id,run_id,verify_sources)

    def article_evidence(self, source: AuthorizedArticle) -> dict[str, object]:
        doc, _, metadata = self.retriever.indexer.prepare_source(source)
        return {"kind":"article", "article_id":str(source.article.id), "slug":source.article.slug,
                "content_hash":doc.content_hash, "metadata":metadata}

    async def knowledge(self, arguments: BaseModel, context: ToolContext) -> ToolOutput:
        args = cast(KnowledgeArgs,arguments)
        token = context.access_token.get_secret_value()
        result = await self.retriever.search(context.user_id,args,token)
        matches, evidence = [], []
        for hit in result.results:
            source = await self.articles.get_published_article(hit.slug,token)
            binding = self.article_evidence(source)
            if (source.user_id != context.user_id or binding["content_hash"] != hit.content_hash
                    or source.article.source_type != hit.source_type or source.article.source_url != hit.source_url):
                raise AppError("AGENT_SOURCE_CHANGED", "知识来源已变化。", 409, True)
            quote = hit.content[:1000]
            matches.append({"article_id":str(hit.article_id),"slug":hit.slug,"title":hit.title,"section":hit.section,
                "ordinal":hit.ordinal,"quote":quote,"quote_truncated":len(quote)<len(hit.content),
                "start_char":hit.start_char,"end_char":hit.start_char+len(quote),
                "position_basis":"normalized_unicode_codepoints","content_hash":hit.content_hash,
                "source_type":hit.source_type,"url":"/#/articles/"+hit.slug,"original_url":hit.source_url})
            evidence.append(binding)
        return ToolOutput(data={"status":"retrieved" if matches else "insufficient_context",
            "matches":matches,"mode":result.mode,"warnings":result.warnings},evidence=evidence)

    async def articles_page(self, arguments: BaseModel, context: ToolContext) -> ToolOutput:
        args = cast(ArticleArgs,arguments)
        if args.category is not None:
            raise AppError("UNSUPPORTED_CATEGORY", "现有 Java 文章 API 不支持分类。", 422)
        token=context.access_token.get_secret_value()
        page=await self.articles.search_published(token,context.user_id,args.keyword,args.page_no,args.page_size)
        articles,evidence=[],[]
        for item in page.items:
            source=await self.articles.get_published_article(item.slug,token)
            if source.user_id!=context.user_id or source.article.id!=item.id:
                raise AppError("AGENT_SOURCE_CHANGED", "文章查询结果已变化。", 409, True)
            article=source.article
            articles.append({"article_id":str(article.id),"slug":article.slug,"title":article.title,
                "summary":article.summary,"source_type":article.source_type,"url":"/#/articles/"+article.slug})
            evidence.append(self.article_evidence(source))
        return ToolOutput(data={"total":page.total,"pages":page.pages,"page_no":args.page_no,
                                "page_size":args.page_size,"category_filter_supported":False,"articles":articles},
                          evidence=evidence)

    async def progress(self, arguments: BaseModel, context: ToolContext) -> ToolOutput:
        args=cast(ProgressArgs,arguments);token=context.access_token.get_secret_value()
        queried_at=datetime.now(timezone.utc).isoformat()
        if args.course_id is None:
            page=await self.learning.lessons(token,context.user_id,args.page_no,args.page_size)
            return ToolOutput(data={"as_of":queried_at,"total":page.total,"pages":page.pages,
                "page_no":args.page_no,"page_size":args.page_size,"lessons":[item.model_dump(mode="json") for item in page.items]},
                evidence=[{"kind":"learning_page","page_no":args.page_no,"page_size":args.page_size,
                           "snapshot":snapshot(page.model_dump(mode="json"))}])
        result=await self.learning.progress(token,context.user_id,args.course_id)
        evidence=[{"kind":"learning_progress","course_id":str(args.course_id),
                   "snapshot":snapshot(result.model_dump(mode="json") if result else None)}]
        if result is None:
            return ToolOutput(data={"as_of":queried_at,"course_id":str(args.course_id),"accessible":False,
                                   "reason":"Java 未返回本人有效课表，不能断言进度为零。"},evidence=evidence)
        return ToolOutput(data={"as_of":queried_at,"course_id":str(args.course_id),"accessible":True,
            "latest_section_id":str(result.latest_section_id) if result.latest_section_id is not None else None,
            "record_count":len(result.records),"finished_record_count":sum(r.finished is True for r in result.records),
            "unknown_finished_count":sum(r.finished is None for r in result.records),
            "records":[r.model_dump(mode="json") for r in result.records[:args.record_limit]],
            "records_truncated":len(result.records)>args.record_limit},evidence=evidence)
