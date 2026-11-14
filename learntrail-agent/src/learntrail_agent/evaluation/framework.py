"""Dataset adapters for the existing evaluation CLI, metrics and experiment storage."""

import asyncio
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4

from opentelemetry import trace

from learntrail_agent.agent.skills import SkillCatalog
from learntrail_agent.errors import AppError
from learntrail_agent.evaluation.datasets import load_dataset,select_cases
from learntrail_agent.evaluation.judge import Judge
from learntrail_agent.evaluation.metrics import category_metrics,repeat_summary,paired_comparisons,retrieval_verdict
from learntrail_agent.main import create_app
from learntrail_agent.rag.bm25 import term_counts,tokenize,bm25_score
from learntrail_agent.security.auth import authenticate_token
from learntrail_agent.settings import PROJECT_ROOT
from learntrail_agent.observability import Telemetry


def dataset_summary(manifest,cases):
    return {'version':manifest.version,'cases':len(cases),'categories':dict(Counter(c.category for c in cases)),
        'splits':dict(Counter(c.split for c in cases)),'review':dict(Counter(c.status for c in cases)),
        'collections':dict(Counter(c.collection for c in cases))}


def offline_bm25(case,corpus,settings):
    """Actual BM25 over frozen whole articles; separate from production chunk retrieval."""
    denied=set(case.setup.get('denied_slugs',[]))
    allowed=set(case.evaluation.search_scope) if case.evaluation and case.evaluation.kind=='rag_qa' else None
    documents=[s for s in corpus['sources'] if s['slug'] not in denied and (allowed is None or s['slug'] in allowed)]
    terms={s['slug']:term_counts(s['text']) for s in documents};df=Counter()
    for counts in terms.values():df.update(counts.keys())
    avg=sum(sum(t.values()) for t in terms.values())/len(terms) if terms else 0
    scored=[(slug,bm25_score(t,tokenize(case.question),df,len(terms),avg,settings.rag_bm25_k1,settings.rag_bm25_b)) for slug,t in terms.items()]
    selected=[(slug,score) for slug,score in sorted(scored,key=lambda v:(-v[1],v[0])) if score>settings.rag_bm25_min_score][:5]
    sources={s['slug']:s for s in documents}
    hits=[{'slug':slug,'article_id':sources[slug]['id'],'title':sources[slug]['title'],
        'content':sources[slug]['text'],'content_hash':sources[slug]['content_hash'],
        'start_char':0,'end_char':len(sources[slug]['text']),'bm25_score':score} for slug,score in selected]
    return {'retrieval':{'relevant':[s for s,g in case.expected.relevant.items() if g>0],
        'grades':case.expected.relevant,'forbidden':case.expected.forbidden,'ranked':[s for s,_ in selected],
        'pipeline':'offline_article_bm25','unit':'article','top_k':5,'hits':hits}}


async def contract_result(nodes,folder,cache,settings):
    """Run referenced existing assertions once; skips are unverified, never passed."""
    for node in nodes:
        if node in cache:continue
        started=time.monotonic()
        if not (PROJECT_ROOT/node.split('::')[0]).is_file():
            cache[node]={'status':'unverified','reason':'CONTRACT_NOT_IMPLEMENTED'};continue
        name='contract-'+hashlib.sha256(node.encode()).hexdigest()[:16]
        xml=folder/(name+'.xml');log=folder/(name+'.log')
        env=os.environ.copy()
        if settings.database_url.get_secret_value():env['LEARNTRAIL_TEST_DATABASE_URL']=settings.database_url.get_secret_value()
        fd=os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as output:
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','pytest',node,'-q','--tb=no','--junitxml='+str(xml),
                cwd=PROJECT_ROOT,env=env,stdout=output,stderr=asyncio.subprocess.STDOUT)
            try:code=await asyncio.wait_for(process.wait(),120)
            except TimeoutError:
                process.kill();await process.wait();cache[node]={'status':'error','reason':'CONTRACT_TIMEOUT'};continue
        if xml.exists():
            xml.chmod(0o600);tree=ET.parse(xml);tests=tree.findall('.//testcase')
            failed=sum(t.find('failure') is not None or t.find('error') is not None for t in tests)
            skipped=sum(t.find('skipped') is not None for t in tests)
            cache[node]={'status':'executed' if code==0 and tests and not skipped else ('unverified' if skipped else 'error'),
                'checks':len(tests),'failed':failed,'skipped':skipped,'reason':'CONTRACT_SKIPPED' if skipped else None}
        else:cache[node]={'status':'error','reason':'CONTRACT_COLLECTION_FAILED'}
        cache[node]['duration_ms']=round((time.monotonic()-started)*1000,2)
    results={n:cache[n] for n in nodes}
    return results,'error' if any(v['status']=='error' for v in results.values()) else (
        'unverified' if any(v['status']=='unverified' for v in results.values()) else 'executed')


