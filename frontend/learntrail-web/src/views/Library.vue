<script setup>
import { computed, ref, watch } from 'vue'
import { api, studioApi, price, notify } from '../api'
import SeriesCard from '../components/SeriesCard.vue'
import Icon from '../components/Icon.vue'
import Pagination from '../components/Pagination.vue'

const props = defineProps({ mode: { type: String, default: 'owned' } })
const items = ref([])
const loading = ref(true)
const error = ref('')
const page = ref(1)
const total = ref(0)
const pages = ref(0)
let requestId = 0
const heading = computed(() => ({
  owned: ['已解锁内容', '把看过的内容，慢慢变成自己的能力。', 'YOUR LIBRARY'],
  saved: ['留给下一段时间', '收藏让你好奇的内容，准备好了再出发。', 'YOUR WATCHLIST'],
  orders: ['每一笔，清楚可见', '查看已有订单与实际交易状态。', 'YOUR ORDERS']
}[props.mode]))

async function load() {
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    if (props.mode === 'orders') {
      const result = await api('/ts/orders/page?pageNo=' + page.value + '&pageSize=10')
      if (id !== requestId) return
      items.value = result.list || []
      total.value = result.total || 0
      pages.value = result.pages || 0
    } else {
      const result = await api(studioApi + '/series/page?scope=' + props.mode + '&sort=newest&pageNo=' + page.value + '&pageSize=10')
      if (id !== requestId) return
      items.value = result.list; total.value = result.total; pages.value = result.pages
    }
  } catch (e) { if (id === requestId) { error.value = e.message; notify(e.message, 'error') } }
  finally { if (id === requestId) loading.value = false }
}
watch(() => props.mode, () => { page.value = 1; load() }, { immediate: true })
function changePage(value) { page.value = value; load() }
</script>

<template>
  <div class="library-heading"><p class="eyebrow">{{ heading[2] }}</p><h1>{{ heading[0] }}</h1><p>{{ heading[1] }}</p></div>
  <div v-if="loading" class="loading-line">正在整理你的内容…</div>
  <div v-else-if="error" class="empty-state"><h2>暂时无法加载</h2><p>{{ error }}</p><button class="btn primary" @click="load">重新加载</button></div>
  <div v-else-if="!items.length" class="empty-state"><Icon :name="mode === 'saved' ? 'bookmark' : mode === 'orders' ? 'bag' : 'library'" :size="36" /><h2>{{ mode === 'saved' ? '你的片单，还留着很多可能' : mode === 'orders' ? '还没有订单' : '下一段旅程，从发现开始' }}</h2><p>{{ mode === 'owned' ? '有效期内已解锁的专栏会出现在这里。公开内容可以直接从发现页观看。' : '去发现页，找到值得花时间的内容。' }}</p><RouterLink to="/" class="btn primary">发现好内容 <Icon name="arrow" :size="16" /></RouterLink></div>
  <div v-else-if="mode !== 'orders'" class="card-grid library-grid"><SeriesCard v-for="item in items" :key="item.id" :series="item" @changed="load" /></div>
  <template v-else>
    <div class="order-list"><article v-for="order in items" :key="order.id" class="order-card"><div class="order-top"><span>订单 {{ order.id }} · {{ order.createTime }}</span><span class="tag">{{ order.statusDesc || ({1:'待支付',2:'已支付',3:'已关闭',4:'已完成',5:'已报名'}[order.status]) || '处理中' }}</span></div><div v-for="detail in order.details || []" :key="detail.id"><h3>{{ detail.name }}</h3><p class="content-tip">内容编号 {{ detail.courseId }}</p></div><div class="order-bottom"><span>{{ (order.details || []).length }} 项内容</span><strong>实付金额 {{ price(order.realAmount) }}</strong></div></article></div>
  </template>
  <Pagination :page="page" :pages="pages" :total="total" :busy="loading" @change="changePage" />
</template>
