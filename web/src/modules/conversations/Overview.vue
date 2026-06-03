<script setup lang="ts">
/**
 * /conversations — read-only browse over agent's chat turn log.
 *
 * Phase 34.B. Master/detail pattern:
 *   - Master (left): paginated table of turns, filterable by user via
 *     the shared RegisteredUserPicker. Cursor-paginated using the
 *     ``next_before`` field the endpoint hands back.
 *   - Detail (right): clicking a row loads the full TurnDetail and
 *     renders the message bubbles + a collapsible "调试信息" block
 *     with latency / tokens / model / trace_id / metadata.
 *
 * Refresh interval: poll every 10s while no row is selected (catches
 * new turns); pause polling when a detail is being read.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, ChatLineRound, ArrowDown, ArrowRight } from '@element-plus/icons-vue'
import {
  getTurn,
  listTurns,
  type ListTurnsParams,
  type TurnDetail,
  type TurnSummary,
} from '@/api/conversations'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import RegisteredUserPicker from '@/modules/common/RegisteredUserPicker.vue'

const turns = ref<TurnSummary[]>([])
const loading = ref(false)
const detail = ref<TurnDetail | null>(null)
const detailLoading = ref(false)
const selectedId = ref<string | null>(null)
const filterUserId = ref<string | null>(null)
const cursor = ref<string | null>(null)
const hasMore = computed(() => cursor.value !== null)
const debugCollapsed = ref(true)

let pollTimer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  loading.value = true
  cursor.value = null
  try {
    const params: ListTurnsParams = { limit: 50 }
    if (filterUserId.value) params.user_id = filterUserId.value
    const r = await listTurns(params)
    turns.value = r.turns
    cursor.value = r.next_before
  } catch (e: any) {
    ElMessage.error(`加载对话记录失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (!cursor.value) return
  loading.value = true
  try {
    const params: ListTurnsParams = { limit: 50, before: cursor.value }
    if (filterUserId.value) params.user_id = filterUserId.value
    const r = await listTurns(params)
    turns.value = [...turns.value, ...r.turns]
    cursor.value = r.next_before
  } catch (e: any) {
    ElMessage.error(`加载下一页失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function select(row: TurnSummary) {
  selectedId.value = row.turn_id
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getTurn(row.turn_id)
  } catch (e: any) {
    ElMessage.error(`加载详情失败: ${extractErrorMessage(e)}`)
  } finally {
    detailLoading.value = false
  }
}

function clearSelection() {
  selectedId.value = null
  detail.value = null
}

// Refresh when user filter changes. RegisteredUserPicker auto-selects
// the first registered user on mount, which fires this watcher with a
// real id (we want refresh — not the legacy "fetch all" view).
watch(filterUserId, () => {
  clearSelection()
  void refresh()
})

onMounted(() => {
  void refresh()
  // Poll only when nothing is selected — looking at a detail is a
  // read-and-hold action; we don't want to silently bump the list.
  pollTimer = setInterval(() => {
    if (loading.value || detailLoading.value || selectedId.value) return
    void refresh()
  }, 10_000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})

// ── helpers ────────────────────────────────────────────────────────────────

function statusTagType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === 'ok') return 'success'
  if (s === 'error' || s === 'failed') return 'danger'
  if (s === 'pending' || s === 'in_progress') return 'warning'
  return 'info'
}

/** Pull the first user-utterance message out of a row's summary for
 *  the master-list preview. We don't have full messages here so we
 *  show the seq/triage hint instead — clicking opens detail. */
function previewLine(t: TurnSummary): string {
  const triage = t.triage_kind ? ` · ${t.triage_kind}` : ''
  return `turn ${t.seq}${triage}`
}

function roleTagType(r: string): 'primary' | 'success' | 'warning' | 'info' {
  if (r === 'user') return 'primary'
  if (r === 'assistant') return 'success'
  if (r === 'tool') return 'warning'
  return 'info'
}

function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function fmtCost(micro: number | null | undefined): string {
  if (!micro) return '$0'
  // micro-USD → USD with 4 decimals; tiny values get more precision so
  // we don't show "$0.0000" for a real (small) cost.
  return `$${(micro / 1_000_000).toFixed(6)}`
}

