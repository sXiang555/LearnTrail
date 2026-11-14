<script setup>
import { ref, watch } from 'vue'
import { api, pointsApi } from '../api'
import Pagination from './Pagination.vue'
const props = defineProps({ revision: { type: Number, default: 0 } })
const page = ref(1)
const kind = ref('')
const direction = ref('')
const result = ref({ list: [], total: 0, pages: 0 })
const busy = ref(false)
const error = ref('')
const labels = { INITIAL: '初始积分', READ: '阅读奖励', WATCH: '观看奖励', SIGN: '每日签到', COMMENT: '评论奖励', UNLOCK: '专栏解锁', COUPON: '优惠券兑换' }
function filter(name, value) { page.value = 1; if (name === 'kind') kind.value = value; else direction.value = value }
let requestId = 0
async function load() {
  const id = ++requestId
  busy.value = true; error.value = ''
  try {
    const data = await api(pointsApi + '/entries?' + new URLSearchParams({ pageNo: page.value, pageSize: 10,
      ...(kind.value ? { kind: kind.value } : {}), ...(direction.value ? { direction: direction.value } : {}) }))
    if (id === requestId) result.value = data
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) busy.value = false }
}
watch([page, kind, direction, () => props.revision], load, { immediate: true })
</script>

<template>
  <section class="content-panel">
    <h3>积分记录</h3>
    <div class="history-filters"><label class="field">变动方向<select :value="direction" @change="filter('direction', $event.target.value)"><option value="">全部</option><option value="income">积分获取</option><option value="expense">积分消费</option></select></label><label class="field">变动类型<select :value="kind" @change="filter('kind', $event.target.value)"><option value="">全部类型</option><option v-for="(label, value) in labels" :key="value" :value="value">{{ label }}</option></select></label></div>
    <p v-if="error" class="form-error" role="alert">{{ error }} <button class="text-link" @click="load">重试</button></p>
    <p v-else-if="busy" role="status">正在载入积分记录…</p>
    <p v-else-if="!result.list.length" class="content-tip">暂时没有积分记录。</p>
    <ol v-else class="point-entries">
      <li v-for="entry in result.list" :key="entry.kind + ':' + entry.targetId">
        <div><b>{{ labels[entry.kind] || entry.kind }}</b><span :class="{ earned: entry.amount > 0 }">{{ entry.amount > 0 ? '+' : '' }}{{ entry.amount }}</span></div>
        <p>{{ entry.title }}</p><small>{{ entry.createdAt?.replace('T', ' ').slice(0, 16) }}</small>
      </li>
    </ol>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="busy" @change="page = $event" />
  </section>
</template>

<style scoped>
.history-filters { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 20px; }
.history-filters .field { min-width: 150px; flex: 1; }
.point-entries { list-style: none; margin: 0; padding: 0; }
.point-entries li { border-top: 1px solid var(--line); padding: 14px 0; }
.point-entries li > div { display: flex; justify-content: space-between; gap: 10px; font-size: 14px; }
.earned { color: var(--green); }
.point-entries p { margin: 6px 0; font-size: 13px; overflow-wrap: anywhere; }
.point-entries small { color: var(--muted); }
</style>
