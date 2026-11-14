<script setup>
import { computed, ref, shallowRef, watch } from 'vue'
import { api } from '../api'
import Pagination from './Pagination.vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const query = shallowRef('')
const search = shallowRef('')
const page = shallowRef(1)
const result = ref({ list: [], total: 0, pages: 0 })
const titles = ref({})
const busy = shallowRef(false)
const error = shallowRef('')
const selected = computed(() => new Set(props.modelValue.map(String)))
let request = 0
async function load() {
  const id = ++request
  busy.value = true; error.value = ''
  try {
    const data = await api('/ls/studio-articles/manage?' + new URLSearchParams({ scope: 'mine', q: search.value, pageNo: page.value, pageSize: 10 }))
    if (id !== request) return
    result.value = data
    for (const item of data.list) titles.value[item.id] = item.title
  } catch (e) { if (id === request) error.value = e.message }
  finally { if (id === request) busy.value = false }
}
watch([page, search], load, { immediate: true })
watch(() => props.modelValue, async ids => {
  if (!ids.length) return
  try {
    const data = await api('/ls/studio-articles/published/links?' + new URLSearchParams({ ids: ids.join(','), owned: true }))
    for (const item of data) titles.value[item.id] = item.title
  } catch (e) { error.value = e.message }
}, { immediate: true })
function toggle(item, checked) {
  if (props.disabled) return
  emit('update:modelValue', checked ? [...props.modelValue, item.id] : props.modelValue.filter(id => String(id) !== String(item.id)))
}
function find() { page.value = 1; search.value = query.value.trim(); load() }
</script>

<template>
  <section class="article-picker" aria-labelledby="related-picker-title">
    <h3 id="related-picker-title">关联文章（可选）</h3>
    <p class="content-tip">最多选择 20 篇本人文章。发布后，仅已发布的文章会显示在配套文章区域。</p>
    <ul v-if="modelValue.length" class="selected-articles">
      <li v-for="id in modelValue" :key="id"><span>{{ titles[id] || '文章 ' + id }}</span><button class="text-link" type="button" :disabled="disabled" :aria-label="'解除关联：' + (titles[id] || id)" @click="toggle({ id }, false)">解除关联</button></li>
    </ul>
    <div class="picker-search"><label class="field">搜索本人文章<input v-model="query" :disabled="disabled" maxlength="80" @keydown.enter.prevent="find" /></label><button class="btn light" type="button" :disabled="disabled || busy" @click="find">搜索</button></div>
    <p v-if="error" class="form-error" role="alert">{{ error }} <button type="button" class="text-link" @click="load">重试</button></p>
    <p v-else-if="busy" role="status">正在载入本人文章…</p>
    <div v-else class="article-options">
      <label v-for="item in result.list" :key="item.id" class="article-option">
        <input type="checkbox" :checked="selected.has(String(item.id))" :disabled="disabled || item.status === 'blocked' || (!selected.has(String(item.id)) && modelValue.length >= 20)" @change="toggle(item, $event.target.checked)" />
        <span>{{ item.title }} <small>{{ ({ draft: '草稿', published: '已发布', archived: '已归档', blocked: '已屏蔽' })[item.status] }}</small></span>
      </label>
      <p v-if="!result.list.length" class="content-tip">还没有符合条件的本人文章。</p>
    </div>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="busy || disabled" @change="page = $event" />
  </section>
</template>

<style scoped>
.article-picker { margin: 24px 0; padding-top: 20px; border-top: 1px solid var(--line); }
.picker-search { display: flex; gap: 12px; align-items: end; margin: 16px 0; }
.picker-search .field { flex: 1; min-width: 0; margin: 0; }
.article-option { display: flex; align-items: start; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
.article-option input { width: auto; margin-top: 4px; accent-color: var(--green); }
.article-option small { color: var(--muted); margin-left: 8px; }
.selected-articles { list-style: none; padding: 0; }
.selected-articles li { display: flex; justify-content: space-between; gap: 16px; padding: 10px; background: var(--background, #f5f6f0); margin: 6px 0; border-radius: 8px; overflow-wrap: anywhere; }
.selected-articles button { flex-shrink: 0; }
</style>
