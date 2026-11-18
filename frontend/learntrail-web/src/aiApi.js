import { getAccessToken, session } from './api.js'

export class AiError extends Error {
  constructor(code, message, status = 0) {
    super(message)
    this.code = code
    this.status = status
  }
}

function sameUser(owner) {
  if (session.user?.id !== owner || !session.token) throw new AiError('SESSION_CHANGED', '登录状态已变化，请重新打开会话')
}

async function aiFetch(path, { method = 'GET', body, signal } = {}) {
  const owner = session.user?.id
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getAccessToken(attempt === 1)
    sameUser(owner)
    const response = await fetch('/ai/v1' + path, {
      method, signal, credentials: 'omit',
      headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined
    })
    sameUser(owner)
    if (response.status === 401 && attempt === 0) {
      await response.body?.cancel()
      continue
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new AiError(payload?.error?.code || 'AI_UNAVAILABLE', payload?.error?.message || 'AI 服务暂时无法连接，请稍后重试', response.status)
    }
    return response
  }
}

async function aiJson(path, options) {
  const owner = session.user?.id
  const response = await aiFetch(path, options)
  const body = await response.json().catch(() => { throw new AiError('INVALID_RESPONSE', 'AI 服务响应格式异常') })
  sameUser(owner)
  return body
}

export const listAiSessions = (offset = 0, signal) => aiJson('/sessions?limit=20&offset=' + offset, { signal })
export const createAiSession = title => aiJson('/sessions', { method: 'POST', body: { title } })
export const getAiSession = (id, { offset = 0, limit = 100, signal } = {}) => aiJson(
  '/sessions/' + encodeURIComponent(id) + '?message_limit=' + limit + '&message_offset=' + offset, { signal }
)
export const cancelAiRun = (id, runId) => aiJson(
  '/sessions/' + encodeURIComponent(id) + '/runs/' + encodeURIComponent(runId) + '/cancel', { method: 'POST' }
)
export const getAiAgentTrace = (id, runId, signal) => aiJson(
  '/sessions/' + encodeURIComponent(id) + '/agent/runs/' + encodeURIComponent(runId), { signal }
)
export const getAiGraphState = (id, runId, signal) => aiJson(
  '/sessions/' + encodeURIComponent(id) + '/agent/runs/' + encodeURIComponent(runId) + '/state', { signal }
)
export const getAiSkills = signal => aiJson('/skills', { signal })
export const listAiApprovals = (id, runId, signal) => aiJson('/sessions/' + encodeURIComponent(id) + '/approvals' +
  (runId ? '?run_id=' + encodeURIComponent(runId) : ''), { signal })
export const decideAiApproval = (id, approval, action) => aiJson('/sessions/' + encodeURIComponent(id) +
  '/approvals/' + encodeURIComponent(approval.approval_id) + '/decision', {
    method: 'POST', body: { action, expected_hash: approval.arguments_hash }
  })
export const listAiMemories = signal => aiJson('/memories', { signal })
export const saveAiMemory = (body, id) => aiJson('/memories' + (id ? '/' + encodeURIComponent(id) : ''), { method: id ? 'PUT' : 'POST', body })
export const deleteAiMemory = item => aiJson('/memories/' + encodeURIComponent(item.id) + '?version=' + item.version, { method: 'DELETE' })

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const protocolError = () => new AiError('INVALID_STREAM', '流式响应格式异常，请刷新会话确认结果')
const specialistRoles = ['learning', 'creator', 'operations']
const agentNodes = ['agent', 'tools', 'skill', 'memory', 'creator', 'operations', 'synthesis']

