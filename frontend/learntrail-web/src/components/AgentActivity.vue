<script setup>
import { computed, ref, watch } from 'vue'
import { getAiAgentTrace, getAiGraphState } from '../aiApi.js'

const props = defineProps({
  sessionId: { type: String, required: true },
  run: { type: Object, default: null },
  liveRunId: { type: String, default: '' },
  liveTools: { type: Array, default: () => [] },
  node: { type: Object, default: null },
  approvalPending: Boolean,
  busy: Boolean
})
const emit = defineEmits(['resume'])
const savedTools = ref([])
const snapshot = ref(null)
const loading = ref(false)
const error = ref('')
const labels = { search_knowledge_base: '检索文章知识', search_articles: '查找已发布文章', get_learning_progress: '查询本人学习进度', remember_learning_preference: '确认学习记忆', save_learning_plan_demo: '保存演示计划' }
const statusLabel = { running: '执行中', succeeded: '成功', failed: '失败', interrupted: '已中断' }
const tools = computed(() => props.liveRunId && props.liveTools.length ? props.liveTools : savedTools.value)
const canResume = computed(() => props.run && ['paused', 'failed', 'cancelled'].includes(props.run.status) && snapshot.value && !snapshot.value.redacted)
const nodeNames = {
  tools: '正在执行工具…', agent: '学习助手正在回答…', skill: '正在选择任务技能…', memory: '正在读取相关学习记忆…',
  creator: '创作者助手正在拟稿…', operations: '运营助手正在分析…', synthesis: '正在汇总两个助手的结果…'
}
const specialistNames = { learning: '学习助手', creator: '创作者助手', operations: '运营助手' }
const specialists = computed(() => (snapshot.value?.specialists || []).map(role => specialistNames[role]).filter(Boolean).join('、'))
const nodeLabel = computed(() => props.node?.status === 'running' ? nodeNames[props.node.node] || '' : '')

watch(() => [props.sessionId, props.run?.id, props.run?.status, props.busy], async ([sid, rid, , busy], previous, onCleanup) => {
  snapshot.value = null; savedTools.value = []; error.value = ''
  if (!rid || busy) { loading.value = false; return }
  const controller = new AbortController()
  let current = true
  onCleanup(() => { current = false; controller.abort() })
  loading.value = true
  try {
    const trace = await getAiAgentTrace(sid, rid, controller.signal)
    if (!current) return
    if (trace.redacted) { error.value = '查询来源或学习状态已变化，请重新提问。'; return }
    const items = []
    for (const event of trace.events) {
      const data = event.payload
      if (event.kind === 'tool_start') items.push({ ...data, status: 'interrupted' })
      if (event.kind === 'tool_end') {
        const item = [...items].reverse().find(item => item.call_id === data.call_id && item.status === 'interrupted')
        if (item) Object.assign(item, data, { status: data.ok ? 'succeeded' : data.error_code === 'RUN_CANCELLED' ? 'interrupted' : 'failed' })
      }
    }
    savedTools.value = items
    const state = await getAiGraphState(sid, rid, controller.signal)
    if (current) snapshot.value = state
  } catch (cause) { if (current && cause.name !== 'AbortError') error.value = cause.message }
  finally { if (current) loading.value = false }
}, { immediate: true })
</script>

<template>
  <section class="agent-activity" aria-label="Agent 执行过程" :aria-busy="loading">
    <p v-if="busy && nodeLabel" role="status">{{ nodeLabel }}</p>
    <p v-if="!busy && specialists">{{ specialists }}</p>
    <ol v-if="tools.length" class="tool-list">
      <li v-for="(tool, index) in tools" :key="tool.call_id + ':' + index">
        <strong>{{ labels[tool.name] || (tool.name.startsWith('mcp_') ? 'MCP · ' + tool.name : tool.name) }}</strong>
        <span :class="{ failed: tool.status === 'failed' }">{{ statusLabel[tool.status] || '已结束' }}</span>
        <small v-if="tool.duration_ms != null">{{ (tool.duration_ms / 1000).toFixed(2) }} 秒</small>
        <small v-if="tool.error_code">{{ tool.error_code }}</small>
      </li>
    </ol>
    <p v-if="loading" role="status">正在恢复执行过程…</p>
    <p v-if="error" class="activity-error" role="status">{{ error }}</p>
    <div v-if="canResume" class="resume-row">
      <span>{{ run.status === 'paused' ? '任务已暂停，可从已保存的步骤继续。' : '任务未完成，可尝试从已保存的步骤恢复。' }}</span>
      <button type="button" class="btn light" :disabled="busy || loading || approvalPending" @click="emit('resume', run.id)">{{ approvalPending ? '请先处理审批卡片' : '继续执行' }}</button>
    </div>
  </section>
</template>

<style scoped>
.agent-activity { margin: 10px 0; color: var(--secondary-text); font-size: 13px; }
.tool-list { list-style: none; padding: 0; display: grid; gap: 8px; }
.tool-list li { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; padding: 9px 12px; background: #f4f6f0; border-radius: 8px; }
.tool-list strong { color: var(--primary-text); font-weight: 500; }
.failed, .activity-error { color: #885427; }
.resume-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
</style>
