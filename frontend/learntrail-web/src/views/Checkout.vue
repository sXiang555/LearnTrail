<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, studioApi, refreshWallet, session, duration } from '../api'
import CoverArt from '../components/CoverArt.vue'
import Icon from '../components/Icon.vue'
import PointCouponSelect from '../components/PointCouponSelect.vue'
import { newOrderKey } from '../pointsCommerce'
const route = useRoute()
const router = useRouter()
const coupon = ref({ userCouponId: null, discountPoints: 0 })
let requestKey = newOrderKey()
function selectCoupon(value) { coupon.value = value; requestKey = newOrderKey() }
const payable = computed(() => Math.max(0, (series.value?.unlockPoints || 0) - (coupon.value.discountPoints || 0)))
const series = ref(null)
const error = ref('')
const unlockError = ref('')
const busy = ref(false)
const loaded = ref(false)
const accessible = computed(() => series.value?.owned || series.value?.creatorOwned || (series.value?.free === 1 && series.value?.available))
const enough = computed(() => session.balance !== null && session.balance >= payable.value)
onMounted(async () => {
  try {
    const [current] = await Promise.all([api(studioApi + '/series/' + route.params.slug), refreshWallet()])
    series.value = current
  } catch (e) { error.value = e.message } finally { loaded.value = true }
})
async function unlock() {
  if (busy.value || accessible.value) return
  busy.value = true
  unlockError.value = ''
  try {
    const order = await api('/ts/point-orders', { method: 'POST', body: {
      requestKey, kind: 'COURSE', targetId: series.value.courseId, userCouponId: coupon.value.userCouponId
    } })
    await router.push('/account/orders/' + order.orderId)
  } catch (e) { unlockError.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <div v-if="error" class="empty-state"><h2>暂时无法查看订阅信息</h2><p>{{ error }}</p><RouterLink to="/" class="btn primary">返回发现页</RouterLink></div>
  <div v-else-if="!loaded" class="loading-line">正在核对专栏与积分…</div>
  <template v-else>
    <div class="breadcrumbs"><RouterLink :to="'/series/' + series.slug">专栏详情</RouterLink><Icon name="chevron" :size="12" /><span>解锁专栏</span></div>
    <div class="library-heading"><p class="eyebrow">MAKE TIME FOR GOOD CONTENT</p><h1>为值得的内容，留一段时间</h1><p>解锁前，确认这是你想开启的下一段旅程。</p></div>
    <div class="checkout-layout">
      <div>
        <section class="checkout-series"><CoverArt :tone="series.coverTone" /><div><span class="tag">{{ series.topic }}</span><h2 class="section-gap">{{ series.title }}</h2><p>{{ series.creatorName }} · {{ series.episodeCount }} 集 · {{ duration(series.duration) }}</p><p>{{ series.subtitle }}</p></div></section>
        <section class="payment-state"><h3>用阅读积累，开启下一段旅程</h3><p>使用积分即可解锁完整专栏。解锁后可在「个人中心 → 已解锁内容」继续观看；同一专栏重复操作不会再次扣分。</p><RouterLink to="/articles" class="text-link">阅读文章，赚取积分 <Icon name="arrow" :size="15" /></RouterLink></section>
        <PointCouponSelect v-if="!accessible" :model-value="coupon" :course-id="series.courseId" :points="series.unlockPoints" :disabled="busy" @update:model-value="selectCoupon" />
      </div>
      <aside class="content-panel">
        <h3>{{ series.owned ? '这个专栏已经属于你' : series.free === 1 ? '向每一份好奇心开放' : '积分解锁完整系列' }}</h3>
        <p class="content-tip">{{ accessible ? '可进入观看完整内容。' : '确认订单后进入详情，使用积分完成支付。' }}</p>
        <div class="checkout-total"><span>所需积分</span><strong>{{ series.free === 1 ? '免费' : series.unlockPoints + ' 分' }}</strong></div>
        <p v-if="!accessible" class="content-tip">优惠抵扣 {{ coupon.discountPoints ?? '校验中' }} 分 · 应付 {{ payable }} 分</p>
        <p class="content-tip">当前余额：{{ session.balance }} 分<span v-if="!accessible && enough"> · 解锁后剩余 {{ session.balance - payable }} 分</span></p>
        <p v-if="unlockError" class="form-error" role="alert">{{ unlockError }}</p>
        <RouterLink v-if="accessible" :to="'/watch/' + series.slug" class="btn primary wide"><Icon name="play" :size="16" />进入观看</RouterLink>
        <button v-else-if="enough" type="button" class="btn primary wide" :disabled="busy || coupon.discountPoints === null" @click="unlock">{{ busy ? '创建订单中…' : '确认订单，前往积分支付' }}</button>
        <template v-else><p class="form-error">积分不足，还差 {{ Math.max(0, payable - session.balance) }} 分。</p><RouterLink to="/account/points" class="btn primary wide">去获取积分</RouterLink></template>
        <RouterLink :to="'/series/' + series.slug" class="btn light wide section-gap">返回详情与试看</RouterLink>
      </aside>
    </div>
  </template>
</template>
