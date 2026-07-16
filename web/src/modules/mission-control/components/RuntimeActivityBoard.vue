<script setup lang="ts">
import { computed } from 'vue'
import type { RuntimeActivity } from '@/api/missionControl'
import { activityKindLabel, currentActivityHop, isActiveActivity } from '../activity'
import { fmtLatency, statusClass } from '../format'

const props = defineProps<{
  activities: RuntimeActivity[]
  companionNames: Record<string, string>
  deviceNames: Record<string, string>
  scope?: string
}>()
defineEmits<{ (e: 'open', activity: RuntimeActivity): void }>()

const visible = computed(() => props.activities.slice(0, 4))
function hopLabel(hop: RuntimeActivity['route'][number]): string {
  if (hop.node_type === 'device') return props.deviceNames[hop.node_id] || '身体'
  if (hop.node_type === 'companion') return props.companionNames[hop.node_id] || 'Companion'
  return hop.label
}
</script>

<template>
  <section class="activity-board" :class="{ standby: !activities.length }">
    <header class="ab-head">
      <span><i class="led" :class="activities.some(isActiveActivity) ? 'ok' : 'idle'" />运行活动</span>
      <em v-if="scope">{{ scope }}</em>
      <b v-if="activities.length > visible.length">+{{ activities.length - visible.length }}</b>
    </header>
    <div v-if="visible.length" class="ab-lanes">
      <button v-for="activity in visible" :key="activity.activity_id" class="ab-lane" @click="$emit('open', activity)">
        <span class="ab-kind" :class="statusClass(activity.status)">{{ activityKindLabel(activity.kind) }}</span>
        <span class="ab-owner">{{ activity.companion_id ? companionNames[activity.companion_id] || activity.companion_id : 'OWNER' }}</span>
        <ol v-if="activity.route.length" class="ab-route">
          <li
            v-for="(hop, index) in activity.route"
            :key="hop.hop_id"
            :class="[statusClass(hop.status), { current: currentActivityHop(activity)?.hop_id === hop.hop_id }]"
          >
            <b>{{ hopLabel(hop) }}</b>
            <em v-if="hop.latency_ms != null" class="num">{{ fmtLatency(hop.latency_ms) }}</em>
            <i v-if="index < activity.route.length - 1">›</i>
          </li>
        </ol>
        <span v-else class="ab-summary">{{ activity.summary }}</span>
        <span class="ab-status" :class="statusClass(activity.status)">{{ activity.status }}</span>
      </button>
    </div>
    <p v-else class="ab-idle">待命中 · 对话、Guard、设备命令和后台任务都会在这里形成独立路径</p>
  </section>
</template>

<style scoped>
.activity-board { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 10px; width: 100%; padding: 8px 12px; border: 1px solid var(--cy-hair); background: var(--cy-panel); }
.ab-head { display: flex; flex-direction: column; justify-content: center; gap: 5px; font: 700 9px/1 var(--cy-mono); letter-spacing: .08em; color: var(--cy-txt-dim); }
.ab-head span { display: inline-flex; align-items: center; gap: 6px; color: var(--cy-txt); }
.ab-head .led { width: 6px; height: 6px; }
.ab-head em { color: var(--cy-cyan); font-style: normal; letter-spacing: .03em; }
.ab-head b { color: var(--cy-yellow); }
.ab-lanes { display: grid; gap: 4px; min-width: 0; }
.ab-lane { display: grid; grid-template-columns: 48px minmax(72px, 120px) minmax(0, 1fr) 70px; align-items: center; gap: 8px; min-width: 0; padding: 5px 7px; border: 1px solid rgba(0, 234, 255, .12); background: rgba(5, 4, 18, .52); color: inherit; text-align: left; cursor: pointer; }
.ab-lane:hover { border-color: rgba(0, 234, 255, .45); }
.ab-kind, .ab-status { font: 700 8px/1 var(--cy-mono); letter-spacing: .05em; }
.ab-owner { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 700 9px/1 var(--cy-mono); color: var(--cy-txt-dim); }
.ab-route { display: flex; align-items: center; min-width: 0; margin: 0; padding: 0; list-style: none; overflow-x: auto; }
.ab-route li { display: inline-flex; align-items: center; flex: 0 0 auto; gap: 5px; white-space: nowrap; }
.ab-route b { font: 700 10px/1 var(--cy-sans); color: var(--cy-txt-dim); }
.ab-route em { font: 700 8px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }
.ab-route i { margin: 0 6px; color: rgba(0, 234, 255, .35); font-style: normal; }
.ab-route li.ok b { color: var(--cy-green); }
.ab-route li.warn b { color: var(--cy-yellow); }
.ab-route li.bad b { color: var(--cy-mag); }
.ab-route li.current b { color: var(--cy-cyan); text-shadow: 0 0 9px currentColor; }
.ab-route li.current::before { content: "▸"; color: var(--cy-cyan); animation: playhead var(--dur-breath) ease-in-out infinite; }
.ab-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 10px/1 var(--cy-sans); color: var(--cy-txt-dim); }
.ab-status { justify-self: end; overflow: hidden; text-overflow: ellipsis; text-transform: uppercase; }
.ab-idle { align-self: center; margin: 0; font: 400 11px/1.3 var(--cy-sans); color: var(--cy-txt-dim); }
@keyframes playhead { 0%, 100% { opacity: .4; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .ab-route li.current::before { animation: none; } }
@media (max-width: 1080px) { .activity-board { grid-template-columns: 1fr; } .ab-head { flex-direction: row; justify-content: flex-start; } }
</style>
