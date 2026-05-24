<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useMemoryUserStore } from '@/stores/memoryUser'

// Most memory pages need a user_id to make any backend call. This shell lazily
// loads the store, hides children until the user is chosen, and gives a
// helpful empty-state.

interface Props {
  /** If true, the page works without a user_id (e.g. Users page itself). */
  noUserNeeded?: boolean
  title?: string
}
const props = defineProps<Props>()
const store = useMemoryUserStore()
onMounted(() => store.load())

const ready = computed(() => props.noUserNeeded || !!store.currentId)
</script>

<template>
  <div class="page">
    <div v-if="title" class="page-title">{{ title }}</div>

    <el-empty
      v-if="!ready && !store.loading"
      description="选择一个 memory user 开始（顶部下拉框）"
    />
    <el-skeleton v-else-if="store.loading && !store.loaded" :rows="6" animated />
    <slot v-else :user-id="store.currentId" />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 16px;
  color: var(--eid-text-primary);
}
</style>
