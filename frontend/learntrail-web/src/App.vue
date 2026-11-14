<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session, logout, notify, refreshWallet } from './api'
import Icon from './components/Icon.vue'
import UserAvatar from './components/UserAvatar.vue'
const route = useRoute()
const isAiRoute = computed(() => /^\/ai(?:\/|$)/.test(route.path))
const router = useRouter()
const mainContent = ref(null)
const query = ref('')
watch(() => session.user, user => {
  if (user && session.token) refreshWallet().catch(() => { session.balance = null })
}, { immediate: true })
watch(() => route.query.q, value => { query.value = typeof value === 'string' ? value : '' }, { immediate: true })
const mobileMenu = ref(false)
const menuToggle = ref(null)
const menuClose = ref(null)
const mobileViewport = window.matchMedia('(max-width: 900px)')
function closeMenu() {
  mobileMenu.value = false
}
function closeMenuOnDesktop(event) {
  if (!event.matches) closeMenu()
}
function keepMenuFocus(event) {
  if (!mobileMenu.value) return
  const controls = event.currentTarget.querySelectorAll('a[href], button:not([disabled])')
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && event.target === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && event.target === last) {
    event.preventDefault()
    first?.focus()
  }
}
watch(mobileMenu, open => {
  if (open) menuClose.value?.focus()
  else if (mobileViewport.matches) menuToggle.value?.focus()
}, { flush: 'post' })
watch(() => route.fullPath, closeMenu)
onMounted(() => mobileViewport.addEventListener('change', closeMenuOnDesktop))
onUnmounted(() => mobileViewport.removeEventListener('change', closeMenuOnDesktop))
const nav = [
  { to: '/', label: '发现好内容', icon: 'discover', exact: true },
  { to: '/account/learning', label: '已解锁内容', icon: 'library', exact: true },
  { to: '/creators', label: '创作者', icon: 'creators' },
  { to: '/watchlist', label: '稍后观看', icon: 'bookmark' },
  { to: '/studio', label: '创作台', icon: 'grid' },
  { to: '/account', label: '个人中心', icon: 'settings', exclude: ['/account/learning'] }
]
function isNavSelected(item) {
  return (item.exact ? route.path === item.to : route.path.startsWith(item.to))
    && !item.exclude?.includes(route.path)
}
const title = computed(() => route.meta.title || '发现好内容')
function search() {
  router.push({ path: '/', query: query.value.trim() ? { q: query.value.trim().slice(0, 80) } : {} })
  mobileMenu.value = false
}
async function leave() {
  try { await logout(); router.push('/'); notify('已退出登录') } catch (e) { notify(e.message, 'error') }
}
</script>

