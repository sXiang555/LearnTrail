"""Versioned evaluation data contracts shared by the existing runner."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal['rag', 'agent', 'memory', 'skills', 'mcp', 'hitl']
Split = Literal['development', 'validation', 'test']


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(Record):
    article_id: str
    slug: str
    content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    quote: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    position_basis: Literal['normalized_unicode_codepoints'] = 'normalized_unicode_codepoints'
    supports: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def extent(self):
        if self.end_char-self.start_char != len(self.quote):
            raise ValueError('Evidence length does not match offsets')
        return self


class Source(Record):
    kind: Literal['article', 'code_contract', 'legacy_case']
    references: list[str] = Field(min_length=1)
    legacy_id: str | None = None


class Expectation(Record):
    outcome: str = Field(min_length=2)
    answer_available: bool | None = None
    reference_answer: str = Field(min_length=2)
    required_facts: list[str] = Field(default_factory=list)
    relevant: dict[str, int] = Field(default_factory=dict)
    forbidden: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    arguments: dict[str, dict] = Field(default_factory=dict)
    failure_conditions: list[str] = Field(min_length=1)
    citation_required: bool = False
    expected_skill: str | None = None
    tool_paths: list[list[str]] = Field(default_factory=list)
    allow_recovery: bool = False
    recoverable_errors: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistency(self):
        if any(type(v) is not int or not 0 <= v <= 3 for v in self.relevant.values()):
            raise ValueError('Relevance grades must be integers 0..3')
        if set(self.required_tools)-set(self.allowed_tools):
            raise ValueError('Required tools must also be allowed')
        if set(self.arguments)-set(self.allowed_tools):
            raise ValueError('Arguments reference an unauthorized tool')
        if any(not path or set(path)-set(self.allowed_tools) for path in self.tool_paths):
            raise ValueError('Alternative tool paths must use allowed tools')
        if set(self.forbidden) & {k for k,v in self.relevant.items() if v>0}:
            raise ValueError('Forbidden document cannot be relevant')
        return self


class ExecutionPlan(Record):
    kind: Literal['rag_evidence','graph','memory','skills','mcp','hitl']
    operation: str = Field(pattern=r'^[a-z][a-z0-9_]{1,60}$')
    parameters: dict = Field(default_factory=dict)
    assertions: dict = Field(min_length=1)


class Verification(Record):
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    scope: str
    report: str
    checks: dict[str, bool] = Field(min_length=1)


class EvaluationSpec(Record):
    kind: Literal['rag_qa','rag_retrieval','functional_contract']
    gold_kind: Literal['source_quotes','scope_absence','code_contract']
    reference_scope: list[str] = Field(default_factory=list)
    search_scope: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=2)
    judge_required: bool


class EvaluationCase(Record):
    id: str = Field(pattern=r'^(rag|agent|memory|skills|mcp|hitl)-[0-9]{3}$')
    category: Category
    collection: Literal['capability', 'regression']
    split: Split
    difficulty: Literal['easy', 'medium', 'hard']
    tags: list[str] = Field(min_length=1)
    groups: list[str] = Field(min_length=1)
    status: Literal['draft', 'verified', 'candidate', 'needs_revision', 'blocked', 'ready']
    review_note: str = Field(min_length=2)
    source: Source
    question: str = Field(min_length=2, max_length=20000)
    history: list[str] = Field(default_factory=list)
    setup: dict = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    expected: Expectation
    evaluators: list[Literal['deterministic', 'judge', 'human']] = Field(min_length=1)
    # Explicit existing pytest node IDs, never commands supplied by the model.
    contract_tests: list[str] = Field(default_factory=list)
    execution: ExecutionPlan | None = None
    verification: Verification | None = None
    blocking_reason: str | None = None
    evaluation: EvaluationSpec | None = None

    @model_validator(mode='after')
    def valid_case(self):
        if self.execution:
            aliases={'initial':'initial','proposal':'proposal','value':'value','plan':'plan',
                'transport':'transport','allow_tools':'allow_tools','tool_arguments':'arguments'}
            for key,target in aliases.items():
                if key in self.setup and self.setup[key]!=self.execution.parameters.get(target):
                    raise ValueError('Setup and execution disagree: '+key)
        if self.setup.get('memory'):
            from learntrail_agent.agent.advanced_models import MemoryCreate
            MemoryCreate.model_validate(self.setup['memory'])
        if not self.id.startswith(self.category+'-'):
            raise ValueError('ID and primary category disagree')
        if self.category=='rag' and self.expected.answer_available and not self.evidence:
            raise ValueError('Answerable RAG case requires source evidence')
        if self.status=='verified' and self.execution is not None:
            if not self.verification or not all(self.verification.checks.values()):
                raise ValueError('Verified execution requires successful checks')
            if self.verification.input_sha256!=case_fingerprint(self):
                raise ValueError('Verification is stale after case modification')
        elif self.status=='verified' and self.source.kind!='legacy_case':
            if not self.review_note.startswith('human-reviewed:'):
                raise ValueError('Verified cases require a recorded human review')
        for node in self.contract_tests:
            path=node.split('::')[0]
            if not path.startswith('tests/test_') or not path.endswith('.py') or '..' in path or any(c.isspace() for c in node):
                raise ValueError('Contract references must be local pytest nodes')
        return self


class DatasetManifest(Record):
    schema_version: Literal[2,3,4,5]
    version: str = Field(pattern=r'^learntrail-eval-[0-9]+\.[0-9]+\.[0-9]+$')
    cases_file: str
    corpus_file: str
    categories: dict[str, int]
    splits: dict[str, int]
    changelog: list[str]
    legacy_dataset: str


def case_fingerprint(case: EvaluationCase) -> str:
    data=case.model_dump(exclude={'status','review_note','verification','blocking_reason'})
    # Preserve fingerprints of immutable v2/v3 records after adding optional v4 fields.
    if data.get('evaluation') is None:data.pop('evaluation',None)
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load_dataset(path: Path) -> tuple[DatasetManifest, list[EvaluationCase], dict]:
    manifest=DatasetManifest.model_validate_json(path.read_text())
    root=path.parent.resolve()
    def local(name):
        p=(root/name).resolve()
        if not p.is_relative_to(root):raise ValueError('Dataset files must remain in their directory')
        return p
    cases=[EvaluationCase.model_validate_json(line) for line in local(manifest.cases_file).read_text().splitlines() if line.strip()]
    corpus=json.loads(local(manifest.corpus_file).read_text())
    if len({c.id for c in cases})!=len(cases):raise ValueError('Duplicate case ID')
    if len({c.question for c in cases})!=len(cases):raise ValueError('Duplicate question')
    if dict(Counter(c.category for c in cases))!=manifest.categories:raise ValueError('Category counts do not match manifest')
    if dict(Counter(c.split for c in cases))!=manifest.splits:raise ValueError('Split counts do not match manifest')
    grouped={}
    executions={}
    scenario_splits={}
    articles={row['slug']:row for row in corpus['sources']}
    for case in cases:
        if manifest.schema_version in {3,4,5}:
            if case.status=='draft' or case.execution is None or case.contract_tests:
                raise ValueError('v3 requires execution plans, v3 status and no fixed test aliases')
            if case.status=='blocked' and not case.blocking_reason:
                raise ValueError('Blocked case requires a reason')
            # Detect duplicate questions with identical executable data.
            execution=case.execution.model_dump()
            execution['parameters']={k:v for k,v in execution['parameters'].items() if k not in {'user_id','final_response'}}
            signature=json.dumps({'question':case.question,'execution':execution,'setup':case.setup,'history':case.history},sort_keys=True,ensure_ascii=False)
            if signature in executions:raise ValueError('Duplicate executable scenario: '+case.id)
            executions[signature]=case.id
            if case.execution.kind in {'memory','mcp','hitl'}:
                # Protocol/state fixtures cannot become independent split samples
                # through a paraphrase, different owner ID or different gold labels.
                scenario=json.dumps({'kind':case.execution.kind,'operation':case.execution.operation,
                    'parameters':execution['parameters'],'history':case.history},sort_keys=True,ensure_ascii=False)
                if scenario in scenario_splits and scenario_splits[scenario]!=case.split:
                    raise ValueError('Executable fixture split leakage: '+case.id)
                scenario_splits[scenario]=case.split
            if case.category=='rag' and case.expected.answer_available:
                supported={fact for e in case.evidence for fact in e.supports}
                if not set(case.expected.required_facts)<=supported:raise ValueError('Missing fact evidence: '+case.id)
        keys=set(case.groups)|{'article:'+slug for slug in list(case.expected.relevant)+case.expected.forbidden}
        if manifest.schema_version in {3,4,5}:
            for slug in [case.execution.parameters.get('knowledge_slug'),*case.source.references]:
                if slug in articles:keys.add('article:'+slug)
        for ev in case.evidence:
            keys.add('article:'+ev.slug)
            source=articles.get(ev.slug)
            if not source or source['id']!=ev.article_id or source['content_hash']!=ev.content_hash:
                raise ValueError('Evidence source identity/hash mismatch: '+case.id)
            if source['text'][ev.start_char:ev.end_char]!=ev.quote:
                raise ValueError('Evidence quote mismatch: '+case.id)
        for key in keys:
            if key in grouped and grouped[key]!=case.split:raise ValueError('Split leakage: '+key)
            grouped[key]=case.split
        if manifest.schema_version in {4,5}:
            spec=case.evaluation
            if spec is None or case.status not in {'ready','blocked'} or 'human' in case.evaluators:
                raise ValueError('v4 requires automatic grading and separate runtime results: '+case.id)
            if set(spec.reference_scope+spec.search_scope)-articles.keys():
                raise ValueError('Unknown evaluation source: '+case.id)
            if spec.kind in {'rag_qa','rag_retrieval'}:
                if not spec.reference_scope or not spec.search_scope or spec.judge_required!=(spec.kind=='rag_qa'):
                    raise ValueError('RAG requires source scopes and matching grading mode: '+case.id)
                if case.expected.answer_available:
                    if spec.gold_kind!='source_quotes' or not case.expected.required_facts:
                        raise ValueError('Answerable v4 case requires extractive facts: '+case.id)
                    if case.expected.reference_answer!='\n'.join(case.expected.required_facts):
                        raise ValueError('Reference answer must be the grounded fact set: '+case.id)
                    for fact in case.expected.required_facts:
                        if not any(fact in e.quote and fact in e.supports and e.slug in spec.reference_scope for e in case.evidence):
                            raise ValueError('Fact has no literal source support: '+case.id)
                elif spec.gold_kind!='scope_absence' or case.expected.required_facts:
                    raise ValueError('No-answer cases require scope-based absence, not unsupported facts: '+case.id)
            elif spec.gold_kind!='code_contract' or spec.judge_required:
                raise ValueError('Functional fixtures must not pretend to be semantic evaluation: '+case.id)
            if manifest.schema_version==5 and (case.category!='rag' or spec.kind!='rag_retrieval'
                    or case.evaluators!=['deterministic'] or case.execution.kind!='rag_evidence'
                    or case.execution.operation!='evidence' or case.history):
                raise ValueError('v5 is retrieval-only: '+case.id)
    for source in articles.values():
        digest=hashlib.sha256((source['title']+'\n\n'+source['text']).encode()).hexdigest()
        if digest!=source['content_hash']:raise ValueError('Corpus fingerprint mismatch')
    return manifest,cases,corpus


def select_cases(cases, *, split='development', categories=None, collection=None, ids=None):
    return [c for c in cases if (split=='all' or c.split==split) and
        (not categories or c.category in categories) and (not collection or c.collection==collection) and
        (not ids or c.id in ids)]
