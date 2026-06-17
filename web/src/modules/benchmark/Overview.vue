<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import {
  getReport,
  listReports,
  type ReportDetail,
  type ReportSummary,
} from '@/api/reports'
import JsonViewer from '@/modules/common/JsonViewer.vue'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

type Dict = Record<string, any>

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const detailLoading = ref(false)
const error = ref('')
const reports = ref<ReportSummary[]>([])
const selectedFilename = ref('')
const selected = ref<ReportDetail | null>(null)

const preferredReports = [
  'latest-live-grpc.json',
  'latest-live-service.json',
  'latest.json',
]

const projects = [
  {
    id: 'agent',
    label: 'Agent',
    hint: 'Realtime benchmark',
    route: { name: 'benchmarks', params: { project: 'agent' } },
  },
]

const activeProject = computed(() => String(route.params.project || 'agent'))
const payload = computed<Dict>(() => selected.value?.payload || {})
const payloadSummary = computed<Dict>(() => asObject(payload.value.summary || selected.value?.summary.summary))
const metrics = computed<Dict>(() => asObject(payload.value.metrics || selected.value?.summary.metrics))
const llmSummary = computed<Dict>(() => asObject(payload.value.llm_summary))
const diagnostic = computed<Dict>(() => asObject(llmSummary.value.diagnostic))
const thresholdChecks = computed<Dict[]>(() => asArray(payload.value.threshold_checks))
const rawFailedChecks = computed<Dict[]>(() => asArray(payload.value.failed_checks))
const dimensions = computed<Dict[]>(() => asArray(payload.value.dimensions))
const scenarios = computed<Dict[]>(() => asArray(payload.value.scenarios))
const allTurns = computed<Dict[]>(() => asArray(payload.value.turns))
const visibleTurns = computed<Dict[]>(() => allTurns.value.slice(0, 160))
const baseline = computed<Dict>(() => asObject(payload.value.baseline))
const chartHints = computed<Dict[]>(() => asArray(asObject(payload.value.visualization).recommended_charts))

const orderedReports = computed(() => {
  const score = (report: ReportSummary) => {
    const preferred = preferredReports.indexOf(report.filename)
    if (preferred >= 0) return preferred
    if (report.filename.startsWith('benchmark-')) return 10
    if (report.filename.startsWith('latest-')) return 20
    return 30
  }
  return [...reports.value].sort((a, b) => {
    const diff = score(a) - score(b)
    if (diff !== 0) return diff
    return String(b.modified_at).localeCompare(String(a.modified_at))
  })
})

const selectedReport = computed(() =>
  reports.value.find((report) => report.filename === selectedFilename.value) || null,
)

const metricCards = computed(() =>
  ['first_delta_ms', 'total_ms'].map((name) => ({
    name,
    label: name === 'first_delta_ms' ? 'First delta' : 'Total',
    stats: asObject(metrics.value[name]),
  })),
)

const metricRows = computed(() =>
  metricCards.value.flatMap((metric) =>
    ['p50', 'p90', 'p95', 'p99', 'max', 'mean'].map((stat) => ({
      metric: metric.name,
      stat,
      value: metric.stats[stat],
      max: metric.stats.max,
    })),
  ),
)

const thresholdFailures = computed(() => {
  const structured = asArray(diagnostic.value.threshold_failures)
  return structured.length ? structured : thresholdChecks.value.filter((check) => check.passed === false)
})

const failedChecks = computed(() => {
  const structured = asArray(diagnostic.value.failed_checks)
  return structured.length ? structured : rawFailedChecks.value
})

const slowTurns = computed(() => asArray(diagnostic.value.slow_turns))

const diagnosticDimensions = computed(() => {
  const structured = asArray(diagnostic.value.dimensions)
  return structured.length ? structured : dimensions.value
})

