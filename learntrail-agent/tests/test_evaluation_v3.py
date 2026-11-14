"""All repaired cases execute their own input; controlled fixtures, real SQL/MCP."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import ValidationError

from learntrail_agent.evaluation.case_runner import CaseRunner
from learntrail_agent.evaluation.datasets import EvaluationCase,case_fingerprint,load_dataset
from learntrail_agent.evaluation.metrics import trajectory_metrics
from learntrail_agent.evaluation.runner import isolated_database
from learntrail_agent.main import create_app
from learntrail_agent.settings import Settings

ROOT=Path(__file__).parents[1]
MANIFEST=ROOT/'evaluation/dataset-v3.json'
MANIFEST_DATA,CASES,CORPUS=load_dataset(MANIFEST)


def test_v3_preserves_ids_quotas_evidence_and_removes_fixed_aliases():
    old=[json.loads(l) for l in (ROOT/'evaluation/cases-v2.jsonl').read_text().splitlines()]
    assert {c.id for c in CASES}=={c['id'] for c in old}
    assert len(CASES)==150 and MANIFEST_DATA.splits=={'development':90,'validation':30,'test':30}
    assert all(c.execution and not c.contract_tests for c in CASES)
    assert all(c.status in {'verified','candidate','needs_revision','blocked'} for c in CASES)
    assert next(c for c in CASES if c.id=='hitl-006').execution.assertions['known_effect_count'] is None
    assert next(c for c in CASES if c.id=='skills-010').expected.expected_skill=='knowledge-learning'
    assert next(c for c in CASES if c.id=='agent-013').status=='blocked'
    for c in CASES:
        if c.category=='rag' and c.expected.answer_available:
            assert set(c.expected.required_facts)<={f for e in c.evidence for f in e.supports}


def test_verification_is_bound_to_exact_case_inputs():
    case=CASES[0];value=case.model_dump()
    value['verification']={'input_sha256':case_fingerprint(case),'scope':'evidence-contract','report':'private-evidence.json','checks':{'evidence':True}}
    value['status']='verified';verified=EvaluationCase.model_validate(value)
    assert verified.status=='verified'
    value['question']+='补充追问'
    with pytest.raises(ValidationError):EvaluationCase.model_validate(value)


def test_setup_cannot_disagree_with_the_executed_arguments():
    original=next(c for c in CASES if c.id=='mcp-002')
    value=original.model_dump();value.update(status='candidate',verification=None)
    value['setup']['tool_arguments']={'topic':'说明与执行不一致'}
    with pytest.raises(ValidationError):EvaluationCase.model_validate(value)


def test_protocol_fixture_cannot_leak_by_paraphrasing_question(tmp_path):
    rows=[c.model_dump() for c in CASES]
    original=next(c for c in rows if c['id']=='mcp-002')
    target=next(c for c in rows if c['id']=='mcp-009')
    assert original['split']!=target['split']
    target.update(execution=json.loads(json.dumps(original['execution'])),
        setup=dict(original['setup']),history=list(original['history']),status='candidate',verification=None)
    target['execution']['parameters']['user_id']+=100000
    (tmp_path/'cases-v3.jsonl').write_text('\n'.join(json.dumps(c) for c in rows)+'\n')
    (tmp_path/'corpus-v2.json').write_text(json.dumps(CORPUS))
    (tmp_path/'dataset-v3.json').write_text(MANIFEST.read_text())
    with pytest.raises(ValueError,match='Executable fixture split leakage'):
        load_dataset(tmp_path/'dataset-v3.json')


def test_recovery_and_alternative_paths_do_not_require_all_attempts_successful():
    events=[{'kind':'model_end','payload':{'tool_calls':[{'function':{'name':'read','arguments':'{"n":"3"}'}}]}},
        {'kind':'tool_result','payload':{'name':'read','ok':False,'error_code':'INVALID_TOOL_ARGUMENTS'}},
        {'kind':'model_end','payload':{'tool_calls':[{'function':{'name':'read','arguments':'{"n":3}'}}]}},
        {'kind':'tool_result','payload':{'name':'read','ok':True}}]
    expected={'required_tools':['read'],'allowed_tools':['read'],'arguments':{'read':{'n':3}},
        'allow_recovery':True,'recoverable_errors':['INVALID_TOOL_ARGUMENTS']}
    assert trajectory_metrics(expected,events,'succeeded')['trajectory_success']
    assert not trajectory_metrics({**expected,'allow_recovery':False},events,'succeeded')['trajectory_success']
    assert not trajectory_metrics(expected,events[:-1],'succeeded')['trajectory_success']
    alt={'required_tools':[],'allowed_tools':['read','search'],'tool_paths':[['read'],['search']]}
    assert trajectory_metrics(alt,[{'kind':'tool_result','payload':{'name':'search','ok':True}}],'succeeded')['trajectory_success']
    assert not trajectory_metrics(alt,[],'succeeded')['trajectory_success']
    failed_path={'required_tools':[],'allowed_tools':['read','helper'],'tool_paths':[['read']],
        'allow_recovery':True,'recoverable_errors':['INVALID_TOOL_ARGUMENTS']}
    unrelated=[{'kind':'tool_result','payload':{'name':'read','ok':False,'error_code':'INVALID_TOOL_ARGUMENTS'}},
        {'kind':'tool_result','payload':{'name':'helper','ok':True}}]
    assert not trajectory_metrics(failed_path,unrelated,'succeeded')['trajectory_success']


@pytest_asyncio.fixture(scope='module',loop_scope='module')
async def case_runner(tmp_path_factory):
    dsn=os.getenv('LEARNTRAIL_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Explicit PostgreSQL test DSN is required for per-case SQL/Graph tests')
    folder=tmp_path_factory.mktemp('evaluation-v3')
    settings=Settings(_env_file=None,database_url=dsn,checkpoint_key_file=folder/'key',
        observability_enabled=False,langfuse_enabled=False,mcp_servers_file=None)
    async with isolated_database(settings) as schema:
        config=settings.model_copy(update={'database_schema':schema})
        app=create_app(config)
        async with app.router.lifespan_context(app):
            yield CaseRunner(config,CORPUS,app.state,folder)


@pytest.mark.asyncio(loop_scope='module')
@pytest.mark.parametrize('case',CASES,ids=lambda c:c.id)
async def test_each_case_uses_own_input_and_expected_checks(case_runner,case):
    result=await case_runner.run(case)
    if case.status=='blocked':
        assert result['status']=='unverified' and result['reason']==case.blocking_reason
        return
    assert result['status']=='executed',(case.id,result)
    assert result['input_sha256']==case_fingerprint(case)
    assert result['question_used']==case.question
    assert set(result['checks'])==set(case.execution.assertions)
    assert all(result['checks'].values())


@pytest.mark.asyncio(loop_scope='module')
async def test_changed_mcp_argument_does_not_reuse_another_cases_pass(case_runner):
    original=next(c for c in CASES if c.id=='mcp-002')
    value=original.model_dump();value['status']='candidate';value['verification']=None
    value['execution']['parameters']['arguments']['topic']='另一个完全不同的主题'
    value['setup']['tool_arguments']=dict(value['execution']['parameters']['arguments'])
    mutated=EvaluationCase.model_validate(value)
    result=await case_runner.run(mutated)
    assert result['status']=='error' and result['checks']['topic'] is False


@pytest.mark.asyncio(loop_scope='module')
async def test_changed_memory_precondition_changes_measured_result(case_runner):
    case=next(c for c in CASES if c.id=='memory-006')
    value=case.model_dump();value.update(status='candidate',verification=None)
    value['execution']['parameters']['user_id']=8800001
    value['execution']['parameters']['version']=99
    result=await case_runner.run(EvaluationCase.model_validate(value))
    assert result['status']=='error'
    assert result['actual']['error']=='MEMORY_CONFLICT'


@pytest.mark.asyncio
async def test_evidence_mutation_is_detected_without_model(settings,tmp_path):
    value=CASES[0].model_dump();value.update(status='candidate',verification=None)
    value['evidence'][0]['quote']='X'*len(value['evidence'][0]['quote'])
    result=await CaseRunner(settings,CORPUS,workdir=tmp_path).run(EvaluationCase.model_validate(value))
    assert result['status']=='error' and not result['checks']['evidence_valid']


@pytest.mark.asyncio
async def test_rag_runs_the_actual_question_through_bm25(settings,tmp_path):
    original=next(c for c in CASES if c.id=='rag-001')
    runner=CaseRunner(settings,CORPUS,workdir=tmp_path)
    before=await runner.run(original)
    value=original.model_dump();value.update(status='candidate',verification=None,
        question='zzqvnomatchingtermevaluation')
    after=await runner.run(EvaluationCase.model_validate(value))
    assert before['observations']['retrieval']['ranked']
    assert after['observations']['retrieval']['ranked']==[]
