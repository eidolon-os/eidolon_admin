<script setup lang="ts">
// Hub hardware table: device access, approval, and reachability for physical
// bodies discovered by Hub. Host-local web bodies live in the owner inventory
// above this table and do not need Hub approval.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bell, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveDevice,
  identifyDevice,
  listDevices,
  refreshDevices,
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

const emit = defineEmits<{ approved: [deviceId: string] }>()

type ReadinessFilter = 'all' | 'attention' | 'ready' | 'sessions' | 'offline'
type DeviceReadiness =
  | 'pending_approval'
  | 'disabled'
  | 'offline'
  | 'degraded'
  | 'standby'
  | 'in_session'
  | 'online'
  | 'unknown'
type BadgeState = 'online' | 'offline' | 'warning' | 'unknown'
type TagTone = 'success' | 'warning' | 'info' | 'danger'
type DetailTab = 'overview' | 'connection' | 'raw'

interface DeviceRow {
  device: DeviceView
  readiness: DeviceReadiness
  label: string
  state: BadgeState
  reason: string
}

interface LifecycleCheck {
  label: string
  ok: boolean
  value: string
}

const filter = ref<ReadinessFilter>('all')
const devices = ref<DeviceView[]>([])
const hubAvailable = ref(true)
const loading = ref(false)
const detail = ref<DeviceView | null>(null)
const detailTab = ref<DetailTab>('overview')
let timer: ReturnType<typeof setInterval> | null = null

const busyDeviceId = ref('')
const rollCallLoading = ref(false)
const syncLoading = ref(false)

const rows = computed<DeviceRow[]>(() => devices.value.map(toDeviceRow))
const callableDevices = computed(() => devices.value.filter(canSendDeviceCommand))
const filteredRows = computed(() => {
  if (filter.value === 'all') return rows.value
  if (filter.value === 'attention') {
    return rows.value.filter((r) => (
      r.readiness === 'pending_approval'
      || r.readiness === 'disabled'
      || r.readiness === 'degraded'
      || r.readiness === 'unknown'
    ))
  }
  if (filter.value === 'ready') {
    return rows.value.filter((r) => r.readiness === 'standby' || r.readiness === 'online')
  }
  if (filter.value === 'sessions') {
    return rows.value.filter((r) => r.readiness === 'in_session')
  }
  return rows.value.filter((r) => r.device.status === 'offline')
})

const attentionCount = computed(() => rows.value.filter((r) => (
  r.readiness === 'pending_approval'
  || r.readiness === 'disabled'
  || r.readiness === 'degraded'
  || r.readiness === 'unknown'
)).length)
const readyCount = computed(() => rows.value.filter((r) => r.readiness === 'standby' || r.readiness === 'online').length)
const sessionCount = computed(() => rows.value.filter((r) => r.readiness === 'in_session').length)
const offlineCount = computed(() => rows.value.filter((r) => r.device.status === 'offline').length)

const lastDiscovery = ref<DeviceListResponse['discovery'] | null>(null)
const lastLiveKit = ref<DeviceListResponse['livekit'] | null>(null)
const discoveryBadge = computed(() => {
  if (!hubAvailable.value) return { label: 'Hub unreachable', type: 'danger' as const }
  if (!lastDiscovery.value) return { label: 'Discovery unknown', type: 'info' as const }
  if (lastDiscovery.value.registered) return { label: 'mDNS broadcasting', type: 'success' as const }
  return { label: 'mDNS stopped', type: 'warning' as const }
})
const liveKitMismatch = computed(() => {
  const mdnsIp = lastDiscovery.value?.ip || ''
  const nodeIp = lastLiveKit.value?.node_ip || ''
  return !!mdnsIp && !!nodeIp && mdnsIp !== nodeIp
})

