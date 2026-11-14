"""Explicit OTel/Langfuse instrumentation. Never captures prompts or credentials."""

import asyncio
import functools
import inspect
import json
import logging
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

from opentelemetry import context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, MetricExporter, MetricExportResult
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.trace import Status, StatusCode

from learntrail_agent.settings import Settings

logger=logging.getLogger(__name__)
active_telemetry: ContextVar["Telemetry | None"]=ContextVar("learntrail_telemetry",default=None)


class Pricing:
    """Operator-supplied, versioned rates per million tokens; unknown is not zero."""
    def __init__(self,path: Path | None):
        self.rates={};self.version=None
        if path is None:return
        raw=path.read_bytes()
        if len(raw)>32000:raise ValueError("Pricing file too large")
        data=json.loads(raw)
        if set(data)!={"version","models"} or not isinstance(data["version"],str):raise ValueError("Invalid pricing manifest")
        self.version=data["version"]
        for name,row in data["models"].items():
            if set(row)!={"input_per_million","output_per_million","currency"}:raise ValueError("Invalid price fields")
            values=[Decimal(str(row[k])) for k in ("input_per_million","output_per_million")]
            if not all(v.is_finite() and v>=0 for v in values) or row["currency"] not in {"USD","CNY","EUR"}:
                raise ValueError("Invalid model price")
            self.rates[name]=(*values,row["currency"])

    def estimate(self,model,usage,*,complete=True):
        row=self.rates.get(model)
        usage=usage.model_dump() if hasattr(usage,"model_dump") else usage
        if not complete or not row or not usage or any(usage.get(k) is None for k in ("prompt_tokens","completion_tokens")):
            return {"amount":None,"currency":row[2] if row else None,"status":"unknown","price_version":self.version}
        if any(type(usage[k]) is not int or usage[k]<0 for k in ("prompt_tokens","completion_tokens")):
            raise ValueError("Invalid token counts")
        amount=(Decimal(usage["prompt_tokens"])*row[0]+Decimal(usage["completion_tokens"])*row[1])/1000000
        return {"amount":str(amount),"currency":row[2],"status":"estimated","price_version":self.version}


class PrivateJSONL:
    def __init__(self,directory: Path,name: str,max_bytes: int):
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=directory/name;self.maximum=max_bytes;self.lock=threading.Lock()

    def write(self,rows):
        with self.lock:
            if self.path.exists() and self.path.stat().st_size>=self.maximum:
                os.replace(self.path,self.path.with_suffix(".previous.jsonl"))
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,"a") as file:
                for row in rows:file.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")


class PrivateJSONExporter(SpanExporter):
    def __init__(self,directory: Path,max_bytes: int):
        self.output=PrivateJSONL(directory,"spans.jsonl",max_bytes)
        self.path=self.output.path

    def export(self,spans):
        try:
            self.output.write({"trace_id":format(span.context.trace_id,"032x"),
                "span_id":format(span.context.span_id,"016x"),"parent_id":format(span.parent.span_id,"016x") if span.parent else None,
                "name":span.name,"start_ns":span.start_time,"duration_ms":(span.end_time-span.start_time)/1e6,
                "status":span.status.status_code.name,"attributes":dict(span.attributes or {})} for span in spans)
            return SpanExportResult.SUCCESS
        except OSError:
            logger.warning("Local telemetry export unavailable")
            return SpanExportResult.FAILURE

    def shutdown(self):pass


class PrivateMetricExporter(MetricExporter):
    """Actual SDK MetricsData snapshots; no Collector or extra dependency required."""
    def __init__(self,directory: Path,max_bytes: int):
        super().__init__()
        self.output=PrivateJSONL(directory,"metrics.jsonl",max_bytes)

    def export(self,metrics_data,timeout_millis=10000,**kwargs):
        try:
            self.output.write([{"collected_at_ns":time.time_ns(),"data":json.loads(metrics_data.to_json())}])
            return MetricExportResult.SUCCESS
        except (OSError,ValueError,TypeError):
            logger.warning("Local metric export unavailable")
            return MetricExportResult.FAILURE

    def force_flush(self,timeout_millis=10000):return True

    def shutdown(self,timeout_millis=30000,**kwargs):pass


