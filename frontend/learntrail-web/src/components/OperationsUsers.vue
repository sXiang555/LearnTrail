<script setup>
import { reactive, ref, watch } from 'vue'
import { api, notify } from '../api'
import Pagination from './Pagination.vue'
const filters = reactive({ name: '', status: '' })
const page = ref(1)
const result = ref({ list: [], total: 0, pages: 0 })
const loading = ref(false)
const busy = ref('')
const error = ref('')
let requestId = 0
async function load() {
  const id = ++requestId
  loading.value = true; error.value = ''
  const query = new URLSearchParams({ pageNo: page.value, pageSize: 10, name: filters.name })
  if (filters.status !== '') query.set('status', filters.status)
  try {
    const data = await api('/us/operations/users?' + query)
    if (id === requestId) result.value = data
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) loading.value = false }
}
watch(page, load, { immediate: true })
function search() { if (page.value !== 1) page.value = 1; else load() }
async function status(user) {
  const next = user.status === 1 ? 0 : 1
  if (!window.confirm((next ? '恢复' : '禁用') + '账号「' + user.username + '」？')) return
  busy.value = user.id
  try {
    await api('/us/operations/users/' + user.id + '/status/' + next, { method: 'PUT' })
    await load(); notify('用户状态已更新')
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
</script>

<template>
  <section class="content-panel">
    <h2>用户管理</h2><p class="content-tip">此入口只能修改普通账号状态。禁用阻止新登录和续期，已签发的会话最多 5 分钟后失效。</p>
    <form class="ops-filters" @submit.prevent="search">
      <label class="field">昵称<input v-model.trim="filters.name" name="user-search" autocomplete="off" maxlength="80" placeholder="按昵称搜索" /></label>
      <label class="field">账号状态<select v-model="filters.status" name="user-status"><option value="">全部</option><option value="1">正常</option><option value="0">禁用</option></select></label>
      <button class="btn light" :disabled="loading">查询</button>
    </form>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p><p v-if="loading" role="status">正在载入用户…</p>
    <div class="table-scroll"><table>
      <caption class="sr-only">平台用户及账号状态</caption>
      <thead><tr><th scope="col">昵称 / 用户名</th><th scope="col">类型</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
      <tbody><tr v-for="user in result.list" :key="user.id"><td><b>{{ user.name || '未设置昵称' }}</b><small>{{ user.username }}</small></td><td>{{ ({ 1: '员工', 2: '读者', 3: '讲师' })[user.type] || '其他' }}</td><td>{{ user.status === 1 ? '正常' : '禁用' }}</td><td><button v-if="user.type !== 1" class="btn light" :disabled="!!busy" @click="status(user)">{{ busy === user.id ? '处理中…' : user.status === 1 ? '禁用账号' : '恢复账号' }}</button><span v-else class="content-tip">受保护</span></td></tr></tbody>
    </table></div>
    <p v-if="!loading && !result.list.length" class="content-tip">没有匹配的用户。</p>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading || !!busy" @change="page = $event" />
  </section>
</template>

<style scoped>
.ops-filters { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; margin-top: 20px; }
.ops-filters .field { flex: 1; min-width: 150px; margin: 0; }
.table-scroll { overflow-x: auto; margin-top: 24px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 14px 12px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
td small { display: block; margin-top: 5px; color: var(--muted); }
</style>
