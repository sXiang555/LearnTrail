<script setup>
import { ref, watch } from 'vue'
import { api, notify, studioApi } from '../api'
import Pagination from './Pagination.vue'
const page = ref(1)
const search = ref('')
const result = ref({ list: [], total: 0, pages: 0 })
const loading = ref(false)
const busy = ref('')
const error = ref('')
const labels = { draft: '草稿', published: '已发布', unlisted: '已下架', archived: '已归档' }
let requestId = 0
async function load() {
  const id = ++requestId
  loading.value = true; error.value = ''
  try {
    const data = await api(studioApi + '/series/page?' + new URLSearchParams({ scope: 'operations', sort: 'newest', q: search.value, pageNo: page.value, pageSize: 10 }))
    if (id === requestId) result.value = data
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) loading.value = false }
}
watch(page, load, { immediate: true })
function query() { if (page.value !== 1) page.value = 1; else load() }
async function moderate(item) {
  if (!window.confirm(item.blocked ? '解除屏蔽后恢复此专栏原有发布状态，继续吗？' : '屏蔽此专栏？它将停止展示、新的播放授权与讨论；已签发视频链接到期前仍可能使用。已有权益和积分流水保留。')) return
  busy.value = item.id
  try {
    await api(studioApi + '/operations/series/' + item.id + '/blocked/' + !item.blocked, { method: 'PUT' })
    await load(); notify('专栏屏蔽状态已更新')
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
</script>

<template>
  <section class="content-panel">
    <h2>平台专栏</h2><p class="content-tip">创作者下架保留已有观看权益；运营屏蔽停止新的播放授权与讨论，解除后保留原发布状态。已签发的视频链接最长 2 小时后失效。</p>
    <form class="ops-search" @submit.prevent="query"><label class="field">搜索专栏<input v-model.trim="search" name="series-search" autocomplete="off" maxlength="80" placeholder="标题或创作者" /></label><button class="btn light" :disabled="loading">查询</button></form>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在载入专栏…</p>
    <article v-for="item in result.list" :key="item.id" class="ops-row">
      <div><span class="tag">{{ item.blocked ? '运营已屏蔽' : labels[item.status] || item.status }}</span><h3>{{ item.title }}</h3><p class="content-tip">{{ item.creatorName }} · {{ item.episodeCount || 0 }} 集</p></div>
      <button class="btn light" :disabled="!!busy" @click="moderate(item)">{{ busy === item.id ? '处理中…' : item.blocked ? '解除屏蔽' : '屏蔽专栏' }}</button>
    </article>
    <p v-if="!loading && !result.list.length" class="content-tip">没有匹配的专栏。</p>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading || !!busy" @change="page = $event" />
  </section>
</template>

<style scoped>
.ops-search { display: flex; gap: 12px; align-items: end; margin-top: 20px; flex-wrap: wrap; }
.ops-search .field { flex: 1; min-width: 180px; margin: 0; }
.ops-row { display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 20px 0; border-bottom: 1px solid var(--line); }
.ops-row > div { min-width: 0; overflow-wrap: anywhere; }
.ops-row button { flex-shrink: 0; }
.ops-row h3 { margin: 10px 0; }
@media (max-width: 600px) { .ops-row { align-items: start; flex-direction: column; } }
</style>
