<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowUp, Promotion, Refresh } from '@element-plus/icons-vue'
import StatusBadge from '@/modules/common/StatusBadge.vue'
import RegisteredUserPicker from '@/modules/common/RegisteredUserPicker.vue'
import {
  getTurn,
  listTurns,
  type ChatMessageView,
  type ListTurnsParams,
} from '@/api/conversations'
import { getLongTask, type LongTaskDetail } from '@/api/longTasks'
import { extractErrorMessage } from '@/utils/format'

// Conversation-style view over the agent /chat/test SSE endpoint.
//
// Why a separate page from ChatTest: ChatTest renders the raw turn-event
// stream (one row per DELTA/TOOL_CALL/STATE/DONE) — great for debugging the
// wire format, terrible for actually reading what the agent said. This view
// is the human-facing dual: chat bubbles, streaming text, tool calls collapsed
// inline, and recent history loaded from the agent conversation log.
//
// Note on session semantics: each user input opens a fresh pairing →
// ExchangePairingCode → Chat stream. The agent doesn't carry conversation
// state across these calls; this page only reloads recent persisted turns for
// visual continuity.
// For real conversation continuity, point a real device at the agent gRPC
// endpoint with a stable device_token.

// ──────────────────────────────────────────────────────────────────────────

interface ToolCall {
  name: string
  args?: any
  result?: any
  taskId?: string
  progressSubject?: string
  status?: string
}

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'error' | 'tool'
  text: string                  // assistant: streamed; others: full text
  tools: ToolCall[]
  state?: string                // last STATE event for assistant turn
  done: boolean
  errored: boolean
  startedAt: number
  finishedAt?: number
  durationMs?: number | null
}

// ──────────────────────────────────────────────────────────────────────────

// 2026-06-03: user_id used to default to "tester" and was a free
// text input. That caused every "chat doesn't recall my memory"
// support ticket — memory had no palace for ``tester``. Now backed by
// RegisteredUserPicker so only admin-registered users (which have
// real memory palaces) are selectable.
const settings = ref({
  tenant_id: 'default',
  user_id: '' as string | null,
  template_id: '',
})
const settingsCollapsed = ref(false)
const input = ref('')
const messages = ref<Message[]>([])
const historyLoading = ref(false)
const sending = ref(false)
const connected = ref(false)
const showEvents = ref(false)
const rawEventLog = ref<string[]>([])
const paneRef = ref<HTMLElement | null>(null)

let abortCtrl: AbortController | null = null
let historyLoadSeq = 0
const HISTORY_MESSAGE_LIMIT = 20
const longTaskPolls = new Set<string>()
let destroyed = false

function genId(): string {
  return Math.random().toString(36).slice(2, 10)
}

function normalizeRole(role: string): Message['role'] {
  if (role === 'user' || role === 'assistant' || role === 'system' || role === 'tool') return role
  if (role === 'error') return 'error'
  return 'system'
}

function tsToMs(ts: string | null | undefined): number {
  if (!ts) return Date.now()
  const ms = Date.parse(ts)
  return Number.isFinite(ms) ? ms : Date.now()
}

function fromHistoryMessage(m: ChatMessageView, turnDurationMs: number | null = null): Message {
  const at = tsToMs(m.created_at)
  return {
    id: `history-${m.id}`,
    role: normalizeRole(m.role),
    text: m.content || '',
    tools: m.tool_name
      ? [{
          name: m.tool_name,
          args: m.tool_arguments,
          result: m.role === 'tool' ? m.content : undefined,
        }]
      : [],
    done: true,
    errored: m.role === 'error',
    startedAt: at,
    finishedAt: at,
    durationMs: m.role === 'assistant' ? turnDurationMs : null,
  }
}

async function loadRecentHistory() {
  const userId = settings.value.user_id
  if (!userId) {
    historyLoadSeq += 1
    historyLoading.value = false
    messages.value = []
    return
  }
  const seq = ++historyLoadSeq
  historyLoading.value = true
  try {
    const params: ListTurnsParams = {
      tenant_id: settings.value.tenant_id,
      user_id: userId,
      limit: HISTORY_MESSAGE_LIMIT,
    }
    const r = await listTurns(params)
    const details = await Promise.all(r.turns.map((turn) => getTurn(turn.turn_id)))
    if (seq !== historyLoadSeq || sending.value) return

    const loaded = details
      .flatMap((turn) => turn.messages.map((message) => ({
        message,
        turnDurationMs: turn.total_latency_ms,
      })))
      .sort((a, b) => tsToMs(a.message.created_at) - tsToMs(b.message.created_at))
      .slice(-HISTORY_MESSAGE_LIMIT)
      .map(({ message, turnDurationMs }) => fromHistoryMessage(message, turnDurationMs))

    messages.value = loaded
    rawEventLog.value = []
    await scrollToBottom()
  } catch (e: any) {
    if (seq === historyLoadSeq) {
      ElMessage.error(`加载最近 ${HISTORY_MESSAGE_LIMIT} 条消息失败: ${extractErrorMessage(e)}`)
    }
  } finally {
    if (seq === historyLoadSeq) historyLoading.value = false
  }
}

