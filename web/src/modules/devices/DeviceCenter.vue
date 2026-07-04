<script setup lang="ts">
// Single Device Center — consolidates the formerly scattered device surfaces
// (Hub › Devices access/lifecycle/commands + ESP32 flashing) behind one entry.
// Tab is reflected in the route (:tab?) so it is shareable and back/forward-able.
// Deeper owner→companion fleet grouping (server-side join) is a follow-up.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Fleet from '@/modules/hub/Devices.vue'
import Firmware from '@/modules/tools/Esp32.vue'

const route = useRoute()
const router = useRouter()

const tab = computed<'fleet' | 'firmware'>({
  get: () => (route.params.tab === 'firmware' ? 'firmware' : 'fleet'),
  set: (v) => router.replace({ name: 'devices', params: { tab: v } }),
})
</script>

<template>
  <div class="device-center">
    <el-tabs v-model="tab" class="dc-tabs">
      <el-tab-pane label="设备舰队" name="fleet">
        <Fleet v-if="tab === 'fleet'" />
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
