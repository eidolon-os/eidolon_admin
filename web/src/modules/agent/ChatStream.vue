<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowUp, Promotion, Refresh } from '@element-plus/icons-vue'
import StatusBadge from '@/modules/common/StatusBadge.vue'

// Conversation-style view over the agent /chat/test SSE endpoint.
//
// Why a separate page from ChatTest: ChatTest renders the raw turn-event
// stream (one row per DELTA/TOOL_CALL/STATE/DONE) — great for debugging the
// wire format, terrible for actually reading what the agent said. This view
// is the human-facing dual: chat bubbles, streaming text, tool calls collapsed
// inline, multi-turn history maintained client-side.
//
// Note on session semantics: each user input opens a fresh pairing →
// ExchangePairingCode → Chat stream. The agent doesn't carry conversation
// state across these calls; the *visual* history lives only in this page.
// For real conversation continuity, point a real device at the agent gRPC
// endpoint with a stable device_token.

// ──────────────────────────────────────────────────────────────────────────

interface ToolCall {
  name: string
  args?: any
  result?: any
}

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'error'
  text: string                  // assistant: streamed; others: full text
  tools: ToolCall[]
  state?: string                // last STATE event for assistant turn
  done: boolean
  errored: boolean
  startedAt: number
  finishedAt?: number
}

// ──────────────────────────────────────────────────────────────────────────

const settings = ref({
  tenant_id: 'default',
  user_id: 'tester',
  template_id: '',
})
const settingsCollapsed = ref(false)
const input = ref('')
const messages = ref<Message[]>([])
const sending = ref(false)
const connected = ref(false)
const showEvents = ref(false)
const rawEventLog = ref<string[]>([])
const paneRef = ref<HTMLElement | null>(null)

let abortCtrl: AbortController | null = null

function genId(): string {
  return Math.random().toString(36).slice(2, 10)
}

async function send() {
  const text = input.value.trim()
  if (!text) return
  if (!settings.value.user_id) {
    ElMessage.warning('请填写 user_id')
    return
  }

  // append user bubble
  const userMsg: Message = {
    id: genId(),
    role: 'user',
    text,
    tools: [],
    done: true,
    errored: false,
    startedAt: Date.now(),
    finishedAt: Date.now(),
  }
  messages.value.push(userMsg)

  // prepare assistant bubble (will stream into this)
  const asstMsg: Message = {
    id: genId(),
    role: 'assistant',
    text: '',
    tools: [],
    done: false,
    errored: false,
    startedAt: Date.now(),
  }
  messages.value.push(asstMsg)

  input.value = ''
  sending.value = true
  settingsCollapsed.value = true
  await scrollToBottom()

  abortCtrl?.abort()
  abortCtrl = new AbortController()

  try {
    const resp = await fetch('/api/services/agent/chat/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...settings.value, text }),
      signal: abortCtrl.signal,
    })
    if (!resp.ok || !resp.body) {
      asstMsg.errored = true
      asstMsg.text = `HTTP ${resp.status}`
      asstMsg.done = true
      return
    }
    connected.value = true
    await parseSSE(resp.body, asstMsg)
  } catch (e: any) {
    if (e.name !== 'AbortError') {
      asstMsg.errored = true
      asstMsg.text = asstMsg.text
        ? `${asstMsg.text}\n\n[stream error] ${e.message}`
        : `[stream error] ${e.message}`
    }
  } finally {
    asstMsg.done = true
    asstMsg.finishedAt = Date.now()
    sending.value = false
    connected.value = false
    abortCtrl = null
    await scrollToBottom()
  }
}

async function parseSSE(stream: ReadableStream<Uint8Array>, asst: Message) {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      const ev = parseEventBlock(block)
      if (!ev) continue
      rawEventLog.value.push(`[${ev.event}] ${JSON.stringify(ev.data).slice(0, 200)}`)
      applyEvent(ev, asst)
      // throttle UI update via microtask; nextTick keeps scroll smooth
    }
    await scrollToBottom()
  }
}

interface ParsedEvent { event: string; data: any }

function parseEventBlock(block: string): ParsedEvent | null {
  let event = 'message'
  let dataLine = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLine = line.slice(5).trim()
  }
  if (!dataLine) return null
  let data: any = dataLine
  try { data = JSON.parse(dataLine) } catch { /* keep string */ }
  return { event, data }
}

