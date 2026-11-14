<script setup>
import { computed } from 'vue'

const props = defineProps({ sources: { type: Array, default: () => [] } })
const items = computed(() => props.sources.filter(source => source && typeof source.slug === 'string' &&
  source.slug.length > 0 && source.slug.length <= 200 && typeof source.title === 'string').slice(0, 20)
  .map(source => ({ ...source, to: '/articles/' + encodeURIComponent(source.slug) })))
</script>

<template>
  <details v-if="items.length" class="ai-sources">
    <summary>参考来源（{{ items.length }}）</summary>
    <ul class="source-list">
      <li v-for="(source, index) in items" :key="source.slug + ':' + index">
        <RouterLink :to="source.to">{{ source.title }}</RouterLink>
        <small v-if="source.section"> · {{ source.section }}</small>
        <small v-if="Number.isInteger(source.ordinal)"> · 片段 {{ source.ordinal + 1 }}</small>
        <blockquote v-if="source.quote">{{ source.quote }}</blockquote>
      </li>
    </ul>
  </details>
</template>

<style scoped>
.ai-sources { margin-top: 12px; font-size: 13px; color: var(--secondary-text); }
.ai-sources summary { cursor: pointer; padding: 6px 0; }
.source-list { padding-left: 20px; line-height: 1.7; }
.source-list li { margin: 10px 0; }
.source-list a { text-decoration: underline; text-underline-offset: 3px; }
.source-list blockquote { margin: 7px 0; padding-left: 10px; border-left: 2px solid var(--line); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
