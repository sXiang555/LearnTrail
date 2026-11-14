<script setup>
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, notify, refreshWallet, session } from '../api'
import { orderPhases } from '../pointsCommerce'
import Pagination from '../components/Pagination.vue'

const route = useRoute()
const router = useRouter()
const result = ref({ list: [], pages: 0, total: 0 })
const detail = ref(null)
const loading = shallowRef(true)
const busy = shallowRef(false)
const error = shallowRef('')
const page = computed(() => Math.min(100000, Math.max(1, Number(route.query.page) || 1)))
const phase = computed(() => Object.hasOwn(orderPhases, route.query.phase) ? route.query.phase : '')
async function load() {
  loading.value = true; error.value = ''
  try {
    const [data] = await Promise.all([api('/ts/point-orders' + (route.params.id ? '/' + route.params.id : '?' + new URLSearchParams({ pageNo: page.value, pageSize: 10, ...(phase.value ? { phase: phase.value } : {}) }))), refreshWallet()])
    if (route.params.id) detail.value = data; else result.value = data
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}
async function act(action) {
  if (busy.value || !detail.value) return
  busy.value = true; error.value = ''
  try {
    detail.value = await api('/ts/point-orders/' + detail.value.orderId + '/' + action, { method: 'POST' })
    await refreshWallet()
    notify(detail.value.phase === 'PAID' ? '积分支付已完成，权益已生效' : '订单已取消，没有重复扣分')
  } catch (e) { error.value = e.message; await refreshWallet().catch(() => {}) }
  finally { busy.value = false }
}
function filter(value) { router.push({ query: { phase: value || undefined } }) }
function changePage(value) { router.push({ query: { ...route.query, page: value > 1 ? value : undefined } }) }
onMounted(load)
</script>

<template>
  <div class="section-title"><h2>{{ route.params.id ? '订单详情' : '我的订单' }}</h2><label v-if="!route.params.id" class="sort-control">状态<select :value="phase" @change="filter($event.target.value)"><option value="">全部</option><option v-for="(label, value) in orderPhases" :key="value" :value="value">{{ label }}</option></select></label><RouterLink v-else to="/account/orders" class="text-link">← 返回我的订单</RouterLink></div>
  <p v-if="error" class="form-error" role="alert">{{ error }} <button class="text-link" :disabled="busy" @click="load">重新核对</button></p>
  <p v-if="loading" class="loading-line" role="status">正在载入订单…</p>
  <section v-else-if="detail" class="content-panel order-detail">
    <div class="order-top"><span>订单 {{ detail.orderId }}</span><span class="tag">{{ orderPhases[detail.phase] }}</span></div>
    <h3>{{ detail.title }}</h3><p class="content-tip">{{ detail.kind === 'COURSE' ? '视频专栏' : '优惠券兑换' }} · {{ detail.createdAt?.replace('T', ' ') }}</p>
    <dl class="order-prices"><div><dt>所需积分</dt><dd>{{ detail.totalPoints }} 分</dd></div><div><dt>优惠抵扣</dt><dd>−{{ detail.discountPoints }} 分</dd></div><div><dt>{{ detail.phase === 'PAID' ? '实际支付' : '应付积分' }}</dt><dd><strong>{{ detail.payablePoints }} 分</strong></dd></div><div><dt>当前余额</dt><dd>{{ session.balance ?? '—' }} 分</dd></div></dl>
    <p v-if="detail.phase === 'PAYING'" class="content-tip">支付处理中会继续核对实际结算结果；若暂不兑换，可申请取消。已完成扣分的订单会恢复为已支付。</p>
    <p v-if="['CREATED','PAYING'].includes(detail.phase) && session.balance < detail.payablePoints" class="form-error">积分不足，还差 {{ detail.payablePoints - session.balance }} 分。<RouterLink to="/account/points" class="text-link">去获取积分</RouterLink></p>
    <div class="detail-actions">
      <button v-if="['CREATED','PAYING'].includes(detail.phase)" class="btn primary" :disabled="busy || (detail.phase === 'CREATED' && (session.balance === null || session.balance < detail.payablePoints))" @click="act('pay')">{{ busy ? '处理中…' : detail.phase === 'PAYING' ? '继续核对支付' : '确认支付 ' + detail.payablePoints + ' 积分' }}</button>
      <button v-if="!['PAID','CANCELLED'].includes(detail.phase)" class="btn light" :disabled="busy" @click="act('cancel')">{{ detail.phase === 'CANCELLING' ? '继续取消' : '取消订单' }}</button>
      <RouterLink v-if="detail.phase === 'PAID'" :to="detail.kind === 'COURSE' ? '/account/learning' : '/account/coupons'" class="btn primary">{{ detail.kind === 'COURSE' ? '查看已解锁内容' : '查看我的优惠券' }}</RouterLink>
    </div>
  </section>
  <template v-else-if="!route.params.id"><div v-if="result.list.length" class="order-list"><article v-for="order in result.list" :key="order.orderId" class="order-card"><div class="order-top"><span>{{ order.createdAt?.replace('T', ' ') }}</span><span class="tag">{{ orderPhases[order.phase] }}</span></div><h3>{{ order.title }}</h3><div class="order-bottom"><strong>{{ order.payablePoints }} 积分</strong><RouterLink :to="'/account/orders/' + order.orderId" class="btn light">查看订单</RouterLink></div></article></div><section v-else class="empty-state"><h3>还没有积分订单</h3><RouterLink to="/" class="btn primary">发现好内容</RouterLink></section><Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading" @change="changePage" /></template>
</template>

<style scoped>
.order-detail { max-width: 850px; }
.order-detail h3 { margin: 28px 0 12px; font-size: 24px; overflow-wrap: anywhere; }
.order-prices { margin: 26px 0; }
.order-prices div { display: flex; justify-content: space-between; gap: 20px; padding: 12px 0; border-bottom: 1px solid var(--line); }
.order-prices dd { margin: 0; }
.order-prices strong { font-size: 22px; color: var(--green); }
</style>