function applyEvent(ev: ParsedEvent, asst: Message) {
  // Agent's chat_test endpoint emits two SSE event types:
  //   - "status" — handshake breadcrumbs (paired, device_token issued, ...)
  //   - "event"  — turn events with {kind, data}; kinds incl.
  //       STATE, DELTA, TOOL_CALL, TOOL_RESULT, DONE, ERROR
  if (ev.event === 'status') {
    return  // surfaced in the raw event log; doesn't need a chat bubble
  }

  const kind = (typeof ev.data === 'object' && ev.data && ev.data.kind) || ''
  const payload = (typeof ev.data === 'object' && ev.data && ev.data.data) ?? ev.data

  if (kind === 'DELTA') {
    // Accept several common shapes: {text}, {delta}, "raw string".
    const delta =
      (typeof payload === 'object' && (payload.text ?? payload.delta ?? payload.chunk))
      ?? (typeof payload === 'string' ? payload : '')
    if (typeof delta === 'string') asst.text += delta
  } else if (kind === 'STATE') {
    asst.state = (typeof payload === 'object' && payload.state) || JSON.stringify(payload)
  } else if (kind === 'TOOL_CALL') {
    asst.tools.push({
      name: (payload && (payload.tool || payload.name)) || 'tool',
      args: payload?.args ?? payload?.arguments,
    })
  } else if (kind === 'TOOL_RESULT') {
    // attach result to the most recent matching tool call
    const name = payload?.tool || payload?.name
    const tc = [...asst.tools].reverse().find((t) => !name || t.name === name)
    if (tc) tc.result = payload?.result ?? payload
    else asst.tools.push({ name: name || 'tool', result: payload })
  } else if (kind === 'ERROR') {
    asst.errored = true
    const msg = (typeof payload === 'object' && (payload.message || payload.error)) || JSON.stringify(payload)
    asst.text = asst.text ? `${asst.text}\n\n[ERROR] ${msg}` : `[ERROR] ${msg}`
  } else if (kind === 'DONE') {
    asst.state = 'done'
  } else {
    // unknown kind — record in events log but don't pollute bubble
  }
}

function cancel() {
  abortCtrl?.abort()
  abortCtrl = null
  sending.value = false
}

function clearAll() {
  if (sending.value) cancel()
  messages.value = []
  rawEventLog.value = []
}

async function scrollToBottom() {
  await nextTick()
  paneRef.value?.scrollTo({ top: paneRef.value.scrollHeight, behavior: 'smooth' })
}

watch(messages, scrollToBottom, { deep: true })
onBeforeUnmount(cancel)

const placeholder = computed(() =>
  sending.value ? 'agent 正在回复…' : 'Shift+Enter 换行 · Enter 发送',
)

function fmtDuration(m: Message): string {
  if (!m.finishedAt) return ''
  const ms = m.finishedAt - m.startedAt
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    if (!sending.value) send()
  }
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Chat Stream</h2>
        <StatusBadge
          v-if="sending"
          state="starting"
          label="streaming"
        />
      </div>
      <div class="actions">
        <el-checkbox v-model="showEvents" size="small">显示原始事件</el-checkbox>
        <el-button size="small" :icon="Refresh" :disabled="sending" @click="clearAll">清空</el-button>
      </div>
    </div>

    <!-- Settings (collapsible) -->
    <el-card class="settings" :class="{ collapsed: settingsCollapsed }">
      <template #header>
        <div class="settings-header" @click="settingsCollapsed = !settingsCollapsed">
          <span>
            <el-icon><component :is="settingsCollapsed ? ArrowDown : ArrowUp" /></el-icon>
            会话设置
          </span>
          <span class="mono small">
            {{ settings.tenant_id }} / {{ settings.user_id || '(no user)' }}
            <template v-if="settings.template_id"> · {{ settings.template_id }}</template>
          </span>
        </div>
      </template>
      <el-form v-if="!settingsCollapsed" inline>
        <el-form-item label="Tenant">
          <el-input v-model="settings.tenant_id" size="small" style="width: 160px" />
        </el-form-item>
        <el-form-item label="User" required>
          <el-input v-model="settings.user_id" size="small" placeholder="tester" style="width: 160px" />
        </el-form-item>
        <el-form-item label="Template">
          <el-input v-model="settings.template_id" size="small" placeholder="(可选)" style="width: 200px" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Conversation pane -->
    <div ref="paneRef" class="conv">
      <div v-if="messages.length === 0" class="conv-empty">
        输入一句话开始对话 ↓
      </div>
      <div v-for="m in messages" :key="m.id" :class="['bubble-wrap', `role-${m.role}`]">
        <div :class="['bubble', { errored: m.errored, streaming: !m.done }]">
          <div v-if="m.role === 'user'" class="bubble-meta-top">USER</div>
          <div v-else-if="m.role === 'assistant'" class="bubble-meta-top">
            ASSISTANT
            <span v-if="m.state && !m.done" class="state-tag">· {{ m.state }}</span>
          </div>

          <!-- Tool calls (inline cards within assistant bubble) -->
          <div v-for="(t, i) in m.tools" :key="i" class="tool-card">
            <div class="tool-name">⚙ {{ t.name }}</div>
            <pre v-if="t.args" class="tool-block">args: {{ JSON.stringify(t.args, null, 2) }}</pre>
            <pre v-if="t.result" class="tool-block">result: {{ typeof t.result === 'string' ? t.result : JSON.stringify(t.result, null, 2) }}</pre>
          </div>

          <div v-if="m.text" class="bubble-text">{{ m.text }}<span v-if="!m.done && m.role === 'assistant'" class="cursor">▍</span></div>
          <div v-else-if="!m.done && m.role === 'assistant'" class="bubble-thinking">
            <span class="dot" /><span class="dot" /><span class="dot" />
          </div>

          <div v-if="m.done" class="bubble-meta-bottom">{{ fmtDuration(m) }}</div>
        </div>
      </div>
    </div>

    <!-- Composer -->
    <div class="composer">
      <el-input
        v-model="input"
        type="textarea"
        :rows="3"
        :autosize="{ minRows: 2, maxRows: 8 }"
        :placeholder="placeholder"
        :disabled="sending"
        @keydown="onKeyDown"
      />
      <div class="composer-actions">
        <el-button
          v-if="sending"
          type="warning"
          @click="cancel"
        >取消</el-button>
        <el-button
          v-else
          type="primary"
          :icon="Promotion"
          :disabled="!input.trim()"
          @click="send"
        >发送</el-button>
      </div>
    </div>

    <!-- Raw event log (optional drawer-like panel) -->
    <el-card v-if="showEvents" class="events" shadow="never">
      <template #header>
        <span>原始事件（最近 50）</span>
        <span class="hint">· 调试用，正常对话看上面气泡即可</span>
      </template>
      <pre class="events-log">{{ rawEventLog.slice(-50).join('\n') || '(无)' }}</pre>
    </el-card>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.title-row { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.actions { display: flex; gap: 8px; align-items: center; }

.settings {
  margin-bottom: 12px;
  flex: 0 0 auto;
}
.settings :deep(.el-card__header) {
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
}
.settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}
.settings.collapsed :deep(.el-card__body) { padding: 0; }
.mono { font-family: var(--eid-font-mono); }
.small { font-size: 11px; color: var(--eid-text-muted); }

