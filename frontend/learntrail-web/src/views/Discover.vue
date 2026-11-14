<script setup>
import { computed, onActivated, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, session, studioApi } from '../api'
import CoverArt from '../components/CoverArt.vue'
import SeriesCard from '../components/SeriesCard.vue'
import Icon from '../components/Icon.vue'
import Pagination from '../components/Pagination.vue'
import ArticleCard from '../components/ArticleCard.vue'
import CreatorRanking from '../components/CreatorRanking.vue'
const props = defineProps({
  creatorsOnly: Boolean,
  kind: { type: String, default: 'all' },
  search: { type: String, default: '' },
  topic: { type: String, default: '' },
  sort: { type: String, default: 'recommended' }
})
const route = useRoute()
const router = useRouter()
const home = ref({ series: [], creators: [], topics: [] })
const items = ref([])
const articles = ref({ list: [], total: 0, pages: 0 })
const loading = ref(true)
const error = ref('')
const total = ref(0)
const pages = ref(0)
const creatorTotal = ref(0)
const creatorPages = ref(0)
const page = computed(() => Math.min(100000, Math.max(1, Math.floor(Number(route.query.page) || 1))))
const changePage = value => router.replace({ query: { ...route.query, page: value > 1 ? value : undefined } })
let requestId = 0
onBeforeUnmount(() => { requestId++ })
const featured = computed(() => home.value.series.find(item => item.featured) || home.value.series[0])
function setFilter(name, value) {
  router.replace({ query: { ...route.query, [name]: value || undefined, kind: name === 'topic' && value ? 'video' : route.query.kind, page: undefined } })
}
async function load(keepVisible = false) {
  const currentRequest = ++requestId
  if (!keepVisible) loading.value = true
  error.value = ''
  const query = new URLSearchParams({ pageNo: page.value, pageSize: props.kind === 'all' ? 6 : 12, sort: props.sort })
  if (props.search) query.set('q', props.search)
  if (props.topic) query.set('topic', props.topic)
  try {
    const articleQuery = new URLSearchParams({ pageNo: 1, pageSize: 6 })
    articleQuery.set('sort', ['likes', 'views'].includes(props.sort) ? props.sort : 'newest')
    if (props.search) articleQuery.set('q', props.search)
    const [data, filtered, creators, knowledge] = await Promise.all([
      api(studioApi + '/home'),
      props.creatorsOnly || props.kind === 'articles' ? null : api(studioApi + '/series/page?' + query),
      props.creatorsOnly || props.search ? api(studioApi + '/creators/page?' + new URLSearchParams({
        q: props.search, pageNo: props.creatorsOnly ? page.value : 1, pageSize: props.creatorsOnly ? 12 : 8
      })) : null,
      !props.creatorsOnly && props.kind !== 'video' ? api('/ls/studio-articles/published?' + articleQuery) : null
    ])
    if (currentRequest !== requestId) return
    home.value = data
    if (filtered) { items.value = filtered.list; total.value = filtered.total; pages.value = filtered.pages }
    if (creators) { home.value.creators = creators.list; creatorTotal.value = creators.total; creatorPages.value = creators.pages }
    if (knowledge) articles.value = knowledge
  } catch (e) {
    if (currentRequest === requestId && !keepVisible) error.value = e.message
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}
watch([() => props.search, () => props.topic, () => props.sort, () => props.kind, () => props.creatorsOnly, page, () => session.user?.id], () => load(), { immediate: true })
onActivated(() => {
  if (!loading.value && !error.value) load(true)
})
function explore() { document.getElementById('collection')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
</script>

<template>
  <section v-if="error" class="empty-state"><Icon name="monitor" :size="40" /><h2>内容暂时未能加载</h2><p>{{ error }}</p><button class="btn primary" @click="load()">重新加载</button></section>
  <template v-else>
    <div v-if="creatorsOnly" class="page-intro"><p class="eyebrow">CREATOR RANKING</p><h1>创作者</h1><p>发现持续分享知识与经验的人。</p></div>
    <template v-if="!creatorsOnly">
      <div class="page-intro">
        <h1>发现好内容</h1>
        <p>读一篇有启发的文章，或跟随一段视频深入学习。</p>
      </div>
      <nav class="content-types" aria-label="内容类型">
        <RouterLink :to="{ path: '/', query: search ? { q: search } : {} }" :aria-current="kind === 'all' ? 'page' : undefined">全部内容</RouterLink>
        <RouterLink :to="{ path: '/', query: { kind: 'video', q: search || undefined } }" :aria-current="kind === 'video' ? 'page' : undefined">视频</RouterLink>
        <RouterLink :to="{ path: '/articles', query: { q: search || undefined } }">文章</RouterLink>
      </nav>
      <section v-if="featured && kind === 'video' && !search" class="hero-banner" aria-labelledby="featured-title">
        <div class="hero-copy">
          <p class="hero-eyebrow">编辑精选 · {{ featured.topic }}</p>
          <h2 id="featured-title">{{ featured.title }}</h2>
          <p class="hero-subtitle">{{ featured.subtitle }}</p>
          <div class="hero-actions">
            <RouterLink :to="'/series/' + featured.slug" class="btn lime">
              查看精选专栏 <Icon name="arrow" :size="16" />
            </RouterLink>
            <button class="btn hero-browse" @click="explore">浏览全部</button>
          </div>
        </div>
        <div class="hero-visual" aria-hidden="true">
          <CoverArt :tone="featured.coverTone" hero />
        </div>
      </section>
    </template>
    <div v-if="!creatorsOnly && kind !== 'articles'" id="collection" class="collection">
      <div class="section-title">
        <h2><span>{{ search ? '关于「' + search + '」的视频' : '视频' }}</span><span class="count-pill">{{ total }}</span></h2>
        <RouterLink v-if="kind === 'all'" :to="{ path: '/', query: { kind: 'video', q: search || undefined } }" class="text-link">更多视频 <Icon name="arrow" :size="16" /></RouterLink>
        <label class="sort-control">排序
          <select :value="sort" aria-label="专栏排序" @change="setFilter('sort', $event.target.value === 'recommended' ? '' : $event.target.value)">
            <option value="recommended">为你精选</option>
            <option value="newest">最新发布</option>
            <option value="likes">点赞最多</option>
            <option value="views">{{ kind === 'video' ? '播放最多' : '播放／浏览最多' }}</option>
            <option v-if="kind === 'video'" value="price">积分从低到高</option>
            <option v-if="kind === 'video'" value="duration">内容时长</option>
          </select>
        </label>
      </div>
      <div v-if="kind === 'video'" class="topic-tabs" role="group" aria-label="视频主题">
        <button :class="{ active: !topic }" :aria-pressed="!topic" @click="setFilter('topic', '')">全部主题</button>
        <button v-for="value in home.topics" :key="value" :class="{ active: topic === value }"
          :aria-pressed="topic === value" @click="setFilter('topic', value)">{{ value }}</button>
      </div>
      <div v-if="loading" class="card-grid skeleton-grid"><div v-for="n in 4" :key="n" class="skeleton-card"></div></div>
      <div v-else-if="items.length" class="card-grid"><SeriesCard v-for="series in items" :key="series.id" :series="series" /></div>
      <div v-else class="empty-state small"><Icon name="search" :size="32" /><h3>还没有找到这类内容</h3><p>换个关键词，或探索其他主题。</p></div>
      <Pagination :page="page" :pages="pages" :total="total" :busy="loading" @change="changePage" />
    </div>
    <section v-if="!creatorsOnly && kind === 'all'" class="knowledge-section" aria-labelledby="knowledge-title">
      <div class="section-title"><h2 id="knowledge-title">{{ search ? '关于「' + search + '」的文章' : '文章' }} <span class="count-pill">{{ articles.total }}</span></h2><RouterLink :to="{ path: '/articles', query: { q: search || undefined, sort: ['likes', 'views'].includes(sort) ? sort : undefined } }" class="text-link">全部文章 <Icon name="arrow" :size="16" /></RouterLink></div>
      <p v-if="loading" role="status">正在载入文章…</p>
      <div v-else-if="articles.list.length" class="article-list"><ArticleCard v-for="article in articles.list" :key="article.id" :article="article" /></div>
      <p v-else class="content-tip">暂时没有符合条件的文章，试试其他关键词。</p>
    </section>
    <section class="creators-section" :class="{ 'directory-page': creatorsOnly }">
      <div class="section-title">
        <h2>{{ search ? '相关创作者' : creatorsOnly ? '全部创作者' : '创作者' }}</h2>
        <RouterLink v-if="!creatorsOnly" :to="{ path: '/creators', query: { q: search || undefined } }" class="text-link">
          查看全部创作者 <Icon name="arrow" :size="16" />
        </RouterLink>
      </div>
      <p class="ranking-rule">按关注人数排名，同分比较已发布文章与视频专栏总数。{{ search ? '当前为搜索结果，按同一规则排序。' : '已有公开作品的创作者参与排名。' }}</p>
      <CreatorRanking :creators="home.creators" :offset="creatorsOnly ? (page - 1) * 12 : 0" @changed="load(true)" />
      <p v-if="!loading && !home.creators.length" class="content-panel">还没有创作者。</p>
      <Pagination v-if="creatorsOnly" :page="page" :pages="creatorPages" :total="creatorTotal" :busy="loading" @change="changePage" />
    </section>
  </template>
</template>

<style scoped>
.content-types { display: flex; flex-wrap: wrap; gap: 10px; margin: 24px 0; }
.content-types a { padding: 10px 18px; border-radius: 8px; border: 1px solid var(--line); background: white; }
.content-types a[aria-current="page"], .content-types a:hover { background: var(--green); color: white; }
.content-types a:focus-visible { outline: 2px solid var(--green); outline-offset: 3px; }
.knowledge-section { margin: 60px 0; }
.article-list { border-top: 1px solid var(--line); }
.ranking-rule { margin: -6px 0 22px; color: var(--secondary-text); font-size: 13px; }
@media (max-width: 600px) { .knowledge-section { margin: 42px 0; } }
</style>