export async function readAiEvents(response, onEvent, signal) {
  if (response.headers.get('content-type')?.split(';')[0].trim() !== 'text/event-stream' || !response.body) throw protocolError()
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = '', event = '', data = [], size = 0, terminal = false, runId = '', outputSize = 0
  const abort = () => { reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', abort, { once: true })
  async function line(value) {
    if (value === '') {
      if (data.length) {
        let payload
        try { payload = JSON.parse(data.join('\n')) } catch { throw protocolError() }
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw protocolError()
        if (event === 'meta') {
          if (runId || !uuid.test(payload.run_id) || !uuid.test(payload.session_id)) throw protocolError()
          runId = payload.run_id
        } else if (!runId) throw protocolError()
        if (event === 'delta') {
          if (typeof payload.content !== 'string') throw protocolError()
          outputSize += payload.content.length
          if (outputSize > 200000) throw protocolError()
        } else if (event === 'usage') {
          for (const field of ['prompt_tokens', 'completion_tokens', 'total_tokens']) {
            if (payload[field] != null && (!Number.isSafeInteger(payload[field]) || payload[field] < 0)) throw protocolError()
          }
        } else if (event === 'paused') {
          if (payload.run_id !== runId || payload.status !== 'paused') throw protocolError()
          terminal = true
        } else if (event === 'done') {
          if (payload.run_id !== runId || payload.status !== 'succeeded') throw protocolError()
          terminal = true
        } else if (['tool_start', 'tool_end'].includes(event)) {
          if (typeof payload.call_id !== 'string' || !payload.call_id || payload.call_id.length > 200 ||
              typeof payload.name !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(payload.name)) throw protocolError()
          if (event === 'tool_end' && (typeof payload.ok !== 'boolean' || !Number.isSafeInteger(payload.duration_ms) || payload.duration_ms < 0)) throw protocolError()
        } else if (event === 'node') {
          if (!agentNodes.includes(payload.node) || !['running', 'completed'].includes(payload.status)) throw protocolError()
        } else if (event === 'delegation') {
          if (![...specialistRoles, 'auto', 'collaborate'].includes(payload.assistant) ||
              !Array.isArray(payload.specialists) || !payload.specialists.length || payload.specialists.length > specialistRoles.length ||
              payload.specialists.some(role => !specialistRoles.includes(role))) throw protocolError()
        } else if (event === 'skill') {
          if (payload.name != null && (typeof payload.name !== 'string' || !/^[a-z][a-z0-9-]{1,48}$/.test(payload.name))) throw protocolError()
        } else if (event === 'memory') {
          if (!Number.isSafeInteger(payload.count) || payload.count < 0 || payload.count > 8) throw protocolError()
        } else if (event === 'approval') {
          if (!Array.isArray(payload.items) || payload.items.length > 16 || payload.items.some(item => !uuid.test(item.approval_id) || !/^[0-9a-f]{64}$/.test(item.arguments_hash))) throw protocolError()
        } else if (event === 'citations') {
          if (!Array.isArray(payload.items) || payload.items.length > 20) throw protocolError()
        } else if (!['meta', 'error', 'model_start', 'model_end', 'answer_reset'].includes(event)) throw protocolError()
        await onEvent(event, payload)
        if (event === 'error') throw new AiError(payload.error?.code || 'GENERATION_FAILED', payload.error?.message || '生成未完成，请刷新会话确认结果')
      }
      event = ''; data = []; size = 0
      return
    }
    if (value.startsWith(':')) return
    const separator = value.indexOf(':')
    const field = separator < 0 ? value : value.slice(0, separator)
    const contents = separator < 0 ? '' : value.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') event = contents
    if (field === 'data') { data.push(contents); size += contents.length }
    if (size > 1048576 || value.length > 1048576) throw protocolError()
  }
  try {
    while (!terminal) {
      if (signal?.aborted) throw new DOMException('已停止接收', 'AbortError')
      const chunk = await reader.read()
      if (signal?.aborted) throw new DOMException('已停止接收', 'AbortError')
      try { buffer += decoder.decode(chunk.value, { stream: !chunk.done }) } catch { throw protocolError() }
      if (buffer.length > 1048576) throw protocolError()
      let delimiter
      while (!terminal && (delimiter = /\r\n|\r|\n/.exec(buffer))) {
        if (!chunk.done && delimiter[0] === '\r' && delimiter.index === buffer.length - 1) break
        const value = buffer.slice(0, delimiter.index).replace(/^\uFEFF/, '')
        buffer = buffer.slice(delimiter.index + delimiter[0].length)
        await line(value)
      }
      if (chunk.done) break
    }
    if (!terminal) throw new AiError('STREAM_INTERRUPTED', '连接已中断，回复未确认完成，请刷新会话查看状态')
  } finally {
    signal?.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export async function streamAiChat(id, content, onEvent, signal, options = {}) {
  const owner = session.user?.id
  const suffix = options.resumeRunId ? '/agent/runs/' + encodeURIComponent(options.resumeRunId) + '/resume/stream' :
    options.mode === 'agent' ? '/agent/stream' : '/chat/stream'
  const body = options.resumeRunId ? {} : options.mode === 'agent' ?
    {
      content,
      pause_before_tools: options.pauseBeforeTools === true,
      ...(options.assistant ? { assistant: options.assistant } : {}),
      ...(options.skill ? { skill: options.skill } : {})
    } : { content }
  const response = await aiFetch('/sessions/' + encodeURIComponent(id) + suffix, {
    method: 'POST', body, signal
  })
  await readAiEvents(response, async (event, data) => { sameUser(owner); await onEvent(event, data) }, signal)
}
