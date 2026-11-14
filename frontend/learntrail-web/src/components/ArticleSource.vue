<script setup>
import { computed } from 'vue'
import { isReference, safeSourceUrl, sourceLabel, sourceType } from '../articleMeta.js'
const props = defineProps({ article: { type: Object, required: true } })
const url = computed(() => safeSourceUrl(props.article.sourceUrl))
const date = computed(() => {
  const value = new Date(props.article.publishedAt)
  return props.article.publishedAt && !Number.isNaN(value.getTime())
    ? new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(value) : ''
})
</script>

<template>
  <div class="article-source" :class="{ attributed: sourceType(article) !== 'original' }">
    <p class="source-meta"><span class="tag">{{ sourceLabel(article) }}</span><span v-if="sourceType(article) === 'original'">{{ article.authorName }}</span><span v-else>原作者：{{ article.sourceAuthor }}</span><time v-if="date" :datetime="article.publishedAt">{{ date }}</time></p>
    <template v-if="sourceType(article) !== 'original'">
      <p class="source-detail">本站{{ isReference(article) ? '收录' : '发布' }}：{{ article.authorName }}</p>
      <p v-if="article.licenseNote && !isReference(article)" class="source-detail">转载说明：{{ article.licenseNote }}</p>
      <p v-if="isReference(article)" class="source-detail">本站提供资料导读，完整内容请前往原文阅读。</p>
      <a v-if="url" :href="url" target="_blank" rel="noopener noreferrer" class="source-link" :class="{ 'btn primary': isReference(article) }">{{ isReference(article) ? '前往原文阅读' : '查看原文' }} ↗<span class="sr-only">（在新窗口打开）</span></a>
      <p v-else class="source-detail" role="status">原文链接暂不可用。</p>
    </template>
  </div>
</template>

<style scoped>
.article-source { margin: 20px 0 30px; color: var(--secondary-text); font-size: 13px; }
.article-source.attributed { padding: 20px; border: 1px solid var(--line); border-radius: 10px; background: #f6f7f0; }
.source-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 0; }
.source-detail { margin: 12px 0 0; line-height: 1.8; white-space: pre-wrap; }
.source-link { display: inline-block; margin-top: 16px; color: var(--green); text-decoration: underline; text-underline-offset: 3px; }
.source-link.primary { color: white; text-decoration: none; }
.source-link:focus-visible { outline: 2px solid var(--green); outline-offset: 3px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
