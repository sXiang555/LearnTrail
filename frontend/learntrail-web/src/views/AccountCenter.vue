<script setup>
const sections = [
  ['profile', '个人资料'], ['learning', '已解锁内容'],
  ['password', '修改密码'], ['points', '积分记录'], ['orders', '我的订单'], ['coupons', '我的优惠券']
]
</script>

<template>
  <div class="library-heading"><p class="eyebrow">YOUR ACCOUNT</p><h1>个人中心</h1><p>管理账号资料，查看学习权益、积分和订单。</p></div>
  <nav class="account-tabs" aria-label="个人中心导航"><RouterLink v-for="[path, label] in sections" :key="path" :to="'/account/' + path">{{ label }}</RouterLink></nav>
  <RouterView />
</template>

<style scoped>
.account-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 28px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.account-tabs a { padding: 10px 16px; border-radius: 8px; background: white; border: 1px solid var(--line); }
.account-tabs .router-link-active { color: white; background: var(--green); border-color: var(--green); }
.account-tabs a:focus-visible { outline: 2px solid var(--green); outline-offset: 3px; }
</style>
