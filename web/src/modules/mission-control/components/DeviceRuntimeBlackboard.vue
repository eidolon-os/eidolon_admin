<script setup lang="ts">
import { computed } from 'vue'
import type { RuntimeBlackboardDevice, RuntimeCapabilityContract } from '@/api/missionControl'
import type { MissionControlStream } from '../useMissionControlStream'

const props = defineProps<{ mc: MissionControlStream }>()
const blackboard = props.mc.runtimeBlackboard

const devices = computed<RuntimeBlackboardDevice[]>(() =>
  Object.values(blackboard.value?.snapshot?.devices || {})
    .sort((a, b) => a.device_id.localeCompare(b.device_id)),
)
const onlineCount = computed(() => devices.value.filter((device) => isOnline(device)).length)
const capabilityCount = computed(() => devices.value.reduce((sum, device) => sum + device.capabilities.length, 0))

function isOnline(device: RuntimeBlackboardDevice): boolean {
  const lease = Date.parse(device.lease_expires_at)
  return device.status === 'online' && Number.isFinite(lease) && lease > Date.now()
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value
}

function short(value: string, length = 24): string {
  if (!value) return '—'
  return value.length > length ? `${value.slice(0, length)}…` : value
}

function schemaSummary(contract: RuntimeCapabilityContract): string {
  const inputs = Object.keys(contract.input_schema?.properties || {}).length
  const required = Array.isArray(contract.input_schema?.required) ? contract.input_schema.required.length : 0
  return `${inputs} input · ${required} required`
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
        <div>
          <b>DEVICE RUNTIME BLACKBOARD</b>
          <small>OWNER SNAPSHOT · DEVICES · CAPABILITIES</small>
        </div>
      </div>
      <div class="bb-health">
        <strong>{{ (blackboard?.health || 'degraded').toUpperCase() }}</strong>
        <span>{{ blackboard?.detail || 'Runtime blackboard unavailable' }}</span>
      </div>
      <div class="bb-counts">
        <span><b>{{ onlineCount }}</b>/{{ devices.length }} online</span>
        <span><b>{{ capabilityCount }}</b> capabilities</span>
      </div>
    </header>

    <div v-if="blackboard?.snapshot" class="snapshot-meta">
      <span><i>OWNER</i><code>{{ blackboard.snapshot.owner_id }}</code></span>
      <span><i>SCHEMA</i>v{{ blackboard.snapshot.schema_version }}</span>
      <span><i>EPOCH</i><code :title="blackboard.snapshot.epoch">{{ short(blackboard.snapshot.epoch) }}</code></span>
      <span><i>REV</i>{{ blackboard.snapshot.revision }}</span>
      <span><i>READY</i>{{ blackboard.snapshot.ready ? 'YES' : 'NO' }}</span>
      <span><i>UPDATED</i>{{ formatTime(blackboard.snapshot.updated_at) }}</span>
      <span><i>HUB LEASE</i>{{ formatTime(blackboard.snapshot.hub_lease_expires_at) }}</span>
    </div>

    <p v-if="!blackboard?.available" class="bb-degraded">
      当前 snapshot 不可用于在线能力判定；未回退持久化 capability。
    </p>

    <div v-if="devices.length" class="device-tree">
      <div class="tree-heading">
        <span>DEVICES</span>
        <em>{{ devices.length }} current entries</em>
      </div>

      <details v-for="device in devices" :key="device.device_id" class="device-node">
        <summary>
          <span class="chevron" />
          <span class="presence" :class="{ online: isOnline(device) }" />
          <span class="device-identity">
            <b>{{ device.name || device.device_id }}</b>
            <code>{{ device.device_id }}</code>
          </span>
          <span class="provider">
            <i>PROVIDER</i>
            {{ device.provider_companion_name || device.provider_companion_id || '未绑定' }}
          </span>
          <span class="device-state">{{ device.status }} · {{ device.visibility }}</span>
          <span class="cap-count">{{ device.capabilities.length }} CAP</span>
        </summary>

        <div class="device-body">
          <dl class="device-fields">
            <div><dt>registration_id</dt><dd><code>{{ device.registration_id }}</code></dd></div>
            <div><dt>manifest_revision</dt><dd><code :title="device.manifest_revision">{{ short(device.manifest_revision) }}</code></dd></div>
            <div><dt>registered_at</dt><dd>{{ formatTime(device.registered_at) }}</dd></div>
            <div><dt>lease_expires_at</dt><dd :class="{ expired: !isOnline(device) }">{{ formatTime(device.lease_expires_at) }}</dd></div>
            <div><dt>last_seen_at</dt><dd>{{ formatTime(device.last_seen_at) }}</dd></div>
            <div><dt>presence_revision</dt><dd><code>{{ device.presence_revision || '—' }}</code></dd></div>
            <div><dt>room_name</dt><dd><code>{{ device.room_name || '—' }}</code></dd></div>
            <div><dt>participant_sid</dt><dd><code>{{ device.participant_sid || '—' }}</code></dd></div>
            <div><dt>aliases</dt><dd>{{ device.aliases.join(', ') || '—' }}</dd></div>
          </dl>

          <section class="capability-branch">
            <header>
              <span>CAPABILITIES</span>
              <em>{{ device.capabilities.length }}</em>
            </header>
            <details
              v-for="contract in device.capabilities"
              :key="`${contract.name}:${contract.version}`"
              class="capability-node"
            >
              <summary>
                <span class="chevron" />
                <b>{{ contract.name }}</b>
                <span class="version">v{{ contract.version }}</span>
                <p>{{ contract.description }}</p>
                <em>{{ schemaSummary(contract) }}</em>
              </summary>
              <div class="schema-grid">
                <section><b>INPUT SCHEMA</b><pre>{{ raw(contract.input_schema) }}</pre></section>
                <section><b>RESULT SCHEMA</b><pre>{{ raw(contract.result_schema) }}</pre></section>
              </div>
            </details>
            <p v-if="!device.capabilities.length" class="empty-branch">NO CAPABILITIES DECLARED</p>
          </section>
        </div>
      </details>
    </div>
    <div v-else class="bb-empty">NO CURRENT DEVICE ENTRIES</div>

    <details v-if="blackboard" class="raw-snapshot">
      <summary><span class="chevron" />RAW SNAPSHOT</summary>
      <div class="raw-key"><i>KV</i><code>{{ blackboard.bucket }} · {{ blackboard.key || '—' }}</code></div>
      <pre v-if="blackboard.snapshot">{{ raw(blackboard.snapshot) }}</pre>
      <p v-else>NO CURRENT SNAPSHOT</p>
    </details>
  </section>
</template>

<style scoped>
.blackboard { position: relative; z-index: 1; flex: 0 0 auto; border: 1px solid rgba(0, 234, 255, .28); border-left: 3px solid var(--cy-cyan); background: rgba(4, 12, 24, .9); font-family: var(--cy-mono); }
.blackboard.health-degraded { border-left-color: var(--cy-mag); } .blackboard.health-empty { border-left-color: var(--cy-yellow); }
.bb-head { display: grid; grid-template-columns: minmax(250px, 1fr) minmax(260px, 1.3fr) auto; align-items: center; gap: 16px; padding: 9px 12px; }
.bb-title, .bb-health, .bb-counts { display: flex; align-items: center; gap: 9px; }
.bb-title b { display: block; color: #fff; font-size: 11px; letter-spacing: .08em; }
.bb-title small { display: block; margin-top: 3px; color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .06em; }
.bb-led, .presence { flex: 0 0 auto; width: 7px; height: 7px; border-radius: 50%; background: var(--cy-cyan); box-shadow: 0 0 9px currentColor; }
.health-degraded .bb-led { background: var(--cy-mag); } .health-empty .bb-led { background: var(--cy-yellow); }
.bb-health { min-width: 0; } .bb-health strong { padding: 3px 6px; color: var(--cy-cyan); border: 1px solid currentColor; font-size: 9px; }
.health-degraded .bb-health strong { color: var(--cy-mag); } .health-empty .bb-health strong { color: var(--cy-yellow); }
.bb-health span { overflow: hidden; color: var(--cy-txt-dim); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.bb-counts { color: var(--cy-txt-dim); font-size: 9px; white-space: nowrap; } .bb-counts b { color: #fff; font-size: 12px; }
.snapshot-meta { display: flex; flex-wrap: wrap; gap: 5px 14px; padding: 6px 12px; border-top: 1px solid rgba(0, 234, 255, .12); background: rgba(0, 234, 255, .025); color: var(--cy-txt); font-size: 9px; }
.snapshot-meta i, .provider i, .raw-key i { margin-right: 5px; color: var(--cy-txt-dim); font-style: normal; }
code { color: var(--cy-cyan); font: 9px/1.3 var(--cy-mono); overflow-wrap: anywhere; }
.bb-degraded { margin: 0; padding: 6px 12px; border-top: 1px solid rgba(255, 46, 136, .18); color: var(--cy-mag); font-size: 9px; }
.device-tree { max-height: 250px; overflow: auto; border-top: 1px solid rgba(0, 234, 255, .12); }
.tree-heading, .capability-branch > header { display: flex; align-items: center; justify-content: space-between; padding: 6px 12px; color: var(--cy-cyan); font-size: 9px; letter-spacing: .08em; background: rgba(0, 234, 255, .035); }
.tree-heading em, .capability-branch header em { color: var(--cy-txt-dim); font-style: normal; letter-spacing: 0; }
details > summary { cursor: pointer; list-style: none; } details > summary::-webkit-details-marker { display: none; }
.chevron { display: inline-block; flex: 0 0 auto; width: 6px; height: 6px; border-right: 1px solid var(--cy-cyan); border-bottom: 1px solid var(--cy-cyan); transform: rotate(-45deg); transition: transform var(--dur-fast) var(--ease-out); }
details[open] > summary .chevron { transform: rotate(45deg); }
.device-node { border-top: 1px solid rgba(255, 255, 255, .06); }
.device-node > summary { display: grid; grid-template-columns: auto auto minmax(180px, 1fr) minmax(150px, .8fr) auto auto; align-items: center; gap: 9px; padding: 8px 12px; }
.device-node > summary:hover, .capability-node > summary:hover { background: rgba(0, 234, 255, .045); }
.presence { background: var(--cy-txt-dim); box-shadow: none; } .presence.online { background: var(--cy-green); box-shadow: 0 0 7px var(--cy-green); }
.device-identity { min-width: 0; display: flex; align-items: baseline; gap: 8px; } .device-identity b { color: #fff; font-size: 10px; }
.provider, .device-state { color: var(--cy-txt-dim); font-size: 9px; } .cap-count { padding: 2px 5px; border: 1px solid rgba(0, 234, 255, .25); color: var(--cy-cyan); font-size: 8px; }
.device-body { padding: 2px 12px 10px 34px; background: rgba(0, 0, 0, .12); }
.device-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px 14px; margin: 6px 0 10px; }
.device-fields div { min-width: 0; } .device-fields dt { color: var(--cy-txt-dim); font-size: 8px; } .device-fields dd { margin: 2px 0 0; color: var(--cy-txt); font-size: 9px; overflow-wrap: anywhere; }
.device-fields dd.expired { color: var(--cy-mag); }
.capability-branch { border: 1px solid rgba(0, 234, 255, .1); }
.capability-node { border-top: 1px solid rgba(255, 255, 255, .05); }
.capability-node > summary { display: grid; grid-template-columns: auto auto auto minmax(180px, 1fr) auto; align-items: center; gap: 8px; padding: 7px 9px; }
.capability-node summary b { color: var(--cy-cyan); font-size: 10px; } .version { color: var(--cy-yellow); font-size: 8px; }
.capability-node summary p { overflow: hidden; margin: 0; color: var(--cy-txt); font: 10px/1.3 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.capability-node summary em { color: var(--cy-txt-dim); font-size: 8px; font-style: normal; }
.schema-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; padding: 0 9px 9px 29px; }
.schema-grid section > b { color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .05em; }
pre { overflow: auto; max-height: 190px; margin: 4px 0 0; padding: 7px; color: #b9d8e2; background: rgba(0, 0, 0, .35); font: 8px/1.45 var(--cy-mono); white-space: pre; }
.empty-branch, .bb-empty { margin: 0; padding: 8px 12px; color: var(--cy-txt-dim); font-size: 9px; }
.raw-snapshot { border-top: 1px solid rgba(0, 234, 255, .12); }
.raw-snapshot > summary { display: flex; align-items: center; gap: 8px; padding: 7px 12px; color: var(--cy-txt-dim); font-size: 8px; letter-spacing: .06em; }
.raw-snapshot > summary:hover { color: var(--cy-cyan); background: rgba(0, 234, 255, .035); }
.raw-key { padding: 5px 12px 0 26px; font-size: 9px; } .raw-snapshot > pre { max-height: 320px; margin: 6px 12px 10px 26px; }
.raw-snapshot > p { margin: 0; padding: 8px 26px; color: var(--cy-txt-dim); font-size: 9px; }
@media (max-width: 980px) {
  .bb-head { grid-template-columns: 1fr; gap: 7px; } .bb-counts { flex-wrap: wrap; }
  .device-node > summary { grid-template-columns: auto auto 1fr auto; } .provider { grid-column: 3; }
  .device-state { grid-column: 3; } .device-fields, .schema-grid { grid-template-columns: 1fr; }
}
</style>
