<script setup lang="ts">
// Recent runtime events as a readable, scrollable list — replaces the marquee,
// since trace-like content shouldn't scroll past faster than you can read it.
// Scoped to the focused companion when `scope` is set (the composable already
// filters the events by companion).
import type { RuntimeEvent } from '@/api/missionControl'
import { SVC_GLYPH } from '../constants'
import { fmtTime } from '../format'
import OriginBadge from '../primitives/OriginBadge.vue'

defineProps<{ events: RuntimeEvent[]; scope?: string }>()
</script>

<template>
  <section class="events">
    <div class="ev-head">
      <span class="ev-cap"><i class="led idle" />最近事件 · EVENTS<em v-if="events.length" class="ev-n">{{ events.length }}</em></span>
      <span v-if="scope" class="ev-scope">聚焦：{{ scope }}</span>
    </div>
    <ul v-if="events.length" class="ev-list">
      <li v-for="e in events.slice(0, 14)" :key="e.event_id" class="ev-row" :class="'sev-' + e.severity">
        <em class="ev-ts num">{{ fmtTime(e.ts) }}</em>
        <OriginBadge :origin="e.event_origin" />
        <b class="ev-src">{{ SVC_GLYPH[e.source] || '·' }} {{ e.source.toUpperCase() }}</b>
        <span class="ev-sum">{{ e.summary || e.type }}</span>
      </li>
    </ul>
    <p v-else class="ev-idle">暂无事件</p>
  </section>
</template>

<style scoped>
.events { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 6px; padding: 10px 14px; border: 1px solid var(--cy-hair); background: var(--cy-panel); }
.ev-head { display: flex; align-items: center; justify-content: space-between; }
.ev-cap { display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-txt-dim); }
.ev-cap .led { width: 6px; height: 6px; }
.ev-n { font-style: normal; color: var(--cy-cyan); }
.ev-scope { font: 700 9px/1 var(--cy-mono); color: var(--cy-cyan); }
.ev-list { display: grid; gap: 3px; margin: 0; padding: 0; list-style: none; max-height: 168px; overflow-y: auto; }
.ev-row { display: flex; align-items: center; gap: 8px; padding: 3px 4px; font-size: 11.5px; color: var(--cy-txt); border-bottom: 1px solid rgba(255, 255, 255, 0.03); }
.ev-ts { flex: 0 0 auto; font: 700 10px/1 var(--cy-mono); font-style: normal; color: var(--cy-txt-dim); }
.ev-src { flex: 0 0 auto; font: 700 10px/1 var(--cy-mono); color: var(--cy-cyan); letter-spacing: 0.04em; }
.ev-sum { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ev-row.sev-warn .ev-sum { color: var(--cy-yellow); }
.ev-row.sev-error .ev-sum { color: var(--cy-mag); }
.ev-idle { margin: 4px 0; font: 400 11px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
