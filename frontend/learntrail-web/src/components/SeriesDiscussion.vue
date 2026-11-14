<script setup>
import { reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, notify } from '../api'
import Pagination from './Pagination.vue'
import DiscussionThread from './DiscussionThread.vue'
const props = defineProps({ courseId: { type: String, required: true } })
const result = ref({ list: [], total: 0, pages: 0 })
const page = ref(1)
const onlyMine = ref(false)
const selected = ref('')
const editing = ref(null)
const formOpen = ref(false)
const form = reactive({ title: '', description: '', anonymity: false })
const replyDrafts = reactive({})
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const hasInput = () => form.title.trim() || form.description.trim()
onBeforeRouteLeave(() => !busy.value && ((!hasInput() && !Object.values(replyDrafts).some(value => value.trim())) || window.confirm('还有未提交的讨论或回复，确定离开吗？')))
let requestId = 0
async function load() {
  const id = ++requestId
  loading.value = true; error.value = ''
  try {
    const data = await api('/ls/questions/page?courseId=' + props.courseId + '&onlyMine=' + onlyMine.value + '&pageNo=' + page.value + '&pageSize=5')
    if (id === requestId) {
      data.list.forEach(question => { replyDrafts[question.id] ??= '' })
      result.value = data
    }
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) loading.value = false }
}
watch([() => props.courseId, page], load, { immediate: true })
watch(onlyMine, () => { if (page.value !== 1) page.value = 1; else load() })
function clear() {
  editing.value = null; formOpen.value = false
  Object.assign(form, { title: '', description: '', anonymity: false })
}
function cancel() { if (!hasInput() || window.confirm('放弃尚未保存的讨论内容吗？')) clear() }
async function edit(question) {
  if (hasInput() && !window.confirm('切换讨论会放弃当前输入，继续吗？')) return
  try {
    const detail = await api('/ls/questions/' + question.id)
    Object.assign(form, { title: detail.title, description: detail.description, anonymity: detail.anonymity })
    editing.value = detail.id; formOpen.value = true
  } catch (e) { error.value = e.message }
}
async function save() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await api('/ls/questions' + (editing.value ? '/' + editing.value : ''), {
      method: editing.value ? 'PUT' : 'POST', body: { ...form, courseId: props.courseId }
    })
    clear(); page.value = 1; await load(); notify('讨论已保存')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function remove(question) {
  if (!window.confirm('删除这条讨论及其回复？历史记录会保留，页面不再展示。')) return
  busy.value = true
  try {
    await api('/ls/questions/' + question.id, { method: 'DELETE' })
    if (editing.value === question.id) clear()
    await load(); notify('讨论已删除')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <section class="content-panel section-gap" aria-labelledby="discussion-heading">
    <div class="section-title"><h2 id="discussion-heading">专栏讨论</h2><button class="btn light" :disabled="busy" :aria-expanded="formOpen" @click="formOpen = !formOpen">发起讨论</button></div>
    <p v-if="error" class="form-error" role="alert">{{ error }} <button class="text-link" @click="load">重试加载</button></p>
    <form v-show="formOpen" class="question-form" @submit.prevent="save">
      <h3>{{ editing ? '编辑我的讨论' : '与专栏读者交流' }}</h3>
      <label class="field">标题<input v-model="form.title" name="question-title" autocomplete="off" maxlength="254" required :disabled="busy" /></label>
      <label class="field">讨论内容<textarea v-model="form.description" name="question-description" autocomplete="off" rows="4" maxlength="10000" required :disabled="busy" /></label>
      <div class="detail-actions"><label><input v-model="form.anonymity" type="checkbox" :disabled="busy" /> 匿名发布</label><button class="btn primary" :disabled="busy || !form.title.trim() || !form.description.trim()">{{ busy ? '保存中…' : editing ? '保存修改' : '发布讨论' }}</button><button type="button" class="btn light" :disabled="busy" @click="cancel">取消</button></div>
    </form>
    <label class="mine-filter"><input v-model="onlyMine" type="checkbox" /> 只看我的讨论</label>
    <p v-if="loading" role="status">正在载入讨论…</p>
    <div v-else>
      <article v-for="question in result.list" :key="question.id" class="question">
        <small>{{ question.anonymity ? '匿名读者' : question.userName || '用户' }} · {{ question.createTime?.replace('T', ' ').slice(0, 16) }}</small>
        <h3>{{ question.title }}</h3><p class="question-body">{{ question.description }}</p>
        <div class="detail-actions">
          <button class="btn light" :aria-expanded="selected === question.id" @click="selected = selected === question.id ? '' : question.id">{{ selected === question.id ? '收起回复' : '查看回复（' + (question.answerTimes || 0) + '）' }}</button>
          <template v-if="question.mine"><button class="text-link" :disabled="busy" @click="edit(question)">编辑</button><button class="text-link" :disabled="busy" @click="remove(question)">删除</button></template>
        </div>
        <DiscussionThread v-if="selected === question.id" v-model="replyDrafts[question.id]" :question-id="question.id" @changed="load" />
      </article>
      <p v-if="!result.list.length" class="content-tip">还没有讨论，从你的第一个想法开始。</p>
    </div>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading" @change="page = $event" />
  </section>
</template>

<style scoped>
.question { padding: 22px 0; border-bottom: 1px solid var(--line); }
.question h3 { margin: 10px 0; }
.question small { color: var(--muted); }
.question-body { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.question-form { padding: 20px; background: var(--surface-soft, #f5f7f1); border-radius: 10px; margin-bottom: 20px; }
.mine-filter { display: block; margin: 16px 0; font-size: 14px; }
</style>
