<script setup lang="ts">
import { computed } from 'vue'

// Consistent dark-themed `<pre>` for showing JSON / YAML / arbitrary data.
// Keeps formatting + scrolling decisions in one place.

interface Props {
  data: unknown
  /** Max height in viewport units (e.g. "60vh") or px. Defaults to 60vh. */
  maxHeight?: string
  /** If true, parse strings that look like JSON before formatting. */
  parseStrings?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  maxHeight: '60vh',
  parseStrings: false,
})

const text = computed(() => {
  let value: unknown = props.data
  if (props.parseStrings && typeof value === 'string') {
    try { value = JSON.parse(value) } catch { /* leave as-is */ }
  }
  if (value === undefined || value === null) return '(empty)'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
})
</script>

<template>
  <pre class="json" :style="{ maxHeight: maxHeight }">{{ text }}</pre>
</template>

<style scoped>
.json {
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  padding: 14px 16px;
  border-radius: 6px;
  margin: 0;
  overflow: auto;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
