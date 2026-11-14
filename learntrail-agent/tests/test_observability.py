"""Real OTel SDK/export transports to a local fixture; no external telemetry service."""

import asyncio
import gzip
import json
import threading
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from learntrail_agent.errors import AppError
from learntrail_agent.main import create_app
from learntrail_agent.observability import Telemetry,Pricing,observed
from learntrail_agent.schemas import ChatResponse,Usage


def test_price_manifest_unknown_partial_and_estimate(tmp_path):
    path=tmp_path/"prices.json"
    path.write_text(json.dumps({"version":"test-rates-not-provider-pricing","models":{"fixture-model":{
        "input_per_million":"1.5","output_per_million":"3","currency":"CNY"}}}))
    rates=Pricing(path)
    assert rates.estimate("fixture-model",Usage(prompt_tokens=100,completion_tokens=50))["amount"]=="0.0003"
    for model,usage,complete in [("missing",Usage(prompt_tokens=1,completion_tokens=1),True),("fixture-model",None,True),("fixture-model",Usage(total_tokens=10),True),("fixture-model",Usage(prompt_tokens=1,completion_tokens=1),False)]:
        assert rates.estimate(model,usage,complete=complete)["amount"] is None
    path.write_text(path.read_text().replace('"1.5"','"NaN"'))
    with pytest.raises(ValueError):Pricing(path)


@pytest.mark.asyncio
async def test_spans_parent_child_metrics_and_no_sensitive_payload(settings,tmp_path):
    exporter=InMemorySpanExporter();reader=InMemoryMetricReader()
    telemetry=Telemetry(settings.model_copy(update={"observability_enabled":True,"observability_dir":tmp_path}),exporter=exporter,metric_reader=reader)
    @observed("llm.completion","generation")
    async def child(secret):
        return ChatResponse(id="fixture",model="fixture-model",content=secret,finish_reason="stop",usage=Usage(prompt_tokens=2,completion_tokens=3,total_tokens=5))
    @observed("parent")
    async def parent():return await child("PRIVATE-JWT-PROMPT-KEY-PASSWORD")
    try:
        with telemetry.activate():await parent()
        telemetry.provider.force_flush()
        spans=exporter.get_finished_spans();assert len(spans)==2
        child_span,parent_span=spans
        assert child_span.parent.span_id==parent_span.context.span_id
        assert child_span.attributes["learntrail.usage.total_tokens"]==5
        assert "PRIVATE" not in str([dict(s.attributes) for s in spans])
        metric_data=reader.get_metrics_data()
        assert metric_data.resource_metrics
        points=[p for resource in metric_data.resource_metrics for scope in resource.scope_metrics
            for metric in scope.metrics if metric.name=='learntrail.model.tokens' for p in metric.data.data_points
            if p.attributes['token.type']=='total_tokens']
        assert sum(point.value for point in points)==5
    finally:telemetry.close()


@pytest.mark.asyncio
async def test_errors_fixed_codes_and_iterator_cancellation_no_context_leak(settings,tmp_path):
    exporter=InMemorySpanExporter();telemetry=Telemetry(settings.model_copy(update={"observability_enabled":True,"observability_dir":tmp_path}),exporter=exporter)
    @observed("bad")
    async def fail():raise AppError("LLM_TIMEOUT","PRIVATE PROVIDER BODY AND KEY",504)
    @observed("stream")
    async def stream():
        yield "delta",{"content":"PRIVATE RAW CONTENT"}
        await asyncio.Event().wait()
    try:
        with telemetry.activate():
            with pytest.raises(AppError):await fail()
            source=stream();await anext(source)
            # Close in a distinct task, matching ASGI cancellation behavior.
            await asyncio.create_task(source.aclose())
        telemetry.provider.force_flush()
        spans=exporter.get_finished_spans()
        assert len(spans)==2 and spans[0].attributes["error.type"]=="LLM_TIMEOUT"
        assert spans[1].attributes["error.type"]=="CANCELLED"
        assert "PRIVATE" not in str([dict(s.attributes) for s in spans])
    finally:telemetry.close()


