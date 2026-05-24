<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getOverview, type ServiceStatus } from '@/api/overview'
import { getProgram, formatUptime, type ProgramInfo, stateTagType, tailLogUrl } from '@/api/supervisor'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import LogViewer from '@/modules/common/LogViewer.vue'

// Channel has no HTTP/NATS admin — this Overview is a status dashboard built
// from supervisor process info + service-level health.

const status = ref<ServiceStatus | null>(null)
const program = ref<ProgramInfo | null>(null)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const logOpen = ref(false)
const logStream = ref<'stdout' | 'stderr'>('stdout')

async function load() {
  loading.value = true
  try {
    const o = await getOverview()
    status.value = o.services.find((s) => s.id === 'channel') || null
    try {
      program.value = await getProgram('channel:channel-worker')
    } catch {
      program.value = null
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (!loading.value) load() }, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function openLog(stream: 'stdout' | 'stderr') {
  logStream.value = stream
  logOpen.value = true
}

const overall = computed<'online' | 'offline' | 'starting' | 'unknown'>(() => {
  if (!program.value) return 'unknown'
  switch (program.value.statename) {
    case 'RUNNING': return 'online'
    case 'STARTING': return 'starting'
    case 'STOPPED':
    case 'EXITED':
    case 'FATAL':
    case 'BACKOFF':
      return 'offline'
    default: return 'unknown'
  }
})
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Channel Worker</h2>
        <StatusBadge :state="overall" :label="program?.statename || 'unknown'" />
      </div>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="status && !status.supervised"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #title>
        channel.conf 尚未启用。进入 Supervisor 页面打开开关后这里会显示进程详情。
      </template>
    </el-alert>

    <div class="stats">
      <div class="stat-card">
        <div class="stat-label">State</div>
        <div class="stat-val">
          <el-tag v-if="program" :type="stateTagType(program.statename)" effect="dark">
            {{ program.statename }}
          </el-tag>
          <span v-else class="muted">—</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">PID</div>
        <div class="stat-val mono">{{ program?.pid || '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Uptime</div>
        <div class="stat-val">
          {{ program?.statename === 'RUNNING' ? formatUptime(program.start, program.now) : '—' }}
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Last exit</div>
        <div class="stat-val small">
          {{ program?.exitstatus !== undefined ? program.exitstatus : '—' }}
        </div>
      </div>
    </div>

    <el-card v-if="program" style="margin-top: 16px">
      <template #header>Logs</template>
      <p class="hint">channel 是纯 LiveKit worker，没有 HTTP / NATS admin 接口。状态和日志是当前可见的全部维度。</p>
      <el-space>
        <el-button @click="openLog('stdout')">查看 stdout (follow)</el-button>
        <el-button @click="openLog('stderr')">查看 stderr (follow)</el-button>
      </el-space>
      <div class="log-files">
        <div class="mono">{{ program.stdout_logfile }}</div>
        <div class="mono">{{ program.stderr_logfile }}</div>
      </div>
    </el-card>

    <LogViewer
      v-model:open="logOpen"
      :title="`channel-worker :: ${logStream}`"
      :url="tailLogUrl('channel:channel-worker', logStream)"
    />
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat-card {
  background: var(--eid-bg-panel);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  padding: 14px 16px;
}
.stat-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--eid-text-muted);
}
.stat-val { font-size: 18px; font-weight: 600; margin-top: 4px; }
.stat-val.small { font-size: 13px; font-family: var(--eid-font-mono); }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.hint { font-size: 12px; color: var(--eid-text-secondary); margin-bottom: 12px; }
.log-files { margin-top: 12px; color: var(--eid-text-muted); }
.muted { color: var(--eid-text-muted); }
</style>
