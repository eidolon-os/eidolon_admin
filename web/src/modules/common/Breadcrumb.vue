<script setup lang="ts">
// Small breadcrumb with clickable ancestors. Used by drill-down pages
// (Owner Workspace, Device Center) so users aren't stranded without a way back.
import { useRouter, type RouteLocationRaw } from 'vue-router'

interface Crumb {
  label: string
  to?: RouteLocationRaw
}
defineProps<{ items: Crumb[] }>()
const router = useRouter()
</script>

<template>
  <nav class="breadcrumb" aria-label="breadcrumb">
    <template v-for="(c, i) in items" :key="i">
      <button v-if="c.to" class="crumb-link" @click="router.push(c.to)">{{ c.label }}</button>
      <span v-else class="crumb-cur">{{ c.label }}</span>
      <i v-if="i < items.length - 1" class="crumb-sep">›</i>
    </template>
  </nav>
</template>

<style scoped>
.breadcrumb { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.crumb-link { border: 0; background: none; padding: 0; color: var(--eid-accent); cursor: pointer; font-size: 12px; }
.crumb-link:hover { color: var(--eid-accent-hover); text-decoration: underline; }
.crumb-cur { color: var(--eid-text-secondary); font-weight: 600; }
.crumb-sep { color: var(--eid-text-muted); font-style: normal; }
</style>
