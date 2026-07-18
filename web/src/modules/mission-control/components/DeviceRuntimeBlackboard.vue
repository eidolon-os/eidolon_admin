<script setup lang="ts">
import { computed } from 'vue'
import type { RuntimeBlackboardDevice, RuntimeCapabilityContract } from '@/api/missionControl'
import type { MissionControlStream } from '../useMissionControlStream'

const props = defineProps<{ mc: MissionControlStream }>()
const blackboard = props.mc.runtimeBlackboard

interface CompanionGroup {
  id: string
  name: string
  devices: RuntimeBlackboardDevice[]
}

const groups = computed<CompanionGroup[]>(() => {
  const rows = Object.values(blackboard.value?.snapshot?.devices || {})
  const grouped = new Map<string, CompanionGroup>()
  for (const device of rows) {
    const id = device.provider_companion_id || '__unbound__'
    const group = grouped.get(id) || {
      id,
      name: device.provider_companion_name || device.provider_companion_id || '未绑定 Companion',
      devices: [],
    }
    group.devices.push(device)
    grouped.set(id, group)
  }
  return [...grouped.values()]
    .map((group) => ({ ...group, devices: group.devices.sort((a, b) => a.device_id.localeCompare(b.device_id)) }))
    .sort((a, b) => a.name.localeCompare(b.name))
})

const deviceCount = computed(() => groups.value.reduce((sum, group) => sum + group.devices.length, 0))
const onlineCount = computed(() => groups.value.reduce(
  (sum, group) => sum + group.devices.filter((device) => device.status === 'online' && !isExpired(device.lease_expires_at)).length,
  0,
))
const capabilityCount = computed(() => groups.value.reduce(
  (sum, group) => sum + group.devices.reduce((count, device) => count + device.capabilities.length, 0),
  0,
))

function isExpired(value: string): boolean {
  const timestamp = Date.parse(value)
  return !Number.isFinite(timestamp) || timestamp <= Date.now()
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value
}

function short(value: string, length = 14): string {
  if (!value) return '—'
  return value.length > length ? `${value.slice(0, length)}…` : value
}

function schemaSummary(contract: RuntimeCapabilityContract): string {
  const properties = Object.keys(contract.input_schema?.properties || {})
  const required = Array.isArray(contract.input_schema?.required) ? contract.input_schema.required.length : 0
  return `${properties.length} inputs · ${required} required · result ${contract.result_schema?.type || 'any'}`
}

