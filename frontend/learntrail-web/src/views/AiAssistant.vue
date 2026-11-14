<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session } from '../api.js'
import { createAiSession, getAiSession, listAiSessions } from '../aiApi.js'
import AiConversation from '../components/AiConversation.vue'
import Icon from '../components/Icon.vue'

const route = useRoute()
const router = useRouter()
const sessions = ref([])
const total = ref(0)
const detail = ref(null)
const messages = ref([])
const messageOffset = ref(0)
const listLoading = ref(false)
const detailLoading = ref(false)
const creating = ref(false)
const generating = ref(false)
const error = ref('')
const listError = ref('')
const historyViewport = window.matchMedia?.('(max-width: 800px)')
const historyOpen = ref(!historyViewport?.matches)
const historyViewportChanged = event => { historyOpen.value = !event.matches }
historyViewport?.addEventListener('change', historyViewportChanged)
const selected = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const dateFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const controller = new AbortController()
let disposed = false, pollTimer, listVersion = 0, detailVersion = 0
const owner = () => session.user?.id
const current = captured => !disposed && captured === owner()

async function loadList(append = false) {
  const user = owner(), version = ++listVersion
  listLoading.value = true
  listError.value = ''
  try {
    const result = await listAiSessions(append ? sessions.value.length : 0, controller.signal)
    if (!current(user) || version !== listVersion) return
    const items = append ? [...sessions.value, ...result.items] : result.items
    sessions.value = [...new Map(items.map(item => [item.id, item])).values()]
    total.value = result.total
  } catch (cause) { if (current(user) && cause.name !== 'AbortError') listError.value = cause.message }
  finally { if (current(user) && version === listVersion) listLoading.value = false }
}

async function loadDetail(earlier = false) {
  if (!selected.value) return
  clearTimeout(pollTimer)
  const user = owner(), version = ++detailVersion
  detailLoading.value = true
  error.value = ''
  try {
    let offset = earlier ? Math.max(0, messageOffset.value - 100) : 0
    let result = await getAiSession(selected.value, { offset, signal: controller.signal })
    if (!earlier && result.total_messages > 100) {
      offset = result.total_messages - 100
      result = await getAiSession(selected.value, { offset, signal: controller.signal })
    }
    if (!current(user) || version !== detailVersion) return
    detail.value = result
    messages.value = earlier ? [...new Map([...result.messages, ...messages.value].map(item => [item.id, item])).values()] : result.messages
    messageOffset.value = offset
    if (result.recent_runs.some(run => run.status === 'running') && !generating.value) {
      pollTimer = setTimeout(() => loadDetail(), 1000)
    }
  } catch (cause) {
    if (current(user) && cause.name !== 'AbortError') {
      error.value = cause.message
      if (cause.status === 404 || cause.status === 403) { detail.value = null; messages.value = [] }
    }
  } finally { if (current(user) && version === detailVersion) detailLoading.value = false }
}

async function create() {
  if (creating.value || generating.value) return
  const user = owner()
  creating.value = true
  error.value = ''
  try {
    const created = await createAiSession('新对话')
    if (current(user)) await router.push('/ai/' + created.id)
  } catch (cause) { if (current(user)) error.value = cause.message }
  finally { if (current(user)) creating.value = false }
}
function refresh() { loadList(); loadDetail() }
watch(() => session.user?.id, user => {
  clearTimeout(pollTimer)
  sessions.value = []; detail.value = null; messages.value = []; total.value = 0
  if (user == null) { router.replace({ path: '/login', query: { next: route.fullPath } }); return }
  refresh()
}, { immediate: true })
onScopeDispose(() => { disposed = true; controller.abort(); clearTimeout(pollTimer); historyViewport?.removeEventListener('change', historyViewportChanged) })
</script>

