<script setup lang="ts">
// Shared status dashboard for process-only services (channel, client-web) whose
// only observable surface is supervisord process info (+ optional HTTP probe).
// Config is folded in as a second tab. Per-service copy comes via slots/props.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getOverview, type ServiceStatus } from '@/api/overview'
import { getProgram, formatUptime, type ProgramInfo, stateTagType, tailLogUrl } from '@/api/supervisor'
import StatusBadge from './StatusBadge.vue'
import LogViewer from './LogViewer.vue'
import ServiceConfig from './ServiceConfig.vue'

interface EnvResponse { env_file: string; entries: { key: string; value: string; masked: boolean }[] }

const props = defineProps<{
  serviceId: string
  program: string
  title: string
  showHttpProbe?: boolean
  configLoader?: () => Promise<EnvResponse>
}>()

const status = ref<ServiceStatus | null>(null)
const programInfo = ref<ProgramInfo | null>(null)
const loading = ref(false)
const tab = ref('status')
let timer: ReturnType<typeof setInterval> | null = null

const logOpen = ref(false)
const logStream = ref<'stdout' | 'stderr'>('stdout')

async function load() {
  loading.value = true
  try {
    const o = await getOverview()
    status.value = o.services.find((s) => s.id === props.serviceId) || null
    try {
      programInfo.value = await getProgram(props.program)
    } catch {
      programInfo.value = null
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
  if (!programInfo.value) return 'unknown'
  switch (programInfo.value.statename) {
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
const httpProbe = computed(() => status.value?.http_probe)
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">{{ title }}</h2>
        <StatusBadge :state="overall" :label="programInfo?.statename || 'unknown'" />
      </div>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="status && !status.supervised" type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>
        {{ serviceId }}.conf 尚未启用。进入 Supervisor 页面打开开关后这里会显示进程详情。
      </template>
    </el-alert>

    <el-tabs v-model="tab" class="svc-tabs">
      <el-tab-pane label="状态" name="status">
        <div class="stats">
          <div class="stat-card">
            <div class="stat-label">State</div>
            <div class="stat-val">
              <el-tag v-if="programInfo" :type="stateTagType(programInfo.statename)" effect="dark">{{ programInfo.statename }}</el-tag>
              <span v-else class="muted">—</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-label">PID</div>
            <div class="stat-val mono">{{ programInfo?.pid || '—' }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Uptime</div>
            <div class="stat-val">{{ programInfo?.statename === 'RUNNING' ? formatUptime(programInfo.start, programInfo.now) : '—' }}</div>
          </div>
          <div v-if="showHttpProbe" class="stat-card">
            <div class="stat-label">HTTP probe</div>
            <div class="stat-val small">
              <span v-if="httpProbe?.ok" class="ok">{{ httpProbe.status_code }} ({{ httpProbe.latency_ms }}ms)</span>
              <span v-else-if="httpProbe?.configured" class="bad">{{ httpProbe.error || `HTTP ${httpProbe.status_code}` }}</span>
              <template v-else>—</template>
            </div>
          </div>
          <div v-else class="stat-card">
            <div class="stat-label">Last exit</div>
            <div class="stat-val small">{{ programInfo?.exitstatus !== undefined ? programInfo.exitstatus : '—' }}</div>
          </div>
        </div>

        <el-card v-if="programInfo" style="margin-top: 16px">
          <template #header>Logs</template>
          <p class="hint"><slot name="log-note">状态和日志是当前可见的全部维度。</slot></p>
          <el-space>
            <el-button @click="openLog('stdout')">查看 stdout (follow)</el-button>
            <el-button @click="openLog('stderr')">查看 stderr (follow)</el-button>
          </el-space>
          <div class="log-files">
            <div class="mono">{{ programInfo.stdout_logfile }}</div>
            <div class="mono">{{ programInfo.stderr_logfile }}</div>
          </div>
        </el-card>

        <slot name="extra" />
      </el-tab-pane>

      <el-tab-pane v-if="configLoader" label="配置" name="config">
        <ServiceConfig :loader="configLoader">
          <template #note><slot name="config-note">只读视图。修改后在 Supervisor 页重启对应进程生效。</slot></template>
        </ServiceConfig>
      </el-tab-pane>
    </el-tabs>

    <LogViewer
      v-model:open="logOpen"
      :title="`${serviceId} :: ${logStream}`"
      :url="tailLogUrl(program, logStream)"
    />
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat-card { background: var(--eid-bg-panel); border: 1px solid var(--eid-border); border-radius: var(--eid-radius); padding: 14px 16px; }
.stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--eid-text-muted); }
.stat-val { font-size: 18px; font-weight: 600; margin-top: 4px; }
.stat-val.small { font-size: 13px; font-family: var(--eid-font-mono); }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.hint { font-size: 12px; color: var(--eid-text-secondary); margin-bottom: 12px; }
.log-files { margin-top: 12px; color: var(--eid-text-muted); }
.muted { color: var(--eid-text-muted); }
.ok { color: var(--eid-success); }
.bad { color: var(--eid-danger); }
</style>
