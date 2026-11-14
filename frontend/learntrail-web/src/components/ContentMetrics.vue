<script setup>
import Icon from './Icon.vue'
defineProps({
  likedTimes: { type: [Number, String], default: 0 },
  viewTimes: { type: [Number, String], default: 0 },
  kind: { type: String, default: 'article' }
})
</script>

<template>
  <div class="content-metrics" aria-label="内容统计">
    <span><Icon name="heart" :size="14" />{{ likedTimes ?? 0 }} 人点赞</span>
    <span title="同一登录账号每天最多计1次，不包含游客；浏览或实际播放满5秒计入。">
      <Icon :name="kind === 'video' ? 'play' : 'monitor'" :size="14" />
      {{ viewTimes ?? 0 }} 次{{ kind === 'video' ? '播放' : '浏览' }}
    </span>
  </div>
</template>

<style scoped>
.content-metrics { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--secondary-text); font-size: 12px; margin: 12px 0; font-variant-numeric: tabular-nums; }
.content-metrics > span { display: inline-flex; align-items: center; gap: 5px; overflow-wrap: anywhere; }
</style>
