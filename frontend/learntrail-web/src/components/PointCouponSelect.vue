<script setup>
import { onMounted, ref, shallowRef } from 'vue'
import { api } from '../api'
import { couponRule } from '../pointsCommerce'
import Pagination from './Pagination.vue'

const props = defineProps({ courseId: { type: [String, Number], required: true }, points: { type: Number, required: true }, modelValue: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const page = shallowRef(1)
const result = ref({ list: [], total: 0, pages: 0 })
const busy = shallowRef(false)
const error = shallowRef('')
async function load() {
  busy.value = true; error.value = ''
  try { result.value = await api('/prs/point-coupons/mine?' + new URLSearchParams({ pageNo: page.value, pageSize: 5, status: 1 })) }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function choose(item = null) {
  if (busy.value || props.disabled) return
  error.value = ''
  if (!item) { emit('update:modelValue', { userCouponId: null, discountPoints: 0 }); return }
  emit('update:modelValue', { userCouponId: item.id, discountPoints: null, name: item.name })
  busy.value = true
  try {
    const discountPoints = await api('/prs/point-coupons/quote', { method: 'POST', body: { userCouponId: item.id, courseId: props.courseId, points: props.points } })
    emit('update:modelValue', { userCouponId: item.id, discountPoints, name: item.name })
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function changePage(value) { page.value = value; load() }
onMounted(load)
</script>

<template>
  <section class="content-panel section-gap"><h3>使用优惠券</h3><p class="content-tip">每单限用一张，由服务端校验有效期、门槛和适用专栏。</p>
    <button type="button" class="coupon-option" :class="{ chosen: !modelValue.userCouponId }" :aria-pressed="!modelValue.userCouponId" :disabled="busy || disabled" @click="choose()">不使用优惠券</button>
    <p v-if="modelValue.userCouponId" class="content-tip">已选：{{ modelValue.name }} · {{ modelValue.discountPoints === null ? '等待校验' : '抵扣 ' + modelValue.discountPoints + ' 积分' }}</p>
    <p v-if="error" class="form-error" role="alert">{{ error }} <button class="text-link" :disabled="busy" @click="load">重新加载</button></p>
    <p v-if="busy" role="status">正在核对优惠券…</p>
    <button v-for="coupon in result.list" :key="coupon.id" type="button" class="coupon-option" :class="{ chosen: modelValue.userCouponId === coupon.id }" :aria-pressed="modelValue.userCouponId === coupon.id" :disabled="busy || disabled || coupon.reserved" @click="choose(coupon)"><b>{{ coupon.name }}</b><span>{{ couponRule(coupon) }}{{ coupon.reserved ? ' · 其他订单占用中' : '' }}</span></button>
    <p v-if="!busy && !result.list.length" class="content-tip">暂无未使用的积分券。</p>
    <Pagination :page="page" :pages="result.pages" :total="result.total" :busy="busy || disabled" @change="changePage" />
    <RouterLink to="/account/coupons?tab=catalog" class="text-link">领券与兑换 →</RouterLink>
  </section>
</template>

<style scoped>
.coupon-option { display: flex; flex-direction: column; gap: 8px; text-align: left; width: 100%; margin: 12px 0; padding: 15px; border-radius: 8px; background: white; border: 1px solid var(--line); }
.coupon-option.chosen { border-color: var(--green); box-shadow: inset 0 0 0 1px var(--green); }
.coupon-option span { color: var(--muted); font-size: 13px; line-height: 1.6; }
.coupon-option:disabled { opacity: .6; cursor: not-allowed; }
</style>