@pytest.mark.asyncio
async def test_asgi_sse_and_trace_header(settings,tmp_path,respx_mock):
    config=settings.model_copy(update={"observability_enabled":True,"observability_dir":tmp_path})
    app=create_app(config)
    from fastapi.responses import StreamingResponse
    @app.get('/fixture/stream')
    async def stream_fixture():
        async def events():
            yield 'event: delta\ndata: {"content":"PRIVATE STREAM BODY"}\n\n'
            await asyncio.sleep(.03)
            yield 'event: done\ndata: {}\n\n'
        return StreamingResponse(events(),media_type='text/event-stream')
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1") as http:
            response=await http.get('/v1/health?secret=DO_NOT_EXPORT')
            assert response.status_code==200 and len(response.headers["x-trace-id"])==32
            streamed=await http.get('/fixture/stream')
            assert 'event: done' in streamed.text
        app.state.telemetry.provider.force_flush()
    rows=[json.loads(line) for line in (tmp_path/"spans.jsonl").read_text().splitlines()]
    assert rows[0]["attributes"]["http.route"]=="/v1/health"
    assert rows[-1]["attributes"]["http.route"]=="/fixture/stream" and rows[-1]['duration_ms']>=25
    assert "secret" not in json.dumps(rows)
    assert "PRIVATE STREAM" not in json.dumps(rows)


