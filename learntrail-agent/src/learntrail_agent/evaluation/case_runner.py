"""Per-case adapters over production modules; no fixed pytest aliases or paid calls."""

import asyncio
import hashlib
import json
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import httpx
from pydantic import SecretStr, ValidationError

from learntrail_agent.agent.advanced_models import ApprovalDecision, MemoryCreate, MemoryUpdate, MemoryProposal, digest
from learntrail_agent.agent.learntrail_tools import LearnTrailTools
from learntrail_agent.agent.schemas import AgentChatRequest
from learntrail_agent.agent.skills import SkillCatalog
from learntrail_agent.agent.tools import Tool, ToolContext, ToolOutput, ToolExecutor
from learntrail_agent.clients.java import JavaArticleClient, ArticleUnavailable, AuthorizedArticle, PublishedArticle, ArticlePage
from learntrail_agent.clients.learning import JavaLearningClient, LessonPage, CourseProgress
from learntrail_agent.clients.mcp import MCPClient, MCPProfile, arguments_model
from learntrail_agent.clients.tool_models import ToolCall, FunctionCall, ToolChatResponse
from learntrail_agent.errors import AppError
from learntrail_agent.evaluation.datasets import case_fingerprint
from learntrail_agent.rag.indexer import ArticleIndexer
from learntrail_agent.rag.schemas import SearchRequest
from learntrail_agent.services.agent import AgentService
from learntrail_agent.services.chat import ChatService
from learntrail_agent.storage.agent_repository import AgentRepository


class ScriptedModel:
    """Explicit deterministic model fixture; its outputs never count as model quality."""
    def __init__(self, batches, final, raw_response=None):
        self.batches=list(batches);self.final=final;self.requests=[];self.before_response=None
        self.raw_response=raw_response

    async def complete_with_tools(self, request):
        self.requests.append(request)
        if self.before_response:self.before_response(len(self.requests))
        if self.raw_response is not None:
            from learntrail_agent.clients.llm import LLMClient
            from learntrail_agent.settings import Settings
            config=Settings(_env_file=None,llm_api_key='fixture-key',llm_base_url='https://case.invalid/v1',llm_model='case-controlled')
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _:httpx.Response(200,json=self.raw_response))) as http:
                return await LLMClient(http,config).complete_with_tools(request)
        batch=self.batches.pop(0) if self.batches else []
        return ToolChatResponse(id='fixture-'+uuid4().hex,model='case-controlled',usage=None,
            content=None if batch else self.final,finish_reason='tool_calls' if batch else 'stop',
            tool_calls=[ToolCall(id=c.get('id','call-'+uuid4().hex),function=FunctionCall(
                name=c['name'],arguments=json.dumps(c.get('arguments',{}),ensure_ascii=False))) for c in batch])


def call(name, arguments, identifier=None):
    return ToolCall(id=identifier or 'case-'+uuid4().hex,function=FunctionCall(name=name,arguments=json.dumps(arguments,ensure_ascii=False)))


