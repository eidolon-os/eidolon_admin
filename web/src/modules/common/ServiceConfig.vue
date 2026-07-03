<script setup lang="ts">
// Shared read-only .env viewer for process-only services (channel, client-web).
// Pass a loader returning { env_file, entries }; override the note via the slot.
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

interface EnvEntry { key: string; value: string; masked: boolean }
interface EnvResponse { env_file: string; entries: EnvEntry[] }

const props = defineProps<{ loader: () => Promise<EnvResponse>; title?: string }>()

const entries = ref<EnvEntry[]>([])
const envFile = ref('')
const loading = ref(false)
const filter = ref('')

async function load() {
  loading.value = true
  try {
    const r = await props.loader()
    envFile.value = r.env_file
    entries.value = r.entries
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}
onMounted(load)

function visible() {
  const q = filter.value.trim().toLowerCase()
  return q ? entries.value.filter((e) => e.key.toLowerCase().includes(q)) : entries.value
}
</script>

<template>
  <div class="svc-config">
    <div class="topbar">
      <div>
        <h3 v-if="title" class="title">{{ title }}</h3>
        <div class="subtitle mono">{{ envFile }}</div>
      </div>
      <div class="actions">
        <el-input v-model="filter" placeholder="过滤 key" size="small" style="width: 220px" clearable />
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
      <template #title>
        <slot name="note">只读视图 — .env 由子项目自身维护。修改后在 Supervisor 页重启对应进程生效。</slot>
      </template>
    </el-alert>

    <el-table :data="visible()" v-loading="loading" stripe size="small">
      <el-table-column label="Key" width="320">
        <template #default="{ row }"><span class="mono key">{{ row.key }}</span></template>
      </el-table-column>
      <el-table-column label="Value">
        <template #default="{ row }">
          <span :class="['mono', { masked: row.masked }]">{{ row.value || '(empty)' }}</span>
          <el-tag v-if="row.masked" size="small" type="warning" effect="plain" style="margin-left: 8px">masked</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.svc-config { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.title { margin: 0; font-size: 15px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.actions { display: flex; gap: 8px; align-items: center; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.key { color: var(--eid-text-primary); font-weight: 500; }
.masked { color: var(--eid-text-muted); }
</style>
