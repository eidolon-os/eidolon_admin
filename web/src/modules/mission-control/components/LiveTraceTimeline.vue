<script setup lang="ts">
// Region 2 · Live Turn Trace: the active turn's stages as a horizontal timeline.
import type { RuntimeTurn } from '@/api/missionControl'
import { fmtLatency, statusClass } from '../format'

defineProps<{ turn: RuntimeTurn | null }>()
</script>

<template>
  <div class="live-trace" :class="{ standby: !turn }">
    <span class="lt-cap"><i class="led" :class="turn ? 'ok' : 'idle'" />LIVE TRACE</span>
    <ol v-if="turn && turn.stages.length" class="lt-steps">
      <li v-for="(s, i) in turn.stages" :key="s.key" class="lt-step" :class="statusClass(s.status)">
        <b>{{ s.label }}</b>
        <em v-if="s.latency_ms != null" class="num">{{ fmtLatency(s.latency_ms) }}</em>
        <i v-if="i < turn.stages.length - 1" class="lt-arrow">›</i>
      </li>
    </ol>
    <span v-else class="lt-idle">待命中 · 对任意身体说一句话点亮这条链路</span>
  </div>
</template>

<style scoped>
.live-trace { display: flex; align-items: center; gap: 12px; padding: 8px 14px; border: 1px solid var(--cy-hair); background: var(--cy-panel); overflow-x: auto; }
.lt-cap { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-txt-dim); }
.lt-cap .led { width: 6px; height: 6px; }
.lt-steps { display: flex; align-items: center; gap: 0; margin: 0; padding: 0; list-style: none; }
.lt-step { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.lt-step b { font: 700 11px/1 var(--cy-sans); color: var(--cy-txt); }
.lt-step em { font: 700 9px/1 var(--cy-mono); font-style: normal; color: var(--cy-txt-dim); }
.lt-step.ok b { color: var(--cy-green); }
.lt-step.warn b { color: var(--cy-yellow); }
.lt-step.bad b { color: var(--cy-mag); }
.lt-step.idle b { color: var(--cy-txt-dim); }
.lt-arrow { margin: 0 8px; color: rgba(0, 234, 255, 0.4); font-style: normal; }
.lt-idle { font: 400 11.5px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
