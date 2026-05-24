<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, ChatLineRound } from '@element-plus/icons-vue'
import StatusBadge from '@/modules/common/StatusBadge.vue'

// Chat test uses SSE over POST — fetch-with-stream-reader rather than
// EventSource (which only supports GET).

const form = ref({
  tenant_id: 'default',
  user_id: 'tester',
  template_id: '',
  text: '',
})

const sending = ref(false)
const connected = ref(false)
const events = ref<Array<{ type: string; data: any; ts: number }>>([])
const paneRef = ref<HTMLElement | null>(null)
let abortCtrl: AbortController | null = null

async function send() {
  if (!form.value.text.trim()) {
    ElMessage.warning('请输入要测试的话')
    return
  }
  cancel()
  events.value = []
  sending.value = true
  connected.value = false

  abortCtrl = new AbortController()
  try {
    const resp = await fetch('/api/services/agent/chat/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
      signal: abortCtrl.signal,
    })
    if (!resp.ok || !resp.body) {
      events.value.push({ type: 'error', data: `HTTP ${resp.status}`, ts: Date.now() })
      return
    }
    connected.value = true
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // SSE events are separated by blank lines.
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const block of parts) {
        const ev: { type: string; data: any; ts: number } = { type: 'event', data: '', ts: Date.now() }
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) ev.type = line.slice(6).trim()
          else if (line.startsWith('data:')) {
            const text = line.slice(5).trim()
            try { ev.data = JSON.parse(text) } catch { ev.data = text }
          }
        }
        events.value.push(ev)
      }
    }
  } catch (e: any) {
    if (e.name !== 'AbortError') {
      events.value.push({ type: 'error', data: e.message, ts: Date.now() })
    }
  } finally {
    sending.value = false
    connected.value = false
  }
}

function cancel() {
  abortCtrl?.abort()
  abortCtrl = null
}

onBeforeUnmount(cancel)

const recent = computed(() => events.value.slice(-200))

watch(recent, async () => {
  await nextTick()
  paneRef.value?.scrollTo({ top: paneRef.value.scrollHeight })
})

function fmtEv(e: { type: string; data: any }) {
  if (typeof e.data === 'string') return e.data
  return JSON.stringify(e.data)
}
function evTagType(t: string): 'success' | 'warning' | 'danger' | 'info' {
  if (t === 'status') return 'info'
  if (t === 'error') return 'danger'
  if (t === 'event') return 'success'
  return 'warning'
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Chat Test</h2>
        <StatusBadge
          :state="sending ? 'starting' : connected ? 'online' : 'unknown'"
          :label="sending ? 'streaming' : connected ? '已连接' : '空闲'"
        />
      </div>
    </div>

    <el-card>
      <el-form>
        <div class="form-grid">
          <el-form-item label="Tenant"><el-input v-model="form.tenant_id" /></el-form-item>
          <el-form-item label="User"><el-input v-model="form.user_id" /></el-form-item>
          <el-form-item label="Template (可选)"><el-input v-model="form.template_id" /></el-form-item>
        </div>
        <el-form-item label="用户消息">
          <el-input v-model="form.text" type="textarea" :rows="3" placeholder="模拟用户的一句话…" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Promotion" :loading="sending" @click="send">发送</el-button>
          <el-button v-if="sending" @click="cancel">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header>
        <div class="bar">
          <span><el-icon><ChatLineRound /></el-icon>  事件流（{{ recent.length }}）</span>
          <el-button size="small" link @click="events = []">清空</el-button>
        </div>
      </template>
      <div ref="paneRef" class="events">
        <div v-for="(e, i) in recent" :key="i" class="event">
          <el-tag :type="evTagType(e.type)" size="small" effect="dark">{{ e.type }}</el-tag>
          <span class="ev-data">{{ fmtEv(e) }}</span>
        </div>
        <div v-if="recent.length === 0" class="empty-hint">(尚无事件)</div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title-row { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.events {
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border-strong);
  border-radius: 6px;
  padding: 12px 14px;
  max-height: 60vh;
  overflow: auto;
}
.event {
  display: flex;
  gap: 10px;
  padding: 4px 0;
  font-size: 12.5px;
  border-bottom: 1px solid var(--eid-border);
}
.event:last-child { border-bottom: none; }
.ev-data {
  font-family: var(--eid-font-mono);
  white-space: pre-wrap;
  word-break: break-word;
  flex: 1;
  color: var(--eid-text-primary);
}
.empty-hint {
  text-align: center;
  color: var(--eid-text-muted);
  padding: 24px;
  font-size: 12px;
}
</style>
