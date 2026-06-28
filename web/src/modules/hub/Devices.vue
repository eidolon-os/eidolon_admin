<script setup lang="ts">
// Hub > Devices is the product entry for device access/reachability. The data
// and business orchestration still come from Admin's /api/devices aggregate:
// Hub device facts + Admin binding + Agent metadata.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveDevice,
  identifyDevice,
  listDevices,
  refreshDeviceConfig,
  setDeviceEnabled,
  unregisterDevice,
  wakeDevice,
  type DeviceListResponse,
  type DeviceView,
} from '@/api/devices'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import JsonViewer from '@/modules/common/JsonViewer.vue'

type RuntimeFilter = 'all' | 'online' | 'degraded' | 'offline' | 'unknown'
type BadgeState = 'online' | 'offline' | 'warning' | 'unknown'
type DetailTab = 'overview' | 'access' | 'routing' | 'rooms' | 'raw'

const status = ref<RuntimeFilter>('all')
const devices = ref<DeviceView[]>([])
const hubAvailable = ref(true)
const loading = ref(false)
const detail = ref<DeviceView | null>(null)
const detailTab = ref<DetailTab>('overview')
let timer: ReturnType<typeof setInterval> | null = null

const busyDeviceId = ref('')

const filteredDevices = computed(() => {
  if (status.value === 'all') return devices.value
  return devices.value.filter((d) => (d.status || 'unknown') === status.value)
})

const pendingCount = computed(() => devices.value.filter((d) => !d.approved).length)
const routingGapCount = computed(() => devices.value.filter((d) => d.enabled && d.approved && !d.binding).length)
const voiceCount = computed(() => devices.value.filter((d) => inVoiceRoom(d)).length)
const standbyCount = computed(() => devices.value.filter((d) => inControlRoom(d)).length)
const lastDiscovery = ref<DeviceListResponse['discovery'] | null>(null)
const discoveryBadge = computed(() => {
  if (!hubAvailable.value) return { label: 'Hub unreachable', type: 'danger' as const }
  if (!lastDiscovery.value) return { label: 'Discovery unknown', type: 'info' as const }
  if (lastDiscovery.value.registered) return { label: 'mDNS broadcasting', type: 'success' as const }
  return { label: 'mDNS stopped', type: 'warning' as const }
})

