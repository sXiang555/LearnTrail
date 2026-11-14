<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, shallowRef, useTemplateRef } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, notify } from '../api'
import ArticleContent from './ArticleContent.vue'
import MarkdownImport from './MarkdownImport.vue'
import { ARTICLE_LIMIT, EXCERPT_LIMIT, safeSourceUrl, sourceLabels } from '../articleMeta.js'

const props = defineProps({ articleId: { type: [String, Number], default: null } })
const emit = defineEmits(['saved', 'published', 'close'])
const id = shallowRef(props.articleId)
const status = shallowRef('draft')
const loading = shallowRef(!!props.articleId)
const busy = shallowRef(false)
const error = shallowRef('')
const defaults = { title: '', summary: '', body: '', contentFormat: 'markdown', sourceType: 'original', sourceAuthor: '', sourceUrl: '', licenseNote: '' }
const form = reactive({ ...defaults })
const mode = shallowRef('edit')
const importing = shallowRef(false)
const bodyLimit = computed(() => form.sourceType === 'link' ? EXCERPT_LIMIT : ARTICLE_LIMIT)
const baseline = shallowRef(JSON.stringify(form))
const dirty = computed(() => baseline.value !== JSON.stringify(form))
const editorForm = useTemplateRef('editorForm')
const path = '/ls/studio-articles'
const canLeave = () => !dirty.value || window.confirm('文章还有未保存的内容，确定离开吗？')
onBeforeRouteLeave(() => !busy.value && !importing.value && canLeave())
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
let disposed = false
onBeforeUnmount(() => { disposed = true; window.removeEventListener('beforeunload', beforeUnload) })
function hydrate(article) {
  for (const key of Object.keys(form)) form[key] = article[key] ?? (key === 'contentFormat' ? 'text' : defaults[key])
}
function imported(result) {
  form.body = result.body
  form.contentFormat = 'markdown'
  if (!form.title) form.title = result.title
  mode.value = 'preview'
}

onMounted(async () => {
  window.addEventListener('beforeunload', beforeUnload)
  if (!id.value) return
  try {
    const article = await api(path + '/' + id.value)
    if (disposed) return
    hydrate(article)
    status.value = article.status
    baseline.value = JSON.stringify(form)
  } catch (e) { error.value = e.message } finally { loading.value = false }
})

