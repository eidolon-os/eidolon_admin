<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  getReport,
  listReports,
  type ReportDetail,
  type ReportKind,
  type ReportSummary,
} from '@/api/reports'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const reports = ref<ReportSummary[]>([])
const selected = ref<ReportDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const kindFilter = ref<ReportKind | 'all'>('all')

const filteredReports = computed(() => {
  if (kindFilter.value === 'all') return reports.value
  return reports.value.filter((r) => r.kind === kindFilter.value)
})

onMounted(() => {
  void refresh()
})

async function refresh() {
  loading.value = true
  try {
    const r = await listReports(kindFilter.value === 'all' ? undefined : kindFilter.value)
    reports.value = r.reports
    if (!selected.value && r.reports.length > 0) {
      void selectReport(r.reports[0])
    }
  } catch (e: any) {
    ElMessage.error(`加载 replay reports 失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function selectReport(row: ReportSummary) {
  detailLoading.value = true
  try {
    selected.value = await getReport(row.kind, row.filename)
  } catch (e: any) {
    ElMessage.error(`加载 report 详情失败: ${extractErrorMessage(e)}`)
  } finally {
    detailLoading.value = false
  }
}

function statusType(value: boolean | null): 'success' | 'danger' | 'info' {
  if (value === true) return 'success'
  if (value === false) return 'danger'
  return 'info'
}
</script>

<template>
  <div class="page eid-page">
    <header class="page-head eid-page-head">
      <div>
        <h2>Replay Reports</h2>
        <p class="hint eid-page-hint">
          只读视图：浏览 agent 生成的 experience replay 与 realtime guard JSON 报告。
        </p>
      </div>
      <div class="head-actions eid-head-actions">
        <el-radio-group v-model="kindFilter" size="small" @change="refresh">
          <el-radio-button label="all">all</el-radio-button>
          <el-radio-button label="replay">replay</el-radio-button>
          <el-radio-button label="realtime">realtime</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" :loading="loading" size="small" @click="refresh">
          刷新
        </el-button>
      </div>
    </header>

    <div class="split eid-split">
      <section class="left eid-panel eid-panel-pad eid-panel-scroll">
        <el-table
          v-loading="loading"
          :data="filteredReports"
          size="small"
          stripe
          highlight-current-row
          @row-click="(row: ReportSummary) => selectReport(row)"
        >
          <el-table-column label="kind" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.kind }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="status" width="90">
            <template #default="{ row }">
              <el-tag :type="statusType(row.passed)" size="small" effect="dark">
                {{ row.passed === null ? 'unknown' : (row.passed ? 'pass' : 'fail') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="report" min-width="180">
            <template #default="{ row }">
              <span class="mono">{{ row.filename }}</span>
            </template>
          </el-table-column>
          <el-table-column label="updated" width="160">
            <template #default="{ row }">
              <span class="muted mono">{{ formatTimestamp(row.modified_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="!loading && filteredReports.length === 0" class="empty">
          没有 report。运行 agent 脚本并把输出写到 reports/replay 或 reports/realtime。
        </div>
      </section>

      <section class="right eid-panel eid-panel-scroll">
        <div v-if="detailLoading" class="placeholder">加载中...</div>
        <div v-else-if="!selected" class="placeholder">选择左侧 report</div>
        <template v-else>
          <div class="detail-head eid-detail-head">
            <div>
              <h3>{{ selected.summary.id }}</h3>
              <p class="meta eid-meta-row">
                <el-tag :type="statusType(selected.summary.passed)" size="small" effect="dark">
                  {{ selected.summary.passed === null ? 'unknown' : (selected.summary.passed ? 'pass' : 'fail') }}
                </el-tag>
                <span class="mono">{{ selected.summary.schema_version || 'schema unknown' }}</span>
                <span>{{ formatTimestamp(selected.summary.generated_at || selected.summary.modified_at) }}</span>
              </p>
            </div>
          </div>

          <div class="summary-grid">
            <div>
              <span class="lbl">summary</span>
              <JsonViewer :data="selected.summary.summary" max-height="180px" />
            </div>
            <div>
              <span class="lbl">metrics</span>
              <JsonViewer :data="selected.summary.metrics" max-height="180px" />
            </div>
          </div>

          <div class="payload">
            <span class="lbl">payload</span>
            <JsonViewer :data="selected.payload" max-height="52vh" />
          </div>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page { min-height: 0; }
.page-head { margin-bottom: 0; }
.hint { max-width: 620px; }
.empty, .placeholder { padding: 32px; text-align: center; color: var(--eid-text-muted); font-size: 12px; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.detail-head { position: sticky; top: 0; z-index: 1; }
.summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }
.payload { padding: 0 12px 12px; }
.lbl { display: block; margin: 0 0 6px; color: var(--eid-text-muted); font-size: 12px; }
@media (max-width: 760px) {
  .summary-grid { grid-template-columns: 1fr; }
}
</style>
