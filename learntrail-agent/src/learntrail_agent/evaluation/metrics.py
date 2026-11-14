"""Deterministic metrics. Never converts missing observations into successful scores."""

import math
from collections import Counter
from statistics import mean
from decimal import Decimal


def ratio(numerator,denominator):return numerator/denominator if denominator else None


def retrieval_verdict(row: dict, k: int = 5) -> dict:
    """Evaluate retrieved rankings, independently of gold/source-file validity."""
    ranked=row['ranked'][:k];relevant=set(row['relevant'])
    checks={'all_relevant_recalled':relevant<=set(ranked),
        'no_forbidden_hits':not set(ranked)&set(row.get('forbidden',[])),
        'empty_for_zero_relevance':bool(relevant) or not ranked,
        'retrieval_completed':not bool(row.get('error'))}
    return {'status':'passed' if all(checks.values()) else 'failed','checks':checks,
        'missing_relevant':sorted(relevant-set(ranked)),
        'metrics':retrieval_metrics([row],k)}


def retrieval_metrics(rows: list[dict],k: int) -> dict:
    if k<1:raise ValueError("K must be positive")
    positive=[];negative=[];negative_errors=0;forbidden=0;precisions=[];graded=[]
    for row in rows:
        ranked=row["ranked"][:k];relevant=set(row["relevant"])
        forbidden+=sum(item in set(row.get("forbidden",[])) for item in ranked)
        if not relevant:
            if row.get("error"):negative_errors+=1
            else:negative.append(bool(ranked))
            continue
        seen=set();gains=[]
        for item in ranked:
            gains.append(1 if item in relevant and item not in seen else 0);seen.add(item)
        recall=len(relevant&set(ranked))/len(relevant)
        precisions.append(len(relevant&set(ranked))/k)
        mrr=next((1/(i+1) for i,gain in enumerate(gains) if gain),0)
        dcg=sum(gain/math.log2(i+2) for i,gain in enumerate(gains))
        ideal=sum(1/math.log2(i+2) for i in range(min(k,len(relevant))))
        positive.append((recall,mrr,dcg/ideal))
        if row.get('grades'):
            grades=row['grades'];seen=set();g=[]
            for item in ranked:
                g.append(2**grades.get(item,0)-1 if item not in seen else 0);seen.add(item)
            best=sorted((2**v-1 for v in grades.values() if v>0),reverse=True)[:k]
            idcg=sum(v/math.log2(i+2) for i,v in enumerate(best))
            graded.append(sum(v/math.log2(i+2) for i,v in enumerate(g))/idcg if idcg else 0)
    return {"k":k,"positive_cases":len(positive),"negative_cases":len(negative)+negative_errors,
        "recall":mean(x[0] for x in positive) if positive else None,
        "mrr":mean(x[1] for x in positive) if positive else None,
        "ndcg":mean(x[2] for x in positive) if positive else None,
        "precision":mean(precisions) if precisions else None,
        "graded_ndcg":mean(graded) if graded else None,"graded_cases":len(graded),
        "negative_nonempty_rate":mean(negative) if negative else None,"negative_retrieval_errors":negative_errors,"forbidden_hits":forbidden,
        "unit":("article-level relevance; K counts articles" if rows and all(r.get('unit')=='article' for r in rows)
            else "article-level relevance; K counts chunks; repeated article gets no additional gain")}


def citation_metrics(citations: list[dict],sources: dict[str,dict]) -> dict:
    """Exact source/hash/offset validation, not semantic entailment."""
    correct=0
    for citation in citations:
        source=sources.get(citation.get("slug"));start=citation.get("start_char");end=citation.get("end_char")
        if (source and type(start) is int and type(end) is int and 0<=start<end<=len(source["text"])
                and source["text"][start:end]==citation.get("quote") and source["content_hash"]==citation.get("content_hash")
                and citation.get("position_basis")=="normalized_unicode_codepoints"):
            correct+=1
    return {"citations":len(citations),"source_position_accuracy":ratio(correct,len(citations)),
            "semantic_support":"requires blinded human or configured judge; not inferred from quote overlap"}


def answer_metrics(rows: list[dict]) -> dict:
    eligible=[r for r in rows if r.get("status") in {"answered","insufficient_context"}]
    unanswerable=[r for r in rows if not r["expected_answer_available"]]
    answerable=[r for r in rows if r["expected_answer_available"]]
    return {"cases":len(rows),"completed":len(eligible),
        "refusal_recall":ratio(sum(r.get("status")=="insufficient_context" for r in unanswerable),len(unanswerable)),
        "answer_coverage":ratio(sum(r.get("status")=="answered" for r in answerable),len(answerable)),
        "decision_accuracy":ratio(sum((r["status"]=="answered")==r["expected_answer_available"] for r in eligible),len(rows)),
        "errors":len(rows)-len(eligible)}


