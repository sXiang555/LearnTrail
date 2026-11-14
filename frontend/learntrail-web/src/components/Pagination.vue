<script setup>
defineProps({ page: { type: Number, default: 1 }, pages: { type: Number, default: 0 }, total: { type: Number, default: 0 }, busy: Boolean })
const emit = defineEmits(['change'])
</script>

<template>
  <nav v-if="total > 0" class="pagination" aria-label="列表分页">
    <span class="page-count">共 {{ total }} 项 · 第 {{ page }} / {{ Math.max(1, pages) }} 页</span>
    <div class="page-buttons">
      <button type="button" class="btn light" :disabled="busy || page <= 1" @click="emit('change', page - 1)">上一页</button>
      <button type="button" class="btn light" :disabled="busy || page >= pages" @click="emit('change', page + 1)">下一页</button>
    </div>
  </nav>
</template>

<style scoped>
.pagination { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 20px; flex-wrap: wrap; }
.page-count { color: var(--muted); font-size: 13px; }
.page-buttons { display: flex; gap: 8px; }
</style>