async def deterministic_case(case,corpus,settings,folder,cache,scheme,state=None):
    if case.execution is not None:
        from learntrail_agent.evaluation.case_runner import CaseRunner
        if scheme!='auto':return {'status':'unverified','reason':'V3_CONTRACT_SCHEME_IS_AUTO','observations':{}}
        runner=CaseRunner(settings,corpus,state,folder)
        return await runner.run(case)
    supported={'rag':{'auto','bm25'},'agent':{'auto','agent'},'memory':{'auto','short_memory','full_memory'},
        'skills':{'auto','skills','no_skills'},'mcp':{'auto','mcp_stdio','mcp_http'},'hitl':{'auto'}}
    if scheme not in supported[case.category]:return {'status':'unverified','reason':'SCHEME_NOT_AVAILABLE_IN_DETERMINISTIC_MODE','observations':{}}
    if case.category=='mcp' and scheme!='auto' and case.setup.get('transport')!=scheme.removeprefix('mcp_'):
        return {'status':'unverified','reason':'DIFFERENT_TRANSPORT_CASE','observations':{}}
    if case.category=='rag' and not case.contract_tests and scheme in {'auto','bm25'}:
        return {'status':'executed','scope':'offline_article_bm25','observations':offline_bm25(case,corpus,settings)}
    if case.category=='skills' and scheme in {'auto','skills','no_skills'}:
        catalog=SkillCatalog(settings.skills_dir)
        requested=case.setup.get('requested_skill')
        selected=catalog.choose(case.question,requested) if scheme!='no_skills' else None
        return {'status':'executed','scope':'skill_selection_only',
            'observations':{'expected_skill':case.expected.expected_skill,'selected_skill':selected.metadata.name if selected else None}}
    if not case.contract_tests:
        return {'status':'unverified','reason':'LIVE_MODEL_REQUIRED','scope':'not_executed','observations':{}}
    results,status=await contract_result(case.contract_tests,folder,cache,settings)
    observations={};ok=status=='executed'
    # Only assertions represented by the referenced executable contract are scored.
    if case.category=='mcp' and ok:
        key={'discovery':'discovery_success_rate','schema':'schema_conformance','invocation':'invocation_success_rate',
            'error_enforcement':'invocation_error_enforcement','recovery':'transport_recovery_rate'}[case.setup['protocol_check']]
        observations[key]=1.0
    if case.category=='memory' and ok:
        if case.id in {'memory-006','memory-007','memory-008'}:observations['update_accuracy']=1.0
        if case.id in {'memory-013','memory-014'}:observations['deletion_accuracy']=1.0
        if case.id=='memory-015':observations['cross_user_isolation']=1.0
    if case.category=='hitl' and ok:
        action=case.setup['approval_action']
        if action in {'approve','reject','expire','tamper','cancel'}:observations['approval_decision_accuracy']=1.0
        if action in {'approve','resume'}:observations['resume_success_rate']=1.0
        if action in {'reject','expire','cancel'}:observations['rejection_enforcement']=1.0
        if action=='duplicate':observations['duplicate_execution_count']=0
        if case.id=='hitl-010':observations['unauthorized_execution_count']=0
    return {'status':status,'scope':'referenced_contract','contract_results':results,
        'contract_success':ok if status!='unverified' else None,'observations':observations}