class Telemetry:
    def __init__(self,settings: Settings,*,exporter=None,metric_reader=None):
        self.enabled=settings.observability_enabled
        self.pricing=Pricing(settings.llm_pricing_file)
        self.provider=None;self.meters=None;self.langfuse=None
        if not self.enabled:return
        resource=Resource({"service.name":"learntrail-agent","deployment.environment.name":settings.learntrail_ai_env})
        self.provider=TracerProvider(resource=resource,sampler=ParentBased(TraceIdRatioBased(settings.observability_sample_rate)))
        self.provider.add_span_processor(BatchSpanProcessor(exporter or PrivateJSONExporter(settings.observability_dir,settings.observability_file_max_bytes),schedule_delay_millis=500))
        readers=[metric_reader] if metric_reader else [PeriodicExportingMetricReader(
            PrivateMetricExporter(settings.observability_dir,settings.observability_file_max_bytes),
            export_interval_millis=settings.otel_metric_export_interval_ms)]
        if settings.otel_exporter_otlp_endpoint:
            import requests
            headers={}
            for part in settings.otel_exporter_otlp_headers.get_secret_value().split(","):
                if part:
                    k,sep,v=part.partition("=")
                    if not sep or k.lower() in {"host","content-length"}:raise ValueError("Invalid OTLP headers")
                    headers[k.strip()]=v.strip()
            base=settings.otel_exporter_otlp_endpoint
            # Disable ambient proxies, implicit netrc credentials, and SDK defaults.
            session=requests.Session();session.trust_env=False
            self.provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=base+"/v1/traces",headers=headers,timeout=3,session=session)))
            session2=requests.Session();session2.trust_env=False
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=base+"/v1/metrics",headers=headers,timeout=3,session=session2),export_interval_millis=10000))
        self.meters=MeterProvider(resource=resource,metric_readers=readers)
        meter=self.meters.get_meter("learntrail")
        self.count=meter.create_counter("learntrail.operations",unit="{operation}")
        self.duration=meter.create_histogram("learntrail.operation.duration",unit="ms")
        self.tokens=meter.create_counter("learntrail.model.tokens",unit="{token}")
        self.tracer=self.provider.get_tracer("learntrail")
        if settings.langfuse_enabled:
            if not (settings.langfuse_base_url and settings.langfuse_public_key.get_secret_value() and settings.langfuse_secret_key.get_secret_value()):
                raise ValueError("Langfuse explicitly enabled but credentials/URL are missing")
            from langfuse import Langfuse
            self.langfuse=Langfuse(public_key=settings.langfuse_public_key.get_secret_value(),secret_key=settings.langfuse_secret_key.get_secret_value(),
                base_url=settings.langfuse_base_url,tracer_provider=self.provider,timeout=3,
                should_export_span=lambda span:span.instrumentation_scope.name=="learntrail")

    @contextmanager
    def activate(self):
        token=active_telemetry.set(self)
        try:yield self
        finally:active_telemetry.reset(token)

    def begin(self,name,kind="span"):
        if not self.enabled:return None
        return self.tracer.start_span(name,attributes={"langfuse.observation.type":kind})

    def finish(self,span,name,start,error=None,values=None):
        if span is None:return
        values=values or {};code=getattr(error,"code",None) if error else values.get("error_code")
        if error and not code:code="CANCELLED" if isinstance(error,(asyncio.CancelledError,GeneratorExit)) else "INTERNAL_ERROR"
        if code and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}",code):code="INTERNAL_ERROR"
        # Explicit schema: no arbitrary strings, inputs, outputs, identity, URLs or exception messages.
        attributes={k:v for k,v in values.items() if k in {"model_calls","tool_calls","count","result_count","http.status_code","usage_complete"} and isinstance(v,(int,float,bool))}
        model=values.get("model")
        if isinstance(model,str) and re.match(r"(?i)^(sk-|pk-lf-|bearer\s)",model.strip()):model=None
        if isinstance(model,str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}",model):
            attributes["gen_ai.request.model"]=model;attributes["langfuse.observation.model.name"]=model
        usage=values.get("usage")
        if hasattr(usage,"model_dump"):usage=usage.model_dump()
        known={k:v for k,v in (usage or {}).items() if k in {"prompt_tokens","completion_tokens","total_tokens"} and type(v) is int and v>=0}
        for k,v in known.items():attributes["learntrail.usage."+k]=v
        if known:
            span.set_attribute("langfuse.observation.usage_details",json.dumps({{"prompt_tokens":"input","completion_tokens":"output","total_tokens":"total"}[k]:v for k,v in known.items()}))
        cost=self.pricing.estimate(model,usage,complete=values.get("usage_complete",True) and not error)
        attributes["learntrail.cost.status"]=cost["status"]
        if cost["amount"] is not None:
            attributes["learntrail.cost.amount"]=float(cost["amount"]);attributes["learntrail.cost.currency"]=cost["currency"]
            attributes["learntrail.cost.price_version"]=cost["price_version"]
        if code:attributes["error.type"]=code;span.set_status(Status(StatusCode.ERROR))
        else:span.set_status(Status(StatusCode.OK))
        span.set_attributes(attributes)
        elapsed=max(0,(time.monotonic()-start)*1000)
        dimensions={"operation":name,"outcome":"error" if code else "ok"}
        self.count.add(1,dimensions);self.duration.record(elapsed,dimensions)
        if name.startswith(("llm.","embedding.")):
            for k,v in known.items():self.tokens.add(v,{"operation":name,"token.type":k})
        span.end()

    def close(self):
        if self.langfuse:self.langfuse.shutdown()
        if self.provider:self.provider.shutdown()
        if self.meters:self.meters.shutdown()

    def score(self,name: str,value: float | bool | None,case_id: str):
        allowed={"trajectory_success","tool_selection_correct","skill_selection_correct","memory_retrieval_correct",
            "answer_correctness","faithfulness","source_position_accuracy"}
        if name not in allowed or value is None or not self.langfuse:return
        if not isinstance(value,(float,int,bool)) or not math.isfinite(value) or not 0<=value<=1:return
        if not re.fullmatch(r"[a-z0-9-]{1,80}",case_id):return
        current=trace.get_current_span().get_span_context()
        if current.is_valid:
            try:
                self.langfuse.create_score(name=name,value=float(value),data_type="NUMERIC",
                    trace_id=format(current.trace_id,"032x"),observation_id=format(current.span_id,"016x"),
                    metadata={"case_id":case_id,"content_capture":False})
            except Exception:logger.warning("Evaluation score export unavailable")


