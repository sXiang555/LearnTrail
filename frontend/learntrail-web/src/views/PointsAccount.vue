<script setup>
import { onMounted, ref, shallowRef } from 'vue'
import { api, notify, refreshWallet, session } from '../api'
import PointHistory from '../components/PointHistory.vue'

const rules = ref([])
const loading = shallowRef(true)
const error = shallowRef('')
const busy = shallowRef(false)
const signed = shallowRef(false)
const revision = shallowRef(0)
const labels = { READ: '阅读文章', WATCH: '观看视频', SIGN: '每日签到', COMMENT: '有效评论' }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [, policy, signs] = await Promise.all([refreshWallet(), api('/ls/studio-activities/rules'), api('/ls/sign-records')])
    rules.value = policy; signed.value = signs?.at(-1) === 1
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}
async function sign() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const result = await api('/ls/studio-activities/sign', { method: 'POST' })
    session.balance = result.balance; signed.value = true; revision.value++
    notify(result.awarded ? '签到成功 +' + result.awarded + ' 积分' : '今日已签到，没有重复加分')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <p v-if="loading" role="status">正在载入积分账户…</p>
  <p v-if="error" class="form-error" role="alert">{{ error }} <button class="text-link" @click="load">重新加载</button></p>
  <section v-if="!loading" class="content-panel points-summary">
    <div><h2>积分账户</h2><p class="balance"><strong>{{ session.balance ?? '—' }}</strong> 可用积分</p><p class="content-tip">积分用于站内内容解锁与优惠券兑换。</p></div>
    <button class="btn primary" :disabled="busy || signed" @click="sign">{{ busy ? '签到中…' : signed ? '今日已签到' : '每日签到' }}</button>
  </section>
  <section v-if="rules.length" class="content-panel section-gap"><h2>如何获取积分</h2><ul class="reward-rules"><li v-for="rule in rules" :key="rule.kind"><b>{{ labels[rule.kind] }}</b><span>每次 +{{ rule.amount }} · 每日最多 {{ rule.dailyCap }} 分</span><small v-if="rule.kind === 'READ'">有效阅读至少 {{ rule.minSeconds }} 秒并读至末段，每篇仅奖励一次。</small><small v-else-if="rule.kind === 'WATCH'">有效连续观看达到 {{ rule.minSeconds }} 秒；短视频按时长比例计算，每集仅奖励一次。</small><small v-else-if="rule.kind === 'COMMENT'">有实际内容的评论参与奖励；短评、重复正文不发分。</small><small v-else>每日仅奖励一次，连续签到记录沿用原签到服务。</small></li></ul><div class="detail-actions"><RouterLink to="/articles" class="btn light">阅读文章</RouterLink><RouterLink to="/?kind=video" class="btn light">观看视频</RouterLink></div></section>
  <PointHistory v-if="!loading" :revision="revision" class="section-gap" />
</template>

<style scoped>
.points-summary { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.balance { margin: 18px 0; color: var(--muted); }
.balance strong { font-size: 38px; color: var(--green); margin-right: 10px; }
.reward-rules { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); list-style: none; gap: 16px; padding: 0; margin: 24px 0; }
.reward-rules li { display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--line); padding: 18px; border-radius: 10px; }
.reward-rules span, .reward-rules small { color: var(--muted); line-height: 1.7; }
@media (max-width: 600px) { .reward-rules { grid-template-columns: 1fr; } .points-summary { align-items: start; flex-direction: column; } }
</style>
