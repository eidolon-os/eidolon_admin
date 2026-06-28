<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  getOwnerOverview,
  initializeOwnerWorkspace,
  listOwnerMemoryRealms,
  listOwnerPersonaGenomes,
  type MemoryRealmView,
  type OwnerOverviewResponse,
  type PersonaGenomeView,
} from '@/api/eidolonData'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import AgentScopeSelector from './components/AgentScopeSelector.vue'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

type Tab = 'data' | 'memory'

const route = useRoute()
const router = useRouter()
const ownersStore = useOwnersStore()
const ownerId = ref('')
const companionId = ref('')
const loading = ref(false)
const creating = ref(false)
const overview = ref<OwnerOverviewResponse | null>(null)
const genomes = ref<PersonaGenomeView[]>([])
const realms = ref<MemoryRealmView[]>([])
const createForm = ref({
  companion_id: '',
  companion_display_name: '',
  prompt_markdown: '',
  genome_json: JSON.stringify({
    identity: { name: 'Companion', archetype: 'companion' },
    style: { tone: 'warm', initiative: 'balanced' },
    boundaries: {},
    evolution: { enabled: true },
  }, null, 2),
  memory_policy_json: JSON.stringify({ scope: 'owner', recall: 'companion_default' }, null, 2),
})

const activeTab = computed<Tab>(() => route.params.feature === 'memory' ? 'memory' : 'data')
const filteredGenomes = computed(() =>
  companionId.value ? genomes.value.filter((item) => item.companion_id === companionId.value) : genomes.value,
)
const filteredRealms = computed(() =>
  companionId.value ? realms.value.filter((item) => item.companion_id === companionId.value) : realms.value,
)
const filteredConversations = computed(() =>
  companionId.value
    ? (overview.value?.conversations || []).filter((item) => item.companion_id === companionId.value)
    : overview.value?.conversations || [],
)
const filteredJobs = computed(() =>
  companionId.value
    ? (overview.value?.jobs || []).filter((item) => item.companion_id === companionId.value)
    : overview.value?.jobs || [],
)

onMounted(async () => {
  await ownersStore.load()
  ownerId.value = ownersStore.currentId
  await refresh()
})

watch([ownerId, companionId], () => {
  void refresh()
})

async function switchTab(tab: string | number) {
  await router.push({ name: 'feature', params: { serviceId: 'agent', feature: String(tab) } })
}