const baselineRegressions = computed(() => {
  const structured = asArray(asObject(diagnostic.value.baseline).regressions)
  if (structured.length) return structured
  const regressions = asArray(baseline.value.regressions)
  if (regressions.length) return regressions
  return asArray(baseline.value.comparisons).filter((item) => item.regressed === true)
})

const hasLlmSummary = computed(() =>
  Boolean(llmSummary.value.text || llmSummary.value.status || llmSummary.value.model_id),
)

const llmSummaryHtml = computed(() => renderMarkdown(String(llmSummary.value.text || '暂无 LLM 分析。')))

const issueCount = computed(() =>
  thresholdFailures.value.length + failedChecks.value.length + baselineRegressions.value.length,
)

onMounted(() => {
  void refresh()
})

async function refresh() {
  if (activeProject.value !== 'agent') return
  loading.value = true
  error.value = ''
  try {
    const response = await listReports('realtime')
    reports.value = response.reports
    const next = selectedReport.value || chooseDefaultReport(response.reports)
    if (next) {
      await selectReport(next)
    } else {
      selected.value = null
      selectedFilename.value = ''
    }
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    loading.value = false
  }
}

async function selectReport(report: ReportSummary) {
  selectedFilename.value = report.filename
  detailLoading.value = true
  error.value = ''
  try {
    selected.value = await getReport('realtime', report.filename)
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    detailLoading.value = false
  }
}

function chooseDefaultReport(items: ReportSummary[]) {
  for (const filename of preferredReports) {
    const match = items.find((item) => item.filename === filename)
    if (match) return match
  }
  return items.find((item) => item.filename.startsWith('benchmark-')) || items[0]
}

function selectProject(projectId: string) {
  if (projectId === activeProject.value) return
  void router.push({ name: 'benchmarks', params: { project: projectId } })
}

function asObject(value: unknown): Dict {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {}
}

function asArray(value: unknown): Dict[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === 'object') as Dict[] : []
}

function statusType(value: boolean | null | undefined): 'success' | 'danger' | 'info' {
  if (value === true) return 'success'
  if (value === false) return 'danger'
  return 'info'
}

function passText(value: boolean | null | undefined) {
  if (value === true) return 'pass'
  if (value === false) return 'fail'
  return 'unknown'
}

function reportKind(filename: string) {
  if (preferredReports.includes(filename)) return '推荐'
  if (filename.startsWith('benchmark-')) return 'run'
  if (filename.startsWith('latest-')) return 'latest'
  return 'json'
}

function fmt(value: unknown, suffix = '') {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'number') {
    const text = Number.isInteger(value) ? String(value) : value.toFixed(1)
    return `${text}${suffix}`
  }
  return String(value)
}

function fmtMs(value: unknown) {
  return fmt(value, 'ms')
}

function pct(value: unknown) {
  if (typeof value !== 'number') return '-'
  return `${(value * 100).toFixed(1)}%`
}

function metricBarWidth(value: unknown, max: unknown) {
  if (typeof value !== 'number' || typeof max !== 'number' || max <= 0) return '0%'
  return `${Math.min(100, Math.max(0, (value / max) * 100))}%`
}

function turnLabel(row: Dict) {
  return row.logical_turn_id || row.turn_id || row.id || '-'
}

