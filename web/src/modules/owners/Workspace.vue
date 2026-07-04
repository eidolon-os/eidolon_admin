<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  addNearbyDeviceToOwner,
  bindOwnerDevice,
  getOwnerOverview,
  identifyNearbyDevice,
  identifyOwnerDevice,
  initializeOwnerWorkspace,
  listOwnerCompanions,
  listOwnerConversations,
  listOwnerDevices,
  listOwnerEvents,
  listOwnerJobs,
  listOwnerMemoryRealms,
  listNearbyOwnerDevices,
  listOwnerPersonaGenomes,
  releaseOwnerDevice,
  updateOwnerDevice,
  type CompanionView,
  type ConversationView,
  type DeviceView,
  type EventView,
  type JobView,
  type MemoryRealmView,
  type NearbyDeviceView,
  type OwnerOverviewResponse,
  type PersonaGenomeView,
} from '@/api/eidolonData'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import Breadcrumb from '@/modules/common/Breadcrumb.vue'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, CloseBold, Delete, Link, Plus } from '@element-plus/icons-vue'

type Section =
  | 'overview'
  | 'initialize'
  | 'companions'
  | 'persona'
  | 'devices'
  | 'conversations'
  | 'memory'
  | 'jobs'
  | 'events'

const route = useRoute()
const router = useRouter()
const ownerStore = useOwnersStore()

const loading = ref(false)
const overview = ref<OwnerOverviewResponse | null>(null)
const companions = ref<CompanionView[]>([])
const personaGenomes = ref<PersonaGenomeView[]>([])
const devices = ref<DeviceView[]>([])
const nearbyDevices = ref<NearbyDeviceView[]>([])
const nearbyHubAvailable = ref(true)
const conversations = ref<ConversationView[]>([])
const memoryRealms = ref<MemoryRealmView[]>([])
const jobs = ref<JobView[]>([])
const events = ref<EventView[]>([])
const initializing = ref(false)
const deviceActionOpen = ref(false)
const deviceActionMode = ref<'add' | 'bind'>('add')
const deviceActionId = ref('')
const deviceActionName = ref('')
const deviceActionCompanionId = ref('')
const deviceActionLoading = ref(false)
const identifyingDeviceId = ref('')
const releasingDeviceId = ref('')
const unbindingDeviceId = ref('')
const initForm = ref({
  companion_id: '',
  companion_display_name: '',
  companion_kind: 'companion',
  companion_profile_json: '{}',
  companion_runtime_config_json: '{}',
  companion_metadata_json: '{}',
  genome_id: '',
  genome_source_json: '{}',
  genome_json: JSON.stringify({
    identity: { name: 'Companion', archetype: 'companion' },
    style: { tone: 'warm', initiative: 'balanced' },
    boundaries: {},
    evolution: { enabled: true },
  }, null, 2),
  prompt_markdown: defaultPromptMarkdown('Companion'),
  evolution_state_json: JSON.stringify({ version: 1, mode: 'continuous' }, null, 2),
  realm_id: '',
  memory_engine: 'mempalace',
  memory_policy_json: JSON.stringify({ scope: 'owner', recall: 'companion_default' }, null, 2),
})

const sections: Array<{ key: Section; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'initialize', label: 'Initialize Workspace' },
  { key: 'companions', label: 'Companions' },
  { key: 'persona', label: 'Persona' },
  { key: 'devices', label: 'Devices' },
  { key: 'conversations', label: 'Conversations' },
  { key: 'memory', label: 'Memory' },
  { key: 'jobs', label: 'Jobs' },
  { key: 'events', label: 'Events' },
]

const ownerId = computed(() => String(route.params.ownerId || ''))
const activeSection = computed<Section>(() => {
  const raw = String(route.params.section || 'overview')
  return sections.some((section) => section.key === raw) ? raw as Section : 'overview'
})
const owner = computed(() => overview.value?.owner || ownerStore.currentOwner)

