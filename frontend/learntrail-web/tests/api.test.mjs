import test from 'node:test'
import assert from 'node:assert/strict'

const jwt = seconds => 'header.' + Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds })).toString('base64url') + '.signature'
const response = (data, code = 200) => ({ ok: true, status: 200, json: async () => ({ code, data }) })
let sequence = 0
async function client(fetcher) {
  const values = new Map([['lt_token', jwt(-60)], ['lt_user', JSON.stringify({ id: '42', name: '读者' })]])
  globalThis.sessionStorage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  globalThis.fetch = fetcher
  return import('../src/api.js?test=' + sequence++)
}

test('过期令牌先刷新，公开详情不会误按匿名身份返回', async () => {
  const fresh = jwt(300)
  const calls = []
  const api = await client(async (url, options) => {
    calls.push(url)
    if (url.includes('/refresh?')) return response(fresh)
    assert.equal(options.headers.authorization, fresh)
    return response({ owned: true })
  })
  assert.deepEqual(await api.api('/cs/studio/series/example'), { owned: true })
  assert.deepEqual(calls, ['/api/as/accounts/refresh?audience=student', '/api/cs/studio/series/example'])
})

test('钱包与公开详情并发时共用一次刷新并等待完成', async () => {
  const fresh = jwt(300)
  let release
  const pending = new Promise(resolve => { release = resolve })
  let refreshes = 0
  const api = await client(async (url, options) => {
    if (url.includes('/refresh?')) { refreshes++; await pending; return response(fresh) }
    assert.equal(options.headers.authorization, fresh)
    return response({ owned: true })
  })
  const requests = [api.api('/ls/studio-points/wallet'), api.api('/cs/studio/series/example')]
  await Promise.resolve()
  assert.equal(refreshes, 1)
  release()
  await Promise.all(requests)
  assert.equal(refreshes, 1)
})

test('页面恢复先更新过期令牌再查询本人资料', async () => {
  const fresh = jwt(300)
  const api = await client(async (url, options) => {
    if (url.includes('/refresh?')) return response(fresh)
    assert.equal(url, '/api/us/users/me')
    assert.equal(options.headers.authorization, fresh)
    return response({ id: '42', name: '最新资料' })
  })
  await api.restoreSession()
  assert.equal(api.session.token, fresh)
  assert.equal(api.session.user.name, '最新资料')
})

test('刷新失败清理会话并阻止写请求', async () => {
  const calls = []
  const api = await client(async url => { calls.push(url); return response(null, 401) })
  await assert.rejects(api.api('/ls/questions', { method: 'POST', body: { title: '待提交' } }), /请重新登录/)
  assert.equal(api.session.token, '')
  assert.equal(api.session.user, null)
  assert.deepEqual(calls, ['/api/as/accounts/refresh?audience=student'])
})

test('跨服务积分不足错误显示业务提示，不显示嵌套 JSON', async () => {
  const api = await client(async url => url.includes('/refresh?') ? response(jwt(300)) : {
    ok: true, status: 200, json: async () => ({ code: 400, msg: JSON.stringify({ code: 400, msg: '积分不足，请先获取积分' }) })
  })
  await assert.rejects(api.api('/ts/point-orders/5/pay', { method: 'POST' }), error => error.message === '积分不足，请先获取积分')
})

test('空用户资料清理会话并提示重新登录', async () => {
  const api = await client(async url => url.includes('/refresh?') ? response(jwt(300)) : response(null))
  await assert.rejects(api.refreshUser(), /登录已过期，请重新登录/)
  assert.equal(api.session.user, null)
  assert.equal(api.session.token, '')
})

test('AI 与 Java 请求共用现有 Token 刷新过程', async () => {
  let release, refreshes = 0
  const pending = new Promise(resolve => { release = resolve })
  const fresh = jwt(300)
  const api = await client(async url => {
    if (url.includes('/refresh?')) { refreshes++; await pending; return response(fresh) }
    return response({ ok: true })
  })
  const requests = [api.getAccessToken(), api.api('/us/users/me')]
  await Promise.resolve()
  assert.equal(refreshes, 1)
  release()
  assert.equal((await Promise.all(requests))[0], fresh)
})

test('旧账号刷新迟到不会覆盖新登录账号的 Token', async () => {
  let release
  const pending = new Promise(resolve => { release = resolve })
  const api = await client(async () => { await pending; return response(jwt(300)) })
  const request = api.getAccessToken()
  await Promise.resolve()
  const newToken = jwt(300) + '-new-account'
  api.session.user = { id: '99', name: '另一个账号' }
  api.session.token = newToken
  release()
  await assert.rejects(request, /登录状态已变化/)
  assert.equal(api.session.token, newToken)
  assert.equal(api.session.user.id, '99')
})
