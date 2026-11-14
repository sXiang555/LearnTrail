"""150 retrieval cases and the production retrieval adapter; no paid calls."""

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from learntrail_agent.evaluation.case_runner import CaseRunner
from learntrail_agent.evaluation.datasets import load_dataset
from learntrail_agent.evaluation.framework import evaluation_budgets, live_case, v4_stages
from learntrail_agent.rag.schemas import SearchResponse

ROOT = Path(__file__).parents[1]
MANIFEST, CASES, CORPUS = load_dataset(ROOT / 'evaluation/dataset-v5.json')


def test_all_150_are_retrieval_and_old_65_inputs_are_preserved():
    assert len(CASES) == 150 and len(CORPUS['sources']) == 19
    assert [c.id for c in CASES] == [f'rag-{i:03}' for i in range(1, 151)]
    assert Counter(c.split for c in CASES) == {'development': 90, 'validation': 30, 'test': 30}
    assert all(c.category == 'rag' and c.evaluation.kind == 'rag_retrieval'
        and c.evaluators == ['deterministic'] and not c.evaluation.judge_required for c in CASES)
    old = [json.loads(l) for l in (ROOT / 'evaluation/cases-v4.jsonl').read_text().splitlines()]
    expected = {c['id']: c for c in old if c['evaluation']['kind'] == 'rag_qa'}
    retained = [c for c in CASES if c.source.legacy_id]
    assert len(retained) == 65 and {c.source.legacy_id for c in retained} == set(expected)
    for c in retained:
        previous = expected[c.source.legacy_id]
        assert c.question == previous['question']
        assert c.expected.relevant == previous['expected']['relevant']
    old_sources = json.loads((ROOT / 'evaluation/corpus-v2.json').read_text())['sources']
    current = {s['slug']: s for s in CORPUS['sources']}
    assert all(current[s['slug']] == s for s in old_sources)


def test_v5_never_allocates_generation_or_judge_budget(settings):
    args = SimpleNamespace(schemes=None, repeats=1, max_model_calls=999, max_judge_calls=999)
    limits = evaluation_budgets(CASES, args, settings)
    assert limits['model_calls'] == limits['judge_calls'] == 0
    assert limits['scheme_count'] == 3 and limits['selected_cases'] == 150


def test_v5_rejects_nonretrieval_scenarios(tmp_path):
    rows = [c.model_dump() for c in CASES]
    rows[0]['evaluation']['kind'] = 'functional_contract'
    rows[0]['evaluation']['gold_kind'] = 'code_contract'
    (tmp_path / 'cases-v5.jsonl').write_text('\n'.join(json.dumps(c) for c in rows))
    (tmp_path / 'corpus-v5.json').write_text(json.dumps(CORPUS))
    path = tmp_path / 'dataset-v5.json'
    path.write_text(MANIFEST.model_dump_json())
    with pytest.raises(ValueError, match='retrieval-only'):
        load_dataset(path)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda c: c.id)
async def test_every_case_has_valid_independently_located_source_facts(settings, tmp_path, case):
    row = await CaseRunner(settings, CORPUS, workdir=tmp_path).run(case)
    assert row['status'] == 'executed' and all(row['checks'].values())
    assert row['question_used'] == case.question and row['model_calls'] == 0
    assert row['answer'] is None
    assert row['evaluations']['generation']['status'] == 'not_run'
    assert row['evaluations']['semantic']['status'] == 'not_run'


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy', ['vector', 'bm25', 'hybrid'])
async def test_live_retrieval_cannot_call_generation_or_judge(strategy):
    result = SearchResponse(request_id=uuid4(), mode=strategy, vector=[], bm25=[], fused=[],
        results=[], reranked=False, duration_ms=1)
    llm = SimpleNamespace(complete=AsyncMock(side_effect=AssertionError('Generation forbidden')))
    judge = SimpleNamespace(assess=AsyncMock(side_effect=AssertionError('Judge forbidden')))
    state = SimpleNamespace(retriever=SimpleNamespace(search=AsyncMock(return_value=result)), llm=llm)
    case = CASES[0]
    row = await live_case(case, state, SimpleNamespace(user_id=41), 'fixture', judge,
        [0, 0], strategy, CORPUS)
    row = v4_stages(case, row)
    request = state.retriever.search.await_args.args[1]
    assert request.query == case.question and request.mode == strategy
    assert request.article_slugs == case.evaluation.search_scope and request.top_k == 5
    assert not request.rerank
    assert row['scope'] == 'real_java_postgres_retrieval'
    assert row['model_calls'] == row['judge_calls'] == 0
    assert row['evaluations']['retrieval']['status'] == 'failed'
    assert row['evaluations']['semantic']['status'] == 'not_run'
    llm.complete.assert_not_awaited()
    judge.assess.assert_not_awaited()
