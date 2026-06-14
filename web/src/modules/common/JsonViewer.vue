<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'

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

async function copyText() {
  try {
    await navigator.clipboard.writeText(text.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<template>
  <div class="json-wrap">
    <el-button
      class="copy-button"
      :icon="CopyDocument"
      circle
      size="small"
      title="复制内容"
      @click="copyText"
    />
    <pre class="json eid-code-surface" :style="{ maxHeight: maxHeight }">{{ text }}</pre>
  </div>
</template>

<style scoped>
.json-wrap {
  position: relative;
  min-width: 0;
}
.copy-button {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 1;
  opacity: 0;
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.json-wrap:hover .copy-button,
.copy-button:focus-visible {
  opacity: 1;
}
.json {
  color: var(--eid-text-primary);
  padding: 14px 16px;
  margin: 0;
  overflow: auto;
  font-family: var(--eid-font-mono);
  font-size: 12.5px;
  line-height: 1.62;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}
</style>
