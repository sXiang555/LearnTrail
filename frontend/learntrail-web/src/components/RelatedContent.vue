<script setup>
defineProps({ items: { type: Array, default: () => [] }, kind: { type: String, default: 'article' } })
</script>

<template>
  <section v-if="items.length" class="related-content section-gap">
    <h2>{{ kind === 'article' ? '配套文章' : '对应视频' }}</h2>
    <ul class="related-list">
      <li v-for="item in items" :key="item.id"><RouterLink :to="(kind === 'article' ? '/articles/' : '/series/') + item.slug"><span class="tag">{{ kind === 'article' ? '文章' : '视频' }}</span><h3>{{ item.title }}</h3><p v-if="item.summary || item.subtitle">{{ item.summary || item.subtitle }}</p><span class="text-link">{{ kind === 'article' ? '阅读文章' : '查看视频' }} →</span></RouterLink></li>
    </ul>
  </section>
</template>

<style scoped>
.related-content { padding-top: 24px; border-top: 1px solid var(--line); }
.related-list { list-style: none; padding: 0; margin: 12px 0 0; }
.related-list li { border-bottom: 1px solid var(--line); }
.related-list li:last-child { border-bottom: 0; }
.related-list a { display: block; padding: 20px 0; overflow-wrap: anywhere; }
.related-list a:hover h3 { color: var(--green); text-decoration: underline; text-underline-offset: 4px; }
.related-list a:focus-visible { outline: 2px solid var(--green); outline-offset: 3px; }
.related-list h3 { margin: 12px 0; }
.related-list p { color: var(--muted); line-height: 1.7; font-size: 14px; }
</style>
