<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  createPersonaInstance,
  deletePersonaInstance,
  getPersonaEvolution,
  getPersonaSnapshot,
  listPersonaInstances,
  listPersonaTemplates,
  rollbackPersonaEvolution,
  type PersonaInstance,
  type PersonaTemplate,
} from '@/api/agent'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const router = useRouter()
const items = ref<PersonaInstance[]>([])
const templates = ref<PersonaTemplate[]>([])
const loading = ref(false)
const detail = ref<{ title: string; data: any } | null>(null)
const evolution = ref<any[]>([])
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

async function viewSnapshot(row: PersonaInstance) {
  try {
    const data = await getPersonaSnapshot(row.tenant_id, row.user_id, row.instance_id)
    detail.value = { title: `Snapshot · ${row.instance_id}`, data }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  }
}

async function viewEvolution(row: PersonaInstance) {
  try {
    const data = await getPersonaEvolution(row.tenant_id, row.user_id, row.instance_id, 50)
    evolution.value = (data?.events || data || []) as any[]
    detail.value = { title: `Evolution · ${row.instance_id}`, data: evolution.value }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  }
}

async function rollback(row: PersonaInstance) {
  try {
    await ElMessageBox.prompt(
      '输入要回滚的 delta_id（或留空回滚最近一条）：',
      `Rollback · ${row.instance_id}`,
      { confirmButtonText: '回滚', cancelButtonText: '取消', inputValue: '' },
    ).then(async ({ value }) => {
      const r = await rollbackPersonaEvolution(row.tenant_id, row.user_id, row.instance_id, value || '')
      ElMessage.success(`回滚完成：${JSON.stringify(r).slice(0, 80)}`)
      await load()
    })
  } catch (_) { /* cancelled */ }
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
            <el-button size="small" link @click="viewSnapshot(row)">snapshot</el-button>
            <el-button size="small" link @click="viewEvolution(row)">evolution</el-button>
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

    <el-drawer
      :model-value="!!detail"
      @update:model-value="(v: boolean) => { if (!v) detail = null }"
      :title="detail?.title"
      size="60%"
      direction="rtl"
    >
      <JsonViewer v-if="detail" :data="detail.data" />
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
</style>
