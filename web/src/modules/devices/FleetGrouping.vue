<script setup lang="ts">
// Owner → companion ownership overview for the Device Center, backed by the
// server-side /api/devices/fleet join (hub presence/approval + data ownership)
// so the banner reflects real online state in a single call.
import { onMounted, ref, watch } from 'vue'
import { getFleet, type FleetResponse } from '@/api/devices'

const props = defineProps<{ ownerId: string }>()

const fleet = ref<FleetResponse | null>(null)
const loading = ref(false)

async function load() {
  if (!props.ownerId) {
    fleet.value = null
    return
  }
  loading.value = true
  try {
    fleet.value = await getFleet(props.ownerId)
  } catch {
    fleet.value = null
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(() => props.ownerId, load)
</script>

<template>
  <div v-if="ownerId" class="fleet-grouping" v-loading="loading">
    <div class="fg-cap">归属概览 · OWNER → COMPANION → 身体</div>
    <div class="fg-grid">
      <div v-for="g in fleet?.groups || []" :key="g.companion_id" class="fg-card">
        <div class="fg-h"><b>{{ g.companion_name }}</b><em>{{ g.devices.length }} 身体</em></div>
        <ul>
          <li v-for="d in g.devices" :key="d.device_id"><i class="dot" :class="{ on: d.online }" />{{ d.name || d.device_id }}<span>{{ d.kind }}</span></li>
          <li v-if="!g.devices.length" class="empty">未绑定身体</li>
        </ul>
      </div>
      <div v-if="fleet?.unbound?.length" class="fg-card unbound">
        <div class="fg-h"><b>未认领</b><em>{{ fleet.unbound.length }}</em></div>
        <ul>
          <li v-for="d in fleet.unbound" :key="d.device_id"><i class="dot" :class="{ on: d.online }" />{{ d.name || d.device_id }}<span>{{ d.kind }}</span></li>
        </ul>
      </div>
      <div v-if="!(fleet?.groups?.length) && !(fleet?.unbound?.length)" class="fg-empty">当前 owner 暂无设备</div>
    </div>
  </div>
</template>

<style scoped>
.fleet-grouping { margin-bottom: 14px; padding: 12px 14px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.fg-cap { font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: 0.1em; color: var(--eid-text-muted); margin-bottom: 10px; }
.fg-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
.fg-card { border: 1px solid var(--eid-border); border-radius: 6px; padding: 8px 10px; background: var(--eid-bg-elev); }
.fg-card.unbound { border-style: dashed; }
.fg-h { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.fg-h b { font-size: 13px; font-weight: 700; color: var(--eid-text-primary); }
.fg-h em { font-family: var(--eid-font-mono); font-size: 10px; font-style: normal; color: var(--eid-text-muted); }
.fg-card ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 3px; }
.fg-card li { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--eid-text-secondary); }
.fg-card li span { margin-left: auto; font-family: var(--eid-font-mono); font-size: 10px; color: var(--eid-text-muted); }
.fg-card li.empty { color: var(--eid-text-muted); font-style: italic; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--eid-text-muted); flex: 0 0 auto; }
.dot.on { background: var(--eid-success); box-shadow: 0 0 6px var(--eid-success); }
.fg-empty { color: var(--eid-text-muted); font-size: 12px; padding: 8px; }
</style>
