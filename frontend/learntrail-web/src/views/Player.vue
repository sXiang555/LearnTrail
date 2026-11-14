<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, studioApi, timestamp, notify } from '../api'
import Icon from '../components/Icon.vue'
import RelatedContent from '../components/RelatedContent.vue'
import ContentMetrics from '../components/ContentMetrics.vue'
import { useContentView } from '../composables/useContentView.js'
import { useLearningReward } from '../composables/useLearningReward'

const route = useRoute()
const router = useRouter()
const series = ref(null)
const episode = ref(null)
const error = ref('')
const ready = ref(false)
const loading = ref(true)
const savedAt = ref('')
const videoId = 'lt-video-' + useId().replace(/[^a-zA-Z0-9]/g, '')
const unlocked = computed(() => series.value?.owned || series.value?.creatorOwned || (series.value?.free === 1 && series.value?.available))
const allowed = computed(() => unlocked.value || episode.value?.preview)
let player
let disposed = false
let lastSent = 0
let saving = false
let progressFailureShown = false
const atEnd = () => player && player.currentTime() >= (player.duration() || Infinity) - .5
const { error: viewError, retry: retryView } = useContentView({
  kind: 'SERIES', target: () => series.value?.id,
  enabled: () => !!player && ready.value && allowed.value && series.value?.available && !error.value && !player.paused(),
  position: () => player?.currentTime() || 0,
  onCount: count => { if (series.value) series.value.viewTimes = count }
})
const { progress: learningProgress, error: learningError, retry: rewardTick, pause: pauseReward } = useLearningReward({
  kind: 'WATCH', target: () => unlocked.value ? episode.value?.id : null,
  enabled: () => !!player && ready.value && unlocked.value && (!player.paused() || atEnd()),
  position: () => Math.max(0, player?.currentTime() || 0)
})

function choose(item) {
  router.push({
    path: '/watch/' + series.value.slug,
    query: { section: item.id },
  })
}
function localDate() {
  const d = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return (
    d.getFullYear() +
    '-' +
    pad(d.getMonth() + 1) +
    '-' +
    pad(d.getDate()) +
    ' ' +
    pad(d.getHours()) +
    ':' +
    pad(d.getMinutes()) +
    ':' +
    pad(d.getSeconds())
  )
}
async function saveProgress(initial = false, force = false) {
  if (!player || !series.value?.owned || !series.value.lessonId || saving)
    return
  const moment = initial
    ? 0
    : Math.max(0, Math.floor(player.currentTime() || 0))
  if (!force && !initial && Date.now() - lastSent < 15000) return
  saving = true
  lastSent = Date.now()
  try {
    await api('/ls/learning-records', {
      method: 'POST',
      keepalive: true,
      body: {
        sectionType: 1,
        lessonId: series.value.lessonId,
        sectionId: episode.value.id,
        duration: Math.max(
          1,
          Math.round(player.duration() || episode.value.duration || 1),
        ),
        moment,
        commitTime: localDate(),
      },
    })
    savedAt.value = initial
      ? '观看记录已建立'
      : '进度已同步至 ' + timestamp(moment)
  } catch (e) {
    savedAt.value = '进度暂未同步'
    if (!progressFailureShown) {
      notify('观看进度保存失败：' + e.message, 'error')
      progressFailureShown = true
    }
  } finally {
    saving = false
  }
}
function onLeave() {
  saveProgress(false, true)
}

function nativePlayer(video) {
  return {
    on: (name, handler) => video.addEventListener(name, handler),
    currentTime(value) {
      if (value !== undefined) video.currentTime = value
      return video.currentTime
    },
    duration: () => video.duration,
    paused: () => video.paused,
    dispose() {
      video.pause()
      video.removeAttribute('src')
      video.load()
    }
  }
}

function cloudPlayer(playback) {
  if (typeof window.TCPlayer !== 'function')
    throw new Error('视频组件暂时无法加载，请检查网络后重试')
  const encoded = playback.signature.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
  const payload = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=')))
  return window.TCPlayer(videoId, {
    appID: String(payload.appId),
    fileID: playback.fileId,
    psign: playback.signature,
    autoplay: false,
    controls: true,
    preload: 'auto',
    playbackRates: [0.75, 1, 1.25, 1.5, 2],
    plugins: { ContinuePlay: { auto: true } }
  })
}

