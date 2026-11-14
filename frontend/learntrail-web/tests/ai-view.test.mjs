// Real Vue components with mocked HTTP. This is not database/model integration.
import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'

const dom = new JSDOM('<div id="app"></div>', { url: 'http://localhost:5173/' })
for (const key of ['window', 'document', 'Document', 'ShadowRoot', 'Event', 'history', 'location', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'sessionStorage']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
}
const { createApp, h, nextTick } = await import('vue')
const { createRouter, createMemoryHistory, RouterView } = await import('vue-router')
const { session } = await import('../src/api.js')
after(() => dom.window.close())
const flush = () => new Promise(resolve => setImmediate(resolve))
const sessionId = '22222222-2222-4222-8222-222222222222'
const runId = '11111111-1111-4111-8111-111111111111'
const now = new Date().toISOString()
let sequence = 0

async function componentUrl(relative) {
  const filename = new URL(relative, import.meta.url)
  const { descriptor } = parse(await readFile(filename, 'utf8'), { filename: filename.pathname })
  let code = compileScript(descriptor, { id: 'ai-test-' + sequence++, inlineTemplate: true }).content
  const imports = [...code.matchAll(/from\s+['"]([^'"]+)['"]/g)]
  for (const item of imports) {
    const specifier = item[1]
    const target = specifier.endsWith('.vue') ? await componentUrl(new URL(specifier, filename).href) :
      specifier.startsWith('.') ? new URL(specifier, filename).href : import.meta.resolve(specifier)
    code = code.replace(item[0], 'from ' + JSON.stringify(target))
  }
  return 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
}
const { default: AiAssistant } = await import(await componentUrl('../src/views/AiAssistant.vue'))
function frame(name, data) { return 'event: ' + name + '\ndata: ' + JSON.stringify(data) + '\n\n' }
function streamResponse(answer, hold = false) {
  const wire = frame('meta', { run_id: runId, session_id: sessionId }) + frame('delta', { content: answer }) +
    (hold ? '' : frame('done', { run_id: runId, status: 'succeeded', duration_ms: 12, usage: { total_tokens: 9 } }))
  return new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode(wire))
    if (!hold) controller.close()
  } }), { headers: { 'content-type': 'text/event-stream' } })
}
function fixture({ hold = false } = {}) {
  let messages = [], runs = [], requestBodies = []
  globalThis.fetch = async (url, options) => {
    if (url.includes('/chat/stream')) {
      const body = JSON.parse(options.body)
      requestBodies.push(body)
      const index = messages.length
      messages.push({ id: 'user-' + index, run_id: runId, role: 'user', content: body.content, status: hold ? 'pending' : 'completed', created_at: now })
      if (!hold) messages.push({ id: 'assistant-' + index, run_id: runId, role: 'assistant', content: '<img src=x onerror=alert(1)>回答', status: 'completed', created_at: now })
      runs = [{ id: runId, status: hold ? 'running' : 'succeeded', duration_ms: 12, usage: { total_tokens: 9 } }]
      return streamResponse(hold ? '尚未完成的回答' : '<img src=x onerror=alert(1)>回答', hold)
    }
    if (url.endsWith('/cancel')) {
      runs[0].status = 'cancelled'; messages[0].status = 'failed'
      return Response.json(runs[0])
    }
    if (url.startsWith('/ai/v1/sessions?')) return Response.json({ items: [{ id: sessionId, title: '测试会话', created_at: now, updated_at: now }], total: 1 })
    if (url.includes('/sessions/' + sessionId + '?')) return Response.json({
      id: sessionId, title: '测试会话', created_at: now, updated_at: now, messages,
      total_messages: messages.length, message_limit: 100, message_offset: 0, recent_runs: runs
    })
    throw new Error('Unexpected test HTTP route: ' + url)
  }
  return { requestBodies }
}
async function mount() {
  session.token = 'test.' + Buffer.from(JSON.stringify({ exp: Math.floor(Date.now()/1000)+300 })).toString('base64url') + '.test'
  session.user = { id: '41' }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', redirect: '/ai/' + sessionId },
    { path: '/ai/:sessionId?', component: AiAssistant }, { path: '/login', component: { render: () => h('p', '登录') } }
  ] })
  const app = createApp({ render: () => h(RouterView, null, { default: ({ Component, route }) => Component && h(Component, { key: route.fullPath }) }) })
  app.use(router)
  await router.push('/ai/' + sessionId)
  await router.isReady()
  app.mount('#app')
  await flush(); await nextTick()
  return { app, router }
}
async function submit(content) {
  const input = document.querySelector('textarea')
  input.value = content; input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  await nextTick()
  document.querySelector('.composer').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
  await flush(); await nextTick()
}

