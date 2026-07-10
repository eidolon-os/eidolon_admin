<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Delete, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteUnifiedBenchmarkRun,
  getUnifiedBenchmarkRun,
  listBenchmarkProjects,
  listUnifiedBenchmarkRuns,
  type BenchmarkProjectSummary,
  type BenchmarkRunDetail,
  type BenchmarkRunSummary,
  type BenchmarkStatus,
  type BenchmarkSuiteSummary,
} from '@/api/benchmarks'
import JsonViewer from '@/modules/common/JsonViewer.vue'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

type Dict = Record<string, unknown>

const route = useRoute()
const router = useRouter()

const projects = ref<BenchmarkProjectSummary[]>([])
const runs = ref<BenchmarkRunSummary[]>([])
const detail = ref<BenchmarkRunDetail | null>(null)
const selectedSuiteId = ref('')
const selectedRunId = ref('')
const loadingProjects = ref(false)
const loadingRuns = ref(false)
const loadingDetail = ref(false)
const deleting = ref(false)
const error = ref('')

const activeProjectId = computed(() => String(route.params.project || 'agent'))
const activeProject = computed(() =>
  projects.value.find((project) => project.id === activeProjectId.value) || null,
)
const activeSuite = computed(() =>
  activeProject.value?.suites.find((suite) => suite.id === selectedSuiteId.value) || null,
)
const activeSuiteDescription = computed(() => activeSuite.value?.description || suiteFallbackDescription(
  activeProjectId.value,
  selectedSuiteId.value,
))

const visibleCases = computed(() => (detail.value?.cases || []).slice(0, 200))
const metricRows = computed(() => buildMetricRows(detail.value))
const summaryRows = computed(() => keyValueRows(detail.value?.summary || {}))

onMounted(() => {
  void refreshAll()
})

watch(
  () => route.params.project,
  () => {
    selectedSuiteId.value = ''
    selectedRunId.value = ''
    detail.value = null
    void loadRunsForRoute()
  },
)

async function refreshAll() {
  loadingProjects.value = true
  error.value = ''
  try {
    projects.value = await listBenchmarkProjects()
    ensureSelectedSuite()
    await loadRunsForRoute()
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    loadingProjects.value = false
  }
}

async function loadRunsForRoute() {
  ensureSelectedSuite()
  if (!activeProject.value || !selectedSuiteId.value) {
    runs.value = []
    detail.value = null
    selectedRunId.value = ''
    return
  }
  loadingRuns.value = true
  error.value = ''
  try {
    runs.value = await listUnifiedBenchmarkRuns({
      project: activeProjectId.value,
      suite: selectedSuiteId.value,
    })
    const next = runs.value.find((run) => run.run_id === selectedRunId.value) || runs.value[0]
    if (next) {
      await selectRun(next)
    } else {
      selectedRunId.value = ''
      detail.value = null
    }
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    loadingRuns.value = false
  }
}

async function selectProject(projectId: string) {
  if (projectId === activeProjectId.value) return
  await router.push({ name: 'benchmarks', params: { project: projectId } })
}

async function selectSuite(suite: BenchmarkSuiteSummary) {
  if (suite.id === selectedSuiteId.value) return
  selectedSuiteId.value = suite.id
  selectedRunId.value = ''
  detail.value = null
  await router.replace({
    name: 'benchmarks',
    params: { project: activeProjectId.value },
    query: { suite: suite.id },
  })
  await loadRunsForRoute()
}

async function selectRun(run: BenchmarkRunSummary) {
  selectedRunId.value = run.run_id
  loadingDetail.value = true
  error.value = ''
  try {
    detail.value = await getUnifiedBenchmarkRun(run.project, run.suite, run.run_id)
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    loadingDetail.value = false
  }
}

