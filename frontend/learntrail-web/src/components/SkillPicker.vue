<script setup>
import { onMounted, onScopeDispose, ref } from 'vue'
import { getAiSkills } from '../aiApi.js'
defineProps({ modelValue: { type: String, default: 'auto' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const items = ref([]), servers = ref([]), error = ref('')
const controller = new AbortController()
onScopeDispose(() => controller.abort())
onMounted(async () => {
  try {
    const result = await getAiSkills(controller.signal)
    items.value = result.items; servers.value = result.mcp_servers
  } catch (cause) { if (!controller.signal.aborted) error.value = cause.message }
})
</script>

<template>
  <div class="skill-picker">
    <label for="agent-skill">任务技能</label>
    <select id="agent-skill" :value="modelValue" :disabled="disabled" @change="emit('update:modelValue', $event.target.value)">
      <option value="auto">自动选择</option>
      <option v-for="item in items" :key="item.name" :value="item.name">{{ item.description }}</option>
    </select>
    <small v-if="servers.length">已授权 MCP：{{ servers.map(server => server.id + ' (' + server.transport + ')').join('、') }}</small>
    <small v-else>未配置外部 MCP，使用站内工具</small>
    <p v-if="error" role="status">{{ error }}</p>
  </div>
</template>

<style scoped>
.skill-picker { margin-bottom: 12px; display: grid; gap: 6px; font-size: 13px; }
select { width: 100%; max-width: 560px; border: 1px solid var(--line); border-radius: 8px; padding: 8px; background: white; color: inherit; }
small { color: var(--secondary-text); }
</style>
