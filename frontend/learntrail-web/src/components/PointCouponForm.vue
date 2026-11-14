<script setup>
import { reactive, shallowRef } from 'vue'
import { api, notify } from '../api'
const emit = defineEmits(['created'])
const form = reactive({ name: '', discountType: 3, discountValue: 20, thresholdPoints: 0, maxDiscountPoints: 0, exchangePoints: 0, totalNum: 100, userLimit: 1, termDays: 30 })
const busy = shallowRef(false)
const error = shallowRef('')
async function save() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await api('/prs/point-coupons', { method: 'POST', body: { ...form } }); notify('积分优惠券已发布'); emit('created') }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <section class="content-panel section-gap"><h2>发布积分优惠券</h2><p class="content-tip">运营设置。0 积分可免费领取，正数须通过积分订单兑换。适用于积分视频专栏，每单一张。</p>
    <form @submit.prevent="save"><fieldset :disabled="busy"><label class="field">优惠券名称<input v-model.trim="form.name" required maxlength="50" /></label><div class="field-grid">
      <label class="field">抵扣类型<select v-model.number="form.discountType"><option :value="3">固定积分抵扣券</option><option :value="4">满积分使用券</option><option :value="2">比例折扣券</option></select></label>
      <label class="field">{{ form.discountType === 2 ? '支付比例（80 表示 8 折）' : '抵扣积分' }}<input v-model.number="form.discountValue" type="number" min="1" :max="form.discountType === 2 ? 99 : 100000" required /></label>
      <label class="field">使用门槛（积分）<input v-model.number="form.thresholdPoints" :disabled="form.discountType === 3" type="number" min="0" max="100000" required /></label>
      <label class="field">最多抵扣积分（0 不限）<input v-model.number="form.maxDiscountPoints" :disabled="form.discountType !== 2" type="number" min="0" max="100000" required /></label>
      <label class="field">兑换所需积分（0 为免费）<input v-model.number="form.exchangePoints" type="number" min="0" max="100000" required /></label>
      <label class="field">总库存<input v-model.number="form.totalNum" type="number" min="1" max="5000" required /></label>
      <label class="field">每人限领<input v-model.number="form.userLimit" type="number" min="1" max="10" required /></label>
      <label class="field">领取后有效天数<input v-model.number="form.termDays" type="number" min="1" max="365" required /></label>
    </div><p v-if="error" role="alert" class="form-error">{{ error }}</p><button class="btn primary" type="submit">{{ busy ? '发布中…' : '确认发布优惠券' }}</button></fieldset></form>
  </section>
</template>

<style scoped>
fieldset { border: 0; padding: 0; min-width: 0; margin-top: 20px; }
</style>