async function refresh() {
  loading.value = true
  try {
    const d = await listDevices()
    devices.value = d.devices
    hubAvailable.value = d.hub_available
    lastDiscovery.value = d.discovery
    lastLiveKit.value = d.livekit ?? null
  } catch (e: any) {
    ElMessage.error(`加载失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function syncNow() {
  syncLoading.value = true
  try {
    const d = await refreshDevices()
    devices.value = d.devices
    hubAvailable.value = d.hub_available
    lastDiscovery.value = d.discovery
    lastLiveKit.value = d.livekit ?? null
    ElMessage.success('已同步 Hub registry、LiveKit presence 和 Admin routing')
  } catch (e: any) {
    ElMessage.error(`同步失败: ${extractErrorMessage(e)}`)
  } finally {
    syncLoading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => { if (!loading.value && !syncLoading.value) void refresh() }, 10_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function inControlRoom(d: DeviceView): boolean {
  return d.status === 'online' && !!d.room_name && d.room_name.endsWith('-control')
}

function inVoiceRoom(d: DeviceView): boolean {
  return d.status === 'online' && !!d.room_name && !d.room_name.endsWith('-control')
}

function canSendDeviceCommand(d: DeviceView): boolean {
  return d.enabled && d.status === 'online' && !!d.room_name && !!d.participant_sid
}

function missedProbeText(d: DeviceView): string {
  const count = d.missed_probes ?? 0
  return `${count} missed probe${count === 1 ? '' : 's'}`
}

function reachabilityText(d: DeviceView): string {
  if (!d.enabled) return 'disabled by admin'
  if (d.status === 'online') return `LiveKit participant visible in ${d.room_name || 'room'}`
  if (d.status === 'degraded') return `LiveKit presence is unstable: ${missedProbeText(d)}`
  if (d.status === 'offline') {
    return `Hub presence probe missed the device in LiveKit: ${missedProbeText(d)}`
  }
  return d.status || 'runtime status unknown'
}

function commandUnavailableReason(d: DeviceView): string {
  if (!d.enabled) return '设备已停用，不能下发 Hub 控制命令'
  if (d.status === 'degraded') {
    return `设备连接不稳定: 最近 ${d.missed_probes ?? 0} 次 presence probe 未连续确认它在 LiveKit 中。请先点 Refresh 或等待重连稳定。`
  }
  if (d.status === 'offline') {
    return `设备离线: Hub 连续 ${d.missed_probes ?? 0} 次 presence probe 没在 LiveKit room 里看到它。串口日志不等于已连接 LiveKit，请确认日志出现 lk_event=Connected / control_connected。`
  }
  if (!d.room_name) return '设备没有当前 LiveKit room，无法下发命令'
  if (!d.participant_sid) return 'Hub 没有当前 LiveKit participant sid，无法确认 data channel 可投递'
  if (d.status === 'unknown') return '设备运行时状态未知，请刷新或等待下一轮 presence probe'
  return '设备当前不可达，不能下发 Hub 控制命令'
}

function identifyHint(d: DeviceView): string {
  if (canSendDeviceCommand(d)) {
    return '通过 LiveKit data channel 下发 device.identify；设备需要在线并能接收 eidolon.control'
  }
  return commandUnavailableReason(d)
}

function readinessOf(d: DeviceView): DeviceReadiness {
  if (!d.enabled) return 'disabled'
  if (!d.approved) return 'pending_approval'
  if (inVoiceRoom(d)) return 'in_session'
  if (d.status === 'offline') return 'offline'
  if (d.status === 'degraded') return 'degraded'
  if (inControlRoom(d)) return 'standby'
  if (d.status === 'online') return 'online'
  return 'unknown'
}

function registryState(d: DeviceView): { label: string; state: BadgeState; reason: string } {
  if (!d.enabled) return { label: 'Disabled', state: 'offline', reason: 'persistent Hub registry: disabled by admin' }
  if (!d.approved) return { label: 'Pending approval', state: 'warning', reason: 'persistent Hub registry: not approved yet' }
  return { label: 'Approved', state: 'online', reason: `persistent Hub registry · last seen ${formatTimestamp(d.last_seen)}` }
}

function lastIpText(d: DeviceView): string {
  return d.last_ip || 'unknown ip'
}

function reachabilityState(d: DeviceView): { label: string; state: BadgeState; reason: string } {
  if (!d.enabled) return { label: 'Not active', state: 'offline', reason: 'disabled devices are not command targets' }
  if (d.status === 'online') return { label: 'Online', state: 'online', reason: reachabilityText(d) }
  if (d.status === 'degraded') return { label: 'Degraded', state: 'warning', reason: reachabilityText(d) }
  if (d.status === 'offline') return { label: 'Offline', state: 'offline', reason: reachabilityText(d) }
  return { label: 'Unknown', state: 'unknown', reason: reachabilityText(d) }
}

function toDeviceRow(d: DeviceView): DeviceRow {
  const readiness = readinessOf(d)
  const lastSeen = formatTimestamp(d.last_seen)
  if (readiness === 'disabled') {
    return { device: d, readiness, label: 'Disabled', state: 'offline', reason: 'admin disabled' }
  }
  if (readiness === 'pending_approval') {
    return {
      device: d,
      readiness,
      label: 'Pending approval',
      state: d.status === 'offline' ? 'offline' : 'warning',
      reason: `waiting for Hub approval · ${reachabilityText(d)}`,
    }
  }
  if (readiness === 'in_session') {
    return { device: d, readiness, label: 'In session', state: 'online', reason: 'voice room active' }
  }
  if (readiness === 'offline') {
    return { device: d, readiness, label: 'Offline', state: 'offline', reason: `${reachabilityText(d)} · last seen ${lastSeen}` }
  }
  if (readiness === 'degraded') {
    return { device: d, readiness, label: 'Degraded', state: 'warning', reason: reachabilityText(d) }
  }
  if (readiness === 'standby') {
    return { device: d, readiness, label: 'Standby', state: 'online', reason: 'control room connected' }
  }
  if (readiness === 'online') {
    return { device: d, readiness, label: 'Online', state: 'online', reason: 'online, no control room' }
  }
  return { device: d, readiness, label: 'Unknown', state: 'unknown', reason: d.status || 'no runtime status' }
}

function sessionState(d: DeviceView): { label: string; type: TagTone; detail: string } {
  if (inVoiceRoom(d)) return { label: 'Voice', type: 'success', detail: d.room_name || '-' }
  if (inControlRoom(d)) return { label: 'Control', type: 'success', detail: d.room_name || '-' }
  if ((d.room_name || '').includes('pending')) return { label: 'Pending', type: 'warning', detail: d.room_name || '-' }
  return { label: 'None', type: 'info', detail: '-' }
}

function primaryAction(row: DeviceRow): { label: string; type: 'primary' | 'default'; icon?: any; run: () => void } {
  const d = row.device
  if (row.readiness === 'disabled') {
    return { label: 'Enable', type: 'default', run: () => void onToggleEnabled(d, true) }
  }
  if (row.readiness === 'pending_approval') {
    return { label: 'Approve', type: 'primary', run: () => void onApprove(d) }
  }
  if (row.readiness === 'in_session') {
    return { label: 'View room', type: 'default', run: () => openDetail(d, 'connection') }
  }
  if (row.readiness === 'standby') {
    return { label: 'Start session', type: 'primary', icon: VideoPlay, run: () => void onWake(d) }
  }
  if (row.readiness === 'degraded') {
    return { label: 'Diagnose', type: 'default', run: () => openDetail(d, 'connection') }
  }
  if (row.readiness === 'online') {
    return { label: '点名', type: 'default', icon: Bell, run: () => void onIdentify(d) }
  }
  return { label: 'Details', type: 'default', run: () => openDetail(d) }
}

function lifecycleChecks(d: DeviceView): LifecycleCheck[] {
  return [
    { label: 'Registry identity', ok: true, value: d.device_id },
    { label: 'Approved', ok: d.approved, value: d.approved ? formatTimestamp(d.approved_at) : 'pending' },
    { label: 'Enabled', ok: d.enabled, value: d.enabled ? 'yes' : 'no' },
    { label: 'LiveKit presence', ok: d.status === 'online', value: reachabilityText(d) },
    { label: 'Probe misses', ok: (d.missed_probes ?? 0) === 0, value: missedProbeText(d) },
    { label: 'Command target', ok: canSendDeviceCommand(d), value: canSendDeviceCommand(d) ? d.participant_sid || 'ready' : commandUnavailableReason(d) },
    { label: 'Control room', ok: inControlRoom(d) || inVoiceRoom(d), value: sessionState(d).detail },
    { label: 'Voice session', ok: inVoiceRoom(d), value: inVoiceRoom(d) ? 'active' : 'none' },
  ]
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
      ElMessage.success('已下发会话启动命令')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`启动失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onIdentify(d: DeviceView) {
  if (!canSendDeviceCommand(d)) {
    ElMessage.warning(commandUnavailableReason(d))
    return
  }
  await withBusy(d, async () => {
    try {
      await identifyDevice(d.device_id)
      ElMessage.success('已下发点名命令')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`点名失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onRollCallReachable() {
  const targets = callableDevices.value
  if (targets.length === 0) {
    ElMessage.warning('当前没有可点名的在线设备')
    return
  }
  rollCallLoading.value = true
  try {
    const results = await Promise.allSettled(targets.map((d) => identifyDevice(d.device_id)))
    const ok = results.filter((r) => r.status === 'fulfilled').length
    const failed = results.length - ok
    if (failed > 0) {
      const firstFailure = results.find((r) => r.status === 'rejected')
      const reason = firstFailure?.status === 'rejected' ? `: ${extractErrorMessage(firstFailure.reason)}` : ''
      ElMessage.warning(`点名完成: ${ok}/${results.length} 成功, ${failed} 失败${reason}`)
    } else {
      ElMessage.success(`已点名 ${ok} 台可达设备`)
    }
    await refresh()
  } finally {
    rollCallLoading.value = false
  }
}

async function onRefreshConfig(d: DeviceView) {
  if (!canSendDeviceCommand(d)) {
    ElMessage.warning(commandUnavailableReason(d))
    return
  }
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
      emit('approved', d.device_id)
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
    ? `确认启用 ${d.device_id}?`
    : `确认停用 ${d.device_id}? 设备记录和 Admin routing 会保留。`
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

async function onForget(d: DeviceView) {
  try {
    await ElMessageBox.confirm(
      `确认移除 ${d.device_id}? 设备重新发现后会回到待批准状态。`,
      'Forget device',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await withBusy(d, async () => {
    try {
      await unregisterDevice(d.device_id)
      ElMessage.success('已移除，设备重新发现后需重新批准')
      await refresh()
    } catch (e: any) {
      ElMessage.error(`移除失败: ${extractErrorMessage(e)}`)
    }
  })
}

async function onMoreCommand(command: string, d: DeviceView) {
  if (command === 'detail') openDetail(d)
  if (command === 'connection') openDetail(d, 'connection')
  if (command === 'raw') openDetail(d, 'raw')
  if (command === 'identify') await onIdentify(d)
  if (command === 'refresh-config') await onRefreshConfig(d)
  if (command === 'enable') await onToggleEnabled(d, true)
  if (command === 'disable') await onToggleEnabled(d, false)
  if (command === 'forget') await onForget(d)
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Hub 硬件设备</h2>
        <div class="subtitle">
          {{ devices.length }} hardware devices · Hub registry / LiveKit reachability
          <span v-if="!hubAvailable" class="warn">Hub 不可达, 状态可能过期</span>
        </div>
      </div>
      <div class="actions">
        <el-tag v-if="attentionCount > 0" size="small" type="warning" effect="dark">
          {{ attentionCount }} attention
        </el-tag>
        <el-tag v-if="readyCount > 0" size="small" type="success">
          {{ readyCount }} ready
        </el-tag>
        <el-tag v-if="sessionCount > 0" size="small" type="success" effect="dark">
          {{ sessionCount }} sessions
        </el-tag>
        <el-tag v-if="offlineCount > 0" size="small" type="info">
          {{ offlineCount }} offline
        </el-tag>
        <el-radio-group v-model="filter" size="small">
          <el-radio-button value="all">all</el-radio-button>
          <el-radio-button value="attention">attention</el-radio-button>
          <el-radio-button value="ready">ready</el-radio-button>
          <el-radio-button value="sessions">sessions</el-radio-button>
          <el-radio-button value="offline">offline</el-radio-button>
        </el-radio-group>
        <el-button
          size="small"
          :icon="Bell"
          :loading="rollCallLoading"
          :disabled="callableDevices.length === 0"
          @click="onRollCallReachable"
        >
          点名可达设备
        </el-button>
        <el-tooltip
          content="立即同步 Hub registry 和 LiveKit presence"
          placement="top"
        >
          <span class="button-wrap">
            <el-button
              size="small"
              :icon="Refresh"
              :loading="syncLoading"
              @click="syncNow"
            >
              Refresh
            </el-button>
          </span>
        </el-tooltip>
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
      <span v-if="lastLiveKit?.node_ip" class="muted">
        LiveKit node <span class="mono">{{ lastLiveKit.node_ip }}</span>
      </span>
      <el-tag v-if="liveKitMismatch" size="small" type="danger" effect="dark">
        Hub mDNS IP != LiveKit node_ip
      </el-tag>
      <span v-if="lastLiveKit?.last_error" class="warn">
        {{ lastLiveKit.last_error }}
      </span>
      <span v-if="lastDiscovery?.last_error" class="warn">
        {{ lastDiscovery.last_error }}
      </span>
    </div>

    <el-table :data="filteredRows" v-loading="loading && devices.length === 0" size="small" stripe>
      <el-table-column label="Device" min-width="250">
        <template #default="{ row }">
          <div class="device-cell">
            <strong>{{ row.device.name || row.device.device_id }}</strong>
            <span class="mono">{{ row.device.device_id }}</span>
            <div class="inline-meta">
              <el-tag size="small" type="info">{{ row.device.kind || 'unknown' }}</el-tag>
              <span class="mono">{{ lastIpText(row.device) }}</span>
              <span class="muted">{{ formatTimestamp(row.device.last_seen) }}</span>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Hub Registry" min-width="230">
        <template #default="{ row }">
          <div class="state-cell">
            <StatusBadge
              :state="registryState(row.device).state"
              :label="registryState(row.device).label"
            />
            <span class="muted">{{ registryState(row.device).reason }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="LiveKit Reachability" min-width="250">
        <template #default="{ row }">
          <div class="state-cell">
            <StatusBadge
              :state="reachabilityState(row.device).state"
              :label="reachabilityState(row.device).label"
            />
            <span class="muted">{{ reachabilityState(row.device).reason }}</span>
            <span class="mono">{{ row.device.participant_sid || 'no participant' }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Room" min-width="190">
        <template #default="{ row }">
          <div class="state-cell">
            <el-tag :type="sessionState(row.device).type" size="small" effect="dark">
              {{ sessionState(row.device).label }}
            </el-tag>
            <span class="mono">{{ sessionState(row.device).detail }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="Action" width="300" align="right" fixed="right">
        <template #default="{ row }">
          <div class="row-actions">
            <el-tooltip
              v-if="row.readiness === 'pending_approval'"
              :content="identifyHint(row.device)"
              placement="top"
            >
              <span class="button-wrap">
                <el-button
                  size="small"
                  :icon="Bell"
                  :loading="busyDeviceId === row.device.device_id"
                  :disabled="!canSendDeviceCommand(row.device)"
                  @click="onIdentify(row.device)"
                >
                  点名
                </el-button>
              </span>
            </el-tooltip>
            <el-button
              size="small"
              :type="primaryAction(row).type"
              :icon="primaryAction(row).icon"
              :loading="busyDeviceId === row.device.device_id"
              @click="primaryAction(row).run()"
            >
              {{ primaryAction(row).label }}
            </el-button>
            <el-dropdown size="small" trigger="click" @command="(cmd: string) => onMoreCommand(cmd, row.device)">
              <el-button size="small">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="detail">Overview</el-dropdown-item>
                  <el-dropdown-item command="connection">Connection</el-dropdown-item>
                  <el-dropdown-item command="raw">Raw JSON</el-dropdown-item>
                  <el-dropdown-item command="identify" :disabled="!canSendDeviceCommand(row.device)">点名</el-dropdown-item>
                  <el-dropdown-item command="refresh-config" :disabled="!canSendDeviceCommand(row.device)">Refresh config</el-dropdown-item>
                  <el-dropdown-item :command="row.device.enabled ? 'disable' : 'enable'">
                    {{ row.device.enabled ? 'Disable' : 'Enable' }}
                  </el-dropdown-item>
                  <el-dropdown-item divided command="forget">Forget device</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="!loading && filteredRows.length === 0" class="empty">
      {{ lastDiscovery?.registered ? '等待物理设备请求配置' : '等待 Hub mDNS 广播恢复' }}
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
          <div class="readiness-panel">
            <StatusBadge
              :state="registryState(detail).state"
              :label="registryState(detail).label"
            />
            <StatusBadge
              :state="reachabilityState(detail).state"
              :label="reachabilityState(detail).label"
            />
            <span class="muted">{{ registryState(detail).reason }} · {{ reachabilityState(detail).reason }}</span>
          </div>
          <div class="checklist">
            <div v-for="item in lifecycleChecks(detail)" :key="item.label" class="check-row">
              <el-tag size="small" :type="item.ok ? 'success' : 'info'" effect="dark">
                {{ item.ok ? 'OK' : '—' }}
              </el-tag>
              <span class="check-label">{{ item.label }}</span>
              <span class="mono">{{ item.value }}</span>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="Connection" name="connection">
          <div class="detail-grid">
            <div class="detail-box"><span class="label">Runtime status</span><span class="mono">{{ detail.status || 'unknown' }}</span></div>
            <div class="detail-box"><span class="label">Session</span><span class="mono">{{ sessionState(detail).label }}</span></div>
            <div class="detail-box"><span class="label">Device IP</span><span class="mono">{{ lastIpText(detail) }}</span></div>
            <div class="detail-box"><span class="label">Last seen</span><span class="mono">{{ formatTimestamp(detail.last_seen) }}</span></div>
            <div class="detail-box"><span class="label">Missed probes</span><span class="mono">{{ detail.missed_probes ?? 0 }}</span></div>
            <div class="detail-box wide"><span class="label">Room</span><span class="mono">{{ detail.room_name || 'none' }}</span></div>
            <div class="detail-box wide"><span class="label">Participant</span><span class="mono">{{ detail.participant_sid || 'none' }}</span></div>
            <div class="detail-box wide"><span class="label">Reachability</span><span class="muted">{{ reachabilityText(detail) }}</span></div>
            <div v-if="!canSendDeviceCommand(detail)" class="detail-box wide"><span class="label">Command blocked</span><span class="muted">{{ commandUnavailableReason(detail) }}</span></div>
            <div class="detail-box"><span class="label">Enabled</span><span>{{ detail.enabled ? 'yes' : 'no' }}</span></div>
            <div class="detail-box"><span class="label">Approved</span><span>{{ detail.approved ? 'yes' : 'no' }}</span></div>
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
.device-cell, .state-cell { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.inline-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.row-actions { display: flex; justify-content: flex-end; gap: 8px; }
.button-wrap { display: inline-flex; }
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
.readiness-panel {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}
.checklist { display: flex; flex-direction: column; border-top: 1px solid var(--eid-border); }
.check-row {
  display: grid;
  grid-template-columns: 52px minmax(120px, 1fr) minmax(0, 2fr);
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--eid-border);
}
.check-label { font-size: 13px; font-weight: 600; }
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
