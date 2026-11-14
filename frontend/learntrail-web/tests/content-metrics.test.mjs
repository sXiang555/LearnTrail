import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
const dom = new JSDOM('<div id="app"></div>', { url: 'http://localhost:5173/' })
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'sessionStorage']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
}
const { createApp, h, reactive, nextTick } = await import('vue')
const { session } = await import('../src/api.js')
const { useContentView } = await import('../src/composables/useContentView.js')
after(() => dom.window.close())
async function fixture({ video = false, anonymous = false } = {}) {
  let tick, now = 0, cleared = false, hidden = false, calls = [], result, failed = false
  const saved = { setInterval, clearInterval, performance }
  globalThis.setInterval = fn => { tick = fn; return 11 }
  globalThis.clearInterval = id => { assert.equal(id, 11); cleared = true }
  Object.defineProperty(globalThis, 'performance', { configurable: true, value: { now: () => now } })
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  session.user = anonymous ? null : { id: '42' }
  session.token = anonymous ? '' : 't.' + Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 300 })).toString('base64url') + '.t'
  const state = reactive({ id: '1', playing: true, position: 0 })
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options.method })
    if (failed) return Response.json({ code: 503, msg: '统计离线' })
    return Response.json({ code: 200, data: { viewTimes: '8', counted: true } })
  }
  let control
  const app = createApp({ setup() {
    control = useContentView({ kind: video ? 'SERIES' : 'ARTICLE', target: () => state.id,
      enabled: () => state.playing, position: video ? () => state.position : undefined,
      onCount: value => { result = value } })
    return () => h('p', control.error.value)
  } })
  app.mount('#app'); await nextTick()
  const step = async (seconds = 1, moved = seconds) => { now += seconds * 1000; state.position += moved; await tick(); await nextTick() }
  return { state, calls, step, control,
    hide: value => { hidden = value; document.dispatchEvent(new window.Event('visibilitychange')) },
    fail: value => { failed = value }, result: () => result, cleared: () => cleared,
    dispose() { app.unmount() },
    restore() { app.unmount(); globalThis.setInterval = saved.setInterval; globalThis.clearInterval = saved.clearInterval;
      Object.defineProperty(globalThis, 'performance', { configurable: true, value: saved.performance }) }
  }
}
test('文章满5秒才上报，同一页面成功后不重复计数', async () => {
  const f = await fixture()
  try {
    for (let n = 0; n < 5; n++) await f.step()
    assert.equal(f.calls.length, 0)
    await f.step(); assert.equal(f.calls.length, 1); assert.equal(f.result(), '8')
    assert.deepEqual(f.calls[0], { url: '/api/rs/likes/content/ARTICLE/1/view', method: 'POST' })
    await f.step(20); assert.equal(f.calls.length, 1)
  } finally { f.restore() }
})
test('游客不写入浏览量', async () => {
  const f = await fixture({ anonymous: true })
  try { for (let n = 0; n < 10; n++) await f.step(); assert.equal(f.calls.length, 0) } finally { f.restore() }
})
test('后台、暂停和进度跳跃不累计为有效播放', async () => {
  const f = await fixture({ video: true })
  try {
    await f.step(); f.hide(true)
    for (let n = 0; n < 7; n++) await f.step()
    f.hide(false); f.state.playing = false
    for (let n = 0; n < 7; n++) await f.step()
    f.state.playing = true; await f.step(); await f.step(1, 30)
    assert.equal(f.calls.length, 0)
    for (let n = 0; n < 5; n++) await f.step()
    assert.equal(f.calls.length, 1)
    assert.match(f.calls[0].url, /SERIES\/1\/view$/)
  } finally { f.restore() }
})
test('切换内容重置时长，卸载清理定时器', async () => {
  const f = await fixture()
  try {
    for (let n = 0; n < 5; n++) await f.step()
    f.state.id = '2'; await nextTick(); await f.step()
    assert.equal(f.calls.length, 0)
    for (let n = 0; n < 5; n++) await f.step()
    assert.match(f.calls[0].url, /ARTICLE\/2\/view$/)
    f.dispose(); assert.equal(f.cleared(), true)
    await f.step(); assert.equal(f.calls.length, 1)
  } finally { f.restore() }
})
test('统计失败有错误反馈，显式重试成功后才更新', async () => {
  const f = await fixture()
  try {
    f.fail(true); for (let n = 0; n < 6; n++) await f.step()
    assert.match(f.control.error.value, /统计离线/); assert.equal(f.result(), undefined)
    await f.step(); assert.equal(f.calls.length, 1)
    f.fail(false); await f.control.retry(); assert.equal(f.calls.length, 2)
    assert.equal(f.control.error.value, ''); assert.equal(f.result(), '8')
  } finally { f.restore() }
})