def case_legacy(case):
    result={'id':case.id,'question':case.question,'reference':case.expected.reference_answer,
        'expected':{'required_tools':case.expected.required_tools,'allowed_tools':case.expected.allowed_tools,
            'arguments':case.expected.arguments,'status':'succeeded','tool_paths':case.expected.tool_paths,
            'allow_recovery':case.expected.allow_recovery,'recoverable_errors':case.expected.recoverable_errors}}
    if case.execution:
        p=case.execution.parameters
        if p.get('requested_skill'):result['skill']=p['requested_skill']
        if case.category=='memory' and p.get('initial'):result['setup_memory']=p['initial']
    if case.setup.get('skill_mode')=='explicit':result['skill']=case.setup.get('requested_skill')
    if case.expected.expected_skill:result['expected_skill']=case.expected.expected_skill
    if case.setup.get('memory'):result['setup_memory']=case.setup['memory']
    result['expected_memory_retrieval']=case.setup.get('expected_retrieval',True)
    result['expire_memory_before_query']=case.setup.get('expire_memory_before_query',False)
    result['history']=case.history
    return result


async def live_case(case,state,user,token,judge,budget,scheme,corpus):
    from learntrail_agent.evaluation.runner import rag_suite,agent_suite
    if case.status=='blocked':return {'status':'unverified','reason':case.blocking_reason,'observations':{}}
    if case.execution:
        plan=case.execution
        supported=(plan.kind=='rag_evidence' and plan.operation=='evidence') or (
            plan.kind=='graph' and plan.operation=='workflow' and not plan.parameters.get('tool_failure')) or (
            plan.kind=='memory' and plan.operation=='retrieve') or (plan.kind=='skills' and plan.operation=='select')
        if not supported:return {'status':'unverified','reason':'CASE_REQUIRES_DETERMINISTIC_SCENARIO','observations':{}}
    semantic_task=(case.category=='memory' and case.setup.get('memory')) or (
        case.category=='skills' and 'topic:skill-authorization' not in case.groups)
    if case.contract_tests and not semantic_task:
        return {'status':'unverified','reason':'USE_DETERMINISTIC_CONTRACT_MODE','observations':{}}
    if case.category=='rag':
        strategy='hybrid' if scheme=='auto' else scheme
        if strategy not in {'vector','bm25','hybrid','hybrid_reranker'}:
            return {'status':'unverified','reason':'SCHEME_NOT_AVAILABLE','observations':{}}
        if strategy=='hybrid_reranker' and not state.settings.reranker_configured:
            return {'status':'unverified','reason':'RERANKER_NOT_CONFIGURED','observations':{}}
        reference=case.expected.reference_answer
        if case.evaluation:reference+='\n评判范围与要求：'+case.evaluation.rationale
        data={'sources':[s for s in corpus['sources'] if not case.evaluation or s['slug'] in case.evaluation.search_scope],'cases':[{'id':case.id,'query':case.question,
            'relevant':[s for s,g in case.expected.relevant.items() if g>0],'grades':case.expected.relevant,
            'forbidden':case.expected.forbidden,'answer_available':case.expected.answer_available,'evidence':reference}]}
        sources={s['slug']:s for s in corpus['sources']}
        calls_before=budget[0]
        result=await rag_suite(state,user,token,data,sources,judge,True,False,strategies=[strategy],answer_strategy=strategy)
        strategy_row=result['strategies'][strategy]['cases'][0];answer=result['answers'][0]
        observations={'retrieval':strategy_row,'judge':answer.get('judge',{}),
            'abstention_correct':(answer['status']=='answered')==case.expected.answer_available if answer['status'] in {'answered','insufficient_context'} else False}
        if observations['judge'].get('status')=='scored':observations['task_success']=observations['judge']['task_success']
        return {'status':'error' if answer.get('error') or strategy_row.get('error') else 'executed',
            'scope':'real_java_postgres_models','observations':observations,'answer':answer.get('answer'),
            'retrieval':strategy_row.get('retrieval'),'answer_retrieval':answer.get('retrieval'),
            'model_calls':budget[0]-calls_before,
            'citations':answer.get('citation_details'),'usage':answer.get('usage'),
            'usage_complete':bool(answer.get('usage')),'cost':answer.get('cost'),'error':answer.get('error')}
    if case.category in {'agent','memory','skills'} and scheme in {'auto','agent','short_memory','full_memory','skills','no_skills'}:
        result=await agent_suite(state,user,token,[case_legacy(case)],scheme,judge,budget)
        row=result['cases'][0];observations=dict(row.get('tool_quality',{}));judged=row.get('judge',{})
        observations['judge']=judged
        if case.category=='skills':
            observations['expected_skill']=case.expected.expected_skill
            observations['selected_skill']=row.get('selected_skill')
        if row.get('memory_observations'):observations.update(row['memory_observations'])
        if judged.get('status')=='scored':
            observations['task_success']=judged['task_success'] and row.get('metrics',{}).get('trajectory_success',False)
        return {'status':'error' if row.get('error') else ('unverified' if row.get('verification')=='unverified' else 'executed'),
            'scope':'real_java_postgres_models','observations':observations,'usage':row.get('usage'),
            'usage_complete':row.get('usage_complete',False),'cost':row.get('cost'),'steps':row.get('graph_model_steps'),
            'tool_calls':row.get('tool_calls'),'model_calls':row.get('model_calls'),'answer':row.get('answer'),
            'error':row.get('error'),'reason':row.get('reason')}
    return {'status':'unverified','reason':'SCHEME_NOT_IMPLEMENTED','observations':{}}


