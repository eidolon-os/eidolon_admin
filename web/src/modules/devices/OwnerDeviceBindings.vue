<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, CloseBold, Delete, Link, MagicStick, Plus, Refresh } from '@element-plus/icons-vue'
import {
  addNearbyDeviceToOwner,
  bindOwnerDevice,
  identifyNearbyDevice,
  identifyOwnerDevice,
  wiggleNearbyDevice,
  wiggleOwnerDevice,
  listNearbyOwnerDevices,
  listOwnerCompanions,
  listOwnerDevices,
  releaseOwnerDevice,
  updateOwnerDevice,
  type CompanionView,
  type DeviceView,
  type NearbyDeviceView,
} from '@/api/eidolonData'
import {
  approveDevice,
  getFleet,
  listDevices as listHubDevices,
  type DeviceView as HubDeviceView,
} from '@/api/devices'
import type { RuntimeDevice } from '@/api/missionControl'
import { devicePresenceLabel } from '@/modules/mission-control/format'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

const props = defineProps<{ ownerId: string }>()
const emit = defineEmits<{ changed: [] }>()
const router = useRouter()

const loading = ref(false)
const devices = ref<DeviceView[]>([])
const nearbyDevices = ref<NearbyDeviceView[]>([])
const companions = ref<CompanionView[]>([])
const hubDevices = ref<HubDeviceView[]>([])
// Live runtime presence keyed by device_id, sourced from the same fleet join as
// the "我的设备" view so the 状态 column agrees with it (data.status is a
// lifecycle field — "active" for any claimed device — not presence).
const runtimeById = ref<Record<string, RuntimeDevice>>({})
const hubAvailable = ref(true)
const actionOpen = ref(false)
const actionMode = ref<'claim' | 'bind'>('claim')
const actionDeviceId = ref('')
const actionName = ref('')
const actionCompanionId = ref('')
const actionLoading = ref(false)
const identifyingId = ref('')
const wigglingId = ref('')
const unbindingId = ref('')
const releasingId = ref('')
const approvingId = ref('')

const pendingApproval = computed(() => hubDevices.value.filter((device) => !device.approved))
const unboundCount = computed(() => devices.value.filter((device) => !device.bound_companion_id).length)
const readyCount = computed(() => devices.value.filter((device) => !!device.bound_companion_id).length)

onMounted(load)
watch(() => props.ownerId, load)

async function load() {
  if (!props.ownerId) {
    devices.value = []
    nearbyDevices.value = []
    companions.value = []
    return
  }
  loading.value = true
  try {
    const [owned, ownerCompanions] = await Promise.all([
      listOwnerDevices(props.ownerId),
      listOwnerCompanions(props.ownerId),
    ])
    devices.value = owned
    companions.value = ownerCompanions.filter((item) => item.kind !== 'guard' && item.companion_type !== 'guard')
    try {
      const fleet = await getFleet(props.ownerId)
      const map: Record<string, RuntimeDevice> = {}
      for (const group of fleet.groups) for (const device of group.devices) map[device.device_id] = device
      for (const device of fleet.unbound) map[device.device_id] = device
      runtimeById.value = map
    } catch {
      runtimeById.value = {}
    }
    try {
      const [nearby, hub] = await Promise.all([
        listNearbyOwnerDevices(props.ownerId),
        listHubDevices(),
      ])
      nearbyDevices.value = nearby.devices
      hubDevices.value = hub.devices
      hubAvailable.value = nearby.hub_available && hub.hub_available
    } catch {
      nearbyDevices.value = []
      hubDevices.value = []
      hubAvailable.value = false
    }
  } catch (error) {
    ElMessage.error(`加载设备绑定失败: ${extractErrorMessage(error)}`)
  } finally {
    loading.value = false
  }
}

async function refreshAfterApproval(deviceId: string) {
  await load()
  const device = nearbyDevices.value.find((item) => item.device_id === deviceId)
  if (device) {
    openClaim(device)
    return
  }
  ElMessage.info('设备已批准；请刷新接入列表继续认领，或检查它是否已属于其他 Eidolon')
}

defineExpose({ load, refreshAfterApproval })

// Live presence, consistent with the "我的设备" fleet view; falls back to the
// data lifecycle status only when the device is absent from the fleet join.
function presenceLabel(row: DeviceView): string {
  const runtime = runtimeById.value[row.device_id]
  return runtime ? devicePresenceLabel(runtime) : row.status
}

