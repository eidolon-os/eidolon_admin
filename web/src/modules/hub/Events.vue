<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEventStream } from '@/components/useEventStream'
import { eventsStreamUrl } from '@/api/hub'
import StatusBadge from '@/modules/common/StatusBadge.vue'

const { connected, lines, open, close, clear } = useEventStream()
const paneRef = ref<HTMLElement | null>(null)
const follow = ref(true)
const filter = ref('')
const started = ref(false)

onMounted(() => {
  open(eventsStreamUrl())
  started.value = true
})
onBeforeUnmount(close)

function toggle() {
  if (started.value) {
    close()
    started.value = false
  } else {
    open(eventsStreamUrl())
    started.value = true
  }
}

const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const arr = lines.value.slice(-1000)
  if (!q) return arr
  return arr.filter((l) => l.toLowerCase().includes(q))
})

watch(filtered, async () => {
  if (!follow.value) return
  await nextTick()
  paneRef.value?.scrollTo({ top: paneRef.value.scrollHeight })
})
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Events (SSE)</h2>
        <StatusBadge :state="connected ? 'online' : 'offline'" :label="connected ? '已连接' : '未连接'" />
      </div>
      <div class="actions">
        <el-input v-model="filter" placeholder="过滤" size="small" style="width: 240px" clearable />
        <el-checkbox v-model="follow" size="small">滚动跟随</el-checkbox>
        <el-button size="small" @click="clear">清空</el-button>
        <el-button size="small" :type="started ? 'warning' : 'primary'" @click="toggle">
          {{ started ? '停止订阅' : '订阅' }}
        </el-button>
      </div>
    </div>

    <pre ref="paneRef" class="stream">{{ filtered.join('\n') || '(等待事件...)' }}</pre>
    <div class="meta">{{ lines.length }} 行 · 过滤后 {{ filtered.length }}</div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.actions { display: flex; gap: 8px; align-items: center; }
.stream {
  flex: 1;
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  border: 1px solid var(--eid-border-strong);
  border-radius: var(--eid-radius);
  padding: 14px 16px;
  margin: 0;
  overflow: auto;
  height: calc(100vh - 200px);
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}
.meta {
  font-size: 12px;
  color: var(--eid-text-muted);
  margin-top: 8px;
}
</style>
