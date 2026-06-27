<script setup lang="ts">
// Hub > Devices is the product entry for device access/reachability. The data
// and business orchestration still come from Admin's /api/devices aggregate:
// Hub device facts + Admin binding + Agent metadata.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveDevice,
  bindDevice,
  listDevices,
  setDeviceEnabled,
  unbindDevice,
  unregisterDevice,
  wakeDevice,
  type DeviceListResponse,
  type DeviceView,
} from '@/api/devices'
import { listAgents, type AgentRef } from '@/api/agents'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import JsonViewer from '@/modules/common/JsonViewer.vue'

type RuntimeFilter = 'all' | 'online' | 'degraded' | 'offline' | 'unknown'

const status = ref<RuntimeFilter>('all')
const devices = ref<DeviceView[]>([])
const agents = ref<AgentRef[]>([])
const hubAvailable = ref(true)
const loading = ref(false)
const detail = ref<DeviceView | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const bindDialogOpen = ref(false)
const bindTarget = ref<DeviceView | null>(null)
const bindAgentId = ref('')
const submitting = ref(false)
const togglingDeviceId = ref('')

const filteredDevices = computed(() => {
  if (status.value === 'all') return devices.value
  return devices.value.filter((d) => (d.status || 'unknown') === status.value)
})

const pendingCount = computed(() => devices.value.filter((d) => !d.approved).length)
const unboundCount = computed(() => devices.value.filter((d) => d.approved && !d.binding).length)
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
    const [d, a] = await Promise.all([listDevices(), listAgents()])
    devices.value = d.devices
    hubAvailable.value = d.hub_available
    lastDiscovery.value = d.discovery
    agents.value = a.agents
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

function badgeState(s?: string): 'online' | 'offline' | 'warning' | 'unknown' {
  if (s === 'online') return 'online'
  if (s === 'degraded') return 'warning'
  if (s === 'offline') return 'offline'
  return 'unknown'
}

function inControlRoom(d: DeviceView): boolean {
  return d.status === 'online' && !!d.room_name && d.room_name.endsWith('-control')
}

function inVoiceRoom(d: DeviceView): boolean {
  return d.status === 'online' && !!d.room_name && !d.room_name.endsWith('-control')
}

function productState(d: DeviceView): { label: string; state: 'online' | 'offline' | 'warning' | 'unknown' } {
  if (!d.enabled) return { label: '已停用', state: 'offline' }
  if (!d.approved) return { label: '待批准', state: 'warning' }
  if (!d.binding) return { label: '待绑定', state: 'warning' }
  if (inVoiceRoom(d)) return { label: '通话中', state: 'online' }
  if (inControlRoom(d)) return { label: '待机在线', state: 'online' }
  if (d.status === 'degraded') return { label: '连接不稳', state: 'warning' }
  if (d.status === 'offline') return { label: '离线', state: 'offline' }
  return { label: d.status || '未知', state: 'unknown' }
}

function pairingTag(d: DeviceView): { label: string; type: 'success' | 'warning' | 'info' | 'danger' } {
  if (!d.enabled) return { label: 'disabled', type: 'info' }
  if (!d.approved) return { label: 'pending', type: 'warning' }
  if (d.binding) return { label: 'bound', type: 'success' }
  return { label: 'unbound', type: 'info' }
}

async function onWake(d: DeviceView) {
  try {
    await wakeDevice(d.device_id)
    ElMessage.success('已下发唤醒')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`唤醒失败: ${extractErrorMessage(e)}`)
  }
}

function agentLabel(agentId: string): string {
  const a = agents.value.find((x) => x.agent_id === agentId)
  if (!a) return agentId.slice(0, 12) + '...'
  return `${a.display_name || a.agent_id.slice(0, 8)} (${a.user_id})`
}

async function onApprove(d: DeviceView) {
  try {
    await approveDevice(d.device_id)
    ElMessage.success(`已批准 ${d.device_id}`)
    await refresh()
  } catch (e: any) {
    ElMessage.error(`批准失败: ${extractErrorMessage(e)}`)
  }
}

async function onToggleEnabled(d: DeviceView, enabled: boolean | string | number) {
  const nextEnabled = Boolean(enabled)
  if (nextEnabled === d.enabled) return
  const title = nextEnabled ? '启用设备' : '停用设备'
  const message = nextEnabled
    ? `确认启用 ${d.device_id}? 启用后可批准、绑定并参与运行时调度。`
    : `确认停用 ${d.device_id}? 设备记录和绑定会保留，但不会参与运行时使用。`
  try {
    await ElMessageBox.confirm(message, title, { type: nextEnabled ? 'info' : 'warning' })
  } catch {
    return
  }
  togglingDeviceId.value = d.device_id
  try {
    await setDeviceEnabled(d.device_id, nextEnabled)
    ElMessage.success(nextEnabled ? '已启用' : '已停用')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`${nextEnabled ? '启用' : '停用'}失败: ${extractErrorMessage(e)}`)
  } finally {
    togglingDeviceId.value = ''
  }
}

function onEnabledSwitchChange(d: DeviceView, value: boolean | string | number) {
  void onToggleEnabled(d, value)
}

function openBind(d: DeviceView) {
  bindTarget.value = d
  bindAgentId.value = d.binding?.agent_id || agents.value[0]?.agent_id || ''
  bindDialogOpen.value = true
}