async function refresh() {
  loading.value = true
  try {
    const d = await listDevices()
    devices.value = d.devices
    hubAvailable.value = d.hub_available
    lastDiscovery.value = d.discovery
  } catch (e: any) {
    ElMessage.error(`加载失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => { if (!loading.value) void refresh() }, 10_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function inControlRoom(d: DeviceView): boolean {
  return d.status === 'online' && !!d.room_name && d.room_name.endsWith('-control')
}

function inVoiceRoom(d: DeviceView): boolean {
  return d.status === 'online' && !!d.room_name && !d.room_name.endsWith('-control')
}

function accessState(d: DeviceView): { label: string; state: BadgeState } {
  if (!d.enabled) return { label: 'disabled', state: 'offline' }
  if (!d.approved) return { label: 'pending approval', state: 'warning' }
  return { label: 'approved', state: 'online' }
}

function reachabilityState(d: DeviceView): { label: string; state: BadgeState } {
  if (!d.enabled) return { label: 'disabled', state: 'offline' }
  if (inVoiceRoom(d)) return { label: 'in voice room', state: 'online' }
  if (inControlRoom(d)) return { label: 'standby online', state: 'online' }
  if (d.status === 'degraded') return { label: 'degraded', state: 'warning' }
  if (d.status === 'offline') return { label: 'offline', state: 'offline' }
  if (d.status === 'online') return { label: 'online', state: 'online' }
  return { label: d.status || 'unknown', state: 'unknown' }
}

function roomKind(d: DeviceView): { label: string; type: 'success' | 'warning' | 'info' } {
  const room = d.room_name || ''
  if (!room) return { label: 'none', type: 'info' }
  if (room.includes('pending')) return { label: 'pending', type: 'warning' }
  if (room.endsWith('-control')) return { label: 'control', type: 'success' }
  return { label: 'voice', type: 'success' }
}

function routeState(d: DeviceView): { label: string; type: 'success' | 'warning' | 'info' } {
  if (!d.approved) return { label: 'not routed', type: 'info' }
  if (!d.binding) return { label: 'routing missing', type: 'warning' }
  return { label: 'bound', type: 'success' }
}

function routingText(d: DeviceView): string {
  if (!d.binding) return 'Admin routing required'
  return d.binding.agent_id
}

function primaryAction(d: DeviceView): { label: string; type: 'primary' | 'default'; disabled?: boolean; run: () => void } {
  if (!d.enabled) {
    return { label: '启用', type: 'default', run: () => void onToggleEnabled(d, true) }
  }
  if (!d.approved) {
    return { label: '批准', type: 'primary', run: () => void onApprove(d) }
  }
  if (inControlRoom(d)) {
    return { label: '唤醒', type: 'primary', run: () => void onWake(d) }
  }
  if (d.status === 'online') {
    return { label: '识别', type: 'default', run: () => void onIdentify(d) }
  }
  if (!d.binding) {
    return { label: '路由状态', type: 'default', run: () => openDetail(d, 'routing') }
  }
  return { label: '详情', type: 'default', run: () => openDetail(d) }
}

function openDetail(d: DeviceView, tab: DetailTab = 'overview') {
  detail.value = d
  detailTab.value = tab
}

async function withBusy(d: DeviceView, fn: () => Promise<void>) {
  busyDeviceId.value = d.device_id
  try {
    await fn()
  } finally {
    busyDeviceId.value = ''
  }
}

async function onWake(d: DeviceView) {
  await withBusy(d, async () => {
    try {
      await wakeDevice(d.device_id)
      ElMessage.success('已下发唤醒')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`唤醒失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onIdentify(d: DeviceView) {
  await withBusy(d, async () => {
    try {
      await identifyDevice(d.device_id)
      ElMessage.success('已下发识别命令')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`识别失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onRefreshConfig(d: DeviceView) {
  await withBusy(d, async () => {
    try {
      await refreshDeviceConfig(d.device_id)
      ElMessage.success('已请求设备刷新配置')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`刷新配置失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onApprove(d: DeviceView) {
  await withBusy(d, async () => {
    try {
      await approveDevice(d.device_id)
      ElMessage.success(`已批准 ${d.device_id}`)
      await refresh()
    } catch (e: any) {
      ElMessage.error(`批准失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onToggleEnabled(d: DeviceView, enabled: boolean | string | number) {
  const nextEnabled = Boolean(enabled)
  if (nextEnabled === d.enabled) return
  const title = nextEnabled ? '启用设备' : '停用设备'
  const message = nextEnabled
    ? `确认启用 ${d.device_id}? 启用后可批准并参与 Hub 接入流程。`
    : `确认停用 ${d.device_id}? 设备记录和 Admin routing 会保留，但不会参与运行时使用。`
  try {
    await ElMessageBox.confirm(message, title, { type: nextEnabled ? 'info' : 'warning' })
  } catch {
    return
  }
  await withBusy(d, async () => {
    try {
      await setDeviceEnabled(d.device_id, nextEnabled)
      ElMessage.success(nextEnabled ? '已启用' : '已停用')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`${nextEnabled ? '启用' : '停用'}失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onUnregister(d: DeviceView) {
  try {
    await ElMessageBox.confirm(
      `确认注销 ${d.device_id}? 这会清除 Hub 记录和 Admin routing；设备重新发现后会回到待批准状态。`,
      '注销 / 撤销设备',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await withBusy(d, async () => {
    try {
      await unregisterDevice(d.device_id)
      ElMessage.success('已注销，设备重新发现后需重新批准')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`注销失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onMoreCommand(command: string, d: DeviceView) {
  if (command === 'detail') openDetail(d)
  if (command === 'access') openDetail(d, 'access')
  if (command === 'routing') openDetail(d, 'routing')
  if (command === 'rooms') openDetail(d, 'rooms')
  if (command === 'raw') openDetail(d, 'raw')
  if (command === 'identify') await onIdentify(d)
  if (command === 'refresh-config') await onRefreshConfig(d)
  if (command === 'enable') await onToggleEnabled(d, true)
  if (command === 'disable') await onToggleEnabled(d, false)
  if (command === 'unregister') await onUnregister(d)
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Devices</h2>
        <div class="subtitle">
          {{ devices.length }} devices · access and reachability via Hub
          <span v-if="!hubAvailable" class="warn">Hub 不可达, 状态可能过期</span>
        </div>
      </div>
      <div class="actions">
        <el-tag v-if="voiceCount > 0" size="small" type="success" effect="dark">
          {{ voiceCount }} in voice
        </el-tag>
        <el-tag v-if="standbyCount > 0" size="small" type="success">
          {{ standbyCount }} standby
        </el-tag>
        <el-tag v-if="pendingCount > 0" size="small" type="warning" effect="dark">
          {{ pendingCount }} pending
        </el-tag>
        <el-tag v-if="routingGapCount > 0" size="small" type="info">
          {{ routingGapCount }} routing gaps
        </el-tag>
        <el-radio-group v-model="status" size="small">
          <el-radio-button value="all">all</el-radio-button>
          <el-radio-button value="online">online</el-radio-button>
          <el-radio-button value="degraded">degraded</el-radio-button>
          <el-radio-button value="offline">offline</el-radio-button>
          <el-radio-button value="unknown">unknown</el-radio-button>
        </el-radio-group>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      </div>
    </div>

    <div class="discovery-row">
      <el-tag size="small" :type="discoveryBadge.type" effect="dark">
        {{ discoveryBadge.label }}
      </el-tag>
      <span v-if="lastDiscovery?.config_url" class="mono compact">
        {{ lastDiscovery.config_url }}
      </span>
      <span v-if="lastDiscovery?.ip" class="muted">
        {{ lastDiscovery.service_name }} · {{ lastDiscovery.ip }}:{{ lastDiscovery.port }}
      </span>
      <span v-if="lastDiscovery?.last_error" class="warn">
        {{ lastDiscovery.last_error }}
      </span>
    </div>

    <el-table :data="filteredDevices" v-loading="loading && devices.length === 0" size="small" stripe>
      <el-table-column label="Device" min-width="240">
        <template #default="{ row }">
          <div class="device-cell">
            <strong>{{ row.name || row.device_id }}</strong>
            <span class="mono">{{ row.device_id }}</span>
            <el-tag size="small" type="info">{{ row.kind || 'unknown' }}</el-tag>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Access" width="170">
        <template #default="{ row }">
          <StatusBadge :state="accessState(row).state" :label="accessState(row).label" />
        </template>
      </el-table-column>

      <el-table-column label="Reachability" width="180">
        <template #default="{ row }">
          <StatusBadge :state="reachabilityState(row).state" :label="reachabilityState(row).label" />
        </template>
      </el-table-column>

      <el-table-column label="Routing" min-width="240">
        <template #default="{ row }">
          <div class="routing-cell">
            <el-tag :type="routeState(row).type" size="small" effect="dark">
              {{ routeState(row).label }}
            </el-tag>
            <span class="mono">{{ routingText(row) }}</span>
            <span v-if="row.resolved_user_id || row.resolved_template_id" class="muted">
              {{ row.resolved_user_id || '—' }} / {{ row.resolved_template_id || '—' }}
            </span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Room" min-width="220">
        <template #default="{ row }">
          <div class="room-cell">
            <el-tag :type="roomKind(row).type" size="small">{{ roomKind(row).label }}</el-tag>
            <span class="mono">{{ row.room_name || '—' }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Last seen" width="170">
        <template #default="{ row }">
          <span class="muted">{{ formatTimestamp(row.last_seen) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="Action" width="230" align="right" fixed="right">
        <template #default="{ row }">
          <div class="row-actions">
            <el-button
              size="small"
              :type="primaryAction(row).type"
              :icon="primaryAction(row).label === '唤醒' ? VideoPlay : undefined"
              :loading="busyDeviceId === row.device_id"
              @click="primaryAction(row).run()"
            >
              {{ primaryAction(row).label }}
            </el-button>
            <el-dropdown size="small" trigger="click" @command="(cmd: string) => onMoreCommand(cmd, row)">
              <el-button size="small">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="detail">查看详情</el-dropdown-item>
                  <el-dropdown-item command="rooms">Room 状态</el-dropdown-item>
                  <el-dropdown-item command="routing">Routing 状态</el-dropdown-item>
                  <el-dropdown-item command="raw">Raw JSON</el-dropdown-item>
                  <el-dropdown-item command="identify" :disabled="!row.enabled">设备识别</el-dropdown-item>
                  <el-dropdown-item command="refresh-config" :disabled="!row.enabled">刷新配置</el-dropdown-item>
                  <el-dropdown-item :command="row.enabled ? 'disable' : 'enable'">
                    {{ row.enabled ? '停用' : '启用' }}
                  </el-dropdown-item>
                  <el-dropdown-item divided command="unregister">注销 / 撤销</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="!loading && filteredDevices.length === 0" class="empty">
      {{ lastDiscovery?.registered ? '等待设备请求配置' : '等待 Hub mDNS 广播恢复' }}
    </div>

    <el-drawer
      :model-value="!!detail"
      @update:model-value="(v: boolean) => { if (!v) detail = null }"
      :title="detail ? `Device · ${detail.device_id}` : ''"
      size="56%"
      direction="rtl"
    >
      <el-tabs v-if="detail" v-model="detailTab" class="detail-tabs">
        <el-tab-pane label="Overview" name="overview">
          <div class="detail-grid">
            <div class="detail-box">
              <span class="label">Access</span>
              <StatusBadge :state="accessState(detail).state" :label="accessState(detail).label" />
            </div>
            <div class="detail-box">
              <span class="label">Reachability</span>
              <StatusBadge :state="reachabilityState(detail).state" :label="reachabilityState(detail).label" />
            </div>
            <div class="detail-box">
              <span class="label">Last seen</span>
              <span class="mono">{{ formatTimestamp(detail.last_seen) }}</span>
            </div>
            <div class="detail-box">
              <span class="label">Device kind</span>
              <span class="mono">{{ detail.kind || 'unknown' }}</span>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Access" name="access">
          <div class="detail-grid">
            <div class="detail-box"><span class="label">Enabled</span><span>{{ detail.enabled ? 'yes' : 'no' }}</span></div>
            <div class="detail-box"><span class="label">Approved</span><span>{{ detail.approved ? 'yes' : 'no' }}</span></div>
            <div class="detail-box"><span class="label">Approved at</span><span class="mono">{{ formatTimestamp(detail.approved_at) }}</span></div>
            <div class="detail-box"><span class="label">Missed probes</span><span class="mono">{{ detail.missed_probes ?? 0 }}</span></div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Routing" name="routing">
          <div class="detail-grid">
            <div class="detail-box wide"><span class="label">Admin routing</span><span class="mono">{{ detail.binding?.agent_id || 'not configured' }}</span></div>
            <div class="detail-box"><span class="label">User</span><span class="mono">{{ detail.resolved_user_id || '—' }}</span></div>
            <div class="detail-box"><span class="label">Template</span><span class="mono">{{ detail.resolved_template_id || '—' }}</span></div>
            <div class="detail-box"><span class="label">Configured at</span><span class="mono">{{ formatTimestamp(detail.binding?.bound_at) }}</span></div>
            <div class="detail-box wide">
              <span class="label">Ownership</span>
              <span class="muted">Routing is resolved by Admin. Hub Devices only displays this state for runtime diagnosis.</span>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Rooms" name="rooms">
          <div class="detail-grid">
            <div class="detail-box"><span class="label">Runtime status</span><span class="mono">{{ detail.status || 'unknown' }}</span></div>
            <div class="detail-box"><span class="label">Room kind</span><span class="mono">{{ roomKind(detail).label }}</span></div>
            <div class="detail-box wide"><span class="label">Room name</span><span class="mono">{{ detail.room_name || '—' }}</span></div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Raw" name="raw">
          <JsonViewer :data="detail" />
        </el-tab-pane>
      </el-tabs>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; gap: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.warn { color: var(--eid-warning); margin-left: 8px; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.discovery-row { display: flex; gap: 10px; align-items: center; min-height: 30px; margin: -4px 0 12px; flex-wrap: wrap; }
.mono {
  font-family: var(--eid-font-mono);
  font-size: 12px;
  padding: 1px 6px;
  background: var(--eid-bg-canvas);
  border-radius: 3px;
  overflow-wrap: anywhere;
}
.mono.compact { max-width: 520px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.device-cell, .routing-cell, .room-cell { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.row-actions { display: flex; justify-content: flex-end; gap: 8px; }
.empty {
  padding: 32px;
  text-align: center;
  color: var(--eid-text-muted);
  font-size: 12px;
  background: var(--eid-bg-panel);
  border: 1px dashed var(--eid-border);
  border-radius: var(--eid-radius);
}
.detail-tabs { min-height: 360px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.detail-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  background: var(--eid-bg-panel);
}
.detail-box.wide { grid-column: 1 / -1; }
.label {
  color: var(--eid-text-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0;
}
</style>
