<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, session } from '../api'
import Icon from '../components/Icon.vue'
import Pagination from '../components/Pagination.vue'
import ArticleContent from '../components/ArticleContent.vue'
import ArticleSource from '../components/ArticleSource.vue'
import RelatedContent from '../components/RelatedContent.vue'
import ArticleCard from '../components/ArticleCard.vue'
import ContentLikeButton from '../components/ContentLikeButton.vue'
import ContentMetrics from '../components/ContentMetrics.vue'
import { useContentView } from '../composables/useContentView.js'
import { useLearningReward } from '../composables/useLearningReward'
import { isReference } from '../articleMeta.js'

const route = useRoute()
const router = useRouter()
const sort = computed(() => ['newest', 'likes', 'views'].includes(route.query.sort) ? route.query.sort : 'newest')
const page = computed(() => Math.min(100000, Math.max(1, Math.floor(Number(route.query.page) || 1))))
const total = ref(0)
const pages = ref(0)
let disposed = false
onBeforeUnmount(() => { disposed = true })
const search = ref(typeof route.query.q === 'string' ? route.query.q.slice(0, 80) : '')
const changePage = next => router.push({ path: '/articles', query: { ...route.query, page: next > 1 ? next : undefined } })
const find = () => router.push({ path: '/articles', query: { q: search.value.trim() || undefined, sort: sort.value === 'newest' ? undefined : sort.value } })
const changeSort = value => router.push({ path: '/articles', query: { ...route.query, sort: value === 'newest' ? undefined : value, page: undefined } })
const articles = ref([])
const article = ref(null)
const related = ref([])
const relatedError = ref('')
const loading = ref(true)
const error = ref('')
const { error: viewError, retry: retryView } = useContentView({
  kind: 'ARTICLE', target: () => article.value?.id,
  enabled: () => !!article.value && !loading.value,
  onCount: count => { if (article.value) article.value.viewTimes = count }
})
const readingBody = ref(null)
function readingPosition() {
  const rect = readingBody.value?.getBoundingClientRect()
  return rect ? Math.min(1, Math.max(0, (window.innerHeight - rect.top) / Math.max(1, rect.height))) : 0
}
const { progress, error: rewardError, retry } = useLearningReward({
  kind: 'READ', target: () => article.value && !isReference(article.value) ? article.value.id : null,
  enabled: () => !!article.value && !loading.value, position: readingPosition
})
async function loadRelated() {
  relatedError.value = ''
  try {
    const data = await api('/cs/studio/articles/' + article.value.id + '/series')
    if (!disposed) related.value = data
  } catch (e) { if (!disposed) relatedError.value = e.message }
}
onMounted(async () => {
  try {
    const content = await api('/ls/studio-articles/published' + (route.params.slug ? '/' + route.params.slug : '?' + new URLSearchParams({ pageNo: page.value, pageSize: 10, q: search.value, sort: sort.value })))
    if (disposed) return
    if (route.params.slug) { article.value = content; await loadRelated() }
    else { articles.value = content.list; total.value = content.total; pages.value = content.pages }
  } catch (e) { if (!disposed) error.value = e.message }
  finally { loading.value = false }
  if (!disposed) retry()
})
</script>

