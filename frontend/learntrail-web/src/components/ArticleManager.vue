<script setup>
import { nextTick, onBeforeUnmount, reactive, ref, shallowRef, useTemplateRef, watch } from 'vue'
import { api, notify } from '../api'
import ArticleEditor from './ArticleEditor.vue'
import Pagination from './Pagination.vue'

const props = defineProps({ scope: { type: String, default: 'mine' } })
const result = ref({ list: [], total: 0, pages: 0 })
const page = shallowRef(1)
const loading = shallowRef(false)
const busy = shallowRef('')
const error = shallowRef('')
const editing = shallowRef(null)
const published = shallowRef('')
const filters = reactive({ q: '', status: '' })
const anchor = useTemplateRef('editorAnchor')
const labels = { draft: '草稿', published: '已发布', archived: '已归档', blocked: '运营已屏蔽' }
let request

async function load() {
  request?.abort()
  const controller = new AbortController()
  request = controller
  loading.value = true
  error.value = ''
  try {
    const query = new URLSearchParams({ scope: props.scope, pageNo: page.value, pageSize: 10, q: filters.q, status: filters.status })
    const current = await api('/ls/studio-articles/manage?' + query, { signal: controller.signal })
    if (!controller.signal.aborted) result.value = current
  } catch (e) {
    if (!controller.signal.aborted) error.value = e.message
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}
watch([page, () => props.scope], load, { immediate: true })
onBeforeUnmount(() => request?.abort())
function search() { if (page.value !== 1) page.value = 1; else load() }

async function open(id = null) {
  editing.value = { id, key: id || 'new' }
  await nextTick()
  anchor.value?.scrollIntoView({ block: 'start' })
}

async function state(article, next) {
  if (busy.value || editing.value) return
  if (['archived', 'blocked'].includes(next) && !window.confirm('确认' + (next === 'blocked' ? '屏蔽' : '归档') + '「' + article.title + '」？它将停止公开展示和新的阅读奖励。')) return
  busy.value = article.id
  error.value = ''
  try {
    await api('/ls/studio-articles/' + article.id + '/state/' + next, { method: 'PUT' })
    notify('文章状态已更新')
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}

function onPublished(slug) {
  published.value = slug
  editing.value = null
  load()
}
</script>

<template>
  <section class="article-manager" aria-label="文章管理">
    <div class="section-title"><h2>{{ scope === 'admin' ? '平台文章' : '我的文章' }}</h2><button type="button" class="btn primary" :disabled="!!editing || !!busy" @click="open()">新建文章</button></div>
    <p class="content-tip">发布文章供读者阅读，首次阅读奖励由平台统一发放；归档不清除历史积分记录。</p>
    <div v-if="published" class="article-published" role="status">文章已发布 <RouterLink :to="'/articles/' + published" class="text-link">查看文章</RouterLink></div>
    <div v-if="editing" ref="editorAnchor"><ArticleEditor :key="editing.key" :article-id="editing.id" @saved="load" @published="onPublished" @close="editing = null" /></div>
    <form class="article-filters" @submit.prevent="search">
      <label class="field">搜索文章<input v-model.trim="filters.q" maxlength="80" placeholder="标题或摘要" /></label>
      <label class="field">文章状态<select v-model="filters.status"><option value="">全部状态</option><option v-for="(label, value) in labels" :key="value" :value="value">{{ label }}</option></select></label>
      <button type="submit" class="btn light" :disabled="loading">查询</button>
    </form>
    <p v-if="error" role="alert" class="form-error">{{ error }}</p>
    <p v-if="loading" role="status" class="content-tip">正在载入文章…</p>
    <div class="managed-articles" :aria-busy="loading">
      <article v-for="article in result.list" :key="article.id" class="managed-article">
        <div class="managed-copy"><span class="tag">{{ labels[article.status] }}</span><h3 class="managed-title">{{ article.title }}</h3><p class="managed-summary">{{ article.summary }}</p><small v-if="scope === 'admin'">作者：{{ article.authorName }}</small></div>
        <div class="managed-actions">
          <RouterLink v-if="article.status === 'published'" :to="'/articles/' + article.slug" class="btn light">阅读</RouterLink>
          <button v-if="article.status !== 'blocked' || scope === 'admin'" type="button" class="btn light" :disabled="!!editing || !!busy" @click="open(article.id)">编辑</button>
          <button v-if="article.status === 'published'" type="button" class="btn light" :disabled="!!editing || !!busy" @click="state(article, scope === 'admin' ? 'blocked' : 'archived')">{{ scope === 'admin' ? '屏蔽' : '归档' }}</button>
          <button v-else-if="article.status === 'archived' || (article.status === 'blocked' && scope === 'admin')" type="button" class="btn light" :disabled="!!editing || !!busy" @click="state(article, 'draft')">恢复草稿</button>
          <button v-else-if="article.status === 'draft'" type="button" class="btn light" :disabled="!!editing || !!busy" @click="state(article, 'archived')">归档草稿</button>
        </div>
      </article>
      <p v-if="!loading && !error && !result.list.length" class="content-tip">没有符合条件的文章。</p>
    </div>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading || !!editing || !!busy" @change="page = $event" />
  </section>
</template>

<style scoped>
.article-manager { margin-top: 28px; }
.article-filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin: 20px 0; }
.article-filters .field { margin: 0; flex: 1 1 180px; }
.article-filters .btn { flex: 0 0 auto; }
.managed-article { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 18px 0; border-bottom: 1px solid var(--line); }
.managed-copy { min-width: 0; }
.managed-title { margin: 10px 0 6px; font-size: 17px; overflow-wrap: anywhere; }
.managed-summary { margin: 0; color: var(--secondary-text); line-height: 1.7; }
.managed-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.article-published { display: flex; gap: 16px; align-items: center; padding: 12px 0; }
@media (max-width: 700px) { .managed-article { align-items: start; flex-direction: column; } }
</style>
