<script setup>
import { computed, nextTick, ref, shallowRef, watch } from 'vue'
import { api, session } from '../api.js'
import { cancelAiRun } from '../aiApi.js'
import { renderMarkdown } from '../articleMarkdown.js'
import { useAiStream } from '../composables/useAiStream.js'
import AgentActivity from './AgentActivity.vue'
import AiSources from './AiSources.vue'
import SkillPicker from './SkillPicker.vue'
import ApprovalCards from './ApprovalCards.vue'
import MemoryManager from './MemoryManager.vue'
import Icon from './Icon.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  messages: { type: Array, default: () => [] },
  runs: { type: Array, default: () => [] },
  loading: Boolean,
  hasEarlier: Boolean
})
const emit = defineEmits(['refresh', 'earlier', 'busy'])
const { state, question, answer, runId, usage, durationMs, error, busy, start, stop, resume,
  tools, node, citations, skill, memoryCount } = useAiStream(props.sessionId, () => emit('refresh'))
const assistant = shallowRef(props.runs[0]?.kind === 'agent' ? 'learning' : 'chat')
const canOperate = shallowRef(false)
const mode = computed(() => assistant.value === 'chat' ? 'chat' : 'agent')
const selectedSkill = ref('auto')
const approvalPending = ref(false)
const pauseBeforeTools = ref(false)
const draft = ref('')
const composer = ref(null)
const transcript = ref(null)
const followBottom = ref(true)
const cancelling = ref(false)
const cancelError = ref('')
const activeRun = computed(() => props.runs.find(run => run.status === 'running'))
const transientUser = computed(() => question.value && !props.messages.some(message => message.run_id === runId.value && message.role === 'user'))
const transientAnswer = computed(() => answer.value && !props.messages.some(message => message.run_id === runId.value && message.role === 'assistant'))
const assistantHtml = computed(() => new Map(props.messages
  .filter(message => message.role === 'assistant' && !message.redacted)
  .map(message => [message.id, renderMarkdown(message.content, window, { prefix: 'ai-' + message.id, images: false }).html])))
const streamingHtml = computed(() => transientAnswer.value
  ? renderMarkdown(answer.value, window, { prefix: 'ai-stream', images: false }).html
  : '')
const canSend = computed(() => !busy.value && !props.loading && !activeRun.value && !!draft.value.trim())
const incomplete = computed(() => ['failed', 'cancelled', 'interrupted'].includes(state.value))
const lastRun = computed(() => props.runs[0])
const latestAgentRun = computed(() => lastRun.value?.kind === 'agent' ? lastRun.value : null)
const measuredRun = computed(() => runId.value ? props.runs.find(run => run.id === runId.value) : lastRun.value)
const tokenCount = computed(() => usage.value?.total_tokens ?? measuredRun.value?.usage?.total_tokens)
const elapsed = computed(() => durationMs.value ?? measuredRun.value?.duration_ms)
const dateFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const timeLabel = value => dateFormat.format(new Date(value))
watch(() => session.user?.id, async (owner, _previous, onCleanup) => {
  canOperate.value = false
  if (assistant.value === 'operations' || assistant.value === 'collaborate') assistant.value = 'learning'
  if (owner == null) return
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  try {
    const access = await api('/ls/studio-articles/access', { signal: controller.signal, cache: 'no-store' })
    if (!controller.signal.aborted && session.user?.id === owner) canOperate.value = access.operator === true
  } catch {
    // Keep the restricted options hidden until Java confirms the user's access.
  }
}, { immediate: true })
watch(draft, async () => {
  await nextTick()
  const element = composer.value
  if (element) { element.style.height = 'auto'; element.style.height = Math.min(180, element.scrollHeight) + 'px' }
})
watch(busy, value => emit('busy', value), { immediate: true })
watch(() => [props.messages.length, answer.value.length, question.value], async () => {
  if (!followBottom.value) return
  await nextTick()
  if (transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight
}, { flush: 'post' })
function trackScroll() {
  const element = transcript.value
  followBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}
async function send() {
  if (!canSend.value) return
  const content = draft.value.trim()
  draft.value = ''
  followBottom.value = true
  await start(content, {
    mode: mode.value,
    assistant: assistant.value,
    pauseBeforeTools: pauseBeforeTools.value,
    ...(assistant.value === 'learning' && selectedSkill.value !== 'auto' ? { skill: selectedSkill.value } : {})
  })
  if (state.value === 'failed' && !runId.value) draft.value = content
  await nextTick()
  composer.value?.focus()
}
async function resumeRun(id) {
  if (assistant.value === 'chat') assistant.value = 'learning'
  await resume(id)
}
function composerKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    send()
  }
}
async function stopPrevious() {
  if (!activeRun.value || cancelling.value) return
  cancelling.value = true
  cancelError.value = ''
  try { await cancelAiRun(props.sessionId, activeRun.value.id); emit('refresh') }
  catch (cause) { cancelError.value = cause.message }
  finally { cancelling.value = false }
}
</script>

