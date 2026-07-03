<script setup lang="ts">
import type { RuntimeEvent } from '@/api/missionControl'
import { SVC_GLYPH } from '../constants'
import { fmtTime } from '../format'

defineProps<{ events: RuntimeEvent[]; pipelineActive: boolean }>()
</script>

<template>
  <div class="bus-crawl">
    <span class="crawl-tag" :class="pipelineActive ? 'ok' : 'idle'"><i class="led" />TELEMETRY</span>
    <div class="crawl-track">
      <div v-if="events.length" class="crawl-run">
        <template v-for="pass in 2" :key="pass">
          <span v-for="e in events" :key="pass + e.event_id" class="cev" :class="'sev-' + e.severity">
            <em>{{ fmtTime(e.ts) }}</em><b>{{ SVC_GLYPH[e.source] || '·' }} {{ e.source.toUpperCase() }}</b>{{ e.summary || e.type }}<u>◇</u>
          </span>
        </template>
      </div>
      <div v-else class="crawl-run"><span class="cev">等待实时信号进入视野…</span></div>
    </div>
  </div>
</template>

<style scoped>
.bus-crawl { display: flex; align-items: center; gap: 12px; padding-top: 8px; border-top: 1px solid rgba(0, 234, 255, 0.1); }
.crawl-tag { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-txt-dim); }
.crawl-tag .led { width: 6px; height: 6px; }
.crawl-track { flex: 1 1 auto; overflow: hidden; mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent); }
.crawl-run { display: inline-flex; white-space: nowrap; animation: crawl 48s linear infinite; }
.crawl-run:hover { animation-play-state: paused; }
.cev { display: inline-flex; align-items: center; gap: 7px; margin-right: 6px; font-size: 11.5px; color: var(--cy-txt); }
.cev em { font: 700 10px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }
.cev b { font: 700 10px/1 var(--cy-mono); color: var(--cy-cyan); letter-spacing: 0.04em; }
.cev u { text-decoration: none; color: rgba(0, 234, 255, 0.3); margin-left: 4px; }
.cev.sev-warn { color: var(--cy-yellow); }
.cev.sev-error { color: var(--cy-mag); }
@keyframes crawl { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@media (prefers-reduced-motion: reduce) { .crawl-run { animation: none !important; } }
</style>
