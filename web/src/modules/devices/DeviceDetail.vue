<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Bell, Link, MagicStick, VideoPlay } from '@element-plus/icons-vue'
import { useOwnersStore } from '@/stores/owners'
import {
  bindOwnerDevice,
  identifyOwnerDevice,
  wiggleOwnerDevice,
  listOwnerCompanions,
  listOwnerDevices,
  listOwnerGuardBindings,
  type CompanionView,
  type DeviceView as OwnerDeviceView,
  type GuardBindingView,
} from '@/api/eidolonData'
import { listDevices, wakeDevice, type DeviceView as HubDeviceView } from '@/api/devices'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const ownersStore = useOwnersStore()
const ownerId = computed(() => ownersStore.currentId)
const deviceId = computed(() => String(route.params.deviceId || ''))
const ownerDevice = ref<OwnerDeviceView | null>(null)
const hubDevice = ref<HubDeviceView | null>(null)
const companions = ref<CompanionView[]>([])
const guardBindings = ref<GuardBindingView[]>([])
const companionId = ref('')
const loading = ref(false)
const saving = ref(false)
const starting = ref(false)
const identifying = ref(false)
const wiggling = ref(false)

// Guard is a role carried by the bound guard companion, expressed through a
// live guard binding — not by the device's hardware kind or capability flag.
const isGuard = computed(() => guardBindings.value.some(
  (binding) => binding.device_id === deviceId.value
    && !binding.revoked_at
    && binding.state !== 'revoked'
    && binding.state !== 'replaced',
))

const productState = computed(() => {
  if (!hubDevice.value && ownerDevice.value?.kind === 'web') return ownerDevice.value.bound_companion_id ? '已准备' : '待绑定'
  if (!hubDevice.value?.approved) return '待 Hub 批准'
  if (!ownerDevice.value?.owner_id) return '待认领'
  if (!ownerDevice.value.bound_companion_id) return '待绑定'
  if (hubDevice.value.status === 'offline') return '离线'
  if (hubDevice.value.status === 'degraded') return '连接不稳定'
  return '已就绪'
})

const canStartSession = computed(() => {
  const device = hubDevice.value
  return Boolean(
    ownerDevice.value?.bound_companion_id
    && !isGuard.value
    && device?.enabled
    && device.approved
    && device.status === 'online'
    && device.participant_sid
    && device.room_name?.endsWith('-control'),
  )
})

const sessionActive = computed(() => Boolean(
  hubDevice.value?.status === 'online'
  && hubDevice.value.room_name
  && !hubDevice.value.room_name.endsWith('-control'),
))

const canIdentify = computed(() => {
  const device = hubDevice.value
  return Boolean(
    ownerDevice.value
    && device?.enabled
    && device.approved
    && device.status === 'online'
    && device.participant_sid
    && device.room_name,
  )
})

onMounted(load)
watch([ownerId, deviceId], load)

