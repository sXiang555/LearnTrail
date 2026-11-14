<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, studioApi, session, notify } from '../api'
import SeriesCard from '../components/SeriesCard.vue'
import Icon from '../components/Icon.vue'
import Pagination from '../components/Pagination.vue'
import ArticleCard from '../components/ArticleCard.vue'
const route = useRoute()
const router = useRouter()
const data = ref(null)
const error = ref('')
const busy = ref(false)
const collection = ref({ list: [], total: 0, pages: 0 })
const articles = ref({ list: [], total: 0, pages: 0 })
const page = computed(() => Math.min(100000, Math.max(1, Math.floor(Number(route.query.page) || 1))))
const articlePage = computed(() => Math.min(100000, Math.max(1, Math.floor(Number(route.query.articlePage) || 1))))
const changePage = value => router.replace({ query: { ...route.query, page: value > 1 ? value : undefined } })
const changeArticlePage = value => router.replace({ query: { ...route.query, articlePage: value > 1 ? value : undefined } })
let requestId = 0
onBeforeUnmount(() => { requestId++ })
async function load() {
  const id = ++requestId
  error.value = ''
  try {
    const [profile, result] = await Promise.all([
      api(studioApi + '/creators/' + route.params.slug),
      api(studioApi + '/series/page?creator=' + encodeURIComponent(route.params.slug) + '&pageNo=' + page.value + '&pageSize=12')
    ])
    const knowledge = profile.creator.articleAuthorId ? await api('/ls/studio-articles/published?author=' + profile.creator.articleAuthorId
      + '&pageNo=' + articlePage.value + '&pageSize=6') : { list: [], total: 0, pages: 0 }
    if (id !== requestId) return
    data.value = profile
    collection.value = result
    articles.value = knowledge
  } catch (e) { if (id === requestId) error.value = e.message }
}
watch([() => route.params.slug, page, articlePage], load, { immediate: true })
async function follow() {
  if (!session.token) return router.push({ path: '/login', query: { next: route.fullPath } })
  busy.value = true
  try {
    await api(studioApi + '/creators/' + data.value.creator.slug + '/follow', { method: data.value.creator.following ? 'DELETE' : 'POST' })
    await load()
    notify(data.value.creator.following ? '已关注这位创作者' : '已取消关注')
  } catch (e) { notify(e.message, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div v-if="error" class="empty-state"><h2>暂时无法打开创作者主页</h2><p>{{ error }}</p><RouterLink to="/creators" class="btn primary">返回创作者</RouterLink></div>
  <div v-else-if="!data" class="loading-line">正在认识这位创作者…</div>
  <template v-else>
    <div class="breadcrumbs"><RouterLink to="/creators">创作者</RouterLink><Icon name="chevron" :size="12" /><span>{{ data.creator.displayName }}</span></div>
    <section class="creator-hero"><div class="profile-top"><span class="avatar huge" :class="data.creator.avatarColor">{{ data.creator.initials }}</span><div class="profile-heading"><p class="eyebrow">INDEPENDENT CREATOR / LEARNTRAIL</p><h1>{{ data.creator.displayName }}</h1><p>{{ data.creator.headline }}</p><div class="creator-stat"><span><strong>{{ data.creator.articleCount }}</strong>篇文章</span><span><strong>{{ data.creator.seriesCount }}</strong>个专栏</span><span><strong>{{ data.creator.followerCount }}</strong>人关注</span></div></div><button class="btn" :class="data.creator.following ? 'outlined' : 'primary'" :disabled="busy" @click="follow"><Icon :name="data.creator.following ? 'check' : 'plus'" :size="16" />{{ data.creator.following ? '已关注' : '关注创作者' }}</button></div><p class="prose">{{ data.creator.bio }}</p></section>
    <section aria-labelledby="creator-articles" class="creator-knowledge">
      <div class="section-title"><h2 id="creator-articles">文章</h2><span class="tag">{{ articles.total }} 篇</span></div>
      <div class="article-list"><ArticleCard v-for="article in articles.list" :key="article.id" :article="article" /></div>
      <p v-if="!articles.list.length" class="content-tip">这位创作者暂时没有公开文章。</p>
      <Pagination :page="articlePage" :pages="articles.pages" :total="articles.total" @change="changeArticlePage" />
    </section>
    <div class="section-title"><div><p class="eyebrow">IDEAS WORTH SPENDING TIME WITH</p><h2>来自 {{ data.creator.displayName }} 的专栏</h2></div><span class="tag">{{ collection.total }} 个内容系列</span></div>
    <div class="card-grid library-grid"><SeriesCard v-for="series in collection.list" :key="series.id" :series="series" /></div>
    <p v-if="!collection.list.length" class="content-panel">这位创作者暂时没有公开专栏。</p>
    <Pagination :page="page" :pages="collection.pages" :total="collection.total" @change="changePage" />
  </template>
</template>

<style scoped>
.creator-knowledge { margin-bottom: 36px; }
.article-list { border-top: 1px solid var(--line); }
.creator-stat { flex-wrap: wrap; gap: 12px 24px; }
</style>