class CaseRunner:
    def __init__(self, settings, corpus, state=None, workdir=None):
        self.settings=settings;self.corpus=corpus;self.state=state
        self.workdir=workdir or settings.observability_dir

    async def run(self, case):
        start=time.monotonic()
        if case.status=='blocked':
            return {'status':'unverified','reason':case.blocking_reason,'scope':'blocked','observations':{}}
        plan=case.execution
        if plan is None:raise ValueError('Case execution plan missing')
        if case.evaluation and case.evaluation.kind in {'rag_qa','rag_retrieval'}:
            return await self.run_rag_qa(case)
        if (plan.kind in {'graph','memory','hitl'} or plan.operation=='other_session' or plan.parameters.get('exercise_tool')) and (self.state is None or self.state.pool is None):
            return {'status':'unverified','reason':'DATABASE_REQUIRED','scope':plan.kind,'observations':{}}
        observation={};scope={'rag_evidence':'rag_evidence_integrity' if plan.operation=='evidence' else 'rag_source_access_guard',
            'graph':'controlled_model_native_tools_graph_sql','memory':'memory_sql_and_approval_contract',
            'skills':'skill_selection_authorization_and_controlled_graph','mcp':'actual_local_mcp_protocol',
            'hitl':'hitl_controlled_graph_checkpoint_sql'}[plan.kind]
        try:
            handler=getattr(self,'run_'+plan.kind)
            observation=await handler(case)
            checks={key:key in observation and observation[key]==expected for key,expected in plan.assertions.items()}
            status='executed' if all(checks.values()) else 'error'
            metrics={}
            if plan.kind=='rag_evidence' and plan.operation=='evidence':
                metrics={'retrieval':observation['retrieval']}
            elif plan.kind=='skills':
                metrics={'expected_skill':case.expected.expected_skill,'selected_skill':observation.get('selected_skill')}
                if plan.operation=='authorization':metrics['unauthorized_tool_rate']=float(observation.get('executions',0)>0)
            elif plan.kind=='memory':
                names={'update':'update_accuracy','delete':'deletion_accuracy','isolation':'cross_user_isolation'}
                if plan.operation in names:metrics[names[plan.operation]]=float(all(checks.values()))
                elif plan.operation=='retrieve' and plan.assertions.get('target_present') is True:
                    metrics['retrieval_recall_at_k']=float(observation.get('target_present',False))
            elif plan.kind=='mcp':
                names={'discovery':'discovery_success_rate','schema':'schema_conformance','recovery':'transport_recovery_rate'}
                if plan.operation in names:metrics[names[plan.operation]]=float(all(checks.values()))
                if 'ok' in observation:metrics['invocation_success_rate']=float(observation['ok'])
            elif plan.kind=='hitl':
                if plan.operation in {'approve','reject','expire','cancel_owner','tamper'}:metrics['approval_decision_accuracy']=float(all(checks.values()))
                if plan.operation=='resume':metrics['resume_success_rate']=float(observation.get('resumed',False))
                if plan.operation in {'reject','expire','cancel_owner'}:metrics['rejection_enforcement']=float(observation.get('effect_count')==0)
                if plan.operation=='duplicate':metrics['duplicate_execution_count']=max(0,observation.get('effect_count',1)-1)
            return {'status':status,'scope':scope,'contract_success':all(checks.values()),
                'checks':checks,'actual':observation,'expected':plan.assertions,
                'input_sha256':case_fingerprint(case),'question_used':case.question,
                'observations':metrics,'error':None if status=='executed' else 'CASE_ASSERTION_FAILED',
                'duration_ms':round((time.monotonic()-start)*1000,2)}
        except (AppError,ValidationError,ValueError,TimeoutError) as error:
            return {'status':'error','scope':scope,'error':error.code if isinstance(error,AppError) else type(error).__name__,
                'input_sha256':case_fingerprint(case),'question_used':case.question,'actual':observation,'observations':{},
                'duration_ms':round((time.monotonic()-start)*1000,2)}

    async def fixture_service(self, case, model, *, settings=None):
        """Real AgentService, native tool handlers and SQL; fixed Java response fixtures."""
        cfg=settings or self.settings;p=case.execution.parameters
        uid=p['user_id'];flags={'revoked':False}
        records={s['slug']:s for s in self.corpus['sources']}
        articles=AsyncMock(spec=JavaArticleClient);learning=AsyncMock(spec=JavaLearningClient)
        articles.settings=cfg
        prepared={}
        indexer=ArticleIndexer(articles,None,None,cfg)
        for slug,s in records.items():
            a=PublishedArticle(id=s['id'],slug=slug,title=s['title'],body=s['text'],status='published',
                contentFormat='markdown',sourceType=s['source_type'])
            prepared[slug]=AuthorizedArticle(uid,a)
        async def get(slug,token):
            if flags['revoked'] or slug not in prepared:raise ArticleUnavailable(uid,slug)
            return prepared[slug]
        async def search(token,user_id,keyword,page_no,page_size):
            found=[s.article for s in prepared.values() if not keyword or keyword.casefold() in (s.article.title+s.article.summary).casefold()]
            selected=found[(page_no-1)*page_size:page_no*page_size]
            return ArticlePage.model_validate({'total':len(found),'pages':(len(found)+page_size-1)//page_size,
                'list':[a.model_dump(by_alias=True) for a in selected]})
        articles.get_published_article.side_effect=get;articles.search_published.side_effect=search
        learning.lessons.return_value=LessonPage.model_validate(p.get('lesson_page',{'total':0,'pages':0,'list':[]}))
        learning.progress.return_value=CourseProgress.model_validate(p['course_progress']) if p.get('course_progress') else None
        retriever=Mock();retriever.indexer=indexer
        async def retrieve(user_id,request,token):
            from learntrail_agent.rag.schemas import SearchHit
            slug=p.get('knowledge_slug')
            hits=[]
            if slug:
                source=prepared[slug];doc,_,_=indexer.prepare_source(source)
                hits=[SearchHit(article_id=source.article.id,slug=slug,generation=uuid4(),ordinal=0,title=source.article.title,
                    section='fixture source',start_char=0,end_char=len(doc.text),content=doc.text,content_hash=doc.content_hash,
                    source_type=source.article.source_type,source_url=source.article.source_url,score=1)]
            return SimpleNamespace(results=hits,mode=request.mode,warnings=[])
        retriever.search=AsyncMock(side_effect=retrieve)
        domain=LearnTrailTools(articles,learning,retriever)
        async def identity():return None
        async def verify(evidence):
            if flags['revoked']:raise AppError('AGENT_SOURCE_CHANGED','Fixture resource revoked',409)
        async def context(token,session_id=None,run_id=None):
            return ToolContext(uid,SecretStr('case-fixture-token'),frozenset({'knowledge:read','articles:read','learning:read'}),
                identity,session_id,run_id,verify)
        domain.context=context
        def service():
            return AgentService(self.state.sessions,AgentRepository(self.state.sessions),domain,model,
                ChatService(self.state.sessions,model,cfg),cfg,self.state.checkpointer,self.state.advanced)
        return service,SimpleNamespace(articles=articles,learning=learning,retriever=retriever,flags=flags)

    async def seed_history(self, case, agent, model, sid):
        # History is executed as real earlier turns, not silently copied into a field.
        history=[]
        for text in case.history:
            old=model.batches;model.batches=[]
            result=await agent.execute(await agent.prepare(case.execution.parameters['user_id'],sid,AgentChatRequest(content=text),'case-token'))
            history.append(str(result.run_id));model.batches=old
        return history

    async def run_graph(self, case):
        p=case.execution.parameters;uid=p['user_id'];op=case.execution.operation
        model=ScriptedModel(p.get('batches',[]),p['final_response'],p.get('raw_response'))
        cfg=self.settings.model_copy(update={'agent_max_rounds':p.get('max_rounds',6),'agent_timeout_seconds':p.get('deadline_seconds',10)})
        factory,fixture=await self.fixture_service(case,model,settings=cfg);agent=factory()
        if p.get('tool_failure'):
            tool=agent.loop.registry.get(p['tool_failure']['name'])
            async def fail(args,ctx):
                if p['tool_failure']['kind']=='timeout':await asyncio.sleep(2)
                raise AppError('TOOL_EXECUTION_FAILED','Fixture failure',503)
            from learntrail_agent.agent.tools import ToolRegistry
            updated=ToolRegistry()
            for item in agent.loop.registry.tools():updated.register(replace(item,execute=fail,timeout_seconds=.03) if item.name==tool.name else item)
            agent.loop.registry=updated;agent.loop.executor=ToolExecutor(updated)
        sid=(await self.state.sessions.create(uid,case.question[:80])).id
        await self.seed_history(case,agent,model,sid)
        before=len(model.requests);code=None;result=None
        if op=='source_revoke':
            def revoke(n):
                if n>before+1:fixture.flags['revoked']=True
            model.before_response=revoke
        prepared=await agent.prepare(uid,sid,AgentChatRequest(content=case.question,skill=p.get('requested_skill')), 'case-token')
        if op in {'cancel','cancel_tool','disconnect'}:
            started=asyncio.Event();original=model.complete_with_tools
            async def waiting(request):
                model.requests.append(request)
                started.set();await asyncio.sleep(10);return await original(request)
            if op=='cancel_tool':
                from learntrail_agent.agent.tools import ToolRegistry
                async def waiting_tool(args,ctx):started.set();await asyncio.sleep(10);return ToolOutput()
                updated=ToolRegistry()
                for item in agent.loop.registry.tools():updated.register(replace(item,execute=waiting_tool) if item.name=='get_learning_progress' else item)
                agent.loop.registry=updated;agent.loop.executor=ToolExecutor(updated)
                # prepare() already bound a per-run registry; update that exact bound adapter too.
                if prepared.capabilities:
                    bound=prepared.capabilities.registry.get('get_learning_progress')
                    registry=ToolRegistry()
                    for item in prepared.capabilities.registry.tools():registry.register(replace(item,execute=waiting_tool) if item.name==bound.name else item)
                    prepared.capabilities.registry=registry;prepared.capabilities.executor=ToolExecutor(registry)
            else:model.complete_with_tools=waiting
            task=asyncio.create_task(agent.execute(prepared))
            await asyncio.wait_for(started.wait(),2)
            if op in {'cancel','cancel_tool'}:await self.state.sessions.cancel_run(uid,sid,prepared.run.id)
            else:task.cancel()
            try:result=await asyncio.wait_for(task,3)
            except (AppError,asyncio.CancelledError) as e:code=e.code if isinstance(e,AppError) else 'CANCELLED'
        else:
            try:result=await agent.execute(prepared)
            except AppError as e:code=e.code
        record=await self.state.sessions.get_run(uid,sid,prepared.run.id)
        trajectory=await agent.traces.trace(uid,sid,prepared.run.id)
        events=trajectory[1] if isinstance(trajectory,tuple) else trajectory
        tool_events=[e['payload'] for e in events if e['kind']=='tool_result']
        def contains_question(message):
            if message.role!='user':return False
            if message.content==case.question:return True
            try:return json.loads(message.content).get('question')==case.question
            except (ValueError,TypeError,AttributeError):return False
        calls=[m for r in model.requests[before:] for m in getattr(r,'messages',[]) if contains_question(m)]
        forwarded={}
        for request in model.requests[before:]:
            for message in request.messages:
                if message.role=='tool':
                    forwarded[message.tool_call_id]=json.loads(message.content)
        matched=[e for e in tool_events if e.get('call_id') in forwarded and
            forwarded[e['call_id']].get('ok')==e.get('ok') and
            forwarded[e['call_id']].get('data')==e.get('data') and
            (forwarded[e['call_id']].get('error') or {}).get('code')==e.get('error_code')]
        detail=await self.state.sessions.detail(uid,sid,100,0)
        return {'question_observed':bool(calls),
            'status':record.status,'error':code,'tool_calls':result.tool_calls if result else None,
            'model_requests':len(model.requests)-before,'history_turns':len(prepared.history),
            'assistant_committed':any(m.role=='assistant' and m.run_id==prepared.run.id for m in detail.messages),
            'requested_arguments':[json.loads(c['function']['arguments']) for e in events if e['kind']=='model_end'
                for c in e['payload'].get('tool_calls',[])],
            'tool_errors':[e['error_code'] for e in tool_events if e.get('error_code')],
            'tool_names':[e['name'] for e in tool_events],
            'tool_results_forwarded':len(matched),
            'learning_course_ids':sorted({str(row['course_id']) for e in tool_events
                for row in (e.get('data') or {}).get('lessons',[])}),
            'knowledge_source_slugs':sorted({row['slug'] for e in tool_events
                for row in (e.get('data') or {}).get('matches',[])}),
            'trace_available':bool(events)}

    async def run_rag_qa(self,case):
        """Free article-level baseline with explicit, independent grading stages."""
        from learntrail_agent.evaluation.framework import offline_bm25
        from learntrail_agent.evaluation.metrics import retrieval_verdict
        started=time.monotonic();spec=case.evaluation
        sources={s['slug']:s for s in self.corpus['sources']}
        scope_hash=hashlib.sha256(json.dumps([(s['id'],s['content_hash']) for s in self.corpus['sources']],sort_keys=True).encode()).hexdigest()
        source_valid=bool(spec.reference_scope) and set(spec.reference_scope)<=sources.keys() and all(
            hashlib.sha256((s['title']+'\n\n'+s['text']).encode()).hexdigest()==s['content_hash'] for s in sources.values())
        source_valid=source_valid and scope_hash==case.execution.parameters['scope_sha256'] and all(
            e.slug in sources and sources[e.slug]['id']==e.article_id and sources[e.slug]['content_hash']==e.content_hash and
            sources[e.slug]['text'][e.start_char:e.end_char]==e.quote for e in case.evidence)
        if case.expected.answer_available:
            grounded=bool(case.expected.required_facts) and all(any(f in e.quote and f in e.supports and
                e.slug in spec.reference_scope for e in case.evidence) for f in case.expected.required_facts)
        else:
            grounded=not case.expected.required_facts and spec.gold_kind=='scope_absence' and all(
                slug in sources for slug in spec.reference_scope)
        actual={'source_valid':source_valid,'gold_facts_grounded':grounded}
        checks={key:actual.get(key)==value for key,value in case.execution.assertions.items()}
        retrieval=offline_bm25(case,self.corpus,self.settings)['retrieval'];verdict=retrieval_verdict(retrieval)
        return {'status':'executed' if all(checks.values()) else 'error','scope':'offline_article_bm25',
            'evaluation_status':'failed' if not all(checks.values()) or verdict['status']=='failed' else 'partial',
            'checks':checks,'actual':actual,'expected':case.execution.assertions,'contract_success':None,
            'reference_answer':case.expected.reference_answer,'required_facts':case.expected.required_facts,
            'answer':None,'model_calls':0,'model_quality_evaluated':False,
            'evaluations':{'source_validation':{'status':'passed' if all(checks.values()) else 'failed'},
                'retrieval':verdict,'generation':{'status':'not_run'},'semantic':{'status':'not_run'}},
            'observations':{'retrieval':retrieval},'question_used':case.question,
            'input_sha256':case_fingerprint(case),'error':None if all(checks.values()) else 'INVALID_GOLD_SOURCE',
            'duration_ms':round((time.monotonic()-started)*1000,2)}

    async def run_rag_evidence(self,case):
        if case.execution.operation!='evidence':return await self.rag_access(case)
        from learntrail_agent.evaluation.framework import offline_bm25
        sources={s['slug']:s for s in self.corpus['sources']};evidence=case.evidence
        valid=all(e.slug in sources and sources[e.slug]['id']==e.article_id and
            sources[e.slug]['content_hash']==e.content_hash and sources[e.slug]['text'][e.start_char:e.end_char]==e.quote for e in evidence)
        covered=set(f for e in evidence for f in e.supports)
        return {'evidence_valid':valid,'required_facts_covered':all(f in covered for f in case.expected.required_facts) if case.expected.answer_available else True,
            'answer_available':case.expected.answer_available,'evidence_count':len(evidence),
            'source_scope':case.execution.parameters['source_scope'],'question_sha256':hashlib.sha256(case.question.encode()).hexdigest(),
            'retrieval':offline_bm25(case,self.corpus,self.settings)['retrieval']}

    async def rag_access(self,case):
        from learntrail_agent.rag.retriever import Retriever
        from learntrail_agent.rag.qa import RagService
        from learntrail_agent.storage.search_repository import SearchRepository
        from learntrail_agent.storage.vector_repository import VectorRepository,IndexState
        p=case.execution.parameters;uid=p['user_id'];op=case.execution.operation
        java=AsyncMock(spec=JavaArticleClient);emb=SimpleNamespace(embed=AsyncMock())
        repo=AsyncMock(spec=SearchRepository);vectors=AsyncMock(spec=VectorRepository)
        indexer=ArticleIndexer(java,emb,vectors,self.settings)
        source=AuthorizedArticle(uid,PublishedArticle(id=p['article_id'],slug=p['slug'],title=p['title'],body=p['stale_text'],
            status='published',contentFormat='text',sourceType='original'))
        doc,fingerprint,metadata=indexer.prepare_source(source)
        index=IndexState(user_id=uid,article_id=source.article.id,slug=p['slug'],active=True,
            content_hash=doc.content_hash,fingerprint=fingerprint,generation=uuid4(),dimensions=3,chunk_count=1,
            source_metadata=metadata,embedding_model='synthetic-guard-fixture')
        repo.active_indexes.return_value=[index];vectors.get_state.return_value=index
        java.get_published_article.side_effect=ArticleUnavailable(uid,p['slug'])
        r=Retriever(repo,vectors,java,indexer,emb,Mock(),self.settings)
        if op=='other_session':
            sid=(await self.state.sessions.create(uid,case.question[:80])).id
            qa=RagService(r,Mock(),self.state.sessions,self.settings)
            error=None
            try:await qa.answer(uid+1,sid,SearchRequest(query=case.question),'fixture-token')
            except AppError as e:error=e.code
            return {'error':error,'article_queries':java.get_published_article.await_count,'embedding_calls':emb.embed.await_count}
        result=await r.search(uid,SearchRequest(query=case.question,mode='vector'),'fixture-token')
        return {'returned_chunks':len(result.results),'invalidations':vectors.invalidate_generation.await_count,
            'embedding_calls':emb.embed.await_count,'source_state':p['source_state']}

    async def run_memory(self,case):
        p=case.execution.parameters;op=case.execution.operation;uid=p['user_id'];repo=self.state.advanced.memories
        sid=(await self.state.sessions.create(uid,case.question[:80])).id
        if case.history:
            history_model=ScriptedModel([],p['final_response']);factory,_=await self.fixture_service(case,history_model)
            await self.seed_history(case,factory(),history_model,sid)
        memory=None
        if p.get('initial'):memory=await repo.create(uid,MemoryCreate(**p['initial'],source_session_id=sid))
        out={'question':case.question}
        if op=='proposal':
            args=p['proposal'];model=ScriptedModel([[{'name':'remember_learning_preference','arguments':args}]],p['final_response'])
            factory,_=await self.fixture_service(case,model);agent=factory()
            result=await agent.execute(await agent.prepare(uid,sid,AgentChatRequest(content=case.question),'case-token'))
            pending=await self.state.advanced.approvals.list(uid,sid)
            out.update(status=result.status,pending=len(pending),memories=len(await repo.list(uid)))
            if p.get('decision') and pending:
                a=pending[0];await self.state.advanced.approvals.decide(uid,sid,a['approval_id'],ApprovalDecision(action=p['decision'],expected_hash=a['arguments_hash']))
                await agent.execute(await agent.prepare_resume(uid,sid,result.run_id,'case-token'))
                saved=await repo.list(uid);out.update(memories=len(saved),content=saved[0].content if saved else None,key=saved[0].key if saved else None)
        elif op=='create':
            try:
                created=await repo.create(uid,MemoryCreate(**p['value'],source_session_id=sid));out.update(content=created.content,key=created.key,memories=len(await repo.list(uid)))
            except (AppError,ValidationError) as e:out['error']=e.code if isinstance(e,AppError) else 'INVALID_MEMORY'
        elif op=='update':
            try:
                updated=await repo.update(uid,memory.id,MemoryUpdate(**p['value'],version=p['version'],source_session_id=sid))
                out.update(content=updated.content,key=updated.key,version=updated.version)
            except AppError as e:out['error']=e.code
        elif op in {'retrieve','delete','isolation'}:
            if op=='delete':await repo.delete(uid,memory.id,memory.version)
            if p.get('expire'):
                async with self.state.pool.connection() as conn:
                    await conn.execute("UPDATE ai_memories SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(memory.id,))
            other=(await self.state.sessions.create(uid,'another-session-'+case.id)).id
            found=await repo.relevant(uid+1 if op=='isolation' else uid,case.question,3,2000)
            out.update(retrieved_ids=[str(x['memory_id']) for x in found],retrieved=len(found),same_source_session=sid==other,
                target_present=any(x['memory_id']==str(memory.id) for x in found))
            if op=='isolation':
                try:await repo.get(uid+1,memory.id)
                except AppError as e:out['error']=e.code
        else:raise ValueError('Unsupported memory operation')
        return out

    async def run_skills(self,case):
        p=case.execution.parameters;catalog=SkillCatalog(self.settings.skills_dir)
        selected=catalog.choose(case.question,p.get('requested_skill'))
        out={'selected_skill':selected.metadata.name if selected else None,'question':case.question}
        loaded=None
        if selected:
            tools=frozenset(p['available_tools'])
            try:
                loaded=catalog.load(selected,tools);out['authorized']=True;out['allowed_tools']=loaded['tools']
            except AppError as e:out['authorized']=False;out['error']=e.code
        if case.execution.operation=='authorization':
            from learntrail_agent.agent.tools import ToolRegistry
            from learntrail_agent.agent.learntrail_tools import ArticleArgs
            registry=ToolRegistry();effects=[]
            async def forbidden(args,ctx):effects.append(args);return ToolOutput()
            registry.register(Tool(p['attempted_tool'],'Fixture forbidden action',ArticleArgs,forbidden,'fixture'))
            async def verify():return None
            if p.get('untrusted_guide'):
                folder=self.workdir/('skill-fixture-'+case.id)/'bounded';folder.mkdir(parents=True,exist_ok=True)
                file=folder/'SKILL.md'
                header='---\nname: bounded\ndescription: bounded fixture\nscenarios: [permission test]\ntriggers: [test]\ntools: [search_knowledge_base, search_articles]\n---\n'
                file.write_text(header+p['untrusted_guide'])
                attack=SkillCatalog(folder.parent);skill=attack.choose('test')
                loaded=attack.load(skill,frozenset(p['available_tools']))
            allowed=loaded['tools'] if loaded else []
            context=ToolContext(p['user_id'],SecretStr('fixture'),frozenset({'fixture'}),verify,
                allowed_tools=frozenset(allowed))
            result=await ToolExecutor(registry).execute(call(p['attempted_tool'],p['arguments']),context)
            out.update(error=result.error_code,executions=len(effects))
        elif p.get('exercise_tool'):
            run=await self.run_graph(case)
            out.update(tool_calls=run['tool_calls'],tool_names=run['tool_names'],graph_status=run['status'])
        return out

    async def run_hitl(self,case):
        p=case.execution.parameters;uid=p['user_id'];op=case.execution.operation
        model=ScriptedModel([[{'name':'save_learning_plan_demo','arguments':p['plan']}]],p['final_response'])
        factory,_=await self.fixture_service(case,model);agent=factory();sid=(await self.state.sessions.create(uid,case.question[:80])).id
        if op=='read_only':
            model.batches=[[{'name':'get_learning_progress','arguments':p['arguments']}]]
            result=await agent.execute(await agent.prepare(uid,sid,AgentChatRequest(content=case.question),'case-token'))
            return {'status':result.status,'tool_calls':result.tool_calls,'approvals':len(await self.state.advanced.approvals.list(uid,sid))}
        if op=='unknown':
            from learntrail_agent.agent.advanced_models import DemoPlan
            entered=asyncio.Event();effects=[]
            async def external(args,ctx):effects.append(str(ctx.approval_id));entered.set();await asyncio.Event().wait()
            agent.loop.registry.register(Tool('external_case','Unknown external result fixture',DemoPlan,external,'demo:write',10,policy='approval_required'))
            model.batches=[[{'name':'external_case','arguments':p['plan']}]]
        paused=await agent.execute(await agent.prepare(uid,sid,AgentChatRequest(content=case.question),'case-token'))
        a=(await self.state.advanced.approvals.list(uid,sid))[0];out={'paused':paused.status=='paused'}
        approvals=self.state.advanced.approvals
        async def decide(action,user=uid,hash_value=None):
            return await approvals.decide(user,sid,a['approval_id'],ApprovalDecision(action=action,expected_hash=hash_value or a['arguments_hash']))
        if op=='pause':pass
        elif op=='expire':
            async with self.state.pool.connection() as conn:
                await conn.execute("UPDATE ai_approvals SET created_at=clock_timestamp()-interval '1 minute',expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(a['approval_id'],))
        elif op=='cancel_owner':
            try:await decide('approve',uid+1)
            except AppError as e:out['owner_error']=e.code
            await decide('cancel')
        elif op=='tamper':
            try:await decide('approve',hash_value='0'*64)
            except AppError as e:out['error']=e.code
        else:
            await decide('reject' if op=='reject' else 'approve')
            if op=='duplicate':await decide('approve')
        if op=='unknown':
            prepared=await agent.prepare_resume(uid,sid,paused.run_id,'case-token');task=asyncio.create_task(agent.execute(prepared))
            await asyncio.wait_for(entered.wait(),3)
            await self.state.sessions.cancel_run(uid,sid,paused.run_id);await approvals.cancel_run(uid,sid,paused.run_id)
            try:await asyncio.wait_for(task,3)
            except AppError as e:out['cancel_error']=e.code
            await agent.execute(await agent.prepare_resume(uid,sid,paused.run_id,'case-token'))
            out.update(started_count=len(effects),known_effect_count=None,retry_count=len(effects)-1)
        elif op not in {'pause','tamper'}:
            if op=='resume':agent=factory()
            result=await agent.execute(await agent.prepare_resume(uid,sid,paused.run_id,'case-token'));out['resumed']=result.status=='succeeded'
            if op=='duplicate':
                await decide('approve')
                try:await agent.execute(await agent.prepare_resume(uid,sid,paused.run_id,'case-token'))
                except AppError as e:out['duplicate_resume_error']=e.code
        final=await approvals.get(uid,sid,a['approval_id']);out['approval_status']=final['status']
        async with self.state.pool.connection() as conn:
            total=(await (await conn.execute('SELECT count(*) AS n FROM ai_demo_actions WHERE approval_id=%s',(a['approval_id'],))).fetchone())['n']
            out['demo_row_count' if op=='unknown' else 'effect_count']=total
        return out

    async def run_mcp(self,case):
        p=case.execution.parameters;op=case.execution.operation
        async def verify():return None
        context=ToolContext(p['user_id'],SecretStr('fixture'),frozenset({'mcp:case'}),verify)
        if op=='schema':
            try:arguments_model('case','bounded',p['schema']).model_validate(p['arguments']);return {'accepted':True}
            except ValueError:return {'accepted':False}
        process=None
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        args=['-m','learntrail_agent.mcp_demo_server']
        if p.get('disconnect_once'):
            path=self.workdir/(case.id+'-disconnect');args+=['--disconnect-once-file',str(path)]
        async def start_http():
            nonlocal process
            process=await asyncio.create_subprocess_exec(sys.executable,*args,'--http','--port',str(port),
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            async with httpx.AsyncClient(trust_env=False) as http:
                for _ in range(100):
                    try:await http.get(f'http://127.0.0.1:{port}/mcp',timeout=.1);return
                    except httpx.TransportError:await asyncio.sleep(.03)
            raise TimeoutError()
        async def stop_http():
            if process and process.returncode is None:process.terminate();await asyncio.wait_for(process.wait(),5)
        try:
            if p['transport']=='http':await start_http()
            profile=MCPProfile(id='case',trusted=True,transport=p['transport'],allow_tools=p['allow_tools'],
                command=sys.executable if p['transport']=='stdio' else None,args=args if p['transport']=='stdio' else [],
                url=f'http://127.0.0.1:{port}/mcp' if p['transport']=='http' else None,timeout_seconds=p.get('timeout_seconds',5))
            client=MCPClient([profile],'')
            from learntrail_agent.agent.tools import ToolRegistry
            registry=ToolRegistry();await client.register(registry,context)
            out={'registered':sorted(t.name for t in registry.tools())}
            if op=='discovery':return out
            if op=='recovery' and p['transport']=='http':await stop_http();await start_http()
            tool=registry.get('mcp_case_'+p['tool'])
            result=await ToolExecutor(registry).execute(call('mcp_case_'+p['tool'],p['arguments']),context)
            out.update(ok=result.ok,error=result.error_code)
            if result.ok:
                out['topic']=result.data.get('result',{}).get('topic');out['preview_only']=result.data.get('result',{}).get('preview_only')
            return out
        finally:await stop_http()
