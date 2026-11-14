<script setup>
import { computed, shallowRef, watch } from 'vue'
const props = defineProps({ user: { type: Object, default: () => ({}) }, size: { type: Number, default: 36 } })
const failed = shallowRef(false)
const url = computed(() => /^https?:\/\//.test(props.user?.icon || '') ? props.user.icon : '')
const initial = computed(() => Array.from(props.user?.name || '我')[0])
watch(url, () => { failed.value = false })
</script>

<template>
  <span class="avatar forest user-avatar" :style="{ width: size + 'px', height: size + 'px' }" aria-hidden="true">
    <img v-if="url && !failed" :src="url" alt="" :width="size" :height="size" referrerpolicy="no-referrer" @error="failed = true" />
    <span v-else>{{ initial }}</span>
  </span>
</template>

<style scoped>
.user-avatar { flex-shrink: 0; overflow: hidden; }
.user-avatar img { width: 100%; height: 100%; object-fit: cover; }
</style>
