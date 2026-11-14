<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, studioApi, notify, session } from '../api'
import CoverArt from '../components/CoverArt.vue'
import Icon from '../components/Icon.vue'
import StudioDraft from '../components/StudioDraft.vue'
import ArticleManager from '../components/ArticleManager.vue'
import Followers from '../components/Followers.vue'
const followersOpen = ref(false)
const active = ref(useRoute().query.tab === 'articles' ? 'articles' : 'series')
const lifecycleBusy = ref('')
const states = { draft: '草稿', published: '已发布', unlisted: '已下架', archived: '已归档' }
const workspace = ref(null)
const error = ref('')
const editing = ref(null)
const saving = ref(false)
const profileBusy = ref(false)
const profileOpen = ref(false)
const draftEditor = ref(null)
const draftAnchor = ref(null)
const publishedSlug = ref('')
const profile = reactive({ displayName: '', headline: '', bio: '' })
const form = reactive({ title: '', subtitle: '', description: '', topic: '', coverTone: 'forest' })
const published = computed(() => workspace.value?.series.filter(s => s.available).length || 0)
onMounted(() => load({ refreshProfile: true }))
async function load({ refreshProfile = false } = {}) {
  try {
    workspace.value = await api(studioApi + '/workspace')
    if (refreshProfile) {
      Object.keys(profile).forEach(key => { profile[key] = workspace.value.creator?.[key] || '' })
      if (!workspace.value.creator) profile.displayName = session.user?.name || ''
    }
  } catch (e) { error.value = e.message }
}
async function edit(series) {
  if (series.status === 'draft') {
    await openDraft(series.id)
    return
  }
  draftEditor.value = null
  editing.value = editing.value === series.id ? null : series.id
  Object.keys(form).forEach(key => { form[key] = series[key] || '' })
  if (editing.value !== series.id) return

  await nextTick()
  document.getElementById('series-title-' + series.id)?.scrollIntoView({ block: 'nearest' })
}
async function openDraft(id = null) {
  editing.value = null
  draftEditor.value = { id, key: id || 'new' }
  publishedSlug.value = ''
  await nextTick()
  draftAnchor.value?.scrollIntoView({ block: 'nearest' })
}
async function draftSaved(id) {
  if (draftEditor.value) draftEditor.value.id = id
  await load()
}
async function draftPublished(slug) {
  draftEditor.value = null
  publishedSlug.value = slug
  await load()
  notify('专栏已发布')
}
async function saveSeries() {
  saving.value = true
  try {
    await api(studioApi + '/series/' + editing.value, { method: 'PUT', body: { ...form } })
    editing.value = null
    await load()
    notify('专栏信息已更新，发现页与详情页同步生效')
  } catch (e) { notify(e.message, 'error') } finally { saving.value = false }
}
async function transition(series, state) {
  if (lifecycleBusy.value || draftEditor.value) return
  if (['revision', 'unlisted', 'archived'].includes(state) && !window.confirm(
    state === 'revision' ? '进入内容编辑后，此专栏将暂时停止公开和新的解锁。已有权益可继续观看旧版本。确定继续吗？'
      : state === 'unlisted' ? '确认下架此专栏？已有权益继续保留。' : '确认归档此草稿？可在工作台恢复。')) return
  lifecycleBusy.value = series.id
  try {
    const path = studioApi + '/workspace/series/' + series.id
    await api(path + (state === 'revision' ? '/revision' : '/state/' + state), { method: state === 'revision' ? 'POST' : 'PUT' })
    editing.value = null
    await load()
    if (state === 'revision') await openDraft(series.id)
    notify(state === 'revision' ? '已打开修订稿' : '专栏状态已更新')
  } catch (e) { notify(e.message, 'error') } finally { lifecycleBusy.value = '' }
}
async function saveProfile() {
  profileBusy.value = true
  try {
    const opening = !workspace.value.creator
    await api(studioApi + (opening ? '/workspace' : '/creators/me'), {
      method: opening ? 'POST' : 'PUT',
      body: { ...profile }
    })
    await load({ refreshProfile: true })
    notify(opening ? '创作者空间已开通，可以新建专栏了' : '创作者主页已更新')
  } catch (e) { notify(e.message, 'error') } finally { profileBusy.value = false }
}
</script>

