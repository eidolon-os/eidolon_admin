<script setup lang="ts">
// Recent runtime events as a readable, scrollable list — replaces the marquee,
// since trace-like content shouldn't scroll past faster than you can read it.
// Scoped to the focused companion when `scope` is set (the composable already
// filters the events by companion).
import type { RuntimeEvent } from '@/api/missionControl'
import { SVC_GLYPH } from '../constants'
import { compactEventSummary, fmtTime } from '../format'
import OriginBadge from '../primitives/OriginBadge.vue'

defineProps<{ events: RuntimeEvent[]; scope?: string; selectedEventId?: string }>()
defineEmits<{
  (e: 'select', event: RuntimeEvent): void
  (e: 'hover', event: RuntimeEvent | null): void
}>()

// Tie each event row to its source's colour (same legend as the bus / crawl), so a
// new row sliding in reads as "this came from there" — the events list becomes a
// causal ledger, not an anonymous log.
const SRC_COLOR: Record<string, string> = {
  hub: 'var(--src-hub)', channel: 'var(--src-channel)', agent: 'var(--src-agent)',
  memory: 'var(--src-memory)', data: 'var(--src-data)', admin: 'var(--src-admin)',
  permission: 'var(--src-permission)', nats: 'var(--cy-green)', livekit: 'var(--cy-cyan)',
  mementos: 'var(--cy-mag)',
}
function srcColor(s: string): string {
  return SRC_COLOR[s] || 'var(--cy-hair-strong)'
}
</script>

<template>
  <section class="events">
    <div class="ev-head">
      <span class="ev-cap"><i class="led idle" />最近事件 · EVENTS<em v-if="events.length" class="ev-n">{{ events.length }}</em></span>
      <span v-if="scope" class="ev-scope">聚焦：{{ scope }}</span>
    </div>
    <transition-group v-if="events.length" tag="ul" name="ev" class="ev-list">
      <li
        v-for="e in events.slice(0, 14)"
        :key="e.event_id"
        class="ev-row"
        :class="['sev-' + e.severity, { selected: selectedEventId === e.event_id, linked: !!(e.turn_id || e.trace_id) }]"
        :style="{ '--row-src': srcColor(e.source) }"
        tabindex="0"
        @click="$emit('select', e)"
        @keydown.enter="$emit('select', e)"
        @mouseenter="$emit('hover', e)"
        @mouseleave="$emit('hover', null)"
      >
        <em class="ev-ts num">{{ fmtTime(e.ts) }}</em>
        <OriginBadge :origin="e.event_origin" />
        <b class="ev-src">{{ SVC_GLYPH[e.source] || '·' }} {{ e.source.toUpperCase() }}</b>
        <span class="ev-sum" :title="e.summary || e.type">{{ compactEventSummary(e) }}</span>
      </li>
    </transition-group>
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
.ev-row { display: flex; align-items: center; gap: 8px; padding: 3px 4px 3px 8px; font-size: 11.5px; color: var(--cy-txt); border-bottom: 1px solid rgba(255, 255, 255, 0.03); border-left: 2px solid var(--row-src, var(--cy-hair)); }
.ev-row.linked { cursor: pointer; }
.ev-row.linked:hover, .ev-row.selected { background: rgba(0, 234, 255, .08); outline: 1px solid rgba(0, 234, 255, .18); }
/* New events slide in from the top and push older rows down — a felt "arrival". */
.ev-enter-from { opacity: 0; transform: translateX(-10px); }
.ev-enter-active { transition: opacity var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out); }
.ev-move { transition: transform var(--dur-base) var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .ev-enter-from { opacity: 1; transform: none; }
  .ev-enter-active, .ev-move { transition: none; }
}
.ev-ts { flex: 0 0 auto; font: 700 10px/1 var(--cy-mono); font-style: normal; color: var(--cy-txt-dim); }
.ev-src { flex: 0 0 auto; font: 700 10px/1 var(--cy-mono); color: var(--cy-cyan); letter-spacing: 0.04em; }
.ev-sum { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ev-row.sev-warn .ev-sum { color: var(--cy-yellow); }
.ev-row.sev-error .ev-sum { color: var(--cy-mag); }
.ev-idle { margin: 4px 0; font: 400 11px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
