<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, notify, studioApi, timestamp } from '../api'
import RelatedArticlePicker from './RelatedArticlePicker.vue'

const props = defineProps({ seriesId: { type: [String, Number], default: null } })
const emit = defineEmits(['saved', 'published', 'close'])
const id = ref(props.seriesId)
const previouslyPublished = ref(false)
const form = reactive({ title: '', subtitle: '', description: '', topic: '', coverTone: 'forest', unlockPoints: 100, categoryId: '', articleIds: [] })
const categories = ref([])
const episodes = ref([])
const previewMediaId = ref('')
const previewMinutes = ref(3)
const loading = ref(true)
const busy = ref('')
const error = ref('')
const basics = ref(null)
const fileInput = ref(null)
const replaceIndex = ref(null)
const workspace = studioApi + '/workspace'
const draftPath = computed(() => workspace + '/series/' + id.value)

onMounted(async () => {
  try {
    const [options, draft] = await Promise.all([
      api(workspace + '/categories'),
      id.value ? api(draftPath.value) : null
    ])
    categories.value = options
    if (!draft) return
    previouslyPublished.value = draft.series.previouslyPublished
    for (const key of Object.keys(form)) form[key] = draft.series[key]
    episodes.value = draft.episodes
    previewMediaId.value = draft.episodes.find(episode => episode.id === draft.series.previewSectionId)?.mediaId || ''
    previewMinutes.value = draft.series.previewMinutes || 3
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function perform(action, work) {
  busy.value = action
  error.value = ''
  try {
    await work()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

async function persistBasics() {
  const body = { ...form, unlockPoints: Number(form.unlockPoints) }
  if (id.value) {
    await api(draftPath.value, { method: 'PUT', body })
  } else {
    id.value = await api(workspace + '/series', { method: 'POST', body })
  }
}

function saveBasics() {
  perform('basics', async () => {
    await persistBasics()
    emit('saved', id.value)
    notify('草稿已保存')
  })
}

function chooseVideo(index = null) {
  replaceIndex.value = index
  fileInput.value.click()
}

function upload(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (!file) return
  const index = replaceIndex.value
  perform('upload', async () => {
    const body = new FormData()
    body.append('file', file)
    const asset = await api('/ms/medias/studio/upload', { method: 'POST', body })
    const name = file.name.replace(/\.mp4$/i, '').slice(0, 100) || '未命名分集'
    const episode = { mediaId: asset.id, title: name, filename: asset.filename, duration: asset.duration }
    if (index === null) {
      episodes.value.push(episode)
    } else {
      const previous = episodes.value[index]
      if (previewMediaId.value === previous.mediaId) previewMediaId.value = episode.mediaId
      episodes.value[index] = { ...episode, title: previous.title }
    }
    notify('视频已上传，请保存分集')
  })
}

function move(index, offset) {
  const next = index + offset
  const [episode] = episodes.value.splice(index, 1)
  episodes.value.splice(next, 0, episode)
}

function remove(index) {
  const [episode] = episodes.value.splice(index, 1)
  if (previewMediaId.value === episode.mediaId) previewMediaId.value = ''
}

async function persistEpisodes() {
  if (episodes.value.some(episode => !episode.title.trim())) throw new Error('请填写每一集的标题')
  await api(draftPath.value + '/episodes', {
    method: 'PUT',
    body: {
      episodes: episodes.value.map(({ mediaId, title }) => ({ mediaId, title })),
      previewMediaId: previewMediaId.value || null,
      previewMinutes: previewMinutes.value
    }
  })
}

function saveEpisodes() {
  perform('episodes', async () => {
    await persistEpisodes()
    emit('saved', id.value)
    notify('分集与试看设置已保存')
  })
}

function publish() {
  if (!basics.value.reportValidity()) return
  perform('publish', async () => {
    await persistBasics()
    await persistEpisodes()
    emit('saved', id.value)
    const slug = await api(draftPath.value + '/publish', { method: 'POST' })
    emit('published', slug)
  })
}
</script>

<template>
  <section class="content-panel draft-editor" aria-label="专栏草稿">
    <div class="section-title">
      <h2>{{ id ? '编辑专栏草稿' : '新建专栏' }}</h2>
      <button type="button" class="btn light" :disabled="!!busy" @click="emit('close')">收起</button>
    </div>
    <p v-if="loading" class="content-tip" role="status">正在载入草稿…</p>
    <p v-if="previouslyPublished" class="content-tip">正在编辑已发布专栏的修订稿。重新发布前，已有权益仍观看原版本；解锁积分和分类保持不变。</p>
    <div v-if="error" class="form-error" role="alert">{{ error }}</div>
    <template v-if="!loading && categories.length">
      <form ref="basics" class="edit-form" @submit.prevent="saveBasics">
        <fieldset :disabled="!!busy">
          <legend>1. 专栏信息</legend>
          <label class="field">专栏标题<input v-model.trim="form.title" maxlength="80" required /></label>
          <label class="field">一句话介绍<input v-model.trim="form.subtitle" maxlength="180" required /></label>
          <div class="field-grid">
            <label class="field">内容分类
              <select v-model="form.categoryId" :disabled="previouslyPublished" required>
                <option disabled value="">请选择分类</option>
                <option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option>
              </select>
            </label>
            <label class="field">内容主题<input v-model.trim="form.topic" maxlength="40" required /></label>
            <label class="field">解锁积分（0 为免费）
              <input v-model="form.unlockPoints" :readonly="previouslyPublished" type="number" min="0" max="100000" step="1" required />
            </label>
            <label class="field">封面风格
              <select v-model="form.coverTone">
                <option value="forest">深林</option><option value="clay">陶土</option>
                <option value="violet">暮紫</option><option value="mint">青柠</option>
              </select>
            </label>
          </div>
          <label class="field">完整介绍<textarea v-model.trim="form.description" maxlength="4000" required /></label>
          <RelatedArticlePicker v-model="form.articleIds" :disabled="!!busy" />
          <button type="submit" class="btn primary">
            {{ busy === 'basics' ? '保存中…' : id ? '保存专栏信息' : '保存并添加视频' }}
          </button>
        </fieldset>
      </form>
      <section v-if="id" class="draft-episodes">
        <h3>2. 视频分集与试看</h3>
        <p class="content-tip">每集上传一个 MP4 文件，最大 100 MB。可在发布前更换视频或调整顺序。</p>
        <input ref="fileInput" type="file" accept="video/mp4,.mp4" hidden @change="upload" />
        <ol class="episode-editor-list">
          <li v-for="(episode, index) in episodes" :key="episode.mediaId">
            <label class="field">第 {{ index + 1 }} 集标题
              <input v-model.trim="episode.title" :disabled="!!busy" maxlength="100" required />
            </label>
            <p class="content-tip">{{ episode.filename }}{{ episode.duration ? ' · ' + timestamp(episode.duration) : '' }}</p>
            <div class="detail-actions">
              <button type="button" class="btn light" :disabled="!!busy || index === 0" @click="move(index, -1)">上移</button>
              <button type="button" class="btn light" :disabled="!!busy || index === episodes.length - 1" @click="move(index, 1)">下移</button>
              <button type="button" class="btn light" :disabled="!!busy" @click="chooseVideo(index)">替换视频</button>
              <button type="button" class="btn light" :disabled="!!busy" @click="remove(index)">移除分集</button>
            </div>
          </li>
        </ol>
        <button type="button" class="btn light" :disabled="!!busy || episodes.length >= 30" @click="chooseVideo()">
          {{ busy === 'upload' ? '正在上传…' : '上传视频' }}
        </button>
        <div v-if="episodes.length && Number(form.unlockPoints) > 0" class="field-grid preview-fields">
          <label class="field">试看分集
            <select v-model="previewMediaId" :disabled="!!busy">
              <option value="">不设置试看</option>
              <option v-for="(episode, index) in episodes" :key="episode.mediaId" :value="episode.mediaId">
                第 {{ index + 1 }} 集 · {{ episode.title }}
              </option>
            </select>
          </label>
          <label v-if="previewMediaId" class="field">试看时长
            <select v-model.number="previewMinutes" :disabled="!!busy">
              <option v-for="minutes in 3" :key="minutes" :value="minutes">{{ minutes }} 分钟</option>
            </select>
          </label>
        </div>
        <div class="detail-actions">
          <button type="button" class="btn light" :disabled="!!busy" @click="saveEpisodes">
            {{ busy === 'episodes' ? '保存中…' : '保存分集与试看' }}
          </button>
        </div>
      </section>
      <section v-if="id" class="draft-episodes">
        <h3>3. 发布专栏</h3>
        <p class="content-tip">发布会保存当前编辑内容。至少需要一集视频，并等待视频处理完成。</p>
        <button type="button" class="btn primary" :disabled="!!busy || !episodes.length" @click="publish">
          {{ busy === 'publish' ? '发布中…' : '发布专栏' }}
        </button>
      </section>
    </template>
    <p v-else-if="!loading && !error" class="content-tip">暂无可用分类。</p>
  </section>
</template>

<style scoped>
.draft-editor { margin-bottom: 18px; }
fieldset { min-width: 0; border: 0; padding: 0; }
legend, .draft-episodes h3 { font-weight: 650; margin-bottom: 12px; }
.draft-episodes { padding-top: 18px; margin-top: 18px; border-top: 1px solid var(--line); }
.episode-editor-list { list-style: none; padding: 0; }
.episode-editor-list li { padding: 12px; margin-bottom: 10px; border: 1px solid var(--line); border-radius: 8px; }
.episode-editor-list .content-tip { overflow-wrap: anywhere; }
.preview-fields { margin-top: 14px; }
</style>
