import { reactive } from 'vue'

function storedUser() {
  try { return JSON.parse(sessionStorage.getItem('lt_user') || 'null') } catch { return null }
}
export const session = reactive({
  token: sessionStorage.getItem('lt_token') || '',
  user: storedUser(),
  audience: sessionStorage.getItem('lt_audience') === 'admin' ? 'admin' : 'student',
  balance: null,
  notice: '',
  noticeType: 'info'
})
let refreshPromise
let noticeTimer

export function notify(message, type = 'info') {
  session.notice = message
  session.noticeType = type
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { session.notice = '' }, 3500)
}
export function clearSession() {
  session.token = ''
  session.user = null
  session.balance = null
  session.audience = 'student'
  sessionStorage.removeItem('lt_token')
  sessionStorage.removeItem('lt_user')
  sessionStorage.removeItem('lt_audience')
}
function token(value) {
  session.token = value
  sessionStorage.setItem('lt_token', value)
}
async function refresh() {
  const previousToken = session.token
  const previousUser = session.user?.id
  const audience = session.audience
  const response = await fetch('/api/as/accounts/refresh?audience=' + session.audience, { credentials: 'include' })
  const body = await response.json()
  if (!response.ok || body.code !== 200 || !body.data) throw new Error('请重新登录')
  if (session.token !== previousToken || session.user?.id !== previousUser || session.audience !== audience) {
    throw new Error('登录状态已变化，请重新操作')
  }
  token(body.data)
}
function expiresSoon() {
  if (!session.token) return false
  try {
    // 只用于决定刷新时机；身份和权限仍由服务端验签校验。
    const part = session.token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const expiry = Number(JSON.parse(atob(part)).exp)
    return !Number.isFinite(expiry) || expiry * 1000 <= Date.now() + 10000
  } catch { return true }
}
function renew() {
  refreshPromise ||= refresh().finally(() => { refreshPromise = undefined })
  return refreshPromise
}
export async function getAccessToken(forceRefresh = false) {
  const owner = session.user?.id
  if (!session.token || owner == null) throw new Error('请先登录')
  try {
    if (forceRefresh || expiresSoon()) await renew()
  } catch (error) {
    if (session.user?.id === owner) clearSession()
    throw error
  }
  if (!session.token || session.user?.id !== owner) throw new Error('登录状态已变化，请重新操作')
  return session.token
}
export async function restoreSession() {
  if (session.token && session.user && !expiresSoon()) return
  try {
    await renew()
    session.user = await api('/us/users/me')
    sessionStorage.setItem('lt_user', JSON.stringify(session.user))
  } catch {
    clearSession()
  }
}
export async function api(path, options = {}, retried = false) {
  if (session.token && expiresSoon() && !retried) {
    try { await renew() } catch {
      clearSession()
      if (options.method && options.method !== 'GET') throw new Error('登录已过期，请重新登录')
    }
  }
  const multipart = options.body instanceof FormData
  const response = await fetch('/api' + path, {
    ...options,
    credentials: 'include',
    headers: {
      ...(options.body && !multipart ? { 'Content-Type': 'application/json' } : {}),
      ...(session.token ? { authorization: session.token } : {}),
      ...options.headers
    },
    body: multipart ? options.body : options.body ? JSON.stringify(options.body) : undefined
  })
  const body = await response.json().catch(() => ({ msg: '服务暂时无法连接，请稍后再试' }))
  if ((response.status === 401 || body.code === 401) && session.token && !retried) {
    try {
      await renew()
      return api(path, options, true)
    } catch {
      clearSession()
      if (!options.method || options.method === 'GET') return api(path, options, true)
      throw new Error('登录已过期，请重新登录')
    }
  }
  if (!response.ok || body.code !== 200) {
    let message = body.msg || body.message || '操作未完成，请稍后再试'
    for (let depth = 0; depth < 3 && typeof message === 'string' && message.trim().startsWith('{'); depth++) {
      try { const nested = JSON.parse(message); const next = nested.msg || nested.message; if (!next) break; message = next } catch { break }
    }
    throw new Error(typeof message === 'string' ? message : '操作未完成，请稍后再试')
  }
  const data = body.data
  if (Array.isArray(data?.list) && data.total != null && data.pages != null) {
    return { ...data, total: Number(data.total), pages: Number(data.pages) }
  }
  return data
}
export async function login(account, password, rememberMe, audience = 'student') {
  clearSession()
  const body = { type: 1, password, rememberMe }
  if (/^\d{11}$/.test(account)) body.cellPhone = account
  else body.username = account
  token(await api('/as/accounts/' + (audience === 'admin' ? 'admin/' : '') + 'login', { method: 'POST', body }))
  session.audience = audience === 'admin' ? 'admin' : 'student'
  sessionStorage.setItem('lt_audience', session.audience)
  try {
    session.user = await api('/us/users/me')
    sessionStorage.setItem('lt_user', JSON.stringify(session.user))
  } catch (error) {
    clearSession()
    throw error
  }
}
export async function logout() {
  try { await api('/as/accounts/logout', { method: 'POST' }) } finally { clearSession() }
}
export async function refreshUser() {
  const user = await api('/us/users/me')
  if (!user?.id) {
    clearSession()
    throw new Error('登录已过期，请重新登录')
  }
  session.user = user
  sessionStorage.setItem('lt_user', JSON.stringify(user))
  return user
}
export const studioApi = '/cs/studio'
export const pointsApi = '/ls/studio-points'
export async function refreshWallet() {
  const currentUser = session.user
  const wallet = await api(pointsApi + '/wallet')
  if (session.token && session.user === currentUser) session.balance = wallet.balance
  return wallet
}
export const price = value => '¥' + ((Number(value) || 0) / 100).toFixed(Number(value) % 100 ? 2 : 0)
export const duration = seconds => {
  const total = Number(seconds) || 0
  return total >= 3600 ? (total / 3600).toFixed(1) + ' 小时' : Math.max(1, Math.round(total / 60)) + ' 分钟'
}
export const timestamp = seconds => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0')
}
