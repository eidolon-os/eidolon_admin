<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FleetGrouping from './FleetGrouping.vue'
import OwnerDeviceBindings from './OwnerDeviceBindings.vue'
import { useOwnersStore } from '@/stores/owners'

const route = useRoute()
const router = useRouter()
const ownersStore = useOwnersStore()

const section = computed<'overview' | 'connect'>({
  get: () => route.params.section === 'connect' ? 'connect' : 'overview',
  set: (value) => router.replace({
    name: 'devices',
    params: { section: value },
    query: { ...route.query, owner_id: ownersStore.currentId || undefined },
  }),
})
</script>

<template>
  <section class="device-center">
    <header class="page-head">
      <div>
        <p>DEVICES &amp; BODIES</p>
        <h1>设备与身体</h1>
        <span>查看当前 Eidolon 拥有的身体，或按步骤接入并绑定物理设备。</span>
      </div>
      <el-tag v-if="ownersStore.currentId" size="small" type="info" effect="plain">Owner scoped</el-tag>
    </header>

    <el-tabs v-model="section" class="dc-tabs">
      <el-tab-pane label="我的设备" name="overview">
        <FleetGrouping v-if="section === 'overview' && ownersStore.currentId" :owner-id="ownersStore.currentId" />
        <el-empty v-else-if="section === 'overview'" description="请先选择一个 Eidolon 空间" />
      </el-tab-pane>
      <el-tab-pane label="接入设备" name="connect">
        <OwnerDeviceBindings v-if="section === 'connect' && ownersStore.currentId" :owner-id="ownersStore.currentId" />
        <el-empty v-else-if="section === 'connect'" description="请先选择一个 Eidolon 空间" />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.device-center { display: flex; width: min(1240px, 100%); min-height: 100%; margin: 0 auto; padding-bottom: 32px; flex-direction: column; gap: 14px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 16px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.page-head p { margin: 0; color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: .12em; }
.page-head h1 { margin: 5px 0; color: var(--eid-text-primary); font-size: 24px; }
.page-head span { color: var(--eid-text-secondary); font-size: 12px; }
.dc-tabs { flex: 1 1 auto; }
:deep(.dc-tabs > .el-tabs__content) { padding-top: 4px; }
</style>
