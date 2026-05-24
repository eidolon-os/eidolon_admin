<script setup lang="ts">
/**
 * Top-level /devices page.
 *
 * Pure orchestration: load the device list, render rows with status badges,
 * and open the right drawer / dialog when the operator clicks a row action.
 * All actual workflow lives in dialogs / drawers / api/devices.ts.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  approveDevice,
  deriveDeviceStatusLabel,
  formatTimestamp,
  listDevices,
  type DeviceView,
} from '@/api/devices'
import AgentsDrawer from './AgentsDrawer.vue'

const devices = ref<DeviceView[]>([])
const natsAvailable = ref(true)
const loading = ref(false)
const drawerOpen = ref(false)
const drawerDevice = ref<DeviceView | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  loading.value = true
  try {
    const r = await listDevices()
    devices.value = r.devices
    natsAvailable.value = r.nats_available
    // If the drawer is open, replace its target with the fresh copy so
    // newly-added agents show up without re-opening.
    if (drawerOpen.value && drawerDevice.value) {
      const fresh = r.devices.find((d) => d.device_id === drawerDevice.value?.device_id)
      if (fresh) drawerDevice.value = fresh
    }
  } catch (e: any) {
    ElMessage.error(`加载设备列表失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => { if (!loading.value) refresh() }, 10_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

const pendingCount = computed(
  () => devices.value.filter((d) => !d.approved).length,
)

async function onApprove(d: DeviceView) {
  try {
    await approveDevice(d.device_id)
    ElMessage.success(`已批准 ${d.device_id}`)
    await refresh()
  } catch (e: any) {
    ElMessage.error(`批准失败: ${e?.response?.data?.detail || e?.message || e}`)
  }
}

function onManage(d: DeviceView) {
  drawerDevice.value = d
  drawerOpen.value = true
}
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h2>Devices</h2>
        <p class="hint">
          设备通过 mDNS 发现 hub 后会出现在这里。操作员先【批准】, 再【管理 agents】
          为它绑定一个 (或多个) persona 模板的 render 拷贝。
          <span v-if="!natsAvailable" class="warn">⚠ NATS 不可达, binding 信息暂不可见</span>
        </p>
      </div>
      <div class="actions">
        <el-tag
          v-if="pendingCount > 0"
          size="small"
          type="warning"
          effect="dark"
        >
          {{ pendingCount }} 待批准
        </el-tag>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      </div>
    </header>

    <el-table
      :data="devices"
      v-loading="loading && devices.length === 0"
      size="small"
      stripe
      class="table"
    >
      <el-table-column label="device_id" min-width="160">
        <template #default="{ row }">
          <code>{{ row.device_id }}</code>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="name" min-width="160" />
      <el-table-column label="状态" min-width="220">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="deriveDeviceStatusLabel(row).tone"
            effect="dark"
          >
            {{ deriveDeviceStatusLabel(row).label }}
          </el-tag>
          <el-tag
            v-if="row.paired"
            size="small"
            effect="plain"
            style="margin-left: 6px"
          >
            paired
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="last_seen" min-width="160">
        <template #default="{ row }">
          <span class="muted">{{ formatTimestamp(row.last_seen) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" min-width="220" align="right">
        <template #default="{ row }">
          <el-button
            v-if="!row.approved"
            size="small"
            type="primary"
            @click="onApprove(row)"
          >
            批准
          </el-button>
          <el-button
            v-if="row.approved"
            size="small"
            @click="onManage(row)"
          >
            管理 agents
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="!loading && devices.length === 0" class="empty">
      还没有设备 — 让设备通过 mDNS 找到 hub 后这里就会出现。
    </div>

    <AgentsDrawer
      v-model:open="drawerOpen"
      :device="drawerDevice"
      @changed="refresh"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.head h2 {
  margin: 0 0 4px;
  font-size: 18px;
  font-weight: 600;
  color: var(--eid-text-primary);
}
.hint {
  margin: 0;
  font-size: 12px;
  color: var(--eid-text-secondary);
  max-width: 720px;
  line-height: 1.6;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.warn { color: var(--eid-warning); margin-left: 8px; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.table {
  background: var(--eid-bg-panel);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
}
.empty {
  padding: 32px;
  text-align: center;
  color: var(--eid-text-muted);
  font-size: 12px;
  background: var(--eid-bg-panel);
  border: 1px dashed var(--eid-border);
  border-radius: var(--eid-radius);
}
</style>
