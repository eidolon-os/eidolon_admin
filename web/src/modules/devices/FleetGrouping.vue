<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, Connection, MagicStick, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getFleet, wakeDevice, type FleetResponse } from '@/api/devices'
import { identifyOwnerDevice, wiggleOwnerDevice } from '@/api/eidolonData'
import type { RuntimeDevice } from '@/api/missionControl'
import {
  devicePresenceClass,
  devicePresenceLabel,
  deviceShort,
  deviceType,
  isPreparedWebBody,
} from '@/modules/mission-control/format'
import { webBodyLaunchUrl } from '@/utils/clientWeb'

type StatusFilter = 'all' | 'ready' | 'attention' | 'offline'
type KindFilter = 'all' | 'web' | 'physical' | 'security'
type GroupMode = 'companion' | 'kind'

interface BodyItem {
  device: RuntimeDevice
  companionId: string
  companionName: string
}

interface BodyGroup {
  key: string
  label: string
  hint: string
  items: BodyItem[]
}

const props = defineProps<{ ownerId: string }>()
const router = useRouter()
const fleet = ref<FleetResponse | null>(null)
const loading = ref(false)
const statusFilter = ref<StatusFilter>('all')
const kindFilter = ref<KindFilter>('all')
const groupMode = ref<GroupMode>('companion')
const startingId = ref('')
const identifyingId = ref('')
const wigglingId = ref('')

const allItems = computed<BodyItem[]>(() => {
  const grouped = (fleet.value?.groups || []).flatMap((group) => group.devices.map((device) => ({
    device,
    companionId: group.companion_id,
    companionName: group.companion_name,
  })))
  const unbound = (fleet.value?.unbound || []).map((device) => ({
    device,
    companionId: '',
    companionName: '未绑定 Companion',
  }))
  return [...grouped, ...unbound]
})

const stats = computed(() => ({
  total: allItems.value.length,
  ready: allItems.value.filter((item) => productState(item) === 'ready').length,
  attention: allItems.value.filter((item) => productState(item) === 'attention').length,
  offline: allItems.value.filter((item) => productState(item) === 'offline').length,
}))

const visibleItems = computed(() => allItems.value.filter((item) => {
  if (statusFilter.value !== 'all' && productState(item) !== statusFilter.value) return false
  return kindFilter.value === 'all' || productKind(item.device) === kindFilter.value
}))

const visibleGroups = computed<BodyGroup[]>(() => {
  const groups = new Map<string, BodyGroup>()
  for (const item of visibleItems.value) {
    const kind = productKind(item.device)
    const key = groupMode.value === 'companion' ? (item.companionId || 'unbound') : kind
    const label = groupMode.value === 'companion' ? item.companionName : kindLabel(kind)
    const hint = groupMode.value === 'companion'
      ? (item.companionId ? 'Companion bodies' : '需要选择一个 Companion')
      : kindHint(kind)
    const group = groups.get(key) || { key, label, hint, items: [] }
    group.items.push(item)
    groups.set(key, group)
  }
  return [...groups.values()]
})

