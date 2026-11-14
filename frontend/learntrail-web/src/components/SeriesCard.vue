<script setup>
import { useRoute, useRouter } from 'vue-router'
import { api, studioApi, session, notify, duration } from '../api'
import CoverArt from './CoverArt.vue'
import Icon from './Icon.vue'
import ContentMetrics from './ContentMetrics.vue'
const props = defineProps({ series: { type: Object, required: true } })
const emit = defineEmits(['changed'])
const router = useRouter()
const route = useRoute()
async function save() {
  if (!session.token) return router.push({ path: '/login', query: { next: route.fullPath } })
  try {
    await api(studioApi + '/series/' + props.series.id + '/watchlist', { method: props.series.saved ? 'DELETE' : 'POST' })
    props.series.saved = !props.series.saved
    notify(props.series.saved ? '已加入你的片单' : '已移出片单')
    emit('changed')
  } catch (error) { notify(error.message, 'error') }
}
</script>

<template>
  <article class="series-card">
    <div class="card-cover">
      <RouterLink :to="'/series/' + series.slug" :aria-label="'查看专栏：' + series.title">
        <CoverArt :tone="series.coverTone" />
      </RouterLink>
      <button
        class="save-button"
        :class="{ saved: series.saved }"
        :aria-label="series.saved ? '移出片单' : '加入片单'"
        @click="save"
      >
        <Icon name="bookmark" :size="17" />
      </button>
    </div>
    <div class="card-labels">
      <span v-if="series.blocked || series.available === false" class="tag">{{ series.blocked ? '暂时不可访问' : '暂未公开' }}</span>
      <span class="tag">{{ series.topic }}</span>
      <span v-if="!series.owned && series.free !== 1 && series.previewSectionId" class="tag">可试看</span>
    </div>
    <h3><RouterLink :to="'/series/' + series.slug">{{ series.title }}</RouterLink></h3>
    <RouterLink :to="'/creators/' + series.creatorSlug" class="card-creator">
      <span class="mini-avatar" :class="series.creatorColor">{{ series.creatorInitials }}</span>
      {{ series.creatorName }}
    </RouterLink>
    <p class="card-subtitle">{{ series.subtitle }}</p>
    <ContentMetrics kind="video" :liked-times="series.likedTimes" :view-times="series.viewTimes" />
    <div v-if="series.owned" class="progress-track">
      <i :style="{ width: series.progressPercent + '%' }"></i>
    </div>
    <div class="card-bottom">
      <span>{{ series.episodeCount }} 集 · {{ duration(series.duration) }}</span>
      <strong :class="{ 'free-price': series.free === 1 || series.owned }">
        {{ series.free === 1 ? '免费' : series.owned ? '已解锁' : series.unlockPoints + ' 积分' }}
      </strong>
    </div>
  </article>
</template>
