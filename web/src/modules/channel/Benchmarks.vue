<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  getBenchmarkRun,
  listBenchmarkRuns,
  type BenchmarkMetricStats,
  type BenchmarkRunDetail,
  type BenchmarkRunSummary,
  type BenchmarkRunnerSummary,
} from '@/api/channelBenchmarks'

const loading = ref(false)
const error = ref('')
const runsDir = ref('')
const runs = ref<BenchmarkRunSummary[]>([])
const selectedRunId = ref('')
const detail = ref<BenchmarkRunDetail | null>(null)

const selectedRun = computed(() =>
  runs.value.find((run) => run.run_id === selectedRunId.value) || null,
)

const runnerSummaries = computed(() => selectedRun.value?.runners || [])

const findings = computed(() => {
  const items: string[] = []
  for (const runner of runnerSummaries.value) {
    const failed = runner.summary.failed || 0
    if (failed > 0) {
      items.push(`${runner.runner}: ${failed} failed case(s)`)
    }
  }
  return items
})

const keyLatencyMetrics = [
  'interrupt_decision_ms',
  'elapsed_ms',
  'vad_elapsed_ms',
  'stt_elapsed_ms',
  'eot_elapsed_ms',
  'tts_elapsed_ms',
  'room_connected_ms',
  'participant_connected_ms',
  'agent_track_subscribed_ms',
  'agent_audio_first_ms',
  'user_done_to_agent_audio_after_user_done_ms',
  'publish_to_agent_audio_first_ms',
]

async function loadRuns() {
  loading.value = true
  error.value = ''
  try {
    const payload = await listBenchmarkRuns()
    runsDir.value = payload.runs_dir
    runs.value = payload.runs
    if (!selectedRunId.value && payload.runs.length) {
      selectedRunId.value = payload.runs[0].run_id
    }
    if (selectedRunId.value) {
      detail.value = await getBenchmarkRun(selectedRunId.value)
    }
  } catch (err: any) {
    error.value = err?.response?.data?.detail || err?.message || '加载 benchmark 失败'
  } finally {
    loading.value = false
  }
}

async function selectRun(runId: string) {
  selectedRunId.value = runId
  loading.value = true
  error.value = ''
  try {
    detail.value = await getBenchmarkRun(runId)
  } catch (err: any) {
    error.value = err?.response?.data?.detail || err?.message || '加载 benchmark 详情失败'
  } finally {
    loading.value = false
  }
}

function statusType(runner: BenchmarkRunnerSummary) {
  return (runner.summary.failed || 0) > 0 ? 'danger' : 'success'
}

function formatTime(ts: number) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

function fmt(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(1)
  return String(value)
}

function metricRows(runner: BenchmarkRunnerSummary) {
  const metrics = runner.summary.metrics || {}
  return keyLatencyMetrics
    .filter((key) => metrics[key])
    .map((key) => ({ key, value: metrics[key] as BenchmarkMetricStats }))
}

function passLabel(runner: BenchmarkRunnerSummary) {
  return `${runner.summary.passed || 0}/${runner.summary.total || 0}`
}

