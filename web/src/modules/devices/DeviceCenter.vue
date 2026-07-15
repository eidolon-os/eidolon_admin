<script setup lang="ts">
// Single Device Center — owner body inventory, Hub hardware lifecycle, and
// ESP32 flashing behind one entry.
// Tab is reflected in the route (:tab?) so it is shareable and back/forward-able.
// Deeper owner→companion fleet grouping (server-side join) is a follow-up.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Fleet from '@/modules/hub/Devices.vue'
import Firmware from '@/modules/tools/Esp32.vue'
import GuardPanel from '@/modules/owners/GuardPanel.vue'
import FleetGrouping from './FleetGrouping.vue'
import { useOwnersStore } from '@/stores/owners'

const route = useRoute()
const router = useRouter()
const ownersStore = useOwnersStore()

const tab = computed<'fleet' | 'guard' | 'firmware'>({
  get: () => {
    if (route.params.tab === 'guard') return 'guard'
    if (route.params.tab === 'firmware') return 'firmware'
    return 'fleet'
  },
  set: (v) => router.replace({ name: 'devices', params: { tab: v }, query: route.query }),
})
</script>

<template>
  <div class="device-center">
    <el-tabs v-model="tab" class="dc-tabs">
      <el-tab-pane label="设备与身体" name="fleet">
        <template v-if="tab === 'fleet'">
          <FleetGrouping :owner-id="ownersStore.currentId" />
          <Fleet />
        </template>
      </el-tab-pane>
      <el-tab-pane label="Guard 与身份识别" name="guard">
        <GuardPanel v-if="tab === 'guard' && ownersStore.currentId" :owner-id="ownersStore.currentId" />
        <el-empty v-else-if="tab === 'guard'" description="请先选择一个 Eidolon 空间" />
      </el-tab-pane>
      <el-tab-pane label="固件烧录" name="firmware">
        <Firmware v-if="tab === 'firmware'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.device-center { display: flex; flex-direction: column; min-height: 100%; }
.dc-tabs { flex: 1 1 auto; }
:deep(.dc-tabs > .el-tabs__content) { padding-top: 4px; }
</style>