function companionLabel(companionId: string | null | undefined) {
  if (!companionId) return '未绑定'
  const companion = companions.value.find((item) => item.companion_id === companionId)
  return companion?.display_name || companionId
}

async function approveAndContinue(device: HubDeviceView) {
  approvingId.value = device.device_id
  try {
    await approveDevice(device.device_id)
    ElMessage.success('Hub 已批准；继续认领并绑定到当前 Eidolon')
    await refreshAfterApproval(device.device_id)
  } catch (error) {
    ElMessage.error(`批准失败: ${extractErrorMessage(error)}`)
  } finally {
    approvingId.value = ''
  }
}

function goHubDevices() {
  router.push({ name: 'hub-devices' })
}

function openClaim(device: NearbyDeviceView) {
  actionMode.value = 'claim'
  actionDeviceId.value = device.device_id
  actionName.value = device.name || device.device_id
  actionCompanionId.value = companions.value[0]?.companion_id || ''
  actionOpen.value = true
}

function openBind(device: DeviceView) {
  actionMode.value = 'bind'
  actionDeviceId.value = device.device_id
  actionName.value = device.name || device.device_id
  actionCompanionId.value = device.bound_companion_id || ''
  actionOpen.value = true
}

async function submitAction() {
  if (!props.ownerId || !actionDeviceId.value) return
  actionLoading.value = true
  try {
    const companionId = actionCompanionId.value || null
    if (actionMode.value === 'claim') {
      // interaction_mode is firmware-declared per board (X-Device-Interaction-Mode)
      // and is the sole source of truth — there is no admin override. Claiming a
      // device / binding a companion is orthogonal to turn-taking mode.
      await addNearbyDeviceToOwner(props.ownerId, actionDeviceId.value, {
        name: actionName.value.trim() || actionDeviceId.value,
        companion_id: companionId,
        access_policy_json: companionId
          ? { conversation: true, voice_input: true, voice_output: true, memory_recall: true }
          : {},
      })
      ElMessage.success(companionId ? '设备已认领并绑定 Companion' : '设备已认领到当前空间')
    } else {
      await updateOwnerDevice(props.ownerId, actionDeviceId.value, {
        name: actionName.value.trim() || null,
      })
      await bindOwnerDevice(props.ownerId, actionDeviceId.value, companionId)
      ElMessage.success(companionId ? '设备已绑定 Companion' : '设备已解绑 Companion')
    }
    actionOpen.value = false
    await load()
    emit('changed')
  } catch (error) {
    ElMessage.error(`设备操作失败: ${extractErrorMessage(error)}`)
  } finally {
    actionLoading.value = false
  }
}

async function unbind(device: DeviceView) {
  unbindingId.value = device.device_id
  try {
    await bindOwnerDevice(props.ownerId, device.device_id, null)
    ElMessage.success('设备已解绑 Companion')
    await load()
    emit('changed')
  } catch (error) {
    ElMessage.error(`解绑失败: ${extractErrorMessage(error)}`)
  } finally {
    unbindingId.value = ''
  }
}

async function release(device: DeviceView) {
  try {
    await ElMessageBox.confirm(
      `释放 ${device.name || device.device_id} 后，它会回到可认领设备列表。`,
      '释放设备',
      { type: 'warning', confirmButtonText: '释放' },
    )
  } catch {
    return
  }
  releasingId.value = device.device_id
  try {
    await releaseOwnerDevice(props.ownerId, device.device_id)
    ElMessage.success('设备已释放')
    await load()
    emit('changed')
  } catch (error) {
    ElMessage.error(`释放失败: ${extractErrorMessage(error)}`)
  } finally {
    releasingId.value = ''
  }
}

async function identifyOwned(device: DeviceView) {
  identifyingId.value = device.device_id
  try {
    await identifyOwnerDevice(props.ownerId, device.device_id)
    ElMessage.success('点名命令已下发')
  } catch (error) {
    ElMessage.error(`点名失败: ${extractErrorMessage(error)}`)
  } finally {
    identifyingId.value = ''
  }
}

async function identifyNearby(device: NearbyDeviceView) {
  identifyingId.value = device.device_id
  try {
    await identifyNearbyDevice(props.ownerId, device.device_id)
    ElMessage.success('点名命令已下发')
  } catch (error) {
    ElMessage.error(`点名失败: ${extractErrorMessage(error)}`)
  } finally {
    identifyingId.value = ''
  }
}

