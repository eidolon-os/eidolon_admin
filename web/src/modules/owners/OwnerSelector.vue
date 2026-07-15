<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus, Setting } from '@element-plus/icons-vue'
import { useOwnersStore } from '@/stores/owners'

const store = useOwnersStore()
const route = useRoute()
const router = useRouter()

onMounted(async () => {
  await store.load()
  await syncFromRoute()
})

watch(() => route.query.owner_id, syncFromRoute)

async function syncFromRoute() {
  const ownerId = typeof route.query.owner_id === 'string' ? route.query.owner_id : ''
  if (ownerId && store.owners.some((owner) => owner.owner_id === ownerId)) {
    if (ownerId !== store.currentId) store.setCurrent(ownerId)
    return
  }
  if (store.currentId) {
    await router.replace({
      name: route.name || 'home',
      params: route.params,
      query: { ...route.query, owner_id: store.currentId },
    })
  }
}

async function handleSelect(value: string | number) {
  const ownerId = String(value)
  store.setCurrent(ownerId)
  await router.replace({
    name: route.name || 'home',
    params: route.params,
    query: { ...route.query, owner_id: ownerId },
  })
}

async function openCreate() {
  await router.push({ name: 'spaces', query: { owner_id: store.currentId || undefined, create: '1' } })
}

async function openManage() {
  await router.push({ name: 'spaces', query: { owner_id: store.currentId || undefined } })
}
</script>

<template>
  <div class="owner-selector">
    <el-select
      :model-value="store.currentId"
      size="small"
      filterable
      placeholder="当前 Eidolon 空间"
      :loading="store.loading"
      class="owner-select"
      @change="handleSelect"
    >
      <el-option
        v-for="owner in store.owners"
        :key="owner.owner_id"
        :label="owner.display_name || owner.owner_id"
        :value="owner.owner_id"
      >
        <span>{{ owner.display_name || owner.owner_id }}</span>
        <small>{{ owner.kind }} · {{ owner.owner_id }}</small>
      </el-option>
    </el-select>
    <el-button size="small" :icon="Plus" title="新建 Eidolon 空间" @click="openCreate" />
    <el-button size="small" :icon="Setting" title="管理 Eidolon 空间" @click="openManage" />
  </div>
</template>

<style scoped>
.owner-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}
.owner-select {
  width: 210px;
}
:deep(.el-select-dropdown__item) {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
:deep(.el-select-dropdown__item small) {
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
}
</style>
