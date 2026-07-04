<script setup lang="ts">
// First-paint skeleton for list/table pages. Use on initial load (data empty);
// keep v-loading for subsequent refreshes so the layout doesn't flash.
withDefaults(defineProps<{ rows?: number }>(), { rows: 6 })
</script>

<template>
  <div class="table-skeleton">
    <el-skeleton animated :rows="rows" />
  </div>
</template>

<style scoped>
.table-skeleton { padding: 8px 4px; }
</style>
