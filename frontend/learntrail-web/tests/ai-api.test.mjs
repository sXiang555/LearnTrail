import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'

const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
const { session } = await import('../src/api.js')
const { readAiEvents, listAiSessions, streamAiChat } = await import('../src/aiApi.js')
const token = () => 'test.' + Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 300 })).toString('base64url') + '.test'
const runId = '11111111-1111-4111-8111-111111111111'
const sessionId = '22222222-2222-4222-8222-222222222222'
const event = (name, data, newline = '\n') => 'event: ' + name + newline + 'data: ' + JSON.stringify(data) + newline + newline
const meta = event('meta', { run_id: runId, session_id: sessionId })
const done = event('done', { run_id: runId, status: 'succeeded' })
function response(wire, bytewise = false) {
  const bytes = new TextEncoder().encode(wire)
  return new Response(new ReadableStream({ start(controller) {
    if (bytewise) for (const value of bytes) controller.enqueue(new Uint8Array([value]))
    else controller.enqueue(bytes)
    controller.close()
  } }), { headers: { 'content-type': 'text/event-stream; charset=utf-8' } })
}
beforeEach(() => { storage.clear(); session.token = token(); session.user = { id: '41' }; session.audience = 'student' })

test('SSE 中文逐字节与 CRLF 分包保持原文、Usage 和结束事件', async () => {
  const wire = ': heartbeat\r\n\r\n' + meta.replaceAll('\n', '\r\n') +
    event('delta', { content: '你好，学途！' }, '\r\n') + event('usage', { total_tokens: 8 }) + done
  const events = []
  await readAiEvents(response(wire, true), (name, data) => events.push([name, data]))
  assert.deepEqual(events.map(item => item[0]), ['meta', 'delta', 'usage', 'done'])
  assert.equal(events[1][1].content, '你好，学途！')
})

for (const [label, wire] of [
  ['未收到完成事件', meta + event('delta', { content: '半句' })],
  ['增量先于 meta', event('delta', { content: '半句' }) + done],
  ['错误的 Usage', meta + event('usage', { total_tokens: -1 }) + done],
  ['结束任务不匹配', meta + event('done', { run_id: sessionId, status: 'succeeded' })],
  ['不认识的事件', meta + event('unexpected', {})],
  ['非法 JSON', meta + 'event: delta\ndata: not-json\n\n']
]) test('拒绝异常流：' + label, async () => {
  await assert.rejects(readAiEvents(response(wire), () => {}), error => ['INVALID_STREAM', 'STREAM_INTERRUPTED'].includes(error.code))
})

test('服务端错误事件不会被当作正常完成', async () => {
  await assert.rejects(readAiEvents(response(meta + event('error', { error: { code: 'RUN_CANCELLED', message: '已停止生成' } })), () => {}),
    error => error.code === 'RUN_CANCELLED')
})

test('Python 请求使用 Bearer、独立代理和 omit cookie', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/ai/v1/sessions?limit=20&offset=0')
    assert.equal(options.headers.Authorization, 'Bearer ' + session.token)
    assert.equal(options.credentials, 'omit')
    return Response.json({ items: [], total: 0 })
  }
  assert.deepEqual(await listAiSessions(), { items: [], total: 0 })
})

test('仅在开流前 401 时复用 Java 刷新并重试一次', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push(url)
    if (url.startsWith('/api/')) return Response.json({ code: 200, data: token() })
    assert.equal(options.credentials, 'omit')
    return calls.length === 1 ? Response.json({ error: { code: 'TOKEN_EXPIRED' } }, { status: 401 }) : response(meta + done)
  }
  await streamAiChat(sessionId, '你好', () => {})
  assert.deepEqual(calls, ['/ai/v1/sessions/' + sessionId + '/chat/stream', '/api/as/accounts/refresh?audience=student', '/ai/v1/sessions/' + sessionId + '/chat/stream'])
})

test('已经收到增量后断流，不自动重发模型请求', async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return response(meta + event('delta', { content: '半句' })) }
  await assert.rejects(streamAiChat(sessionId, '你好', () => {}), error => error.code === 'STREAM_INTERRUPTED')
  assert.equal(calls, 1)
})

test('协作模式把助手选项发到 Agent 接口并读取分派、节点与最终回复', async () => {
  const received = []
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/ai/v1/sessions/' + sessionId + '/agent/stream')
    assert.deepEqual(JSON.parse(options.body), {
      content: '先盘点，再写大纲', pause_before_tools: false, assistant: 'collaborate'
    })
    return response(meta +
      event('delegation', { assistant: 'collaborate', specialists: ['operations', 'creator'] }) +
      event('node', { node: 'operations', status: 'running' }) +
      event('node', { node: 'creator', status: 'running' }) +
      event('node', { node: 'synthesis', status: 'running' }) +
      event('delta', { content: '汇总后的建议' }) + done)
  }
  await streamAiChat(sessionId, '先盘点，再写大纲',
    (kind, data) => received.push([kind, data]), undefined, { mode: 'agent', assistant: 'collaborate' })
  assert.deepEqual(received.filter(([kind]) => kind === 'node').map(([, data]) => data.node),
    ['operations', 'creator', 'synthesis'])
  assert.equal(received.find(([kind]) => kind === 'delta')[1].content, '汇总后的建议')
})

test('切换用户后丢弃上一用户的会话响应', async () => {
  globalThis.fetch = async () => { session.user = { id: '42' }; return Response.json({ items: [{ id: sessionId }] }) }
  await assert.rejects(listAiSessions(), error => error.code === 'SESSION_CHANGED')
})

test('主动中断会关闭读取器并拒绝报告完成', async () => {
  const abort = new AbortController()
  let cancelled = false
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(meta)) }, cancel() { cancelled = true } })
  await assert.rejects(readAiEvents(new Response(stream, { headers: { 'content-type': 'text/event-stream' } }),
    () => abort.abort(), abort.signal), error => error.name === 'AbortError')
  assert.equal(cancelled, true)
})