async function load() {
  if (!ownerId.value || !deviceId.value) return
  loading.value = true
  try {
    const [ownerRows, companionRows, hub, bindings] = await Promise.all([
      listOwnerDevices(ownerId.value),
      listOwnerCompanions(ownerId.value),
      listDevices().catch(() => ({ devices: [], hub_available: false, discovery: null })),
      listOwnerGuardBindings(ownerId.value).catch(() => [] as GuardBindingView[]),
    ])
    ownerDevice.value = ownerRows.find((item) => item.device_id === deviceId.value) || null
    hubDevice.value = hub.devices.find((item) => item.device_id === deviceId.value) || null
    guardBindings.value = bindings
    companions.value = companionRows.filter((item) => item.kind !== 'guard' && item.companion_type !== 'guard')
    companionId.value = ownerDevice.value?.bound_companion_id || ''
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function back() {
  router.push({ name: 'devices', params: { section: 'overview' }, query: { owner_id: ownerId.value || undefined } })
}

function goConnect() {
  router.push({ name: 'devices', params: { section: 'connect' }, query: { owner_id: ownerId.value || undefined } })
}

function goSecurity() {
  router.push({ name: 'identity-security', query: { owner_id: ownerId.value || undefined } })
}

async function saveBinding() {
  if (!ownerId.value || !ownerDevice.value) return
  saving.value = true
  try {
    ownerDevice.value = await bindOwnerDevice(ownerId.value, deviceId.value, companionId.value || null)
    ElMessage.success(companionId.value ? 'Companion 绑定已更新' : '已解绑 Companion')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    saving.value = false
  }
}

async function startSession() {
  if (!hubDevice.value || !canStartSession.value) return
  starting.value = true
  try {
    await wakeDevice(hubDevice.value.device_id)
    ElMessage.success('已下发会话启动命令')
    await load()
  } catch (error) {
    ElMessage.error(`启动失败: ${extractErrorMessage(error)}`)
  } finally {
    starting.value = false
  }
}

async function identify() {
  if (!ownerId.value || !canIdentify.value) return
  identifying.value = true
  try {
    await identifyOwnerDevice(ownerId.value, deviceId.value)
    ElMessage.success('已下发点名命令')
  } catch (error) {
    ElMessage.error(`点名失败: ${extractErrorMessage(error)}`)
  } finally {
    identifying.value = false
  }
}

async function wiggle() {
  if (!ownerId.value || !canIdentify.value) return
  wiggling.value = true
  try {
    await wiggleOwnerDevice(ownerId.value, deviceId.value)
    ElMessage.success('已下发动一动命令')
  } catch (error) {
    ElMessage.error(`动一动失败: ${extractErrorMessage(error)}`)
  } finally {
    wiggling.value = false
  }
}
</script>

<template>
  <section class="device-detail" v-loading="loading">
    <header class="page-head">
      <el-button text :icon="ArrowLeft" @click="back">返回设备</el-button>
      <div class="title-row">
        <div><p>DEVICE</p><h1>{{ ownerDevice?.name || hubDevice?.name || deviceId }}</h1><code>{{ deviceId }}</code></div>
        <el-tag :type="productState === '已就绪' || productState === '已准备' ? 'success' : 'warning'">{{ productState }}</el-tag>
      </div>
    </header>

    <el-alert v-if="isGuard" type="info" :closable="false" title="这是安全设备，Guard 绑定与 Owner Face 请在“身份与安全”中管理。" show-icon>
      <template #default><el-button size="small" @click="goSecurity">前往身份与安全</el-button></template>
    </el-alert>

    <div class="detail-grid">
      <section class="panel">
        <header><h2>归属与绑定</h2><p>Owner 认领与 Companion 绑定是两个独立关系。</p></header>
        <template v-if="ownerDevice">
          <el-form label-position="top">
            <el-form-item label="所属 Eidolon"><el-input :model-value="ownerDevice.owner_id || '未认领'" disabled /></el-form-item>
            <el-form-item label="Companion">
              <el-select v-model="companionId" clearable filterable placeholder="尚未绑定" style="width: 100%">
                <el-option v-for="item in companions" :key="item.companion_id" :label="item.display_name || item.companion_id" :value="item.companion_id" />
              </el-select>
            </el-form-item>
            <el-button type="primary" :icon="Link" :loading="saving" @click="saveBinding">保存绑定</el-button>
          </el-form>
        </template>
        <el-empty v-else description="设备尚未认领到当前 Eidolon">
          <el-button type="primary" @click="goConnect">继续接入</el-button>
        </el-empty>
      </section>

      <section class="panel">
        <header class="runtime-head">
          <div><h2>运行状态</h2><p>Hub 可达性与当前会话信息。</p></div>
          <div class="runtime-actions">
            <el-tag v-if="sessionActive" type="success" effect="dark">Session active</el-tag>
            <el-button v-if="canIdentify" :icon="Bell" :loading="identifying" @click="identify">点名</el-button>
            <el-button v-if="canIdentify" :icon="MagicStick" :loading="wiggling" @click="wiggle">动一动</el-button>
            <el-button v-if="canStartSession" type="primary" :icon="VideoPlay" :loading="starting" @click="startSession">Start session</el-button>
          </div>
        </header>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="类型">{{ ownerDevice?.kind || hubDevice?.kind || 'unknown' }}</el-descriptions-item>
          <el-descriptions-item label="Hub 批准">{{ hubDevice ? (hubDevice.approved ? '已批准' : '待批准') : '不适用' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ hubDevice?.status || ownerDevice?.status || 'unknown' }}</el-descriptions-item>
          <el-descriptions-item label="Room">{{ hubDevice?.room_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="交互模式">
            <span class="mode" :class="{ 'mode-full': (hubDevice?.interaction_mode || ownerDevice?.interaction_mode) === 'full_duplex', 'mode-null': !(hubDevice?.interaction_mode || ownerDevice?.interaction_mode) }">{{ hubDevice?.interaction_mode || ownerDevice?.interaction_mode || 'null' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="最近在线">{{ formatTimestamp(hubDevice?.last_seen || ownerDevice?.last_seen_at) }}</el-descriptions-item>
        </el-descriptions>
      </section>
    </div>

    <section v-if="ownerDevice" class="panel">
      <header><h2>能力与接入信息</h2><p>调试字段只在详情页展示，不参与主导航分类。</p></header>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="认证类型">{{ ownerDevice.auth_type || '—' }}</el-descriptions-item>
        <el-descriptions-item label="Capabilities" :span="2"><code>{{ JSON.stringify(ownerDevice.capabilities_json) }}</code></el-descriptions-item>
      </el-descriptions>
    </section>
  </section>
</template>

<style scoped>
.device-detail { display: flex; width: min(1080px, 100%); margin: 0 auto; padding-bottom: 32px; flex-direction: column; gap: 14px; }
.mode { font-family: var(--eid-font-mono); }
.mode-full { color: var(--eid-success); }
.mode-null { color: var(--eid-text-muted); font-style: italic; }
.page-head, .panel { padding: 16px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-top: 10px; }
.title-row p { margin: 0; color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: .12em; }
.title-row h1 { margin: 4px 0; color: var(--eid-text-primary); font-size: 24px; }
.title-row code { color: var(--eid-text-muted); font-size: 10px; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.panel > header { margin-bottom: 14px; }
.runtime-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.runtime-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.panel h2 { margin: 0; color: var(--eid-text-primary); font-size: 16px; }
.panel header p { margin: 4px 0 0; color: var(--eid-text-muted); font-size: 11px; }
.panel code { color: var(--eid-text-secondary); font-size: 10px; overflow-wrap: anywhere; }
@media (max-width: 760px) { .detail-grid { grid-template-columns: 1fr; } }
</style>
