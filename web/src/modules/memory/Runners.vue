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
  if (!data.value) return { total: 0, running: 0, listening: 0, consRunning: 0 }
  return {
    total: data.value.runners.length,
    running: data.value.runners.filter((r) => r.running).length,
    listening: data.value.runners.filter((r) => r.listening).length,
    consRunning: data.value.runners.filter((r) => r.consolidator?.running).length,
  }
})

function statusTag(r: { running: boolean; listening: boolean; enabled: boolean }) {
  if (!r.enabled) return { type: 'info', label: 'disabled' }
  if (r.running && r.listening) return { type: 'success', label: 'running' }
  if (r.running && !r.listening) return { type: 'warning', label: 'process up, port closed' }
  if (!r.running) return { type: 'danger', label: 'stopped' }
  return { type: 'info', label: 'unknown' }
}

function consolidatorTag(c: {
  configured: boolean
  enabled: boolean
  running: boolean
}) {
  if (!c.configured) return { type: 'info', label: 'off' }
  if (!c.enabled) return { type: 'info', label: 'configured, disabled' }
  if (c.running) return { type: 'success', label: 'running' }
  return { type: 'warning', label: 'enabled, down' }
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Runners & Workers</h2>
        <el-tag size="small" effect="dark">
          agent {{ summary.running }} / {{ summary.total }}
        </el-tag>
        <el-tag size="small" type="info" effect="plain">
          consolidator {{ summary.consRunning }}
        </el-tag>
        <el-tag v-if="data && data.orphans.length" size="small" type="warning" effect="dark">
          {{ data.orphans.length }} agent orphan{{ data.orphans.length > 1 ? 's' : '' }}
        </el-tag>
        <el-tag
          v-if="data && data.consolidator_orphans?.length"
          size="small"
          type="warning"
          effect="dark"
        >
          {{ data.consolidator_orphans.length }} consolidator orphan{{
            data.consolidator_orphans.length > 1 ? 's' : ''
          }}
        </el-tag>
      </div>
      <div class="actions">
        <span class="last-fetch">{{ lastFetch ? `更新于 ${lastFetch.toLocaleTimeString()}` : '' }}</span>
        <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="data && !data.users_source_exists"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #title>
        用户数据源不可用：<code>{{ data.users_source }}</code>
        ——
        确认 Eidolon Data SQLite 和 admin-api 正常
      </template>
    </el-alert>

    <el-card>
      <template #header>
        <span>声明的用户（{{ data?.users_source || '...' }}）</span>
      </template>

      <el-table :data="data?.runners || []" stripe v-loading="loading">
        <el-table-column label="User ID" width="160">
          <template #default="{ row }">
            <span class="user-id">{{ row.user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Port" width="72">
          <template #default="{ row }">{{ row.port || '-' }}</template>
        </el-table-column>
        <el-table-column label="Agent" width="168">
          <template #default="{ row }">
            <el-tag
              :type="(statusTag(row).type as any)"
              effect="dark"
              size="small"
            >
              {{ statusTag(row).label }}
            </el-tag>
            <span v-if="row.pid" class="mono pid-hint">pid {{ row.pid }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Consolidator" width="180">
          <template #default="{ row }">
            <el-tag
              :type="(consolidatorTag(row.consolidator).type as any)"
              effect="plain"
              size="small"
            >
              {{ consolidatorTag(row.consolidator).label }}
            </el-tag>
            <span v-if="row.consolidator?.pid" class="mono pid-hint">
              pid {{ row.consolidator.pid }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="Uptime" width="100">
          <template #default="{ row }">{{ formatUptime(row.uptime_sec) }}</template>
        </el-table-column>
        <el-table-column label="日志" min-width="200">
          <template #default="{ row }">
            <div class="log-links">
              <code v-if="row.agent_log_path" class="log-path" :title="row.agent_log_path">
                agent
              </code>
              <code
                v-if="row.consolidator?.log_path"
                class="log-path"
                :title="row.consolidator.log_path"
              >
                consolidator
              </code>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="Palace" min-width="160">
          <template #default="{ row }">
            <span v-if="row.palace_path" class="path">{{ row.palace_path }}</span>
            <span v-else class="muted">(default)</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card
      v-if="data && (data.orphans.length || data.consolidator_orphans?.length)"
      style="margin-top: 16px"
      shadow="hover"
    >
      <template #header>
        <span style="color: var(--eid-warning)">⚠ 孤儿进程</span>
        <span class="hint">
          进程在运行但 Eidolon Data 无对应 owner（通常需触发 memory-supervisor reconcile）
        </span>
      </template>
      <el-table
        :data="[
          ...data.orphans.map((o) => ({ ...o, role: o.role || 'agent' })),
          ...(data.consolidator_orphans || []).map((o) => ({
            ...o,
            role: o.role || 'consolidator',
          })),
        ]"
        size="small"
      >
        <el-table-column label="Role" prop="role" width="110" />
        <el-table-column label="User ID" prop="user_id" />
        <el-table-column label="PID">
          <template #default="{ row }"><span class="mono">{{ row.pid }}</span></template>
        </el-table-column>
        <el-table-column label="Uptime">
          <template #default="{ row }">{{ formatUptime(row.uptime_sec) }}</template>
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
  flex-wrap: wrap;
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
.pid-hint {
  margin-left: 6px;
  color: var(--eid-text-muted);
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
.log-links {
  display: flex;
  gap: 8px;
}
.log-path {
  font-size: 11px;
  cursor: help;
  color: var(--eid-text-secondary);
}
</style>