<template>
  <div v-if="error" class="empty-state"><Icon name="lock" :size="32" /><h2>创作者工作台</h2><p>{{ error }}</p><RouterLink to="/" class="btn primary">返回发现页</RouterLink></div>
  <div v-else-if="!workspace" class="loading-line">正在打开你的工作台…</div>
  <section v-else-if="!workspace.creator" class="content-panel studio-start">
    <p class="eyebrow">CREATOR STUDIO</p>
    <h1>开通创作者空间</h1>
    <p class="content-tip">创建你的公开主页。开通后就可以新建专栏、上传视频和设置试看。</p>
    <form @submit.prevent="saveProfile">
      <label class="field">创作者名称<input v-model.trim="profile.displayName" required maxlength="50" /></label>
      <label class="field">一句话介绍<input v-model.trim="profile.headline" required maxlength="120" /></label>
      <label class="field">关于自己<textarea v-model.trim="profile.bio" required maxlength="2000" /></label>
      <button type="submit" class="btn primary" :disabled="profileBusy">
        {{ profileBusy ? '正在开通…' : '开通创作者空间' }}
      </button>
    </form>
  </section>
  <template v-else>
    <div class="library-heading">
      <p class="eyebrow">CREATOR STUDIO</p>
      <div class="studio-heading">
        <h1>创作台</h1>
        <button v-if="active === 'series'" type="button" class="btn primary" :disabled="!!draftEditor" @click="openDraft()">新建专栏</button>
      </div>
      <p>{{ workspace.creator.displayName }}，在这里管理文章、视频专栏与配套资料。</p>
    </div>
    <div v-if="publishedSlug" class="content-panel detail-actions" role="status">
      <span>专栏已发布</span>
      <RouterLink :to="'/series/' + publishedSlug" class="text-link">查看公开页面 <Icon name="upRight" :size="14" /></RouterLink>
    </div>
    <div class="studio-summary"><div class="metric"><span>我的专栏</span><strong>{{ workspace.series.length }}</strong></div><div class="metric"><span>已公开专栏</span><strong>{{ published }}</strong></div><button type="button" class="metric follower-entry" :aria-expanded="followersOpen" aria-controls="studio-followers" @click="followersOpen = !followersOpen"><span>关注者 · 查看名单</span><strong>{{ workspace.creator.followerCount || 0 }}</strong></button></div>
    <div v-if="followersOpen" id="studio-followers"><Followers /></div>
    <aside class="content-panel creator-profile">
      <div class="detail-title">
        <h3>你的创作者名片</h3>
        <button type="button" class="btn light" :aria-expanded="profileOpen"
          aria-controls="creator-profile-form" @click="profileOpen = !profileOpen">
          {{ profileOpen ? '收起编辑' : '展开编辑' }}
        </button>
      </div>
      <form v-show="profileOpen" id="creator-profile-form" @submit.prevent="saveProfile">
        <p class="content-tip">告诉大家你是谁，以及你想分享什么。</p>
        <label class="field">
          创作者名称
          <input v-model.trim="profile.displayName" required maxlength="50" />
        </label>
        <label class="field">
          个人主张
          <input v-model.trim="profile.headline" required maxlength="120" />
        </label>
        <label class="field">
          关于自己
          <textarea v-model.trim="profile.bio" required maxlength="2000" />
        </label>
        <button type="submit" class="btn primary wide" :disabled="profileBusy">
          {{ profileBusy ? '保存中…' : '更新创作者主页' }}
        </button>
      </form>
    </aside>
    <nav class="studio-tabs" aria-label="创作内容切换">
      <button type="button" class="btn" :class="active === 'series' ? 'primary' : 'light'" :aria-pressed="active === 'series'" @click="active = 'series'">视频专栏</button>
      <button type="button" class="btn" :class="active === 'articles' ? 'primary' : 'light'" :aria-pressed="active === 'articles'" @click="active = 'articles'">文章</button>
    </nav>
    <section v-show="active === 'articles'" class="content-panel"><ArticleManager /></section>
    <div v-show="active === 'series'">
    <div v-if="draftEditor" ref="draftAnchor">
      <StudioDraft :key="draftEditor.key" :series-id="draftEditor.id"
        @close="draftEditor = null" @saved="draftSaved" @published="draftPublished" />
    </div>
    <div class="studio-layout">
      <section class="content-panel">
        <div class="section-title">
          <h2>我的专栏</h2>
          <RouterLink :to="'/creators/' + workspace.creator.slug" class="text-link">查看主页 <Icon name="upRight" :size="14" /></RouterLink>
        </div>
        <p class="content-tip">从这里继续编辑草稿，或维护已发布的专栏信息。</p>
        <div v-for="series in workspace.series" :key="series.id">
          <article class="studio-item">
            <CoverArt :tone="series.coverTone" />
            <div class="studio-item-info"><h3>{{ series.title }}</h3><p>{{ series.episodeCount }} 集 · {{ series.blocked ? '运营已屏蔽' : states[series.status] }}</p><small v-if="series.status === 'draft' && series.previouslyPublished">修订中，已有权益仍可观看旧版本</small></div>
            <div class="series-management-actions">
            <button v-if="series.status === 'draft'" type="button" class="btn light"
              :disabled="!!draftEditor || !!lifecycleBusy || series.blocked" @click="edit(series)">继续编辑</button>
            <button v-else-if="series.status === 'published'" type="button" class="btn light" :disabled="!!draftEditor || saving || !!lifecycleBusy || series.blocked" :aria-expanded="editing === series.id"
              :aria-controls="editing === series.id ? 'series-edit-' + series.id : undefined" @click="edit(series)">
              {{ editing === series.id ? '收起' : '编辑介绍' }}
            </button>
            <button v-if="series.status === 'published' || series.status === 'unlisted' || series.status === 'archived'" type="button" class="btn light" :disabled="!!draftEditor || !!lifecycleBusy || series.blocked" @click="transition(series, 'revision')">{{ series.status === 'archived' ? '恢复编辑' : '编辑内容与关联' }}</button>
            <button v-if="series.status === 'published' || series.status === 'unlisted'" type="button" class="btn light" :disabled="!!draftEditor || !!lifecycleBusy || series.blocked" @click="transition(series, series.status === 'published' ? 'unlisted' : 'published')">{{ series.status === 'published' ? '下架' : '重新上架' }}</button>
            <button v-if="series.status === 'draft' && !series.previouslyPublished" type="button" class="btn light" :disabled="!!draftEditor || !!lifecycleBusy || series.blocked" @click="transition(series, 'archived')">归档</button>
            </div>
          </article>
          <form v-if="editing === series.id" :id="'series-edit-' + series.id" class="edit-form" @submit.prevent="saveSeries">
            <label class="field">专栏标题<input :id="'series-title-' + series.id" v-model.trim="form.title" required maxlength="80" /></label>
            <label class="field">一句话介绍<input v-model.trim="form.subtitle" required maxlength="180" /></label>
            <div class="field-grid">
              <label class="field">内容主题<input v-model.trim="form.topic" required maxlength="40" /></label>
              <label class="field">封面风格
                <select v-model="form.coverTone">
                  <option value="forest">深林</option><option value="clay">陶土</option>
                  <option value="violet">暮紫</option><option value="mint">青柠</option>
                </select>
              </label>
            </div>
            <label class="field">完整介绍<textarea v-model.trim="form.description" required maxlength="4000" /></label>
            <div class="detail-actions">
              <button class="btn primary" :disabled="saving">{{ saving ? '保存中…' : '保存专栏信息' }}</button>
              <RouterLink :to="'/series/' + series.slug" class="text-link">查看详情 <Icon name="upRight" :size="13" /></RouterLink>
            </div>
          </form>
        </div>
        <p v-if="!workspace.series.length" class="content-tip">你还没有专栏。点击「新建专栏」，从一份草稿开始。</p>
      </section>
    </div>
    </div>
  </template>
</template>

<style scoped>
.studio-heading { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
.creator-profile { margin-bottom: 18px; }
.studio-tabs { display: flex; gap: 10px; margin-bottom: 18px; }
.follower-entry { width: 100%; text-align: left; cursor: pointer; font: inherit; color: inherit; }
.follower-entry:hover { border-color: var(--green); }
.studio-start { max-width: 580px; margin-inline: auto; }
.studio-start h1 { margin-top: 8px; }
.studio-layout { grid-template-columns: minmax(0, 1fr); }
.studio-item-info { flex: 1; min-width: 0; }
.studio-item h3 { overflow-wrap: anywhere; }
.series-management-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-left: auto; flex-shrink: 0; }
.series-management-actions .btn { margin-left: 0; }
@media (max-width: 800px) { .studio-item { flex-wrap: wrap; } .series-management-actions { width: 100%; } }
</style>