onMounted(() => {
  ownerStore.load()
  refresh()
})

watch([ownerId, activeSection], () => {
  refresh()
})

async function switchSection(section: string | number) {
  await router.push({
    name: 'owner-workspace',
    params: { ownerId: ownerId.value, section: String(section) },
  })
}

async function refresh() {
  if (!ownerId.value) return
  ownerStore.setCurrent(ownerId.value)
  loading.value = true
  try {
    if (activeSection.value === 'overview') {
      overview.value = await getOwnerOverview(ownerId.value)
      companions.value = overview.value.companions
      devices.value = overview.value.devices
      conversations.value = overview.value.conversations
      memoryRealms.value = overview.value.memory_realms
      jobs.value = overview.value.jobs
      events.value = overview.value.events
      return
    }
    if (activeSection.value === 'initialize') {
      overview.value = await getOwnerOverview(ownerId.value)
      companions.value = overview.value.companions
      memoryRealms.value = overview.value.memory_realms
    }
    if (activeSection.value === 'companions') companions.value = await listOwnerCompanions(ownerId.value)
    if (activeSection.value === 'persona') personaGenomes.value = await listOwnerPersonaGenomes(ownerId.value)
    if (activeSection.value === 'devices') await loadDevicesSection()
    if (activeSection.value === 'conversations') conversations.value = await listOwnerConversations(ownerId.value)
    if (activeSection.value === 'memory') memoryRealms.value = await listOwnerMemoryRealms(ownerId.value)
    if (activeSection.value === 'jobs') jobs.value = await listOwnerJobs(ownerId.value)
    if (activeSection.value === 'events') events.value = await listOwnerEvents(ownerId.value)
  } catch (e) {
    ElMessage.error(`加载 Owner Workspace 失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

function jsonSummary(value: Record<string, any> | null | undefined): string {
  const keys = Object.keys(value || {})
  return keys.length ? keys.slice(0, 4).join(', ') : '—'
}

async function loadDevicesSection() {
  const [owned, ownerCompanions, nearby] = await Promise.all([
    listOwnerDevices(ownerId.value),
    listOwnerCompanions(ownerId.value),
    listNearbyOwnerDevices(ownerId.value),
  ])
  devices.value = owned
  companions.value = ownerCompanions
  nearbyDevices.value = nearby.devices
  nearbyHubAvailable.value = nearby.hub_available
}

async function identifyNearby(row: NearbyDeviceView) {
  identifyingDeviceId.value = row.device_id
  try {
    const result = await identifyNearbyDevice(ownerId.value, row.device_id)
    ElMessage.success(`点名命令已下发: ${result.command_id || row.device_id}`)
  } catch (e) {
    ElMessage.error(`点名失败: ${extractErrorMessage(e)}`)
  } finally {
    identifyingDeviceId.value = ''
  }
}

async function identifyOwned(row: DeviceView) {
  identifyingDeviceId.value = row.device_id
  try {
    const result = await identifyOwnerDevice(ownerId.value, row.device_id)
    ElMessage.success(`点名命令已下发: ${result.command_id || row.device_id}`)
  } catch (e) {
    ElMessage.error(`点名失败: ${extractErrorMessage(e)}`)
  } finally {
    identifyingDeviceId.value = ''
  }
}

function openAddDevice(row: NearbyDeviceView) {
  deviceActionMode.value = 'add'
  deviceActionId.value = row.device_id
  deviceActionName.value = row.name || row.device_id
  const availableCompanion = companions.value.find(
    (companion) => !isCompanionBoundToOtherDevice(companion.companion_id, row.device_id),
  )
  deviceActionCompanionId.value = availableCompanion?.companion_id || ''
  deviceActionOpen.value = true
}

function openBindDevice(row: DeviceView) {
  deviceActionMode.value = 'bind'
  deviceActionId.value = row.device_id
  deviceActionName.value = row.name || row.device_id
  deviceActionCompanionId.value = row.bound_companion_id || ''
  deviceActionOpen.value = true
}

async function submitDeviceAction() {
  if (!deviceActionId.value) return
  deviceActionLoading.value = true
  try {
    const companionId = deviceActionCompanionId.value || null
    if (deviceActionMode.value === 'add') {
      await addNearbyDeviceToOwner(ownerId.value, deviceActionId.value, {
        name: deviceActionName.value.trim() || deviceActionId.value,
        companion_id: companionId,
        interaction_mode: companionId ? 'voice' : null,
        access_policy_json: companionId
          ? { conversation: true, voice_input: true, voice_output: true, memory_recall: true }
          : {},
      })
      ElMessage.success('设备已认领并绑定到当前 Owner')
    } else {
      await updateOwnerDevice(ownerId.value, deviceActionId.value, {
        name: deviceActionName.value.trim() || null,
      })
      await bindOwnerDevice(ownerId.value, deviceActionId.value, companionId)
      ElMessage.success(companionId ? '设备已绑定 Companion' : '设备已解绑 Companion')
    }
    deviceActionOpen.value = false
    await loadDevicesSection()
  } catch (e) {
    ElMessage.error(`设备操作失败: ${extractErrorMessage(e)}`)
  } finally {
    deviceActionLoading.value = false
  }
}

async function unbindDevice(row: DeviceView) {
  unbindingDeviceId.value = row.device_id
  try {
    await bindOwnerDevice(ownerId.value, row.device_id, null)
    ElMessage.success('设备已解绑 Companion')
    await loadDevicesSection()
  } catch (e) {
    ElMessage.error(`解绑失败: ${extractErrorMessage(e)}`)
  } finally {
    unbindingDeviceId.value = ''
  }
}

async function releaseDevice(row: DeviceView) {
  try {
    await ElMessageBox.confirm(
      `释放设备 ${row.name || row.device_id} 后，它会回到可认领设备列表。`,
      '释放设备',
      { confirmButtonText: '释放', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  releasingDeviceId.value = row.device_id
  try {
    await releaseOwnerDevice(ownerId.value, row.device_id)
    ElMessage.success('设备已释放')
    await loadDevicesSection()
  } catch (e) {
    ElMessage.error(`释放失败: ${extractErrorMessage(e)}`)
  } finally {
    releasingDeviceId.value = ''
  }
}

function companionLabel(companionId: string | null | undefined): string {
  if (!companionId) return '—'
  const companion = companions.value.find((item) => item.companion_id === companionId)
  return companion?.display_name || companionId
}

function isCompanionBoundToOtherDevice(companionId: string, deviceId: string): boolean {
  return devices.value.some(
    (device) =>
      device.device_id !== deviceId
      && device.bound_companion_id === companionId
      && device.status !== 'revoked'
      && !device.revoked_at,
  )
}

async function goInitialize() {
  await router.push({
    name: 'owner-workspace',
    params: { ownerId: ownerId.value, section: 'initialize' },
  })
}

async function submitInitialize() {
  if (!ownerId.value) return
  initializing.value = true
  try {
    const companionName = initForm.value.companion_display_name.trim()
    await initializeOwnerWorkspace(ownerId.value, {
      companion_id: nullableText(initForm.value.companion_id),
      companion_display_name: companionName,
      companion_kind: initForm.value.companion_kind,
      companion_profile_json: parseJson(initForm.value.companion_profile_json, 'Companion profile JSON'),
      companion_runtime_config_json: parseJson(initForm.value.companion_runtime_config_json, 'Companion runtime JSON'),
      companion_metadata_json: parseJson(initForm.value.companion_metadata_json, 'Companion metadata JSON'),
      genome_id: nullableText(initForm.value.genome_id),
      genome_source_json: parseJson(initForm.value.genome_source_json, 'Genome source JSON'),
      genome_json: normalizedGenomeJson(companionName),
      prompt_markdown: initForm.value.prompt_markdown.trim() || defaultPromptMarkdown(companionName || 'Companion'),
      evolution_state_json: parseJson(initForm.value.evolution_state_json, 'Evolution state JSON'),
      realm_id: nullableText(initForm.value.realm_id),
      memory_engine: initForm.value.memory_engine.trim() || 'mempalace',
      memory_engine_config_json: {},
      memory_policy_json: parseJson(initForm.value.memory_policy_json, 'Memory policy JSON'),
    })
    ElMessage.success('Companion 工作区已初始化')
    await router.push({
      name: 'owner-workspace',
      params: { ownerId: ownerId.value, section: 'overview' },
    })
    await refresh()
  } catch (e) {
    ElMessage.error(`初始化失败: ${extractErrorMessage(e)}`)
  } finally {
    initializing.value = false
  }
}

function nullableText(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function parseJson(value: string, label: string): Record<string, any> {
  try {
    return JSON.parse(value || '{}')
  } catch {
    throw new Error(`${label} 格式不正确`)
  }
}

function normalizedGenomeJson(companionName: string): Record<string, any> {
  const genome = parseJson(initForm.value.genome_json, 'Genome JSON')
  const identity = { ...(genome.identity || {}) }
  if (!String(identity.name || '').trim()) identity.name = companionName || 'Companion'
  if (!String(identity.archetype || '').trim()) identity.archetype = 'companion'
  genome.identity = identity
  return genome
}

function defaultPromptMarkdown(name: string): string {
  return [
    `# ${name}`,
    '',
    '## Identity',
    '',
    `- Name: ${name}`,
    '- Archetype: companion',
    '',
    '## Style',
    '',
    '- Warm, clear, and grounded.',
    "- Respond to the user's intent before adding suggestions.",
    '- Keep healthy boundaries and avoid pretending to know what was not provided.',
    '',
    '## Evolution',
    '',
    '- This persona may evolve through reviewed or policy-approved genome versions.',
    '',
  ].join('\n')
}
</script>

<template>
  <CatalogPage
    :title="owner?.display_name || ownerId"
    :hint="`Owner workspace · ${ownerId}`"
  >
    <template #breadcrumb>
      <Breadcrumb :items="[{ label: 'Owners', to: { name: 'owners' } }, { label: owner?.display_name || ownerId }]" />
    </template>
    <template #head-actions>
      <el-button size="small" @click="refresh">刷新</el-button>
    </template>

    <el-tabs :model-value="activeSection" @tab-change="switchSection">
      <el-tab-pane v-for="section in sections" :key="section.key" :label="section.label" :name="section.key" />
    </el-tabs>

    <section v-if="activeSection === 'overview'" v-loading="loading" class="workspace-section">
      <el-alert
        v-if="overview && !overview.initialized"
        title="Companion workspace 尚未初始化"
        type="warning"
        :closable="false"
        show-icon
      >
        <template #default>
          <el-button size="small" type="primary" @click="goInitialize">初始化工作区</el-button>
        </template>
      </el-alert>

      <div class="metric-grid">
        <div v-for="(value, key) in overview?.counts || {}" :key="key" class="metric">
          <span>{{ key }}</span>
          <strong>{{ value }}</strong>
        </div>
      </div>

      <div class="split">
        <el-table :data="companions" size="small" stripe>
          <el-table-column prop="display_name" label="companions" min-width="140" />
          <el-table-column prop="status" label="status" width="100" />
        </el-table>
        <el-table :data="devices" size="small" stripe>
          <el-table-column prop="name" label="devices" min-width="140" />
          <el-table-column prop="status" label="status" width="100" />
        </el-table>
      </div>

      <el-table :data="jobs" size="small" stripe>
        <el-table-column prop="job_id" label="recent jobs" min-width="220" />
        <el-table-column prop="kind" label="kind" width="140" />
        <el-table-column prop="status" label="status" width="120" />
        <el-table-column label="updated" width="190">
          <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <section v-else-if="activeSection === 'initialize'" v-loading="loading" class="workspace-section">
      <el-alert
        v-if="overview?.initialized"
        title="当前 Owner 已有 Companion，可以继续创建新的 Companion 工作区"
        type="info"
        :closable="false"
        show-icon
      />

      <el-form
        label-position="top"
        class="init-form"
        @submit.prevent="submitInitialize"
      >
        <section class="init-block">
          <h3>Companion</h3>
          <div class="form-grid">
            <el-form-item label="companion_id">
              <el-input v-model="initForm.companion_id" placeholder="c:owner-default:default" />
            </el-form-item>
            <el-form-item label="显示名">
              <el-input v-model="initForm.companion_display_name" placeholder="Eidolon Companion" />
            </el-form-item>
            <el-form-item label="kind">
              <el-input v-model="initForm.companion_kind" />
            </el-form-item>
          </div>
          <el-collapse>
            <el-collapse-item title="Advanced JSON" name="companion-json">
              <div class="json-grid">
                <el-form-item label="profile_json">
                  <el-input v-model="initForm.companion_profile_json" type="textarea" :rows="5" />
                </el-form-item>
                <el-form-item label="runtime_config_json">
                  <el-input v-model="initForm.companion_runtime_config_json" type="textarea" :rows="5" />
                </el-form-item>
                <el-form-item label="metadata_json">
                  <el-input v-model="initForm.companion_metadata_json" type="textarea" :rows="5" />
                </el-form-item>
              </div>
            </el-collapse-item>
          </el-collapse>
        </section>

        <section class="init-block">
          <h3>Persona Genome</h3>
          <el-form-item label="genome_id">
            <el-input v-model="initForm.genome_id" placeholder="g:owner-default:default:v1" />
          </el-form-item>
          <el-form-item label="prompt_markdown">
            <el-input v-model="initForm.prompt_markdown" type="textarea" :rows="12" />
          </el-form-item>
          <div class="json-grid">
            <el-form-item label="genome_json">
              <el-input v-model="initForm.genome_json" type="textarea" :rows="9" />
            </el-form-item>
            <el-form-item label="evolution_state_json">
              <el-input v-model="initForm.evolution_state_json" type="textarea" :rows="9" />
            </el-form-item>
          </div>
          <el-collapse>
            <el-collapse-item title="Source JSON" name="source-json">
              <el-form-item label="source_json">
                <el-input v-model="initForm.genome_source_json" type="textarea" :rows="5" />
              </el-form-item>
            </el-collapse-item>
          </el-collapse>
        </section>

        <section class="init-block">
          <h3>Memory Realm</h3>
          <div class="form-grid">
            <el-form-item label="realm_id">
              <el-input v-model="initForm.realm_id" placeholder="r:owner-default:default" />
            </el-form-item>
            <el-form-item label="engine">
              <el-input v-model="initForm.memory_engine" />
            </el-form-item>
          </div>
          <div class="json-grid">
            <el-form-item label="policy_json">
              <el-input v-model="initForm.memory_policy_json" type="textarea" :rows="7" />
            </el-form-item>
          </div>
        </section>

        <div class="form-actions">
          <el-button type="primary" :loading="initializing" @click="submitInitialize">
            创建 Companion 工作区
          </el-button>
        </div>
      </el-form>
    </section>

    <el-table v-else-if="activeSection === 'companions'" :data="companions" v-loading="loading" size="small" stripe>
      <el-table-column prop="companion_id" label="companion_id" min-width="180" />
      <el-table-column prop="display_name" label="显示名" min-width="140" />
      <el-table-column prop="kind" label="kind" width="110" />
      <el-table-column prop="status" label="status" width="110" />
      <el-table-column prop="current_genome_id" label="genome" min-width="180" />
      <el-table-column prop="default_memory_realm_id" label="memory realm" min-width="180" />
    </el-table>

    <el-table v-else-if="activeSection === 'persona'" :data="personaGenomes" v-loading="loading" size="small" stripe>
      <el-table-column prop="genome_id" label="genome_id" min-width="200" />
      <el-table-column prop="companion_id" label="companion_id" min-width="180" />
      <el-table-column prop="version" label="version" width="100" />
      <el-table-column prop="status" label="status" width="120" />
      <el-table-column prop="base_genome_id" label="base" min-width="180" />
      <el-table-column label="source" min-width="180">
        <template #default="{ row }">{{ jsonSummary(row.source_json) }}</template>
      </el-table-column>
      <el-table-column label="markdown" width="110">
        <template #default="{ row }">{{ row.prompt_markdown ? 'yes' : '—' }}</template>
      </el-table-column>
      <el-table-column label="updated" width="190">
        <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
      </el-table-column>
    </el-table>

    <section v-else-if="activeSection === 'devices'" v-loading="loading" class="workspace-section">
      <section class="device-block">
        <div class="section-head">
          <div>
            <h3>Owner Devices</h3>
            <p>已添加到当前 Owner 的设备</p>
          </div>
          <el-button size="small" @click="loadDevicesSection">刷新</el-button>
        </div>
        <el-table :data="devices" size="small" stripe>
          <el-table-column prop="device_id" label="device_id" min-width="190" />
          <el-table-column prop="name" label="name" min-width="140" />
          <el-table-column prop="kind" label="kind" width="120" />
          <el-table-column prop="status" label="status" width="110" />
          <el-table-column label="companion" min-width="180">
            <template #default="{ row }">{{ companionLabel(row.bound_companion_id) }}</template>
          </el-table-column>
          <el-table-column label="last seen" width="190">
            <template #default="{ row }">{{ formatTimestamp(row.last_seen_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="330" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button
                  size="small"
                  :icon="Bell"
                  :loading="identifyingDeviceId === row.device_id"
                  @click="identifyOwned(row)"
                >
                  点名
                </el-button>
                <el-button size="small" :icon="Link" @click="openBindDevice(row)">
                  {{ row.bound_companion_id ? '换绑' : '绑定' }}
                </el-button>
                <el-button
                  size="small"
                  :icon="CloseBold"
                  :disabled="!row.bound_companion_id"
                  :loading="unbindingDeviceId === row.device_id"
                  @click="unbindDevice(row)"
                >
                  解绑
                </el-button>
                <el-button
                  size="small"
                  type="danger"
                  :icon="Delete"
                  :loading="releasingDeviceId === row.device_id"
                  @click="releaseDevice(row)"
                >
                  释放
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="device-block">
        <div class="section-head">
          <div>
            <h3>Available Approved Devices</h3>
            <p>Hub 已批准且还未归属任何 Owner 的设备</p>
          </div>
          <el-tag :type="nearbyHubAvailable ? 'success' : 'warning'" size="small">
            {{ nearbyHubAvailable ? 'Hub online' : 'Hub unavailable' }}
          </el-tag>
        </div>
        <el-table :data="nearbyDevices" size="small" stripe>
          <template #empty>
            <span>{{ nearbyHubAvailable ? '暂无可认领设备，请先在 Hub / Devices 批准设备' : 'Hub unavailable' }}</span>
          </template>
          <el-table-column prop="device_id" label="device_id" min-width="190" />
          <el-table-column prop="name" label="name" min-width="140" />
          <el-table-column prop="kind" label="kind" width="120" />
          <el-table-column prop="status" label="status" width="110" />
          <el-table-column prop="room_name" label="room" min-width="180" />
          <el-table-column label="last seen" width="190">
            <template #default="{ row }">{{ formatTimestamp(row.last_seen) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="210" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button
                  size="small"
                  :icon="Bell"
                  :loading="identifyingDeviceId === row.device_id"
                  @click="identifyNearby(row)"
                >
                  点名
                </el-button>
                <el-button size="small" type="primary" :icon="Plus" @click="openAddDevice(row)">
                  Claim
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </section>

    <el-table v-else-if="activeSection === 'conversations'" :data="conversations" v-loading="loading" size="small" stripe>
      <el-table-column prop="conversation_id" label="conversation_id" min-width="220" />
      <el-table-column prop="title" label="title" min-width="160" />
      <el-table-column prop="companion_id" label="companion" min-width="180" />
      <el-table-column prop="status" label="status" width="110" />
      <el-table-column label="updated" width="190">
        <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
      </el-table-column>
    </el-table>

    <el-table v-else-if="activeSection === 'memory'" :data="memoryRealms" v-loading="loading" size="small" stripe>
      <el-table-column prop="realm_id" label="realm_id" min-width="200" />
      <el-table-column prop="companion_id" label="companion" min-width="180" />
      <el-table-column prop="engine" label="engine" width="130" />
      <el-table-column prop="status" label="status" width="110" />
      <el-table-column label="policy" min-width="180">
        <template #default="{ row }">{{ jsonSummary(row.policy_json) }}</template>
      </el-table-column>
    </el-table>

    <el-table v-else-if="activeSection === 'jobs'" :data="jobs" v-loading="loading" size="small" stripe>
      <el-table-column prop="job_id" label="job_id" min-width="220" />
      <el-table-column prop="provider" label="provider" width="130" />
      <el-table-column prop="kind" label="kind" width="150" />
      <el-table-column prop="status" label="status" width="120" />
      <el-table-column label="updated" width="190">
        <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
      </el-table-column>
    </el-table>

    <el-table v-else :data="events" v-loading="loading" size="small" stripe>
      <el-table-column prop="event_type" label="event_type" min-width="180" />
      <el-table-column prop="subject_type" label="subject" width="130" />
      <el-table-column prop="subject_id" label="subject_id" min-width="180" />
      <el-table-column prop="actor_type" label="actor" width="120" />
      <el-table-column label="created" width="190">
        <template #default="{ row }">{{ formatTimestamp(row.created_at) }}</template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="deviceActionOpen"
      :title="deviceActionMode === 'add' ? 'Claim + Bind Device' : 'Bind Device'"
      width="520px"
    >
      <el-form label-position="top">
        <el-form-item label="设备名称">
          <el-input
            v-model="deviceActionName"
            placeholder="例如 box-3；用于区分具体物理设备"
          />
        </el-form-item>
        <el-form-item label="Companion">
          <el-select
            v-model="deviceActionCompanionId"
            clearable
            filterable
            placeholder="选择 Companion"
            style="width: 100%"
          >
            <el-option
              v-for="companion in companions"
              :key="companion.companion_id"
              :label="companion.display_name || companion.companion_id"
              :value="companion.companion_id"
              :disabled="isCompanionBoundToOtherDevice(companion.companion_id, deviceActionId)"
            >
              <span>{{ companion.display_name || companion.companion_id }}</span>
              <span class="option-id">{{ companion.companion_id }}</span>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deviceActionOpen = false">取消</el-button>
        <el-button type="primary" :loading="deviceActionLoading" @click="submitDeviceAction">
          {{ deviceActionMode === 'add' ? 'Claim + Bind' : '保存绑定' }}
        </el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
.workspace-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.metric {
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
  padding: 12px;
}
.metric span {
  display: block;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.metric strong {
  display: block;
  margin-top: 4px;
  color: var(--eid-text-primary);
  font-size: 22px;
}
.split {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}
.device-block {
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
  padding: 14px;
}
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.section-head h3 {
  margin: 0;
  color: var(--eid-text-primary);
  font-size: 14px;
}
.section-head p {
  margin: 4px 0 0;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.option-id {
  float: right;
  margin-left: 12px;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.init-form,
.init-block {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.init-block {
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
  padding: 14px;
}
.init-block h3 {
  margin: 0;
  color: var(--eid-text-primary);
  font-size: 14px;
}
.form-grid,
.json-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
