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
  listMemoryAudit,
  listTurns,
  type ListTurnsParams,
  type MemoryAuditRow,
  type TurnDetail,
  type TurnSummary,
} from '@/api/conversations'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import RegisteredUserPicker from '@/modules/common/RegisteredUserPicker.vue'

const turns = ref<TurnSummary[]>([])
const loading = ref(false)
const detail = ref<TurnDetail | null>(null)
const detailLoading = ref(false)
const auditRows = ref<MemoryAuditRow[]>([])
const auditLoading = ref(false)
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
    void refreshMemoryAudit()
  } catch (e: any) {
    ElMessage.error(`加载对话记录失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function refreshMemoryAudit() {
  auditLoading.value = true
  try {
    const params: ListTurnsParams = { limit: 50 }
    if (filterUserId.value) params.user_id = filterUserId.value
    const r = await listMemoryAudit(params)
    auditRows.value = r.rows
  } catch (e: any) {
    ElMessage.error(`加载 memory audit 失败: ${extractErrorMessage(e)}`)
  } finally {
    auditLoading.value = false
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

async function selectAudit(row: MemoryAuditRow) {
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

function memoryWriteLabel(row: TurnSummary): string {
  const write = row.observability_summary?.memory_write
  if (!write?.disposition) return '—'
  if (!write.fanout_allowed) return `${write.disposition} / ${write.skipped_reason || 'blocked'}`
  return write.disposition
}

function memoryWriteTagType(row: TurnSummary): 'success' | 'warning' | 'danger' | 'info' {
  const write = row.observability_summary?.memory_write
  if (!write?.disposition) return 'info'
  if (!write.fanout_allowed) return 'warning'
  if (write.disposition === 'ignore') return 'info'
  if (write.disposition === 'sensitive_requires_consent') return 'danger'
  return 'success'
}

function contextLabel(row: TurnSummary): string {
  const ctx = row.observability_summary?.context
  if (!ctx) return '—'
  const dropped = ctx.dropped_count ? ` drop ${ctx.dropped_count}` : ''
  const degraded = ctx.degraded_sources.length ? ` deg ${ctx.degraded_sources.join(',')}` : ''
  return `${ctx.segment_kinds.join('>') || 'empty'}${dropped}${degraded}`
}

function guardLabel(row: TurnSummary): string {
  const guards = row.observability_summary?.development_guards
  if (!guards) return '—'
  const context = guards.context_budget
  const memory = guards.memory_write_policy
  const tool = guards.tool_policy
  return [
    `ctx:${context.mode || '—'}${context.applied ? '' : '/shadow'}`,
    `mem:${memory.mode || '—'}${memory.fanout_allowed ? '' : '/blocked'}`,
    `tool:${tool.schema_strict ? 'strict' : 'compat'}`,
  ].join(' ')
}

function auditTagType(row: MemoryAuditRow): 'success' | 'warning' | 'danger' | 'info' {
  if (!row.fanout_allowed) return row.skipped_reason === 'requires_consent' ? 'danger' : 'warning'
  if (row.disposition === 'ignore') return 'info'
  return 'success'
}
</script>

<template>
  <div class="page conversations-page">
    <header class="page-head eid-page-head">
      <div>
        <h2>对话记录</h2>
        <p class="hint eid-page-hint">
          只读视图：浏览 agent SQLite 里的 turn 日志。按 user 过滤；
          点击一行展开看消息正文 + 调试信息（latency / tokens / trace）。
        </p>
      </div>
      <div class="head-actions eid-head-actions">
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

    <div class="split eid-split">
      <div class="left eid-panel eid-panel-pad eid-panel-scroll">
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
          <el-table-column label="memory" width="170">
            <template #default="{ row }">
              <el-tag :type="memoryWriteTagType(row)" size="small">
                {{ memoryWriteLabel(row) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="context" min-width="180">
            <template #default="{ row }">
              <span class="muted mono">{{ contextLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="guards" min-width="190">
            <template #default="{ row }">
              <span class="muted mono">{{ guardLabel(row) }}</span>
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

      <div class="right eid-panel eid-panel-scroll">
        <div v-if="!detail && !detailLoading" class="placeholder">
          <el-icon><ChatLineRound /></el-icon>
          <span>选择左侧一行查看详情</span>
        </div>
        <div v-else-if="detailLoading" class="placeholder">加载中…</div>
        <template v-else-if="detail">
          <div class="detail-head eid-detail-head">
            <div>
              <h3>{{ detail.user_id }} · turn {{ detail.seq }}</h3>
              <p class="meta eid-meta-row">
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

          <div v-if="detail.observability_summary" class="observability">
            <div class="obs-item">
              <span class="lbl">privacy</span>
              <span class="val mono">{{ detail.observability_summary.privacy_mode || '—' }}</span>
            </div>
            <div class="obs-item">
              <span class="lbl">context</span>
              <span class="val mono">
                {{ detail.observability_summary.context.segment_kinds.join(' > ') || '—' }}
                <template v-if="detail.observability_summary.context.dropped_count">
                  · dropped {{ detail.observability_summary.context.dropped_count }}
                </template>
              </span>
            </div>
            <div class="obs-item">
              <span class="lbl">memory recall</span>
              <span class="val mono">
                hits {{ detail.observability_summary.memory.hit_count }}
                <template v-if="detail.observability_summary.memory.degraded"> · degraded</template>
              </span>
            </div>
            <div class="obs-item">
              <span class="lbl">memory write</span>
              <span class="val mono">
                {{ detail.observability_summary.memory_write.disposition || '—' }}
                <template v-if="!detail.observability_summary.memory_write.fanout_allowed">
                  · {{ detail.observability_summary.memory_write.skipped_reason || 'blocked' }}
                </template>
              </span>
            </div>
            <div class="obs-item">
              <span class="lbl">tools</span>
              <span class="val mono">
                {{ detail.observability_summary.tools.names.join(', ') || 'none' }}
                <template v-if="detail.observability_summary.tools.error_count">
                  · errors {{ detail.observability_summary.tools.error_count }}
                </template>
              </span>
            </div>
            <div class="obs-item">
              <span class="lbl">fingerprint</span>
              <span class="val mono">{{ detail.observability_summary.prompt_fingerprint }}</span>
            </div>
            <div class="obs-item full">
              <span class="lbl">guards</span>
              <span class="val mono">
                context {{ detail.observability_summary.development_guards.context_budget.mode || '—' }}
                <template v-if="detail.observability_summary.development_guards.context_budget.shadow_dropped_count">
                  · shadow drops {{ detail.observability_summary.development_guards.context_budget.shadow_dropped_count }}
                </template>
                · memory {{ detail.observability_summary.development_guards.memory_write_policy.mode || '—' }}
                <template v-if="!detail.observability_summary.development_guards.memory_write_policy.fanout_allowed">
                  / {{ detail.observability_summary.development_guards.memory_write_policy.skipped_reason || 'blocked' }}
                </template>
                · tools {{ detail.observability_summary.development_guards.tool_policy.schema_strict ? 'strict' : 'compat' }}
                / max {{ detail.observability_summary.development_guards.tool_policy.max_tool_iters || '—' }}
              </span>
            </div>
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

    <section class="audit eid-panel eid-panel-pad">
      <div class="audit-head">
        <div>
          <h3>Memory audit</h3>
          <p class="muted">
            本地 turn trace 中的 memory 写入候选，只展示 disposition / reason，不展示消息正文。
          </p>
        </div>
        <el-button :icon="Refresh" :loading="auditLoading" size="small" @click="refreshMemoryAudit">
          刷新
        </el-button>
      </div>
      <el-table
        v-loading="auditLoading"
        :data="auditRows"
        size="small"
        stripe
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
        <el-table-column label="disposition" width="190">
          <template #default="{ row }">
            <el-tag :type="auditTagType(row)" size="small">
              {{ row.disposition || '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="reason" min-width="220">
          <template #default="{ row }">
            <span class="muted mono">{{ row.reason || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="fanout" width="130">
          <template #default="{ row }">
            <span class="muted mono">
              {{ row.fanout_allowed ? 'allowed' : (row.skipped_reason || 'blocked') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="privacy" width="100">
          <template #default="{ row }">
            <span class="muted mono">{{ row.privacy_mode || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="turn" width="120">
          <template #default="{ row }">
            <button class="link-button" @click="selectAudit(row)">
              {{ row.turn_id.slice(0, 10) }}
            </button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!auditLoading && auditRows.length === 0" class="empty audit-empty">
        当前过滤条件下没有 memory 写入候选。
      </div>
    </section>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; min-height: 0; }
.conversations-page { height: 100%; overflow: hidden; }
.page-head { margin-bottom: 0; }
.hint { max-width: 620px; }
.split { min-height: 0; }
.left, .right { min-height: 0; }
.placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 8px; color: var(--eid-text-muted); }
.empty { padding: 32px; text-align: center; color: var(--eid-text-muted); font-size: 12px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); margin-top: 8px; }
.pager { display: flex; justify-content: center; padding: 12px; }
:deep(.is-selected) { background: var(--eid-bg-canvas) !important; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }

.detail-head { position: sticky; top: 0; z-index: 1; }
.meta .mono { padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; }

.observability { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; padding: 10px 4px; border-bottom: 1px solid var(--eid-border); font-size: 12px; }
.obs-item { min-width: 0; display: flex; gap: 8px; align-items: baseline; }
.obs-item.full { grid-column: 1 / -1; }
.obs-item .lbl { color: var(--eid-text-muted); min-width: 92px; flex: 0 0 auto; }
.obs-item .val { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.messages { display: flex; flex-direction: column; gap: 12px; padding: 12px; }
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
.audit { flex: 0 0 230px; margin-top: 0; overflow: auto; min-height: 0; }
.audit-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 8px; }
.audit-head h3 { margin: 0; font-size: 14px; }
.audit-head p { margin: 4px 0 0; }
.audit-empty { margin-top: 8px; }
.link-button { padding: 0; border: 0; background: transparent; color: var(--eid-accent); font-family: var(--eid-font-mono); font-size: 12px; cursor: pointer; }
.link-button:hover { text-decoration: underline; }
@media (max-height: 760px) {
  .audit { flex-basis: 190px; }
}
</style>