async function load() {
  if (!props.ownerId) {
    fleet.value = null
    return
  }
  loading.value = true
  try {
    fleet.value = await getFleet(props.ownerId)
  } catch {
    fleet.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.ownerId, load)

function isWebBody(device: RuntimeDevice): boolean {
  return String(device.kind || '').toLowerCase() === 'web'
}

function productKind(device: RuntimeDevice): Exclude<KindFilter, 'all'> {
  // Security is a role from the bound guard companion, not a hardware trait.
  if (String(device.role_kind || '') === 'guard') return 'security'
  if (isWebBody(device) || String(device.kind || '').toLowerCase().includes('virtual')) return 'web'
  return 'physical'
}

function productState(item: BodyItem): Exclude<StatusFilter, 'all'> {
  const device = item.device
  if (!item.companionId || device.status === 'degraded' || device.status === 'unknown') return 'attention'
  if (device.online || isPreparedWebBody(device)) return 'ready'
  if (device.status === 'offline') return 'offline'
  return 'attention'
}

function kindLabel(kind: Exclude<KindFilter, 'all'>): string {
  return ({ web: 'Web Body', physical: '物理设备', security: '安全设备' })[kind]
}

function kindHint(kind: Exclude<KindFilter, 'all'>): string {
  return ({
    web: '本机网页端入口，不需要 Hub 批准',
    physical: '通过 Hub 接入的物理身体',
    security: '具有 Guard 能力的身份安全设备',
  })[kind]
}

function sourceLabel(device: RuntimeDevice): string {
  const source = String(device.signals?.source || '')
  if (source === 'data') return 'Owner 数据'
  if (source === 'hub+data') return 'Owner + Hub'
  if (source === 'hub') return 'Hub'
  return source || '未知来源'
}

function bodyHint(item: BodyItem): string {
  const device = item.device
  if (!item.companionId) return '已认领，等待绑定 Companion'
  if (isPreparedWebBody(device)) return '网页端入口已准备，可直接启动'
  if (device.online) return device.room_name ? `已进入 ${device.room_name}` : '运行时在线'
  if (device.status === 'degraded') return '连接不稳定，需要检查'
  if (device.status === 'offline') return '当前离线'
  return '等待运行时连接'
}

function launchBody(item: BodyItem) {
  if (!props.ownerId || !item.companionId || !isWebBody(item.device)) return
  window.open(webBodyLaunchUrl({
    ownerId: props.ownerId,
    companionId: item.companionId,
    deviceId: item.device.device_id,
  }), '_blank', 'noopener')
}

function canStartSession(item: BodyItem): boolean {
  const device = item.device
  return (
    !!item.companionId
    && productKind(device) === 'physical'
    && device.approved
    && device.online
    && !!device.participant_sid
    && !!device.room_name
    && device.room_name.endsWith('-control')
  )
}

function canIdentify(item: BodyItem): boolean {
  const device = item.device
  return (
    productKind(device) !== 'web'
    && device.approved
    && device.online
    && !!device.participant_sid
    && !!device.room_name
  )
}

async function identify(item: BodyItem) {
  if (!canIdentify(item)) return
  identifyingId.value = item.device.device_id
  try {
    await identifyOwnerDevice(props.ownerId, item.device.device_id)
    ElMessage.success('已下发点名命令')
  } catch (error: any) {
    ElMessage.error(`点名失败: ${error?.response?.data?.detail || error?.message || 'unknown error'}`)
  } finally {
    identifyingId.value = ''
  }
}

async function wiggle(item: BodyItem) {
  if (!canIdentify(item)) return
  wigglingId.value = item.device.device_id
  try {
    await wiggleOwnerDevice(props.ownerId, item.device.device_id)
    ElMessage.success('已下发动一动命令')
  } catch (error: any) {
    ElMessage.error(`动一动失败: ${error?.response?.data?.detail || error?.message || 'unknown error'}`)
  } finally {
    wigglingId.value = ''
  }
}

async function startSession(item: BodyItem) {
  if (!canStartSession(item)) return
  startingId.value = item.device.device_id
  try {
    await wakeDevice(item.device.device_id)
    ElMessage.success('已下发会话启动命令')
    await load()
  } catch (error: any) {
    ElMessage.error(`启动失败: ${error?.response?.data?.detail || error?.message || 'unknown error'}`)
  } finally {
    startingId.value = ''
  }
}

function goConnect() {
  router.push({
    name: 'devices',
    params: { section: 'connect' },
    query: { owner_id: props.ownerId },
  })
}

function goDetail(item: BodyItem) {
  router.push({
    name: 'device-detail',
    params: { deviceId: item.device.device_id },
    query: { owner_id: props.ownerId },
  })
}
</script>

<template>
  <div class="fleet-overview" v-loading="loading">
    <div class="summary-grid">
      <button :class="{ active: statusFilter === 'all' }" @click="statusFilter = 'all'"><span>全部设备</span><strong>{{ stats.total }}</strong></button>
      <button :class="{ active: statusFilter === 'ready' }" @click="statusFilter = 'ready'"><span>已就绪</span><strong>{{ stats.ready }}</strong></button>
      <button :class="{ active: statusFilter === 'attention' }" @click="statusFilter = 'attention'"><span>待处理</span><strong>{{ stats.attention }}</strong></button>
      <button :class="{ active: statusFilter === 'offline' }" @click="statusFilter = 'offline'"><span>离线</span><strong>{{ stats.offline }}</strong></button>
    </div>

    <div class="toolbar">
      <div class="filters">
        <el-select v-model="kindFilter" size="small" style="width: 150px" aria-label="设备类型">
          <el-option label="全部类型" value="all" />
          <el-option label="Web Body" value="web" />
          <el-option label="物理设备" value="physical" />
          <el-option label="安全设备" value="security" />
        </el-select>
        <el-radio-group v-model="groupMode" size="small">
          <el-radio-button value="companion">按 Companion</el-radio-button>
          <el-radio-button value="kind">按类型</el-radio-button>
        </el-radio-group>
      </div>
      <el-button type="primary" :icon="Connection" @click="goConnect">接入设备</el-button>
    </div>

    <div class="group-grid">
      <article v-for="group in visibleGroups" :key="group.key" class="body-group">
        <header>
          <div><strong>{{ group.label }}</strong><span>{{ group.hint }}</span></div>
          <em>{{ group.items.length }} 个身体</em>
        </header>
        <ul>
          <li v-for="item in group.items" :key="item.device.device_id">
            <i class="dot" :class="'st-' + devicePresenceClass(item.device)" />
            <div class="body-main">
              <strong>{{ item.device.name || deviceShort(item.device) }}</strong>
              <span>{{ deviceType(item.device) }} · {{ devicePresenceLabel(item.device) }} · {{ sourceLabel(item.device) }} · <span class="mode" :class="{ 'mode-full': item.device.interaction_mode === 'full_duplex', 'mode-null': !item.device.interaction_mode }">{{ item.device.interaction_mode || 'null' }}</span></span>
              <em>{{ groupMode === 'kind' ? `${item.companionName} · ` : '' }}{{ bodyHint(item) }}</em>
            </div>
            <div class="row-actions">
              <el-button size="small" text @click="goDetail(item)">详情</el-button>
              <el-button v-if="canIdentify(item)" size="small" :icon="Bell" :loading="identifyingId === item.device.device_id" @click="identify(item)">点名</el-button>
              <el-button v-if="canIdentify(item)" size="small" :icon="MagicStick" :loading="wigglingId === item.device.device_id" @click="wiggle(item)">动一动</el-button>
              <el-button v-if="isWebBody(item.device) && item.companionId" size="small" type="primary" plain :icon="VideoPlay" @click="launchBody(item)">启动</el-button>
              <el-button v-else-if="canStartSession(item)" size="small" type="primary" :icon="VideoPlay" :loading="startingId === item.device.device_id" @click="startSession(item)">Start session</el-button>
              <el-button v-else-if="!item.companionId" size="small" type="warning" plain @click="goConnect">去绑定</el-button>
            </div>
          </li>
        </ul>
      </article>
      <el-empty v-if="!visibleGroups.length" description="当前筛选下没有设备" />
    </div>
  </div>
</template>

<style scoped>
.fleet-overview { display: flex; flex-direction: column; gap: 14px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.summary-grid button { display: flex; min-height: 76px; padding: 14px; align-items: flex-end; justify-content: space-between; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); color: var(--eid-text-secondary); cursor: pointer; text-align: left; }
.summary-grid button.active { border-color: var(--el-color-primary); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 40%, transparent); }
.summary-grid span { font-size: 12px; }
.summary-grid strong { color: var(--eid-text-primary); font-family: var(--eid-font-mono); font-size: 24px; }
.toolbar, .filters { display: flex; align-items: center; gap: 10px; }
.toolbar { justify-content: space-between; padding: 12px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.group-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 12px; }
.body-group { padding: 14px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.body-group > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.body-group > header div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
.body-group > header strong { color: var(--eid-text-primary); font-size: 14px; }
.body-group > header span, .body-group > header em { color: var(--eid-text-muted); font-size: 10px; font-style: normal; }
.body-group ul { display: grid; margin: 0; padding: 0; gap: 10px; list-style: none; }
.body-group li { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; align-items: center; gap: 9px; padding-top: 10px; border-top: 1px solid var(--eid-border); }
.body-main { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.body-main strong, .body-main span, .body-main em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.body-main strong { color: var(--eid-text-primary); font-size: 12px; }
.body-main span { color: var(--eid-text-secondary); font-size: 11px; }
.body-main em { color: var(--eid-text-muted); font-size: 10px; font-style: normal; }
.body-main .mode { font-family: var(--eid-font-mono); }
.body-main .mode-full { color: var(--eid-success); }
.body-main .mode-null { color: var(--eid-text-muted); font-style: italic; }
.row-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 4px; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--eid-text-muted); }
.dot.st-ok { background: var(--eid-success); box-shadow: 0 0 6px var(--eid-success); }
.dot.st-warn { background: var(--eid-warning); box-shadow: 0 0 6px var(--eid-warning); }
.dot.st-bad { background: var(--eid-danger); box-shadow: 0 0 6px var(--eid-danger); }
@media (max-width: 760px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } .toolbar { align-items: stretch; flex-direction: column; } .filters { align-items: stretch; flex-direction: column; } .group-grid { grid-template-columns: 1fr; } }
</style>
