<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  listDevices,
  type AdminDevice,
  type DevicePresenceStatus,
} from '@/api/hub'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const status = ref<'all' | DevicePresenceStatus>('all')
const items = ref<AdminDevice[]>([])
const loading = ref(false)
const detail = ref<AdminDevice | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  try {
    items.value = await listDevices(status.value === 'all' ? undefined : status.value)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (!loading.value) load() }, 10_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
watch(status, load)

function badgeState(s?: string): 'online' | 'offline' | 'warning' | 'unknown' {
  if (s === 'online') return 'online'
  if (s === 'degraded') return 'warning'
  if (s === 'offline') return 'offline'
  return 'unknown'
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Devices</h2>
        <div class="subtitle">{{ items.length }} 设备 · 10 秒自动刷新</div>
      </div>
      <div class="actions">
        <el-radio-group v-model="status" size="small">
          <el-radio-button label="all">全部</el-radio-button>
          <el-radio-button label="online">online</el-radio-button>
          <el-radio-button label="degraded">degraded</el-radio-button>
          <el-radio-button label="offline">offline</el-radio-button>
        </el-radio-group>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column label="Device ID" min-width="220">
        <template #default="{ row }"><span class="mono">{{ row.device_id }}</span></template>
      </el-table-column>
      <el-table-column label="Name" prop="name" width="180" />
      <el-table-column label="状态" width="140">
        <template #default="{ row }">
          <StatusBadge :state="badgeState(row.status)" :label="row.status || 'unknown'" />
        </template>
      </el-table-column>
      <el-table-column label="Room" width="160">
        <template #default="{ row }">
          <span v-if="row.room_name" class="mono">{{ row.room_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="Last seen" width="200">
        <template #default="{ row }">{{ row.last_seen_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="Missed" width="90">
        <template #default="{ row }">{{ row.missed_probes ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button size="small" link @click="detail = row">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer
      :model-value="!!detail"
      @update:model-value="(v: boolean) => { if (!v) detail = null }"
      :title="detail ? `Device · ${detail.device_id}` : ''"
      size="50%"
      direction="rtl"
    >
      <JsonViewer v-if="detail" :data="detail" />
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.actions { display: flex; gap: 12px; align-items: center; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
</style>