onMounted(loadRuns)
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Channel Benchmarks</h2>
        <p class="subtitle">读取 eidolon_channel 生成的 benchmark artifacts，用于查看体验基线和真实链路结果。</p>
      </div>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="loadRuns">刷新</el-button>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon class="block">
      <template #title>{{ error }}</template>
    </el-alert>

    <el-alert v-if="!loading && runs.length === 0" type="info" :closable="false" show-icon class="block">
      <template #title>还没有 benchmark run。先在 eidolon_channel 中执行 scripts/bench_voice.py。</template>
      <div class="mono">{{ runsDir }}</div>
    </el-alert>

    <div v-if="runs.length" class="layout">
      <el-card class="runs-panel">
        <template #header>
          <div class="card-header">
            <span>Runs</span>
            <span class="muted mono">{{ runsDir }}</span>
          </div>
        </template>
        <div
          v-for="run in runs"
          :key="run.run_id"
          class="run-row"
          :class="{ active: run.run_id === selectedRunId }"
          @click="selectRun(run.run_id)"
        >
          <div class="run-id">{{ run.run_id }}</div>
          <div class="muted">{{ formatTime(run.modified_at) }}</div>
          <div class="runner-tags">
            <el-tag v-for="runner in run.runners" :key="runner.runner" size="small" :type="statusType(runner)">
              {{ runner.runner }} {{ passLabel(runner) }}
            </el-tag>
          </div>
        </div>
      </el-card>

      <div class="detail">
        <div class="summary-grid">
          <el-card v-for="runner in runnerSummaries" :key="runner.runner" class="runner-card">
            <div class="runner-name">{{ runner.runner }}</div>
            <div class="pass" :class="{ bad: (runner.summary.failed || 0) > 0 }">
              {{ passLabel(runner) }}
            </div>
            <div class="muted">{{ runner.run.profile || '—' }}</div>
            <div class="muted mono">sha {{ runner.run.git_sha || 'unknown' }}</div>
          </el-card>
        </div>

        <el-card class="block">
          <template #header>Findings</template>
          <ul v-if="findings.length" class="findings">
            <li v-for="item in findings" :key="item">{{ item }}</li>
          </ul>
          <div v-else class="ok">所有 runner case 均通过。性能回归请结合 channel 生成的 dashboard.json/report 继续判断。</div>
        </el-card>

        <el-card v-for="runner in runnerSummaries" :key="`${runner.runner}-metrics`" class="block">
          <template #header>{{ runner.runner }} latency metrics</template>
          <el-table :data="metricRows(runner)" size="small" border>
            <el-table-column prop="key" label="Metric" min-width="260" />
            <el-table-column label="P50" width="110" align="right">
              <template #default="{ row }">{{ fmt(row.value.p50) }}</template>
            </el-table-column>
            <el-table-column label="P95" width="110" align="right">
              <template #default="{ row }">{{ fmt(row.value.p95) }}</template>
            </el-table-column>
            <el-table-column label="Max" width="110" align="right">
              <template #default="{ row }">{{ fmt(row.value.max) }}</template>
            </el-table-column>
            <el-table-column label="Count" width="90" align="right">
              <template #default="{ row }">{{ fmt(row.value.count) }}</template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card v-if="detail" class="block">
          <template #header>Cases</template>
          <el-collapse>
            <el-collapse-item
              v-for="[runner, payload] in Object.entries(detail.metrics || {})"
              :key="runner"
              :title="`${runner} (${payload.summary.passed || 0}/${payload.summary.total || 0})`"
            >
              <el-table :data="payload.cases" size="small" border>
                <el-table-column prop="case_id" label="Case" min-width="240" />
                <el-table-column prop="suite" label="Suite" min-width="160" />
                <el-table-column label="Status" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.passed ? 'success' : 'danger'" size="small">
                      {{ row.passed ? 'PASS' : 'FAIL' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="Errors" min-width="260">
                  <template #default="{ row }">
                    <span class="muted">{{ row.errors?.join('; ') || '—' }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { margin: 6px 0 0; color: var(--eid-text-secondary); font-size: 13px; }
.layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 16px; align-items: start; }
.runs-panel { position: sticky; top: 0; }
.card-header { display: flex; flex-direction: column; gap: 4px; }
.run-row { border: 1px solid var(--eid-border); border-radius: var(--eid-radius); padding: 10px; margin-bottom: 8px; cursor: pointer; background: var(--eid-bg-panel); }
.run-row.active { border-color: var(--eid-accent); box-shadow: 0 0 0 1px var(--eid-accent); }
.run-id { font-weight: 600; margin-bottom: 4px; word-break: break-all; }
.runner-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.detail { min-width: 0; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.runner-card { min-height: 126px; }
.runner-name { font-weight: 600; }
.pass { margin: 8px 0 4px; color: #047857; font-size: 28px; font-weight: 700; }
.pass.bad { color: #b91c1c; }
.block { margin-top: 16px; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.mono { font-family: var(--eid-font-mono); }
.ok { color: #047857; }
.findings { margin: 0; padding-left: 18px; color: #b45309; }
@media (max-width: 960px) {
  .layout { grid-template-columns: 1fr; }
  .runs-panel { position: static; }
}
</style>
