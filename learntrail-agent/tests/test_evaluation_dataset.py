"""Formal dataset and evaluator contracts; no billable calls."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from learntrail_agent.evaluation.datasets import EvaluationCase,load_dataset,select_cases
from learntrail_agent.evaluation.framework import deterministic_case,offline_bm25,run_dataset
from learntrail_agent.evaluation.metrics import category_metrics,retrieval_metrics,set_metrics,tool_metrics,engineering_metrics,repeat_summary
from learntrail_agent.evaluation.runner import BudgetedModel
from learntrail_agent.errors import AppError

ROOT=Path(__file__).parents[1]
MANIFEST=ROOT/'evaluation/dataset-v2.json'


def test_dataset_exact_counts_stable_legacy_evidence_and_review_states():
    manifest,cases,corpus=load_dataset(MANIFEST)
    assert len(cases)==150
    assert manifest.categories=={'rag':70,'agent':35,'memory':15,'skills':10,'mcp':10,'hitl':10}
    assert {s:len(select_cases(cases,split=s)) for s in manifest.splits}=={'development':90,'validation':30,'test':30}
    assert sum(c.status=='verified' for c in cases)==8
    legacy=json.loads((ROOT/'tests/fixtures/rag_retrieval_cases.json').read_text())
    assert {c.question for c in cases if c.source.legacy_id}=={c['query'] for c in legacy['cases']}
    assert len(corpus['sources'])==7
    assert all('chunk_id' not in e.model_dump() for c in cases for e in c.evidence)


@pytest.mark.parametrize('mutation',['split','quote','duplicate','verified'])
def test_dataset_rejects_split_leakage_evidence_drift_duplicates_and_unreviewed_promotion(tmp_path,mutation):
    manifest=json.loads(MANIFEST.read_text())
    data=[json.loads(s) for s in (ROOT/'evaluation/cases-v2.jsonl').read_text().splitlines()]
    if mutation=='split':
        # Swap splits preserving counts, but break article/topic family isolation.
        data[1]['split'],data[40]['split']=data[40]['split'],data[1]['split']
    elif mutation=='quote':data[1]['evidence'][0]['quote']='X'*len(data[1]['evidence'][0]['quote'])
    elif mutation=='duplicate':data[1]['id']=data[0]['id']
    else:data[1]['status']='verified'
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/manifest['cases_file']).write_text('\n'.join(json.dumps(x) for x in data))
    (tmp_path/manifest['corpus_file']).write_bytes((ROOT/'evaluation/corpus-v2.json').read_bytes())
    with pytest.raises(ValueError):load_dataset(tmp_path/'manifest.json')


def test_case_cannot_expect_unauthorized_tool_or_conflicting_source():
    _,cases,_=load_dataset(MANIFEST);value=cases[0].model_dump()
    value['expected']['required_tools']=['unknown']
    with pytest.raises(ValidationError):EvaluationCase.model_validate(value)
    value=cases[0].model_dump();value['expected']['forbidden']=list(value['expected']['relevant'])
    with pytest.raises(ValidationError):EvaluationCase.model_validate(value)


def test_graded_ndcg_precision_at_fixed_k_and_no_duplicate_gain():
    rows=[{'relevant':['a','b'],'grades':{'a':3,'b':1},'ranked':['b','a','a']}]
    m=retrieval_metrics(rows,5)
    assert m['precision']==.4 and m['recall']==1
    assert 0<m['graded_ndcg']<1 and m['graded_cases']==1
    assert retrieval_metrics([{'relevant':['a'],'ranked':['a']}],5)['graded_ndcg'] is None


def test_tool_f1_allows_extra_authorized_path_and_order_changes():
    expected={'required_tools':['a','b'],'allowed_tools':['a','b','c'],'arguments':{'a':{'n':2}}}
    events=[{'kind':'model_end','payload':{'tool_calls':[
        {'function':{'name':n,'arguments':'{"n":2}'}} for n in ['b','c','a']]}},
        *[{'kind':'tool_result','payload':{'name':n,'ok':True}} for n in ['a','b','c']]]
    m=tool_metrics(expected,events)
    assert m['tool_selection_f1']==1 and m['argument_correctness']==1 and m['unnecessary_tool_call_rate']==0
    events[0]['payload']['tool_calls'].append({'function':{'name':'write','arguments':'{}'}})
    assert tool_metrics(expected,events)['unnecessary_tool_call_rate']==.25


def test_missing_metrics_are_not_success_and_repeat_variance_is_measured():
    for category in ['rag','agent','memory','skills','mcp','hitl']:
        m=category_metrics(category,[{'status':'unverified'}])
        assert m['metrics']['task_success_rate']=={'value':None,'observations':0}
    assert set_metrics(['a','b'],['a','c'])['f1']==.5
    failures=category_metrics('agent',[{'status':'error'}, {'status':'executed','observations':{'task_success':True}}])
    assert failures['metrics']['task_success_rate']=={'value':.5,'observations':2}
    rows=[{'repeat':1,'observations':{'task_success':True}},{'repeat':2,'observations':{'task_success':False}}]
    assert repeat_summary(rows)['population_stddev']==.5
    rows[0].update(cost={'status':'estimated','amount':'1','currency':'CNY'})
    assert engineering_metrics(rows)['cost_per_successful_task'] is None
    rows[1].update(cost={'status':'estimated','amount':'2','currency':'CNY'})
    assert engineering_metrics(rows)['cost_per_successful_task']=={'CNY':'3'}


@pytest.mark.asyncio
async def test_new_runner_validation_is_offline_and_defaults_to_development(settings,tmp_path):
    args=SimpleNamespace(dataset=MANIFEST,dataset_version='learntrail-eval-2.0.0',split='development',
        categories=None,collection=None,case_ids=None,mode='validate',output=tmp_path)
    report,folder=await run_dataset(args,settings)
    assert report['selected']==90 and folder is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_bm25_snapshot_and_skill_ablation_measure_real_outputs(settings,tmp_path):
    _,cases,corpus=load_dataset(MANIFEST)
    row=offline_bm25(cases[0],corpus,settings)
    assert row['retrieval']['ranked'] and 'task_success' not in row
    skill=next(c for c in cases if c.id=='skills-001')
    enabled=await deterministic_case(skill,corpus,settings,tmp_path,{},'skills')
    disabled=await deterministic_case(skill,corpus,settings,tmp_path,{},'no_skills')
    assert enabled['observations']['selected_skill']=='knowledge-learning'
    assert disabled['observations']['selected_skill'] is None
    assert disabled['observations']['expected_skill']=='knowledge-learning'
    assert 'task_success' not in enabled['observations']


@pytest.mark.asyncio
async def test_plain_model_budget_is_checked_before_every_attempt():
    client=AsyncMock();budget=[0,1];model=BudgetedModel(client,budget)
    await model.complete(None)
    with pytest.raises(AppError):await model.complete(None)
    client.complete.assert_awaited_once()


def test_all_contract_references_resolve_to_existing_test_functions():
    import ast
    _,cases,_=load_dataset(MANIFEST)
    for node in {n for c in cases for n in c.contract_tests}:
        file,function=node.split('::')
        definitions={n.name for n in ast.walk(ast.parse((ROOT/file).read_text())) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        assert function in definitions,node


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy',['bm25','vector','hybrid_reranker'])
async def test_selected_retrieval_strategy_reaches_answer_generation(strategy):
    from unittest.mock import Mock
    from learntrail_agent.evaluation.runner import rag_suite
    response=SimpleNamespace(status='insufficient_context',answer='没有足够内容。',duration_ms=1,citations=[],model='fixture',usage=None)
    state=SimpleNamespace(settings=SimpleNamespace(reranker_configured=True),
        retriever=SimpleNamespace(search=AsyncMock(return_value=SimpleNamespace(results=[],duration_ms=1,embedding_usage=None))),
        rag_service=SimpleNamespace(answer=AsyncMock(return_value=response)),
        sessions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id='session'))),
        telemetry=SimpleNamespace(score=Mock(),pricing=SimpleNamespace(estimate=Mock(return_value={'status':'unknown'}))))
    judge=SimpleNamespace(assess=AsyncMock(return_value={'status':'unverified'}))
    dataset={'sources':[{'slug':'fixture-source'}],'cases':[{'id':'r','query':'未知问题','relevant':[],'answer_available':False,'evidence':'没有答案'}]}
    await rag_suite(state,SimpleNamespace(user_id=41),'test-token',dataset,{},judge,True,False,strategies=[strategy],answer_strategy=strategy)
    request=state.rag_service.answer.call_args.args[2]
    assert request.mode==('hybrid' if strategy=='hybrid_reranker' else strategy)
    assert request.rerank==(strategy=='hybrid_reranker')


def test_paired_skill_selection_is_not_semantic_success():
    from learntrail_agent.evaluation.metrics import paired_comparisons
    rows=[{'id':'skills-001','repeat':1,'category':'skills','scheme':scheme,
        'observations':{'selected_skill':selected,'expected_skill':'knowledge-learning'}}
        for scheme,selected in [('no_skills',None),('skills','knowledge-learning')]]
    result=paired_comparisons(rows)[0]
    assert result['skill_selection_improvement']==1 and result['selection_pairs']==1
    assert result['success_rate_improvement'] is None and result['semantic_pairs']==0