test('实际 Vue 组件连续发送、显示流式文本、刷新后读取持久化 API 历史', async () => {
  const backend = fixture()
  let mounted = await mount()
  try {
    await submit('第一问')
    assert.match(document.body.textContent, /第一问/)
    assert.match(document.body.textContent, /<img src=x onerror=alert\(1\)>回答/)
    assert.equal(document.querySelector('img'), null)
    await submit('继续解释')
    assert.deepEqual(backend.requestBodies, [{ content: '第一问' }, { content: '继续解释' }])
    mounted.app.unmount()
    mounted = await mount()
    assert.match(document.body.textContent, /第一问/)
    assert.match(document.body.textContent, /继续解释/)
    session.user = { id: '42' }
    globalThis.fetch = async () => Response.json({ error: { code: 'SESSION_NOT_FOUND', message: '会话不存在' } }, { status: 404 })
    await flush(); await nextTick()
    assert.doesNotMatch(document.body.textContent, /第一问/)
  } finally { mounted.app.unmount() }
})

test('停止真实 Vue 流式请求，部分回答标为未完成且刷新后不作为成功历史', async () => {
  fixture({ hold: true })
  let mounted = await mount()
  try {
    await submit('请展开解释')
    const stop = [...document.querySelectorAll('button')].find(button => button.textContent.trim() === '停止生成')
    assert.ok(stop)
    stop.click()
    await flush(); await nextTick()
    assert.match(document.body.textContent, /已停止/)
    assert.match(document.body.textContent, /不纳入后续对话/)
    mounted.app.unmount()
    mounted = await mount()
    assert.doesNotMatch(document.body.textContent, /尚未完成的回答/)
    assert.match(document.body.textContent, /本轮未完成/)
  } finally { mounted.app.unmount() }
})

test('聊天输入 Enter 发送，Shift+Enter 和中文输入法选词不误发送', async () => {
  const backend = fixture(), mounted = await mount()
  try {
    const input = document.querySelector('#ai-question')
    input.value = '解释异步编程'; input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
    await nextTick()
    for (const extra of [{ shiftKey: true }, { isComposing: true }, { keyCode: 229 }]) {
      const event = new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...extra })
      input.dispatchEvent(event)
      assert.equal(event.defaultPrevented, false)
    }
    assert.equal(backend.requestBodies.length, 0)
    const enter = new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    input.dispatchEvent(enter); await flush(); await nextTick()
    assert.equal(enter.defaultPrevented, true)
    assert.deepEqual(backend.requestBodies, [{ content: '解释异步编程' }])
  } finally { mounted.app.unmount() }
})

