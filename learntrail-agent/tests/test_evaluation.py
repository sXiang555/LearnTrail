"""Metric ground truth and mocked judge protocols; live model benchmarks are explicit CLI runs."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from learntrail_agent.evaluation.metrics import retrieval_metrics,citation_metrics,answer_metrics,trajectory_metrics,latency,aggregate
from learntrail_agent.evaluation.runner import BudgetedModel,isolated_database,private_json,VARIANTS,normalized_trajectory,snapshot
from learntrail_agent.evaluation.judge import Judge,Verdict,ragas_metrics
from learntrail_agent.errors import AppError
from learntrail_agent.schemas import ChatResponse,Usage
from test_postgres_integration import postgres_repository


def test_retrieval_ndcg_duplicates_no_answer_and_withdrawn():
    rows=[{"relevant":["a","b"],"ranked":["a","a","b"]},{"relevant":[],"ranked":["withdrawn"],"forbidden":["withdrawn"]}]
    result=retrieval_metrics(rows,3)
    assert result['recall']==1 and result['mrr']==1 and 0<result['ndcg']<1
    assert result['negative_nonempty_rate']==1 and result['forbidden_hits']==1
    assert retrieval_metrics([],3)['recall'] is None
    assert retrieval_metrics([{"relevant":["a"],"ranked":[]}],3)['recall']==0


def test_citation_position_accuracy_is_not_semantic_support():
    sources={"a":{"text":"中文原文引用","content_hash":"abc"}}
    valid={"slug":"a","start_char":2,"end_char":4,"quote":"原文","content_hash":"abc","position_basis":"normalized_unicode_codepoints"}
    assert citation_metrics([valid],sources)['source_position_accuracy']==1
    assert citation_metrics([valid,{**valid,"quote":"编造"}],sources)['source_position_accuracy']==.5
    assert citation_metrics([],sources)['source_position_accuracy'] is None


def test_refusal_errors_stay_in_denominator():
    rows=[{"expected_answer_available":False,"status":"insufficient_context"},{"expected_answer_available":True,"status":"error"}]
    m=answer_metrics(rows)
    assert m['refusal_recall']==1 and m['answer_coverage']==0 and m['decision_accuracy']==.5


def test_trajectory_tool_selection_parameters_failures_and_semantic_unknown():
    events=[{"kind":"model_end","payload":{"tool_calls":[{"function":{"name":"search_articles","arguments":'{"page_size":3}'}}]}},
        {"kind":"tool_result","payload":{"name":"search_articles","ok":True}}]
    expected={"required_tools":["search_articles"],"arguments":{"search_articles":{"page_size":3}}}
    m=trajectory_metrics(expected,events,'succeeded')
    assert m['trajectory_success'] and m['tool_parameter_accuracy']==1 and m['semantic_task_success'] is None
    wrong=trajectory_metrics({**expected,"arguments":{"search_articles":{"page_size":10}}},events,'succeeded')
    assert not wrong['trajectory_success'] and wrong['tool_parameter_accuracy']==0
    assert not trajectory_metrics(expected,events,'failed')['trajectory_success']
    assert trajectory_metrics({"required_tools":[]},[],"succeeded")['trajectory_success']


def test_latency_and_unverified_not_counted_as_success():
    assert latency([1,2,3,4])['p95_ms']==4
    m=aggregate([{"verification":"executed","metrics":{"trajectory_success":False},"duration_ms":10},{"verification":"unverified"}])
    assert m['executed']==1 and m['unverified']==1 and m['trajectory_success_rate']==0


def test_cost_subtotals_do_not_turn_unknown_usage_or_prices_into_zero():
    rows=[{"verification":"executed","metrics":{"trajectory_success":True},"model_calls":2,"usage":{"total_tokens":30},"usage_complete":True,
        "cost":{"status":"estimated","amount":"0.0003","currency":"CNY"}},
        {"verification":"executed","metrics":{"trajectory_success":False},"model_calls":1,"usage":None,"usage_complete":False,
         "cost":{"status":"unknown","amount":None}}]
    report=aggregate(rows)
    assert report['model_calls']['known_total']==3 and report['tokens']['known_total']==30
    assert report['tokens']['incomplete_cases']==1
    assert report['cost']['estimated_subtotals']=={'CNY':'0.0003'}
    assert report['cost']['unknown_cases']==1 and report['cost']['complete_estimate'] is False
    assert report['cost']['is_provider_invoice'] is False


@pytest.mark.asyncio
async def test_model_budget_checked_before_network_including_failures():
    client=AsyncMock();client.complete_with_tools.side_effect=AppError('LLM_TIMEOUT','controlled',504)
    budget=[0,1];wrapped=BudgetedModel(client,budget)
    with pytest.raises(AppError):await wrapped.complete_with_tools(None)
    with pytest.raises(AppError) as e:await wrapped.complete_with_tools(None)
    assert e.value.code=='EVALUATION_BUDGET_EXCEEDED' and budget[0]==1
    client.complete_with_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_judge_independent_configuration_budget_and_invalid_json(settings):
    async with httpx.AsyncClient() as http:
        missing=Judge(http,settings)
        assert (await missing.assess('q','r','a',[]))['reason']=='JUDGE_NOT_CONFIGURED'
        from pydantic import SecretStr
        cfg=settings.model_copy(update={"judge_model":"judge-fixture","judge_base_url":"https://judge.example/v1","judge_api_key":SecretStr('test-only'),"judge_max_calls":2})
        judge=Judge(http,cfg)
        assert judge.llm.settings.llm_model=='judge-fixture' and judge.llm.settings.llm_model!=settings.llm_model
        judge.llm.complete=AsyncMock(side_effect=[
            ChatResponse(id='t',model='judge-fixture',finish_reason='stop',content=json.dumps({"task_success":True,"answer_correctness":1.0,"faithfulness":1.0,"rationale":"Supported"}),usage=Usage(total_tokens=10)),
            ChatResponse(id='t',model='judge-fixture',finish_reason='stop',content='not json')])
        assert (await judge.assess('q','r','a',[]))['task_success'] is True
        assert (await judge.assess('q','r','a',[]))['reason']=='JUDGE_INVALID_RESPONSE'
        assert (await judge.assess('q','r','a',[]))['reason']=='JUDGE_BUDGET_EXCEEDED'
        assert len(judge.calls)==2


@pytest.mark.asyncio
async def test_judge_rejects_credential_shaped_model_before_http(settings):
    from pydantic import SecretStr
    config=settings.model_copy(update={'judge_model':'sk-test-only-misplaced-secret',
        'judge_base_url':'https://judge.example/v1','judge_api_key':SecretStr('test-only')})
    async with httpx.AsyncClient() as http:
        judge=Judge(http,config);judge.llm.complete=AsyncMock()
        assert (await judge.assess('q','r','a',[]))['reason']=='JUDGE_INVALID_CONFIGURATION'
        assert (await ragas_metrics(judge,'q','a','r',[]))['reason']=='JUDGE_INVALID_CONFIGURATION'
        with pytest.raises(AppError) as caught:await judge.generate('q',Verdict)
        assert caught.value.code=='JUDGE_INVALID_CONFIGURATION'
        assert judge.calls==[]
        judge.llm.complete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('base,mode,thinking,json_mode',[
    ('https://api.deepseek.com','auto','disabled',True),
    ('https://api.deepseek.com','enabled','enabled',False),
    ('https://api.deepseek.com','provider_default',None,False),
    ('https://judge.example/v1','auto',None,False),
])
async def test_judge_provider_options_and_independent_http_timeout(settings,respx_mock,base,mode,thinking,json_mode):
    from pydantic import SecretStr
    config=settings.model_copy(update={'judge_model':'deepseek-v4-pro','judge_base_url':base,
        'judge_api_key':SecretStr('test-only'),'judge_timeout_seconds':30,'judge_thinking_mode':mode})
    seen=[]
    def respond(request):
        body=json.loads(request.content);seen.append(body)
        assert request.extensions['timeout']['read']==30
        assert request.extensions['timeout']['connect']==1
        return httpx.Response(200,json={'id':'fixture','model':'deepseek-v4-pro','choices':[{
            'index':0,'message':{'content':json.dumps({'task_success':True,'answer_correctness':1.,'faithfulness':1.,'rationale':'Supported'})},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
    route=respx_mock.post(base+'/chat/completions').mock(side_effect=respond)
    async with httpx.AsyncClient(timeout=.001) as http:
        judge=Judge(http,config)
        assert (await judge.assess('q','r','a',['r']))['status']=='scored'
        body=seen[0]
        assert body.get('thinking')==({'type':thinking} if thinking else None)
        assert body.get('response_format')==({'type':'json_object'} if json_mode else None)
        assert judge.calls[0]['thinking_mode']==(thinking or 'provider_default')
        assert judge.calls[0]['duration_ms']>=0 and route.call_count==1


@pytest.mark.asyncio
async def test_judge_read_timeout_is_distinct_and_never_retried(settings,respx_mock):
    from pydantic import SecretStr
    config=settings.model_copy(update={'judge_model':'judge-fixture','judge_base_url':'https://judge.example/v1',
        'judge_api_key':SecretStr('test-only')})
    route=respx_mock.post('https://judge.example/v1/chat/completions').mock(side_effect=httpx.ReadTimeout('PRIVATE UPSTREAM ERROR'))
    async with httpx.AsyncClient() as http:
        judge=Judge(http,config);result=await judge.assess('q','r','a',[])
        assert result=={'status':'unverified','reason':'JUDGE_TIMEOUT'}
        assert route.call_count==1 and len(judge.calls)==1
        assert judge.calls[0]['error']=='JUDGE_TIMEOUT' and 'usage' not in judge.calls[0]
        assert 'PRIVATE' not in json.dumps(result) and judge.calls[0]['duration_ms']>=0


@pytest.mark.asyncio
async def test_judge_total_deadline_cancels_pending_http(settings,respx_mock):
    import asyncio
    from pydantic import SecretStr
    closed=asyncio.Event();attempts=[]
    async def stalled(request):
        attempts.append(request)
        try:await asyncio.sleep(1)
        finally:closed.set()
    route=respx_mock.post('https://judge.example/v1/chat/completions').mock(side_effect=stalled)
    config=settings.model_copy(update={'judge_model':'judge-fixture','judge_base_url':'https://judge.example/v1',
        'judge_api_key':SecretStr('test-only'),'judge_timeout_seconds':.02})
    async with httpx.AsyncClient() as http:
        judge=Judge(http,config)
        assert (await judge.assess('q','r','a',[]))['reason']=='JUDGE_TIMEOUT'
        assert closed.is_set() and len(attempts)==1 and len(judge.calls)==1
        assert 10<=judge.calls[0]['duration_ms']<500


@pytest.mark.asyncio
async def test_judge_user_cancellation_stays_cancelled(settings,respx_mock):
    import asyncio
    from pydantic import SecretStr
    entered=asyncio.Event();closed=asyncio.Event();attempts=[]
    async def stalled(request):
        attempts.append(request)
        entered.set()
        try:await asyncio.sleep(10)
        finally:closed.set()
    route=respx_mock.post('https://judge.example/v1/chat/completions').mock(side_effect=stalled)
    config=settings.model_copy(update={'judge_model':'judge-fixture','judge_base_url':'https://judge.example/v1',
        'judge_api_key':SecretStr('test-only')})
    async with httpx.AsyncClient() as http:
        judge=Judge(http,config);task=asyncio.create_task(judge.assess('q','r','a',[]))
        await asyncio.wait_for(entered.wait(),1);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert closed.is_set() and len(attempts)==1 and len(judge.calls)==1
        assert judge.calls[0]['status']=='cancelled' and judge.calls[0]['error']=='JUDGE_CANCELLED'


@pytest.mark.asyncio
async def test_ragas_optional_new_metrics_use_shared_budget(settings):
    pytest.importorskip('ragas',reason='Optional evaluation dependency group not installed')
    # Actual RAGAS algorithms, deterministic structured judge substitute; not live quality.
    async with httpx.AsyncClient() as http:
        judge=Judge(http,settings);judge.configured=True
        calls=[]
        async def generate(prompt,model):
            calls.append(model.__name__)
            fields=model.model_fields
            if model.__name__=='NLIStatementOutput':return model.model_validate({'statements':[{'statement':'Python supports async.','reason':'Supported by context','verdict':1}]})
            if 'statements' in fields:return model.model_validate({'statements':['Python supports async.']})
            if 'verdicts' in fields:return model.model_validate({'verdicts':[{'statement':'Python supports async.','reason':'Supported by context','verdict':1}]})
            if 'verdict' in fields:return model.model_validate({'reason':'Relevant context','verdict':1})
            raise AssertionError('Unexpected RAGAS contract: '+str(fields.keys()))
        judge.generate=generate
        result=await ragas_metrics(judge,'Does Python support async?','Python supports async.','Python supports async.',['Python supports async.'])
        assert calls and result['scores']['faithfulness']['status']=='scored'
        assert result['scores']['faithfulness']['value']==1
        assert result['scores']['context_precision']['status']=='scored'


def test_dataset_has_reviewed_expectations_and_no_fabricated_historical_baseline():
    data=json.loads((Path(__file__).parents[1]/'evaluation/cases.json').read_text())
    assert len({c['id'] for c in data['agent_cases']})==len(data['agent_cases'])
    assert all(c['reference'] and 'required_tools' in c['expected'] for c in data['agent_cases'])
    assert data['comparisons']['historical_handwritten_loop']['status']=='unverified'
    assert set(VARIANTS)=={'baseline','prompt_checklist','no_memory','no_skills'}


def test_reports_are_private_exclusive_and_no_symlink_overwrite(tmp_path):
    path=tmp_path/'report.json';private_json(path,{'ok':True})
    assert path.stat().st_mode&0o077==0
    with pytest.raises(FileExistsError):private_json(path,{'overwrite':True})


def test_evaluation_applies_declared_defaults_without_fixing_invalid_arguments():
    from learntrail_agent.agent.learntrail_tools import ArticleArgs
    from learntrail_agent.agent.tools import Tool,ToolRegistry,ToolOutput
    async def read(args,ctx):return ToolOutput()
    registry=ToolRegistry();registry.register(Tool('search_articles','published article fixture',ArticleArgs,read,'read'))
    events=[{"kind":"model_end","payload":{"tool_calls":[{"function":{"name":"search_articles","arguments":'{"keyword":"Python"}'}}]}}]
    parsed=normalized_trajectory(events,registry)
    args=json.loads(parsed[0]['payload']['tool_calls'][0]['function']['arguments'])
    assert args['page_no']==1 and args['page_size']==5
    assert 'page_no' not in events[0]['payload']['tool_calls'][0]['function']['arguments']


def test_reproducible_source_archive_excludes_secrets(tmp_path):
    import zipfile
    value=snapshot(tmp_path)
    assert len(value)==64
    path=tmp_path/'source.zip'
    assert path.stat().st_mode&0o077==0
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        assert 'uv.lock' in names and 'evaluation/cases.json' in names
        assert not any(name.startswith(('.env','.local','.venv')) or name.endswith('.key') for name in names)


@pytest.mark.asyncio
async def test_real_evaluation_schema_never_uses_existing_session_tables(postgres_repository,settings,monkeypatch):
    import os
    from pydantic import SecretStr
    import psycopg
    dsn=os.environ['LEARNTRAIL_TEST_DATABASE_URL']
    config=settings.model_copy(update={"database_url":SecretStr(dsn)})
    async with isolated_database(config) as schema:
        async with await psycopg.AsyncConnection.connect(dsn,autocommit=True) as conn:
            row=await (await conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname=%s AND tablename='ai_approvals'",(schema,))).fetchone()
            assert row[0]==1
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        assert (await (await conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname=%s",(schema,))).fetchone())[0]==0
