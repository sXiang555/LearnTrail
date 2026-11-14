<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, studioApi, session, notify, duration, timestamp } from '../api'
import CoverArt from '../components/CoverArt.vue'
import Icon from '../components/Icon.vue'
import SeriesDiscussion from '../components/SeriesDiscussion.vue'
import RelatedContent from '../components/RelatedContent.vue'
import ContentLikeButton from '../components/ContentLikeButton.vue'
import ContentMetrics from '../components/ContentMetrics.vue'
const route = useRoute()
const router = useRouter()
const series = ref(null)
const error = ref('')
const busy = ref(false)
const descriptionHeading = ref(null)
const accessible = computed(() => series.value?.owned || series.value?.creatorOwned || (series.value?.free === 1 && series.value?.available))
const previewEpisode = computed(() => {
  const episodes = series.value?.episodes || []
  return episodes.find(item => item.preview && item.id === series.value.previewSectionId)
    || episodes.find(item => item.preview)
})
const hasLockedEpisodes = computed(() =>
  !accessible.value && series.value?.episodes.some(item => !item.preview)
)
const playLabel = computed(() => {
  if (series.value?.creatorOwned && !series.value?.owned) return '预览我的专栏'
  if (series.value?.owned) return '进入我的专栏'
  if (accessible.value) return session.token ? '开始免费观看' : '登录后免费观看'
  return session.token ? '开始试看' : '登录试看'
})
async function load() {
  try { series.value = await api(studioApi + '/series/' + route.params.slug) } catch (e) { error.value = e.message }
}
watch(() => route.params.slug, load, { immediate: true })
function canPlay(episode) {
  return accessible.value || episode.preview
}
function showDescription() {
  const heading = descriptionHeading.value
  heading?.focus({ preventScroll: true })
  heading?.scrollIntoView({ block: 'start' })
}
function play(episode) {
  const current = series.value
  const target = episode ?? (accessible.value
    ? current.episodes.find(item => item.id === current.latestSectionId) || current.episodes[0]
    : previewEpisode.value)
  if (!target || !canPlay(target)) return

  router.push({
    name: 'watch',
    params: { slug: current.slug },
    query: { section: target.id }
  })
}
async function save() {
  if (!session.token) return router.push({ path: '/login', query: { next: route.fullPath } })
  busy.value = true
  try {
    await api(studioApi + '/series/' + series.value.id + '/watchlist', { method: series.value.saved ? 'DELETE' : 'POST' })
    series.value.saved = !series.value.saved
    notify(series.value.saved ? '已加入片单，留给下一段完整的时间' : '已移出片单')
  } catch (e) { notify(e.message, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div v-if="error" class="empty-state"><h2>暂时无法查看专栏</h2><p>{{ error }}</p><RouterLink to="/" class="btn primary">返回发现页</RouterLink></div>
  <div v-else-if="!series" class="loading-line">正在打开这一段新旅程…</div>
  <template v-else>
    <div class="breadcrumbs"><RouterLink to="/">发现好内容</RouterLink><Icon name="chevron" :size="12" /><span>{{ series.topic }}</span><Icon name="chevron" :size="12" /><span>专栏详情</span></div>
    <section class="detail-hero">
      <div class="detail-copy">
        <span class="tag">{{ series.free === 1 ? '公开专栏' : '积分专栏' }}</span>
        <p v-if="!series.available" class="content-tip">此专栏暂未公开，已有权益仍可观看已发布版本。</p>
        <h1>{{ series.title }}</h1>
        <p>{{ series.subtitle }}</p>
        <div class="detail-meta">
          <RouterLink :to="'/creators/' + series.creatorSlug" class="detail-creator">
            <span class="avatar small" :class="series.creatorColor">{{ series.creatorInitials }}</span>
            <b>{{ series.creatorName }}</b>
            <Icon name="upRight" :size="13" />
          </RouterLink>
          <span><Icon name="play" :size="15" />{{ series.episodeCount }} 集</span>
          <span><Icon name="clock" :size="15" />{{ duration(series.duration) }}</span>
        </div>
        <ContentMetrics kind="video" :liked-times="series.likedTimes" :view-times="series.viewTimes" />
        <div class="price-block">
          <strong>{{ series.owned ? '已解锁' : series.free === 1 ? '免费开放' : series.unlockPoints + ' 积分' }}</strong>
          <span v-if="!accessible && previewEpisode">含免费试看</span>
          <button v-if="series.description" type="button" class="text-link" @click="showDescription">
            查看专栏介绍
          </button>
        </div>
        <div class="detail-actions">
          <button v-if="accessible ? series.episodes.length : previewEpisode" class="btn primary" @click="play()">
            <Icon name="play" :size="16" />
            {{ playLabel }}
          </button>
          <RouterLink
            v-if="!accessible"
            :to="'/checkout/' + series.slug"
            class="btn"
            :class="previewEpisode ? 'outlined' : 'primary'"
          >
            积分解锁 <Icon name="arrow" :size="16" />
          </RouterLink>
          <button
            class="btn outlined"
            :disabled="busy"
            :aria-pressed="Boolean(series.saved)"
            @click="save"
          >
            <Icon name="bookmark" :size="16" />{{ series.saved ? '已收藏' : '收藏' }}
          </button>
          <ContentLikeButton biz-type="SERIES" :biz-id="series.id" :disabled="!series.available" :liked-times="series.likedTimes" @updated="series.likedTimes = $event.likedTimes" />
        </div>
      </div>
      <CoverArt :tone="series.coverTone" />
    </section>
    <div class="detail-columns">
      <section aria-labelledby="episode-heading">
        <div class="detail-title">
          <h2 id="episode-heading">剧集目录</h2>
          <span>{{ series.episodeCount }} 集 · {{ duration(series.duration) }}</span>
        </div>
        <div v-if="hasLockedEpisodes" class="episode-access-note">
          <p>使用 {{ series.unlockPoints }} 积分解锁后，可观看完整系列。阅读文章可获得积分。</p>
          <button v-if="previewEpisode" type="button" class="text-link" @click="play(previewEpisode)">
            {{ playLabel }}：{{ previewEpisode.title }}
          </button>
        </div>
        <div class="episode-list">
          <button
            v-for="(episode, index) in series.episodes"
            :key="episode.id"
            class="episode-row"
            type="button"
            :disabled="!canPlay(episode)"
            @click="play(episode)"
          >
            <span class="episode-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <span>
              <b class="episode-title">{{ episode.title }}</b>
              <span class="episode-meta">{{ timestamp(episode.duration) }}<span v-if="episode.finished"> · 已完成</span></span>
            </span>
            <span class="episode-status">
              <span v-if="!canPlay(episode)" class="tag">需解锁</span>
              <span v-else-if="!accessible" class="preview-tag">可试看</span>
              <Icon :name="canPlay(episode) ? 'play' : 'lock'" :size="16" aria-hidden="true" />
            </span>
          </button>
          <p v-if="!series.episodes.length" class="content-tip">这个专栏暂无剧集。</p>
        </div>
      </section>
      <aside v-if="series.description" class="content-panel series-description">
        <h2 ref="descriptionHeading" tabindex="-1">专栏介绍</h2>
        <div class="prose">{{ series.description }}</div>
      </aside>
    </div>
    <RelatedContent :items="series.articles" />
    <SeriesDiscussion v-if="session.token && accessible" :course-id="series.courseId" />
    <section v-else class="content-panel section-gap"><h2>专栏讨论</h2><p class="content-tip">{{ session.token ? '解锁专栏后，可以参与讨论、分享学习心得。' : '登录并取得专栏观看权限后，可以参与讨论。' }}</p></section>
  </template>
</template>