async function wiggleOwned(device: DeviceView) {
  wigglingId.value = device.device_id
  try {
    await wiggleOwnerDevice(props.ownerId, device.device_id)
    ElMessage.success('动一动命令已下发')
  } catch (error) {
    ElMessage.error(`动一动失败: ${extractErrorMessage(error)}`)
  } finally {
    wigglingId.value = ''
  }
}

async function wiggleNearby(device: NearbyDeviceView) {
  wigglingId.value = device.device_id
  try {
    await wiggleNearbyDevice(props.ownerId, device.device_id)
    ElMessage.success('动一动命令已下发')
  } catch (error) {
    ElMessage.error(`动一动失败: ${extractErrorMessage(error)}`)
  } finally {
    wigglingId.value = ''
  }
}
</script>

<template>
  <section class="binding-center" v-loading="loading">
    <header class="binding-head">
      <div>
        <span>OWNER DEVICE BINDINGS</span>
        <h3>设备认领与 Companion 绑定</h3>
        <p>按“批准 → 认领 → 绑定”完成物理设备接入；批准不是流程终点。</p>
      </div>
      <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
    </header>

    <div class="stage-strip">
      <div><span>1</span><strong>Hub 批准</strong><small>{{ pendingApproval.length }} 台待处理</small></div>
      <i>→</i>
      <div><span>2</span><strong>认领到空间</strong><small>{{ nearbyDevices.length }} 台待处理</small></div>
      <i>→</i>
      <div><span>3</span><strong>绑定 Companion</strong><small>{{ unboundCount }} 台待处理</small></div>
      <i>→</i>
      <div class="done"><span>4</span><strong>接入完成</strong><small>{{ readyCount }} 台已就绪</small></div>
    </div>

    <div class="binding-block pending">
      <div class="block-head">
        <div><strong>待 Hub 批准</strong><small>{{ pendingApproval.length }} 台</small></div>
        <el-button size="small" text @click="goHubDevices">打开 Hub 高级管理</el-button>
      </div>
      <el-table :data="pendingApproval" size="small" stripe>
        <template #empty>{{ hubAvailable ? '暂无待批准设备' : 'Hub unavailable' }}</template>
        <el-table-column label="设备" min-width="220">
          <template #default="{ row }"><div class="device-name"><strong>{{ row.name || row.device_id }}</strong><code>{{ row.device_id }}</code></div></template>
        </el-table-column>
        <el-table-column prop="kind" label="类型" width="110" />
        <el-table-column prop="status" label="运行状态" width="120" />
        <el-table-column label="最近发现" width="180"><template #default="{ row }">{{ formatTimestamp(row.last_seen) }}</template></el-table-column>
        <el-table-column label="下一步" width="150" align="right" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" :loading="approvingId === row.device_id" @click="approveAndContinue(row)">批准并继续</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="binding-block available">
      <div class="block-head">
        <div><strong>已批准、待认领</strong><small>{{ nearbyDevices.length }} 台</small></div>
        <el-tag size="small" :type="hubAvailable ? 'success' : 'warning'">{{ hubAvailable ? 'Hub online' : 'Hub unavailable' }}</el-tag>
      </div>
      <el-table :data="nearbyDevices" size="small" stripe>
        <template #empty>{{ hubAvailable ? '暂无已批准待认领设备' : 'Hub unavailable' }}</template>
        <el-table-column label="设备" min-width="210">
          <template #default="{ row }"><div class="device-name"><strong>{{ row.name || row.device_id }}</strong><code>{{ row.device_id }}</code></div></template>
        </el-table-column>
        <el-table-column prop="kind" label="类型" width="110" />
        <el-table-column prop="status" label="状态" width="110" />
        <el-table-column prop="room_name" label="Room" min-width="170" />
        <el-table-column label="最近在线" width="180"><template #default="{ row }">{{ formatTimestamp(row.last_seen) }}</template></el-table-column>
        <el-table-column label="下一步" width="190" align="right" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :icon="Bell" :loading="identifyingId === row.device_id" @click="identifyNearby(row)">点名</el-button>
            <el-button size="small" :icon="MagicStick" :loading="wigglingId === row.device_id" @click="wiggleNearby(row)">动一动</el-button>
            <el-button size="small" type="primary" :icon="Plus" @click="openClaim(row)">认领并绑定</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="binding-block">
      <div class="block-head">
        <div><strong>当前空间设备</strong><small>{{ devices.length }} 台</small></div>
      </div>
      <el-table :data="devices" size="small" stripe>
        <template #empty>当前空间还没有认领设备</template>
        <el-table-column label="设备" min-width="210">
          <template #default="{ row }"><div class="device-name"><strong>{{ row.name || row.device_id }}</strong><code>{{ row.device_id }}</code></div></template>
        </el-table-column>
        <el-table-column prop="kind" label="类型" width="110" />
        <el-table-column label="Companion" min-width="170">
          <template #default="{ row }"><el-tag :type="row.bound_companion_id ? 'success' : 'warning'" size="small">{{ companionLabel(row.bound_companion_id) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }">{{ presenceLabel(row) }}</template></el-table-column>
        <el-table-column label="最近在线" width="180"><template #default="{ row }">{{ formatTimestamp(row.last_seen_at) }}</template></el-table-column>
        <el-table-column label="操作" width="330" align="right" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :icon="Bell" :loading="identifyingId === row.device_id" @click="identifyOwned(row)">点名</el-button>
            <el-button size="small" :icon="MagicStick" :loading="wigglingId === row.device_id" @click="wiggleOwned(row)">动一动</el-button>
            <el-button size="small" type="primary" plain :icon="Link" @click="openBind(row)">{{ row.bound_companion_id ? '换绑' : '绑定' }}</el-button>
            <el-button size="small" :icon="CloseBold" :disabled="!row.bound_companion_id" :loading="unbindingId === row.device_id" @click="unbind(row)">解绑</el-button>
            <el-button size="small" type="danger" plain :icon="Delete" :loading="releasingId === row.device_id" @click="release(row)">释放</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="actionOpen" :title="actionMode === 'claim' ? '认领并绑定设备' : '绑定 Companion'" width="520px" append-to-body>
      <el-form label-position="top" @submit.prevent="submitAction">
        <el-form-item label="设备名称"><el-input v-model="actionName" /></el-form-item>
        <el-form-item label="Companion">
          <el-select v-model="actionCompanionId" clearable filterable placeholder="可先认领，稍后再绑定" style="width: 100%">
            <el-option v-for="companion in companions" :key="companion.companion_id" :label="companion.display_name || companion.companion_id" :value="companion.companion_id">
              <span>{{ companion.display_name || companion.companion_id }}</span>
              <small class="option-id">{{ companion.companion_id }}</small>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="actionOpen = false">取消</el-button>
        <el-button type="primary" :loading="actionLoading" @click="submitAction">{{ actionMode === 'claim' ? '认领并绑定' : '保存绑定' }}</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.binding-center { display: flex; flex-direction: column; gap: 12px; margin-bottom: 14px; }