def result_values(result):
    values={}
    if isinstance(result,list):values["count"]=len(result)
    for key in ("model","usage","model_calls","tool_calls","usage_complete","error_code"):
        value=getattr(result,key,None)
        if value is not None:values[key]=value
    results=getattr(result,"results",None)
    if isinstance(results,list):values["result_count"]=len(results)
    return values


def observed(name: str,kind: str = "span"):
    """Async methods and iterators; no context token remains attached across yields."""
    def decorate(function):
        if inspect.isasyncgenfunction(function):
            @functools.wraps(function)
            async def stream(*args,**kwargs):
                telemetry=active_telemetry.get()
                if telemetry is None or not telemetry.enabled:
                    source=function(*args,**kwargs)
                    try:
                        async for event in source:yield event
                    finally:await source.aclose()
                    return
                span=telemetry.begin(name,kind);parent=trace.set_span_in_context(span)
                source=function(*args,**kwargs);start=time.monotonic();error=None;values={}
                if args and hasattr(args[0],"settings"):values["model"]=args[0].settings.llm_model
                try:
                    while True:
                        token=context.attach(parent)
                        try:event=await anext(source)
                        except StopAsyncIteration:break
                        finally:context.detach(token)
                        if isinstance(event,tuple) and len(event)==2:
                            event_name,data=event
                            if event_name=="complete":values.update(result_values(data))
                            elif event_name=="usage":values["usage"]=data.get("usage",data)
                            elif event_name=="done":values["usage_complete"]=True
                            elif event_name=="error":values["error_code"]=(data.get("error") or {}).get("code","GENERATION_FAILED")
                        yield event
                except BaseException as caught:error=caught;raise
                finally:
                    try:await source.aclose()
                    except BaseException as caught:
                        error=error or caught;raise
                    finally:telemetry.finish(span,name,start,error,values)
            return stream
        @functools.wraps(function)
        async def run(*args,**kwargs):
            telemetry=active_telemetry.get()
            if telemetry is None or not telemetry.enabled:return await function(*args,**kwargs)
            span=telemetry.begin(name,kind);start=time.monotonic();error=None;result=None
            if name=="tool.execute" and len(args)>1:
                tool=args[0].registry.get(args[1].function.name)
                if tool:span.set_attribute("gen_ai.tool.name",tool.name)
            if name=="agent.run" and len(args)>1:
                span.set_attribute("learntrail.run_id",str(args[1].run.id))
            with trace.use_span(span,end_on_exit=False,record_exception=False,set_status_on_exception=False):
                try:result=await function(*args,**kwargs);return result
                except BaseException as caught:error=caught;raise
                finally:
                    values=result_values(result)
                    if kind=="embedding" and values.get("usage") is not None:
                        usage=values["usage"].model_dump();usage["completion_tokens"]=0;values["usage"]=usage
                    telemetry.finish(span,name,start,error,values)
        return run
    return decorate


class TraceMiddleware:
    """Pure ASGI: spans remain alive until the SSE body closes, including disconnects."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        telemetry=getattr(scope.get("app").state,"telemetry",None) if scope.get("app") else None
        if scope["type"]!="http" or telemetry is None or not telemetry.enabled:
            await self.app(scope,receive,send);return
        span=telemetry.begin("http.request");start=time.monotonic();status=500;error=None
        async def traced_send(message):
            nonlocal status
            if message["type"]=="http.response.start":
                status=message["status"]
                message={**message,"headers":[*message.get("headers",[]),(b"x-trace-id",format(span.get_span_context().trace_id,"032x").encode())]}
            await send(message)
        with telemetry.activate(),trace.use_span(span,end_on_exit=False,record_exception=False,set_status_on_exception=False):
            try:await self.app(scope,receive,traced_send)
            except BaseException as caught:error=caught;raise
            finally:
                route=getattr(scope.get("route"),"path",None)
                # Route templates only, never URL/query strings or client trace/baggage.
                if route:span.set_attribute("http.route",route)
                telemetry.finish(span,"http.request",start,error,{"http.status_code":status,"error_code":"HTTP_ERROR" if status>=400 else None})