async function send() {
  const text = input.value.trim()
  if (!text) return
  if (!settings.value.user_id) {
    ElMessage.warning('请选择 user')
    return
  }
  historyLoadSeq += 1
  historyLoading.value = false

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
    asstMsg.durationMs = asstMsg.finishedAt - asstMsg.startedAt
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
    const result = payload?.result ?? payload
    const content = result?.content ?? result
    if (tc) {
      tc.result = result
      if (isCoworkerDelegationTool(name) && content?.task_id) {
        tc.taskId = content.task_id
        tc.progressSubject = content.progress_subject
        tc.status = content.accepted ? 'accepted' : tc.status
      }
    } else {
      asst.tools.push({
        name: name || 'tool',
        result,
        taskId: isCoworkerDelegationTool(name) ? content?.task_id : undefined,
        progressSubject: isCoworkerDelegationTool(name) ? content?.progress_subject : undefined,
        status: isCoworkerDelegationTool(name) && content?.accepted ? 'accepted' : undefined,
      })
    }
  } else if (kind === 'HANDOFF') {
    const taskId = payload?.task_id
    if (taskId) {
      const tc = [...asst.tools].reverse().find((t) => t.taskId === taskId || isCoworkerDelegationTool(t.name))
      if (tc) {
        tc.taskId = taskId
        tc.progressSubject = payload?.progress_subject ?? tc.progressSubject
        tc.status = tc.status || 'accepted'
      }
      void pollLongTaskResult(taskId, asst)
    }
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

function isCoworkerDelegationTool(name: unknown): boolean {
  return name === 'delegate_to_coworker' || name === 'submit_long_task'
}

function isLongTaskTerminal(status: string | null | undefined): boolean {
  return status === 'succeeded'
    || status === 'failed'
    || status === 'cancelled'
    || status === 'timed_out'
}

function applyLongTaskDetail(task: LongTaskDetail, asst: Message) {
  const tc = [...asst.tools].reverse().find((t) => t.taskId === task.task_id)
  if (tc) {
    tc.status = task.status
    tc.result = task.result_text || task.error_message || tc.result
  }
  if (!isLongTaskTerminal(task.status)) return

  const id = `long-task-result-${task.task_id}`
  if (messages.value.some((m) => m.id === id)) return

  const ok = task.status === 'succeeded'
  const text = ok
    ? (task.result_text || '长任务已完成。')
    : `长任务 ${task.status}: ${task.error_message || task.error_code || '未返回详细错误'}`
  const at = task.completed_at ? tsToMs(task.completed_at) : Date.now()
  messages.value.push({
    id,
    role: ok ? 'assistant' : 'error',
    text,
    tools: [],
    done: true,
    errored: !ok,
    startedAt: at,
    finishedAt: at,
    durationMs: null,
  })
}

async function pollLongTaskResult(taskId: string, asst: Message) {
  if (longTaskPolls.has(taskId)) return
  longTaskPolls.add(taskId)
  try {
    for (let attempt = 0; attempt < 30 && !destroyed; attempt += 1) {
      const task = await getLongTask(taskId, { suppressToast: true })
      applyLongTaskDetail(task, asst)
      if (isLongTaskTerminal(task.status)) return
      await new Promise((resolve) => window.setTimeout(resolve, 2000))
    }
  } catch (e: any) {
    const tc = [...asst.tools].reverse().find((t) => t.taskId === taskId)
    if (tc) tc.status = `poll_error: ${extractErrorMessage(e)}`
  } finally {
    longTaskPolls.delete(taskId)
    await scrollToBottom()
  }
}

function cancel() {
  abortCtrl?.abort()
  abortCtrl = null
  sending.value = false
}

function clearAll() {
  historyLoadSeq += 1
  historyLoading.value = false
  if (sending.value) cancel()
  messages.value = []
  rawEventLog.value = []
}

async function scrollToBottom() {
  await nextTick()
  paneRef.value?.scrollTo({ top: paneRef.value.scrollHeight, behavior: 'smooth' })
}

watch(messages, scrollToBottom, { deep: true })
watch(
  () => [settings.value.tenant_id, settings.value.user_id],
  () => {
    if (sending.value) return
    void loadRecentHistory()
  },
)
onBeforeUnmount(() => {
  destroyed = true
  cancel()
})

const placeholder = computed(() =>
  sending.value ? 'agent 正在回复…' : 'Shift+Enter 换行 · Enter 发送',
)

function fmtDurationMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtClock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function fmtBubbleMeta(m: Message): string {
  const duration = m.durationMs ?? (m.finishedAt ? m.finishedAt - m.startedAt : null)
  if (m.role === 'assistant' && duration !== null && duration > 0) {
    return `耗时 ${fmtDurationMs(duration)}`
  }
  return fmtClock(m.startedAt)
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
        <el-button
          size="small"
          :icon="Refresh"
          :loading="historyLoading"
          :disabled="sending || !settings.user_id"
          @click="loadRecentHistory"
        >
          刷新历史
        </el-button>
        <el-button size="small" :disabled="sending" @click="clearAll">清空</el-button>
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
          <RegisteredUserPicker v-model="settings.user_id" width="220px" />
        </el-form-item>
        <el-form-item label="Template">
          <el-input v-model="settings.template_id" size="small" placeholder="(可选)" style="width: 200px" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Conversation pane -->
    <div ref="paneRef" class="conv">
      <div v-if="historyLoading && messages.length === 0" class="conv-empty">
        正在加载最近 20 条消息…
      </div>
      <div v-else-if="messages.length === 0" class="conv-empty">
        输入一句话开始对话 ↓
      </div>
      <div v-for="m in messages" :key="m.id" :class="['bubble-wrap', `role-${m.role}`]">
        <div :class="['bubble', { errored: m.errored, streaming: !m.done }]">
          <div v-if="m.role === 'user'" class="bubble-meta-top">USER</div>
          <div v-else-if="m.role === 'assistant'" class="bubble-meta-top">
            ASSISTANT
            <span v-if="m.state && !m.done" class="state-tag">· {{ m.state }}</span>
          </div>
          <div v-else class="bubble-meta-top">{{ m.role.toUpperCase() }}</div>

          <!-- Tool calls (inline cards within assistant bubble) -->
          <div v-for="(t, i) in m.tools" :key="i" class="tool-card">
            <div class="tool-name">
              ⚙ {{ t.name }}
              <span v-if="t.status" class="tool-status">{{ t.status }}</span>
              <span v-if="t.taskId" class="tool-task-id">{{ t.taskId.slice(0, 8) }}</span>
            </div>
            <pre v-if="t.args" class="tool-block">args: {{ JSON.stringify(t.args, null, 2) }}</pre>
            <pre v-if="t.result" class="tool-block">result: {{ typeof t.result === 'string' ? t.result : JSON.stringify(t.result, null, 2) }}</pre>
          </div>

          <div v-if="m.text" class="bubble-text">{{ m.text }}<span v-if="!m.done && m.role === 'assistant'" class="cursor">▍</span></div>
          <div v-else-if="!m.done && m.role === 'assistant'" class="bubble-thinking">
            <span class="dot" /><span class="dot" /><span class="dot" />
          </div>

          <div v-if="m.done" class="bubble-meta-bottom">{{ fmtBubbleMeta(m) }}</div>
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
.small { font-size: 12px; line-height: 1.45; color: var(--eid-text-muted); }

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
.bubble-wrap.role-error,
.bubble-wrap.role-tool { justify-content: flex-start; }

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
  font-size: 11px;
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
  line-height: 1.62;
  font-size: 14px;
  color: var(--eid-text-primary);
}
.bubble-meta-bottom {
  font-size: 11px;
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
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-family: var(--eid-font-mono);
  color: var(--eid-accent);
  font-weight: 500;
  margin-bottom: 4px;
}
.tool-status,
.tool-task-id {
  color: var(--eid-text-muted);
  border: 1px solid var(--eid-border);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 11px;
  line-height: 1.4;
}
.tool-block {
  margin: 4px 0 0 0;
  padding: 6px 8px;
  background: var(--eid-bg-canvas);
  border-radius: 4px;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.55;
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
.events .hint { color: var(--eid-text-muted); font-size: 12px; line-height: 1.5; }
.events-log {
  background: var(--eid-bg-inset);
  color: var(--eid-text-secondary);
  border-radius: 4px;
  padding: 10px 12px;
  margin: 0;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.55;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
