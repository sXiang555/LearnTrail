// Actual Vue components with HTTP doubles; live Redis is verified separately.
import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'

const dom = new JSDOM('<div id="app"></div>', { url: 'http://localhost:5173/' })
for (const key of ['window', 'document', 'Document', 'ShadowRoot', 'Event', 'history', 'location', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'sessionStorage']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
}
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
const { createApp, h, nextTick, reactive } = await import('vue')
const { createRouter, createMemoryHistory } = await import('vue-router')
const { session } = await import('../src/api.js')
after(() => dom.window.close())
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await nextTick() }
let sequence = 0
async function componentUrl(relative) {
  const filename = new URL(relative, import.meta.url)
  const { descriptor } = parse(await readFile(filename, 'utf8'), { filename: filename.pathname })
  let code = compileScript(descriptor, { id: 'likes-test-' + sequence++, inlineTemplate: true }).content
  for (const item of [...code.matchAll(/from\s+['"]([^'"]+)['"]/g)]) {
    const specifier = item[1]
    const target = specifier.endsWith('.vue') ? await componentUrl(new URL(specifier, filename).href) :
      specifier.startsWith('.') ? new URL(/\.[a-z]+$/i.test(specifier) ? specifier : specifier + '.js', filename).href : import.meta.resolve(specifier)
    code = code.replace(item[0], 'from ' + JSON.stringify(target))
  }
  return 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
}
const { default: ContentLikeButton } = await import(await componentUrl('../src/components/ContentLikeButton.vue'))
const { default: App } = await import(await componentUrl('../src/App.vue'))
const { default: ArticleCard } = await import(await componentUrl('../src/components/ArticleCard.vue'))
const { default: SeriesCard } = await import(await componentUrl('../src/components/SeriesCard.vue'))
const ok = data => Response.json({ code: 200, data })
const loggedIn = () => {
  session.token = 'test.' + Buffer.from(JSON.stringify({ exp: Math.floor(Date.now()/1000)+300 })).toString('base64url') + '.test'
  session.user = { id: '42' }
}
test('游客能看公开点赞数，点击时仍需登录', async () => {
  session.token = ''; session.user = null
  globalThis.fetch = async () => { throw new Error('Anonymous count comes from public content') }
  const view = await mount(ContentLikeButton, { bizType: 'ARTICLE', bizId: '10', likedTimes: '28' })
  try {
    assert.equal(document.querySelector('.like-count').textContent, '28')
    assert.equal(document.querySelector('.like-button').disabled, false)
    document.querySelector('.like-button').click(); await flush()
    assert.equal(view.router.currentRoute.value.path, '/login')
  } finally { view.app.unmount() }
})
test('文章卡片公开展示点赞和浏览量，字符串计数可用', async () => {
  session.token = ''; session.user = null
  const view = await mount(ArticleCard, { article: { slug: 'example', title: '导读', summary: '摘要', likedTimes: '28', viewTimes: '103' } })
  try {
    assert.match(document.querySelector('.content-metrics').textContent, /28 人点赞/)
    assert.match(document.querySelector('.content-metrics').textContent, /103 次浏览/)
  } finally { view.app.unmount() }
})
test('视频卡片展示真实播放量，零值正常展示', async () => {
  const view = await mount(SeriesCard, { series: { slug: 'example', title: '演示', duration: 60, likedTimes: '0', viewTimes: '9' } })
  try {
    assert.match(document.querySelector('.content-metrics').textContent, /0 人点赞/)
    assert.match(document.querySelector('.content-metrics').textContent, /9 次播放/)
  } finally { view.app.unmount() }
})
async function mount(component = ContentLikeButton, values = { bizType: 'ARTICLE', bizId: '10' }) {
  const props = reactive(values)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: { render: () => h('p', '内容') } }
  ] })
  const app = createApp({ render: () => h(component, props) })
  app.use(router)
  await router.push('/articles/example'); await router.isReady()
  app.mount('#app'); await flush()
  return { app, props, router }
}

test('点赞和取消使用服务端数量，重新挂载恢复本人状态', async () => {
  loggedIn()
  let liked = false, count = 8
  const writes = []
  globalThis.fetch = async (url, options) => {
    if (url === '/api/rs/likes') {
      const body = JSON.parse(options.body); writes.push(body)
      liked = body.liked; count = liked ? 12 : 11
    }
    return ok({ liked, likedTimes: count })
  }
  let view = await mount()
  try {
    assert.match(document.querySelector('.like-button').textContent, /8/)
    document.querySelector('.like-button').click(); await flush()
    assert.equal(document.querySelector('.like-button').getAttribute('aria-pressed'), 'true')
    assert.match(document.querySelector('.like-button').textContent, /12/)
    assert.deepEqual(writes[0], { bizId: '10', bizType: 'ARTICLE', liked: true })
    view.app.unmount(); view = await mount()
    assert.equal(document.querySelector('.like-button').getAttribute('aria-pressed'), 'true')
    document.querySelector('.like-button').click(); await flush()
    assert.equal(document.querySelector('.like-button').getAttribute('aria-pressed'), 'false')
    assert.match(document.querySelector('.like-button').textContent, /11/)
    assert.equal(writes[1].liked, false)
  } finally { view.app.unmount() }
})

