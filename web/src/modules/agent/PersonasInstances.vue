<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import {
  approvePersonaEvolutionProposal,
  createPersonaInstance,
  deletePersonaInstance,
  getPersonaEvolution,
  getPersonaSnapshot,
  listPersonaInstances,
  listPersonaEvolutionProposals,
  listPersonaObservations,
  listPersonaTemplates,
  rejectPersonaEvolutionProposal,
  rollbackPersonaEvolution,
  runPersonaReflection,
  type PersonaEvolutionProposal,
  type PersonaInstance,
  type PersonaObservation,
  type PersonaTemplate,
} from '@/api/agentLegacyProxy'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const router = useRouter()
const items = ref<PersonaInstance[]>([])
const templates = ref<PersonaTemplate[]>([])
const loading = ref(false)
const selected = ref<PersonaInstance | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const activeTab = ref('observations')
const snapshot = ref<any | null>(null)
const history = ref<any[]>([])
const observations = ref<PersonaObservation[]>([])
const proposals = ref<PersonaEvolutionProposal[]>([])
const reflectionLoading = ref(false)
const proposalActionId = ref('')
const createOpen = ref(false)
const form = ref({ tenant_id: 'default', user_id: '', instance_id: '', template_id: '' })

async function load() {
  loading.value = true
  try {
    const [insts, tmpls] = await Promise.all([listPersonaInstances(), listPersonaTemplates()])
    items.value = insts
    templates.value = tmpls
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function openDetail(row: PersonaInstance, tab = 'observations') {
  selected.value = row
  activeTab.value = tab
  detailOpen.value = true
  await refreshDetail()
}

async function refreshDetail() {
  const row = selected.value
  if (!row) return
  detailLoading.value = true
  try {
    const [snap, evo, obs, props] = await Promise.all([
      getPersonaSnapshot(row.tenant_id, row.user_id, row.instance_id),
      getPersonaEvolution(row.tenant_id, row.user_id, row.instance_id, 50),
      listPersonaObservations(row.tenant_id, row.user_id, row.instance_id, { limit: 80 }),
      listPersonaEvolutionProposals(row.tenant_id, row.user_id, row.instance_id, { limit: 80 }),
    ])
    snapshot.value = snap
    history.value = (evo?.events || evo || []) as any[]
    observations.value = obs
    proposals.value = props
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    detailLoading.value = false
  }
}

async function runReflection(row = selected.value) {
  if (!row) return
  reflectionLoading.value = true
  try {
    const created = await runPersonaReflection(row.tenant_id, row.user_id, row.instance_id, {
      dry_run: false,
      limit: 80,
    })
    ElMessage.success(`已生成 ${created.length} 条 proposal`)
    activeTab.value = 'proposals'
    await refreshDetail()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    reflectionLoading.value = false
  }
}

async function rollback(row: PersonaInstance) {
  try {
    await ElMessageBox.prompt(
      '输入要回滚的 delta_id：',
      `Rollback · ${row.instance_id}`,
      { confirmButtonText: '回滚', cancelButtonText: '取消', inputValue: '' },
    ).then(async ({ value }) => {
      if (!value) {
        ElMessage.warning('delta_id 必填')
        return
      }
      const r = await rollbackPersonaEvolution(row.tenant_id, row.user_id, row.instance_id, value || '')
      ElMessage.success(`回滚完成：${JSON.stringify(r).slice(0, 80)}`)
      await Promise.all([load(), refreshDetail()])
    })
  } catch (_) { /* cancelled */ }
}

async function approveProposal(row: PersonaEvolutionProposal) {
  proposalActionId.value = row.id
  try {
    await approvePersonaEvolutionProposal(row.id, { actor: 'admin' })
    ElMessage.success('已应用 proposal')
    await Promise.all([refreshDetail(), load()])
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    proposalActionId.value = ''
  }
}

async function rejectProposal(row: PersonaEvolutionProposal) {
  try {
    const { value } = await ElMessageBox.prompt(
      'Reject reason',
      `Reject · ${row.id}`,
      { confirmButtonText: 'Reject', cancelButtonText: '取消', inputValue: '' },
    )
    proposalActionId.value = row.id
    await rejectPersonaEvolutionProposal(row.id, { actor: 'admin', reason: value || null })
    ElMessage.success('已拒绝 proposal')
    await refreshDetail()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(e?.response?.data?.detail || e.message)
    }
  } finally {
    proposalActionId.value = ''
  }
}

async function destroy(row: PersonaInstance) {
  await ElMessageBox.confirm(`删除实例 ${row.instance_id}？`, '确认', { type: 'warning' })
  await deletePersonaInstance(row.tenant_id, row.user_id, row.instance_id)
  ElMessage.success('已删除')
  await load()
}

function openLab(row: PersonaInstance) {
  router.push({
    name: 'feature',
    params: { serviceId: 'agent', feature: 'persona-lab' },
    query: { tenant: row.tenant_id, user: row.user_id, instance: row.instance_id },
  })
}

function openCreate() {
  form.value = { tenant_id: 'default', user_id: '', instance_id: '', template_id: templates.value[0]?.template_id || '' }
  createOpen.value = true
}

async function submitCreate() {
  if (!form.value.user_id || !form.value.instance_id || !form.value.template_id) {
    ElMessage.warning('user_id / instance_id / template_id 必填')
    return
  }
  try {
    await createPersonaInstance({ ...form.value })
    createOpen.value = false
    ElMessage.success('已创建')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  }
}

const grouped = computed(() => {
  const m = new Map<string, PersonaInstance[]>()
  for (const i of items.value) {
    const k = `${i.tenant_id} / ${i.user_id}`
    if (!m.has(k)) m.set(k, [])
    m.get(k)!.push(i)
  }
  return Array.from(m.entries()).map(([key, instances]) => ({ key, instances }))
})

function pct(value: number | undefined): string {
  if (typeof value !== 'number') return '-'
  return `${Math.round(value * 100)}%`
}

function patchText(row: PersonaEvolutionProposal): string {
  const patches = row.patches || []
  if (!patches.length) return '-'
  return patches.map((p) => {
    const delta = typeof p.delta === 'number' ? ` ${p.delta > 0 ? '+' : ''}${p.delta}` : ''
    return `${p.type}: ${p.target}${delta}`
  }).join('\n')
}

function statusType(status: string | undefined): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'pending') return 'warning'
  if (status === 'applied') return 'success'
  if (status === 'rejected') return 'info'
  return 'info'
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Persona Instances</h2>
        <div class="subtitle">{{ items.length }} 实例，按 tenant / user 分组</div>
      </div>
      <div class="actions">
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建实例</el-button>
      </div>
    </div>

    <el-card v-for="g in grouped" :key="g.key" style="margin-bottom: 12px">
      <template #header>
        <span class="mono group-header">{{ g.key }}</span>
        <span class="muted" style="margin-left: 8px">{{ g.instances.length }} instances</span>
      </template>
      <el-table :data="g.instances" stripe size="small">
        <el-table-column label="Instance ID" min-width="240">
          <template #default="{ row }"><span class="mono">{{ row.instance_id }}</span></template>
        </el-table-column>
        <el-table-column label="Template" prop="template_id" width="200" />
        <el-table-column label="Overlay ver" width="120" prop="overlay_version" />
        <el-table-column label="Updated" width="180" prop="updated_at" />
        <el-table-column label="操作" width="400">
          <template #default="{ row }">
            <el-button size="small" link @click="openDetail(row, 'observations')">detail</el-button>
            <el-button size="small" link @click="openDetail(row, 'history')">evolution</el-button>
            <el-button size="small" link type="primary" @click="openLab(row)">lab</el-button>
            <el-button size="small" link type="warning" @click="rollback(row)">rollback</el-button>
            <el-button size="small" link type="danger" @click="destroy(row)">delete</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-empty v-if="!loading && items.length === 0" description="无实例" />

    <!-- 创建实例 -->
    <el-dialog v-model="createOpen" title="新建 Persona 实例" width="480px">
      <el-form label-position="top">
        <el-form-item label="Tenant ID" required>
          <el-input v-model="form.tenant_id" />
        </el-form-item>
        <el-form-item label="User ID" required>
          <el-input v-model="form.user_id" />
        </el-form-item>
        <el-form-item label="Instance ID" required>
          <el-input v-model="form.instance_id" />
        </el-form-item>
        <el-form-item label="Template" required>
          <el-select v-model="form.template_id" filterable style="width: 100%">
            <el-option v-for="t in templates" :key="t.template_id || t.id"
              :value="t.template_id || t.id"
              :label="`${t.template_id || t.id}${t.name ? ' · ' + t.name : ''}`" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" :title="selected ? `Persona · ${selected.instance_id}` : 'Persona'" size="72%" direction="rtl">
      <div v-if="selected" v-loading="detailLoading" class="detail">
        <div class="detail-head">
          <div>
            <div class="mono">{{ selected.tenant_id }} / {{ selected.user_id }} / {{ selected.instance_id }}</div>
            <div class="muted">template {{ selected.template_id }} · overlay {{ selected.overlay_version ?? '-' }}</div>
          </div>
          <div class="actions">
            <el-button size="small" :icon="Refresh" :loading="detailLoading" @click="refreshDetail">刷新</el-button>
            <el-button size="small" type="primary" :icon="VideoPlay" :loading="reflectionLoading" @click="runReflection()">Run reflection</el-button>
            <el-button size="small" type="warning" @click="rollback(selected)">Rollback</el-button>
          </div>
        </div>

        <el-tabs v-model="activeTab">
          <el-tab-pane label="Observations" name="observations">
            <el-table :data="observations" size="small" stripe>
              <el-table-column type="expand">
                <template #default="{ row }">
                  <JsonViewer :data="row.evidence || row" max-height="36vh" />
                </template>
              </el-table-column>
              <el-table-column label="Kind" min-width="180">
                <template #default="{ row }"><span class="mono">{{ row.kind }}</span></template>
              </el-table-column>
              <el-table-column label="Source" prop="source" width="150" />
              <el-table-column label="Status" width="110">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusType(row.status)">{{ row.status || '-' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Strength" width="100">
                <template #default="{ row }">{{ pct(row.strength) }}</template>
              </el-table-column>
              <el-table-column label="Confidence" width="110">
                <template #default="{ row }">{{ pct(row.confidence) }}</template>
              </el-table-column>
              <el-table-column label="Summary" prop="summary" min-width="260" show-overflow-tooltip />
              <el-table-column label="Created" prop="created_at" width="180" />
            </el-table>
            <el-empty v-if="observations.length === 0" description="暂无 observations" />
          </el-tab-pane>

          <el-tab-pane label="Evolution Proposals" name="proposals">
            <el-table :data="proposals" size="small" stripe>
              <el-table-column type="expand">
                <template #default="{ row }">
                  <JsonViewer :data="row" max-height="42vh" />
                </template>
              </el-table-column>
              <el-table-column label="Proposal" min-width="230">
                <template #default="{ row }"><span class="mono">{{ row.id }}</span></template>
              </el-table-column>
              <el-table-column label="Status" width="110">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Confidence" width="110">
                <template #default="{ row }">{{ pct(row.confidence) }}</template>
              </el-table-column>
              <el-table-column label="Patch" min-width="260">
                <template #default="{ row }"><pre class="patch">{{ patchText(row) }}</pre></template>
              </el-table-column>
              <el-table-column label="Rationale" prop="rationale" min-width="240" show-overflow-tooltip />
              <el-table-column label="Updated" prop="updated_at" width="180" />
              <el-table-column label="操作" width="150" fixed="right">
                <template #default="{ row }">
                  <el-button
                    v-if="row.status === 'pending'"
                    size="small"
                    link
                    type="success"
                    :icon="Check"
                    :loading="proposalActionId === row.id"
                    @click="approveProposal(row)"
                  >
                    approve
                  </el-button>
                  <el-button
                    v-if="row.status === 'pending'"
                    size="small"
                    link
                    type="danger"
                    :icon="Close"
                    :loading="proposalActionId === row.id"
                    @click="rejectProposal(row)"
                  >
                    reject
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="proposals.length === 0" description="暂无 proposals" />
          </el-tab-pane>

          <el-tab-pane label="Evolution History" name="history">
            <el-table :data="history" size="small" stripe>
              <el-table-column type="expand">
                <template #default="{ row }">
                  <JsonViewer :data="row" max-height="42vh" />
                </template>
              </el-table-column>
              <el-table-column label="Applied" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.applied ? 'success' : 'info'">{{ row.applied ? 'yes' : 'no' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Rationale" prop="rationale" min-width="260" show-overflow-tooltip />
              <el-table-column label="Changes" min-width="320">
                <template #default="{ row }">
                  <pre class="patch">{{ JSON.stringify(row.changes || [], null, 2) }}</pre>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="history.length === 0" description="暂无 evolution history" />
          </el-tab-pane>

          <el-tab-pane label="Snapshot" name="snapshot">
            <JsonViewer :data="snapshot" max-height="62vh" />
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.actions { display: flex; gap: 8px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.group-header { font-weight: 600; color: var(--eid-text-primary); font-size: 13px; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.detail { min-width: 0; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 12px; }
.patch {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.45;
}
</style>
