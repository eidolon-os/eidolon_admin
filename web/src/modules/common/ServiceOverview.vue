<script setup lang="ts">
// Shared status dashboard for process-only services (channel, client-web) whose
// only observable surface is supervisord process info (+ optional HTTP probe),
// read from /api/overview/services. Config is folded in as a second tab.
// Per-service copy comes via slots/props.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { formatUptime, getOverview, stateTagType, type ServiceStatus } from '@/api/overview'
import StatusBadge from './StatusBadge.vue'
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
const loading = ref(false)
const tab = ref('status')
let timer: ReturnType<typeof setInterval> | null = null

const programInfo = computed(
  () => status.value?.programs.find((p) => p.full_name === props.program) ?? null,
)

async function load() {
  loading.value = true
  try {
    const o = await getOverview()
    status.value = o.services.find((s) => s.id === props.serviceId) || null
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (!loading.value) load() }, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

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
        services.yaml 没有为 {{ serviceId }} 声明 supervisord 进程，这里不显示进程详情。
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
            <div class="stat-val">{{ programInfo?.statename === 'RUNNING' ? formatUptime(programInfo.uptime_sec) : '—' }}</div>
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
            <div class="stat-label">Detail</div>
            <div class="stat-val small">{{ programInfo?.spawnerr || programInfo?.description || '—' }}</div>
          </div>
        </div>

        <slot name="extra" />
      </el-tab-pane>

      <el-tab-pane v-if="configLoader" label="配置" name="config">
        <ServiceConfig :loader="configLoader">
          <template #note><slot name="config-note">只读视图。修改后在「主机服务」(Host Services) 页重启对应服务生效。</slot></template>
        </ServiceConfig>
      </el-tab-pane>
    </el-tabs>
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
.muted { color: var(--eid-text-muted); }
.ok { color: var(--eid-success); }
.bad { color: var(--eid-danger); }
</style>