def trajectory_metrics(expected: dict,events: list[dict],status: str) -> dict:
    calls=[];successful=[];errors=Counter();argument_pass=[]
    for event in events:
        data=event.get("payload") or {}
        if event["kind"]=="tool_result":
            calls.append(data.get("name"));successful.append(data.get("ok") is True)
            if data.get("error_code"):errors[data["error_code"]]+=1
        elif event["kind"]=="model_end":
            for call in data.get("tool_calls",[]):
                name=call.get("function",{}).get("name")
                spec=expected.get("arguments",{}).get(name)
                if spec is not None:
                    import json
                    try:args=json.loads(call["function"]["arguments"]);argument_pass.append(all(args.get(k)==v for k,v in spec.items()))
                    except (ValueError,KeyError,TypeError):argument_pass.append(False)
    required=set(expected.get("required_tools",[]));allowed=set(expected.get("allowed_tools",required))
    chosen=set(calls)
    paths=expected.get('tool_paths',[])
    selection=(any(set(path)<=chosen for path in paths) if paths else required<=chosen) and chosen<=allowed
    args_ok=all(argument_pass)
    recovery=expected.get('allow_recovery',False)
    completed={e['payload'].get('name') for e in events if e['kind']=='tool_result' and e.get('payload',{}).get('ok') is True}
    recoverable=set(expected.get('recoverable_errors',[]))
    completed_path=any(set(path)<=completed for path in paths) if paths else required<=completed
    execution_ok=all(successful) or (recovery and bool(completed) and completed_path and set(errors)<=recoverable)
    if recovery and execution_ok:
        # Invalid intermediate arguments are allowed only if the corrected call succeeded.
        args_ok=True
        for name,spec in expected.get('arguments',{}).items():
            latest=[c for e in events if e['kind']=='model_end' for c in e.get('payload',{}).get('tool_calls',[]) if c.get('function',{}).get('name')==name]
            try:
                import json
                value=json.loads(latest[-1]['function']['arguments'])
                args_ok=args_ok and all(value.get(k)==v for k,v in spec.items())
            except (IndexError,ValueError,KeyError,TypeError):args_ok=False
    return {"tool_selection_correct":selection,"tool_parameter_accuracy":ratio(sum(argument_pass),len(argument_pass)),
        "tool_success_rate":ratio(sum(successful),len(successful)),"calls":len(calls),"failures":dict(errors),
        "trajectory_success":status==expected.get("status","succeeded") and selection and args_ok and execution_ok,
        "semantic_task_success":None}


def latency(values: list[float]) -> dict:
    if not values:return {"count":0,"p50_ms":None,"p95_ms":None}
    if any(not math.isfinite(v) or v<0 for v in values):raise ValueError("Invalid latency")
    ordered=sorted(values)
    return {"count":len(values),"p50_ms":ordered[math.ceil(len(values)*.5)-1],"p95_ms":ordered[math.ceil(len(values)*.95)-1]}


def aggregate(rows: list[dict]) -> dict:
    verified=[row for row in rows if row.get("verification")=="executed"]
    success=[r["metrics"]["trajectory_success"] for r in verified if "trajectory_success" in r.get("metrics",{})]
    result={"cases":len(rows),"executed":len(verified),"unverified":len(rows)-len(verified),
        "trajectory_success_rate":ratio(sum(success),len(success)),
        "latency":latency([r["duration_ms"] for r in verified if r.get("duration_ms") is not None]),
        "note":"Controlled contract cases and live-model cases must be aggregated separately"}
    for name in ("tool_selection_correct","tool_parameter_accuracy","tool_success_rate","skill_selection_correct","memory_retrieval_correct"):
        values=[r["metrics"][name] for r in verified if r.get("metrics",{}).get(name) is not None]
        result[name]={"cases":len(values),"mean":mean(values) if values else None}
    result["model_calls"]={"known_total":sum(r["model_calls"] for r in verified if r.get("model_calls") is not None),
        "unknown_cases":sum(r.get("model_calls") is None for r in verified)}
    result["tokens"]={"known_total":sum((r.get("usage") or {}).get("total_tokens") or 0 for r in verified),
        "incomplete_cases":sum(not r.get("usage_complete",False) for r in verified)}
    totals={};unknown=0
    for row in verified:
        cost=row.get("cost") or {}
        if cost.get("status")!="estimated" or cost.get("amount") is None:unknown+=1;continue
        currency=cost["currency"];totals[currency]=totals.get(currency,Decimal(0))+Decimal(cost["amount"])
    result["cost"]={"estimated_subtotals":{k:str(v) for k,v in totals.items()},"unknown_cases":unknown,
        "complete_estimate":bool(verified) and unknown==0,"is_provider_invoice":False}
    return result


