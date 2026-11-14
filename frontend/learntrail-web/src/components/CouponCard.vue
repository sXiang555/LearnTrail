<script setup>
import { couponRule } from '../pointsCommerce'
defineProps({ coupon: { type: Object, required: true }, mine: Boolean, busy: Boolean })
defineEmits(['receive', 'details'])
</script>

<template>
  <article class="content-panel coupon-card">
    <span class="tag">{{ mine ? ({ 1: coupon.reserved ? '订单占用中' : '未使用', 2: '已使用', 3: '已过期' })[coupon.status] : '积分优惠券' }}</span>
    <h3>{{ coupon.name }}</h3><p class="coupon-rule">{{ couponRule(coupon) }}</p>
    <p class="content-tip">{{ coupon.specific ? '适用于指定专栏' : '适用于积分视频专栏订单' }} · 每单限用一张</p>
    <p class="content-tip">{{ coupon.termEndTime ? '有效期至 ' + coupon.termEndTime.replace('T', ' ').slice(0, 16) : '领取后 ' + coupon.termDays + ' 天内有效' }}</p>
    <p v-if="!mine" class="content-tip">剩余 {{ Math.max(0, coupon.totalNum - coupon.issueNum) }} 张 · 每人限领 {{ coupon.userLimit }} 张</p>
    <div class="detail-actions"><button class="btn light" type="button" :disabled="busy" @click="$emit('details', coupon)">查看详情</button><button v-if="!mine" class="btn primary" type="button" :disabled="busy || coupon.issueNum >= coupon.totalNum" @click="$emit('receive', coupon)">{{ coupon.exchangePoints ? coupon.exchangePoints + ' 积分兑换' : '免费领取' }}</button></div>
  </article>
</template>

<style scoped>
.coupon-card { min-width: 0; overflow-wrap: anywhere; }
.coupon-card h3 { margin: 16px 0; }
.coupon-rule { color: var(--green); font-size: 20px; line-height: 1.6; font-weight: 600; }
</style>
