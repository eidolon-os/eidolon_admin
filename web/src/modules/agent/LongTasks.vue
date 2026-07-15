<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Tickets } from '@element-plus/icons-vue'
import {
  getLongTask,
  listLongTasks,
  type ListLongTasksParams,
  type LongTaskDetail,
  type LongTaskSummary,
} from '@/api/longTasks'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import AgentScopeSelector from './components/AgentScopeSelector.vue'
import { useOwnersStore } from '@/stores/owners'

const ownersStore = useOwnersStore()
const tasks = ref<LongTaskSummary[]>([])
const loading = ref(false)
const detail = ref<LongTaskDetail | null>(null)
const detailLoading = ref(false)
const selectedId = ref<string | null>(null)
const ownerId = ref(ownersStore.currentId)
const companionId = ref('')
const filterStatus = ref<string>('')
const cursor = ref<string | null>(null)
const hasMore = computed(() => cursor.value !== null)

let pollTimer: ReturnType<typeof setInterval> | null = null

const statusOptions = [
  'accepted',
  'queued',
  'running',
  'succeeded',
  'failed',
  'timed_out',
  'cancelled',
]

async function refresh() {
  loading.value = true
  cursor.value = null
  try {
    const params = buildParams()
    const r = await listLongTasks(params)
    tasks.value = r.tasks
    cursor.value = r.next_before
  } catch (e: any) {
    ElMessage.error(`加载长任务失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (!cursor.value) return
  loading.value = true
  try {
    const params = buildParams()
    params.before = cursor.value
    const r = await listLongTasks(params)
    tasks.value = [...tasks.value, ...r.tasks]
    cursor.value = r.next_before
  } catch (e: any) {
    ElMessage.error(`加载下一页失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function select(row: LongTaskSummary) {
  selectedId.value = row.task_id
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getLongTask(row.task_id)
  } catch (e: any) {
    ElMessage.error(`加载任务详情失败: ${extractErrorMessage(e)}`)
  } finally {
    detailLoading.value = false
  }
}

function clearSelection() {
  selectedId.value = null
  detail.value = null
}

function buildParams(): ListLongTasksParams {
  const params: ListLongTasksParams = { limit: 50 }
  if (ownerId.value) params.owner_id = ownerId.value
  if (companionId.value) params.companion_id = companionId.value
  if (filterStatus.value) params.status = filterStatus.value
  return params
}

watch([ownerId, companionId, filterStatus], () => {
  clearSelection()
  void refresh()
})

watch(() => ownersStore.currentId, (next) => {
  if (next && next !== ownerId.value) ownerId.value = next
})

onMounted(() => {
  void refresh()
  pollTimer = setInterval(() => {
    if (loading.value || detailLoading.value || selectedId.value) return
    void refresh()
  }, 10_000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})

function statusTagType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === 'succeeded') return 'success'
  if (s === 'failed' || s === 'timed_out' || s === 'cancelled') return 'danger'
  if (s === 'accepted' || s === 'queued' || s === 'running') return 'warning'
  return 'info'
}

function rowClass({ row }: { row: LongTaskSummary }): string {
  return row.task_id === selectedId.value ? 'is-selected' : ''
}

function shortId(id: string | null | undefined): string {
  if (!id) return '—'
  return id.length > 14 ? `${id.slice(0, 12)}…` : id
}

function durationLabel(row: LongTaskSummary): string {
  if (!row.completed_at || !row.created_at) return '—'
  const ms = new Date(row.completed_at).getTime() - new Date(row.created_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}

function jsonText(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <div class="page eid-page">
    <header class="page-head eid-page-head">
      <div>
        <h2>Long Tasks</h2>
        <p class="hint eid-page-hint">查看 owner / companion 维度的长任务，任务身份来自运行时 token。</p>
      </div>
      <div class="head-actions eid-head-actions">
        <AgentScopeSelector
          v-model:owner-id="ownerId"
          v-model:companion-id="companionId"
          allow-all-companions
        />
        <el-select
          v-model="filterStatus"
          clearable
          size="small"
          placeholder="status"
          style="width: 150px"
        >
          <el-option
            v-for="s in statusOptions"
            :key="s"
            :label="s"
            :value="s"
          />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" size="small" @click="refresh">
          刷新
        </el-button>
      </div>
    </header>

    <div class="split eid-split eid-split--wide-left">
      <div class="left eid-panel eid-panel-pad eid-panel-scroll">
        <el-table
          v-loading="loading && tasks.length === 0"
          :data="tasks"
          stripe
          highlight-current-row
          size="small"
          :row-class-name="rowClass"
          @row-click="(row: LongTaskSummary) => select(row)"
        >
          <el-table-column label="created" width="160">
            <template #default="{ row }">
              <span class="muted mono">{{ formatTimestamp(row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="owner" width="130">
            <template #default="{ row }">
              <span class="mono">{{ row.owner_id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="companion" width="140">
            <template #default="{ row }">
              <span class="mono">{{ row.companion_id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="status" width="110">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="type" width="120">
            <template #default="{ row }">
              <span class="muted mono">{{ row.task_type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="task" min-width="260">
            <template #default="{ row }">
              <div class="task-cell">
                <span>{{ row.task }}</span>
                <span class="muted mono">{{ shortId(row.task_id) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="worker" width="130">
            <template #default="{ row }">
              <span class="muted mono">{{ shortId(row.worker_id) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="duration" width="90">
            <template #default="{ row }">
              <span class="muted mono">{{ durationLabel(row) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="!loading && tasks.length === 0" class="empty">
          当前过滤条件下没有长任务。
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
          <span v-else-if="tasks.length > 0" class="muted">已加载全部</span>
        </div>
      </div>

      <div class="right eid-panel eid-panel-scroll">
        <div v-if="!detail && !detailLoading" class="placeholder">
          <el-icon><Tickets /></el-icon>
          <span>选择左侧任务查看详情</span>
        </div>
        <div v-else-if="detailLoading" class="placeholder">加载中…</div>
        <template v-else-if="detail">
          <div class="detail-head eid-detail-head">
            <div>
              <h3>{{ detail.owner_id }} · {{ detail.companion_id }} · {{ detail.task_type }}</h3>
              <p class="meta eid-meta-row">
                <code class="mono">{{ shortId(detail.task_id) }}</code>
                <span>•</span>
                <span>{{ formatTimestamp(detail.created_at) }}</span>
                <span>•</span>
                <el-tag :type="statusTagType(detail.status)" size="small" effect="dark">
                  {{ detail.status }}
                </el-tag>
              </p>
            </div>
            <el-button size="small" link @click="clearSelection">关闭</el-button>
          </div>

          <section class="detail-section">
            <h4>Request</h4>
            <p class="task-text">{{ detail.task }}</p>
            <div class="kv-grid">
              <div><span class="lbl">expected</span><span>{{ detail.expected_output || '—' }}</span></div>
              <div><span class="lbl">urgency</span><span class="mono">{{ detail.urgency }}</span></div>
              <div><span class="lbl">session_key</span><span class="mono">{{ detail.session_key }}</span></div>
              <div><span class="lbl">task_key</span><span class="mono">{{ detail.task_key }}</span></div>
              <div><span class="lbl">turn_id</span><span class="mono">{{ detail.turn_id }}</span></div>
              <div><span class="lbl">trace_id</span><span class="mono">{{ detail.trace_id || '—' }}</span></div>
              <div><span class="lbl">memory realm</span><span class="mono">{{ detail.memory_realm_id || '—' }}</span></div>
              <div><span class="lbl">genome</span><span class="mono">{{ detail.genome_id || '—' }}</span></div>
            </div>
          </section>

          <section class="detail-section">
            <h4>Mementos</h4>
            <div class="kv-grid">
              <div><span class="lbl">session</span><span class="mono">{{ detail.mementos_session_id || '—' }}</span></div>
              <div><span class="lbl">conversation</span><span class="mono">{{ detail.mementos_conversation_id || '—' }}</span></div>
              <div><span class="lbl">run</span><span class="mono">{{ detail.mementos_run_id || '—' }}</span></div>
              <div><span class="lbl">latest_seq</span><span class="mono">{{ detail.mementos_latest_seq ?? '—' }}</span></div>
              <div><span class="lbl">external</span><span class="mono">{{ detail.external_status || '—' }}</span></div>
              <div><span class="lbl">workspace</span><span class="mono">{{ detail.mementos_workspace_dir || '—' }}</span></div>
            </div>
          </section>

          <section v-if="detail.progress_summary || detail.result_text || detail.error_message" class="detail-section">
            <h4>Outcome</h4>
            <p v-if="detail.progress_summary" class="outcome">{{ detail.progress_summary }}</p>
            <p v-if="detail.result_text" class="outcome">{{ detail.result_text }}</p>
            <p v-if="detail.error_message" class="outcome error">{{ detail.error_code }} · {{ detail.error_message }}</p>
          </section>

          <section class="detail-section">
            <h4>Timing</h4>
            <div class="kv-grid">
              <div><span class="lbl">submitted</span><span>{{ formatTimestamp(detail.submitted_at) }}</span></div>
              <div><span class="lbl">started</span><span>{{ formatTimestamp(detail.started_at) }}</span></div>
              <div><span class="lbl">progress</span><span>{{ formatTimestamp(detail.last_progress_at) }}</span></div>
              <div><span class="lbl">polled</span><span>{{ formatTimestamp(detail.last_polled_at) }}</span></div>
              <div><span class="lbl">completed</span><span>{{ formatTimestamp(detail.completed_at) }}</span></div>
              <div><span class="lbl">lease</span><span>{{ formatTimestamp(detail.lease_until) }}</span></div>
            </div>
          </section>

          <el-collapse class="json-blocks">
            <el-collapse-item title="request_payload" name="request">
              <pre>{{ jsonText(detail.request_payload) }}</pre>
            </el-collapse-item>
            <el-collapse-item title="progress_events" name="progress">
              <pre>{{ jsonText(detail.progress_events) }}</pre>
            </el-collapse-item>
            <el-collapse-item title="result_payload" name="result">
              <pre>{{ jsonText(detail.result_payload) }}</pre>
            </el-collapse-item>
            <el-collapse-item title="error_payload" name="error">
              <pre>{{ jsonText(detail.error_payload) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { min-height: 0; }
.page-head { margin-bottom: 0; }
.hint { max-width: 560px; }
.placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 8px; color: var(--eid-text-muted); }
.empty { padding: 32px; text-align: center; color: var(--eid-text-muted); font-size: 12px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); margin-top: 8px; }
.pager { display: flex; justify-content: center; padding: 12px; }
:deep(.is-selected) { background: var(--eid-bg-canvas) !important; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.task-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.task-cell span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.detail-head { position: sticky; top: 0; z-index: 1; }
.meta .mono { padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; }
.detail-section { padding: 12px; border-bottom: 1px solid var(--eid-border); }
.detail-section h4 { margin: 0 0 8px; font-size: 12px; color: var(--eid-text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.task-text, .outcome { margin: 0 0 10px; font-size: 13px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.outcome.error { color: var(--eid-danger, #f56c6c); }
.kv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px 14px; font-size: 12px; }
.kv-grid div { min-width: 0; display: flex; gap: 8px; align-items: baseline; }
.kv-grid .lbl { color: var(--eid-text-muted); flex: 0 0 92px; }
.kv-grid span:last-child { min-width: 0; overflow-wrap: anywhere; word-break: break-word; }
.json-blocks { margin-top: 8px; border-top: 0; }
.json-blocks pre { margin: 0; padding: 10px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); max-height: 260px; overflow: auto; font-family: var(--eid-font-mono); font-size: 12px; line-height: 1.45; }
</style>
