<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getMetrics, getProbeHealth, type HubMetrics, type ProbeHealth } from '@/api/hub'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const metrics = ref<HubMetrics | null>(null)
const probe = ref<ProbeHealth | null>(null)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  try {
    const [m, p] = await Promise.all([getMetrics(), getProbeHealth()])
    metrics.value = m
    probe.value = p
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (!loading.value) load() }, 5_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="page">
    <div class="topbar">
      <h2 class="title">Metrics</h2>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-card>
      <template #header>
        <div class="bar">
          <span>Probe</span>
          <StatusBadge
            v-if="probe"
            :state="probe.running ? 'online' : 'offline'"
            :label="probe.running ? 'running' : 'stopped'"
          />
        </div>
      </template>
      <div v-if="probe" class="stats">
        <div class="stat-card">
          <div class="stat-label">Total cycles</div>
          <div class="stat-val">{{ probe.total_cycles }}</div>
        </div>
        <div class="stat-card" :class="{ warn: probe.consecutive_failures > 0 }">
          <div class="stat-label">Consecutive failures</div>
          <div class="stat-val">{{ probe.consecutive_failures }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Last success</div>
          <div class="stat-val small">{{ probe.last_success_at || '—' }}</div>
        </div>
        <div v-if="probe.last_error" class="stat-card danger">
          <div class="stat-label">Last error</div>
          <div class="stat-val small">{{ probe.last_error }}</div>
        </div>
      </div>
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header>Raw metrics</template>
      <JsonViewer :data="metrics" />
    </el-card>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat-card {
  background: var(--eid-bg-panel);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  padding: 14px 16px;
}
.stat-card.warn   { border-left: 3px solid var(--eid-warning); }
.stat-card.danger { border-left: 3px solid var(--eid-danger); }
.stat-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--eid-text-muted);
}
.stat-val { font-size: 22px; font-weight: 600; margin-top: 4px; }
.stat-val.small { font-size: 13px; font-family: var(--eid-font-mono); }
</style>
