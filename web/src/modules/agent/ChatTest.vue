<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, ChatLineRound, Promotion } from '@element-plus/icons-vue'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import AgentScopeSelector from './components/AgentScopeSelector.vue'
import { chatTestUrl } from '@/api/agentRuntime'
import { useOwnersStore } from '@/stores/owners'

// Chat test uses SSE over POST — fetch-with-stream-reader rather than
// EventSource (which only supports GET).

const ownersStore = useOwnersStore()
const form = ref({
  owner_id: ownersStore.currentId,
  companion_id: '',
  text: '',
  persist_memory: false,
})

const sending = ref(false)
const connected = ref(false)
const mode = ref<'stream' | 'events'>('stream')
const events = ref<Array<{ type: string; data: any; ts: number }>>([])
const userText = ref('')
const assistantText = ref('')
const paneRef = ref<HTMLElement | null>(null)
let abortCtrl: AbortController | null = null

async function send() {
  if (!form.value.text.trim()) {
    ElMessage.warning('请输入要测试的话')
    return
  }
  if (!form.value.owner_id || !form.value.companion_id) {
    ElMessage.warning('请选择 owner 和 companion')
    return
  }
  cancel()
  events.value = []
  userText.value = form.value.text.trim()
  assistantText.value = ''
  sending.value = true
  connected.value = false

  abortCtrl = new AbortController()
  try {
    const resp = await fetch(chatTestUrl(), {
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
        appendAssistantDelta(ev)
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

function clearOutput() {
  events.value = []
  userText.value = ''
  assistantText.value = ''
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

function appendAssistantDelta(e: { type: string; data: any }) {
  if (e.type !== 'event' || e.data?.kind !== 'DELTA') return
  const text = e.data?.data?.text
  if (typeof text === 'string') assistantText.value += text
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
          <el-form-item label="Runtime identity" class="identity-item">
            <AgentScopeSelector
              v-model:owner-id="form.owner_id"
              v-model:companion-id="form.companion_id"
            />
          </el-form-item>
        </div>
        <el-form-item label="用户消息">
          <el-input v-model="form.text" type="textarea" :rows="3" placeholder="例如：请点名 box-3，让它发出本地识别提示" />
        </el-form-item>
        <el-form-item label="Memory 写入">
          <el-switch
            v-model="form.persist_memory"
            active-text="写入"
            inactive-text="只读"
          />
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
          <span><el-icon><ChatLineRound /></el-icon> Chat</span>
          <el-radio-group v-model="mode" size="small">
            <el-radio-button label="stream">Stream</el-radio-button>
            <el-radio-button label="events">Events</el-radio-button>
          </el-radio-group>
          <el-button size="small" link @click="clearOutput">清空</el-button>
        </div>
      </template>

      <div v-if="mode === 'stream'" class="stream-pane">
        <div v-if="userText" class="bubble user">
          <div class="bubble-label">User</div>
          <div class="bubble-text">{{ userText }}</div>
        </div>
        <div class="bubble assistant">
          <div class="bubble-label">
            <el-icon><ChatDotRound /></el-icon>
            Assistant
          </div>
          <div class="bubble-text">{{ assistantText || (sending ? '...' : '') }}</div>
        </div>
        <div v-if="!userText && !assistantText" class="empty-hint">(尚无消息)</div>
      </div>

      <div v-else ref="paneRef" class="events">
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
.form-grid { display: grid; grid-template-columns: 1fr; gap: 16px; }
.identity-item { margin-bottom: 0; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.stream-pane {
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border-strong);
  border-radius: 6px;
  padding: 14px;
  min-height: 220px;
  max-height: 60vh;
  overflow: auto;
}
.bubble {
  display: grid;
  gap: 6px;
  max-width: min(760px, 100%);
  margin-bottom: 14px;
}
.bubble.user {
  margin-left: auto;
  text-align: right;
}
.bubble.assistant {
  margin-right: auto;
}
.bubble-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--eid-text-muted);
}
.bubble.user .bubble-label {
  justify-content: flex-end;
}
.bubble-text {
  border: 1px solid var(--eid-border-strong);
  border-radius: 6px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--eid-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
}
.bubble.user .bubble-text {
  background: rgba(35, 211, 255, 0.14);
  border-color: rgba(35, 211, 255, 0.35);
}
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
