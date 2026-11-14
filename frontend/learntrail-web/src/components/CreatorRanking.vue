<script setup>
import { ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, studioApi, session, notify } from '../api'

const props = defineProps({ creators: { type: Array, default: () => [] }, offset: { type: Number, default: 0 } })
const emit = defineEmits(['changed'])
const router = useRouter()
const route = useRoute()
const busy = shallowRef(null)
const following = ref({})
watch(() => props.creators, () => { following.value = {} })
const rank = (creator, index) => creator.rank || props.offset + index + 1
const isFollowing = creator => following.value[creator.id] ?? creator.following
async function follow(creator) {
  if (!session.token) return router.push({ path: '/login', query: { next: route.fullPath } })
  if (busy.value) return
  busy.value = creator.id
  const previous = isFollowing(creator)
  try {
    await api(studioApi + '/creators/' + creator.slug + '/follow', { method: previous ? 'DELETE' : 'POST' })
    following.value[creator.id] = !previous
    notify(previous ? '已取消关注' : '已关注这位创作者')
    emit('changed')
  } catch (e) { notify(e.message, 'error') }
  finally { busy.value = null }
}
</script>

<template>
  <ol class="creator-ranking" aria-label="创作者排名">
    <li v-for="(creator, index) in creators" :key="creator.id" class="ranking-row">
      <span class="ranking-number" :class="{ leading: rank(creator, index) <= 3 }" :aria-label="'第 ' + rank(creator, index) + ' 名'">{{ String(rank(creator, index)).padStart(2, '0') }}</span>
      <RouterLink :to="'/creators/' + creator.slug" class="ranking-person"><span class="avatar large" :class="creator.avatarColor" aria-hidden="true">{{ creator.initials }}</span><div><h3>{{ creator.displayName }}</h3><p>{{ creator.headline }}</p></div></RouterLink>
      <div class="ranking-stats"><span><strong>{{ creator.followerCount }}</strong> 人关注</span><span>{{ creator.articleCount }} 篇文章 · {{ creator.seriesCount }} 个视频专栏</span></div>
      <RouterLink v-if="session.user && creator.articleAuthorId && session.user.id === creator.articleAuthorId" to="/studio" class="text-link ranking-action">我的创作</RouterLink>
      <button v-else class="btn ranking-action" :class="isFollowing(creator) ? 'light' : 'outlined'" :disabled="!!busy" @click="follow(creator)">{{ busy === creator.id ? '处理中…' : isFollowing(creator) ? '已关注' : '关注' }}</button>
    </li>
  </ol>
</template>

<style scoped>
.creator-ranking { list-style: none; padding: 0; margin: 0; }
.ranking-row { display: grid; grid-template-columns: 44px minmax(0, 1fr) 210px 102px; align-items: center; gap: 24px; padding: 27px 0; border-bottom: 1px solid var(--line); }
.ranking-row:last-child { border-bottom: 0; }
.ranking-number { font-size: 23px; font-weight: 500; font-variant-numeric: tabular-nums; color: var(--muted); }
.ranking-number.leading { color: var(--green); font-weight: 750; }
.ranking-person { display: flex; align-items: center; gap: 18px; min-width: 0; }
.ranking-person > div { min-width: 0; }
.ranking-person .avatar { flex-shrink: 0; }
.ranking-person h3 { margin: 0 0 8px; font-size: 20px; line-height: 1.5; overflow-wrap: anywhere; }
.ranking-person p { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
.ranking-person:hover h3 { color: var(--green); text-decoration: underline; text-underline-offset: 4px; }
.ranking-stats { display: flex; flex-direction: column; gap: 10px; color: var(--muted); font-size: 13px; }
.ranking-stats strong { font-size: 17px; color: var(--text); font-variant-numeric: tabular-nums; }
.ranking-action { justify-self: end; white-space: nowrap; }
.ranking-person:focus-visible { outline: 2px solid var(--green); outline-offset: 5px; border-radius: 4px; }
@media (max-width: 1100px) { .ranking-row { grid-template-columns: 34px minmax(0, 1fr) 160px 90px; gap: 16px; } }
@media (max-width: 650px) { .ranking-row { grid-template-columns: 28px minmax(0, 1fr) auto; gap: 14px; padding: 22px 0; } .ranking-person { gap: 12px; } .ranking-person h3 { font-size: 17px; } .ranking-person .avatar { width: 42px; height: 42px; font-size: 17px; } .ranking-stats { grid-column: 2 / -1; flex-direction: row; flex-wrap: wrap; gap: 8px 16px; padding-left: 54px; } .ranking-action { grid-column: 3; grid-row: 1; padding: 9px 12px; } .ranking-number { font-size: 19px; } }
</style>