def set_metrics(expected,actual):
    expected=set(expected);actual=set(actual);hits=len(expected & actual)
    precision=ratio(hits,len(actual));recall=ratio(hits,len(expected))
    f1=2*hits/(len(expected)+len(actual)) if expected or actual else None
    return {'precision':precision,'recall':recall,'f1':f1,
        'exact_match':expected==actual,'expected':len(expected),'observed':len(actual)}


def tool_metrics(expected,events):
    calls=[];successful=[];args=[];unnecessary=0
    allowed=set(expected.get('allowed_tools',[]));required=set(expected.get('required_tools',[]))
    for event in events:
        data=event.get('payload') or {}
        if event['kind']=='model_end':
            import json
            for call in data.get('tool_calls',[]):
                name=call.get('function',{}).get('name');calls.append(name)
                unnecessary+=name not in allowed
                if name in expected.get('arguments',{}):
                    try:
                        parsed=json.loads(call['function']['arguments'])
                        args.append(all(parsed.get(k)==v for k,v in expected['arguments'][name].items()))
                    except (ValueError,KeyError,TypeError):args.append(False)
        if event['kind']=='tool_result':successful.append(data.get('ok') is True)
    chosen=set(calls);valid=len(chosen&allowed);covered=len(chosen&required)
    p=ratio(valid,len(chosen));r=ratio(covered,len(required))
    f1=2*p*r/(p+r) if p is not None and r is not None and p+r else (0 if p is not None and r is not None else None)
    return {'tool_selection_precision':p,'tool_selection_recall':r,'tool_selection_f1':f1,
        'argument_correctness':ratio(sum(args),len(args)),
        'tool_execution_success_rate':ratio(sum(successful),len(successful)),
        'unnecessary_tool_call_rate':ratio(unnecessary,len(calls)),'tool_calls':len(calls)}


def category_metrics(category,rows):
    """Consume measured observations, with per-metric denominators and coverage."""
    result={'cases':len(rows),'errors':sum(r.get('status')=='error' for r in rows),
        'unverified':sum(r.get('status')=='unverified' for r in rows),'metrics':{}}
    def avg(name,values):
        values=[v for v in values if v is not None]
        result['metrics'][name]={'value':mean(values) if values else None,'observations':len(values)}
    observations=[r.get('observations',{}) for r in rows]
    avg('task_success_rate',[None if r.get('evaluation_kind') and r.get('scope')!='real_java_postgres_models' else (
        False if r.get('status')=='error' else r.get('observations',{}).get('task_success')) for r in rows])
    if category=='rag':
        retrieved=[o['retrieval'] for o in observations if 'retrieval' in o]
        result['retrieval']=retrieval_metrics(retrieved,5) if retrieved else None
        for key in ['answer_correctness','faithfulness','citation_precision']:
            avg(key,[o.get('judge',{}).get(key) for o in observations])
        avg('abstention_accuracy',[o.get('abstention_correct') for o in observations])
    elif category=='agent':
        for key in ['tool_selection_precision','tool_selection_recall','tool_selection_f1','argument_correctness',
                    'tool_execution_success_rate','unnecessary_tool_call_rate']:
            avg(key,[o.get(key) for o in observations])
        avg('average_steps',[r.get('steps') for r in rows])
    elif category=='memory':
        for name in ['extraction_precision','extraction_recall','extraction_f1','retrieval_recall_at_k',
                     'update_accuracy','deletion_accuracy','cross_user_isolation','irrelevant_memory_rejection']:
            avg(name,[o.get(name) for o in observations])
    elif category=='skills':
        pairs=[(o['expected_skill'],o['selected_skill']) for o in observations if 'selected_skill' in o]
        avg('selection_accuracy',[a==b for a,b in pairs])
        labels={label for pair in pairs for label in pair};f1=[]
        for label in labels:
            tp=sum(a==b==label for a,b in pairs);den=sum(a==label for a,b in pairs)+sum(b==label for a,b in pairs)
            f1.append(2*tp/den)
        result['metrics']['selection_macro_f1']={'value':mean(f1) if f1 else None,'observations':len(pairs)}
        avg('execution_success_rate',[o.get('task_success') for o in observations])
        avg('unauthorized_tool_rate',[o.get('unauthorized_tool_rate') for o in observations])
    elif category=='mcp':
        for name in ['discovery_success_rate','schema_conformance','invocation_success_rate','transport_recovery_rate']:
            avg(name,[o.get(name) for o in observations])
    elif category=='hitl':
        for name in ['approval_decision_accuracy','resume_success_rate','rejection_enforcement']:
            avg(name,[o.get(name) for o in observations])
        for name in ['duplicate_execution_count','unauthorized_execution_count']:
            values=[o[name] for o in observations if o.get(name) is not None]
            result['metrics'][name]={'value':sum(values) if values else None,'observations':len(values)}
    result['engineering']=engineering_metrics(rows)
    contracts=[r['contract_success'] for r in rows if r.get('contract_success') is not None]
    result['contract_success_rate']={'value':mean(contracts) if contracts else None,'observations':len(contracts)}
    stages=[r.get('evaluations',{}) for r in rows]
    result['stage_coverage']={
        'retrieval_evaluated':sum(s.get('retrieval',{}).get('status') in {'passed','failed'} for s in stages),
        'retrieval_failed':sum(s.get('retrieval',{}).get('status')=='failed' for s in stages),
        'generation_completed':sum(s.get('generation',{}).get('status')=='completed' for s in stages),
        'semantic_scored':sum(s.get('semantic',{}).get('status')=='scored' for s in stages)}
    return result