def test_actual_otlp_and_langfuse_sdk_local_receiver(settings,tmp_path):
    received=[]
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
            if self.headers.get('Content-Encoding')=='gzip':raw=gzip.decompress(raw)
            received.append((self.path,raw))
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    config=settings.model_copy(update={"observability_enabled":True,"observability_dir":tmp_path,"otel_exporter_otlp_endpoint":base,
        "langfuse_enabled":True,"langfuse_base_url":base})
    from pydantic import SecretStr
    config.langfuse_public_key=SecretStr('pk-lf-local-protocol-test');config.langfuse_secret_key=SecretStr('sk-lf-local-protocol-test')
    telemetry=Telemetry(config)
    try:
        import time
        span=telemetry.begin('llm.completion','generation')
        telemetry.finish(span,'llm.completion',time.monotonic(),values={"model":"fixture-model","usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}})
        telemetry.provider.force_flush();telemetry.meters.force_flush()
        paths=[p for p,_ in received]
        assert '/v1/traces' in paths and '/v1/metrics' in paths
        assert any('api/public/otel' in p for p in paths)
        for path,raw in received:
            if path.endswith('/traces'):
                payload=ExportTraceServiceRequest.FromString(raw)
                assert payload.resource_spans
                assert b'PRIVATE' not in raw
    finally:
        telemetry.close();server.shutdown();server.server_close();thread.join(timeout=3)


def test_disabled_telemetry_creates_no_files(settings,tmp_path):
    telemetry=Telemetry(settings.model_copy(update={"observability_dir":tmp_path/'disabled'}))
    assert telemetry.begin('unused') is None
    telemetry.close();assert not (tmp_path/'disabled').exists()


@pytest.mark.asyncio
async def test_experiment_shares_provider_across_app_lifecycles(settings,tmp_path,respx_mock):
    config=settings.model_copy(update={"observability_enabled":True,"observability_dir":tmp_path})
    exporter=InMemorySpanExporter();shared=Telemetry(config,exporter=exporter)
    try:
        for _ in range(2):
            app=create_app(config,telemetry=shared)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as http:
                    assert (await http.get('/v1/health')).status_code==200
        shared.provider.force_flush()
        assert len(exporter.get_finished_spans())==2
    finally:shared.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('activate_disabled',[False,True])
async def test_disabled_stream_closes_underlying_iterator_immediately(settings,activate_disabled):
    closed=asyncio.Event();telemetry=Telemetry(settings)
    @observed('disabled.stream')
    async def source():
        try:
            yield 'delta',{'content':'test'}
            await asyncio.Event().wait()
        finally:closed.set()
    with telemetry.activate() if activate_disabled else nullcontext():
        stream=source();await anext(stream);await stream.aclose()
        assert closed.is_set()
    telemetry.close()


def test_ambient_otel_resource_attributes_are_not_exported(settings,tmp_path,monkeypatch):
    monkeypatch.setenv('OTEL_RESOURCE_ATTRIBUTES','private.configuration=DO_NOT_EXPORT')
    telemetry=Telemetry(settings.model_copy(update={'observability_enabled':True,'observability_dir':tmp_path}))
    try:
        assert 'private.configuration' not in telemetry.provider.resource.attributes
        assert telemetry.provider.resource.attributes['service.name']=='learntrail-agent'
    finally:telemetry.close()


@pytest.mark.asyncio
async def test_sdk_metrics_export_locally_without_collector(settings,tmp_path):
    config=settings.model_copy(update={'observability_enabled':True,'observability_dir':tmp_path,
        'otel_metric_export_interval_ms':60000})
    telemetry=Telemetry(config)
    @observed('llm.completion','generation')
    async def successful():
        return ChatResponse(id='fixture',model='fixture-model',content='PRIVATE MESSAGE NOT METRICS',
            finish_reason='stop',usage=Usage(prompt_tokens=2,completion_tokens=3,total_tokens=5))
    @observed('fixture.failure')
    async def failed():raise AppError('LLM_TIMEOUT','PRIVATE UPSTREAM CONTENT',504)
    try:
        with telemetry.activate():
            await successful()
            with pytest.raises(AppError):await failed()
        telemetry.meters.force_flush()
        telemetry.provider.force_flush()
        path=tmp_path/'metrics.jsonl'
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        metrics=[m for row in rows for resource in row['data']['resource_metrics']
            for scope in resource['scope_metrics'] for m in scope['metrics']]
        assert {'learntrail.operations','learntrail.operation.duration','learntrail.model.tokens'}<={m['name'] for m in metrics}
        operations=[p for m in metrics if m['name']=='learntrail.operations' for p in m['data']['data_points']]
        assert any(p['attributes']=={'operation':'fixture.failure','outcome':'error'} and p['value']==1 for p in operations)
        assert 'PRIVATE' not in path.read_text()
        assert path.stat().st_mode&0o077==0
    finally:telemetry.close()


def test_local_metric_exporter_rotates_and_rejects_symlink(settings,tmp_path):
    from learntrail_agent.observability import PrivateMetricExporter
    from opentelemetry.sdk.metrics.export import MetricExportResult
    from types import SimpleNamespace
    exporter=PrivateMetricExporter(tmp_path,10)
    data=SimpleNamespace(to_json=lambda:json.dumps({'resource_metrics':[]}))
    assert exporter.export(data)==MetricExportResult.SUCCESS
    assert exporter.export(data)==MetricExportResult.SUCCESS
    assert (tmp_path/'metrics.previous.jsonl').exists()
    target=tmp_path/'preserve.txt';target.write_text('keep')
    blocked=tmp_path/'symlink';blocked.mkdir();(blocked/'metrics.jsonl').symlink_to(target)
    assert PrivateMetricExporter(blocked,10000).export(data)==MetricExportResult.FAILURE
    assert target.read_text()=='keep'


def test_credential_shaped_model_is_not_exported(settings,tmp_path):
    import time
    exporter=InMemorySpanExporter()
    telemetry=Telemetry(settings.model_copy(update={'observability_enabled':True,'observability_dir':tmp_path}),exporter=exporter)
    try:
        span=telemetry.begin('llm.completion','generation')
        telemetry.finish(span,'llm.completion',time.monotonic(),values={'model':'sk-test-only-misplaced-secret'})
        telemetry.provider.force_flush()
        attributes=dict(exporter.get_finished_spans()[0].attributes)
        assert 'gen_ai.request.model' not in attributes
        assert 'langfuse.observation.model.name' not in attributes
        assert 'sk-test-only' not in json.dumps(attributes)
    finally:telemetry.close()
