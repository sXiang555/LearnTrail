<script setup>
import { sourceLabel, isReference } from '../articleMeta.js'
import ContentMetrics from './ContentMetrics.vue'
defineProps({ article: { type: Object, required: true }, heading: { type: String, default: 'h3', validator: value => ['h2', 'h3'].includes(value) } })
const date = value => {
  const parsed = new Date(value)
  return value && !Number.isNaN(parsed.getTime())
    ? new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' }).format(parsed) : ''
}
</script>

<template>
  <RouterLink :to="'/articles/' + article.slug" class="article-card">
    <component :is="heading" class="article-title">{{ article.title }}</component>
    <p>{{ article.summary }}</p>
    <ContentMetrics :liked-times="article.likedTimes" :view-times="article.viewTimes" />
    <div class="article-meta"><span>{{ article.sourceAuthor || article.authorName }}</span><time v-if="article.publishedAt" :datetime="article.publishedAt">{{ date(article.publishedAt) }}</time><span class="article-type">{{ sourceLabel(article) }}</span><span v-if="article.rewarded && !isReference(article)">已领取阅读奖励</span></div>
  </RouterLink>
</template>

<style scoped>
.article-card { display: block; min-width: 0; padding: 26px 0; border-bottom: 1px solid var(--line); text-decoration: none; color: inherit; }
.article-card:last-child { border-bottom: 0; }
.article-card:hover .article-title { color: var(--green); text-decoration: underline; text-underline-offset: 5px; }
.article-card:focus-visible { outline: 2px solid var(--green); outline-offset: 3px; }
.article-type { color: var(--secondary-text); }
.article-title { margin: 0 0 10px; font-size: clamp(19px, 2vw, 23px); line-height: 1.5; transition: color .15s; overflow-wrap: anywhere; }
.article-card p { margin: 0 0 14px; line-height: 1.8; color: var(--secondary-text); max-width: 900px; overflow-wrap: anywhere; }
.article-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12px; color: var(--secondary-text); }
@media (max-width: 600px) { .article-card { padding: 22px 0; } }
</style>