def v4_stages(case,row):
    """Attach measured stages without upgrading data readiness to task success."""
    spec=case.evaluation
    if spec is None:return row
    row['evaluation_kind']=spec.kind
    if spec.kind!='rag_qa' and row.get('scope')!='real_java_postgres_models':
        row['evaluation_status']='passed' if row.get('contract_success') is True else (
            'failed' if row.get('status')=='error' else 'not_run')
        row['model_quality_evaluated']=False
        return row
    if 'evaluations' in row:return row
    observations=row.get('observations',{});retrieval=observations.get('retrieval')
    judge=observations.get('judge',{});scored=judge.get('status')=='scored'
    row['evaluations']={'retrieval':retrieval_verdict(retrieval) if retrieval else {'status':'not_run'},
        'generation':{'status':'completed' if row.get('answer') and row.get('model_calls',0)>0 else (
            'refusal_without_generation' if row.get('answer') else 'not_run')},
        'semantic':{'status':judge.get('status','not_run'),'result':judge or None}}
    failed=(row['evaluations']['retrieval']['status']=='failed' or (scored and not judge.get('task_success'))
        or observations.get('abstention_correct') is False or observations.get('task_success') is False)
    row['evaluation_status']='failed' if failed else ('passed' if scored and row.get('answer') else 'partial')
    row['model_quality_evaluated']=scored
    return row


