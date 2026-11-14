import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Discover from './views/Discover.vue'
import { session, notify, restoreSession } from './api'
import './styles.css'

const views = {
  Creator: () => import('./views/Creator.vue'),
  Series: () => import('./views/Series.vue'),
  Player: () => import('./views/Player.vue'),
  Library: () => import('./views/Library.vue'),
  Studio: () => import('./views/Studio.vue'),
  Login: () => import('./views/Login.vue'),
  AiAssistant: () => import('./views/AiAssistant.vue'),
  Checkout: () => import('./views/Checkout.vue'),
  Articles: () => import('./views/Articles.vue'),
  Account: () => import('./views/Account.vue'),
  AccountCenter: () => import('./views/AccountCenter.vue'),
  PointsAccount: () => import('./views/PointsAccount.vue'),
  PointOrders: () => import('./views/PointOrders.vue'),
  Coupons: () => import('./views/Coupons.vue'),
  Operations: () => import('./views/Operations.vue')
}
const page = name => views[name]
const routes = [
  {
    path: '/',
    component: Discover,
    name: 'discover',
    props: ({ query }) => ({
      kind: ['video', 'articles'].includes(query.kind) ? query.kind : 'all',
      search: typeof query.q === 'string' ? query.q : '',
      topic: typeof query.topic === 'string' ? query.topic : '',
      sort: ['price', 'duration'].includes(query.sort) ? query.sort : 'recommended'
    }),
    meta: { title: '发现好内容', english: 'DISCOVER' }
  },
  { path: '/creators', component: Discover, props: ({ query }) => ({ creatorsOnly: true, search: typeof query.q === 'string' ? query.q : '' }), meta: { title: '创作者', english: 'CREATOR RANKING' } },
  { path: '/creators/:slug', component: page('Creator'), meta: { title: '创作者主页', english: 'MEET THE CREATOR' } },
  { path: '/series/:slug', component: page('Series'), meta: { title: '专栏详情', english: 'A DEEPER LOOK' } },
  { path: '/watch/:slug', component: page('Player'), name: 'watch', meta: { title: '观看专栏', english: 'FOCUS & EXPLORE', auth: true } },
  { path: '/library', redirect: '/account/learning' },
  { path: '/watchlist', component: page('Library'), props: { mode: 'saved' }, meta: { title: '稍后观看', english: 'WATCHLIST', auth: true } },
  { path: '/orders', redirect: '/account/orders' },
  { path: '/articles', component: page('Articles'), meta: { title: '文章', english: 'KNOWLEDGE & IDEAS' } },
  { path: '/articles/:slug', component: page('Articles'), meta: { title: '阅读文章', english: 'KNOWLEDGE & IDEAS' } },
  { path: '/account', component: page('AccountCenter'), redirect: '/account/profile', meta: { title: '个人中心', english: 'YOUR ACCOUNT', auth: true }, children: [
    { path: 'profile', component: page('Account'), props: { mode: 'profile' }, meta: { title: '个人资料' } },
    { path: 'password', component: page('Account'), props: { mode: 'password' }, meta: { title: '修改密码' } },
    { path: 'creations', redirect: to => ({ path: '/studio', query: to.query }) },
    { path: 'learning', component: page('Library'), props: { mode: 'owned' }, meta: { title: '已解锁内容' } },
    { path: 'points', component: page('PointsAccount'), meta: { title: '积分记录' } },
    { path: 'orders', component: page('PointOrders'), meta: { title: '我的订单' } },
    { path: 'orders/:id', component: page('PointOrders'), meta: { title: '订单详情' } },
    { path: 'coupons', component: page('Coupons'), meta: { title: '我的优惠券' } }
  ] },
  { path: '/operations', component: page('Operations'), meta: { title: '运营后台', english: 'PLATFORM OPERATIONS', auth: true, operator: true } },
  { path: '/studio', component: page('Studio'), meta: { title: '创作台', english: 'CREATOR STUDIO', auth: true } },
  { path: '/ai/:sessionId?', component: page('AiAssistant'), meta: { title: 'AI 学习助手', english: 'LEARNTRAIL AI', auth: true } },
  { path: '/login', component: page('Login'), meta: { title: '欢迎来到 LearnTrail', english: 'YOUR NEXT CHAPTER' } },
  { path: '/checkout/:slug', component: page('Checkout'), meta: { title: '解锁专栏', english: 'UNLOCK YOUR NEXT CHAPTER', auth: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]
const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.name === 'discover' && from.name === 'discover' && to.query.q === from.query.q && to.query.kind === from.query.kind) return false
    return { top: 0 }
  }
})
const initialSession = restoreSession()
router.beforeEach(async to => {
  if (to.path === '/' && to.query.kind === 'articles') return { path: '/articles', query: { q: to.query.q, page: to.query.page } }
  await initialSession
  if (to.meta.auth && !session.token) return { path: '/login', query: { next: to.fullPath, audience: to.meta.operator ? 'admin' : undefined } }
  if (to.meta.operator && Number(session.user?.type) !== 1) {
    notify('当前账号没有运营权限', 'error')
    return '/'
  }
})
router.afterEach((to, from, failure) => { if (!failure) document.title = (to.meta.title || '发现好内容') + ' · LearnTrail 学途' })
router.onError(error => notify(error.message, 'error'))
createApp(App).use(router).mount('#app')
