<script setup>
import { computed, nextTick, onBeforeUnmount, shallowRef, useId, useTemplateRef, watch } from 'vue'
import { renderMarkdown } from '../articleMarkdown.js'
import { ARTICLE_LIMIT } from '../articleMeta.js'
import 'highlight.js/styles/github.css'

const props = defineProps({
  body: { type: String, default: '' },
  format: { type: String, default: 'text' },
  title: { type: String, default: '' },
  showToc: { type: Boolean, default: true }
})
const prefix = 'lt-body-' + useId().replace(/[^a-z0-9_-]/gi, '-')
const content = useTemplateRef('content')
const rendered = shallowRef({ html: '', toc: [] })
const error = shallowRef('')
const pending = shallowRef(false)
const active = shallowRef('')
const navigationError = shallowRef('')
const paragraphs = computed(() => props.body.split(/\n\s*\n/))
const hasToc = computed(() => props.showToc && rendered.value.toc.length > 0)
let observer
let disposed = false
onBeforeUnmount(() => { disposed = true; observer?.disconnect() })

watch([() => props.body, () => props.format, () => props.title, () => props.showToc], (_, __, onCleanup) => {
  let current = true
  observer?.disconnect()
  error.value = ''
  navigationError.value = ''
  pending.value = props.format === 'markdown'
  // 避免每个按键都同步解析长正文，预览和正式阅读复用同一套规则。
  const timer = setTimeout(async () => {
    if (disposed || !current) return
    try {
      rendered.value = props.format === 'markdown' ? renderMarkdown(props.body, window, { prefix, title: props.title }) : { html: '', toc: [] }
      active.value = rendered.value.toc[0]?.id || ''
    } catch {
      rendered.value = { html: '', toc: [] }
      error.value = props.body.length > ARTICLE_LIMIT ? '正文最多 20 万字符' : '暂时无法排版，请检查 Markdown 内容后重试'
    } finally { pending.value = false }
    await nextTick()
    if (disposed || !current || !content.value) return
    content.value.querySelectorAll('pre,table').forEach(block => {
      block.tabIndex = 0
      block.setAttribute('aria-label', block.tagName === 'PRE' ? '代码块，可横向滚动' : '数据表格，可横向滚动')
    })
    if (!hasToc.value) return
    const headings = content.value.querySelectorAll('h2[id],h3[id],h4[id],h5[id],h6[id]')
    headings.forEach(heading => { heading.tabIndex = -1 })
    if (typeof IntersectionObserver === 'undefined') return
    observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible[0]) active.value = visible[0].target.id
    }, { rootMargin: '-80px 0px -60% 0px' })
    headings.forEach(heading => observer.observe(heading))
  }, props.format === 'markdown' ? 180 : 0)
  onCleanup(() => { current = false; clearTimeout(timer); observer?.disconnect() })
}, { immediate: true })

function go(id) {
  const heading = document.getElementById(id)
  if (!heading || !content.value?.contains(heading)) { navigationError.value = '未找到此章节，请使用目录重新定位'; return }
  navigationError.value = ''
  active.value = id
  heading.tabIndex = -1
  heading.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
  heading.focus({ preventScroll: true })
}
function internalLink(event) {
  const anchor = event.target.closest?.('a')
  const href = anchor?.getAttribute('href')
  if (!href?.startsWith('#')) return
  event.preventDefault()
  let fragment
  try { fragment = decodeURIComponent(href.slice(1)) } catch { fragment = href.slice(1) }
  const heading = rendered.value.toc.find(item => item.slug === fragment || item.id === fragment)
  if (heading) go(heading.id)
  else navigationError.value = '未找到此章节，请使用目录重新定位'
}
</script>

<template>
  <div class="document-layout" :class="{ 'with-toc': hasToc }">
    <aside v-if="hasToc" class="desktop-toc">
      <nav aria-label="文章目录"><h2 class="toc-title">本文目录</h2><ol><li v-for="heading in rendered.toc" :key="heading.id"><button type="button" :class="{ nested: heading.level > 2 }" :aria-current="active === heading.id ? 'location' : undefined" @click="go(heading.id)">{{ heading.text }}</button></li></ol></nav>
    </aside>
    <div class="document-pane">
      <details v-if="hasToc" class="mobile-toc"><summary>本文目录 · {{ rendered.toc.length }} 个章节</summary><nav aria-label="文章目录"><ol><li v-for="heading in rendered.toc" :key="heading.id"><button type="button" :class="{ nested: heading.level > 2 }" :aria-current="active === heading.id ? 'location' : undefined" @click="go(heading.id)">{{ heading.text }}</button></li></ol></nav></details>
      <p v-if="pending" class="document-status" role="status">正在排版…</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <p v-if="navigationError" class="document-status" role="status">{{ navigationError }}</p>
      <!-- HTML 仅来自 articleMarkdown 中禁用原始 HTML 后再经 DOMPurify 净化的结果。 -->
      <div v-if="format === 'markdown'" ref="content" class="markdown-document" @click="internalLink" v-html="rendered.html"></div>
      <div v-else ref="content" class="plain-document"><p v-for="(paragraph, index) in paragraphs" :key="index">{{ paragraph }}</p></div>
    </div>
  </div>