def engineering_metrics(rows):
    successes=[r for r in rows if r.get('observations',{}).get('task_success') is True]
    costs={};unknown=sum((r.get('cost') or {}).get('status')!='estimated' for r in rows)
    for row in rows:
        c=row.get('cost') or {}
        if c.get('status')=='estimated' and c.get('amount') is not None:
            costs[c['currency']]=costs.get(c['currency'],Decimal(0))+Decimal(str(c['amount']))
    return {'latency':latency([r['duration_ms'] for r in rows if r.get('duration_ms') is not None]),
        'error_rate':ratio(sum(r.get('status')=='error' for r in rows),len(rows)),
        'tokens_known':sum((r.get('usage') or {}).get('total_tokens') or 0 for r in rows),
        'usage_unknown_cases':sum(not r.get('usage_complete',False) for r in rows),
        'tool_calls_known':sum(r.get('tool_calls') or 0 for r in rows),
        'average_steps':mean([r['steps'] for r in rows if r.get('steps') is not None]) if any(r.get('steps') is not None for r in rows) else None,
        'estimated_cost':{k:str(v) for k,v in costs.items()},'unknown_cost_cases':unknown,
        'cost_per_successful_task':{k:str(v/len(successes)) for k,v in costs.items()} if successes and not unknown else None}


def repeat_summary(rows):
    from statistics import pstdev
    groups={}
    for row in rows:groups.setdefault(row['repeat'],[]).append(row)
    rates=[ratio(sum(r.get('observations',{}).get('task_success') is True for r in group),
        sum(r.get('status')=='error' or r.get('observations',{}).get('task_success') is not None for r in group)) for group in groups.values()]
    values=[v for v in rates if v is not None]
    return {'repeats':len(groups),'success_rates':rates,'mean':mean(values) if values else None,
        'population_stddev':pstdev(values) if len(values)>1 else None}


def paired_comparisons(rows):
    result=[]
    for baseline,candidate in [('no_skills','skills'),('short_memory','full_memory'),('vector','hybrid'),('bm25','hybrid'),('hybrid','hybrid_reranker')]:
        left={(r['id'],r['repeat']):r for r in rows if r['scheme']==baseline}
        right={(r['id'],r['repeat']):r for r in rows if r['scheme']==candidate}
        for category in sorted({r['category'] for r in rows}):
            pairs=[(left[key],right[key]) for key in left.keys()&right.keys() if left[key]['category']==category]
            if not pairs:continue
            success=[];selection=[]
            for a,b in pairs:
                ao=a.get('observations',{});bo=b.get('observations',{})
                if ao.get('task_success') is not None and bo.get('task_success') is not None:
                    success.append(int(bo['task_success'])-int(ao['task_success']))
                if 'selected_skill' in ao and 'selected_skill' in bo:
                    selection.append(int(bo['selected_skill']==bo['expected_skill'])-int(ao['selected_skill']==ao['expected_skill']))
            result.append({'category':category,'baseline':baseline,'candidate':candidate,'matched_pairs':len(pairs),
                'success_rate_improvement':mean(success) if success else None,'semantic_pairs':len(success),
                'skill_selection_improvement':mean(selection) if selection else None,'selection_pairs':len(selection)})
    return result
