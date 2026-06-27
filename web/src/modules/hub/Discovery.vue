<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getDiscoveryStatus, type HubDiscoveryStatus } from '@/api/hub'
import JsonViewer from '@/modules/common/JsonViewer.vue'
import StatusBadge from '@/modules/common/StatusBadge.vue'

const discovery = ref<HubDiscoveryStatus | null>(null)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  try {
    discovery.value = await getDiscoveryStatus()
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (!loading.value) void load() }, 5_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="page">
    <div class="topbar">
      <h2 class="title">Discovery</h2>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-card>
      <template #header>
        <div class="bar">
          <span>mDNS</span>
          <StatusBadge
            v-if="discovery"
            :state="discovery.registered ? 'online' : 'warning'"
            :label="discovery.registered ? 'broadcasting' : 'stopped'"
          />
        </div>
      </template>

      <div v-if="discovery" class="grid">
        <div class="field">
          <span class="label">Service</span>
          <span class="value mono">{{ discovery.service_type }}</span>
        </div>
        <div class="field">
          <span class="label">Host</span>
          <span class="value mono">{{ discovery.hostname }} · {{ discovery.ip }}:{{ discovery.port }}</span>
        </div>
        <div class="field wide">
          <span class="label">TXT config_url</span>
          <span class="value mono">{{ discovery.config_url || '—' }}</span>
        </div>
        <div class="field">
          <span class="label">Registered at</span>
          <span class="value mono">{{ discovery.last_registered_at || '—' }}</span>
        </div>
        <div class="field">
          <span class="label">Updated at</span>
          <span class="value mono">{{ discovery.last_updated_at || '—' }}</span>
        </div>
        <div v-if="discovery.last_error" class="field wide error">
          <span class="label">Last error</span>
          <span class="value mono">{{ discovery.last_error }}</span>
        </div>
      </div>
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header>Raw discovery</template>
      <JsonViewer :data="discovery" />
    </el-card>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  background: var(--eid-bg-panel);
}
.field.wide { grid-column: 1 / -1; }
.field.error { border-left: 3px solid var(--eid-danger); }
.label {
  color: var(--eid-text-muted);
  font-size: 11px;
  letter-spacing: 0;
  text-transform: uppercase;
}
.value { min-width: 0; overflow-wrap: anywhere; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
</style>
