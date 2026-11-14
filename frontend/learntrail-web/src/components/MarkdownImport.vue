<script setup>
import { onBeforeUnmount, shallowRef } from 'vue'
import { readMarkdownFile } from '../articleMarkdown.js'
import { ARTICLE_LIMIT } from '../articleMeta.js'

const props = defineProps({ disabled: Boolean, hasBody: Boolean, maxChars: { type: Number, default: ARTICLE_LIMIT } })
const emit = defineEmits(['imported', 'reading'])
const reading = shallowRef(false)
const pending = shallowRef(null)
const error = shallowRef('')
const message = shallowRef('')
let disposed = false
onBeforeUnmount(() => { disposed = true })

function apply(result) {
  if (result.body.length > props.maxChars) { error.value = '当前内容类型最多允许 ' + props.maxChars + ' 字符'; pending.value = null; return }
  pending.value = null
  emit('imported', result)
  message.value = '已导入 ' + result.filename + '，请检查预览后保存'
}
async function select(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || props.disabled || reading.value) return
  error.value = ''; message.value = ''; pending.value = null
  reading.value = true
  emit('reading', true)
  try {
    const result = await readMarkdownFile(file)
    if (disposed) return
    if (result.body.length > props.maxChars) throw new Error('当前内容类型最多允许 ' + props.maxChars + ' 字符')
    if (props.hasBody) pending.value = result
    else apply(result)
  } catch (e) { if (!disposed) error.value = e.message }
  finally { if (!disposed) { reading.value = false; emit('reading', false) } }
}
</script>

<template>
  <div class="markdown-import">
    <label class="field">导入 Markdown 文件<input name="markdown-file" type="file" accept=".md,.markdown,text/markdown" :disabled="disabled || reading" @change="select" /></label>
    <p class="content-tip">UTF-8 编码，文件最多 1 MiB。导入只替换正文，空标题会使用文件中的一级标题；本地图片需另行提供可访问地址。</p>
    <p v-if="reading" role="status">正在读取文件…</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="message" class="content-tip" role="status">{{ message }}</p>
    <div v-if="pending" class="import-confirm" role="region" aria-label="确认替换正文"><p>导入「{{ pending.filename }}」将替换当前正文，标题、摘要和来源信息会保留。</p><div class="detail-actions"><button type="button" class="btn primary" :disabled="disabled" @click="apply(pending)">替换正文</button><button type="button" class="btn light" :disabled="disabled" @click="pending = null">取消导入</button></div></div>
  </div>
</template>

<style scoped>
.markdown-import { margin: 20px 0; }
.markdown-import .field { margin-bottom: 8px; }
.markdown-import input { padding: 10px; font-size: 13px; }
.import-confirm { border: 1px solid var(--line); border-radius: 8px; padding: 16px; background: #f4f5ed; }
.import-confirm p { margin-top: 0; line-height: 1.8; }
</style>
