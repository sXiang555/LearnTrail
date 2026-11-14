import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRenderer, reactive } from 'vue'
import { couponRule, newOrderKey } from '../src/pointsCommerce.js'

test('积分券保持整数积分，比例券显示实际折扣和上限', () => {
  assert.equal(couponRule({ discountType: 3, discountValue: 20 }), '抵扣 20 积分')
  assert.equal(couponRule({ discountType: 4, discountValue: 30, thresholdAmount: 100 }), '满 100 积分，抵扣 30 积分')
  assert.equal(couponRule({ discountType: 2, discountValue: 80, thresholdAmount: 100, maxDiscountAmount: 25 }), '满 100 积分，8 折，最多抵扣 25 积分')
  assert.equal(couponRule({ discountType: 2, discountValue: 85 }), '8.5 折')
})
test('订单重试键可用于普通 HTTP 页面，不依赖 randomUUID', () => {
  const keys = Array.from({ length: 100 }, newOrderKey)
  assert.equal(new Set(keys).size, 100)
  assert.ok(keys.every(key => /^[a-f0-9]{32}$/.test(key)))
})

const renderer = createRenderer({
  insert() {}, remove() {}, createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {}
})
let serial = 0
const flush = () => new Promise(resolve => setImmediate(resolve))
async function harness(api) {
  const previous = { document: globalThis.document, setInterval, clearInterval }
  const document = new EventTarget(); document.hidden = false
  globalThis.document = document
  let tick, cleared = false
  globalThis.setInterval = callback => { tick = callback; return 1 }
  globalThis.clearInterval = () => { cleared = true }
  const session = reactive({ token: 'test', user: { id: 1 }, balance: 1000 })
  const notices = []
  globalThis.__ltLearningTest = { api, session, notify: message => notices.push(message) }
  let source = await readFile(new URL('../src/composables/useLearningReward.js', import.meta.url), 'utf8')
  source = source.replace("from 'vue'", 'from ' + JSON.stringify(import.meta.resolve('vue')))
    .replace("import { api, notify, session } from '../api'", 'const { api, notify, session } = globalThis.__ltLearningTest')
  const { useLearningReward } = await import('data:text/javascript;base64,' + Buffer.from(source + '\n// ' + serial++).toString('base64'))
  let state
  const app = renderer.createApp({ setup() { state = useLearningReward({ kind: 'READ', target: () => 10, enabled: () => true, position: () => .9 }); return () => null } })
  app.mount({})
  return {
    document, state, session, notices, tick: () => tick(), cleared: () => cleared,
    unmount: () => app.unmount(), restore() {
      globalThis.document = previous.document; globalThis.setInterval = previous.setInterval; globalThis.clearInterval = previous.clearInterval
      delete globalThis.__ltLearningTest
    }
  }
}
test('页面隐藏时停止心跳，组件卸载清理定时器', async () => {
  const calls = []
  const h = await harness(async (path, options) => { calls.push([path, options]); return { activityId: '5', seconds: 0, requiredSeconds: 30 } })
  try {
    await flush(); await h.tick(); assert.equal(calls.length, 2)
    h.document.hidden = true; h.document.dispatchEvent(new Event('visibilitychange'))
    await h.tick(); assert.equal(calls.length, 2)
    h.unmount(); assert.equal(h.cleared(), true)
    await h.tick(); assert.equal(calls.length, 2)
  } finally { h.restore() }
})
test('卸载后的迟到奖励响应不改余额或弹出通知', async () => {
  let resolve
  const pending = new Promise(done => { resolve = done })
  const h = await harness(() => pending)
  try {
    h.unmount(); resolve({ activityId: '5', awarded: 20, balance: 1020 }); await flush()
    assert.equal(h.session.balance, 1000); assert.deepEqual(h.notices, [])
  } finally { h.restore() }
})
