<script setup>
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { api, studioApi } from '../api'
import Pagination from './Pagination.vue'
import UserAvatar from './UserAvatar.vue'
const page = shallowRef(1)
const loading = shallowRef(false)
const error = shallowRef('')
const result = ref({ list: [], total: 0, pages: 0 })
const format = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
const date = value => value ? format.format(new Date(value.replace(' ', 'T'))) : '时间未知'
let request
async function load() {
  request?.abort()
  const current = new AbortController()
  request = current
  loading.value = true
  error.value = ''
  try {
    const found = await api(studioApi + '/workspace/followers?pageNo=' + page.value + '&pageSize=10', { signal: current.signal })
    if (!current.signal.aborted) result.value = found
  } catch (e) { if (!current.signal.aborted) error.value = e.message }
  finally { if (!current.signal.aborted) loading.value = false }
}
watch(page, load, { immediate: true })
onBeforeUnmount(() => request?.abort())
</script>

<template>
  <section class="content-panel followers-panel" aria-label="关注者名单">
    <h2>关注你的人</h2><p class="content-tip">按最近关注排序，共 {{ result.total }} 人。</p>
    <p v-if="loading" role="status" class="content-tip">正在载入关注者…</p>
    <p v-if="error" role="alert" class="form-error">{{ error }} <button type="button" class="text-link" @click="load">重新加载</button></p>
    <ul class="followers-list">
      <li v-for="follower in result.list" :key="follower.userId" class="follower-row">
        <UserAvatar :user="follower" /><div><b>{{ follower.name }}</b><small class="follower-date">{{ date(follower.followedAt) }} 关注</small></div>
      </li>
    </ul>
    <p v-if="!loading && !error && !result.list.length" class="content-tip">暂时还没有关注者。</p>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading" @change="page = $event" />
  </section>
</template>

<style scoped>
.followers-panel { margin: 0 0 20px; }
.followers-list { list-style: none; padding: 0; margin: 0; }
.follower-row { display: flex; align-items: center; gap: 12px; padding: 16px 0; border-bottom: 1px solid var(--line); }
.follower-date { display: block; margin-top: 5px; color: var(--secondary-text); }
</style>