function agentFixture(paused = false) {
  const citation = { slug: 'article-test', title: '事务与幂等', ordinal: 0, quote: '<script>不会执行</script>' }
  const events = [
    { kind: 'tool_start', payload: { call_id: 'tool-a', name: 'search_knowledge_base' } },
    { kind: 'tool_end', payload: { call_id: 'tool-a', name: 'search_knowledge_base', ok: true, duration_ms: 18 } }
  ]
  const user = { id: 'user-agent', run_id: runId, role: 'user', content: '查找知识', status: 'pending', created_at: now }
  let messages = paused ? [user] : []
  let runs = paused ? [{ id: runId, kind: 'agent', status: 'paused', usage: { total_tokens: 3 } }] : []
  const requests = []
  let complete
  globalThis.fetch = async (url, options) => {
    if (url === '/ai/v1/skills') return Response.json({ items: [{ name: 'knowledge-learning', description: '文章知识学习' }], mcp_servers: [] })
    if (url.includes('/approvals')) return Response.json({ items: [] })
    if (url.endsWith('/agent/stream') || url.endsWith('/resume/stream')) {
      requests.push({ url, body: JSON.parse(options.body) })
      messages = [user]; runs = [{ id: runId, kind: 'agent', status: 'running' }]
      return new Response(new ReadableStream({ start(controller) {
        controller.enqueue(new TextEncoder().encode(frame('meta', { run_id: runId, session_id: sessionId, resumed: paused }) +
          frame('node', { node: 'tools', status: 'running' }) +
          events.map(item => frame(item.kind, item.payload)).join('') + frame('delta', { content: '检索结果' })))
        complete = () => {
          user.status = 'completed'
          messages.push({ id: 'assistant-agent', run_id: runId, role: 'assistant', content: '检索结果如下', status: 'completed', created_at: now, citations: [citation] })
          runs = [{ id: runId, kind: 'agent', status: 'succeeded', duration_ms: 35, usage: { total_tokens: 8 } }]
          controller.enqueue(new TextEncoder().encode(frame('delta', { content: '如下' }) + frame('citations', { items: [citation] }) +
            frame('done', { run_id: runId, status: 'succeeded', usage: { total_tokens: 8 }, citations: [citation] })))
          controller.close()
        }
      } }), { headers: { 'content-type': 'text/event-stream' } })
    }
    if (url.endsWith('/state')) return Response.json({ status: runs[0]?.status, checkpoint_id: 'checkpoint-version', next: paused ? ['gate'] : [], redacted: false })
    if (url.endsWith('/agent/runs/' + runId)) return Response.json({ events, redacted: false })
    if (url.startsWith('/ai/v1/sessions?')) return Response.json({ items: [{ id: sessionId, title: 'Agent 测试', created_at: now, updated_at: now }], total: 1 })
    if (url.includes('/sessions/' + sessionId + '?')) return Response.json({
      id: sessionId, title: 'Agent 测试', created_at: now, updated_at: now, messages,
      total_messages: messages.length, recent_runs: runs
    })
    throw new Error('Unexpected Agent test route: ' + url)
  }
  return { requests, finish: () => complete() }
}

test('Agent 模式实时展示增量、工具状态和引用，刷新恢复已保存内容', async () => {
  const backend = agentFixture()
  let mounted = await mount()
  try {
    const radio = document.querySelector('input[value="agent"]')
    radio.checked = true; radio.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
    await nextTick(); await submit('查找知识')
    assert.match(document.body.textContent, /检索结果/)
    assert.doesNotMatch(document.body.textContent, /检索结果如下/)
    assert.match(document.body.textContent, /检索文章知识/)
    assert.match(document.body.textContent, /成功/)
    assert.deepEqual(backend.requests[0].body, { content: '查找知识', pause_before_tools: false })
    assert.ok(document.querySelector('button[type="button"]').isConnected)
    backend.finish(); await flush(); await nextTick(); await flush()
    assert.match(document.body.textContent, /检索结果如下/)
    assert.match(document.body.textContent, /事务与幂等/)
    assert.equal(document.querySelector('script'), null)
    mounted.app.unmount(); mounted = await mount(); await flush(); await nextTick()
    assert.equal(document.querySelector('input[value="agent"]').checked, true)
    assert.match(document.body.textContent, /事务与幂等/)
    assert.match(document.body.textContent, /检索文章知识/)
  } finally { mounted.app.unmount() }
})

test('刷新后的暂停任务通过原 Run 恢复，不由浏览器指定 thread 或身份', async () => {
  const backend = agentFixture(true)
  const mounted = await mount()
  try {
    await flush(); await nextTick(); await flush()
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.trim() === '继续执行')
    assert.ok(button)
    button.click(); await flush(); await nextTick()
    assert.equal(backend.requests[0].url, '/ai/v1/sessions/' + sessionId + '/agent/runs/' + runId + '/resume/stream')
    assert.deepEqual(backend.requests[0].body, {})
    backend.finish(); await flush(); await nextTick()
    assert.match(document.body.textContent, /检索结果如下/)
  } finally { mounted.app.unmount() }
})