<template>
  <div v-if="!route.params.slug" class="library-heading">
    <p class="eyebrow">KNOWLEDGE & IDEAS</p>
    <h1>文章</h1>
    <p>经验、方法与思考，值得慢慢读，也值得分享。</p>
    <form class="article-search" role="search" @submit.prevent="find"><label class="field">搜索文章<input v-model="search" name="article-search" maxlength="80" placeholder="标题或关键词" /></label><button class="btn primary" type="submit">搜索</button></form>
    <label class="sort-control">排序<select :value="sort" aria-label="文章排序" @change="changeSort($event.target.value)"><option value="newest">最新发布</option><option value="likes">点赞最多</option><option value="views">浏览最多</option></select></label>
  </div>
  <p v-if="loading" class="loading-line" role="status">正在载入文章…</p>
  <div v-else-if="error" class="empty-state"><h2>暂时无法载入</h2><p role="alert">{{ error }}</p><RouterLink to="/" class="btn light">返回发现页</RouterLink></div>
  <div v-else class="reading-layout" :class="{ 'article-detail': !!article }">
    <section>
      <article v-if="article" class="reading-body">
        <RouterLink to="/articles" class="text-link"><Icon name="back" :size="16" />全部文章</RouterLink>
        <h1>{{ article.title }}</h1>
        <p class="content-tip">{{ article.summary }}</p>
        <ArticleSource :article="article" />
        <ContentMetrics :liked-times="article.likedTimes" :view-times="article.viewTimes" />
        <ContentLikeButton biz-type="ARTICLE" :biz-id="article.id" :liked-times="article.likedTimes" @updated="article.likedTimes = $event.likedTimes" />
        <p v-if="viewError" class="content-tip" role="alert">浏览统计暂未更新：{{ viewError }} <button type="button" class="text-link" @click="retryView">重试</button></p>
        <div ref="readingBody"><ArticleContent :body="article.body" :format="article.contentFormat || 'text'" :title="article.title" /></div>
        <div v-if="session.token && !isReference(article)" class="reading-status" role="status">
          <span v-if="article.rewarded || progress?.rewarded">✓ 本篇阅读奖励已领取，重复阅读不再加分。</span>
          <span v-else-if="progress?.eligible">已达到有效阅读条件，今日阅读奖励额度已用完。</span>
          <span v-else-if="progress">有效阅读 {{ progress.seconds }} / {{ progress.requiredSeconds }} 秒，读至正文末段后获得首次奖励。</span>
          <span v-else>正在准备有效阅读记录…</span>
          <p v-if="rewardError" class="form-error" role="alert">{{ rewardError }} <button class="btn light" type="button" @click="retry">重试</button></p>
        </div>
        <p v-else-if="!isReference(article)" class="reading-status"><RouterLink :to="{ path: '/login', query: { next: route.fullPath } }" class="text-link">登录后领取首次阅读奖励</RouterLink></p>
        <p v-else class="reading-status">外部资料的完整内容在原网站，本站导读不发放全文阅读奖励。</p>
        <RelatedContent :items="related" kind="video" />
        <p v-if="relatedError" class="form-error" role="alert">对应视频暂未载入：{{ relatedError }} <button class="text-link" @click="loadRelated">重试</button></p>
        <RouterLink to="/" class="btn light section-gap">看看可以解锁的专栏</RouterLink>
      </article>
      <div v-else class="reading-list">
        <ArticleCard v-for="item in articles" :key="item.id" :article="item" heading="h2" />
        <p v-if="!articles.length" class="content-tip">暂时没有符合条件的文章，试试其他关键词。</p>
        <Pagination :page="page" :pages="pages" :total="total" @change="changePage" />
      </div>
    </section>
    <aside v-if="!article" class="reading-account">
      <section class="article-side-note"><h2>让学习留下收获</h2><p class="content-tip">有效阅读、观看、每日签到和有效评论均可获取站内积分。</p><RouterLink :to="session.token ? '/account/points' : { path: '/login', query: { next: route.fullPath } }" class="btn primary">{{ session.token ? '查看积分账户' : '登录并继续阅读' }}</RouterLink></section>
      <section class="article-side-note section-gap"><h2>分享你的知识</h2><p class="content-tip">把实践经验和思考写成文章，让更多人受益。</p><RouterLink to="/studio?tab=articles" class="text-link">前往创作台 <Icon name="arrow" :size="16" /></RouterLink></section>
    </aside>
  </div>
</template>

<style scoped>
.reading-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 24px; align-items: start; }
.reading-list { min-width: 0; border-top: 1px solid var(--line); }
.reading-body h1 { font-size: clamp(25px, 3vw, 32px); margin: 22px 0 16px; line-height: 1.5; text-wrap: balance; }
.reading-layout.article-detail { display: block; max-width: 1080px; margin: 0 auto; }
.reading-body { min-width: 0; }
.reading-account { border-left: 1px solid var(--line); padding-left: 24px; }
.article-side-note h2 { font-size: 19px; margin-bottom: 12px; }
.article-side-note + .article-side-note { padding-top: 24px; border-top: 1px solid var(--line); }
.reading-status { border-top: 1px solid var(--line); padding-top: 18px; margin-top: 32px; font-size: 14px; color: var(--secondary-text); }
.reading-status button { margin-top: 10px; }
.wallet-number { display: flex; align-items: baseline; gap: 10px; margin: 20px 0; }
.wallet-number strong { font-size: 36px; line-height: 1; }
.wallet-number span { color: var(--muted); font-size: 13px; }
.article-search { display: flex; gap: 12px; align-items: end; max-width: 650px; margin-top: 24px; }
.article-search .field { flex: 1; min-width: 0; margin-bottom: 0; }
@media (max-width: 1100px) { .reading-layout { grid-template-columns: minmax(0, 1fr); } .reading-account { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; padding: 24px 0 0; border-left: 0; border-top: 1px solid var(--line); } .reading-account .section-gap { margin-top: 0; } .article-side-note + .article-side-note { border-top: 0; padding-top: 0; } }
@media (max-width: 600px) { .reading-account { grid-template-columns: 1fr; } .reading-card h2 { font-size: 19px; } }
</style>
