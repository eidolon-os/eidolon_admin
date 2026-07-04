<script setup lang="ts">
// Region 2 · Live Turn Trace: the scoped turn's stages as a compact, clickable
// strip. Clicking opens the trace drawer (full stages + agent spans + the
// companion's event flow). `scope` names the focused companion when set.
import type { RuntimeTurn } from '@/api/missionControl'
import { fmtLatency, statusClass } from '../format'

defineProps<{ turn: RuntimeTurn | null; scope?: string }>()
defineEmits<{ (e: 'open'): void }>()
</script>

<template>
  <button class="live-trace" :class="{ standby: !turn }" @click="$emit('open')">
    <span class="lt-cap">
      <i class="led" :class="turn ? 'ok' : 'idle'" />LIVE TRACE<em v-if="scope" class="lt-scope">· {{ scope }}</em>
    </span>
    <ol v-if="turn && turn.stages.length" class="lt-steps">
      <li v-for="(s, i) in turn.stages" :key="s.key" class="lt-step" :class="statusClass(s.status)">
        <b>{{ s.label }}</b>
        <em v-if="s.latency_ms != null" class="num">{{ fmtLatency(s.latency_ms) }}</em>
        <i v-if="i < turn.stages.length - 1" class="lt-arrow">›</i>
      </li>
    </ol>
    <span v-else class="lt-idle">待命中 · 对任意身体说一句话点亮这条链路</span>
    <span class="lt-more">详情 →</span>
  </button>
</template>

<style scoped>
.live-trace { display: flex; align-items: center; gap: 12px; width: 100%; padding: 8px 14px; border: 1px solid var(--cy-hair); background: var(--cy-panel); text-align: left; cursor: pointer; transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out); overflow: hidden; }
.live-trace:hover { border-color: rgba(0, 234, 255, 0.5); box-shadow: 0 0 16px rgba(0, 234, 255, 0.18); }
.lt-cap { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-txt-dim); }
.lt-cap .led { width: 6px; height: 6px; }
.lt-scope { font-style: normal; color: var(--cy-cyan); }
.lt-steps { display: flex; align-items: center; gap: 0; margin: 0; padding: 0; list-style: none; overflow-x: auto; }
.lt-step { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.lt-step b { font: 700 11px/1 var(--cy-sans); color: var(--cy-txt); }
.lt-step em { font: 700 9px/1 var(--cy-mono); font-style: normal; color: var(--cy-txt-dim); }
.lt-step.ok b { color: var(--cy-green); }
.lt-step.warn b { color: var(--cy-yellow); }
.lt-step.bad b { color: var(--cy-mag); }
.lt-step.idle b { color: var(--cy-txt-dim); }
.lt-arrow { margin: 0 8px; color: rgba(0, 234, 255, 0.4); font-style: normal; }
.lt-idle { font: 400 11.5px/1 var(--cy-sans); color: var(--cy-txt-dim); }
.lt-more { flex: 0 0 auto; margin-left: auto; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.06em; color: var(--cy-cyan); }
</style>
