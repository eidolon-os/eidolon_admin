<script setup lang="ts">
// Agent Span Inspector: one selected/focused voice turn's internal structure —
// input / memory / model / tool / routing spans. Redacted (counts + latency
// only), proving it's a debuggable runtime, not a chat wrapper.
import type { RuntimeTraceSpan } from '@/api/missionControl'
import { fmtLatency, statusClass } from '../format'

defineProps<{ spans: RuntimeTraceSpan[] }>()

const KIND_GLYPH: Record<string, string> = {
  input: '▸', memory_recall: '◈', agent_turn: '◊', model: '◊', tools: '⚙', tool: '⚙', memory_write: '◈', routing: '⇄',
}
</script>

<template>
  <div class="span-inspector">
    <div v-for="s in spans" :key="s.span_id" class="span-row" :class="statusClass(s.status)">
      <i class="span-glyph">{{ KIND_GLYPH[s.kind] || '·' }}</i>
      <b class="span-name">{{ s.name }}</b>
      <em v-if="s.latency_ms != null" class="num">{{ fmtLatency(s.latency_ms) }}</em>
      <span v-if="s.detail" class="span-detail">{{ s.detail }}</span>
    </div>
    <p v-if="!spans.length" class="span-empty">所选对话没有可展示的 Agent spans</p>
  </div>
</template>

<style scoped>
.span-inspector { display: grid; gap: 4px; }
.span-row { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border: 1px solid rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); font: 600 10.5px/1.3 var(--cy-mono); }
.span-glyph { font-style: normal; color: var(--cy-cyan); }
.span-row.ok .span-glyph { color: var(--cy-green); }
.span-row.warn .span-glyph { color: var(--cy-yellow); }
.span-row.bad .span-glyph { color: var(--cy-mag); }
.span-name { font-family: var(--cy-sans); font-weight: 700; color: var(--cy-txt); }
.span-row em { margin-left: auto; color: var(--cy-txt-dim); font-style: normal; }
.span-detail { color: var(--cy-txt-dim); }
.span-empty { margin: 6px 0; font: 400 11px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
