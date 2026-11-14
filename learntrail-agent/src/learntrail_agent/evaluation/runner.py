"""Explicit local evaluation CLI. Live calls require --live; token only via environment."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from opentelemetry import trace

from learntrail_agent.agent.advanced_models import MemoryCreate
from learntrail_agent.agent.schemas import AgentChatRequest
from learntrail_agent.clients.java import ArticleUnavailable
from learntrail_agent.errors import AppError
from learntrail_agent.evaluation.contracts import run_contracts
from learntrail_agent.evaluation.judge import Judge,ragas_metrics
from learntrail_agent.evaluation.metrics import retrieval_metrics,citation_metrics,answer_metrics,trajectory_metrics,aggregate,latency,tool_metrics
from learntrail_agent.main import create_app
from learntrail_agent.rag.schemas import SearchRequest
from learntrail_agent.security.auth import authenticate_token
from learntrail_agent.settings import PROJECT_ROOT,Settings
from learntrail_agent.observability import Telemetry

VARIANTS={"baseline":{},"prompt_checklist":{"agent_prompt_suffix":"调用工具前检查是否需要真实业务数据；遵守用户明确指定的关键词、页码、数量；完成后核对每项任务是否有依据，禁止多余查询。"},
    "no_memory":{"agent_memory_enabled":False},"no_skills":{"agent_skills_enabled":False}}


class BudgetedModel:
    def __init__(self,client,budget):self.client,self.budget=client,budget
    async def complete_with_tools(self,request):
        if self.budget[0]>=self.budget[1]:raise AppError("EVALUATION_BUDGET_EXCEEDED","已达到模型实验预算。",422)
        self.budget[0]+=1
        return await self.client.complete_with_tools(request)

    async def complete(self,request):
        if self.budget[0]>=self.budget[1]:raise AppError('EVALUATION_BUDGET_EXCEEDED','已达到模型实验预算。',422)
        self.budget[0]+=1
        return await self.client.complete(request)


def normalized_trajectory(events,registry):
    """Declared defaults count as their actual executor values; never fixes invalid args."""
    copied=json.loads(json.dumps(events,default=str))
    for event in copied:
        if event["kind"]!="model_end":continue
        for call in event.get("payload",{}).get("tool_calls",[]):
            tool=registry.get(call["function"]["name"])
            if tool:
                try:
                    parsed=tool.arguments.model_validate_json(call["function"]["arguments"])
                    call["function"]["arguments"]=parsed.model_dump_json()
                except ValueError:pass
    return copied


def private_json(path: Path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Do not leave readable intermediate files or follow pre-existing symlinks.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as file:json.dump(value,file,ensure_ascii=False,indent=2,allow_nan=False)


def fingerprint() -> dict:
    sha=hashlib.sha256()
    for path in sorted((PROJECT_ROOT/"src").rglob("*.py")):
        sha.update(str(path.relative_to(PROJECT_ROOT)).encode());sha.update(path.read_bytes())
    sha.update((PROJECT_ROOT/"uv.lock").read_bytes())
    return {"source_and_lock_sha256":sha.hexdigest(),"python":__import__("platform").python_version(),
        "git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=PROJECT_ROOT,text=True).strip(),
        "dirty_worktree":bool(subprocess.check_output(["git","status","--porcelain"],cwd=PROJECT_ROOT))}


def snapshot(folder: Path):
    paths=[*sorted((PROJECT_ROOT/"src").rglob("*.py")),*sorted((PROJECT_ROOT/"migrations").glob("*.sql")),
        *sorted((PROJECT_ROOT/"skills").glob("*/SKILL.md")),*sorted((PROJECT_ROOT/'evaluation').glob('*.json')),
        *sorted((PROJECT_ROOT/'evaluation').glob('*.jsonl')),
        *sorted((PROJECT_ROOT/'tests').glob('*.py')),
        PROJECT_ROOT/"tests/fixtures/rag_retrieval_cases.json",PROJECT_ROOT/"pyproject.toml",PROJECT_ROOT/"uv.lock",PROJECT_ROOT/".python-version"]
    path=folder/"source.zip"
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as file,zipfile.ZipFile(file,"w",zipfile.ZIP_DEFLATED) as archive:
        for item in paths:archive.write(item,str(item.relative_to(PROJECT_ROOT)))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@asynccontextmanager
async def isolated_database(settings: Settings):
    schema="eval_ai_"+uuid4().hex
    dsn=settings.database_url.get_secret_value()
    if not dsn:raise RuntimeError("Evaluation requires the existing AI PostgreSQL")
    async with await psycopg.AsyncConnection.connect(dsn,autocommit=True) as conn:
        extension=await (await conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")).fetchone()
        if not extension:raise RuntimeError("Existing pgvector extension is required; evaluation never installs it")
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await conn.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
            for migration in sorted((PROJECT_ROOT/"migrations").glob("V*.sql")):
                await conn.execute(migration.read_text(),prepare=False)
            yield schema
        finally:
            if conn.info.transaction_status!=psycopg.pq.TransactionStatus.IDLE:await conn.execute("ROLLBACK")
            # Only the random schema created by this invocation is disposable.
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def corpus(state,user,token,dataset,index):
    sources={};summary=[]
    for expected in dataset["sources"]:
        try:source=await state.java_articles.get_published_article(expected["slug"],token)
        except ArticleUnavailable:
            if expected["available"]:raise RuntimeError("Evaluation corpus changed: expected published article unavailable") from None
            summary.append({"slug":expected["slug"],"status":"unavailable"});continue
        if not expected["available"]:raise RuntimeError("Evaluation corpus changed: withdrawn article is published")
        document,_,_=state.article_indexer.prepare_source(source)
        if document.content_hash!=expected["content_hash"]:raise RuntimeError("Corpus hash changed; review qrels before rerunning")
        sources[expected["slug"]]={"text":document.text,"content_hash":document.content_hash}
        if index:
            result=await state.article_indexer.index_article(expected["slug"],token)
            summary.append(result.model_dump(mode="json"))
        else:
            saved=await state.vector_repository.get_state(user.user_id,source.article.id)
            if saved is None or not saved.active or saved.content_hash!=document.content_hash:raise RuntimeError("Missing frozen evaluation index")
    return sources,summary


async def rag_suite(state,user,token,dataset,sources,judge,answers,use_ragas,*,strategies=None,answer_strategy='hybrid'):
    report={"strategies":{},"answers":[]};slugs=[s["slug"] for s in dataset["sources"]]
    for strategy in (strategies or ("vector","bm25","hybrid","hybrid_reranker")):
        if strategy=="hybrid_reranker" and not state.settings.reranker_configured:
            report["strategies"][strategy]={"verification":"unverified","reason":"RERANKER_NOT_CONFIGURED"};continue
        rows=[]
        for case in dataset["cases"]:
            start=time.monotonic()
            try:
                result=await state.retriever.search(user.user_id,SearchRequest(query=case["query"],mode="hybrid" if strategy=="hybrid_reranker" else strategy,
                    top_k=5,article_slugs=slugs,rerank=strategy=="hybrid_reranker"),token)
                rows.append({"id":case["id"],"relevant":case["relevant"],"grades":case.get('grades',{}),"forbidden":case.get("forbidden",[]),
                    "ranked":[h.slug for h in result.results],"duration_ms":result.duration_ms,
                    "retrieval":result.model_dump(mode='json') if hasattr(result,'model_dump') else None,
                    "embedding_usage":result.embedding_usage.model_dump() if result.embedding_usage else None})
            except AppError as error:
                rows.append({"id":case["id"],"relevant":case["relevant"],"grades":case.get('grades',{}),"forbidden":case.get("forbidden",[]),"ranked":[],"error":error.code,
                    "duration_ms":int((time.monotonic()-start)*1000)})
        report["strategies"][strategy]={"verification":"executed","cases":rows,"errors":sum("error" in r for r in rows),
            "metrics":[retrieval_metrics(rows,k) for k in (1,3,5)],"latency":latency([r["duration_ms"] for r in rows])}
        print(json.dumps({"suite":"rag","strategy":strategy,"metrics":report["strategies"][strategy]["metrics"][1]},ensure_ascii=False),flush=True)
    if answers:
        sid=(await state.sessions.create(user.user_id,"评测 RAG 回答")).id
        for case in dataset["cases"]:
            row={"id":case["id"],"expected_answer_available":case["answer_available"]}
            try:
                result=await state.rag_service.answer(user.user_id,sid,SearchRequest(query=case["query"],article_slugs=slugs,top_k=5,
                    mode='hybrid' if answer_strategy=='hybrid_reranker' else answer_strategy,rerank=answer_strategy=='hybrid_reranker'),token)
                row.update(status=result.status,duration_ms=result.duration_ms,answer=result.answer,
                    retrieval=result.retrieval.model_dump(mode='json') if getattr(result,'retrieval',None) is not None else None,
                    citation_details=[c.model_dump(mode='json') for c in result.citations],
                    citations=citation_metrics([c.model_dump() for c in result.citations],sources),
                    usage=result.usage.model_dump() if result.usage else None,
                    cost=state.telemetry.pricing.estimate(result.model,result.usage),
                    judge=await judge.assess(case["query"],case["evidence"],result.answer,[c.quote for c in result.citations]))
                if use_ragas:row["ragas"]=await ragas_metrics(judge,case["query"],result.answer,case["evidence"],[c.quote for c in result.citations])
                state.telemetry.score("source_position_accuracy",row["citations"]["source_position_accuracy"],case["id"])
                for name in ("answer_correctness","faithfulness"):
                    state.telemetry.score(name,row["judge"].get(name),case["id"])
            except AppError as error:row.update(status="error",error=error.code)
            report["answers"].append(row)
        report["answer_metrics"]=answer_metrics(report["answers"])
    return report


async def agent_suite(state,user,token,cases,variant,judge,call_budget):
    rows=[]
    original=state.agent_service.loop.llm
    state.agent_service.loop.llm=BudgetedModel(original,call_budget)
    for case in cases:
        if call_budget[0]>=call_budget[1]:
            rows.append({"id":case["id"],"verification":"unverified","reason":"MODEL_CALL_BUDGET"});continue
        memory=None;sid=(await state.sessions.create(user.user_id,"评测 "+case["id"])).id
        if case.get("setup_memory"):
            origin=(await state.sessions.create(user.user_id,'评测记忆来源会话')).id
            memory=await state.advanced.memories.create(user.user_id,MemoryCreate(**case["setup_memory"],source_session_id=origin))
            if case.get('expire_memory_before_query'):
                async with state.pool.connection() as conn:
                    await conn.execute("UPDATE ai_memories SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(memory.id,))
        started=time.monotonic();prepared=None;calls_before=call_budget[0]
        try:
            for text in case.get('history',[]):
                prior=await state.agent_service.prepare(user.user_id,sid,AgentChatRequest(content=text),token)
                await state.agent_service.execute(prior)
            prepared=await state.agent_service.prepare(user.user_id,sid,AgentChatRequest(content=case["question"],
                skill=case.get("skill") if state.settings.agent_skills_enabled else None),token)
            result=await state.agent_service.execute(prepared)
            trajectory=await state.agent_service.trace(user.user_id,sid,result.run_id,token)
            snapshot=await state.agent_service.workflow_state(user.user_id,sid,result.run_id,token)
            events=normalized_trajectory(trajectory["events"],prepared.capabilities.registry if prepared.capabilities else state.agent_service.loop.registry)
            metrics=trajectory_metrics(case["expected"],events,result.status)
            memory_observations={}
            memory_contexts=[]
            if memory:
                graph_state=await state.agent_service.loop.snapshot(prepared.context,prepared.checkpointer)
                selected=graph_state.values.get('long_term_memory',[])
                ids={f['memory_id'] for f in selected}
                wanted=case.get('expected_memory_retrieval',True)
                if wanted:memory_observations['retrieval_recall_at_k']=float(str(memory.id) in ids)
                else:memory_observations['irrelevant_memory_rejection']=float(str(memory.id) not in ids)
                memory_contexts=[{'confirmed_memory':f['content']} for f in selected]
            if case.get("expected_skill"):
                expected=case["expected_skill"] if state.settings.agent_skills_enabled else None
                metrics["skill_selection_correct"]=(snapshot.get("skill") or {}).get("name")==expected
            if memory:metrics["memory_retrieval_correct"]=(snapshot["memory_count"]>0)==state.settings.agent_memory_enabled
            # Do not pass private prompts or raw Java records to a judge. Only safe domain evidence.
            evidence=[e["payload"].get("data") for e in trajectory["events"] if e["kind"]=="tool_result" and e["payload"].get("ok")]
            tool_quality=tool_metrics(case['expected'],events)
            registry=prepared.capabilities.registry if prepared.capabilities else state.agent_service.loop.registry
            arguments={}
            for event in events:
                if event['kind']!='model_end':continue
                for call in event.get('payload',{}).get('tool_calls',[]):
                    name=call.get('function',{}).get('name');tool=registry.get(name)
                    try:
                        parsed=json.loads(call['function']['arguments'])
                        if not isinstance(parsed,dict):raise ValueError()
                        allowed=set(tool.arguments.model_fields) if tool else set()
                        arguments[call['id']]={'arguments':{k:v for k,v in parsed.items() if k in allowed},
                            'unexpected_argument_keys':sorted(set(parsed)-allowed)}
                    except (ValueError,KeyError,TypeError):arguments[call.get('id')]={'arguments_valid':False}
            tool_summary=[{'tool':e['payload'].get('name'),'ok':e['payload'].get('ok'),
                'error_code':e['payload'].get('error_code'),**arguments.get(e['payload'].get('call_id'),{})}
                for e in events if e['kind']=='tool_result']
            judged=await judge.assess(case["question"],case["reference"],result.content,evidence+memory_contexts,tool_summary=tool_summary)
            for name,value in metrics.items():state.telemetry.score(name,value,case["id"])
            for name in ("answer_correctness","faithfulness"):state.telemetry.score(name,judged.get(name),case["id"])
            rows.append({"id":case["id"],"verification":"executed","source":"real_java_postgres_model","variant":variant,
                "metrics":metrics,"tool_quality":tool_quality,"memory_observations":memory_observations,
                "selected_skill":(snapshot.get('skill') or {}).get('name'),"answer":result.content,"judge":judged,"model_calls":call_budget[0]-calls_before,"graph_model_steps":result.model_calls,"tool_calls":result.tool_calls,
                "duration_ms":result.duration_ms,"usage":result.usage.model_dump() if result.usage else None,
                "usage_complete":result.usage_complete,"cost":state.telemetry.pricing.estimate(result.model,result.usage,complete=result.usage_complete),
                "run_id":str(result.run_id)})
        except AppError as error:
            attempts=prepared.state.model_calls if prepared else 0
            rows.append({"id":case["id"],"verification":"executed","source":"real_java_postgres_model","variant":variant,
                "metrics":{"trajectory_success":False},"error":error.code,"model_calls":call_budget[0]-calls_before,"graph_model_steps":attempts,
                "duration_ms":int((time.monotonic()-started)*1000),"cost":{"amount":None,"status":"unknown"}})
        finally:
            if memory:
                current=await state.advanced.memories.get(user.user_id,memory.id)
                await state.advanced.memories.delete(user.user_id,memory.id,current.version)
        print(json.dumps({"suite":"agent","variant":variant,"id":case["id"],"success":rows[-1].get("metrics",{}).get("trajectory_success"),"error":rows[-1].get("error")}),flush=True)
    state.agent_service.loop.llm=original
    return {"cases":rows,"summary":aggregate(rows)}


def markdown(report):
    lines=["# LearnTrail AI 评测报告","",f"实验：`{report['experiment_id']}`",f"代码指纹：`{report['runtime']['source_and_lock_sha256']}`","",
        "真实模型、受控模型契约和未验证项分别记录。小样本结果不能代表生产泛化效果。","","## RAG","","|策略|Recall@3|MRR@3|NDCG@3|负例非空率|错误|","|---|---:|---:|---:|---:|---:|"]
    for name,row in report.get("rag",{}).get("strategies",{}).items():
        if row.get("verification")!="executed":lines.append(f"|{name}|未验证|—|—|—|{row.get('reason')}|");continue
        m=row["metrics"][1];lines.append(f"|{name}|{m['recall']}|{m['mrr']}|{m['ndcg']}|{m['negative_nonempty_rate']}|{row['errors']}|")
    lines.extend(["","## Agent","","|变体|执行数|轨迹成功率|p50 ms|p95 ms|","|---|---:|---:|---:|---:|"])
    for name,row in report.get("agent",{}).items():
        m=row["summary"];lines.append(f"|{name}|{m['executed']}|{m['trajectory_success_rate']}|{m['latency']['p50_ms']}|{m['latency']['p95_ms']}|")
    lines.extend(["","|变体|模型尝试次数|已知 Tokens|不完整 Usage 项|估算小计|未知费用项|","|---|---:|---:|---:|---|---:|"])
    for name,row in report.get("agent",{}).items():
        m=row["summary"]
        lines.append(f"|{name}|{m.get('model_calls',{}).get('known_total')}|{m.get('tokens',{}).get('known_total')}|{m.get('tokens',{}).get('incomplete_cases')}|{m.get('cost',{}).get('estimated_subtotals') or '未知'}|{m.get('cost',{}).get('unknown_cases')}|")
    lines.extend(["","轨迹成功率只验证状态、预期工具和参数；语义任务成功须参考单独的 Judge 或人工标注。",
        "未知 Usage/价格不计为零成本；费用是指定版本价格表的估算，非供应商账单。","","## 受控契约"," "])
    for row in report.get("contracts",[]):lines.append(f"- {row['id']}: {row.get('metrics',{}).get('trajectory_success')}（{row.get('error','真实 SQL/Graph/MCP，受控模型')}）")
    lines.extend(["","## 待验证与已知问题","",*["- "+item for item in report["known_open_issues"]],
        "- 手写循环/Graph 缺少等价冻结基线，未做效果比较。","- 外部 Langfuse/OTLP 只有配置后才能验证控制台接收，本机协议测试不代表生产接入。"])
    return "\n".join(lines)+"\n"


async def run(args,settings: Settings,token: str):
    if json.loads(args.dataset.read_text()).get('schema_version') in {2,3,4}:
        from learntrail_agent.evaluation.framework import run_dataset
        return await run_dataset(args,settings,token)
    dataset=json.loads(args.dataset.read_text());rag_path=PROJECT_ROOT/dataset["rag_dataset"]
    if not 1<=len(dataset["agent_cases"])<=32:raise ValueError("Agent dataset size must be 1..32")
    if args.case_ids and not set(args.case_ids)<={c["id"] for c in dataset["agent_cases"]}:
        raise ValueError("Unknown evaluation case ID")
    if not rag_path.resolve().is_relative_to(PROJECT_ROOT):raise ValueError("Dataset path must be local")
    rag_data=json.loads(rag_path.read_text())
    if not 1<=len(rag_data["cases"])<=32 or not 1<=len(rag_data["sources"])<=20:
        raise ValueError("RAG dataset exceeds evaluation limits")
    folder=args.output/((datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))+"-"+uuid4().hex[:8])
    folder.mkdir(parents=True,mode=0o700)
    report={"experiment_id":folder.name,"runtime":fingerprint(),"dataset_version":dataset["version"],
        "dataset_sha256":hashlib.sha256(args.dataset.read_bytes()+rag_path.read_bytes()).hexdigest(),
        "known_open_issues":dataset["known_open_issues"],"agent":{},"contracts":[],"judge_calls":[],
        "budgets":{"max_agent_model_calls":args.max_model_calls,"repeats":args.repeats},
        "observability":{"langfuse_configured":settings.langfuse_enabled,"otlp_configured":bool(settings.otel_exporter_otlp_endpoint)},
        "historical_loop_comparison":dataset["comparisons"]["historical_handwritten_loop"]}
    report["source_snapshot_sha256"]=snapshot(folder)
    telemetry=None
    try:
        async with isolated_database(settings) as schema:
            base=settings.model_copy(update={"database_schema":schema,"checkpoint_key_file":folder/"checkpoint.key",
                "observability_enabled":True,"observability_dir":folder/"telemetry","mcp_servers_file":None})
            report["configuration"]={key:getattr(base,key) for key in ("llm_model","embedding_model","embedding_dimensions","rag_min_similarity","rag_rrf_k","agent_max_rounds","agent_max_tokens")}
            telemetry=Telemetry(base)
            budget=[0,args.max_model_calls];indexed=False
            for repeat in range(args.repeats):
                for variant in args.variants:
                    config=base.model_copy(update=VARIANTS[variant]);app=create_app(config,telemetry=telemetry)
                    async with app.router.lifespan_context(app):
                        state=app.state;judge=Judge(state.http,config)
                        with state.telemetry.activate(),trace.use_span(state.telemetry.begin("evaluation.experiment"),end_on_exit=True,record_exception=False):
                            user=await authenticate_token(token,state.jwt_public_key,state.http,config)
                            if not indexed and args.suite in {"all","rag","agent"}:
                                sources,report["corpus"]=await corpus(state,user,token,rag_data,True);indexed=True
                            if repeat==0 and variant==args.variants[0]:
                                if args.suite in {"all","contracts"}:report["contracts"]=await run_contracts(state,user,token,dataset)
                                if args.suite in {"all","rag"}:report["rag"]=await rag_suite(state,user,token,rag_data,sources,judge,args.answers,args.ragas)
                            if args.suite in {"all","agent"}:
                                cases=[c for c in dataset["agent_cases"] if not args.case_ids or c["id"] in args.case_ids]
                                report["agent"][f"{variant}:repeat{repeat+1}"]=await agent_suite(state,user,token,cases,variant,judge,budget)
                            report["judge_calls"].extend(judge.calls)
                    private_json(folder/f"progress-{repeat}-{variant}.json",report)
            report["actual_agent_model_calls"]=budget[0]
    except Exception as error:
        report["blocked"]={"code":error.code if isinstance(error,AppError) else "ENVIRONMENT_OR_DATASET_ERROR","type":type(error).__name__}
        # Report fixed diagnostics; never dump provider bodies or connection strings.
        print(json.dumps({"blocked":report["blocked"]}),flush=True)
    finally:
        if telemetry is not None:await asyncio.to_thread(telemetry.close)
        private_json(folder/"report.json",report)
        fd=os.open(folder/"report.md",os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,"w") as file:file.write(markdown(report))
    print("Evaluation report: "+str(folder/"report.md"),flush=True)
    return report,folder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",help="Explicit authorization for real Java, AI DB and billable model/embedding calls")
    parser.add_argument("--suite",choices=["all","rag","agent","contracts"],default="all")
    parser.add_argument("--dataset",type=Path,default=PROJECT_ROOT/"evaluation/dataset-v4.json")
    parser.add_argument('--dataset-version')
    parser.add_argument('--mode',choices=['validate','deterministic','live'],default='validate')
    parser.add_argument('--split',choices=['development','validation','test','all'],default='development')
    parser.add_argument('--categories',nargs='+',choices=['rag','agent','memory','skills','mcp','hitl'])
    parser.add_argument('--collection',choices=['capability','regression'])
    parser.add_argument('--model',help='Explicit candidate chat model ID using the configured provider')
    parser.add_argument('--schemes',nargs='+',choices=['auto','vector','bm25','hybrid','hybrid_reranker','agent','workflow','memory_none','short_memory','full_memory','skills','no_skills','mcp_stdio','mcp_http'])
    parser.add_argument("--output",type=Path,default=PROJECT_ROOT.parent/".local/evaluation")
    parser.add_argument("--variants",nargs="+",choices=list(VARIANTS),default=["baseline"])
    parser.add_argument("--case-ids",nargs="+")
    parser.add_argument("--repeats",type=int,choices=range(1,6),default=1)
    parser.add_argument("--max-model-calls",type=int,default=None,help="Run-wide generation budget; default derives from selected tasks")
    parser.add_argument("--max-judge-calls",type=int,default=None,help="Run-wide judge budget; default derives from selected tasks")
    parser.add_argument("--answers",action="store_true")
    parser.add_argument("--ragas",action="store_true")
    args=parser.parse_args()
    formal=json.loads(args.dataset.read_text()).get('schema_version') in {2,3,4}
    if not formal and not args.live:parser.error("Legacy real evaluation requires --live")
    if args.max_model_calls is not None and args.max_model_calls<1:parser.error("Model budget must be positive")
    if args.max_judge_calls is not None and args.max_judge_calls<1:parser.error("Judge budget must be positive")
    token=os.getenv("LEARNTRAIL_EVAL_TOKEN","")
    if (not formal or args.mode=='live') and not token:parser.error("Set LEARNTRAIL_EVAL_TOKEN privately; never use a CLI token argument")
    settings=Settings()
    if not formal:
        if args.max_model_calls is None:args.max_model_calls=40
        if args.max_judge_calls is not None:settings=settings.model_copy(update={'judge_max_calls':args.max_judge_calls})
    if args.model:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',args.model) or args.model.lower().startswith(('sk-','pk-lf-')):parser.error('Invalid candidate model ID')
        settings=settings.model_copy(update={'llm_model':args.model})
    report,folder=asyncio.run(run(args,settings,token))
    print(json.dumps(report if folder is None else {'report':str(folder/'report.md'),'cases':len(report.get('results',[]))},ensure_ascii=False))
    if report.get("blocked"):raise SystemExit(2)


if __name__=="__main__":main()
