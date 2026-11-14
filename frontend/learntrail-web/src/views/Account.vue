<script setup>
import { computed, onMounted, reactive, shallowRef } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, notify, refreshUser, session } from '../api'
import UserAvatar from '../components/UserAvatar.vue'

const props = defineProps({ mode: { type: String, default: 'profile' } })
const profile = reactive({ name: '', icon: '', email: '', intro: '' })
const passwords = reactive({ oldPassword: '', password: '', confirmation: '' })
const loading = shallowRef(true)
const busy = shallowRef('')
const error = shallowRef('')
const passwordError = shallowRef('')
const original = shallowRef('')
const dirty = computed(() => session.token && session.user && props.mode === 'profile' && !loading.value && original.value !== JSON.stringify(profile))
onBeforeRouteLeave(() => !busy.value && ((!dirty.value && !Object.values(passwords).some(Boolean)) || window.confirm('还有未保存的资料或密码输入，确定离开吗？')))

function copy(user) {
  profile.name = user.name || ''
  profile.icon = /^https?:\/\//.test(user.icon || '') ? user.icon : ''
  profile.email = user.email || ''
  profile.intro = user.intro || ''
  original.value = JSON.stringify(profile)
}
onMounted(async () => {
  if (props.mode === 'password') { loading.value = false; return }
  try { copy(await refreshUser()) } catch (e) { error.value = e.message } finally { loading.value = false }
})

async function saveProfile() {
  if (busy.value) return
  busy.value = 'profile'
  error.value = ''
  try {
    await api('/us/users', { method: 'PUT', body: { ...profile, type: session.user.type || 2, oldPassword: '', password: '' } })
    copy(await refreshUser())
    notify('个人资料已更新')
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
async function changePassword() {
  if (busy.value) return
  passwordError.value = ''
  if (passwords.password !== passwords.confirmation) { passwordError.value = '两次输入的新密码不一致'; return }
  busy.value = 'password'
  try {
    await api('/us/users', { method: 'PUT', body: { type: session.user.type || 2, oldPassword: passwords.oldPassword, password: passwords.password } })
    Object.keys(passwords).forEach(key => { passwords[key] = '' })
    notify('密码已修改，下次登录请使用新密码')
  } catch (e) { passwordError.value = e.message } finally { busy.value = '' }
}
</script>

<template>
  <section v-if="!session.token || !session.user" class="content-panel"><h2>请重新登录</h2><p class="content-tip">登录已过期，重新登录后可继续管理账号。</p><RouterLink :to="{ path: '/login', query: { next: '/account/' + mode } }" class="btn primary">重新登录</RouterLink></section>
  <p v-else-if="loading" class="loading-line" role="status">正在载入账号资料…</p>
  <div v-else class="account-layout">
    <section v-if="mode === 'profile'" class="content-panel">
      <h2>个人资料</h2>
      <div class="account-identity"><UserAvatar :user="profile" :size="64" /><div><b>{{ session.user?.username }}</b><p class="content-tip">{{ session.user?.roleName }}</p></div></div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <form @submit.prevent="saveProfile">
        <fieldset class="account-fields" :disabled="!!busy">
          <label class="field">昵称<input v-model.trim="profile.name" name="nickname" autocomplete="nickname" maxlength="50" required /></label>
          <label class="field">头像图片地址<input v-model.trim="profile.icon" name="avatar" type="url" autocomplete="off" maxlength="255" placeholder="https://example.com/avatar.jpg" /></label>
          <p class="content-tip">可填写 HTTP 或 HTTPS 图片地址；留空使用昵称首字。</p>
          <label class="field">邮箱<input v-model.trim="profile.email" name="email" type="email" autocomplete="email" maxlength="255" /></label>
          <label class="field">个人介绍<textarea v-model="profile.intro" name="intro" autocomplete="off" maxlength="255" rows="4" /></label>
          <p class="content-tip">邮箱用于展示资料，当前未提供邮件验证或邮件找回密码。</p>
          <button type="submit" class="btn primary">{{ busy === 'profile' ? '保存中…' : '保存个人资料' }}</button>
        </fieldset>
      </form>
    </section>
    <section v-else class="content-panel">
      <h2>修改密码</h2><p class="content-tip">请先验证当前密码，再设置新的登录密码。</p>
      <p v-if="passwordError" class="form-error" role="alert">{{ passwordError }}</p>
      <form @submit.prevent="changePassword">
        <fieldset class="account-fields" :disabled="!!busy">
          <label class="field">原密码<input v-model="passwords.oldPassword" name="current-password" type="password" autocomplete="current-password" maxlength="100" required /></label>
          <label class="field">新密码<input v-model="passwords.password" name="new-password" type="password" autocomplete="new-password" minlength="8" maxlength="64" required /></label>
          <label class="field">确认新密码<input v-model="passwords.confirmation" name="confirm-password" type="password" autocomplete="new-password" minlength="8" maxlength="64" required /></label>
          <p class="content-tip">建议使用 8–64 位字母、数字和符号组合；中文字符会更早达到长度上限。</p>
          <button type="submit" class="btn primary">{{ busy === 'password' ? '修改中…' : '确认修改密码' }}</button>
        </fieldset>
      </form>
    </section>
  </div>
</template>

<style scoped>
.account-layout { max-width: 720px; }
.account-fields { border: 0; padding: 0; margin-top: 20px; min-width: 0; }
.account-identity { display: flex; align-items: center; gap: 16px; margin: 20px 0; }
@media (max-width: 850px) { .account-layout { grid-template-columns: 1fr; } }
</style>