async function refresh() {
  if (!ownerId.value) return
  loading.value = true
  try {
    const [nextOverview, nextGenomes, nextRealms] = await Promise.all([
      getOwnerOverview(ownerId.value),
      listOwnerPersonaGenomes(ownerId.value),
      listOwnerMemoryRealms(ownerId.value),
    ])
    overview.value = nextOverview
    genomes.value = nextGenomes
    realms.value = nextRealms
  } catch (e) {
    ElMessage.error(`加载 Agent 数据失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function createCompanion() {
  if (!ownerId.value) {
    ElMessage.warning('请选择 owner')
    return
  }
  creating.value = true
  try {
    const name = createForm.value.companion_display_name.trim() || 'Companion'
    const result = await initializeOwnerWorkspace(ownerId.value, {
      companion_id: nullable(createForm.value.companion_id),
      companion_display_name: name,
      companion_kind: 'companion',
      genome_json: normalizeGenome(name),
      prompt_markdown: createForm.value.prompt_markdown.trim() || defaultPrompt(name),
      memory_engine: 'mempalace',
      memory_policy_json: parseJson(createForm.value.memory_policy_json, 'Memory policy JSON'),
    })
    companionId.value = result.companion.companion_id
    createForm.value.companion_id = ''
    createForm.value.companion_display_name = ''
    ElMessage.success('Companion 已创建')
    await refresh()
  } catch (e) {
    ElMessage.error(`创建 companion 失败: ${extractErrorMessage(e)}`)
  } finally {
    creating.value = false
  }
}

function nullable(value: string): string | null {
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

function normalizeGenome(name: string): Record<string, any> {
  const genome = parseJson(createForm.value.genome_json, 'Genome JSON')
  genome.identity = { ...(genome.identity || {}), name }
  return genome
}

function defaultPrompt(name: string): string {
  return `# ${name}\n\n## Identity\n\n- Name: ${name}\n- Archetype: companion\n\n## Style\n\n- Warm, clear, and grounded.\n`
}

function jsonKeys(value: Record<string, any>): string {
  const keys = Object.keys(value || {})
  return keys.length ? keys.slice(0, 5).join(', ') : '-'
}
</script>

<template>
  <CatalogPage
    title="Agent"
    hint="Agent 运行态固定落到 owner 下的具体 companion；data 和 memory 都按 companion 边界隔离。"
  >
    <template #head-actions>
      <AgentScopeSelector
        v-model:owner-id="ownerId"
        v-model:companion-id="companionId"
        allow-all-companions
      />
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </template>

    <el-tabs :model-value="activeTab" @tab-change="switchTab">
      <el-tab-pane label="Data" name="data" />
      <el-tab-pane label="Memory" name="memory" />
    </el-tabs>

    <section v-if="activeTab === 'data'" v-loading="loading" class="stack">
      <section class="panel">
        <div class="section-head">
          <div>
            <h3>Create Companion</h3>
            <p>一个 owner 可以拥有多个 companion；每个 companion 会同时创建 genome 和默认 memory realm。</p>
          </div>
          <el-button size="small" type="primary" :loading="creating" @click="createCompanion">
            创建 Companion
          </el-button>
        </div>
        <div class="form-grid">
          <el-input v-model="createForm.companion_id" placeholder="companion_id 可空" />
          <el-input v-model="createForm.companion_display_name" placeholder="显示名" />
        </div>
        <div class="json-grid">
          <el-input v-model="createForm.genome_json" type="textarea" :rows="6" />
          <el-input v-model="createForm.memory_policy_json" type="textarea" :rows="6" />
        </div>
      </section>

      <el-table :data="overview?.companions || []" size="small" stripe>
        <el-table-column prop="companion_id" label="companion_id" min-width="200" />
        <el-table-column prop="display_name" label="name" min-width="150" />
        <el-table-column prop="status" label="status" width="110" />
        <el-table-column prop="current_genome_id" label="genome" min-width="200" />
        <el-table-column prop="default_memory_realm_id" label="memory realm" min-width="200" />
      </el-table>

      <el-table :data="filteredGenomes" size="small" stripe>
        <el-table-column prop="genome_id" label="genome_id" min-width="220" />
        <el-table-column prop="companion_id" label="companion" min-width="200" />
        <el-table-column prop="version" label="version" width="100" />
        <el-table-column prop="status" label="status" width="110" />
        <el-table-column label="source" min-width="180">
          <template #default="{ row }">{{ jsonKeys(row.source_json) }}</template>
        </el-table-column>
        <el-table-column label="updated" width="190">
          <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
        </el-table-column>
      </el-table>

      <el-table :data="filteredConversations" size="small" stripe>
        <el-table-column prop="conversation_id" label="conversation_id" min-width="220" />
        <el-table-column prop="companion_id" label="companion" min-width="200" />
        <el-table-column prop="device_id" label="device" min-width="160" />
        <el-table-column prop="status" label="status" width="110" />
        <el-table-column label="updated" width="190">
          <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <section v-else v-loading="loading" class="stack">
      <el-table :data="filteredRealms" size="small" stripe>
        <el-table-column prop="realm_id" label="realm_id" min-width="220" />
        <el-table-column prop="companion_id" label="companion" min-width="200" />
        <el-table-column prop="engine" label="engine" width="130" />
        <el-table-column prop="status" label="status" width="110" />
        <el-table-column label="policy" min-width="220">
          <template #default="{ row }">{{ jsonKeys(row.policy_json) }}</template>
        </el-table-column>
        <el-table-column label="updated" width="190">
          <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
        </el-table-column>
      </el-table>

      <el-table :data="filteredJobs" size="small" stripe>
        <el-table-column prop="job_id" label="job_id" min-width="220" />
        <el-table-column prop="companion_id" label="companion" min-width="200" />
        <el-table-column prop="provider" label="provider" width="130" />
        <el-table-column prop="kind" label="kind" width="150" />
        <el-table-column prop="status" label="status" width="120" />
        <el-table-column label="updated" width="190">
          <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
        </el-table-column>
      </el-table>
    </section>
  </CatalogPage>
</template>

<style scoped>
.stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.panel {
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
  font-size: 14px;
}
.section-head p {
  margin: 4px 0 0;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.form-grid,
.json-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
</style>
