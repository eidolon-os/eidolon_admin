<script setup lang="ts">
// Region 7 · Capability Registry: the system's capability surface (what each
// body/service exposes), not just online state. Shown in the owner drawer.
import type { RuntimeCapabilityCard } from '@/api/missionControl'
import { statusClass } from '../format'

defineProps<{ cards: RuntimeCapabilityCard[] }>()
</script>

<template>
  <div class="cap-registry">
    <div v-for="c in cards" :key="c.key" class="cap-card" :class="statusClass(c.status)">
      <div class="cap-h"><b>{{ c.title }}</b><em>{{ c.metric }}</em></div>
      <p v-if="c.detail" class="cap-detail">{{ c.detail }}</p>
    </div>
  </div>
</template>

<style scoped>
.cap-registry { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.cap-card { padding: 8px 10px; border: 1px solid rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); border-left: 2px solid var(--cy-txt-dim); }
.cap-card.ok { border-left-color: var(--cy-green); }
.cap-card.warn { border-left-color: var(--cy-yellow); }
.cap-card.bad { border-left-color: var(--cy-mag); }
.cap-h { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.cap-h b { font: 700 11px/1.2 var(--cy-sans); color: #fff; }
.cap-h em { font: 700 9px/1 var(--cy-mono); font-style: normal; color: var(--cy-cyan); }
.cap-detail { margin: 4px 0 0; font: 400 10px/1.4 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