async function submitBind() {
  if (!bindTarget.value || !bindAgentId.value) {
    ElMessage.warning('请选择 agent')
    return
  }
  submitting.value = true
  try {
    await bindDevice(bindTarget.value.device_id, bindAgentId.value)
    ElMessage.success('已绑定')
    bindDialogOpen.value = false
    await refresh()
  } catch (e: any) {
    ElMessage.error(`绑定失败: ${extractErrorMessage(e)}`)
  } finally {
    submitting.value = false
  }
}

async function onUnbind(d: DeviceView) {
  try {
    await ElMessageBox.confirm(`确认解绑 ${d.device_id}?`, '解绑', { type: 'warning' })
  } catch {
    return
  }
  try {
    await unbindDevice(d.device_id)
    ElMessage.success('已解绑')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`解绑失败: ${extractErrorMessage(e)}`)
  }
}

async function onUnregister(d: DeviceView) {
  try {
    await ElMessageBox.confirm(
      `确认注销 ${d.device_id}? 这会清除 hub 记录和 admin 绑定；设备重新发现后会回到待批准状态。`,
      '注销 / 撤销设备',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await unregisterDevice(d.device_id)
    ElMessage.success('已注销，设备重新发现后需重新批准')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`注销失败: ${extractErrorMessage(e)}`)
  }
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Devices</h2>
        <div class="subtitle">
          {{ devices.length }} 设备 · 10 秒自动刷新
          <span v-if="!hubAvailable" class="warn">Hub 不可达, 状态可能过期</span>
        </div>
      </div>
      <div class="actions">
        <el-tag v-if="pendingCount > 0" size="small" type="warning" effect="dark">
          {{ pendingCount }} 待批准
        </el-tag>
        <el-tag v-if="unboundCount > 0" size="small" type="info">
          {{ unboundCount }} 待绑定
        </el-tag>
        <el-radio-group v-model="status" size="small">
          <el-radio-button value="all">全部</el-radio-button>
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
      <span v-if="lastDiscovery?.config_url" class="mono">
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
      <el-table-column label="Device ID" min-width="180">
        <template #default="{ row }"><code class="mono">{{ row.device_id }}</code></template>
      </el-table-column>
      <el-table-column label="名称" prop="name" min-width="140" />
      <el-table-column label="类型" prop="kind" width="100" />
      <el-table-column label="配对" width="110">
        <template #default="{ row }">
          <el-tag :type="pairingTag(row).type" size="small" effect="dark">
            {{ pairingTag(row).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="设备状态" width="130">
        <template #default="{ row }">
          <StatusBadge :state="productState(row).state" :label="productState(row).label" />
        </template>
      </el-table-column>
      <el-table-column label="启用" width="86" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            :loading="togglingDeviceId === row.device_id"
            @change="onEnabledSwitchChange(row, $event)"
          />
        </template>
      </el-table-column>
      <el-table-column label="实时通道" width="130">
        <template #default="{ row }">
          <StatusBadge :state="badgeState(row.status)" :label="row.status || 'unknown'" />
        </template>
      </el-table-column>
      <el-table-column label="绑定 Agent" min-width="220">
        <template #default="{ row }">
          <span v-if="row.binding" class="mono">{{ agentLabel(row.binding.agent_id) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="Last seen" width="170">
        <template #default="{ row }">
          <span class="muted">{{ formatTimestamp(row.last_seen) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="320" align="right">
        <template #default="{ row }">
          <el-button v-if="row.enabled && !row.approved" size="small" type="primary" @click="onApprove(row)">
            批准
          </el-button>
          <el-button
            v-if="row.enabled && row.approved && row.binding"
            size="small"
            type="primary"
            :icon="VideoPlay"
            :disabled="!inControlRoom(row)"
            @click="onWake(row)"
          >
            唤醒
          </el-button>
          <el-button v-if="row.enabled && row.approved" size="small" @click="openBind(row)">
            {{ row.binding ? '改绑' : '绑定 Agent' }}
          </el-button>
          <el-button
            v-if="row.binding"
            size="small"
            type="warning"
            link
            @click="onUnbind(row)"
          >
            解绑
          </el-button>
          <el-button size="small" link @click="detail = row">详情</el-button>
          <el-button size="small" type="danger" link @click="onUnregister(row)">
            注销/撤销
          </el-button>
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
      size="50%"
      direction="rtl"
    >
      <JsonViewer v-if="detail" :data="detail" />
    </el-drawer>

    <el-dialog v-model="bindDialogOpen" title="绑定 Agent" width="480px" :close-on-click-modal="false">
      <p class="dialog-hint">
        Device <code class="mono">{{ bindTarget?.device_id }}</code>
      </p>
      <el-select v-model="bindAgentId" placeholder="选择 agent" style="width: 100%">
        <el-option
          v-for="a in agents"
          :key="a.agent_id"
          :label="`${a.display_name || a.agent_id.slice(0, 8)} - ${a.user_id} / ${a.template_id}`"
          :value="a.agent_id"
        />
      </el-select>
      <template #footer>
        <el-button @click="bindDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitBind">绑定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.warn { color: var(--eid-warning); margin-left: 8px; }
.actions { display: flex; gap: 12px; align-items: center; }
.discovery-row { display: flex; gap: 10px; align-items: center; min-height: 30px; margin: -4px 0 12px; flex-wrap: wrap; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.empty { padding: 32px; text-align: center; color: var(--eid-text-muted); font-size: 12px; background: var(--eid-bg-panel); border: 1px dashed var(--eid-border); border-radius: var(--eid-radius); }
.dialog-hint { margin: 0 0 12px; font-size: 12px; color: var(--eid-text-muted); }
</style>
