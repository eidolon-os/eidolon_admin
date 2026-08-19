<script setup lang="ts">
// Tweened numeric display (A3.3): animates to new values with tabular figures so
// HUD counts glide instead of snapping. Respects reduced-motion (jumps) via tween().
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { tween } from '../motion'

const props = withDefaults(
  defineProps<{ value: number; duration?: number; decimals?: number }>(),
  { duration: 520, decimals: 0 },
)

const shown = ref(props.value)
let cancel = () => {}

watch(
  () => props.value,
  (to, from) => {
    cancel()
    cancel = tween(Number(from) || 0, Number(to) || 0, props.duration, (v) => (shown.value = v))
  },
)
onBeforeUnmount(() => cancel())

const text = computed(() => shown.value.toFixed(props.decimals))
</script>

<template>
  <span class="dn">{{ text }}</span>
</template>

<style scoped>
.dn { font-variant-numeric: tabular-nums; }
</style>
