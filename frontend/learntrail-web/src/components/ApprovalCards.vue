<script setup>
import { computed, ref, watch } from 'vue'
import { decideAiApproval, listAiApprovals } from '../aiApi.js'
const props = defineProps({ sessionId: { type: String, required: true }, run: { type: Object, default: null }, busy: Boolean })
const emit = defineEmits(['resume', 'pending'])
const items = ref([]), error = ref(''), submitting = ref('')
const pending = computed(() => items.value.some(item => item.status === 'pending'))
const labels = { pending: '等待本人确认', approved: '已批准，等待恢复', rejected: '已拒绝', expired: '已过期', cancelled: '已取消', executing: '执行中', executed: '已执行', failed: '执行失败', unknown: '执行结果未知，禁止自动重试' }
const names = { save_learning_plan_demo: '保存演示学习计划', remember_learning_preference: '保存学习记忆' }
watch(pending, value => emit('pending', value), { immediate: true })
watch(() => [props.sessionId, props.run?.id, props.run?.status, props.busy], ([sid, rid, , busy], previous, onCleanup) => {
  items.value = []; error.value = ''
  if (!rid || busy) return
  const controller = new AbortController()
  let current = true
  async function load() {
    try {
      const result = await listAiApprovals(sid, rid, controller.signal)
      if (current) items.value = result.items
    } catch (cause) { if (current && cause.name !== 'AbortError') error.value = cause.message }
  }
  load()
  const timer = setInterval(() => { if (pending.value && !submitting.value) load() }, 5000)
  onCleanup(() => { current = false; controller.abort(); clearInterval(timer) })
}, { immediate: true })
async function decide(item, action) {
  if (submitting.value || props.busy) return
  submitting.value = item.approval_id; error.value = ''
  try {
    await decideAiApproval(props.sessionId, item, action)
    const result = await listAiApprovals(props.sessionId, props.run.id)
    items.value = result.items
    if (!pending.value && props.run.status === 'paused') emit('resume', props.run.id)
  } catch (cause) { error.value = cause.message }
  finally { submitting.value = '' }
}
</script>

<template>
  <section v-if="items.length || error" class="approvals" aria-label="操作审批">
    <article v-for="item in items" :key="item.approval_id" class="approval-card">
      <header><strong>{{ names[item.tool_name] || item.tool_name }}</strong><span role="status">{{ labels[item.status] || item.status }}</span></header>
      <p>目标：{{ item.resource.target || '已授权 MCP 演示操作' }}</p>
      <details><summary>查看参数与当前资源状态</summary><pre>{{ JSON.stringify({ parameters: item.arguments, resource: item.resource }, null, 2) }}</pre></details>
      <small>有效期至 {{ new Date(item.expires_at).toLocaleString('zh-CN') }}；批准后会再次检查权限与资源版本。</small>
      <div v-if="item.status === 'pending'" class="actions">
        <button type="button" class="btn primary" :disabled="!!submitting || busy" @click="decide(item, 'approve')">确认并继续</button>
        <button type="button" class="btn light" :disabled="!!submitting || busy" @click="decide(item, 'reject')">拒绝</button>
        <button type="button" class="text-link" :disabled="!!submitting || busy" @click="decide(item, 'cancel')">取消此操作</button>
      </div>
      <details v-if="item.result"><summary>查看执行结果</summary><pre>{{ JSON.stringify(item.result, null, 2) }}</pre></details>
      <p v-if="item.error_code" role="status">{{ item.error_code }}</p>
    </article>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.approvals { display: grid; gap: 10px; margin: 12px 0; }
.approval-card { border: 1px solid #b7c7a4; background: #f7faf3; border-radius: 10px; padding: 14px; font-size: 13px; }
header, .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
header { justify-content: space-between; } small { display: block; color: var(--secondary-text); margin: 10px 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 240px; overflow: auto; }
summary { cursor: pointer; padding: 6px 0; }
</style>