test('审批卡片刷新恢复、转义参数、提交哈希后恢复原 Run', async () => {
  const backend = agentFixture(true), fallback = globalThis.fetch
  const id = '33333333-3333-4333-8333-333333333333'
  let status = 'pending', decisions = []
  globalThis.fetch = async (url, options) => {
    if (url.includes('/approvals')) {
      if (url.endsWith('/decision')) { decisions.push(JSON.parse(options.body)); status = 'approved'; return Response.json({ status }) }
      return Response.json({ items: [{ approval_id: id, run_id: runId, session_id: sessionId, tool_name: 'save_learning_plan_demo',
        arguments_hash: 'a'.repeat(64), arguments: { title: '<img src=x onerror=alert(1)>演示计划' },
        resource: { target: 'AI 演示计划草稿' }, status, expires_at: new Date(Date.now()+60000).toISOString() }] })
    }
    return fallback(url, options)
  }
  let mounted = await mount()
  try {
    await flush(); await nextTick()
    assert.match(document.body.textContent, /等待本人确认/)
    assert.equal(document.querySelector('.approval-card img'), null)
    mounted.app.unmount(); mounted = await mount(); await flush(); await nextTick()
    const button = [...document.querySelectorAll('button')].find(item => item.textContent === '确认并继续')
    assert.ok(button)
    assert.ok([...document.querySelectorAll('button')].some(item => item.textContent === '请先处理审批卡片' && item.disabled))
    button.click(); await flush(); await flush(); await nextTick()
    assert.deepEqual(decisions, [{ action: 'approve', expected_hash: 'a'.repeat(64) }])
    assert.equal(backend.requests.length, 1)
    assert.deepEqual(backend.requests[0].body, {})
    backend.finish(); await flush(); await nextTick()
  } finally { mounted.app.unmount() }
})

test('显式 Skill 仅传名称，技能正文不来自浏览器', async () => {
  const backend = agentFixture(), mounted = await mount()
  try {
    const radio = document.querySelector('input[value="agent"]')
    radio.checked = true; radio.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
    await nextTick(); await flush(); await nextTick()
    const select = document.querySelector('#agent-skill')
    select.value = 'knowledge-learning'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
    await nextTick(); await submit('查找文章')
    assert.equal(backend.requests[0].body.skill, 'knowledge-learning')
    assert.equal(Object.hasOwn(backend.requests[0].body, 'guide'), false)
    backend.finish(); await flush()
  } finally { mounted.app.unmount() }
})

test('长期记忆管理要求明确确认、版本修改和确认删除', async () => {
  const backend = fixture(); const fallback = globalThis.fetch
  let items = [], writes = []
  globalThis.fetch = async (url, options) => {
    if (url.startsWith('/ai/v1/memories')) {
      if (options.method === 'POST' || options.method === 'PUT') {
        const body = JSON.parse(options.body); writes.push(body)
        items = [{ ...body, id: '33333333-3333-4333-8333-333333333333', source_kind: 'manual', version: (body.version || 0) + 1 }]
        return Response.json(items[0])
      }
      if (options.method === 'DELETE') { assert.match(url, /version=2$/); items = []; return Response.json({ status: 'deleted' }) }
      return Response.json({ items })
    }
    return fallback(url, options)
  }
  const mounted = await mount()
  async function input(selector, value) {
    const element = document.querySelector(selector); element.value = value
    element.dispatchEvent(new dom.window.Event('input', { bubbles: true })); await nextTick()
  }
  try {
    assert.equal(document.querySelector('form form'), null)
    await input('#ai-question', '保留这条聊天草稿，不应随记忆保存而发送')
    const panel = document.querySelector('.memory-panel'); panel.open = true
    panel.dispatchEvent(new dom.window.Event('toggle')); await flush(); await nextTick()
    await input('#memory-content', '先给结论，再给示例')
    const save = panel.querySelector('button[type="submit"]')
    assert.equal(save.disabled, true)
    const checkbox = panel.querySelector('input[type="checkbox"]'); checkbox.click(); await nextTick()
    panel.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await flush(); await nextTick()
    assert.equal(writes[0].confirmed, true); assert.equal(Object.hasOwn(writes[0], 'user_id'), false)
    assert.equal(backend.requestBodies.length, 0)
    assert.match(panel.textContent, /先给结论，再给示例/)
    ;[...panel.querySelectorAll('button')].find(button => button.textContent === '修改').click(); await nextTick()
    await input('#memory-content', '多用代码示例'); checkbox.click(); await nextTick()
    panel.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await flush(); await nextTick(); assert.equal(writes[1].version, 1)
    ;[...panel.querySelectorAll('button')].find(button => button.textContent === '删除').click(); await nextTick()
    ;[...panel.querySelectorAll('button')].find(button => button.textContent === '确认删除').click()
    await flush(); await nextTick(); assert.equal(items.length, 0)
  } finally { mounted.app.unmount() }
})