<template>
  <section class="conversation" aria-label="AI 对话">
    <div ref="transcript" class="transcript" tabindex="0" role="region" aria-label="聊天记录" @scroll.passive="trackScroll">
      <div class="message-column">
      <button v-if="hasEarlier" class="text-link earlier" type="button" :disabled="loading || busy" @click="emit('earlier')">{{ loading ? '正在读取…' : '查看更早的消息' }}</button>
      <div v-if="!messages.length && !question && !loading" class="empty-state"><h2>有什么可以帮你？</h2></div>
      <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role">
        <header><strong>{{ message.role === 'user' ? '你' : 'LearnTrail AI' }}</strong><time :datetime="message.created_at">{{ timeLabel(message.created_at) }}</time></header>
        <div v-if="assistantHtml.has(message.id)" class="chat-markdown" v-html="assistantHtml.get(message.id)"></div>
        <p v-else>{{ message.content }}</p>
        <AiSources v-if="message.role === 'assistant' && !message.redacted" :sources="message.citations || []" />
        <small v-if="message.status === 'failed'" class="message-state">本轮未完成，不纳入后续对话</small>
        <small v-else-if="message.status === 'pending'" class="message-state">正在等待生成结果</small>
      </article>
      <article v-if="transientUser" class="chat-message user"><header><strong>你</strong><small v-if="!runId">正在发送…</small></header><p>{{ question }}</p></article>
      <article v-if="transientAnswer" class="chat-message assistant">
        <header><strong>LearnTrail AI</strong><small v-if="busy">正在生成…</small></header>
        <div class="chat-markdown" v-html="streamingHtml"></div>
        <AiSources :sources="citations" />
        <small v-if="incomplete" class="message-state">{{ state === 'cancelled' ? '已停止' : '未完成' }}，本段回答不纳入后续对话</small>
      </article>
      </div>
    </div>
    <div class="composer-dock">
    <p v-if="busy || state === 'cancelled' || state === 'paused'" role="status" class="conversation-status">{{ state === 'stopping' ? '正在停止…' : state === 'connecting' ? '正在连接…' : state === 'cancelled' ? '已停止生成' : state === 'paused' ? '任务已暂停，可继续执行' : '正在生成回答，尚未确认完成…' }}</p>
    <details v-if="mode === 'agent' && (latestAgentRun || busy || tools.length)" class="agent-process" :open="latestAgentRun?.status === 'paused' && !approvalPending">
      <summary>执行过程<span v-if="node?.status === 'running'"> · 执行中</span></summary>
    <AgentActivity v-if="mode === 'agent'" :session-id="sessionId" :run="latestAgentRun" :live-run-id="runId"
      :live-tools="tools" :node="node" :busy="busy" :approval-pending="approvalPending" @resume="resumeRun" />
    </details>
    <details v-if="mode === 'agent' && latestAgentRun" class="approval-disclosure" :open="approvalPending">
      <summary>审批记录<span v-if="approvalPending"> · 等待你确认</span></summary>
    <ApprovalCards v-if="mode === 'agent'" :session-id="sessionId" :run="latestAgentRun" :busy="busy" @pending="approvalPending = $event" @resume="resumeRun" />
    </details>
    <p v-if="error || cancelError" role="alert" class="form-error">{{ error || cancelError }}</p>
    <div v-if="activeRun && !busy" class="pending-run" role="status">
      上一次生成尚未结束。
      <button class="text-link" type="button" :disabled="cancelling" @click="stopPrevious">{{ cancelling ? '正在停止…' : '停止上次生成' }}</button>
    </div>
    <div v-if="runId || lastRun" class="conversation-meta"><span v-if="mode === 'agent' && skill" :title="skill.description">{{ skill.name }} · {{ memoryCount }} 条记忆</span><span>{{ tokenCount == null ? 'Token 统计暂不可用' : tokenCount + ' Tokens' }}<span v-if="elapsed != null"> · {{ (elapsed / 1000).toFixed(2) }} 秒</span></span></div>
    <div class="composer-shell">
    <form class="composer" @submit.prevent="send">
      <label for="ai-question" class="sr-only">你的问题</label>
      <textarea id="ai-question" ref="composer" v-model="draft" name="question" rows="1" maxlength="20000" autocomplete="off" placeholder="发送消息…" aria-describedby="ai-keyboard-help" :disabled="busy || loading || !!activeRun" @keydown="composerKeydown"></textarea>
      <div class="composer-actions">
        <label for="ai-assistant" class="sr-only">对话助手</label>
        <select id="ai-assistant" v-model="assistant" class="assistant-picker" :disabled="busy || loading || !!activeRun">
          <option value="chat">普通对话</option>
          <option value="auto">自动分派</option>
          <option value="learning">学习助手</option>
          <option value="creator">创作者助手</option>
          <option v-if="canOperate" value="operations">运营助手</option>
          <option v-if="canOperate" value="collaborate">创作与运营协作</option>
        </select>
        <button v-if="busy" type="button" class="send-button" :disabled="state === 'stopping'" :aria-label="state === 'stopping' ? '正在停止…' : '停止生成'" @click="stop"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" /></svg><span class="sr-only">{{ state === 'stopping' ? '正在停止…' : '停止生成' }}</span></button>
        <button v-else type="submit" class="send-button" :disabled="!canSend" aria-label="发送消息" title="发送消息"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg><span class="sr-only">发送消息</span></button>
      </div>
    </form>
    <details class="chat-options">
      <summary title="对话设置"><Icon name="settings" :size="17" aria-hidden="true" /><span class="sr-only">对话设置</span></summary>
      <div class="options-panel">
        <label v-if="mode === 'agent'" class="pause-option"><input v-model="pauseBeforeTools" type="checkbox" :disabled="busy || loading || !!activeRun">工具执行前暂停</label>
        <SkillPicker v-if="assistant === 'learning'" v-model="selectedSkill" :disabled="busy || loading || !!activeRun" />
        <MemoryManager :session-id="sessionId" :disabled="busy || !!activeRun" />
      </div>
    </details>
    </div>
    <p id="ai-keyboard-help" class="keyboard-help">Enter 发送 · Shift + Enter 换行</p>
    </div>
  </section>
