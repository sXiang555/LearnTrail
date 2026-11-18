"""Source-derived Java wire contracts and read-only business tool boundaries."""

import json
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from learntrail_agent.agent.learntrail_tools import LearnTrailTools
from learntrail_agent.agent.tools import ToolContext, ToolExecutor
from learntrail_agent.clients.java import ArticlePage, JavaArticleClient, JavaPage, read_user_data
from learntrail_agent.clients.learning import JavaLearningClient, LessonPage
from learntrail_agent.clients.tool_models import FunctionCall, ToolCall
from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings


def test_java_long_pagination_accepts_actual_string_counts_but_not_bool() -> None:
    assert JavaPage.model_validate({"total":"12","pages":"2"}).total == 12
    for invalid in (True, -1, "invalid", "9223372036854775808"):
        with pytest.raises(ValidationError):
            JavaPage.model_validate({"total":invalid,"pages":"1"})
    assert LessonPage.model_validate({"total":"0","pages":"0","list":[]}).items == []
    with pytest.raises(ValidationError):
        ArticlePage.model_validate({"total":"1","pages":"1","list":[{
            "id":"42","slug":"article-42","title":"Draft","summary":"test","status":"draft"}]})


@pytest.mark.asyncio
async def test_java_clients_pass_raw_signed_token_and_only_supported_parameters(settings, java_key, token_factory, respx_mock) -> None:
    token=token_factory()
    respx_mock.get("https://java.example.test/us/users/41/enabled").respond(200,json={"code":200,"data":True})
    article=respx_mock.get("https://java.example.test/ls/studio-articles/published").respond(200,json={
        "code":200,"data":{"total":"0","pages":"0","list":[]}})
    lessons=respx_mock.get("https://java.example.test/ls/lessons/page").respond(200,json={
        "code":200,"data":{"total":"1","pages":"1","list":[{
            "courseId":"42","courseName":"课程","sections":5,"learnedSections":1,"status":1}]}})
    progress=respx_mock.get("https://java.example.test/ls/learning-records/course/42").respond(200,json={
        "code":200,"data":{"id":"88","latestSectionId":"11","records":[{
            "sectionId":"11","moment":30,"finished":False}]}})
    async with httpx.AsyncClient() as http:
        a=JavaArticleClient(http,settings,java_key.public_key())
        assert (await a.search_published(token,41,"事务",2,3)).total==0
        learning=JavaLearningClient(http,settings,java_key.public_key())
        page=await learning.lessons(token,41,1,5)
        assert page.items[0].model_dump(mode="json")["course_id"]=="42"
        result=await learning.progress(token,41,42)
        assert result.records[0].moment==30
        assert result.model_dump(mode="json")["latest_section_id"]=="11"
        with pytest.raises(AppError):
            await learning.lessons(token,99,1,5)
        with pytest.raises(ValueError):
            await read_user_data(http,settings,java_key.public_key(),token,41,"/ls/studio-points/articles/a/read")
    assert set(article.calls[0].request.url.params)=={"q","pageNo","pageSize"}
    assert article.calls[0].request.headers["Authorization"]==token
    assert "userId" not in lessons.calls[0].request.url.params and "sortBy" not in lessons.calls[0].request.url.params
    assert all(call.request.method=="GET" for call in respx_mock.calls)


@pytest.mark.asyncio
async def test_business_tools_reject_identity_and_unsupported_category_without_java_calls() -> None:
    articles=AsyncMock(spec=JavaArticleClient);learning=AsyncMock(spec=JavaLearningClient);retriever=Mock()
    domain=LearnTrailTools(articles,learning,retriever);registry=domain.registry()
    ctx=ToolContext(41,SecretStr("private-token"),frozenset({"articles:read","learning:read"}),AsyncMock())
    executor=ToolExecutor(registry)
    def call(name,args):return ToolCall(id="call",function=FunctionCall(name=name,arguments=json.dumps(args)))
    invalid=await executor.execute(call("get_learning_progress",{"userId":99}),ctx)
    assert invalid.error_code=="INVALID_TOOL_ARGUMENTS"
    category=await executor.execute(call("search_articles",{"category":"AI"}),ctx)
    assert category.error_code=="UNSUPPORTED_CATEGORY"
    articles.search_published.assert_not_called()
    learning.progress.assert_not_called()
    learning.progress.return_value=None
    missing=await executor.execute(call("get_learning_progress",{"course_id":42}),ctx)
    assert missing.ok and missing.data["accessible"] is False
    assert "record_count" not in missing.data