.conv {
  flex: 1;
  overflow-y: auto;
  padding: 14px 4px;
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border-strong);
  border-radius: var(--eid-radius);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.conv-empty {
  text-align: center;
  color: var(--eid-text-muted);
  padding: 60px 0;
  font-size: 13px;
}

.bubble-wrap {
  display: flex;
  padding: 0 14px;
}
.bubble-wrap.role-user { justify-content: flex-end; }
.bubble-wrap.role-assistant,
.bubble-wrap.role-system,
.bubble-wrap.role-error { justify-content: flex-start; }

.bubble {
  max-width: 75%;
  background: var(--eid-bg-panel);
  border: 1px solid var(--eid-border);
  border-radius: 10px;
  padding: 10px 14px;
  position: relative;
}
.role-user .bubble {
  background: var(--eid-accent-soft);
  border-color: var(--eid-accent);
}
.bubble.errored {
  border-color: var(--eid-danger);
  background: rgba(239, 68, 68, 0.08);
}
.bubble.streaming { border-color: var(--eid-accent); }
.bubble-meta-top {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--eid-text-muted);
  text-transform: uppercase;
  margin-bottom: 6px;
}
.state-tag { color: var(--eid-text-secondary); font-weight: normal; }
.bubble-text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
  font-size: 13.5px;
  color: var(--eid-text-primary);
}
.bubble-meta-bottom {
  font-size: 10px;
  color: var(--eid-text-muted);
  margin-top: 6px;
  text-align: right;
}
.cursor {
  display: inline-block;
  animation: blink 0.9s steps(1, start) infinite;
  margin-left: 1px;
  color: var(--eid-accent);
}
@keyframes blink { 50% { opacity: 0; } }

.bubble-thinking {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 4px 0;
}
.bubble-thinking .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--eid-text-muted);
  animation: bounce 1.4s infinite ease-in-out both;
}
.bubble-thinking .dot:nth-child(2) { animation-delay: 0.16s; }
.bubble-thinking .dot:nth-child(3) { animation-delay: 0.32s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
  40% { transform: scale(1); opacity: 1; }
}

.tool-card {
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  border-radius: 6px;
  padding: 8px 10px;
  margin: 6px 0;
  font-size: 12px;
}
.tool-name {
  font-family: var(--eid-font-mono);
  color: var(--eid-accent);
  font-weight: 500;
  margin-bottom: 4px;
}
.tool-block {
  margin: 4px 0 0 0;
  padding: 6px 8px;
  background: var(--eid-bg-canvas);
  border-radius: 4px;
  font-family: var(--eid-font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow: auto;
  color: var(--eid-text-secondary);
}

.composer {
  margin-top: 12px;
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.composer :deep(.el-textarea) { flex: 1; }
.composer-actions { flex: 0 0 auto; }

.events {
  margin-top: 12px;
  flex: 0 0 auto;
}
.events :deep(.el-card__header) { padding: 8px 14px; }
.events .hint { color: var(--eid-text-muted); font-size: 11px; }
.events-log {
  background: var(--eid-bg-inset);
  color: var(--eid-text-secondary);
  border-radius: 4px;
  padding: 10px 12px;
  margin: 0;
  font-family: var(--eid-font-mono);
  font-size: 11px;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