</template>

<style scoped>
.conversation { min-width: 0; min-height: 0; flex: 1; display: flex; flex-direction: column; color: #30332d; }
.transcript { flex: 1; min-height: 0; overflow-y: auto; padding: 24px; overscroll-behavior: contain; }
.message-column { width: 100%; max-width: 760px; margin-inline: auto; display: flex; flex-direction: column; }
.transcript:has(.empty-state) { display: flex; align-items: center; }
.empty-state { text-align: center; padding-bottom: 36px; }
.empty-state h2 { font-size: clamp(22px, 3vw, 28px); font-weight: 500; letter-spacing: -.025em; }
.chat-message { margin: 0 0 30px; min-width: 0; position: relative; }
.chat-message.user { align-self: flex-end; width: fit-content; max-width: 88%; }
.chat-message.user p { padding: 10px 18px; border-radius: 20px; background: #f1f2ee; margin: 0; }
.chat-message.user header { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.chat-message header { display: flex; align-items: baseline; gap: 12px; font-size: 12px; color: #73776c; }
.chat-message header strong { font-weight: 500; }
.chat-message header time { margin-left: auto; font-size: 10px; opacity: 0; }
.chat-message:hover header time, .chat-message:focus-within header time { opacity: 1; }
.chat-message p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.85; font-size: 15px; margin: 8px 0 0; }
.chat-markdown { overflow-wrap: anywhere; line-height: 1.75; font-size: 15px; margin-top: 8px; }
.chat-markdown :deep(p), .chat-markdown :deep(ul), .chat-markdown :deep(ol) { margin: 0 0 10px; }
.chat-markdown :deep(ul) { list-style: disc; padding-inline-start: 22px; }
.chat-markdown :deep(ol) { list-style: decimal; padding-inline-start: 22px; }
.chat-markdown :deep(li > p) { margin-bottom: 4px; }
.chat-markdown :deep(h2), .chat-markdown :deep(h3), .chat-markdown :deep(h4),
.chat-markdown :deep(h5), .chat-markdown :deep(h6) { font-size: 1.08em; margin: 16px 0 6px; }
.chat-markdown :deep(a) { color: var(--green); text-decoration: underline; }
.chat-markdown :deep(blockquote) { margin: 10px 0; padding-inline-start: 12px; border-inline-start: 2px solid var(--line); }
.chat-markdown :deep(pre), .chat-markdown :deep(table) { max-width: 100%; overflow-x: auto; margin: 10px 0; }
.chat-markdown :deep(pre) { padding: 10px 12px; border-radius: 8px; background: #f1f2ee; }
.chat-markdown :deep(table) { display: block; border-collapse: collapse; }
.chat-markdown :deep(th), .chat-markdown :deep(td) { padding: 4px 8px; border: 1px solid var(--line); }
.chat-markdown :deep(> :last-child) { margin-bottom: 0; }
.message-state { display: block; margin-top: 8px; color: #885427; font-size: 11px; }
.composer-dock { flex-shrink: 0; width: 100%; max-width: 808px; margin-inline: auto; padding: 0 24px 12px; }
.composer-shell { position: relative; }
.conversation-status, .pending-run { color: #74796c; font-size: 12px; margin: 6px 0; }
.conversation-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; font-size: 10px; color: #898d82; margin: 6px 4px; font-variant-numeric: tabular-nums; }
.composer { border: 1px solid #e3e5df; border-radius: 23px; padding: 10px 12px 9px; box-shadow: 0 3px 14px #222b1808; background: #fff; }
.composer:focus-within { border-color: #abb3a0; }
.composer textarea { display: block; width: 100%; resize: none; min-height: 44px; max-height: 180px; border: 0; padding: 6px 5px 10px; color: inherit; background: transparent; font-size: 14px; line-height: 1.6; box-shadow: none; border-radius: 8px; }
.composer textarea:focus-visible { outline: 2px solid #849375; outline-offset: 1px; }
.composer-actions { display: flex; align-items: center; gap: 7px; position: relative; }
.assistant-picker { max-width: 100%; min-width: 0; padding: 5px 7px; margin: 0; font: inherit; font-size: 12px; border: 0; border-radius: 8px; background: #eff1eb; color: #304329; }
.assistant-picker:focus-visible { outline: 2px solid #849375; }
.chat-options { position: absolute; right: 52px; bottom: 10px; }
.chat-options > summary { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; list-style: none; cursor: pointer; color: #7a8071; }
.chat-options > summary::-webkit-details-marker { display: none; }
.chat-options > summary:hover, .chat-options[open] > summary { background: #eff1eb; }
.options-panel { position: absolute; z-index: 16; bottom: calc(100% + 16px); right: -40px; width: min(360px, calc(100vw - 56px)); max-height: 48dvh; overflow-y: auto; padding: 16px; border: 1px solid #e2e5dc; border-radius: 12px; background: #fff; box-shadow: 0 8px 30px #18230d12; }
.pause-option { display: flex; gap: 6px; align-items: center; font-size: 12px; margin-bottom: 14px; }
.pause-option input { width: auto; }
.send-button { display: grid; place-items: center; margin-left: auto; width: 32px; height: 32px; flex-shrink: 0; border: 0; border-radius: 50%; padding: 0; background: #263020; color: #fff; cursor: pointer; }
.send-button:disabled { opacity: .35; cursor: default; }
.send-button:hover:not(:disabled) { background: #425538; }
.keyboard-help { margin: 7px 0 0; text-align: center; color: #969b8f; font-size: 10px; }
.agent-process, .approval-disclosure { font-size: 11px; color: #78806e; margin: 4px 0; }
.agent-process > summary, .approval-disclosure > summary { cursor: pointer; padding: 3px 0; }
.agent-process[open], .approval-disclosure[open] { max-height: 38dvh; overflow-y: auto; }
.approval-disclosure > summary > span { color: #6a542c; }
.earlier { display: block; margin: 4px auto 20px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@media (max-width: 600px) { .transcript { padding: 16px; } .composer-dock { padding: 0 12px max(10px, env(safe-area-inset-bottom)); } .chat-message.user { max-width: 92%; } .chat-message p, .chat-markdown { font-size: 14px; } .composer { border-radius: 20px; } }
</style>