</template>

<style scoped>
.document-layout { min-width: 0; }
.document-layout.with-toc { display: grid; grid-template-columns: minmax(0, 1fr) 180px; gap: 28px; align-items: start; }
.document-pane { min-width: 0; grid-column: 1; grid-row: 1; }
.desktop-toc { grid-column: 2; grid-row: 1; position: sticky; top: 88px; max-height: calc(100vh - 120px); overflow: auto; border-left: 1px solid var(--line); padding-left: 16px; }
.toc-title { margin: 0 0 12px; font-size: 14px; }
.desktop-toc ol, .mobile-toc ol { list-style: none; padding: 0; margin: 0; }
.desktop-toc button, .mobile-toc button { display: block; width: 100%; border: 0; border-radius: 4px; background: transparent; color: var(--secondary-text); text-align: left; padding: 7px 5px; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; cursor: pointer; }
.desktop-toc button.nested, .mobile-toc button.nested { padding-left: 17px; }
.desktop-toc button:hover, .mobile-toc button:hover, button[aria-current="location"] { color: var(--green); background: #edf1e5; }
.desktop-toc button:focus-visible, .mobile-toc button:focus-visible, .mobile-toc summary:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }
.mobile-toc { display: none; margin-bottom: 24px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; }
.mobile-toc summary { cursor: pointer; font-weight: 600; }
.mobile-toc nav { margin-top: 12px; }
.document-status { color: var(--secondary-text); font-size: 13px; }
.plain-document p { margin: 22px 0; line-height: 1.95; white-space: pre-wrap; }
.markdown-document { line-height: 1.95; overflow-wrap: anywhere; }
.markdown-document :deep(h2), .markdown-document :deep(h3), .markdown-document :deep(h4), .markdown-document :deep(h5), .markdown-document :deep(h6) { scroll-margin-top: 90px; margin: 32px 0 14px; line-height: 1.5; }
.markdown-document :deep(h2) { font-size: 23px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
.markdown-document :deep(h3) { font-size: 20px; }
.markdown-document :deep(h4), .markdown-document :deep(h5), .markdown-document :deep(h6) { font-size: 17px; }
.markdown-document :deep(p), .markdown-document :deep(ul), .markdown-document :deep(ol) { margin: 18px 0; }
.markdown-document :deep(a) { color: var(--green); text-decoration: underline; text-underline-offset: 3px; }
.markdown-document :deep(a:focus-visible), .markdown-document :deep([tabindex]:focus-visible) { outline: 2px solid var(--green); outline-offset: 3px; }
.markdown-document :deep(blockquote) { margin: 24px 0; padding: 2px 20px; border-left: 3px solid #a0ba70; color: var(--secondary-text); background: #f3f5ed; }
.markdown-document :deep(pre) { max-width: 100%; padding: 18px; overflow-x: auto; background: #f3f5ef; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; line-height: 1.75; }
.markdown-document :deep(code) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.markdown-document :deep(p code), .markdown-document :deep(li code) { background: #edf1e5; padding: 2px 5px; border-radius: 4px; }
.markdown-document :deep(pre code) { white-space: pre; overflow-wrap: normal; }
.markdown-document :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; font-size: 14px; }
.markdown-document :deep(th), .markdown-document :deep(td) { min-width: 100px; padding: 10px 14px; border: 1px solid var(--line); text-align: left; }
.markdown-document :deep(th) { background: #f0f3e9; }
.markdown-document :deep(img) { display: block; max-width: 100%; height: auto; margin: 24px auto; border-radius: 8px; }
.markdown-document :deep(hr) { border: 0; border-top: 1px solid var(--line); margin: 30px 0; }
@media (max-width: 1000px) { .document-layout.with-toc { display: block; } .desktop-toc { display: none; } .mobile-toc { display: block; } }
</style>