<template>
  <div class="app-shell" :class="{ cinema: route.name === 'watch', 'menu-open': mobileMenu, 'ai-chat-shell': isAiRoute }">
    <button type="button" class="skip-content" @click="mainContent?.focus()">跳到正文</button>
    <aside id="site-navigation" class="sidebar" :role="mobileMenu ? 'dialog' : undefined"
      :aria-modal="mobileMenu || undefined" aria-label="站点导航"
      @keydown.esc="closeMenu" @keydown.tab="keepMenuFocus">
      <button ref="menuClose" type="button" class="icon-button sidebar-close"
        aria-label="关闭导航" @click="closeMenu">
        <Icon name="close" :size="18" aria-hidden="true" />
      </button>
      <RouterLink to="/" class="brand" aria-label="LearnTrail 首页" @click="mobileMenu = false"><span class="brand-symbol"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 6h6v19H7zM16 6h11v6H16zM16 15h6v10h-6z" fill="currentColor"/></svg></span><span>LearnTrail<small>学途 · 创作者专栏</small></span></RouterLink>
      <span class="sidebar-label">你的探索空间</span>
      <RouterLink v-if="Number(session.user?.type) === 1" to="/operations" class="btn light" @click="closeMenu">运营后台</RouterLink>
      <nav aria-label="主导航"><RouterLink v-for="item in nav" :key="item.to" :to="item.to" :class="{ selected: isNavSelected(item) }" :aria-label="item.label" :title="isAiRoute ? item.label : undefined" @click="mobileMenu = false"><Icon :name="item.icon" :size="20" aria-hidden="true" /><span class="nav-label">{{ item.label }}</span><span v-if="item.to === '/' && route.path === '/'" class="nav-dot"></span></RouterLink></nav>
      <div class="sidebar-divider"></div>
      <nav class="assistant-nav" aria-label="学习助手">
        <RouterLink to="/ai" :class="{ selected: isAiRoute }" aria-label="AI 学习助手"
          :title="isAiRoute ? 'AI 学习助手' : undefined" @click="closeMenu">
          <Icon name="headphones" :size="20" aria-hidden="true" /><span class="nav-label">AI 学习助手</span>
        </RouterLink>
      </nav>
      <div class="sidebar-bottom"><div class="sidebar-note"><span class="note-spark">✳</span><p>始于好奇，<br>不止于观看。</p><span>KEEP YOUR CURIOSITY.</span></div><div v-if="session.user" class="user-block"><span class="avatar forest">{{ (session.user.name || '我').slice(0, 1) }}</span><div><b>{{ session.user.name }}</b><small>你的学习旅程</small></div><button class="icon-button" aria-label="退出登录" @click="leave"><Icon name="logout" :size="17" /></button></div><RouterLink v-else to="/login" class="btn primary sidebar-login">登录，开启你的旅程 <Icon name="arrow" :size="16" /></RouterLink></div>
    </aside>
    <div v-if="mobileMenu" class="menu-scrim" @click="closeMenu"></div>
    <div class="main-shell" :inert="mobileMenu">
      <header class="topbar">
        <div class="topbar-title">
          <button ref="menuToggle" type="button" class="icon-button mobile-toggle"
            aria-label="打开导航" aria-controls="site-navigation" :aria-expanded="mobileMenu"
            @click="mobileMenu = true">
            <Icon name="menu" aria-hidden="true" />
          </button>
          <span>{{ route.path === '/login' ? '登录' : title }}</span>
          <span class="topbar-slash">/</span>
          <small>{{ route.meta.english || 'EXPLORE SOMETHING NEW' }}</small>
        </div>
        <div class="topbar-actions">
          <form class="global-search" role="search" @submit.prevent="search">
            <Icon name="search" :size="17" aria-hidden="true" />
            <input v-model="query" aria-label="搜索文章、视频或创作者" placeholder="搜索文章、视频、创作者…" maxlength="80" />
            <kbd>↵</kbd>
          </form>
          <span class="topbar-divider"></span>
          <RouterLink v-if="!session.user" to="/login" class="top-login">登录 <Icon name="upRight" :size="17" /></RouterLink>
          <RouterLink v-else to="/account" class="top-profile" aria-label="个人中心">
            <UserAvatar :user="session.user" :size="28" />
            <span>{{ session.user.name }}<small class="profile-points">{{ session.balance === null ? '积分钱包' : session.balance + ' 积分' }}</small></span>
          </RouterLink>
        </div>
      </header>
      <main ref="mainContent" tabindex="-1" class="page-content">
        <RouterView v-slot="{ Component, route: viewRoute }">
          <KeepAlive include="Discover" :max="2">
            <component :is="Component" :key="viewRoute.name === 'discover' ? viewRoute.path : viewRoute.fullPath" />
          </KeepAlive>
        </RouterView>
      </main>
      <footer v-if="route.name !== 'watch' && !isAiRoute" class="site-footer"><span><b>LearnTrail.</b> 为值得的内容，走自己的路。</span><span>© 2026 LearnTrail</span></footer>
    </div>
    <Transition name="toast"><div v-if="session.notice" role="status" class="toast-message" :class="session.noticeType"><Icon :name="session.noticeType === 'error' ? 'close' : 'check'" :size="18" />{{ session.notice }}</div></Transition>
  </div>
</template>

<style scoped>
.profile-points { display: block; margin-top: 3px; font-size: 11px; font-weight: 400; color: var(--muted); }
.sidebar .assistant-nav { margin-top: 0; }
.skip-content { position: fixed; top: 8px; left: 16px; z-index: 1000; transform: translateY(-160%); background: var(--green); color: white; padding: 12px 18px; border-radius: 8px; }
.skip-content:focus-visible { transform: translateY(0); outline: 2px solid var(--green); outline-offset: 3px; }
.ai-chat-shell .main-shell { height: 100dvh; min-height: 0; display: flex; flex-direction: column; background: #fff; }
.ai-chat-shell .topbar { flex-shrink: 0; min-height: 52px; padding-inline: 20px; background: #fff; }
.ai-chat-shell .topbar-slash, .ai-chat-shell .topbar-title small, .ai-chat-shell .global-search, .ai-chat-shell .topbar-divider, .ai-chat-shell .profile-points { display: none; }
.ai-chat-shell .page-content { flex: 1; min-height: 0; width: 100%; max-width: none; padding: 0; display: flex; overflow: hidden; }
@media (min-width: 901px) {
  .ai-chat-shell { --sidebar-width: 64px; }
  .ai-chat-shell .sidebar { padding: 16px 10px; background: #f7f7f5; }
  .ai-chat-shell .brand { padding: 0; justify-content: center; }
  .ai-chat-shell .brand > span:last-child, .ai-chat-shell .sidebar-label, .ai-chat-shell .nav-label,
  .ai-chat-shell .sidebar-note, .ai-chat-shell .sidebar-divider, .ai-chat-shell .user-block > div, .ai-chat-shell .user-block > .avatar { display: none; }
  .ai-chat-shell .sidebar nav { margin-top: 26px; }
  .ai-chat-shell .sidebar .assistant-nav { margin-top: 12px; }
  .ai-chat-shell .sidebar nav > a { justify-content: center; padding: 12px; gap: 0; border-radius: 12px; }
  .ai-chat-shell .user-block { justify-content: center; padding: 0; }
  .ai-chat-shell .user-block .icon-button { margin: 0; }
}
</style>
