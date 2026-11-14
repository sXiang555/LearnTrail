"""Grounded dataset and automatic grading tests; no billable API calls."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from learntrail_agent.evaluation.case_runner import CaseRunner
from learntrail_agent.evaluation.datasets import EvaluationCase, load_dataset
from learntrail_agent.evaluation.framework import live_case, v4_stages
from learntrail_agent.evaluation.framework import evaluation_budgets
from learntrail_agent.evaluation.judge import Judge
from learntrail_agent.evaluation.metrics import category_metrics, retrieval_verdict
from learntrail_agent.rag.schemas import Citation, RagResponse, SearchHit, SearchResponse
from learntrail_agent.schemas import ChatResponse


ROOT = Path(__file__).parents[1]
MANIFEST, CASES, CORPUS = load_dataset(ROOT / 'evaluation/dataset-v4.json')
QA = [c for c in CASES if c.evaluation.kind == 'rag_qa']


def test_full_rag_run_budget_scales_with_cases_not_fixed_eight(settings):
    args=SimpleNamespace(schemes=['hybrid'],repeats=1,max_model_calls=None,max_judge_calls=None)
    limits=evaluation_budgets(QA,args,settings)
    assert limits['model_calls']==65 and limits['judge_calls']==65
    args.schemes=['vector','bm25','hybrid'];args.repeats=2
    limits=evaluation_budgets(QA,args,settings)
    assert limits['model_calls']==390 and limits['judge_calls']==390
    args.max_model_calls=17;args.max_judge_calls=9
    limits=evaluation_budgets(QA,args,settings)
    assert limits['model_calls']==17 and limits['judge_calls']==9


@pytest.mark.parametrize('name,value',[('max_model_calls',0),('max_judge_calls',-1)])
def test_explicit_evaluation_budgets_must_be_positive(settings,name,value):
    args=SimpleNamespace(schemes=['hybrid'],repeats=1,max_model_calls=None,max_judge_calls=None)
    setattr(args,name,value)
    with pytest.raises(ValueError,match='positive'):evaluation_budgets(QA,args,settings)


@pytest.mark.asyncio
async def test_judge_supports_full_run_and_shared_budget_across_clients(settings):
    from pydantic import SecretStr
    from learntrail_agent.settings import Settings
    cfg=Settings(_env_file=None,judge_model='judge-fixture',judge_base_url='https://judge.invalid/v1',
        judge_api_key=SecretStr('fixture'),judge_max_calls=65)
    verdict=json.dumps({'task_success':True,'answer_correctness':1.,'faithfulness':1.,'rationale':'fixture-only'})
    ledger=[]
    for count in [40,25]:
        judge=Judge(Mock(),cfg);judge.calls=ledger
        judge.llm.complete=AsyncMock(return_value=ChatResponse(id='fixture',model='judge-fixture',
            content=verdict,finish_reason='stop'))
        for _ in range(count):assert (await judge.assess('q','r','a',['r']))['status']=='scored'
    assert len(ledger)==65
    judge.llm.complete.reset_mock()
    assert (await judge.assess('q','r','a',['r']))['reason']=='JUDGE_BUDGET_EXCEEDED'
    judge.llm.complete.assert_not_awaited()


def case(identifier: str) -> EvaluationCase:
    return next(c for c in CASES if c.id == identifier)


def write_mutation(tmp_path: Path, identifier: str, mutate) -> Path:
    rows = [c.model_dump() for c in CASES]
    mutate(next(c for c in rows if c['id'] == identifier))
    (tmp_path / 'cases-v4.jsonl').write_text('\n'.join(json.dumps(c) for c in rows))
    (tmp_path / 'corpus-v2.json').write_text(json.dumps(CORPUS))
    path = tmp_path / 'dataset-v4.json'
    path.write_text(MANIFEST.model_dump_json())
    return path


def test_counts_and_grading_scope_are_explicit():
    assert len(CASES) == 150 and len(QA) == 65
    assert sum(len(c.expected.required_facts) for c in QA) == 94
    assert sum(c.status == 'ready' for c in CASES) == 149
    assert case('agent-013').status == 'blocked'
    assert all('human' not in c.evaluators for c in CASES)
    assert all(c.evaluation.search_scope == [s['slug'] for s in CORPUS['sources']] for c in QA)
    old = [json.loads(l) for l in (ROOT / 'evaluation/cases-v3.jsonl').read_text().splitlines()]
    assert {(c.id, c.split, c.collection) for c in CASES} == {
        (c['id'], c['split'], c['collection']) for c in old}


def test_invented_gold_fact_is_rejected_even_with_matching_support_label(tmp_path):
    def mutate(c):
        fact = '钱包余额在失败后也会自动增加999999积分。'
        c['expected']['required_facts'] = [fact]
        c['expected']['reference_answer'] = fact
        c['evidence'][0]['supports'] = [fact]
    with pytest.raises(ValueError, match='literal source support'):
        load_dataset(write_mutation(tmp_path, 'rag-001', mutate))


def test_ungrounded_reference_extra_text_is_rejected(tmp_path):
    def mutate(c):
        c['expected']['reference_answer'] += '\n平台保证所有播放故障都不会发生。'
    with pytest.raises(ValueError, match='grounded fact set'):
        load_dataset(write_mutation(tmp_path, 'rag-002', mutate))


def test_no_answer_case_needs_an_explicit_source_scope(tmp_path):
    def mutate(c):
        c['evaluation']['reference_scope'] = []
    with pytest.raises(ValueError, match='source scopes'):
        load_dataset(write_mutation(tmp_path, 'rag-068', mutate))


def test_unknown_outcome_and_skill_gold_are_not_changed_to_match_failures():
    assert case('hitl-006').execution.assertions['known_effect_count'] is None
    assert case('hitl-006').execution.assertions['retry_count'] == 0
    assert case('skills-010').expected.expected_skill == 'knowledge-learning'
    assert '新的播放授权' in case('rag-038').expected.reference_answer
    assert len(case('rag-053').expected.required_facts) == 5
    assert all(len(e.supports) == 1 for e in case('rag-053').evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize('item', QA, ids=lambda c: c.id)
async def test_each_rag_case_records_actual_ranking_and_unexecuted_stages(settings, tmp_path, item):
    row = await CaseRunner(settings, CORPUS, workdir=tmp_path).run(item)
    assert row['status'] == 'executed'
    assert all(row['checks'].values())
    assert row['question_used'] == item.question
    ranking = row['observations']['retrieval']
    assert ranking['pipeline'] == 'offline_article_bm25' and ranking['unit'] == 'article'
    assert ranking['ranked'] == [h['slug'] for h in ranking['hits']]
    for hit in ranking['hits']:
        assert hit['content'] and hit['content_hash'] and hit['bm25_score'] > 0
        assert hit['end_char'] == len(hit['content'])
    assert row['contract_success'] is None and row['answer'] is None
    assert row['evaluations']['generation']['status'] == 'not_run'
    assert row['evaluations']['semantic']['status'] == 'not_run'
    failed = row['evaluations']['retrieval']['status'] == 'failed'
    assert row['evaluation_status'] == ('failed' if failed else 'partial')


@pytest.mark.asyncio
async def test_missing_relevant_article_is_failure_even_if_gold_is_valid(settings, tmp_path):
    # Replay the earlier missing-source query to keep that real failure as a regression.
    value = case('rag-068').model_dump()
    value['question'] = '把那篇外部文章图里的架构逐个模块解释给我。'
    row = await CaseRunner(settings, CORPUS, workdir=tmp_path).run(EvaluationCase.model_validate(value))
    assert all(row['checks'].values())
    assert row['evaluations']['retrieval']['status'] == 'failed'
    assert 'article-2120326750192082945' in row['evaluations']['retrieval']['missing_relevant']
    assert row['evaluation_status'] == 'failed' and row['contract_success'] is None


def test_zero_relevance_misretrieval_and_forbidden_hit_are_not_success():
    row = {'relevant': [], 'ranked': ['unrelated'], 'forbidden': []}
    assert retrieval_verdict(row)['status'] == 'failed'
    assert retrieval_verdict({**row, 'ranked': []})['status'] == 'passed'
    assert retrieval_verdict({'relevant': ['a'], 'ranked': ['a', 'secret'],
        'forbidden': ['secret']})['status'] == 'failed'


def test_missing_or_negative_judge_result_cannot_become_success():
    item = case('rag-001')
    row = {'status': 'executed', 'answer': '模拟回答', 'model_calls': 1,
        'observations': {'retrieval': {'relevant': ['a'], 'ranked': ['a'], 'forbidden': []}}}
    pending = v4_stages(item, dict(row))
    assert pending['evaluation_status'] == 'partial'
    assert not pending['model_quality_evaluated']
    failed = v4_stages(item, {**row, 'observations': {**row['observations'],
        'judge': {'status': 'scored', 'task_success': False, 'rationale': '模拟错误答案'}}})
    assert failed['evaluation_status'] == 'failed'
    summary = category_metrics('rag', [pending, failed])
    assert summary['stage_coverage']['semantic_scored'] == 1
    assert summary['contract_success_rate']['observations'] == 0


def test_positive_judge_does_not_override_failed_tool_trajectory():
    row = {'status': 'executed', 'scope': 'real_java_postgres_models', 'answer': '模拟答案',
        'model_calls': 2, 'observations': {'task_success': False,
            'judge': {'status': 'scored', 'task_success': True, 'rationale': 'fixture-only'}}}
    result = v4_stages(case('agent-004'), row)
    assert result['evaluation_status'] == 'failed'
    assert result['model_quality_evaluated']
    assert result['evaluations']['semantic']['result']['task_success']


@pytest.mark.asyncio
async def test_live_adapter_preserves_chunks_scores_answers_and_judge(settings):
    # Mock only the external execution; exercise the real report adapter/serialization.
    item = case('rag-001');source = CORPUS['sources'][0]
    hit = SearchHit(article_id=int(source['id']), slug=source['slug'], generation=uuid4(), ordinal=0,
        title=source['title'], section='test', start_char=0, end_char=20, content=source['text'][:20],
        content_hash=source['content_hash'], source_type='original', score=.8,
        vector_score=.9, bm25_score=2.0, rrf_score=.03, reranker_score=.8)
    search = SearchResponse(request_id=uuid4(), mode='hybrid', vector=[hit], bm25=[hit], fused=[hit],
        results=[hit], reranked=True, embedding_usage=None, duration_ms=3)
    citation = Citation(id='S1', article_id=str(hit.article_id), slug=hit.slug, title=hit.title,
        section=hit.section, ordinal=0, generation=hit.generation, content_hash=hit.content_hash,
        start_char=0, end_char=20, quote=hit.content, url='/articles/test', source_type='original')
    budget = [0, 2]
    async def answer(*args):
        budget[0] += 1
        return RagResponse(request_id=uuid4(), session_id=uuid4(), status='answered', answer='模拟答案[S1]',
            citations=[citation], retrieval=search, model='fixture-only', usage=None, duration_ms=4)
    state = SimpleNamespace(settings=settings, retriever=SimpleNamespace(search=AsyncMock(return_value=search)),
        rag_service=SimpleNamespace(answer=answer), sessions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=uuid4()))),
        telemetry=SimpleNamespace(pricing=SimpleNamespace(estimate=Mock(return_value={'status': 'unknown'})), score=Mock()))
    judge = SimpleNamespace(assess=AsyncMock(return_value={'status': 'scored', 'task_success': True,
        'answer_correctness': 1.0, 'faithfulness': 1.0, 'rationale': 'fixture-only'}))
    row = await live_case(item, state, SimpleNamespace(user_id=41), 'fixture-token', judge, budget, 'hybrid', CORPUS)
    row = v4_stages(item, row)
    assert row['answer'] == '模拟答案[S1]' and row['model_calls'] == 1
    for field in ['retrieval', 'answer_retrieval']:
        for stage in ['vector', 'bm25', 'fused', 'results']:
            saved = row[field][stage][0]
            assert saved['content'] == hit.content and saved['content_hash'] == hit.content_hash
            assert saved['reranker_score'] == .8 and saved['rrf_score'] == .03
    assert row['evaluations']['generation']['status'] == 'completed'
    assert row['evaluations']['semantic']['status'] == 'scored'
    assert row['evaluations']['semantic']['result']['rationale'] == 'fixture-only'