async function deleteRun(run: BenchmarkRunSummary) {
  if (!run.deletable) return
  try {
    await ElMessageBox.confirm(
      `确认删除 ${run.project}/${run.suite}/${run.run_id}？产物会移入 .trash。`,
      '删除 Benchmark Run',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    const response = await deleteUnifiedBenchmarkRun(run.project, run.suite, run.run_id)
    ElMessage.success(`已移入 trash: ${response.trashed_path}`)
    selectedRunId.value = ''
    detail.value = null
    await refreshAll()
  } catch (err: unknown) {
    ElMessage.error(`删除失败: ${extractErrorMessage(err)}`)
  } finally {
    deleting.value = false
  }
}

function ensureSelectedSuite() {
  const project = activeProject.value
  if (!project) return
  const querySuite = typeof route.query.suite === 'string' ? route.query.suite : ''
  if (querySuite && project.suites.some((suite) => suite.id === querySuite)) {
    selectedSuiteId.value = querySuite
    return
  }
  if (!selectedSuiteId.value || !project.suites.some((suite) => suite.id === selectedSuiteId.value)) {
    selectedSuiteId.value = project.suites[0]?.id || ''
  }
}

function statusType(status: BenchmarkStatus | boolean | null | undefined): 'success' | 'danger' | 'info' {
  if (status === 'passed' || status === true) return 'success'
  if (status === 'failed' || status === false) return 'danger'
  return 'info'
}

function statusText(status: BenchmarkStatus | boolean | null | undefined) {
  if (status === 'passed' || status === true) return 'pass'
  if (status === 'failed' || status === false) return 'fail'
  return 'unknown'
}

function fmt(value: unknown, suffix = '') {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'number') {
    const text = Number.isInteger(value) ? String(value) : value.toFixed(1)
    return `${text}${suffix}`
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function sizeText(value: number | null) {
  if (value === null) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function keyValueRows(source: Dict) {
  return Object.entries(source)
    .filter(([, value]) => value === null || typeof value !== 'object')
    .map(([key, value]) => ({ key, value }))
}

function buildMetricRows(run: BenchmarkRunDetail | null) {
  if (!run) return []
  const rows: Array<{ key: string; count?: unknown; p50?: unknown; p95?: unknown; max?: unknown; value?: unknown }> = []
  collectMetricRows(run.summary?.metrics, 'summary.metrics', rows)
  collectMetricRows(run.metrics, 'metrics', rows)
  return rows.slice(0, 80)
}

function collectMetricRows(
  value: unknown,
  prefix: string,
  rows: Array<{ key: string; count?: unknown; p50?: unknown; p95?: unknown; max?: unknown; value?: unknown }>,
) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return
  const obj = value as Dict
  const hasStats = ['count', 'p50', 'p95', 'max'].some((key) => key in obj)
  if (hasStats) {
    rows.push({
      key: prefix,
      count: obj.count,
      p50: obj.p50,
      p95: obj.p95,
      max: obj.max,
    })
    return
  }
  for (const [key, child] of Object.entries(obj)) {
    if (child && typeof child === 'object' && !Array.isArray(child)) {
      collectMetricRows(child, `${prefix}.${key}`, rows)
    } else if (typeof child === 'number' || typeof child === 'string' || typeof child === 'boolean') {
      rows.push({ key: `${prefix}.${key}`, value: child })
    }
  }
}

function artifactTag(kind: string): 'success' | 'warning' | 'info' {
  if (kind === 'html' || kind === 'markdown') return 'success'
  if (kind === 'log') return 'warning'
  return 'info'
}

function suiteFallbackDescription(project: string, suite: string) {
  const key = `${project}/${suite}`
  const descriptions: Record<string, string> = {
    'agent/realtime': '实时语音 Agent 端到端 benchmark，关注首包、转写、回复和工具调用链路。',
    'agent/replay': '离线回放 benchmark，用固定样本复现 Agent 行为并检查回归。',
    'agent/persona_memory': 'Companion、Persona Genome 与 Memory 证据链 benchmark，覆盖隔离、语义实现、演化治理和快照性能。',
    'channel/voice': '语音通道 benchmark，关注房间、音频流、runner 和报告产物。',
    'memory/memory_perf': 'Memory 服务链路 benchmark：读召回、NATS 写入、memory-agent 落库和 MCP 可见性。',
    'memory/memory_quality': 'Memory 质量 benchmark：召回命中率、隔离、泄漏和语义质量。',
    'memory/memory_readable': '人工可读的 Memory benchmark 结论报告，汇总最终可信 run、历史异常和产品判断。',
    'admin/smoke': 'Admin 基础 smoke benchmark，用于确认管理后台核心页面和 API 可用。',
    'hub/smoke': 'Hub 基础 smoke benchmark，用于确认服务入口和关键 API 可用。',
    'client-web/web': 'Client Web benchmark，用于检查前端页面、构建和浏览器交互。',
  }
  return descriptions[key] || ''
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function renderMarkdown(value: string | null) {
  if (!value) return ''
  const lines = value.split(/\r?\n/)
  const html: string[] = []
  let inCode = false
  let tableRows: string[][] = []

  const flushTable = () => {
    if (!tableRows.length) return
    const [head, ...body] = tableRows
    html.push('<table><thead><tr>')
    for (const cell of head) html.push(`<th>${inlineMarkdown(cell)}</th>`)
    html.push('</tr></thead><tbody>')
    for (const row of body) {
      html.push('<tr>')
      for (const cell of row) html.push(`<td>${inlineMarkdown(cell)}</td>`)
      html.push('</tr>')
    }
    html.push('</tbody></table>')
    tableRows = []
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    if (line.startsWith('```')) {
      flushTable()
      html.push(inCode ? '</code></pre>' : '<pre><code>')
      inCode = !inCode
      continue
    }
    if (inCode) {
      html.push(`${escapeHtml(rawLine)}\n`)
      continue
    }
    if (/^\|.*\|$/.test(line)) {
      const cells = line
        .split('|')
        .slice(1, -1)
        .map((cell) => cell.trim())
      if (cells.every((cell) => /^:?-{3,}:?$/.test(cell))) continue
      tableRows.push(cells)
      continue
    }
    flushTable()
    if (line.startsWith('### ')) html.push(`<h5>${inlineMarkdown(line.slice(4))}</h5>`)
    else if (line.startsWith('## ')) html.push(`<h4>${inlineMarkdown(line.slice(3))}</h4>`)
    else if (line.startsWith('# ')) html.push(`<h3>${inlineMarkdown(line.slice(2))}</h3>`)
    else if (line.startsWith('- ')) html.push(`<p class="bullet">• ${inlineMarkdown(line.slice(2))}</p>`)
    else if (!line.trim()) html.push('')
    else html.push(`<p>${inlineMarkdown(line)}</p>`)
  }
  flushTable()
  if (inCode) html.push('</code></pre>')
  return html.join('')
}

function inlineMarkdown(value: string) {
  const escaped = escapeHtml(value)
  return escaped
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}
</script>

<template>
  <div class="eid-page benchmark-page">
    <header class="eid-page-head">
      <div>
        <h2>Benchmark Center</h2>
        <p class="eid-page-hint">统一查看各子项目 benchmark artifacts。</p>
      </div>
      <div class="eid-head-actions">
        <el-button :icon="Refresh" :loading="loadingProjects || loadingRuns" size="small" @click="refreshAll">
          刷新
        </el-button>
      </div>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon class="inline-alert">
      <template #title>{{ error }}</template>
    </el-alert>

    <div class="benchmark-grid">
      <aside class="project-panel eid-panel eid-panel-scroll" v-loading="loadingProjects">
        <button
          v-for="project in projects"
          :key="project.id"
          class="project-button"
          :class="{ active: project.id === activeProjectId }"
          @click="selectProject(project.id)"
        >
          <span class="project-main">
            <strong>{{ project.label }}</strong>
            <el-tag :type="statusType(project.latest_status)" size="small" effect="plain">
              {{ statusText(project.latest_status) }}
            </el-tag>
          </span>
          <span class="project-meta">{{ project.run_count }} runs</span>
        </button>
      </aside>

      <section class="runs-panel eid-panel eid-panel-scroll">
        <div class="panel-head">
          <div>
            <h3>{{ activeProject?.label || activeProjectId }}</h3>
            <p class="muted">{{ activeSuite?.label || selectedSuiteId || 'no suite' }}</p>
          </div>
        </div>

        <div v-if="activeProject?.suites.length" class="suite-tabs">
          <button
            v-for="suite in activeProject.suites"
            :key="suite.id"
            class="suite-button"
            :class="{ active: suite.id === selectedSuiteId }"
            @click="selectSuite(suite)"
          >
            <span>{{ suite.label }}</span>
            <small>{{ suite.run_count }}</small>
          </button>
        </div>
        <div v-if="activeSuiteDescription" class="suite-description">
          {{ activeSuiteDescription }}
        </div>

        <div v-if="!loadingRuns && runs.length === 0" class="empty-state">
          <span>没有 benchmark run</span>
          <code>{{ activeProjectId }}/{{ selectedSuiteId || '-' }}</code>
        </div>

        <div v-else class="run-list" v-loading="loadingRuns">
          <button
            v-for="run in runs"
            :key="`${run.project}/${run.suite}/${run.run_id}`"
            class="run-row"
            :class="{ active: run.run_id === selectedRunId }"
            @click="selectRun(run)"
          >
            <span class="row-top">
              <el-tag :type="statusType(run.status)" size="small" effect="dark">
                {{ statusText(run.status) }}
              </el-tag>
              <span class="mono run-id">{{ run.title || run.run_id }}</span>
            </span>
            <span class="row-meta">
              <span>{{ formatTimestamp(run.generated_at || run.modified_at) }}</span>
              <span v-if="run.git_sha" class="mono">sha {{ run.git_sha }}</span>
              <span>{{ fmt(run.summary.total) }} total</span>
            </span>
          </button>
        </div>
      </section>

      <main class="detail-panel eid-panel eid-panel-scroll">
        <div v-if="loadingDetail" class="placeholder">加载中...</div>
        <div v-else-if="!detail" class="placeholder">选择一个 benchmark run</div>
        <template v-else>
          <div class="detail-head">
            <div>
              <h3>{{ detail.title }}</h3>
              <p class="muted">
                <el-tag :type="statusType(detail.status)" size="small" effect="dark">
                  {{ statusText(detail.status) }}
                </el-tag>
                <span class="mono">{{ detail.project }}/{{ detail.suite }}/{{ detail.run_id }}</span>
              </p>
            </div>
            <el-button
              v-if="detail.deletable"
              :icon="Delete"
              :loading="deleting"
              size="small"
              type="danger"
              plain
              @click="deleteRun(detail)"
            >
              删除
            </el-button>
            <el-tooltip v-else-if="detail.delete_hint" :content="detail.delete_hint" placement="left">
              <el-button size="small" disabled>删除</el-button>
            </el-tooltip>
          </div>

          <section class="summary-cards">
            <div class="metric-card" :class="{ danger: detail.status === 'failed' }">
              <span>Status</span>
              <strong>{{ statusText(detail.status) }}</strong>
              <small>{{ formatTimestamp(detail.generated_at || detail.modified_at) }}</small>
            </div>
            <div class="metric-card">
              <span>Passed</span>
              <strong>{{ fmt(detail.summary.passed) }}/{{ fmt(detail.summary.total) }}</strong>
              <small>{{ fmt(detail.summary.failed) }} failed</small>
            </div>
            <div class="metric-card">
              <span>Artifacts</span>
              <strong>{{ detail.artifacts.length }}</strong>
              <small>{{ detail.deletable ? 'deletable run' : 'read-only' }}</small>
            </div>
            <div class="metric-card">
              <span>Git</span>
              <strong class="mono small-strong">{{ detail.git_sha || '-' }}</strong>
              <small>{{ detail.suite_label }}</small>
            </div>
          </section>

          <section v-if="detail.markdown" class="content-block report-block">
            <h4>Readable Report</h4>
            <article class="markdown-body" v-html="renderMarkdown(detail.markdown)" />
          </section>

          <section class="content-block">
            <h4>Summary</h4>
            <el-table :data="summaryRows" size="small" border>
              <el-table-column prop="key" label="Key" min-width="180" />
              <el-table-column label="Value" min-width="180">
                <template #default="{ row }">{{ fmt(row.value) }}</template>
              </el-table-column>
            </el-table>
          </section>

          <section class="content-block">
            <h4>Metrics</h4>
            <div v-if="metricRows.length === 0" class="muted">No scalar metrics.</div>
            <el-table v-else :data="metricRows" size="small" border>
              <el-table-column prop="key" label="Metric" min-width="260" />
              <el-table-column label="P50" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.p50) }}</template>
              </el-table-column>
              <el-table-column label="P95" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.p95) }}</template>
              </el-table-column>
              <el-table-column label="Max" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.max) }}</template>
              </el-table-column>
              <el-table-column label="Count" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.count) }}</template>
              </el-table-column>
              <el-table-column label="Value" width="140" align="right">
                <template #default="{ row }">{{ fmt(row.value) }}</template>
              </el-table-column>
            </el-table>
          </section>

          <section class="content-block">
            <h4>Artifacts</h4>
            <el-table :data="detail.artifacts" size="small" border>
              <el-table-column label="Kind" width="110">
                <template #default="{ row }">
                  <el-tag :type="artifactTag(row.kind)" size="small">{{ row.kind }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="Name" min-width="180" />
              <el-table-column label="Size" width="110" align="right">
                <template #default="{ row }">{{ sizeText(row.size) }}</template>
              </el-table-column>
              <el-table-column prop="path" label="Path" min-width="260" class-name="mono" />
            </el-table>
          </section>

          <section class="content-block">
            <h4>Cases</h4>
            <div v-if="visibleCases.length === 0" class="muted">No cases.</div>
            <el-table v-else :data="visibleCases" size="small" border>
              <el-table-column label="Case" min-width="220">
                <template #default="{ row }">{{ row.case_id || row.scenario_id || row.id || '-' }}</template>
              </el-table-column>
              <el-table-column label="Status" width="100">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.passed as boolean | null)" size="small">
                    {{ statusText(row.passed as boolean | null) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Suite" min-width="160">
                <template #default="{ row }">{{ row.suite || row.category || '-' }}</template>
              </el-table-column>
              <el-table-column label="Errors" min-width="260">
                <template #default="{ row }">{{ Array.isArray(row.errors) ? row.errors.join('; ') : '-' }}</template>
              </el-table-column>
            </el-table>
          </section>

          <section class="content-block">
            <h4>Raw Payload</h4>
            <JsonViewer :data="detail.payload" max-height="360px" />
          </section>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.benchmark-page {
  min-height: 0;
}

.inline-alert {
  margin-bottom: 12px;
}

.benchmark-grid {
  display: grid;
  grid-template-columns: 230px 360px minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
}

.project-panel,
.runs-panel,
.detail-panel {
  min-height: 620px;
  max-height: calc(100vh - 150px);
}

.project-panel {
  padding: 8px;
}

.project-button,
.suite-button,
.run-row {
  width: 100%;
  border: 1px solid transparent;
  background: transparent;
  color: var(--eid-text);
  cursor: pointer;
  text-align: left;
}

.project-button {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  border-radius: var(--eid-radius);
}

.project-button:hover,
.suite-button:hover,
.run-row:hover {
  background: var(--eid-bg-inset);
}

.project-button.active,
.suite-button.active,
.run-row.active {
  border-color: var(--eid-accent);
  background: color-mix(in srgb, var(--eid-accent-soft) 70%, transparent);
}

.project-main,
.row-top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.project-main strong,
.run-id {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.project-meta,
.row-meta,
.muted {
  color: var(--eid-text-muted);
  font-size: 12px;
}

.panel-head,
.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border-bottom: 1px solid var(--eid-border);
}

.panel-head h3,
.detail-head h3 {
  margin: 0 0 4px;
  font-size: 16px;
}

.suite-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px;
}

.suite-button {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: auto;
  min-width: 96px;
  padding: 7px 9px;
  border-radius: var(--eid-radius);
}

.suite-button small {
  color: var(--eid-text-muted);
}

.suite-description {
  margin: 0 12px 12px;
  padding: 10px 12px;
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  background: var(--eid-bg-inset);
  color: var(--eid-text-muted);
  font-size: 12px;
  line-height: 1.5;
}

.run-list {
  padding: 8px;
  border-top: 1px solid var(--eid-border);
}

.run-row {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 10px;
  margin-bottom: 8px;
  border-radius: var(--eid-radius);
}

.row-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.placeholder,
.empty-state {
  display: flex;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  color: var(--eid-text-muted);
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding: 14px;
}

.metric-card {
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  padding: 12px;
  background: var(--eid-bg-inset);
  min-width: 0;
}

.metric-card.danger {
  border-color: color-mix(in srgb, var(--eid-danger) 62%, var(--eid-border));
  background: color-mix(in srgb, var(--eid-danger-soft) 30%, var(--eid-bg-inset));
}

.metric-card span,
.metric-card small {
  display: block;
  color: var(--eid-text-muted);
  font-size: 12px;
}

.metric-card strong {
  display: block;
  margin: 6px 0;
  font-size: 24px;
  overflow-wrap: anywhere;
}

.metric-card .small-strong {
  font-size: 14px;
}

.content-block {
  padding: 14px;
  border-top: 1px solid var(--eid-border);
}

.content-block h4 {
  margin: 0 0 10px;
  font-size: 14px;
}

.report-block {
  background: color-mix(in srgb, var(--eid-bg-inset) 45%, transparent);
}

.markdown-body {
  color: var(--eid-text);
  line-height: 1.62;
  font-size: 14px;
}

.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5) {
  margin: 12px 0 6px;
}

