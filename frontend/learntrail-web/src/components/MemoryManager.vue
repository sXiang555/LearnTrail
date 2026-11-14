<script setup>
import { onScopeDispose, ref } from 'vue'
import { deleteAiMemory, listAiMemories, saveAiMemory } from '../aiApi.js'
const props = defineProps({ sessionId: { type: String, required: true }, disabled: Boolean })
const items = ref([]), error = ref(''), busy = ref(false), editing = ref(null), deleting = ref('')
const content = ref(''), key = ref('answer_style'), confirmed = ref(false), expiry = ref('')
const controller = new AbortController()
let disposed = false
onScopeDispose(() => { disposed = true; controller.abort() })
const names = { answer_style: '回答风格', preferred_language: '讲解语言', learning_goal: '学习目标', study_habit: '学习习惯' }
async function load() {
  error.value = ''; busy.value = true
  try { const result = await listAiMemories(controller.signal); if (!disposed) items.value = result.items }
  catch (cause) { if (!disposed) error.value = cause.message }
  finally { if (!disposed) busy.value = false }
}
function edit(item) {
  editing.value = item; content.value = item?.content || ''; key.value = item?.key || 'answer_style'
  confirmed.value = false; expiry.value = item?.expires_at ? new Date(new Date(item.expires_at).getTime() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''
}
async function save() {
  if (!confirmed.value || busy.value || props.disabled) return
  busy.value = true; error.value = ''
  try {
    const body = { memory_type: ['answer_style', 'preferred_language'].includes(key.value) ? 'preference' : key.value,
      key: key.value, content: content.value.trim(), confirmed: true, source_session_id: props.sessionId,
      expires_at: expiry.value ? new Date(expiry.value).toISOString() : null,
      ...(editing.value ? { version: editing.value.version } : {}) }
    await saveAiMemory(body, editing.value?.id)
    if (!disposed) { edit(null); await load() }
  } catch (cause) { if (!disposed) error.value = cause.message }
  finally { if (!disposed) busy.value = false }
}
async function remove(item) {
  if (busy.value || props.disabled) return
  busy.value = true; error.value = ''
  try { await deleteAiMemory(item); if (!disposed) { deleting.value = ''; if (editing.value?.id === item.id) edit(null); await load() } }
  catch (cause) { if (!disposed) error.value = cause.message }
  finally { if (!disposed) busy.value = false }
}
</script>

<template>
  <details class="memory-panel" @toggle="event => { if (event.target.open) load() }">
    <summary>我的长期学习记忆</summary>
    <p>仅保存本人确认的非敏感学习偏好、目标和习惯，跨会话按任务选用。请勿填写密码、联系方式或其他敏感信息。</p>
    <p v-if="busy" role="status">正在处理…</p>
    <p v-if="error" role="alert" class="form-error">{{ error }}</p>
    <p v-if="!items.length && !busy">暂无已确认的记忆。</p>
    <article v-for="item in items" :key="item.id">
      <strong>{{ names[item.key] }}</strong><p>{{ item.content }}</p>
      <small>{{ item.source_kind === 'user_quote' ? '来自本人原话，经审批确认' : '本人手动确认' }} · 版本 {{ item.version }}</small>
      <details><summary>查看来源</summary><p v-if="item.source_quote">{{ item.source_quote }}</p><RouterLink v-if="item.source_session_id" :to="'/ai/' + item.source_session_id">查看来源会话</RouterLink><small v-else>未关联会话</small></details>
      <button type="button" class="text-link" :disabled="busy || disabled" @click="edit(item)">修改</button>
      <button type="button" class="text-link" :disabled="busy || disabled" @click="deleting = item.id">删除</button>
      <span v-if="deleting === item.id">确认删除？ <button type="button" class="text-link" :disabled="busy || disabled" @click="remove(item)">确认删除</button> <button type="button" class="text-link" @click="deleting = ''">保留</button></span>
    </article>
    <form @submit.prevent="save">
      <h4>{{ editing ? '修改记忆' : '新增记忆' }}</h4>
      <label for="memory-key">类型</label><select id="memory-key" v-model="key" :disabled="busy || disabled"><option v-for="(name, value) in names" :key="value" :value="value">{{ name }}</option></select>
      <label for="memory-content">本人确认的内容</label><textarea id="memory-content" v-model="content" minlength="2" maxlength="500" required rows="2" :disabled="busy || disabled"></textarea>
      <label for="memory-expiry">过期时间（可选）</label><input id="memory-expiry" v-model="expiry" type="datetime-local" :disabled="busy || disabled">
      <label class="confirm"><input v-model="confirmed" type="checkbox" :disabled="busy || disabled">我确认这是本人信息，并同意跨会话使用</label>
      <button type="submit" class="btn light" :disabled="busy || disabled || !confirmed || content.trim().length < 2">确认保存</button>
      <button v-if="editing" type="button" class="text-link" @click="edit(null)">取消修改</button>
    </form>
  </details>
</template>

<style scoped>
.memory-panel { border-top: 1px solid var(--line); padding: 12px 0; font-size: 13px; }
summary { cursor: pointer; padding: 6px 0; } article { padding: 10px 0; border-bottom: 1px solid var(--line); }
article p { white-space: pre-wrap; overflow-wrap: anywhere; } small { color: var(--secondary-text); }
form { display: grid; gap: 7px; max-width: 600px; } label { font-weight: 500; }
textarea, select, input[type=datetime-local] { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 7px; background: white; color: inherit; }
.confirm { display: flex; align-items: center; gap: 6px; } .confirm input { width: auto; }
.text-link { margin-right: 10px; } h4 { margin-bottom: 4px; }
</style>