@pytest.mark.asyncio
async def test_loop_requires_source_guard_and_rejects_changed_evidence() -> None:
    from learntrail_agent.agent.loop import AgentLoop, AgentState
    from learntrail_agent.agent.tools import ToolOutput
    from learntrail_agent.clients.llm import LLMClient
    from test_tool_core import registry, reply, call
    async def handler(args,ctx):return ToolOutput(data={"data":"allowed initially"},evidence=[{"kind":"test"}])
    for configured in (False,True):
        tools=registry(handler);llm=AsyncMock(spec=LLMClient)
        llm.complete_with_tools.side_effect=[reply(calls=[call()]),reply(content="旧结果")]
        ctx=ToolContext(41,SecretStr("private"),frozenset({"read"}),AsyncMock(),
            verify_sources=AsyncMock(side_effect=AppError("AGENT_SOURCE_CHANGED","source changed",409)) if configured else None)
        with pytest.raises(AppError) as caught:
            await AgentLoop(llm,tools,ToolExecutor(tools)).run("query",ctx,AgentState())
        assert caught.value.code==("AGENT_SOURCE_CHANGED" if configured else "AGENT_SOURCE_GUARD_MISSING")


@pytest.mark.asyncio
async def test_knowledge_tool_returns_explicit_insufficient_context_without_invented_sources() -> None:
    articles=AsyncMock(spec=JavaArticleClient)
    retriever=Mock()
    retriever.search=AsyncMock(return_value=SimpleNamespace(results=[],mode="bm25",warnings=[]))
    domain=LearnTrailTools(articles,AsyncMock(spec=JavaLearningClient),retriever)
    ctx=ToolContext(41,SecretStr("private-token"),frozenset({"knowledge:read"}),AsyncMock())
    result=await ToolExecutor(domain.registry()).execute(ToolCall(id="empty",function=FunctionCall(
        name="search_knowledge_base",arguments='{"query":"未收录的问题","mode":"bm25"}')),ctx)
    assert result.ok and result.data["status"]=="insufficient_context"
    assert result.data["matches"]==[] and result.evidence==[]
    articles.get_published_article.assert_not_called()
    assert retriever.search.await_args.args[0]==41


@pytest.mark.asyncio
@pytest.mark.parametrize(("detail_status", "detail_code", "published", "expected", "restricted"), [
    (403, 403, True, "restricted", 1),
    (200, 403, False, "no_readable_results", 0),
    (404, 404, False, "no_readable_results", 0),
])
async def test_full_text_only_reports_access_denied_for_still_public_articles(
    settings, java_key, token_factory, respx_mock, detail_status, detail_code, published, expected, restricted,
) -> None:
    respx_mock.get("https://java.example.test/us/users/41/enabled").respond(200, json={"code": 200, "data": True})
    base = "https://java.example.test"
    respx_mock.get(base + "/ls/studio-articles/published").respond(200, json={
        "code": 200, "data": {"total": "0", "pages": "0", "list": []},
    })
    reference = {"id": "42", "slug": "possible-match", "title": "不应暴露的标题", "summary": "不应暴露的摘要"}
    search = respx_mock.get(base + "/ss/content-search").respond(200, json={
        "code": 200, "data": {"total": "1", "pages": "1", "list": [{**reference, "kind": "article"}]},
    })
    respx_mock.get(base + "/ls/studio-articles/published/possible-match").respond(
        detail_status, json={"code": detail_code, "data": None},
    )
    links = respx_mock.get(base + "/ls/studio-articles/published/links").respond(
        200, json={"code": 200, "data": [reference] if published else []},
    )

    async with httpx.AsyncClient() as http:
        domain = LearnTrailTools(JavaArticleClient(http, settings, java_key.public_key()), Mock(), Mock())
        context = await domain.context(token_factory())
        call = ToolCall(id="search", function=FunctionCall(name="search_articles", arguments='{"keyword":"Redis"}'))
        result = await ToolExecutor(domain.registry()).execute(call, context)

    assert result.ok and result.data["status"] == expected
    assert result.data["restricted_count"] == restricted
    assert result.data["search_scope"] == "public_full_text"
    assert result.data["articles"] == [] and result.evidence == []
    assert "不应暴露" not in result.model_content() and "possible-match" not in result.model_content()
    assert dict(search.calls.last.request.url.params) == {
        "q": "Redis", "kind": "article", "pageNo": "1", "pageSize": "5",
    }
    assert links.called == (detail_code == 403)
