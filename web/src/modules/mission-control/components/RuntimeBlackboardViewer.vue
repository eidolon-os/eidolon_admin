<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import type { OwnerView } from '@/api/eidolonData'
import {
  getRuntimeBlackboard,
  type RuntimeBlackboardEntry,
  type RuntimeBlackboardResponse,
} from '@/api/missionControl'
import JsonViewer from '@/modules/common/JsonViewer.vue'
import { blackboardCapabilities, blackboardDevices } from '../blackboard'

const props = defineProps<{
  owners: OwnerView[]
  /** Deterministic fixture injection for render tests; production loads on mount. */
  initialResponse?: RuntimeBlackboardResponse | null
}>()
defineEmits<{ (event: 'close'): void }>()

const root = ref<HTMLElement | null>(null)
const scope = ref('')
const mode = ref<'structured' | 'raw'>('structured')
const loading = ref(false)
const error = ref('')
const response = ref<RuntimeBlackboardResponse | null>(props.initialResponse || null)
let pollTimer: number | undefined

const entries = computed(() => response.value?.entries || [])
const ownerOptions = computed(() => {
  const labels = new Map(props.owners.map((owner) => [
    owner.owner_id,
    owner.display_name || owner.owner_id,
  ]))
  for (const entry of entries.value) {
    if (entry.owner_id && !labels.has(entry.owner_id)) labels.set(entry.owner_id, entry.owner_id)
  }
  return [...labels].map(([owner_id, label]) => ({ owner_id, label }))
    .sort((a, b) => a.label.localeCompare(b.label))
})
const deviceCount = computed(() => entries.value.reduce((total, entry) => total + blackboardDevices(entry).length, 0))
const capabilityCount = computed(() => entries.value.reduce(
  (total, entry) => total + blackboardDevices(entry).reduce(
    (count, row) => count + blackboardCapabilities(row.device).length,
    0,
  ),
  0,
))

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    response.value = await getRuntimeBlackboard(scope.value || undefined)
    error.value = ''
  } catch (cause: any) {
    error.value = cause?.response?.data?.detail || cause?.message || 'Runtime Blackboard unavailable'
  } finally {
    loading.value = false
  }
}

function toggleDetails(open: boolean) {
  root.value?.querySelectorAll<HTMLDetailsElement>('details').forEach((detail) => {
    detail.open = open
  })
}

function snapshotOf(entry: RuntimeBlackboardEntry) {
  return entry.snapshot
}

