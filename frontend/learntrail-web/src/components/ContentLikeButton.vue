<script setup>
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, session } from '../api.js'
import Icon from './Icon.vue'

const props = defineProps({
  bizType: { type: String, required: true, validator: value => ['ARTICLE', 'SERIES'].includes(value) },
  bizId: { type: [String, Number], required: true },
  likedTimes: { type: [String, Number], default: null },
  disabled: Boolean
})
const emit = defineEmits(['updated'])
const router = useRouter()
const route = useRoute()
const state = ref(null)
const loading = shallowRef(false)
const busy = shallowRef(false)
const error = shallowRef('')
let generation = 0
onBeforeUnmount(() => { generation++ })

const statePath = () => '/rs/likes/content/' + props.bizType + '/' + props.bizId
async function load() {
  const current = ++generation
  state.value = null; error.value = ''; busy.value = false
  loading.value = Boolean(session.token && !props.disabled)
  if (!loading.value) return
  try {
    const data = await api(statePath())
    if (current === generation) { state.value = data; emit('updated', data) }
  } catch (e) {
    if (current === generation) error.value = e.message
  } finally {
    if (current === generation) loading.value = false
  }
}
watch([() => props.bizType, () => props.bizId, () => props.disabled,
  () => session.user?.id, () => Boolean(session.token)], load, { immediate: true })

async function toggle() {
  if (busy.value || loading.value || props.disabled) return
  if (!session.token) return router.push({ path: '/login', query: { next: route.fullPath } })
  if (!state.value) return
  const current = generation
  const target = { bizType: props.bizType, bizId: props.bizId, liked: !state.value.liked }
  busy.value = true; error.value = ''
  try {
    await api('/rs/likes', { method: 'POST', body: target })
    if (current !== generation) return
    // Counts come from the server, including concurrent updates by other users.
    const data = await api(statePath())
    if (current === generation) { state.value = data; emit('updated', data) }
  } catch (e) {
    if (current === generation) {
      state.value = null
      error.value = e.message + '，请重新读取点赞状态后再操作。'
    }
  } finally {
    if (current === generation) busy.value = false
  }
}
</script>

<template>
  <div class="content-like">
    <button type="button" class="btn outlined like-button" :class="{ liked: state?.liked }"
      :disabled="disabled || loading || busy || (!!session.token && !state)"
      :aria-pressed="Boolean(state?.liked)" :aria-busy="loading || busy"
      :aria-label="state?.liked ? '取消点赞' : session.token ? '点赞' : '登录后点赞'" @click="toggle">
      <Icon name="heart" :size="17" />
      <span>{{ busy ? '提交中…' : loading ? '读取中…' : state?.liked ? '已点赞' : '点赞' }}</span>
      <span v-if="state || (!session.token && likedTimes !== null)" class="like-count">{{ state?.likedTimes ?? likedTimes }}</span>
    </button>
    <span v-if="disabled" class="like-hint">内容暂未公开，不能点赞。</span>
    <p v-if="error" class="form-error" role="alert">
      {{ error }} <button type="button" class="text-link" :disabled="loading || busy" @click="load">重新读取</button>
    </p>
  </div>
</template>

<style scoped>
.content-like { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
.like-button { min-height: 44px; }
.like-button.liked { color: var(--green); background: #e9efdf; border-color: #789b7d; }
.like-button:hover:not(:disabled) { background: #e9efdf; }
.like-count { min-width: 1ch; font-variant-numeric: tabular-nums; }
.like-hint { color: var(--muted); font-size: 13px; }
.form-error { flex-basis: 100%; font-size: 13px; }
</style>