def report_markdown(report):
    lines=['# LearnTrail AI Evaluation', '',f"版本：{report['dataset']['version']}；集合：{report['split']}；模式：{report['mode']}",
        '', '|类别 / 方案|案例执行数|错误|任务成功率|重复成功率标准差|','|---|---:|---:|---:|---:|']
    for key,summary in report['summaries'].items():
        lines.append(f"|{key}|{summary['cases']}|{summary['errors']}|{summary['metrics']['task_success_rate']['value']}|{summary['repeats']['population_stddev']}|")
    if report.get('execution_coverage'):
        lines+=['','执行覆盖（数据就绪不等于系统通过）：','```json',json.dumps(report['execution_coverage'],ensure_ascii=False,indent=2),'```']
    for key,summary in report['summaries'].items():
        lines+=['',f'## {key} 指标','','|指标|值|观测数|','|---|---:|---:|']
        for name,metric in summary['metrics'].items():
            lines.append(f"|{name}|{metric['value']}|{metric['observations']}|")
        if summary.get('retrieval'):
            r=summary['retrieval']
            for name in ['recall','mrr','precision','graded_ndcg','negative_nonempty_rate','forbidden_hits']:
                lines.append(f"|{name}@5|{r[name]}|{r['positive_cases'] if name not in {'negative_nonempty_rate','forbidden_hits'} else r['negative_cases']}|")
        lines+=['', '工程指标：`'+json.dumps(summary['engineering'],ensure_ascii=False)+'`',
            '', '确定性检查通过率：`'+json.dumps(summary['contract_success_rate'],ensure_ascii=False)+'`']
    lines+=['','## 配对对比', '```json',json.dumps(report.get('paired_comparisons',[]),ensure_ascii=False,indent=2),'```']
    lines+=['','## 逐题结果与诊断','','详见 report.json 与逐题 cases/*.json；按类别列出检索、工具、Memory、Skills、MCP、HITL 和工程指标。',
        'v3 按每题 execution 运行自己的问题、前置数据、参数和断言；逐题 scope 区分证据核对、受控模型 Graph、真实 SQL 与本地 MCP。',
        'RAG 确定性模式运行文章快照级 BM25 并校验证据；受控模型输出不计为真实模型的回答质量。旧版 referenced_contract 仅保留用于复现历史报告。',
        '缺少观测的指标为空；执行结果不自动改变数据集审核状态。Test 需显式选择。', '', '## 失败与待验证']
    for row in report['results']:
        if row.get('status') in {'error','unverified'} or row.get('evaluation_status')=='failed' or row.get('observations',{}).get('task_success') is False:
            missing=row.get('evaluations',{}).get('retrieval',{}).get('missing_relevant',[])
            lines.append(f"- {row['id']} / {row['scheme']}: {row.get('error') or row.get('reason') or 'EVALUATION_FAILED'}; missing_relevant={missing}")
    lines+=['','## 方案结论','仅在相同集合、案例、模型和评价范围内做配对比较；本报告不将离线检索、契约通过与语义成功混合排名。']
    return '\n'.join(lines)+'\n'


def evaluation_budgets(chosen,args,settings):
    """Limits scale with an explicitly selected experiment, independently of concurrency."""
    variants=len(args.schemes or ['auto'])*args.repeats
    active=[case for case in chosen if case.status!='blocked']
    model_default=variants*sum(1 if case.category=='rag' else settings.agent_max_rounds for case in active)
    judge_default=variants*len(active)
    def limit(name,default):
        explicit=getattr(args,name,None)
        if explicit is not None and (type(explicit) is not int or explicit<1):
            raise ValueError(name+' must be a positive integer')
        return explicit if explicit is not None else max(1,default)
    return {'model_calls':limit('max_model_calls',model_default),
        'judge_calls':limit('max_judge_calls',judge_default),
        'source':'explicit values override task-count defaults',
        'selected_cases':len(chosen),'scheme_count':len(args.schemes or ['auto']),'repeats':args.repeats}