function fmt(value: unknown): string {
  if (!value) return '—'
  const date = new Date(String(value))
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

function isExpired(value: unknown): boolean {
  if (!value) return true
  const time = new Date(String(value)).getTime()
  return Number.isNaN(time) || time <= Date.now()
}

onMounted(() => {
  void load()
  pollTimer = window.setInterval(load, 5000)
})
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
watch(scope, () => void load())
</script>

<template>
  <aside ref="root" class="bb-shell" role="dialog" aria-modal="true" aria-label="Runtime Blackboard viewer">
    <div class="bb-panel">
      <header class="bb-head">
        <div>
          <span class="bb-kick">RUNTIME DEVICE BLACKBOARD · SHARED KV</span>
          <h2>共享 Runtime Blackboard</h2>
          <p>直接读取 <code>{{ response?.bucket || 'EIDOLON_RUNTIME_DEVICES' }}</code> 的 owner/current 快照 · READ ONLY</p>
        </div>
        <button class="bb-close" title="关闭" @click="$emit('close')">✕</button>
      </header>

      <div class="bb-toolbar">
        <el-select v-model="scope" class="bb-owner" filterable placeholder="全部 OWNER / CURRENT">
          <el-option label="全部 OWNER / CURRENT" value="" />
          <el-option v-for="owner in ownerOptions" :key="owner.owner_id" :label="owner.label" :value="owner.owner_id" />
        </el-select>
        <div class="bb-tabs" aria-label="查看模式">
          <button :class="{ active: mode === 'structured' }" @click="mode = 'structured'">STRUCTURED</button>
          <button :class="{ active: mode === 'raw' }" @click="mode = 'raw'">RAW JSON</button>
        </div>
        <template v-if="mode === 'structured'">
          <button class="bb-action" @click="toggleDetails(true)">展开全部</button>
          <button class="bb-action" @click="toggleDetails(false)">收起全部</button>
        </template>
        <span class="bb-stat">{{ entries.length }} owner · {{ deviceCount }} device · {{ capabilityCount }} capability</span>
        <button class="bb-refresh" :disabled="loading" @click="load"><el-icon :class="{ spin: loading }"><Refresh /></el-icon>REFRESH</button>
      </div>

      <p v-if="error" class="bb-error">// {{ error }}</p>

      <div v-if="mode === 'raw'" class="bb-raw">
        <JsonViewer :data="response" max-height="calc(100vh - 250px)" />
      </div>

      <div v-else class="bb-content">
        <p v-if="!loading && !entries.length && !error" class="bb-empty">未发现 owner/current snapshot</p>
        <details v-for="entry in entries" :key="entry.key" class="bb-owner-card" open>
          <summary>
            <span class="chevron">›</span>
            <strong>{{ entry.owner_id || 'UNRESOLVED OWNER' }}</strong>
            <code>{{ entry.key }}</code>
            <span v-if="snapshotOf(entry)" class="badge" :class="snapshotOf(entry)?.ready ? 'ok' : 'warn'">{{ snapshotOf(entry)?.ready ? 'READY' : 'NOT READY' }}</span>
            <span v-if="entry.error" class="badge bad">INVALID</span>
          </summary>

          <p v-if="entry.error" class="bb-entry-error">{{ entry.error }}</p>
          <template v-if="snapshotOf(entry)">
            <div class="bb-meta snapshot-meta">
              <div><span>OWNER</span><b>{{ snapshotOf(entry)?.owner_id }}</b></div>
              <div><span>SCHEMA</span><b>v{{ snapshotOf(entry)?.schema_version }}</b></div>
              <div><span>EPOCH</span><b>{{ snapshotOf(entry)?.epoch }}</b></div>
              <div><span>REVISION</span><b>#{{ snapshotOf(entry)?.revision }}</b></div>
              <div><span>READY</span><b :class="snapshotOf(entry)?.ready ? 'ok' : 'warn'">{{ snapshotOf(entry)?.ready }}</b></div>
              <div><span>HUB LEASE</span><b :class="isExpired(snapshotOf(entry)?.hub_lease_expires_at) ? 'bad' : 'ok'">{{ fmt(snapshotOf(entry)?.hub_lease_expires_at) }}</b></div>
              <div><span>UPDATED</span><b>{{ fmt(snapshotOf(entry)?.updated_at) }}</b></div>
              <div><span>DEVICES</span><b>{{ blackboardDevices(entry).length }}</b></div>
            </div>

            <section class="bb-devices">
              <details v-for="row in blackboardDevices(entry)" :key="row.mapKey" class="bb-device-card" open>
                <summary>
                  <span class="chevron">›</span>
                  <i class="status-dot" :class="row.device.status === 'online' && !isExpired(row.device.lease_expires_at) ? 'online' : 'offline'" />
                  <strong>{{ row.device.name || row.device.device_id }}</strong>
                  <code>{{ row.device.device_id }}</code>
                  <span class="badge">{{ row.device.status }}</span>
                  <span class="cap-count">{{ blackboardCapabilities(row.device).length }} contracts</span>
                </summary>

                <div class="bb-meta device-meta">
                  <div><span>MAP KEY / DEVICE ID</span><b>{{ row.mapKey }} / {{ row.device.device_id }}</b></div>
                  <div><span>REGISTRATION ID</span><b>{{ row.device.registration_id || '—' }}</b></div>
                  <div><span>REGISTERED</span><b>{{ fmt(row.device.registered_at) }}</b></div>
                  <div><span>DEVICE LEASE</span><b :class="isExpired(row.device.lease_expires_at) ? 'bad' : 'ok'">{{ fmt(row.device.lease_expires_at) }}</b></div>
                  <div><span>LAST SEEN</span><b>{{ fmt(row.device.last_seen_at) }}</b></div>
                  <div><span>PRESENCE REVISION</span><b>{{ row.device.presence_revision || '—' }}</b></div>
                  <div><span>PROVIDER COMPANION</span><b>{{ row.device.provider_companion_name || '—' }} · {{ row.device.provider_companion_id || 'unbound' }}</b></div>
                  <div><span>VISIBILITY</span><b>{{ row.device.visibility }}</b></div>
                  <div><span>ROOM / PARTICIPANT</span><b>{{ row.device.room_name || '—' }} / {{ row.device.participant_sid || '—' }}</b></div>
                  <div><span>MANIFEST REVISION</span><b>{{ row.device.manifest_revision || '—' }}</b></div>
                  <div><span>ALIASES</span><b>{{ row.device.aliases?.join(', ') || '—' }}</b></div>
                </div>

                <div class="bb-capabilities">
                  <p v-if="!blackboardCapabilities(row.device).length" class="bb-empty">此 registration 未声明 capability contract</p>
                  <details v-for="capability in blackboardCapabilities(row.device)" :key="`${capability.name}:${capability.version}`" class="bb-cap-card">
                    <summary>
                      <span class="chevron">›</span>
                      <strong>{{ capability.name }}</strong>
                      <span class="badge">CONTRACT v{{ capability.version }}</span>
                    </summary>
                    <p class="cap-description">{{ capability.description }}</p>
                    <div class="schema-grid">
                      <section><h4>INPUT SCHEMA</h4><JsonViewer :data="capability.input_schema" max-height="360px" /></section>
                      <section><h4>RESULT SCHEMA</h4><JsonViewer :data="capability.result_schema" max-height="360px" /></section>
                    </div>
                  </details>
                </div>
              </details>
            </section>
          </template>
        </details>
      </div>

      <footer class="bb-foot">
        <span>HUB IS THE ONLY WRITER · NO CACHE · NO WATCH</span>
        <span>GENERATED {{ fmt(response?.generated_at) }}</span>
      </footer>
    </div>
  </aside>
</template>

<style scoped>
.bb-shell { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 22px; background: rgba(2, 4, 14, .82); backdrop-filter: blur(12px); color: var(--cy-txt); font-family: var(--cy-mono); }
.bb-panel { width: min(1480px, 96vw); height: min(920px, 94vh); display: flex; flex-direction: column; overflow: hidden; border: 1px solid rgba(0, 234, 255, .55); background: linear-gradient(145deg, rgba(5, 12, 28, .99), rgba(10, 6, 24, .99)); box-shadow: 0 0 70px rgba(0, 234, 255, .16); clip-path: polygon(18px 0, 100% 0, 100% calc(100% - 18px), calc(100% - 18px) 100%, 0 100%, 0 18px); }
.bb-head { display: flex; justify-content: space-between; gap: 20px; padding: 20px 24px 14px; border-bottom: 1px solid rgba(0, 234, 255, .18); }
.bb-kick { color: var(--cy-mag); font: 800 10px/1 var(--cy-mono); letter-spacing: .14em; }
.bb-head h2 { margin: 7px 0 5px; color: #fff; font: 800 25px/1.1 var(--cy-sans); }
.bb-head p { margin: 0; color: var(--cy-txt-dim); font-size: 10px; } .bb-head code { color: var(--cy-cyan); }
.bb-close { align-self: flex-start; width: 34px; height: 34px; border: 1px solid var(--cy-mag); color: var(--cy-mag); background: rgba(255, 46, 136, .08); cursor: pointer; }
.bb-toolbar { display: flex; align-items: center; gap: 8px; padding: 10px 24px; border-bottom: 1px solid rgba(0, 234, 255, .12); }
.bb-owner { width: 230px; }
.bb-tabs { display: flex; border: 1px solid rgba(0, 234, 255, .28); }
.bb-tabs button, .bb-action, .bb-refresh { min-height: 32px; padding: 0 11px; border: 0; color: var(--cy-txt-dim); background: transparent; font: 700 9px/1 var(--cy-mono); letter-spacing: .06em; cursor: pointer; }
.bb-tabs button.active { color: #041018; background: var(--cy-cyan); }
.bb-action { border: 1px solid rgba(255, 255, 255, .12); }
.bb-refresh { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; border: 1px solid var(--cy-cyan); color: var(--cy-cyan); }
.bb-stat { margin-left: 8px; color: var(--cy-txt-dim); font-size: 9px; white-space: nowrap; }
.bb-error, .bb-entry-error { margin: 10px 24px 0; padding: 9px 12px; border: 1px solid var(--cy-mag); color: var(--cy-mag); background: rgba(255, 46, 136, .08); font-size: 11px; }
.bb-content, .bb-raw { flex: 1; min-height: 0; overflow: auto; padding: 14px 24px 20px; }
.bb-owner-card, .bb-device-card, .bb-cap-card { margin-bottom: 10px; border: 1px solid rgba(0, 234, 255, .18); background: rgba(0, 234, 255, .025); }
summary { display: flex; align-items: center; gap: 9px; min-height: 42px; padding: 0 13px; cursor: pointer; list-style: none; } summary::-webkit-details-marker { display: none; }
.chevron { color: var(--cy-cyan); font-size: 20px; transition: transform .15s; } details[open] > summary > .chevron { transform: rotate(90deg); }
summary strong { color: #fff; font: 750 12px/1.2 var(--cy-sans); } summary code { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--cy-txt-dim); font-size: 9px; white-space: nowrap; }
.badge { margin-left: auto; padding: 3px 6px; border: 1px solid rgba(0, 234, 255, .35); color: var(--cy-cyan); font-size: 8px; white-space: nowrap; }
.badge.ok, b.ok { color: var(--cy-green); border-color: var(--cy-green); } .badge.warn, b.warn { color: var(--cy-yellow); border-color: var(--cy-yellow); } .badge.bad, b.bad { color: var(--cy-mag); border-color: var(--cy-mag); }
.bb-meta { display: grid; gap: 1px; padding: 0 13px 13px; }
.snapshot-meta { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.device-meta { grid-template-columns: repeat(3, minmax(0, 1fr)); padding-top: 8px; }
.bb-meta > div { min-width: 0; padding: 9px 10px; border: 1px solid rgba(255, 255, 255, .055); background: rgba(255, 255, 255, .018); }
.bb-meta span { display: block; margin-bottom: 5px; color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .08em; }
.bb-meta b { display: block; overflow-wrap: anywhere; color: #dce8ff; font: 650 10px/1.4 var(--cy-mono); }
.bb-devices { padding: 0 13px 4px; } .bb-device-card { border-color: rgba(164, 75, 255, .22); background: rgba(164, 75, 255, .025); }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cy-mag); box-shadow: 0 0 8px currentColor; } .status-dot.online { color: var(--cy-green); background: currentColor; } .status-dot.offline { color: var(--cy-mag); background: currentColor; }
.cap-count { color: var(--cy-txt-dim); font-size: 9px; }
.bb-capabilities { padding: 0 13px 4px; } .bb-cap-card { border-color: rgba(255, 214, 64, .18); background: rgba(255, 214, 64, .018); }
.cap-description { margin: 0 13px 10px; padding: 10px 12px; border-left: 2px solid var(--cy-yellow); color: #dce8ff; font: 500 11px/1.55 var(--cy-sans); background: rgba(255, 214, 64, .035); }
.schema-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 0 13px 13px; }
.schema-grid section { min-width: 0; border: 1px solid rgba(255, 255, 255, .07); } .schema-grid h4 { margin: 0; padding: 8px 10px; color: var(--cy-yellow); font-size: 9px; border-bottom: 1px solid rgba(255, 255, 255, .07); }
.schema-grid :deep(.json) { background: rgba(0, 0, 0, .22); color: #b9c9e9; }
.bb-empty { padding: 18px; text-align: center; color: var(--cy-txt-dim); font-size: 11px; }
.bb-foot { display: flex; justify-content: space-between; padding: 9px 24px; border-top: 1px solid rgba(0, 234, 255, .12); color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .08em; }
.spin { animation: spin 900ms linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 900px) { .bb-shell { padding: 0; } .bb-panel { width: 100vw; height: 100vh; clip-path: none; } .bb-toolbar { flex-wrap: wrap; } .bb-stat { display: none; } .snapshot-meta, .device-meta { grid-template-columns: 1fr 1fr; } .schema-grid { grid-template-columns: 1fr; } }
</style>
