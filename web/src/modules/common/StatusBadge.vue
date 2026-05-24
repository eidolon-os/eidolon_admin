<script setup lang="ts">
// Online/offline indicator dot + tag, used everywhere we need to convey
// "is this thing alive". Kept compact + consistent so the UI feels coherent.

interface Props {
  state: 'online' | 'offline' | 'unknown' | 'starting' | 'warning'
  label?: string
  size?: 'small' | 'default'
}

const props = withDefaults(defineProps<Props>(), { size: 'small' })

const palette = {
  online:   { tag: 'success', dot: 'var(--eid-success)',  label: 'online' },
  offline:  { tag: 'danger',  dot: 'var(--eid-danger)',   label: 'offline' },
  starting: { tag: 'warning', dot: 'var(--eid-warning)',  label: 'starting' },
  warning:  { tag: 'warning', dot: 'var(--eid-warning)',  label: 'warning' },
  unknown:  { tag: 'info',    dot: 'var(--eid-text-muted)', label: 'unknown' },
} as const

const spec = palette[props.state] ?? palette.unknown
const displayLabel = props.label ?? spec.label
</script>

<template>
  <span class="status-badge">
    <span class="dot" :style="{ background: spec.dot, boxShadow: state === 'online' ? `0 0 8px ${spec.dot}` : undefined }" />
    <el-tag :type="(spec.tag as any)" effect="dark" :size="size">{{ displayLabel }}</el-tag>
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
</style>