async function save(publish = false) {
  if (busy.value || importing.value) return
  error.value = ''
  if (!form.body.trim() || form.body.length > bodyLimit.value) { mode.value = 'edit'; await nextTick() }
  if (!editorForm.value.reportValidity()) return
  if (form.body.length > bodyLimit.value) { error.value = '当前内容类型最多允许 ' + bodyLimit.value + ' 字符'; return }
  if (form.sourceType !== 'original' && !safeSourceUrl(form.sourceUrl)) { error.value = '请填写有效的 HTTP(S) 原文链接，不包含账号密码'; return }
  busy.value = true
  error.value = ''
  try {
    if (id.value) await api(path + '/' + id.value, { method: 'PUT', body: { ...form } })
    else id.value = await api(path, { method: 'POST', body: { ...form } })
    baseline.value = JSON.stringify(form)
    if (publish) await api(path + '/' + id.value + '/state/published', { method: 'PUT' })
    const current = await api(path + '/' + id.value)
    hydrate(current)
    baseline.value = JSON.stringify(form)
    status.value = current.status
    emit('saved')
    if (publish) emit('published', current.slug)
    notify(publish ? '文章已发布' : '文章已保存')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}

function close() {
  if (!busy.value && !importing.value && canLeave()) emit('close')
}
</script>

<template>
  <section class="content-panel article-editor" aria-label="文章编辑器">
    <div class="section-title"><h2>{{ id ? '编辑文章' : '新建文章' }}</h2><button type="button" class="btn light" :disabled="busy || importing" @click="close">收起编辑</button></div>
    <p v-if="loading" class="content-tip" role="status">正在载入文章…</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <form v-if="!loading && (!error || form.title)" ref="editorForm" @submit.prevent="save(false)">
      <fieldset :disabled="busy || importing" class="article-fields">
        <label class="field">文章标题<input v-model.trim="form.title" name="article-title" autocomplete="off" maxlength="100" required /></label>
        <label class="field">内容摘要<textarea v-model.trim="form.summary" name="article-summary" autocomplete="off" maxlength="240" rows="2" required /></label>
        <div class="article-options">
          <label class="field">正文格式<select v-model="form.contentFormat" name="article-format"><option value="markdown">Markdown</option><option value="text">纯文本</option></select></label>
          <label class="field">内容来源<select v-model="form.sourceType" name="article-source"><option v-for="(label, value) in sourceLabels" :key="value" :value="value">{{ label }}</option></select></label>
        </div>
        <div v-if="form.sourceType !== 'original'" class="article-source-fields">
          <label class="field">原作者<input v-model.trim="form.sourceAuthor" name="source-author" autocomplete="off" maxlength="100" required /></label>
          <label class="field">原文链接<input v-model.trim="form.sourceUrl" name="source-url" type="url" spellcheck="false" autocomplete="off" maxlength="2048" placeholder="https://…" required /></label>
          <label v-if="form.sourceType === 'reprint'" class="field">转载授权或开放许可依据<textarea v-model.trim="form.licenseNote" name="license-note" autocomplete="off" maxlength="500" rows="2" required /></label>
          <p v-else class="content-tip">外部资料仅收录自己的导读与原文入口，不复制未经授权的全文，不发放站内全文阅读奖励。</p>
        </div>
        <MarkdownImport :disabled="busy" :has-body="!!form.body.trim()" :max-chars="bodyLimit" @reading="importing = $event" @imported="imported" />
        <div class="body-toolbar" role="group" aria-label="正文编辑模式"><button type="button" class="btn light" :aria-pressed="mode === 'edit'" @click="mode = 'edit'">编辑正文</button><button type="button" class="btn light" :aria-pressed="mode === 'preview'" @click="mode = 'preview'">预览正文</button><span>{{ form.body.length.toLocaleString('zh-CN') }} / {{ bodyLimit.toLocaleString('zh-CN') }} 字符</span></div>
        <label v-show="mode === 'edit'" class="field">{{ form.sourceType === 'link' ? '资料导读' : '正文' }}<textarea v-model="form.body" name="article-body" autocomplete="off" spellcheck="false" class="article-textarea" :maxlength="bodyLimit" rows="18" required /></label>
        <section v-if="mode === 'preview'" class="article-preview" aria-label="正文预览"><h3>{{ form.title || '正文预览' }}</h3><ArticleContent :body="form.body" :format="form.contentFormat" :title="form.title" :show-toc="false" /></section>
        <p class="content-tip">Markdown 支持标题、代码块、图片、列表与表格。图片使用 HTTP(S) 链接或站内路径；原始 HTML 不会执行。{{ status === 'published' ? '此文章已发布，保存会更新公开正文。' : '保存草稿后仅本人和运营人员可见。' }}</p>
        <div class="detail-actions">
          <button type="submit" class="btn light" :disabled="importing">{{ busy ? '正在保存…' : status === 'published' ? '保存修改' : '保存草稿' }}</button>
          <button type="button" class="btn primary" :disabled="importing" @click="save(true)">{{ busy ? '处理中…' : status === 'published' ? '保存并保持发布' : '保存并发布' }}</button>
        </div>
      </fieldset>
    </form>
  </section>
</template>

<style scoped>
.article-editor { margin: 20px 0; }
.article-fields { min-width: 0; padding: 0; border: 0; }
.article-textarea { min-height: 300px; line-height: 1.8; resize: vertical; }
.article-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.article-options select { min-height: 44px; background: white; color: inherit; }
.body-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 18px 0; }
.body-toolbar span { margin-left: auto; color: var(--secondary-text); font-size: 12px; font-variant-numeric: tabular-nums; }
.body-toolbar button[aria-pressed="true"] { background: var(--green); color: white; }
.article-preview { max-height: 650px; overflow: auto; padding: 24px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 18px; }
.article-preview > h3 { margin-top: 0; font-size: 22px; }
@media (max-width: 600px) { .article-options { grid-template-columns: minmax(0, 1fr); gap: 0; } .article-preview { padding: 16px; } }
</style>
