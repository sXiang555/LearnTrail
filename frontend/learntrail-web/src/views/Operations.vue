<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import ArticleManager from '../components/ArticleManager.vue'
import OperationsSeries from '../components/OperationsSeries.vue'
import OperationsUsers from '../components/OperationsUsers.vue'
const access = ref(false)
const loading = ref(true)
const error = ref('')
const tab = ref('series')
onMounted(async () => {
  try {
    const result = await api('/ls/studio-articles/access')
    if (!result.operator) throw new Error('当前账号没有运营权限')
    access.value = true
  } catch (e) { error.value = e.message } finally { loading.value = false }
})
</script>

<template>
  <div class="library-heading"><p class="eyebrow">PLATFORM OPERATIONS</p><h1>运营后台</h1><p>管理平台内容与账号状态，保留真实业务记录。</p></div>
  <p v-if="loading" class="loading-line" role="status">正在核验运营身份…</p>
  <section v-else-if="error" class="content-panel"><h2>无法打开运营后台</h2><p class="form-error" role="alert">{{ error }}</p><RouterLink to="/" class="btn light">返回发现页</RouterLink></section>
  <template v-else-if="access">
    <div class="topic-tabs" role="group" aria-label="运营内容类型"><button v-for="(label, key) in { series: '专栏管理', articles: '文章管理', users: '用户管理' }" :key="key" :class="{ active: tab === key }" :aria-pressed="tab === key" @click="tab = key">{{ label }}</button></div>
    <OperationsSeries v-show="tab === 'series'" />
    <ArticleManager v-show="tab === 'articles'" scope="admin" />
    <OperationsUsers v-show="tab === 'users'" />
  </template>
</template>