test('提交期间禁止连续点击，不重复发送点赞', async () => {
  loggedIn()
  let release, writes = 0
  const held = new Promise(resolve => { release = resolve })
  globalThis.fetch = async (url, options) => {
    if (options.method === 'POST') { writes++; await held }
    return ok({ liked: false, likedTimes: 0 })
  }
  const view = await mount()
  try {
    document.querySelector('.like-button').click(); await nextTick()
    assert.equal(document.querySelector('.like-button').disabled, true)
    document.querySelector('.like-button').click()
    assert.equal(writes, 1)
    release(); await flush()
  } finally { release(); view.app.unmount() }
})

test('未登录点击跳转登录并保留返回路径，不调用点赞接口', async () => {
  session.token = ''; session.user = null
  let requests = 0
  globalThis.fetch = async () => { requests++; throw new Error('Unexpected request') }
  const view = await mount()
  try {
    document.querySelector('.like-button').click(); await flush()
    assert.equal(view.router.currentRoute.value.path, '/login')
    assert.equal(view.router.currentRoute.value.query.next, '/articles/example')
    assert.equal(requests, 0)
  } finally { view.app.unmount() }
})

test('写入结果不明时不改数字，重新读取后再允许操作', async () => {
  loggedIn()
  globalThis.fetch = async (url, options) => options.method === 'POST'
    ? Response.json({ code: 503, msg: '服务暂不可用' }) : ok({ liked: false, likedTimes: 4 })
  const view = await mount()
  try {
    document.querySelector('.like-button').click(); await flush()
    assert.equal(document.querySelector('.like-button').disabled, true)
    assert.equal(document.querySelector('.like-count'), null)
    assert.match(document.querySelector('[role="alert"]').textContent, /重新读取点赞状态/)
    document.querySelector('.form-error button').click(); await flush()
    assert.equal(document.querySelector('.like-button').disabled, false)
    assert.equal(document.querySelector('.like-count').textContent, '4')
  } finally { view.app.unmount() }
})

test('切换文章后忽略旧请求，不把旧文章点赞结果写入新文章', async () => {
  loggedIn()
  let release
  const held = new Promise(resolve => { release = resolve })
  globalThis.fetch = async url => {
    if (url.endsWith('/10')) { await held; return ok({ liked: true, likedTimes: 99 }) }
    return ok({ liked: false, likedTimes: 2 })
  }
  const view = await mount()
  try {
    view.props.bizId = '11'; await flush()
    assert.equal(document.querySelector('.like-count').textContent, '2')
    release(); await flush()
    assert.equal(document.querySelector('.like-count').textContent, '2')
    assert.equal(document.querySelector('.like-button').getAttribute('aria-pressed'), 'false')
  } finally { release(); view.app.unmount() }
})

test('未公开专栏禁用点赞，不请求状态或写入', async () => {
  loggedIn()
  globalThis.fetch = async () => { throw new Error('Must not request hidden content') }
  const view = await mount(ContentLikeButton, { bizType: 'SERIES', bizId: '1', disabled: true })
  try {
    assert.equal(document.querySelector('.like-button').disabled, true)
    assert.match(document.body.textContent, /内容暂未公开/)
  } finally { view.app.unmount() }
})

test('AI 入口位于个人中心下方横线之后，主导航中不重复出现', async () => {
  session.token = ''; session.user = null
  const view = await mount(App, {})
  try {
    const sidebar = document.querySelector('.sidebar')
    const mainNav = sidebar.querySelector('nav[aria-label="主导航"]')
    assert.equal(mainNav.querySelector('a[href="/ai"]'), null)
    const divider = sidebar.querySelector('.sidebar-divider')
    const assistantNav = divider.nextElementSibling
    assert.equal(assistantNav.getAttribute('aria-label'), '学习助手')
    assert.equal(assistantNav.querySelector('a').getAttribute('href'), '/ai')
    assert.equal(sidebar.querySelectorAll('a[href="/ai"]').length, 1)
    await view.router.push('/ai'); await flush()
    assert.equal(assistantNav.querySelector('a').classList.contains('selected'), true)
  } finally { view.app.unmount() }
})
