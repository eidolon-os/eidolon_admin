<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { listAgentDevices, revokeAgentDevice, type AgentDevice } from '@/api/agent'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const items = ref<AgentDevice[]>([])
const loading = ref(false)
const detail = ref<AgentDevice | null>(null)

async function load() {
  loading.value = true
  try {
    items.value = await listAgentDevices()
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function revoke(d: AgentDevice) {
  await ElMessageBox.confirm(`吊销设备 ${d.device_id}？`, '确认', { type: 'warning' })
  await revokeAgentDevice(d.device_id)
  ElMessage.success('已吊销')
  await load()
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <h2 class="title">Devices (agent)</h2>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column label="Device ID" min-width="240">
        <template #default="{ row }"><span class="mono">{{ row.device_id }}</span></template>
      </el-table-column>
      <el-table-column label="Tenant / User" min-width="200">
        <template #default="{ row }">
          <span class="mono" v-if="row.tenant_id">{{ row.tenant_id }} / {{ row.user_id || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Paired at" prop="paired_at" width="180" />
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button size="small" link @click="detail = row">详情</el-button>
          <el-button size="small" link type="danger" @click="revoke(row)">吊销</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && items.length === 0" description="无设备（agent 当前 stub 实现返回空数组）" />

    <el-drawer
      :model-value="!!detail"
      @update:model-value="(v: boolean) => { if (!v) detail = null }"
      :title="detail ? `Device · ${detail.device_id}` : ''"
      size="50%"
      direction="rtl"
    >
      <JsonViewer v-if="detail" :data="detail" />
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
</style>