.binding-head, .block-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.binding-head { padding: 14px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.binding-head span { color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: .1em; }
.binding-head h3 { margin: 4px 0; font-size: 16px; }
.binding-head p { margin: 0; color: var(--eid-text-secondary); font-size: 12px; line-height: 1.5; }
.binding-block { padding: 14px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.binding-block.available, .binding-block.pending { border-style: dashed; }
.stage-strip { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr; align-items: center; gap: 10px; padding: 14px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.stage-strip > div { display: grid; grid-template-columns: 26px minmax(0, 1fr); align-items: center; gap: 2px 8px; }
.stage-strip span { display: grid; width: 26px; height: 26px; grid-row: 1 / 3; place-items: center; border: 1px solid var(--eid-border); border-radius: 50%; color: var(--eid-text-primary); font-family: var(--eid-font-mono); font-size: 11px; }
.stage-strip strong { color: var(--eid-text-primary); font-size: 12px; }
.stage-strip small { color: var(--eid-text-muted); font-size: 10px; }
.stage-strip i { color: var(--eid-text-muted); font-style: normal; }
.stage-strip .done span { border-color: var(--eid-success); color: var(--eid-success); }
.block-head { margin-bottom: 10px; }
.block-head > div { display: flex; align-items: baseline; gap: 8px; }
.block-head small { color: var(--eid-text-muted); }
.device-name { display: flex; flex-direction: column; gap: 2px; }
.device-name code, .option-id { color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; }
.option-id { float: right; margin-left: 16px; }
@media (max-width: 900px) { .stage-strip { grid-template-columns: 1fr 1fr; } .stage-strip > i { display: none; } }
@media (max-width: 760px) { .binding-head { flex-direction: column; } .stage-strip { grid-template-columns: 1fr; } }
</style>