<template>
  <section class="ai-assistant">
    <div class="ai-layout" :class="{ 'history-hidden': !historyOpen }">
      <button v-if="historyOpen" type="button" class="history-backdrop" aria-label="关闭会话列表" @click="historyOpen = false"></button>
      <aside v-show="historyOpen" id="ai-history" class="session-panel" aria-label="历史会话">
        <div class="history-actions">
          <button type="button" class="new-session" :disabled="creating || generating" @click="create"><Icon name="plus" :size="17" aria-hidden="true" />{{ creating ? '正在创建…' : '新建对话' }}</button>
          <button type="button" class="history-refresh" aria-label="刷新会话" title="刷新会话" :disabled="listLoading || detailLoading || generating" @click="refresh"><Icon name="clock" :size="16" aria-hidden="true" /></button>
        </div>
        <h2>最近会话</h2>
        <p v-if="listError" class="form-error" role="alert">{{ listError }}</p>
        <p v-if="listLoading" role="status" class="empty-copy">正在读取会话…</p>
        <p v-else-if="!sessions.length && !listError" class="empty-copy">还没有会话，创建一个开始吧。</p>
        <nav class="session-list" aria-label="选择会话">
          <RouterLink v-for="item in sessions" :key="item.id" :to="'/ai/' + item.id" :class="{ active: selected === item.id }"
            :aria-current="selected === item.id ? 'page' : undefined" :aria-disabled="generating || creating || undefined"
            @click="event => { if (generating || creating) event.preventDefault(); else if (historyViewport?.matches) historyOpen = false }"><span>{{ item.title }}</span><time :datetime="item.updated_at">{{ dateFormat.format(new Date(item.updated_at)) }}</time></RouterLink>
        </nav>
        <button v-if="sessions.length < total" class="text-link more-sessions" type="button" :disabled="listLoading || generating" @click="loadList(true)">更多会话</button>
      </aside>
      <section class="conversation-panel" :aria-busy="detailLoading">
        <header class="conversation-heading">
          <button type="button" class="history-toggle" :aria-expanded="historyOpen" aria-controls="ai-history" :aria-label="historyOpen ? '收起会话列表' : '展开会话列表'" @click="historyOpen = !historyOpen"><Icon name="menu" :size="18" aria-hidden="true" /></button>
          <h1>{{ detail?.title || 'LearnTrail AI' }}</h1>
        </header>
        <p v-if="error" class="form-error page-error" role="alert">{{ error }}</p>
        <template v-if="detail">
          <AiConversation :key="String(session.user?.id) + detail.id" :session-id="detail.id" :messages="messages" :runs="detail.recent_runs"
            :loading="detailLoading" :has-earlier="messageOffset > 0" @refresh="refresh" @earlier="loadDetail(true)" @busy="generating = $event" />
        </template>
        <p v-else-if="detailLoading" role="status" class="empty-copy">正在恢复会话…</p>
        <div v-else class="choose-conversation"><h2>{{ selected ? '暂时无法打开此会话' : '今天想了解什么？' }}</h2><p v-if="selected">请查看错误提示，或选择其他会话。</p><button v-else type="button" class="new-session" :disabled="creating" @click="create"><Icon name="plus" :size="17" aria-hidden="true" />新建对话</button></div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.ai-assistant { width: 100%; min-height: 0; }
.ai-layout { display: grid; grid-template-columns: 216px minmax(0, 1fr); height: 100%; position: relative; }
.ai-layout.history-hidden { grid-template-columns: minmax(0, 1fr); }
.session-panel { min-width: 0; min-height: 0; padding: 14px 10px; border-right: 1px solid #ededeb; background: #f9f9f7; display: flex; flex-direction: column; }
.history-actions { display: flex; align-items: center; gap: 4px; }
.new-session, .history-refresh, .history-toggle { display: inline-flex; align-items: center; justify-content: center; gap: 7px; background: transparent; border: 0; border-radius: 8px; color: #43463f; padding: 9px; cursor: pointer; }
.new-session { flex: 1; justify-content: flex-start; font-size: 13px; }
.history-refresh, .history-toggle { flex-shrink: 0; width: 34px; height: 34px; }
.new-session:hover, .history-refresh:hover, .history-toggle:hover { background: #ececea; }
.session-panel h2 { margin: 22px 9px 10px; font-size: 11px; font-weight: 500; color: #7b7e77; }
.session-list { display: grid; align-content: start; gap: 2px; min-height: 0; overflow-y: auto; }
.session-list a { display: flex; align-items: baseline; gap: 8px; border-radius: 7px; padding: 10px 9px; font-size: 12px; }
.session-list a.active, .session-list a:hover { background: #eaece7; }
.session-list a > span { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-list time { color: #878b81; font-size: 10px; white-space: nowrap; }
.session-list a[aria-disabled="true"] { opacity: .65; cursor: wait; }
.more-sessions { margin-top: 14px; }
.conversation-panel { min-width: 0; min-height: 0; display: flex; flex-direction: column; background: #fff; }
.conversation-heading { display: flex; align-items: center; gap: 10px; padding: 6px 16px; min-height: 46px; flex-shrink: 0; }
.conversation-heading h1 { font-size: 14px; font-weight: 500; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.page-error { padding: 0 24px; }
.choose-conversation { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; padding: 24px; text-align: center; }
.choose-conversation h2 { font-size: clamp(22px, 3vw, 28px); font-weight: 500; }
.choose-conversation .new-session { flex: 0; background: #f1f2ee; }
.empty-copy, .choose-conversation p { color: var(--secondary-text); line-height: 1.7; font-size: 12px; }
.history-backdrop { display: none; }
@media (max-width: 800px) {
  .ai-layout { grid-template-columns: minmax(0, 1fr); }
  .session-panel { position: absolute; z-index: 12; inset: 0 auto 0 0; width: min(250px, 80%); }
  .history-backdrop { display: block; position: absolute; inset: 0; z-index: 11; background: #0003; border: 0; }
  .conversation-heading { padding-inline: 10px; }
}
</style>
