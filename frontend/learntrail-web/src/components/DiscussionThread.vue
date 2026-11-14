<script setup>
import { ref, watch } from 'vue'
import { api, notify, refreshWallet } from '../api'
import Pagination from './Pagination.vue'
const props = defineProps({ questionId: { type: String, required: true } })
const emit = defineEmits(['changed'])
const result = ref({ list: [], total: 0, pages: 0 })
const page = ref(1)
const content = defineModel({ type: String, required: true })
const anonymous = ref(false)
const busy = ref(false)
const loading = ref(false)
const error = ref('')
let requestId = 0
async function load() {
  const id = ++requestId
  loading.value = true; error.value = ''
  try {
    const data = await api('/ls/replies/page?questionId=' + props.questionId + '&pageNo=' + page.value + '&pageSize=5')
    if (id === requestId) result.value = data
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) loading.value = false }
}
watch([() => props.questionId, page], load, { immediate: true })
async function reply() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await api('/ls/replies', { method: 'POST', body: { questionId: props.questionId, content: content.value, anonymity: anonymous.value } })
    content.value = ''; page.value = 1
    await load(); await refreshWallet().catch(() => {}); emit('changed'); notify('回复已发布')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <div class="discussion-thread">
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在载入回复…</p>
    <div v-else>
      <article v-for="item in result.list" :key="item.id" class="reply">
        <small>{{ item.anonymity ? '匿名读者' : item.userName || '用户' }} · {{ item.createTime?.replace('T', ' ').slice(0, 16) }}</small>
        <p>{{ item.content }}</p>
      </article>
      <p v-if="!result.list.length" class="content-tip">暂时没有回复，分享你的想法吧。</p>
      <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading" @change="page = $event" />
    </div>
    <form @submit.prevent="reply">
      <label class="field">我的回复<textarea v-model="content" name="reply-content" autocomplete="off" rows="3" maxlength="10000" required :disabled="busy" /></label>
      <div class="detail-actions">
        <label><input v-model="anonymous" type="checkbox" :disabled="busy" /> 匿名回复</label>
        <button class="btn primary" :disabled="busy || !content.trim()">{{ busy ? '提交中…' : '发布回复' }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.discussion-thread { border-top: 1px solid var(--line); margin-top: 16px; padding-top: 16px; }
.reply { border-bottom: 1px solid var(--line); padding: 12px 0; }
.reply p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.reply small { color: var(--muted); }
form { margin-top: 20px; }
</style>