.markdown-body :deep(p) {
  margin: 5px 0;
}

.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0 14px;
  overflow: hidden;
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  font-size: 13px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border-bottom: 1px solid var(--eid-border);
  padding: 7px 9px;
  text-align: left;
  vertical-align: top;
}

.markdown-body :deep(th) {
  background: var(--eid-bg-inset);
  color: var(--eid-text-muted);
  font-weight: 600;
}

.markdown-body :deep(code) {
  border: 1px solid var(--eid-border);
  border-radius: 4px;
  padding: 1px 4px;
  background: var(--eid-bg-inset);
  font-family: var(--eid-font-mono);
  font-size: 0.92em;
}

.markdown-body :deep(pre) {
  overflow: auto;
  margin: 10px 0;
  padding: 10px;
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  background: var(--eid-bg-inset);
}

.markdown-body :deep(pre code) {
  border: 0;
  padding: 0;
  background: transparent;
}

.mono {
  font-family: var(--eid-font-mono);
}

@media (max-width: 1180px) {
  .benchmark-grid {
    grid-template-columns: 210px minmax(0, 1fr);
  }

  .detail-panel {
    grid-column: 1 / -1;
    max-height: none;
  }
}

@media (max-width: 760px) {
  .benchmark-grid {
    grid-template-columns: 1fr;
  }

  .project-panel,
  .runs-panel,
  .detail-panel {
    min-height: auto;
    max-height: none;
  }

  .summary-cards {
    grid-template-columns: 1fr;
  }
}
</style>