onMounted(async () => {
  window.addEventListener('pagehide', onLeave)
  try {
    series.value = await api(studioApi + '/series/' + route.params.slug)
    episode.value =
      series.value.episodes.find(
        (item) => item.id === String(route.query.section),
      ) ||
      series.value.episodes.find(
        (item) => item.id === series.value.latestSectionId,
      ) ||
      (unlocked.value
        ? series.value.episodes[0]
        : series.value.episodes.find((item) => item.preview))
    if (!episode.value) throw new Error('当前专栏还没有可播放的小节')
    if (!allowed.value) return
    const playback = await api(
      '/ms/medias/signature/play?sectionId=' + episode.value.id,
    )
    if (disposed) return
    ready.value = true
    await nextTick()
    if (disposed) return
    const video = document.getElementById(videoId)
    player = playback.url ? nativePlayer(video) : cloudPlayer(playback)
    player.on('loadedmetadata', () => {
      if (episode.value.moment && unlocked.value) {
        player.currentTime(
          Math.min(episode.value.moment, Math.max(0, player.duration() - 2)),
        )
      }
      saveProgress(episode.value.moment == null, true)
    })
    player.on('timeupdate', () => {
      if (!player.paused()) saveProgress()
    })
    player.on('play', rewardTick)
    player.on('pause', () => { saveProgress(false, true); if (atEnd()) rewardTick(); else pauseReward() })
    player.on('ended', () => { saveProgress(false, true); rewardTick() })
    player.on('error', () => {
      if (!disposed) error.value = '视频源暂时无法播放，请稍后重试。'
    })
    if (playback.url) video.src = playback.url
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('pagehide', onLeave)
  saveProgress(false, true)
  if (player) player.dispose()
})
</script>

<template>
  <div v-if="!series && error" class="empty-state">
    <h2>暂时无法打开视频</h2>
    <p>{{ error }}</p>
    <RouterLink to="/" class="btn lime">返回发现页</RouterLink>
  </div>
  <div v-else-if="!series" class="loading-line">正在准备这段内容…</div>
  <template v-else>
    <div class="breadcrumbs">
      <RouterLink :to="'/series/' + series.slug"
        ><Icon name="back" :size="14" /> 返回专栏</RouterLink
      ><Icon name="chevron" :size="12" /><span>{{ series.creatorName }}</span>
    </div>
    <div class="player-layout">
      <section>
        <div class="video-shell">
          <video
            v-if="ready"
            :id="videoId"
            playsinline
            webkit-playsinline
            controls
          ></video>
          <div v-if="loading || error || !allowed" class="video-message">
            <Icon
              :name="error ? 'monitor' : !allowed ? 'lock' : 'play'"
              :size="35"
            />
            <h3>
              {{
                error
                  ? '暂时无法播放'
                  : !allowed
                    ? '这集内容需要解锁'
                    : '正在连接视频…'
              }}
            </h3>
            <p>
              {{
                error ||
                (!allowed
                  ? '试看之后，还可以走得更深。解锁专栏后观看完整系列。'
                  : '为自己留一段专注的时间。')
              }}
            </p>
            <RouterLink
              v-if="!allowed"
              :to="'/checkout/' + series.slug"
              class="btn lime"
              >查看解锁信息 <Icon name="arrow" :size="15"
            /></RouterLink>
          </div>
        </div>
        <div class="player-heading">
          <div>
            <h1>{{ episode?.title || series.title }}</h1>
            <p>{{ series.title }} · {{ series.creatorName }}</p>
          </div>
          <span class="preview-tag">{{
            series.creatorOwned && !series.owned ? '创作者预览' : series.owned ? '已解锁' : series.free === 1 ? '公开内容' : allowed ? '试看' : '未解锁'
          }}</span>
        </div>
        <ContentMetrics kind="video" :liked-times="series.likedTimes" :view-times="series.viewTimes" />
        <p v-if="viewError" class="content-tip" role="alert">播放统计暂未更新：{{ viewError }} <button type="button" class="text-link" @click="retryView">重试</button></p>
        <RelatedContent :items="series.articles" />
        <section v-if="unlocked" class="player-note" aria-label="观看积分">
          <b>观看积分</b>
          <p v-if="learningProgress?.rewarded">本集观看奖励已领取。</p>
          <p v-else-if="learningProgress?.eligible">已达到有效观看条件，今日观看奖励额度已用完。</p>
          <p v-else-if="learningProgress">有效观看 {{ learningProgress.seconds }} / {{ learningProgress.requiredSeconds }} 秒。连续播放达到条件后获得奖励。</p>
          <p v-else>播放后开始累计有效观看时长。</p>
          <p v-if="learningError" role="alert">{{ learningError }} <button class="text-link" @click="rewardTick">重试</button></p>
        </section>
        <div class="player-note">
          <template v-if="series.owned"
            ><b>留住每一次深入</b>
            <p>
              {{ savedAt || '播放后会自动记录观看进度，下次可以接着看。' }}
            </p></template
          ><template v-else-if="series.free === 1"
            ><b>好内容，让更多人看见</b>
            <p>
              这是公开专栏，可直接观看。把它加入片单，下次更容易找到。
            </p></template
          ><template v-else
            ><b>先试看，再决定</b>
            <p>
              {{ allowed ? '当前为试看内容，单集最多试看' : '当前小节尚未解锁，可选择目录中的试看视频，最多试看' }}
              {{
                series.previewMinutes
              }}
              分钟。试看不会生成已购权益或学习完成积分。
            </p>
            <RouterLink :to="'/checkout/' + series.slug" class="btn lime"
              >了解完整专栏 <Icon name="arrow" :size="14" /></RouterLink
          ></template>
        </div>
      </section>
      <aside class="player-playlist">
        <div class="playlist-heading">
          沿着目录，继续深入<small
            >{{ series.episodeCount }} 集 · {{ series.creatorName }}</small
          >
        </div>
        <button
          v-for="(item, index) in series.episodes"
          :key="item.id"
          class="playlist-item"
          :class="{ selected: item.id === episode?.id }"
          @click="choose(item)"
        >
          <span>{{ String(index + 1).padStart(2, '0') }}</span
          ><span
            >{{ item.title
            }}<small
              >{{ timestamp(item.duration) }}
              {{ item.finished ? ' · 已完成' : ''
              }}{{ !unlocked && item.preview ? ' · 试看' : '' }}</small
            ></span
          ><Icon
            :name="
              item.finished
                ? 'check'
                : unlocked || item.preview
                  ? 'play'
                  : 'lock'
            "
            :size="14"
          />
        </button>
      </aside>
    </div>
  </template>
</template>
