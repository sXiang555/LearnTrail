<script setup>
import { computed, nextTick, onMounted, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, notify, session } from '../api'
import { couponRule, newOrderKey } from '../pointsCommerce'
import CouponCard from '../components/CouponCard.vue'
import PointCouponForm from '../components/PointCouponForm.vue'
import Pagination from '../components/Pagination.vue'

const route = useRoute()
const router = useRouter()
const mine = computed(() => route.query.tab !== 'catalog')
const page = computed(() => Math.max(1, Math.min(100000, Number(route.query.page) || 1)))
const status = computed(() => ['1', '2', '3'].includes(route.query.status) ? route.query.status : '')
const result = ref({ list: [], pages: 0, total: 0 })
const details = ref(null)
const detailHeading = ref(null)
const loading = shallowRef(true)
const busy = shallowRef(false)
const error = shallowRef('')
const issuerOpen = shallowRef(false)
const requests = new Map()
function changePage(value) { router.push({ query: { ...route.query, page: value > 1 ? value : undefined } }) }
function filter(value) { router.push({ query: { ...route.query, status: value || undefined, page: undefined } }) }
async function load() {
  loading.value = true; error.value = ''
  try { result.value = await api('/prs/point-coupons/' + (mine.value ? 'mine' : 'catalog') + '?' + new URLSearchParams({ pageNo: page.value, pageSize: 10, ...(mine.value && status.value ? { status: status.value } : {}) })) }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
async function show(item) {
  busy.value = true; error.value = ''
  try { details.value = { ...await api('/prs/point-coupons/templates/' + item.templateId), ...(mine.value ? item : {}) }; await nextTick(); detailHeading.value?.focus(); detailHeading.value?.scrollIntoView({ block: 'nearest' }) }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function receive(coupon) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    if (!requests.has(coupon.id)) requests.set(coupon.id, newOrderKey())
    if (coupon.exchangePoints > 0) {
      const order = await api('/ts/point-orders', { method: 'POST', body: { requestKey: requests.get(coupon.id), kind: 'COUPON', targetId: coupon.id } })
      await router.push('/account/orders/' + order.orderId)
    } else {
      await api('/prs/point-coupons/templates/' + coupon.id + '/receive', { method: 'POST', headers: { 'Idempotency-Key': requests.get(coupon.id) } })
      requests.delete(coupon.id)
      notify('领取成功，可在我的优惠券中查看'); await load()
    }
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <div class="section-title"><h2>{{ mine ? '我的优惠券' : '领券与兑换' }}</h2><label v-if="mine" class="sort-control">状态<select :value="status" @change="filter($event.target.value)"><option value="">全部</option><option value="1">未使用</option><option value="2">已使用</option><option value="3">已过期</option></select></label></div>
  <nav class="coupon-tabs" aria-label="优惠券分类"><RouterLink to="/account/coupons" :aria-current="mine ? 'page' : undefined">我的优惠券</RouterLink><RouterLink to="/account/coupons?tab=catalog" :aria-current="!mine ? 'page' : undefined">领券与兑换</RouterLink></nav>
  <p v-if="error" class="form-error" role="alert">{{ error }} <button class="text-link" @click="load">重新加载</button></p>
  <section v-if="details" class="content-panel section-gap"><div class="section-title"><h2 ref="detailHeading" tabindex="-1">{{ details.name }}</h2><button class="btn light" @click="details = null">收起详情</button></div><p>{{ couponRule(details) }}</p><p class="content-tip">{{ details.specific ? '限指定专栏使用，结算时核对适用范围。' : '适用于积分视频专栏订单。' }}每笔订单只能使用一张，不能叠加；不适用于优惠券兑换。</p><p class="content-tip">{{ details.termEndTime ? '有效期至 ' + details.termEndTime.replace('T', ' ') : '领取后 ' + details.termDays + ' 天有效' }} · {{ details.exchangePoints ? '兑换价格 ' + details.exchangePoints + ' 积分' : '免费领取' }}</p></section>
  <p v-if="loading" role="status" class="loading-line">正在载入优惠券…</p>
  <div v-else-if="result.list.length" class="coupon-grid section-gap"><CouponCard v-for="item in result.list" :key="item.id" :coupon="item" :mine="mine" :busy="busy" @receive="receive" @details="show" /></div>
  <section v-else class="empty-state"><h3>{{ mine ? '还没有优惠券' : '暂无可领取的优惠券' }}</h3><RouterLink v-if="mine" to="/account/coupons?tab=catalog" class="btn primary">看看可领取的券</RouterLink></section>
  <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="loading || busy" @change="changePage" />
  <template v-if="Number(session.user?.type) === 1"><button class="btn light section-gap" :aria-expanded="issuerOpen" @click="issuerOpen = !issuerOpen">{{ issuerOpen ? '收起运营设置' : '运营：发布积分券' }}</button><PointCouponForm v-if="issuerOpen" @created="load" /></template>
</template>

<style scoped>
.coupon-tabs { display: flex; gap: 12px; margin-bottom: 20px; }
.coupon-tabs a { padding: 9px 14px; border-radius: 8px; border: 1px solid var(--line); background: white; }
.coupon-tabs a[aria-current] { background: var(--green); color: white; }
.coupon-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
@media (max-width: 650px) { .coupon-grid { grid-template-columns: 1fr; } }
</style>