async def run_dataset(args,settings,token=''):
    from learntrail_agent.evaluation.runner import private_json,fingerprint,snapshot,isolated_database,corpus as prepare_corpus,BudgetedModel
    manifest,cases,corpus=load_dataset(args.dataset)
    if args.dataset_version and args.dataset_version!=manifest.version:raise ValueError('Dataset version mismatch')
    chosen=select_cases(cases,split=args.split,categories=args.categories,collection=args.collection,ids=args.case_ids)
    if args.case_ids and set(args.case_ids)-{c.id for c in chosen}:raise ValueError('Unknown or filtered case ID')
    if not chosen:raise ValueError('No selected cases')
    summary=dataset_summary(manifest,cases)
    if args.mode=='validate':return {'dataset':summary,'selected':len(chosen)},None
    if args.mode=='live' and (not args.live or not token):raise ValueError('Live mode requires --live and LEARNTRAIL_EVAL_TOKEN')
    limits=evaluation_budgets(chosen,args,settings)
    folder=args.output/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]);folder.mkdir(parents=True,mode=0o700)
    report={'experiment_id':folder.name,'dataset':summary,'split':args.split,'mode':args.mode,
        'runtime':fingerprint(),'results':[],'summaries':{},'source_snapshot_sha256':snapshot(folder),
        'dataset_sha256':hashlib.sha256(args.dataset.read_bytes()+(args.dataset.parent/manifest.cases_file).read_bytes()+(args.dataset.parent/manifest.corpus_file).read_bytes()).hexdigest()}
    report['configuration']={key:getattr(settings,key) for key in ['llm_model','embedding_model','embedding_dimensions',
        'rag_chunk_size','rag_chunk_overlap','rag_top_k','rag_min_similarity','rag_bm25_k1','rag_bm25_b','rag_rrf_k','agent_max_rounds']}
    report['configuration']['pricing_configured']=settings.llm_pricing_file is not None
    report['configuration']['reranker_configured']=settings.reranker_configured
    report['budgets']=limits
    config=settings.model_copy(update={'observability_enabled':True,'observability_dir':folder/'telemetry',
        'mcp_servers_file':None,'judge_max_calls':limits['judge_calls']})
    telemetry=Telemetry(config);cache={};budget=[0,limits['model_calls']]
    schemes=args.schemes or ['auto']
    async def execute(state=None,user=None,judge=None,only_repeat=None,only_scheme=None):
        for repeat in ([only_repeat] if only_repeat else range(1,args.repeats+1)):
            for scheme in ([only_scheme] if only_scheme else schemes):
                for case in chosen:
                    started=time.monotonic();span=telemetry.begin('evaluation.case','evaluator')
                    tid=format(span.get_span_context().trace_id,'032x')
                    with telemetry.activate(),trace.use_span(span,end_on_exit=False,record_exception=False,set_status_on_exception=False):
                        try:
                            if args.mode=='deterministic':row=await deterministic_case(case,corpus,config,folder,cache,scheme,state)
                            else:
                                row=await live_case(case,state,user,token,judge,budget,scheme,corpus)
                            row=v4_stages(case,row)
                        except AppError as error:row={'status':'error','error':error.code,'observations':{}}
                        except Exception as error:row={'status':'error','error':'EVALUATION_CASE_FAILED','error_type':type(error).__name__,'observations':{}}
                    telemetry.finish(span,'evaluation.case',started,values={'error_code':row.get('error')})
                    if manifest.schema_version==4:
                        row.update(question=case.question,reference_answer=case.expected.reference_answer,
                            gold_validation=case.evaluation.gold_kind)
                    row.update(id=case.id,category=case.category,collection=case.collection,review_status=case.status,scheme=scheme,repeat=repeat,
                        trace_id=tid,duration_ms=round((time.monotonic()-started)*1000,2))
                    report['results'].append(row);private_json(folder/'cases'/f'{case.id}-{scheme}-{repeat}.json',row)
    try:
        if args.mode=='deterministic' and manifest.schema_version in {3,4}:
            needs_db=any(c.execution.kind in {'graph','memory','hitl'} or c.execution.operation=='other_session' or c.execution.parameters.get('exercise_tool') for c in chosen)
            if needs_db and config.database_url.get_secret_value():
                async with isolated_database(config) as schema:
                    local=config.model_copy(update={'database_schema':schema,'checkpoint_key_file':folder/'case-checkpoint.key'})
                    app=create_app(local,telemetry=telemetry)
                    async with app.router.lifespan_context(app):await execute(app.state)
            else:await execute()
        elif args.mode=='deterministic':await execute()
        else:
            # Reuse the existing isolation/migration/Java corpus verification logic.
            report['judge_calls']=[]
            for repeat in range(1,args.repeats+1):
                for scheme in schemes:
                    async with isolated_database(config) as schema:
                        live_config=config.model_copy(update={'database_schema':schema,
                            'checkpoint_key_file':folder/f'{scheme}-{repeat}.key',
                            'agent_memory_enabled':scheme!='short_memory','agent_skills_enabled':scheme!='no_skills'})
                        app=create_app(live_config,telemetry=telemetry)
                        async with app.router.lifespan_context(app):
                            state=app.state;user=await authenticate_token(token,state.jwt_public_key,state.http,live_config)
                            if any(c.category=='rag' or 'search_knowledge_base' in c.expected.allowed_tools for c in chosen):
                                data={'sources':[{'slug':s['slug'],'content_hash':s['content_hash'],'available':True} for s in corpus['sources']]}
                                await prepare_corpus(state,user,token,data,True)
                            original=state.rag_service.llm;state.rag_service.llm=BudgetedModel(original,budget)
                            judge=Judge(state.http,live_config)
                            # HTTP clients have per-schema lifetimes, but the call ledger
                            # belongs to the entire experiment, including all variants.
                            judge.calls=report['judge_calls']
                            await execute(state,user,judge,repeat,scheme)
    except Exception as error:
        report['blocked']={'code':error.code if isinstance(error,AppError) else 'ENVIRONMENT_OR_CONFIGURATION_ERROR','type':type(error).__name__}
    finally:
        await asyncio.to_thread(telemetry.close)
        for category in sorted({r['category'] for r in report['results']}):
            for scheme in schemes:
                group=[r for r in report['results'] if r['category']==category and r['scheme']==scheme]
                if group:
                    measured=category_metrics(category,group);measured['repeats']=repeat_summary(group);report['summaries'][category+'/'+scheme]=measured
        report['actual_model_attempts']=budget[0]
        stages=[r.get('evaluations',{}) for r in report['results']]
        report['execution_coverage']={
            'dataset_cases_validated':len(cases),'results':len(report['results']),
            'offline_article_retrieval':sum(r.get('scope')=='offline_article_bm25' for r in report['results']),
            'production_retrieval':sum(bool(r.get('retrieval')) for r in report['results']),
            'answers_saved':sum(bool(r.get('answer')) for r in report['results']),
            'generation_completed':sum(s.get('generation',{}).get('status')=='completed' for s in stages),
            'semantic_scored':sum(s.get('semantic',{}).get('status')=='scored' for s in stages),
            'retrieval_failed':sum(s.get('retrieval',{}).get('status')=='failed' for s in stages),
            'functional_contracts_passed':sum(r.get('evaluation_kind')=='functional_contract' and r.get('contract_success') is True for r in report['results']),
            'unexecuted':sum(r.get('status')=='unverified' for r in report['results'])}
        report['paired_comparisons']=paired_comparisons(report['results'])
        report['contract_executions']=cache
        report['comparison_availability']={'workflow':'pending: no equivalent frozen baseline',
            'memory_none':'pending: only short-term and short+long-term modes exist',
            'native_vs_business_mcp':'pending: local transport contracts run; equivalent authenticated business comparison not run'}
        private_json(folder/'report.json',report)
        fd=os.open(folder/'report.md',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as file:file.write(report_markdown(report))
        if manifest.schema_version<4:
            private_json(folder/'human-review.json',[{'case_id':r['id'],'scheme':r['scheme'],'repeat':r['repeat'],
                'judge':r.get('observations',{}).get('judge'),'human_task_success':None,'human_notes':''}
                for r in report['results'] if r.get('observations',{}).get('judge',{}).get('status')=='scored'])
    return report,folder
