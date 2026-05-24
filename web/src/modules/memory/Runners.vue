<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatUptime, listRunners, type RunnersResponse } from '@/api/memory'

const data = ref<RunnersResponse | null>(null)
const loading = ref(false)
const lastFetch = ref<Date | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  loading.value = true
  try {
    data.value = await listRunners()
    lastFetch.value = new Date()
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => {
    if (!loading.value) refresh()
  }, 5000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

const summary = computed(() => {
  if (!data.value) return { total: 0, running: 0, listening: 0 }
  return {
    total: data.value.runners.length,
    running: data.value.runners.filter((r) => r.running).length,
    listening: data.value.runners.filter((r) => r.listening).length,
  }
})

function statusTag(r: { running: boolean; listening: boolean; enabled: boolean }) {
  if (!r.enabled) return { type: 'info', label: 'disabled' }
  if (r.running && r.listening) return { type: 'success', label: 'running' }
  if (r.running && !r.listening) return { type: 'warning', label: 'process up, port closed' }
  if (!r.running) return { type: 'danger', label: 'stopped' }
  return { type: 'info', label: 'unknown' }
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Agent Runners</h2>
        <el-tag size="small" effect="dark">
          {{ summary.running }} / {{ summary.total }} running
        </el-tag>
        <el-tag v-if="data && data.orphans.length" size="small" type="warning" effect="dark">
          {{ data.orphans.length }} orphan{{ data.orphans.length > 1 ? 's' : '' }}
        </el-tag>
      </div>
      <div class="actions">
        <span class="last-fetch">{{ lastFetch ? `更新于 ${lastFetch.toLocaleTimeString()}` : '' }}</span>
        <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="data && !data.users_yaml_exists"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #title>
        users.yaml 不存在：<code>{{ data.users_yaml }}</code>
        ——
        设置环境变量 <code>EIDOLON_MEMORY_USERS_YAML</code> 或确保 memory 项目已初始化
      </template>
    </el-alert>

    <el-card>
      <template #header>
        <span>声明的用户（{{ data?.users_yaml || '...' }}）</span>
      </template>

      <el-table :data="data?.runners || []" stripe v-loading="loading">
        <el-table-column label="User ID" width="180">
          <template #default="{ row }">
            <span class="user-id">{{ row.user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Port" width="80">
          <template #default="{ row }">{{ row.port || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="180">
          <template #default="{ row }">
            <el-tag
              :type="(statusTag(row).type as any)"
              effect="dark"
              size="small"
            >
              {{ statusTag(row).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="PID" width="100">
          <template #default="{ row }">
            <span class="mono">{{ row.pid ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Uptime" width="120">
          <template #default="{ row }">{{ formatUptime(row.uptime_sec) }}</template>
        </el-table-column>
        <el-table-column label="RSS" width="100">
          <template #default="{ row }">
            {{ row.rss_mb !== null ? `${row.rss_mb} MB` : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="Palace">
          <template #default="{ row }">
            <span v-if="row.palace_path" class="path">{{ row.palace_path }}</span>
            <span v-else class="muted">(default)</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="data && data.orphans.length" style="margin-top: 16px" shadow="hover">
      <template #header>
        <span style="color: var(--eid-warning)">⚠ 孤儿进程</span>
        <span class="hint">
          这些 agent_runner 进程在运行，但 users.yaml 里找不到对应条目（通常因为 yaml 已修改但未 SIGHUP memory-supervisor）
        </span>
      </template>
      <el-table :data="data.orphans" size="small">
        <el-table-column label="User ID" prop="user_id" />
        <el-table-column label="PID">
          <template #default="{ row }"><span class="mono">{{ row.pid }}</span></template>
        </el-table-column>
        <el-table-column label="Uptime">
          <template #default="{ row }">{{ formatUptime(row.uptime_sec) }}</template>
        </el-table-column>
        <el-table-column label="RSS">
          <template #default="{ row }">{{ row.rss_mb ? `${row.rss_mb} MB` : '-' }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.last-fetch {
  font-size: 12px;
  color: var(--eid-text-muted);
}
.user-id {
  font-weight: 600;
  color: var(--eid-text-primary);
}
.mono {
  font-family: var(--eid-font-mono);
  font-size: 12px;
}
.path {
  font-family: var(--eid-font-mono);
  font-size: 12px;
  color: var(--eid-text-secondary);
}
.muted {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.hint {
  display: block;
  font-size: 12px;
  color: var(--eid-text-secondary);
  margin-top: 4px;
  font-weight: normal;
}
</style>
