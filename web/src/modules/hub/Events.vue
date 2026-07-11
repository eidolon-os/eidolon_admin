<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEventStream } from '@/components/useEventStream'
import { eventsStreamUrl } from '@/api/hub'
import StatusBadge from '@/modules/common/StatusBadge.vue'

const { connected, status, frames, lastEventAt, lastErrorAt, open, close, clear } = useEventStream({
  eventNames: ['connected', 'hub_event', 'ping'],
})
const paneRef = ref<HTMLElement | null>(null)
const follow = ref(true)
const filter = ref('')
const showProtocolFrames = ref(false)
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
  const arr = visibleRows.value.slice(-1000)
  if (!q) return arr
  return arr.filter((row) => row.searchText.includes(q))
})

const hubEventFrames = computed(() => frames.value.filter((frame) => frame.event === 'hub_event'))
const lastHubEvent = computed(() => hubEventFrames.value.at(-1) || null)
const lastPing = computed(() => [...frames.value].reverse().find((frame) => frame.event === 'ping') || null)
const connectionFrame = computed(() => frames.value.find((frame) => frame.event === 'connected') || null)
const lastHubEventAt = computed(() => {
  if (!lastHubEvent.value) return null
  const parsed = parseFrameData(lastHubEvent.value.data)
  if (typeof parsed === 'object' && parsed && 'at' in parsed) return String((parsed as any).at)
  return lastHubEvent.value.receivedAt
})

const visibleRows = computed(() =>
  frames.value
    .filter((frame) => showProtocolFrames.value || frame.event === 'hub_event')
    .map((frame) => {
      const parsed = parseFrameData(frame.data)
      const text = typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)
      const type = typeof parsed === 'object' && parsed && 'type' in parsed ? String((parsed as any).type) : frame.event
      const at = typeof parsed === 'object' && parsed && 'at' in parsed ? String((parsed as any).at) : ''
      return {
        key: `${frame.event}-${frame.receivedAt}-${frame.data}`,
        event: frame.event,
        type,
        text,
        receivedAt: frame.receivedAt,
        sourceAt: at,
        searchText: `${frame.event} ${type} ${text}`.toLowerCase(),
      }
    }),
)

const statusView = computed(() => {
  if (connected.value) return { state: 'online' as const, label: '已连接' }
  if (status.value === 'connecting') return { state: 'starting' as const, label: '连接中' }
  if (status.value === 'reconnecting') return { state: 'warning' as const, label: '重连中' }
  if (status.value === 'closed') return { state: 'offline' as const, label: started.value ? '未连接' : '已停止' }
  return { state: 'unknown' as const, label: '未启动' }
})

function parseFrameData(data: string): unknown {
  try {
    return JSON.parse(data)
  } catch {
    return data
  }
}

function fmtTime(value: number | string | null | undefined): string {
  if (!value) return '-'
  const date = typeof value === 'number' ? new Date(value) : new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleTimeString()
}

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
        <h2 class="title">Hub Device Events</h2>
        <StatusBadge :state="statusView.state" :label="statusView.label" />
      </div>
      <div class="actions">
        <el-input v-model="filter" placeholder="过滤" size="small" style="width: 240px" clearable />
        <el-checkbox v-model="showProtocolFrames" size="small">协议帧</el-checkbox>
        <el-checkbox v-model="follow" size="small">滚动跟随</el-checkbox>
        <el-button size="small" @click="clear">清空</el-button>
        <el-button size="small" :type="started ? 'warning' : 'primary'" @click="toggle">
          {{ started ? '停止订阅' : '订阅' }}
        </el-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-cell">
        <span>Source</span>
        <strong>{{ connectionFrame ? 'hub.admin_runtime' : 'waiting' }}</strong>
      </div>
      <div class="summary-cell">
        <span>Hub events</span>
        <strong>{{ hubEventFrames.length }}</strong>
      </div>
      <div class="summary-cell">
        <span>Last event</span>
        <strong>{{ fmtTime(lastHubEventAt || lastEventAt) }}</strong>
      </div>
      <div class="summary-cell">
        <span>Heartbeat</span>
        <strong>{{ fmtTime(lastPing?.receivedAt) }}</strong>
      </div>
      <div class="summary-cell">
        <span>Error</span>
        <strong>{{ fmtTime(lastErrorAt) }}</strong>
      </div>
    </div>

    <div ref="paneRef" class="stream">
      <div v-if="filtered.length === 0" class="empty">
        {{ frames.length ? '没有匹配的事件' : '(等待 Hub 设备事件...)' }}
      </div>
      <article v-for="row in filtered" :key="row.key" class="event-row" :class="`ev-${row.event}`">
        <div class="event-meta">
          <el-tag size="small" effect="plain">{{ row.event }}</el-tag>
          <strong>{{ row.type }}</strong>
          <span>{{ fmtTime(row.sourceAt || row.receivedAt) }}</span>
        </div>
        <pre>{{ row.text }}</pre>
      </article>
    </div>
    <div class="meta">
      {{ frames.length }} 帧 · {{ hubEventFrames.length }} 条 Hub 事件 · 过滤后 {{ filtered.length }}
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; height: 100%; }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 12px; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.summary-cell {
  min-width: 0;
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
  padding: 10px 12px;
}
.summary-cell span {
  display: block;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.summary-cell strong {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--eid-text-primary);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stream {
  flex: 1;
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  border: 1px solid var(--eid-border-strong);
  border-radius: var(--eid-radius);
  padding: 14px 16px;
  overflow: auto;
  height: calc(100vh - 200px);
}
.empty {
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 12px;
}
.event-row {
  border: 1px solid color-mix(in srgb, var(--eid-border) 78%, transparent);
  border-radius: 8px;
  background: rgba(13, 17, 20, 0.58);
  padding: 10px 12px;
}
.event-row + .event-row {
  margin-top: 10px;
}
.event-row.ev-connected,
.event-row.ev-ping {
  opacity: 0.74;
}
.event-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 12px;
}
.event-meta strong {
  color: var(--eid-text-primary);
  font-family: var(--eid-font);
  font-size: 13px;
}
.event-meta span {
  margin-left: auto;
}
.event-row pre {
  margin: 0;
  color: var(--eid-text-primary);
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
@media (max-width: 980px) {
  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }
  .actions {
    justify-content: flex-start;
  }
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
