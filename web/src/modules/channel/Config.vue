<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getChannelConfig, type ChannelConfigEntry } from '@/api/channel'

const entries = ref<ChannelConfigEntry[]>([])
const envFile = ref('')
const loading = ref(false)
const filter = ref('')

async function load() {
  loading.value = true
  try {
    const r = await getChannelConfig()
    envFile.value = r.env_file
    entries.value = r.entries
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const visible = () => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter((e) => e.key.toLowerCase().includes(q))
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Channel Config</h2>
        <div class="subtitle mono">{{ envFile }}</div>
      </div>
      <div class="actions">
        <el-input v-model="filter" placeholder="过滤 key" size="small" style="width: 220px" clearable />
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      <template #title>
        只读视图 — channel 的 .env 由 channel 项目自己维护。如需修改，编辑
        <code>{{ envFile }}</code> 后在 Supervisor 页 restart channel-worker。
      </template>
    </el-alert>

    <el-table :data="visible()" v-loading="loading" stripe size="small">
      <el-table-column label="Key" width="320">
        <template #default="{ row }"><span class="mono key">{{ row.key }}</span></template>
      </el-table-column>
      <el-table-column label="Value">
        <template #default="{ row }">
          <span :class="['mono', { masked: row.masked }]">{{ row.value || '(empty)' }}</span>
          <el-tag v-if="row.masked" size="small" type="warning" effect="plain" style="margin-left: 8px">
            masked
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.actions { display: flex; gap: 8px; align-items: center; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.key { color: var(--eid-text-primary); font-weight: 500; }
.masked { color: var(--eid-text-muted); }
</style>
