import { computed, onScopeDispose, readonly, ref } from 'vue'
import { cancelAiRun, streamAiChat } from '../aiApi.js'

export function useAiStream(sessionId, onSettled) {
  const state = ref('idle')
  const question = ref('')
  const answer = ref('')
  const runId = ref('')
  const usage = ref(null)
  const durationMs = ref(null)
  const error = ref('')
  const tools = ref([])
  const node = ref(null)
  const citations = ref([])
  const checkpoint = ref(null)
  const skill = ref(null)
  const memoryCount = ref(0)
  const busy = computed(() => ['connecting', 'generating', 'stopping'].includes(state.value))
  let controller = null
  let disposed = false
  let confirmedCancel = false

  async function start(content, options = {}) {
    if (busy.value || disposed) return
    const current = new AbortController()
    controller = current
    confirmedCancel = false
    question.value = content
    answer.value = ''
    runId.value = ''
    usage.value = null
    durationMs.value = null
    error.value = ''
    tools.value = []; node.value = null; citations.value = []; checkpoint.value = null
    skill.value = null; memoryCount.value = 0
    state.value = 'connecting'
    try {
      await streamAiChat(sessionId, content, (event, data) => {
        if (disposed) return
        if (event === 'meta') { runId.value = data.run_id; state.value = 'generating' }
        if (event === 'delta') answer.value += data.content
        if (event === 'answer_reset') answer.value = ''
        if (event === 'node') node.value = data
        if (event === 'skill') skill.value = data.selection
        if (event === 'memory') memoryCount.value = data.count
        if (event === 'tool_start') tools.value.push({ ...data, status: 'running' })
        if (event === 'tool_end') {
          const item = [...tools.value].reverse().find(item => item.call_id === data.call_id && item.status === 'running')
          if (item) Object.assign(item, data, { status: data.ok ? 'succeeded' : 'failed' })
        }
        if (event === 'citations') citations.value = data.items
        if (event === 'usage') usage.value = Object.hasOwn(data, 'usage') ? data.usage : data
        if (event === 'error' && data.discard_answer) { answer.value = ''; citations.value = [] }
        if (event === 'paused') { state.value = 'paused'; checkpoint.value = data.checkpoint; durationMs.value = data.duration_ms }
        if (event === 'done') {
          state.value = 'succeeded'
          durationMs.value = data.duration_ms
          usage.value = data.usage
          citations.value = data.citations || []
          checkpoint.value = data.checkpoint
        }
      }, current.signal, options)
    } catch (cause) {
      if (disposed) return
      if (state.value !== 'succeeded') {
        state.value = confirmedCancel || cause.code === 'RUN_CANCELLED' ? 'cancelled' :
          current.signal.aborted || cause.code === 'STREAM_INTERRUPTED' ? 'interrupted' : 'failed'
        error.value = state.value === 'cancelled' ? '' : current.signal.aborted ?
          '连接已断开，请查看历史确认生成状态' : cause.message || '生成失败，请稍后重试'
      }
    } finally {
      if (controller === current) controller = null
      if (!disposed) {
        if (busy.value) state.value = 'interrupted'
        for (const item of tools.value) if (item.status === 'running') item.status = 'interrupted'
        onSettled?.()
      }
    }
  }

  const resume = id => start('', { mode: 'agent', resumeRunId: id })

  async function stop() {
    if (!controller || state.value === 'stopping') return
    const current = controller
    state.value = 'stopping'
    try {
      if (runId.value) {
        const run = await cancelAiRun(sessionId, runId.value)
        confirmedCancel = run.status === 'cancelled'
        if (run.status === 'succeeded') state.value = 'succeeded'
      }
    } catch (cause) {
      if (!disposed) error.value = '停止状态尚未确认：' + cause.message
    } finally {
      current.abort()
    }
  }

  onScopeDispose(() => { disposed = true; controller?.abort() })
  return {
    state: readonly(state), question: readonly(question), answer: readonly(answer), runId: readonly(runId),
    usage: readonly(usage), durationMs: readonly(durationMs), error: readonly(error), busy, start, stop, resume,
    tools: readonly(tools), node: readonly(node), citations: readonly(citations), checkpoint: readonly(checkpoint),
    skill: readonly(skill), memoryCount: readonly(memoryCount)
  }
}
