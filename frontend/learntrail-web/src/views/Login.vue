<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, login, studioApi } from '../api'
import CoverArt from '../components/CoverArt.vue'
import Icon from '../components/Icon.vue'
const route = useRoute()
const router = useRouter()
const account = ref('')
const password = ref('')
const nickname = ref('')
const confirmation = ref('')
const remember = ref(false)
const busy = ref(false)
const error = ref('')
const viewing = ref(null)
const admin = computed(() => route.query.audience === 'admin')
const registering = computed(() => !admin.value && route.query.mode === 'register')
const nextPath = computed(() => {
  const next = route.query.next
  return typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') ? next : admin.value ? '/operations' : '/library'
})
onMounted(async () => {
  const target = router.resolve(nextPath.value)
  if (target.name !== 'watch' || typeof target.params.slug !== 'string') return

  try {
    const series = await api(studioApi + '/series/' + encodeURIComponent(target.params.slug))
    const episode = series.episodes.find(item => item.id === target.query.section)
    viewing.value = {
      title: series.title,
      episode: episode?.title,
      previewMinutes: series.free !== 1 && episode?.preview ? series.previewMinutes : null
    }
  } catch {
    // Playback context is supplementary. Sign-in remains available if it cannot load.
  }
})
async function submit() {
  if (registering.value && password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  error.value = ''
  let registered = false
  try {
    if (registering.value) {
      await api('/us/students/register/account', {
        method: 'POST',
        body: { username: account.value.trim(), nickname: nickname.value.trim(), password: password.value }
      })
      registered = true
    }
    await login(account.value.trim(), password.value, remember.value, admin.value ? 'admin' : 'student')
    await router.replace(nextPath.value)
  } catch (e) {
    if (registered) {
      await router.replace({ path: '/login', query: { ...route.query, mode: undefined } })
      confirmation.value = ''
      error.value = '账号已注册，请重新登录。' + e.message
    } else {
      error.value = e.message
    }
  } finally { busy.value = false }
}
function clearPassword() {
  password.value = ''
  confirmation.value = ''
  error.value = ''
}
</script>

<template>
  <section class="login-layout">
    <div class="login-art"><p class="eyebrow">GOOD CONTENT. REAL CONNECTIONS.</p><h2>你的好奇心，<br>值得一个<br>认真对待的地方。</h2><p>从一次观看，走向一段属于自己的成长。</p><CoverArt tone="forest" hero /></div>
    <form class="login-form" @submit.prevent="submit">
      <p class="eyebrow">WELCOME TO YOUR NEXT CHAPTER</p>
      <h1>{{ admin ? 'LearnTrail 运营登录' : registering ? '创建 LearnTrail 账号' : viewing ? '登录后继续观看' : '欢迎回到 LearnTrail' }}</h1>
      <div v-if="viewing" class="login-viewing" aria-live="polite">
        <strong>{{ viewing.title }}</strong>
        <p v-if="viewing.episode">{{ viewing.episode }}</p>
        <p v-if="viewing.previewMinutes">登录后可试看最多 {{ viewing.previewMinutes }} 分钟。</p>
      </div>
      <p v-else>{{ admin ? '使用已有员工账号管理平台内容与用户。' : registering ? '关注你喜欢的创作者，或发布自己的视频专栏。' : '登录，继续你的探索。' }}</p>
      <div v-if="error" role="alert" class="form-error">{{ error }}</div>
      <label class="field">
        {{ registering ? '用户名' : '用户名 / 手机号' }}
        <input v-model="account" name="username" autocomplete="username" spellcheck="false"
          :pattern="registering ? '[a-zA-Z][a-zA-Z0-9_]{3,31}' : undefined"
          :placeholder="registering ? '4–32 个字符，以字母开头' : '输入用户名或手机号'"
          :maxlength="registering ? 32 : 80" required />
      </label>
      <label v-if="registering" class="field">
        昵称
        <input v-model.trim="nickname" name="nickname" autocomplete="nickname" maxlength="50" required />
      </label>
      <label class="field">
        密码
        <input v-model="password" name="password" type="password"
          :autocomplete="registering ? 'new-password' : 'current-password'"
          :placeholder="registering ? '至少 8 个字符' : '输入你的密码'"
          :minlength="registering ? 8 : undefined" :maxlength="registering ? 64 : 100" required />
      </label>
      <label v-if="registering" class="field">
        确认密码
        <input v-model="confirmation" name="passwordConfirmation" type="password"
          autocomplete="new-password" maxlength="64" required />
      </label>
      <label v-else class="form-check"><input v-model="remember" type="checkbox" />7 天内记住我</label>
      <button class="btn primary wide" :disabled="busy" type="submit">
        {{ registering ? (busy ? '正在注册…' : '注册并登录') : (busy ? '正在登录…' : '登录') }}
        <Icon name="arrow" :size="17" aria-hidden="true" />
      </button>
      <p v-if="!busy && !admin" class="form-help">
        {{ registering ? '已有账号？' : '还没有账号？' }}
        <RouterLink :to="{ path: '/login', query: { ...route.query, mode: registering ? undefined : 'register' } }"
          class="text-link" @click="clearPassword">{{ registering ? '去登录' : '注册账号' }}</RouterLink>
      </p>
      <RouterLink v-if="!busy" :to="{ path: '/login', query: admin ? {} : { audience: 'admin', next: '/operations' } }" class="text-link" @click="clearPassword">{{ admin ? '返回读者登录' : '运营人员登录' }}</RouterLink>
      <RouterLink to="/" class="text-link"><Icon name="back" :size="14" />先去发现好内容</RouterLink>
    </form>
  </section>
</template>