function raw(value: unknown): string {
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <section class="blackboard" :class="`health-${blackboard?.health || 'degraded'}`">
    <header class="bb-head">
      <div class="bb-title">
        <span class="bb-led" />
        <div><b>DEVICE RUNTIME BLACKBOARD</b><small>NATS KV · OWNER CURRENT SNAPSHOT · READ ONLY</small></div>
      </div>
      <div class="bb-health">
        <strong>{{ (blackboard?.health || 'degraded').toUpperCase() }}</strong>
        <span>{{ blackboard?.detail || 'Runtime blackboard unavailable' }}</span>
      </div>
      <div class="bb-counts">
        <span><b>{{ onlineCount }}</b>/{{ deviceCount }} online</span>
        <span><b>{{ capabilityCount }}</b> contracts</span>
      </div>
    </header>

    <div v-if="blackboard?.snapshot" class="bb-meta">
      <span><i>SCHEMA</i>v{{ blackboard.snapshot.schema_version }}</span>
      <span><i>READY</i>{{ blackboard.snapshot.ready ? 'YES' : 'NO' }}</span>
      <span><i>EPOCH</i><code :title="blackboard.snapshot.epoch">{{ short(blackboard.snapshot.epoch) }}</code></span>
      <span><i>REV</i>{{ blackboard.snapshot.revision }}</span>
      <span><i>UPDATED</i>{{ formatTime(blackboard.snapshot.updated_at) }}</span>
      <span><i>HUB LEASE</i>{{ formatTime(blackboard.snapshot.hub_lease_expires_at) }}</span>
      <span><i>OWNER</i><code>{{ blackboard.snapshot.owner_id }}</code></span>
    </div>

    <div v-if="!blackboard?.available" class="bb-degraded">
      当前 snapshot 不可用于在线能力判定；已按空 capability 集合 fail closed，未回退持久化 capability。
    </div>

    <div v-if="groups.length" class="bb-groups">
      <details v-for="group in groups" :key="group.id" class="companion-group">
        <summary>
          <span class="group-name">COMPANION · {{ group.name }}</span>
          <span>{{ group.devices.length }} device · {{ group.devices.reduce((n, d) => n + d.capabilities.length, 0) }} contract</span>
        </summary>
        <div class="device-list">
          <details v-for="device in group.devices" :key="device.device_id" class="device-row">
            <summary>
              <span class="device-state" :class="{ online: device.status === 'online' && !isExpired(device.lease_expires_at) }" />
              <b>{{ device.name || device.device_id }}</b>
              <code>{{ device.device_id }}</code>
              <em>{{ device.status }} · {{ device.visibility }} · {{ device.capabilities.length }} caps</em>
            </summary>
            <div class="device-detail">
              <dl class="identity-grid">
                <div><dt>registration</dt><dd><code>{{ device.registration_id }}</code></dd></div>
                <div><dt>manifest rev</dt><dd><code :title="device.manifest_revision">{{ short(device.manifest_revision, 22) }}</code></dd></div>
                <div><dt>registered</dt><dd>{{ formatTime(device.registered_at) }}</dd></div>
                <div><dt>device lease</dt><dd :class="{ expired: isExpired(device.lease_expires_at) }">{{ formatTime(device.lease_expires_at) }}</dd></div>
                <div><dt>last seen</dt><dd>{{ formatTime(device.last_seen_at) }}</dd></div>
                <div><dt>presence rev</dt><dd><code>{{ device.presence_revision || '—' }}</code></dd></div>
                <div><dt>room</dt><dd><code>{{ device.room_name || '—' }}</code></dd></div>
                <div><dt>participant</dt><dd><code>{{ device.participant_sid || '—' }}</code></dd></div>
                <div><dt>aliases</dt><dd>{{ device.aliases.join(', ') || '—' }}</dd></div>
              </dl>

              <div v-if="device.capabilities.length" class="contracts">
                <details v-for="contract in device.capabilities" :key="`${contract.name}:${contract.version}`" class="contract">
                  <summary><b>{{ contract.name }}</b><span>v{{ contract.version }}</span><em>{{ schemaSummary(contract) }}</em></summary>
                  <p>{{ contract.description }}</p>
                  <div class="schema-pair">
                    <div><b>INPUT SCHEMA</b><pre>{{ raw(contract.input_schema) }}</pre></div>
                    <div><b>RESULT SCHEMA</b><pre>{{ raw(contract.result_schema) }}</pre></div>
                  </div>
                </details>
              </div>
              <p v-else class="no-contract">NO CAPABILITY CONTRACT DECLARED</p>

              <details class="raw-fields"><summary>RAW DEVICE FIELDS</summary><pre>{{ raw(device) }}</pre></details>
            </div>
          </details>
        </div>
      </details>
    </div>
    <div v-else class="bb-empty">NO CURRENT DEVICE ENTRIES</div>

    <details v-if="blackboard" class="raw-snapshot">
      <summary>RAW SNAPSHOT / KEY</summary>
      <code>{{ blackboard.bucket }} · {{ blackboard.key }}</code>
      <pre v-if="blackboard.snapshot">{{ raw(blackboard.snapshot) }}</pre>
    </details>
  </section>
</template>

<style scoped>
.blackboard { position: relative; z-index: 1; flex: 0 0 auto; border: 1px solid rgba(0, 234, 255, .28); border-left: 3px solid var(--cy-cyan); background: rgba(4, 12, 24, .88); font-family: var(--cy-mono); }
.blackboard.health-degraded { border-left-color: var(--cy-mag); } .blackboard.health-empty { border-left-color: var(--cy-yellow); }
.bb-head { display: grid; grid-template-columns: minmax(250px, 1fr) minmax(260px, 1.4fr) auto; align-items: center; gap: 16px; padding: 9px 12px; }
.bb-title { display: flex; align-items: center; gap: 9px; } .bb-title b { display: block; color: #fff; font-size: 11px; letter-spacing: .08em; }
.bb-title small { display: block; margin-top: 3px; color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .06em; }
.bb-led { width: 8px; height: 8px; border-radius: 50%; background: var(--cy-cyan); box-shadow: 0 0 10px currentColor; }
.health-degraded .bb-led { background: var(--cy-mag); } .health-empty .bb-led { background: var(--cy-yellow); }
.bb-health { min-width: 0; display: flex; align-items: center; gap: 10px; }
.bb-health strong { padding: 3px 6px; color: var(--cy-cyan); border: 1px solid currentColor; font-size: 9px; }
.health-degraded .bb-health strong { color: var(--cy-mag); } .health-empty .bb-health strong { color: var(--cy-yellow); }
.bb-health span { overflow: hidden; color: var(--cy-txt-dim); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.bb-counts { display: flex; gap: 12px; color: var(--cy-txt-dim); font-size: 9px; white-space: nowrap; } .bb-counts b { color: #fff; font-size: 13px; }
.bb-meta { display: flex; flex-wrap: wrap; gap: 5px 14px; padding: 6px 12px; border-top: 1px solid rgba(0, 234, 255, .12); background: rgba(0, 234, 255, .025); color: var(--cy-txt); font-size: 9px; }
.bb-meta i { margin-right: 5px; color: var(--cy-txt-dim); font-style: normal; } .bb-meta code, .device-row code, .raw-snapshot code { color: var(--cy-cyan); font-size: 9px; }
.bb-degraded { padding: 7px 12px; border-top: 1px solid rgba(255, 46, 136, .18); color: var(--cy-mag); font-size: 9px; }
.bb-groups { max-height: 220px; overflow: auto; border-top: 1px solid rgba(0, 234, 255, .12); }
details > summary { cursor: pointer; list-style: none; } details > summary::-webkit-details-marker { display: none; }
.companion-group > summary { display: flex; justify-content: space-between; padding: 7px 12px; color: var(--cy-txt-dim); font-size: 9px; background: rgba(255, 255, 255, .018); }
.companion-group > summary::before, .device-row > summary::before, .contract > summary::before { content: '＋'; margin-right: 7px; color: var(--cy-cyan); }
.companion-group[open] > summary::before, .device-row[open] > summary::before, .contract[open] > summary::before { content: '−'; }
.group-name { flex: 1; color: #fff; font-weight: 700; letter-spacing: .05em; } .device-list { padding: 0 8px 7px 24px; }
.device-row { border-top: 1px solid rgba(255, 255, 255, .06); }
.device-row > summary { display: grid; grid-template-columns: auto auto auto minmax(120px, 1fr) minmax(150px, 1fr) auto; align-items: center; gap: 8px; padding: 7px 4px; font-size: 9px; }
.device-state { width: 6px; height: 6px; border-radius: 50%; background: var(--cy-txt-dim); } .device-state.online { background: var(--cy-green); box-shadow: 0 0 7px var(--cy-green); }
.device-row summary b { color: #fff; } .device-row summary em { color: var(--cy-txt-dim); font-style: normal; text-align: right; }
.device-detail { padding: 3px 8px 10px 28px; } .identity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px 12px; margin: 0 0 8px; }
.identity-grid div { min-width: 0; } .identity-grid dt { color: var(--cy-txt-dim); font-size: 8px; text-transform: uppercase; }
.identity-grid dd { overflow-wrap: anywhere; margin: 2px 0 0; color: var(--cy-txt); font-size: 9px; } .identity-grid dd.expired { color: var(--cy-mag); }
.contracts { display: grid; gap: 4px; } .contract { border: 1px solid rgba(0, 234, 255, .12); background: rgba(0, 234, 255, .025); }
.contract > summary { display: flex; align-items: center; gap: 8px; padding: 6px 8px; } .contract > summary b { color: var(--cy-cyan); font-size: 10px; }
.contract > summary span { color: var(--cy-yellow); font-size: 8px; } .contract > summary em { margin-left: auto; color: var(--cy-txt-dim); font-size: 8px; font-style: normal; }
.contract p { margin: 0; padding: 0 9px 7px; color: var(--cy-txt); font: 10px/1.45 var(--cy-sans); }
.schema-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding: 0 8px 8px; }
.schema-pair b, .raw-fields summary, .raw-snapshot summary { color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .05em; }
pre { overflow: auto; max-height: 180px; margin: 4px 0 0; padding: 7px; color: #b9d8e2; background: rgba(0, 0, 0, .35); font: 8px/1.45 var(--cy-mono); white-space: pre-wrap; overflow-wrap: anywhere; }
.no-contract, .bb-empty { margin: 0; padding: 8px 12px; color: var(--cy-txt-dim); font-size: 9px; } .raw-fields { margin-top: 7px; }
.raw-snapshot { padding: 6px 12px; border-top: 1px solid rgba(0, 234, 255, .1); } .raw-snapshot > code { display: block; margin-top: 5px; overflow-wrap: anywhere; }
@media (max-width: 980px) { .bb-head { grid-template-columns: 1fr auto; } .bb-health { grid-column: 1 / -1; grid-row: 2; } .identity-grid, .schema-pair { grid-template-columns: 1fr; } }
</style>