function turnIssue(row: Dict) {
  return row.error || row.failed_check_names?.join(', ') || row.failure || '-'
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function inlineMarkdown(value: string) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

function renderMarkdown(value: string) {
  const lines = value.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let listOpen = false
  let codeOpen = false

  const closeList = () => {
    if (listOpen) {
      out.push('</ul>')
      listOpen = false
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    if (line.trim().startsWith('```')) {
      closeList()
      if (codeOpen) {
        out.push('</code></pre>')
        codeOpen = false
      } else {
        out.push('<pre><code>')
        codeOpen = true
      }
      continue
    }
    if (codeOpen) {
      out.push(`${escapeHtml(line)}\n`)
      continue
    }
    if (!line.trim()) {
      closeList()
      continue
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line)
    if (heading) {
      closeList()
      const level = Math.min(3, heading[1].length + 2)
      out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`)
      continue
    }
    const bullet = /^[-*]\s+(.+)$/.exec(line.trim())
    if (bullet) {
      if (!listOpen) {
        out.push('<ul>')
        listOpen = true
      }
      out.push(`<li>${inlineMarkdown(bullet[1])}</li>`)
      continue
    }
    closeList()
    out.push(`<p>${inlineMarkdown(line)}</p>`)
  }
  closeList()
  if (codeOpen) out.push('</code></pre>')
  return out.join('')
}
</script>

<template>
  <div class="eid-page benchmark-page">
    <header class="eid-page-head">
      <div>
        <h2>Benchmark</h2>
        <p class="eid-page-hint">
          独立 benchmark 视图。当前接入 Agent realtime benchmark JSON，并优先展示 LLM 诊断总结。
        </p>
      </div>
      <div class="eid-head-actions">
        <el-button :icon="Refresh" :loading="loading" size="small" @click="refresh">
          刷新
        </el-button>
      </div>
    </header>

    <div class="benchmark-grid">
      <aside class="project-nav eid-panel eid-panel-pad">
        <button
          v-for="project in projects"
          :key="project.id"
          class="project-button"
          :class="{ active: activeProject === project.id }"
          @click="selectProject(project.id)"
        >
          <span>{{ project.label }}</span>
          <small>{{ project.hint }}</small>
        </button>
      </aside>

      <section class="reports-panel eid-panel eid-panel-scroll">
        <div class="eid-detail-head">
          <div>
            <h3>Agent Reports</h3>
            <p class="eid-meta-row">GET /api/admin/reports?kind=realtime</p>
          </div>
        </div>

        <el-alert v-if="error" type="error" :closable="false" show-icon class="inline-alert">
          <template #title>{{ error }}</template>
        </el-alert>

        <div v-if="!loading && reports.length === 0" class="eid-empty-state">
          <span>没有 realtime benchmark report</span>
          <code>~/eidolon/debug/reports/realtime</code>
        </div>

        <div v-else class="report-list" v-loading="loading">
          <button
            v-for="report in orderedReports"
            :key="report.filename"
            class="report-row"
            :class="{ active: selectedFilename === report.filename }"
            @click="selectReport(report)"
          >
            <span class="row-top">
              <el-tag :type="statusType(report.passed)" size="small" effect="dark">
                {{ passText(report.passed) }}
              </el-tag>
              <span class="mono filename">{{ report.filename }}</span>
            </span>
            <span class="row-meta">
              <el-tag size="small" effect="plain">{{ reportKind(report.filename) }}</el-tag>
              <span>{{ formatTimestamp(report.generated_at || report.modified_at) }}</span>
              <span>{{ report.schema_version || 'schema unknown' }}</span>
            </span>
          </button>
        </div>
      </section>

      <main class="detail-panel eid-panel eid-panel-scroll">
        <div v-if="detailLoading" class="placeholder">加载中...</div>
        <div v-else-if="!selected" class="placeholder">选择左侧 benchmark report</div>
        <template v-else>
          <div class="eid-detail-head detail-head">
            <div>
              <h3>{{ payload.run_id || selected.summary.id }}</h3>
              <p class="eid-meta-row">
                <el-tag :type="statusType(selected.summary.passed)" size="small" effect="dark">
                  {{ passText(selected.summary.passed) }}
                </el-tag>
                <span class="mono">{{ payload.mode || 'mode unknown' }}</span>
                <span>{{ payload.profile || 'profile unknown' }}</span>
                <span>{{ formatTimestamp(payload.generated_at || selected.summary.generated_at || selected.summary.modified_at) }}</span>
              </p>
            </div>
            <div class="schema mono">{{ selected.summary.schema_version || 'schema unknown' }}</div>
          </div>

          <section class="summary-cards">
            <div class="metric-card status-card" :class="{ danger: selected.summary.passed === false }">
              <span class="card-label">Status</span>
              <strong>{{ passText(selected.summary.passed) }}</strong>
              <small>{{ payload.mode || '-' }} · {{ payload.run_id || selected.summary.filename }}</small>
            </div>
            <div class="metric-card">
              <span class="card-label">Turns</span>
              <strong>{{ fmt(payloadSummary.passed_turn_count) }}/{{ fmt(payloadSummary.turn_count) }}</strong>
              <small>{{ fmt(payloadSummary.failed_turn_count) }} failed · {{ fmt(payloadSummary.scenario_count) }} scenarios</small>
            </div>
            <div
              v-for="metric in metricCards"
              :key="metric.name"
              class="metric-card"
            >
              <span class="card-label">{{ metric.label }} p95</span>
              <strong>{{ fmtMs(metric.stats.p95) }}</strong>
              <small>p50 {{ fmtMs(metric.stats.p50) }} · p99 {{ fmtMs(metric.stats.p99) }}</small>
            </div>
            <div class="metric-card" :class="{ danger: issueCount > 0 }">
              <span class="card-label">Issues</span>
              <strong>{{ issueCount }}</strong>
              <small>{{ thresholdFailures.length }} threshold · {{ failedChecks.length }} checks · {{ baselineRegressions.length }} baseline</small>
            </div>
          </section>

          <section class="content-block llm-block">
            <div class="section-head">
              <div>
                <h4 class="eid-section-title">LLM 分析</h4>
                <p class="muted">
                  {{ llmSummary.status || 'status unknown' }}
                  <span v-if="llmSummary.model_id"> · {{ llmSummary.model_id }}</span>
                  <span v-if="llmSummary.generated_at"> · {{ formatTimestamp(llmSummary.generated_at) }}</span>
                </p>
              </div>
              <el-tag v-if="hasLlmSummary" size="small" effect="plain">payload.llm_summary</el-tag>
            </div>
            <article class="markdown-body" v-html="llmSummaryHtml" />
          </section>

          <section class="content-block">
            <h4 class="eid-section-title">不达标项</h4>
            <div v-if="issueCount === 0" class="ok-line">没有 threshold、failed check 或 baseline regression。</div>
            <el-collapse v-else>
              <el-collapse-item
                v-if="thresholdFailures.length"
                :title="`Threshold failures (${thresholdFailures.length})`"
                name="thresholds"
              >
                <el-table :data="thresholdFailures" size="small" border>
                  <el-table-column prop="name" label="Check" min-width="190" />
                  <el-table-column prop="metric" label="Metric" min-width="140" />
                  <el-table-column prop="stat" label="Stat" width="80" />
                  <el-table-column label="Actual" width="110" align="right">
                    <template #default="{ row }">{{ fmtMs(row.actual) }}</template>
                  </el-table-column>
                  <el-table-column label="Threshold" width="120" align="right">
                    <template #default="{ row }">{{ fmtMs(row.threshold) }}</template>
                  </el-table-column>
                  <el-table-column prop="detail" label="Detail" min-width="220" />
                </el-table>
              </el-collapse-item>

              <el-collapse-item
                v-if="failedChecks.length"
                :title="`Failed checks (${failedChecks.length})`"
                name="checks"
              >
                <el-table :data="failedChecks" size="small" border>
                  <el-table-column prop="scenario_id" label="Scenario" min-width="180" />
                  <el-table-column prop="turn_id" label="Turn" min-width="140" />
                  <el-table-column prop="name" label="Check" min-width="180" />
                  <el-table-column prop="detail" label="Detail" min-width="260" />
                </el-table>
              </el-collapse-item>

              <el-collapse-item
                v-if="baselineRegressions.length"
                :title="`Baseline regressions (${baselineRegressions.length})`"
                name="baseline"
              >
                <el-table :data="baselineRegressions" size="small" border>
                  <el-table-column prop="metric" label="Metric" min-width="150" />
                  <el-table-column prop="stat" label="Stat" width="80" />
                  <el-table-column label="Baseline" width="110" align="right">
                    <template #default="{ row }">{{ fmtMs(row.baseline) }}</template>
                  </el-table-column>
                  <el-table-column label="Current" width="110" align="right">
                    <template #default="{ row }">{{ fmtMs(row.current) }}</template>
                  </el-table-column>
                  <el-table-column label="Delta" width="110" align="right">
                    <template #default="{ row }">{{ fmtMs(row.delta_ms ?? row.delta) }}</template>
                  </el-table-column>
                  <el-table-column label="Ratio" width="90" align="right">
                    <template #default="{ row }">{{ pct(row.delta_ratio) }}</template>
                  </el-table-column>
                  <el-table-column prop="detail" label="Detail" min-width="220" />
                </el-table>
              </el-collapse-item>
            </el-collapse>
          </section>

          <section class="content-block split-block">
            <div>
              <h4 class="eid-section-title">Summary JSON</h4>
              <JsonViewer :data="payloadSummary" max-height="220px" />
            </div>
            <div>
              <h4 class="eid-section-title">LLM Diagnostic</h4>
              <JsonViewer :data="diagnostic" max-height="220px" />
            </div>
          </section>

          <section class="content-block">
            <h4 class="eid-section-title">图表 · Latency Percentiles</h4>
            <div class="metric-chart">
              <div v-for="row in metricRows" :key="`${row.metric}-${row.stat}`" class="chart-row">
                <span class="chart-label mono">{{ row.metric }} {{ row.stat }}</span>
                <div class="chart-track">
                  <span :style="{ width: metricBarWidth(row.value, row.max) }" />
                </div>
                <span class="chart-value mono">{{ fmtMs(row.value) }}</span>
              </div>
            </div>
          </section>

          <section v-if="diagnosticDimensions.length" class="content-block">
            <h4 class="eid-section-title">图表 · Dimensions</h4>
            <el-table :data="diagnosticDimensions" size="small" border>
              <el-table-column prop="name" label="Dimension" min-width="220" />
              <el-table-column label="First Delta P95" width="150" align="right">
                <template #default="{ row }">{{ fmtMs(row.metrics?.first_delta_ms?.p95 ?? row.first_delta_p95_ms) }}</template>
              </el-table-column>
              <el-table-column label="Total P95" width="130" align="right">
                <template #default="{ row }">{{ fmtMs(row.metrics?.total_ms?.p95 ?? row.total_p95_ms) }}</template>
              </el-table-column>
              <el-table-column label="Filters" min-width="220">
                <template #default="{ row }">
                  <span class="mono">{{ JSON.stringify(row.filters || {}) }}</span>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section v-if="visibleTurns.length" class="content-block">
            <div class="section-head">
              <h4 class="eid-section-title">图表 · Turns</h4>
              <span class="muted">first delta / total latency bars</span>
            </div>
            <el-table :data="visibleTurns" size="small" border>
              <el-table-column label="Status" width="90">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.passed)" size="small">
                    {{ passText(row.passed) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="scenario_id" label="Scenario" min-width="180" />
              <el-table-column label="Turn" min-width="160">
                <template #default="{ row }">
                  <span class="mono">{{ turnLabel(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="First" width="90" align="right">
                <template #default="{ row }">{{ fmtMs(row.first_delta_ms) }}</template>
              </el-table-column>
              <el-table-column label="Total" min-width="200">
                <template #default="{ row }">
                  <div class="turn-bar">
                    <span class="total" :style="{ width: metricBarWidth(row.total_ms, metrics.total_ms?.max) }" />
                    <span class="first" :style="{ width: metricBarWidth(row.first_delta_ms, metrics.total_ms?.max) }" />
                  </div>
                  <span class="mono">{{ fmtMs(row.total_ms) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="Failure" min-width="220">
                <template #default="{ row }">
                  <span class="muted">{{ turnIssue(row) }}</span>
                </template>
              </el-table-column>
            </el-table>
            <p v-if="allTurns.length > visibleTurns.length" class="table-note">
              仅展示前 {{ visibleTurns.length }} 个 turns，完整数据在 payload 中。
            </p>
          </section>

          <section v-if="slowTurns.length" class="content-block">
            <h4 class="eid-section-title">Slow Turns</h4>
            <el-table :data="slowTurns" size="small" border>
              <el-table-column prop="scenario_id" label="Scenario" min-width="180" />
              <el-table-column label="Turn" min-width="160">
                <template #default="{ row }">
                  <span class="mono">{{ turnLabel(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="First" width="100" align="right">
                <template #default="{ row }">{{ fmtMs(row.first_delta_ms) }}</template>
              </el-table-column>
              <el-table-column label="Total" width="100" align="right">
                <template #default="{ row }">{{ fmtMs(row.total_ms) }}</template>
              </el-table-column>
              <el-table-column prop="reason" label="Reason" min-width="220" />
            </el-table>
          </section>

          <section v-if="scenarios.length" class="content-block">
            <h4 class="eid-section-title">Scenarios</h4>
            <el-table :data="scenarios" size="small" border>
              <el-table-column label="Status" width="90">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.passed)" size="small">
                    {{ passText(row.passed) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="scenario_id" label="Scenario" min-width="220" />
              <el-table-column prop="description" label="Description" min-width="260" />
              <el-table-column label="Turns" width="90" align="right">
                <template #default="{ row }">{{ fmt(row.turn_count) }}</template>
              </el-table-column>
            </el-table>
          </section>

          <section class="content-block split-block">
            <div>
              <h4 class="eid-section-title">Recommended Charts</h4>
              <JsonViewer :data="chartHints" max-height="220px" />
            </div>
            <div>
              <h4 class="eid-section-title">Baseline</h4>
              <JsonViewer :data="baseline" max-height="220px" />
            </div>
          </section>

          <section class="content-block payload-block">
            <h4 class="eid-section-title">Payload</h4>
            <JsonViewer :data="payload" max-height="520px" />
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
.benchmark-grid {
  display: grid;
  grid-template-columns: 178px 360px minmax(0, 1fr);
  gap: 16px;
  flex: 1 1 auto;
  min-height: 0;
}
.project-nav {
  gap: 8px;
}
.project-button,
.report-row {
  width: 100%;
  border: 1px solid color-mix(in srgb, var(--eid-border-strong) 72%, transparent);
  border-radius: 6px;
  background: var(--eid-bg-inset);
  color: var(--eid-text-secondary);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease;
}
.project-button {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px;
}
.project-button span {
  color: var(--eid-text-primary);
  font-weight: 700;
}
.project-button small,
.row-meta,
.schema,
.muted,
.table-note {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.project-button:hover,
.project-button.active,
.report-row:hover,
.report-row.active {
  border-color: color-mix(in srgb, var(--eid-accent) 48%, var(--eid-border));
  background: color-mix(in srgb, var(--eid-accent-soft) 32%, var(--eid-bg-inset));
  color: var(--eid-text-primary);
}
.reports-panel,
.detail-panel {
  min-height: 0;
}
.inline-alert {
  margin: 10px;
}
.report-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
}
.report-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
}
.row-top,
.row-meta,
.section-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-top,
.row-meta,
.section-head {
  min-width: 0;
  flex-wrap: wrap;
}
.section-head {
  justify-content: space-between;
  margin-bottom: 8px;
}
.filename {
  flex: 1 1 auto;
  min-width: 0;
}
.detail-head {
  position: sticky;
  top: 0;
  z-index: 2;
}
.placeholder {
  padding: 32px;
  color: var(--eid-text-muted);
  font-size: 12px;
  text-align: center;
}
.summary-cards {
  display: grid;
  grid-template-columns: repeat(5, minmax(150px, 1fr));
  gap: 10px;
  padding: 12px;
}
.metric-card {
  min-width: 0;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 16%, var(--eid-border));
  border-radius: 6px;
  background: var(--eid-bg-inset);
}
.metric-card.danger {
  border-color: color-mix(in srgb, var(--eid-danger) 62%, var(--eid-border));
  background: color-mix(in srgb, var(--eid-danger-soft) 28%, var(--eid-bg-inset));
}
.card-label {
  display: block;
  color: var(--eid-text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}
.metric-card strong {
  display: block;
  margin: 6px 0 2px;
  color: var(--eid-text-primary);
  font-size: 25px;
  line-height: 1;
  overflow-wrap: anywhere;
}
.metric-card small {
  color: var(--eid-text-secondary);
}
.content-block {
  padding: 12px;
  border-top: 1px solid color-mix(in srgb, var(--eid-accent) 12%, var(--eid-border));
}
.llm-block {
  background:
    linear-gradient(180deg, rgba(52, 211, 153, 0.045), transparent 140px),
    transparent;
}
.markdown-body {
  color: var(--eid-text-primary);
  font-size: 13px;
  line-height: 1.72;
}
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5) {
  margin: 12px 0 6px;
  color: var(--eid-text-primary);
  font-size: 14px;
}
.markdown-body :deep(p) {
  margin: 8px 0;
}
.markdown-body :deep(ul) {
  margin: 8px 0;
  padding-left: 20px;
}
.markdown-body :deep(li) {
  margin: 4px 0;
}
.markdown-body :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--eid-bg-inset);
  color: var(--eid-accent-hover);
  font-family: var(--eid-font-mono);
  font-size: 12px;
}
.markdown-body :deep(pre) {
  margin: 10px 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid var(--eid-border);
  border-radius: 5px;
  background: var(--eid-bg-inset);
}
.ok-line {
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--eid-success) 34%, var(--eid-border));
  border-radius: 6px;
  background: color-mix(in srgb, var(--eid-success-soft) 30%, transparent);
  color: var(--eid-success);
  font-size: 13px;
}
.split-block {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
}
.metric-chart {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chart-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.42fr) minmax(160px, 1fr) 86px;
  gap: 10px;
  align-items: center;
}
.chart-label,
.chart-value {
  color: var(--eid-text-secondary);
  font-size: 12px;
}
.chart-value {
  text-align: right;
}
.chart-track {
  height: 12px;
  overflow: hidden;
  border: 1px solid var(--eid-border);
  border-radius: 4px;
  background: var(--eid-bg-inset);
}
.chart-track span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--eid-accent), var(--eid-accent-warm));
}
.turn-bar {
  position: relative;
  height: 12px;
  margin-bottom: 4px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
}
.turn-bar span {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
}
.turn-bar .total {
  background: color-mix(in srgb, var(--eid-accent) 38%, transparent);
}
.turn-bar .first {
  background: color-mix(in srgb, var(--eid-accent-warm) 68%, transparent);
}
.table-note {
  margin: 8px 0 0;
}
.mono {
  font-family: var(--eid-font-mono);
  overflow-wrap: anywhere;
  word-break: break-word;
}
@media (max-width: 1360px) {
  .benchmark-grid {
    grid-template-columns: 160px 320px minmax(0, 1fr);
  }
  .summary-cards {
    grid-template-columns: repeat(3, minmax(150px, 1fr));
  }
}
@media (max-width: 1040px) {
  .benchmark-grid {
    grid-template-columns: 1fr;
  }
  .project-nav {
    min-height: auto;
  }
  .split-block {
    grid-template-columns: 1fr;
  }
  .chart-row {
    grid-template-columns: 1fr;
  }
  .chart-value {
    text-align: left;
  }
}
</style>
