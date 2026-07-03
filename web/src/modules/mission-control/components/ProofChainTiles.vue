<script setup lang="ts">
// The three demo proof-chains as evidence tiles (region 8 · one-claim-one-proof).
import type { EvidenceChain } from '@/api/missionControl'

defineProps<{ chains: EvidenceChain[] }>()

function tone(status: string) {
  return status === 'proven' ? 'ok' : status === 'partial' ? 'warn' : 'idle'
}
</script>

<template>
  <div class="proof-tiles">
    <article v-for="c in chains" :key="c.key" class="tile" :class="tone(c.status)">
      <header class="tile-h">
        <span class="tile-title">{{ c.title }}</span>
        <span class="tile-conf num">{{ c.confidence }}<i>%</i></span>
      </header>
      <p class="tile-claim">{{ c.claim }}</p>
      <div class="tile-bar"><i :style="{ width: c.confidence + '%' }" /></div>
      <ul class="tile-steps">
        <li v-for="s in c.steps" :key="s.key" :class="{ done: s.done }" :title="s.detail || s.label">
          <b>{{ s.done ? '✓' : '○' }}</b><span>{{ s.label }}</span>
        </li>
      </ul>
    </article>
  </div>
</template>

<style scoped>
.proof-tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; position: relative; z-index: 1; }
.tile { padding: 10px 12px; border: 1px solid var(--cy-hair); background: var(--cy-panel); backdrop-filter: blur(3px); clip-path: polygon(0 0, 100% 0, 100% 100%, 8px 100%, 0 calc(100% - 8px)); }
.tile.ok { border-color: rgba(55, 245, 179, 0.4); }
.tile.warn { border-color: rgba(247, 255, 74, 0.32); }
.tile-h { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.tile-title { font: 700 12.5px/1 var(--cy-sans); color: #fff; }
.tile-conf { font: 900 16px/1 var(--cy-mono); color: var(--cy-cyan); }
.tile.ok .tile-conf { color: var(--cy-green); }
.tile.warn .tile-conf { color: var(--cy-yellow); }
.tile.idle .tile-conf { color: var(--cy-txt-dim); }
.tile-conf i { font-size: 9px; font-style: normal; color: var(--cy-txt-dim); }
.tile-claim { margin: 6px 0 8px; font: 400 11px/1.5 var(--cy-sans); color: #aab6d8; min-height: 2.6em; }
.tile-bar { height: 3px; background: rgba(0, 234, 255, 0.12); overflow: hidden; }
.tile-bar i { display: block; height: 100%; background: var(--cy-cyan); box-shadow: 0 0 8px var(--cy-cyan); transition: width var(--dur-slow) var(--ease-out); }
.tile.ok .tile-bar i { background: var(--cy-green); box-shadow: 0 0 8px var(--cy-green); }
.tile.warn .tile-bar i { background: var(--cy-yellow); box-shadow: 0 0 8px var(--cy-yellow); }
.tile-steps { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 8px 0 0; padding: 0; list-style: none; }
.tile-steps li { display: inline-flex; align-items: center; gap: 4px; font: 600 9.5px/1.3 var(--cy-mono); color: var(--cy-txt-dim); }
.tile-steps li.done { color: var(--cy-txt); }
.tile-steps li b { color: var(--cy-txt-dim); }
.tile-steps li.done b { color: var(--cy-green); }
@media (max-width: 1080px) { .proof-tiles { grid-template-columns: 1fr; } }
</style>
