<script setup lang="ts">
// Provenance pill: LIVE / POLL / REPLAY / MOCK (A3 §a). Wired per-event once the
// backend stamps `event_origin` (M2); kept here as the shared primitive.
import type { EventOrigin } from '../types'

defineProps<{ origin: EventOrigin }>()

const LABEL: Record<EventOrigin, string> = { live: 'LIVE', polling: 'POLL', replay: 'REPLAY', mock: 'MOCK' }
</script>

<template>
  <span class="origin" :class="'o-' + origin">{{ LABEL[origin] }}</span>
</template>

<style scoped>
.origin { font: 700 8px/1 var(--cy-mono); letter-spacing: 0.08em; padding: 2px 4px; border: 1px solid currentColor; border-radius: 2px; }
.o-live { color: var(--cy-green); }
.o-polling { color: var(--cy-txt-dim); }
.o-replay { color: var(--cy-yellow); }
.o-mock { color: var(--cy-mag); }
</style>
