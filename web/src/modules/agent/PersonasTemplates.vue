<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Download } from '@element-plus/icons-vue'
import {
  getPersonaTemplateRaw,
  listPersonaTemplates,
  reloadPersonaTemplates,
  type PersonaTemplate,
} from '@/api/agent'

const items = ref<PersonaTemplate[]>([])
const loading = ref(false)
const drawerOpen = ref(false)
const drawerTitle = ref('')
const drawerBody = ref('')

async function load() {
  loading.value = true
  try {
    items.value = await listPersonaTemplates()
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function reload() {
  try {
    const r = await reloadPersonaTemplates()
    ElMessage.success(`已重新加载 ${r.loaded ?? '?'} 个模板`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  }
}

async function viewRaw(t: PersonaTemplate) {
  const id = t.template_id || t.id || ''
  drawerTitle.value = `Template :: ${id}`
  try {
    drawerBody.value = await getPersonaTemplateRaw(id)
  } catch (e: any) {
    drawerBody.value = `[error] ${e?.response?.data?.detail || e.message}`
  }
  drawerOpen.value = true
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Persona Templates</h2>
        <div class="subtitle">{{ items.length }} 个模板（YAML 源自 agent 项目 templates_dir）</div>
      </div>
      <div class="actions">
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button size="small" type="primary" :icon="Download" @click="reload">重新加载</el-button>
      </div>
    </div>

    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column label="ID" min-width="220">
        <template #default="{ row }">
          <span class="mono">{{ row.template_id || row.id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Name" prop="name" width="220" />
      <el-table-column label="Version" prop="version" width="100" />
      <el-table-column label="Description" prop="description" show-overflow-tooltip />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button size="small" link @click="viewRaw(row)">查看 YAML</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="drawerOpen" :title="drawerTitle" size="60%" direction="rtl">
      <pre class="raw">{{ drawerBody }}</pre>
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
.raw {
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  border: 1px solid var(--eid-border-strong);
  border-radius: 6px;
  padding: 14px 16px;
  margin: 0;
  overflow: auto;
  font-family: var(--eid-font-mono);
  font-size: 12.5px;
  line-height: 1.55;
  max-height: 75vh;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
