<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useEventStream } from '@/components/useEventStream'

// URL-driven SSE log viewer. The caller decides what to show (which program,
// which stream, what label) and just hands us a streaming endpoint URL.

interface Props {
  open: boolean
  /** Drawer title (e.g. "memory:memory-supervisor :: stdout"). */
  title: string
  /** SSE endpoint URL (text/event-stream, one log line per event). */
  url: string
}

const props = defineProps<Props>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const { connected, lines, open: openStream, close, clear } = useEventStream()
const paneRef = ref<HTMLElement | null>(null)
const follow = ref(true)

watch(
  () => props.open,
  (v) => {
    if (v && props.url) {
      clear()
      openStream(props.url)
    } else {
      close()
    }
  },
)

// Re-subscribe if the caller hot-swaps the URL while open.
watch(
  () => props.url,
  (url, prev) => {
    if (!props.open || url === prev) return
    clear()
    if (url) openStream(url)
  },
)

watch(lines, async () => {
  if (!follow.value) return
  await nextTick()
  paneRef.value?.scrollTo({ top: paneRef.value.scrollHeight })
})
</script>

<template>
  <el-drawer
    :model-value="open"
    @update:model-value="(v: boolean) => emit('update:open', v)"
    :title="title"
    size="70%"
    direction="rtl"
  >
    <div class="log-wrap">
      <div class="toolbar">
        <el-tag :type="connected ? 'success' : 'info'" size="small" effect="dark">
          {{ connected ? '已连接' : '未连接' }}
        </el-tag>
        <el-checkbox v-model="follow" size="small">滚动跟随</el-checkbox>
        <el-button size="small" @click="clear">清空</el-button>
        <span class="hint">{{ lines.length }} 行</span>
      </div>
      <pre ref="paneRef" class="stream">{{ lines.join('\n') }}</pre>
    </div>
  </el-drawer>
</template>

<style scoped>
.log-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 12px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hint {
  font-size: 12px;
  color: var(--eid-text-muted);
  margin-left: auto;
}
.stream {
  flex: 1;
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  border: 1px solid var(--eid-border-strong);
  border-radius: 6px;
  padding: 12px 14px;
  margin: 0;
  overflow: auto;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