function rowClass({ row }: { row: TurnSummary }): string {
  return row.turn_id === selectedId.value ? 'is-selected' : ''
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h2>对话记录</h2>
        <p class="hint">
          只读视图：浏览 agent SQLite 里的 turn 日志。按 user 过滤；
          点击一行展开看消息正文 + 调试信息（latency / tokens / trace）。
        </p>
      </div>
      <div class="head-actions">
        <RegisteredUserPicker
          v-model="filterUserId"
          width="240px"
          placeholder="按 user 过滤"
          :auto-select-first="true"
        />
        <el-button :icon="Refresh" :loading="loading" size="small" @click="refresh">
          刷新
        </el-button>
      </div>
    </header>

    <div class="split">
      <div class="left">
        <el-table
          v-loading="loading && turns.length === 0"
          :data="turns"
          stripe
          highlight-current-row
          size="small"
          :row-class-name="rowClass"
          @row-click="(row: TurnSummary) => select(row)"
        >
          <el-table-column label="time" width="160">
            <template #default="{ row }">
              <span class="muted mono">{{ formatTimestamp(row.started_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="user" width="120">
            <template #default="{ row }">
              <span class="mono">{{ row.user_id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="status" width="80">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="turn" min-width="140">
            <template #default="{ row }">
              <span class="muted">{{ previewLine(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="latency" width="100">
            <template #default="{ row }">
              <span class="muted mono">{{ fmtDuration(row.total_latency_ms) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="!loading && turns.length === 0" class="empty">
          这个 user 还没有对话记录。让 ta 通过 companion / agent chat 测试发一句话试试。
        </div>

        <div class="pager">
          <el-button
            v-if="hasMore"
            size="small"
            :loading="loading"
            @click="loadMore"
          >
            加载更多
          </el-button>
          <span v-else-if="turns.length > 0" class="muted">已加载全部</span>
        </div>
      </div>

      <div class="right">
        <div v-if="!detail && !detailLoading" class="placeholder">
          <el-icon><ChatLineRound /></el-icon>
          <span>选择左侧一行查看详情</span>
        </div>
        <div v-else-if="detailLoading" class="placeholder">加载中…</div>
        <template v-else-if="detail">
          <div class="detail-head">
            <div>
              <h3>{{ detail.user_id }} · turn {{ detail.seq }}</h3>
              <p class="meta">
                <code class="mono">{{ detail.turn_id.slice(0, 12) }}…</code>
                <span>•</span>
                <span>{{ formatTimestamp(detail.started_at) }}</span>
                <span>•</span>
                <el-tag :type="statusTagType(detail.status)" size="small" effect="dark">
                  {{ detail.status }}
                </el-tag>
              </p>
            </div>
            <el-button size="small" link @click="clearSelection">关闭</el-button>
          </div>

          <div class="messages">
            <div
              v-for="m in detail.messages"
              :key="m.id"
              class="msg"
              :class="`role-${m.role}`"
            >
              <div class="msg-head">
                <el-tag :type="roleTagType(m.role)" size="small" effect="dark">
                  {{ m.role }}
                </el-tag>
                <span v-if="m.tool_name" class="muted mono">{{ m.tool_name }}</span>
                <span class="muted">{{ formatTimestamp(m.created_at) }}</span>
              </div>
              <div class="msg-body">
                <pre v-if="m.role === 'tool' || m.tool_arguments">{{ m.content }}</pre>
                <span v-else>{{ m.content }}</span>
                <div v-if="m.tool_arguments" class="tool-args">
                  <span class="muted">args:</span>
                  <pre>{{ JSON.stringify(m.tool_arguments, null, 2) }}</pre>
                </div>
              </div>
            </div>
            <div v-if="detail.messages.length === 0" class="muted">
              (这条 turn 没有 message —— 通常意味着 status≠ok。)
            </div>
          </div>

          <div class="debug">
            <button class="debug-toggle" @click="debugCollapsed = !debugCollapsed">
              <el-icon><component :is="debugCollapsed ? ArrowRight : ArrowDown" /></el-icon>
              <span>调试信息</span>
            </button>
            <div v-if="!debugCollapsed" class="debug-grid">
              <div><span class="lbl">latency</span><span class="val">first {{ fmtDuration(detail.latency_first_delta_ms) }} / total {{ fmtDuration(detail.total_latency_ms) }}</span></div>
              <div><span class="lbl">tokens</span><span class="val">in {{ detail.tokens_in }} / out {{ detail.tokens_out }}</span></div>
              <div><span class="lbl">cost</span><span class="val">{{ fmtCost(detail.cost_usd_micro) }}</span></div>
              <div><span class="lbl">model</span><span class="val mono">{{ detail.model || '—' }}</span></div>
              <div><span class="lbl">triage</span><span class="val mono">{{ detail.triage_kind || '—' }}</span></div>
              <div><span class="lbl">trigger</span><span class="val mono">{{ detail.trigger }}</span></div>
              <div><span class="lbl">device</span><span class="val mono">{{ detail.device_id || '—' }}</span></div>
              <div><span class="lbl">caller_kind</span><span class="val mono">{{ detail.caller_kind || '—' }}</span></div>
              <div><span class="lbl">trace_id</span><span class="val mono">{{ detail.trace_id || '—' }}</span></div>
              <div v-if="detail.error_code"><span class="lbl">error_code</span><span class="val mono">{{ detail.error_code }}</span></div>
              <div class="full"><span class="lbl">conversation</span><span class="val mono">{{ detail.conversation_id }}</span></div>
              <div class="full"><span class="lbl">agent_instance</span><span class="val mono">{{ detail.agent_instance_id }}</span></div>
              <div v-if="detail.metadata" class="full">
                <span class="lbl">metadata</span>
                <pre class="val">{{ JSON.stringify(detail.metadata, null, 2) }}</pre>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-head h2 { margin: 0; font-size: 18px; font-weight: 600; }
.hint { margin: 4px 0 0; font-size: 12px; color: var(--eid-text-muted); max-width: 580px; }
.head-actions { display: flex; gap: 8px; align-items: center; }
.split { display: grid; grid-template-columns: minmax(420px, 1fr) 1.4fr; gap: 16px; }
.left, .right { background: var(--eid-bg-panel); border: 1px solid var(--eid-border); border-radius: var(--eid-radius-sm); padding: 8px; min-height: 560px; }
.placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 8px; color: var(--eid-text-muted); }
.empty { padding: 32px; text-align: center; color: var(--eid-text-muted); font-size: 12px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); margin-top: 8px; }
.pager { display: flex; justify-content: center; padding: 12px; }
:deep(.is-selected) { background: var(--eid-bg-canvas) !important; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }

.detail-head { display: flex; justify-content: space-between; align-items: flex-start; padding: 8px 4px 12px; border-bottom: 1px solid var(--eid-border); }
.detail-head h3 { margin: 0; font-size: 14px; }
.meta { margin: 4px 0 0; font-size: 12px; color: var(--eid-text-muted); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.meta .mono { padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; }

.messages { display: flex; flex-direction: column; gap: 12px; padding: 12px 4px; max-height: 480px; overflow: auto; }
.msg { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); border: 1px solid var(--eid-border); }
.msg.role-user { border-left: 3px solid var(--eid-accent); }
.msg.role-assistant { border-left: 3px solid var(--eid-success, #67c23a); }
.msg.role-tool { border-left: 3px solid var(--eid-warning, #e6a23c); }
.msg-head { display: flex; gap: 8px; align-items: center; }
.msg-body { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.55; }
.msg-body pre { margin: 0; font-family: var(--eid-font-mono); font-size: 12px; white-space: pre-wrap; }
.tool-args { margin-top: 6px; font-size: 11px; }
.tool-args pre { background: var(--eid-bg-panel); padding: 6px 8px; border-radius: 3px; margin: 4px 0 0; }

.debug { margin-top: 12px; padding: 8px 4px 0; border-top: 1px solid var(--eid-border); }
.debug-toggle { display: flex; gap: 6px; align-items: center; background: transparent; border: 0; color: var(--eid-text-muted); font-size: 12px; cursor: pointer; padding: 4px 0; }
.debug-toggle:hover { color: var(--eid-text-primary); }
.debug-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; padding: 8px 4px; font-size: 12px; }
.debug-grid .full { grid-column: 1 / -1; }
.debug-grid .lbl { color: var(--eid-text-muted); min-width: 90px; display: inline-block; }
.debug-grid .val { font-family: var(--eid-font-mono); }
.debug-grid pre.val { margin: 4px 0 0; padding: 8px; background: var(--eid-bg-canvas); border-radius: 3px; max-height: 200px; overflow: auto; }
</style>
