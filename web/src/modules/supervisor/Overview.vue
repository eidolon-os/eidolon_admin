<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  disableConfig,
  enableConfig,
  formatUptime,
  reread,
  restartProgram,
  startGroup,
  startProgram,
  stateTagType,
  stopGroup,
  stopProgram,
  tailLogUrl,
  type ConfigSummary,
  listConfigs,
} from '@/api/supervisor'
import { getOverview, type OverviewResponse, type ProgramView } from '@/api/overview'
import ConfigEditor from './ConfigEditor.vue'
import SystemHealthPanel from './SystemHealthPanel.vue'
import LogViewer from '@/modules/common/LogViewer.vue'
import OnboardingBanner from '@/modules/common/OnboardingBanner.vue'

const overview = ref<OverviewResponse | null>(null)
const configs = ref<ConfigSummary[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

// drawer state
const editorOpen = ref(false)
const editorTarget = ref<string>('')
const logOpen = ref(false)
const logTarget = ref<{ name: string; stream: 'stdout' | 'stderr' } | null>(null)

async function refresh() {
  loading.value = true
  try {
    const [o, c] = await Promise.all([getOverview(), listConfigs()])
    overview.value = o
    configs.value = c
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => {
    if (!loading.value) refresh()
  }, 5000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

const supervisordOk = computed(() => overview.value?.supervisord_reachable ?? false)

function configFor(serviceId: string): ConfigSummary | undefined {
  // Match by name == id, then by any program overlap.
  return configs.value.find((c) => c.name === serviceId)
}

function configForGroup(group: string): ConfigSummary | undefined {
  return configs.value.find(
    (c) => c.name === group || c.groups.includes(group),
  )
}

async function onToggleEnable(cfg: ConfigSummary) {
  try {
    if (cfg.enabled) {
      await ElMessageBox.confirm(
        `禁用 ${cfg.name}？将停止其中所有 program 并移除软链。`,
        '确认',
        { type: 'warning' },
      )
      await disableConfig(cfg.name)
      ElMessage.success(`${cfg.name} 已禁用`)
    } else {
      await enableConfig(cfg.name)
      ElMessage.success(`${cfg.name} 已启用`)
    }
    await refresh()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(`操作失败：${e?.message || e}`)
  }
}

async function doProgramAction(p: ProgramView, action: 'start' | 'stop' | 'restart') {
  try {
    if (action === 'start') await startProgram(p.full_name)
    if (action === 'stop') await stopProgram(p.full_name)
    if (action === 'restart') await restartProgram(p.full_name)
    ElMessage.success(`${p.full_name}: ${action}`)
    await refresh()
  } catch (e: any) {
    ElMessage.error(`${action} 失败：${e?.response?.data?.detail || e.message}`)
  }
}

async function doGroupAction(group: string, action: 'start' | 'stop') {
  try {
    if (action === 'start') await startGroup(group)
    else await stopGroup(group)
    await refresh()
  } catch (e: any) {
    ElMessage.error(`${action} group 失败：${e?.response?.data?.detail || e.message}`)
  }
}

async function doReread() {
  try {
    const { data } = await reread()
    ElMessage.success(
      `reread: +${data.added?.length || 0} ~${data.changed?.length || 0} -${data.removed?.length || 0}`,
    )
    await refresh()
  } catch (e: any) {
    ElMessage.error(`reread 失败：${e?.response?.data?.detail || e.message}`)
  }
}

function openEditor(name: string) {
  editorTarget.value = name
  editorOpen.value = true
}

function openLog(p: ProgramView, stream: 'stdout' | 'stderr') {
  logTarget.value = { name: p.full_name, stream }
  logOpen.value = true
}

// supervisord state machine reference:
//   STOPPED, EXITED, FATAL, BACKOFF  → not running, can start
//   RUNNING, STARTING                → running, can stop
//   STOPPING                         → transient, neither start nor stop
//   UNKNOWN                          → not registered (config disabled)
const RUNNING_STATES = new Set(['RUNNING', 'STARTING'])
const STOPPED_STATES = new Set(['STOPPED', 'EXITED', 'FATAL', 'BACKOFF'])

function canStart(row: { statename: string }): boolean {
  return supervisordOk.value && STOPPED_STATES.has(row.statename)
}
function canStop(row: { statename: string }): boolean {
  return supervisordOk.value && RUNNING_STATES.has(row.statename)
}

function probeLabel(probe: { configured: boolean; ok?: boolean; status_code?: number; latency_ms?: number; error?: string }) {
  if (!probe.configured) return 'no probe'
  if (probe.ok) return `HTTP ${probe.status_code} (${probe.latency_ms}ms)`
  if (probe.error) return probe.error
  if (probe.status_code) return `HTTP ${probe.status_code}`
  return 'unreachable'
}
</script>

<template>
  <div class="page">
    <OnboardingBanner />
    <div class="topbar">
      <div class="title-row">
        <h2 class="title">Supervisor</h2>
        <el-tag :type="supervisordOk ? 'success' : 'danger'" effect="dark" size="small">
          {{ supervisordOk ? `supervisord 在线` : 'supervisord 离线' }}
        </el-tag>
      </div>
      <div class="actions">
        <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
        <el-button size="small" type="primary" @click="doReread">Reread 配置</el-button>
      </div>
    </div>

    <el-alert
      v-if="overview && !supervisordOk"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #title>
        supervisord 未运行。在终端启动：<code>./deploy/dev/run_all.sh start</code>
      </template>
    </el-alert>

    <!-- === System Health === -->
    <SystemHealthPanel class="health-panel" />

    <!-- === Services === -->
    <h3 class="section-title">Services</h3>
    <div class="grid">
      <el-card
        v-for="svc in overview?.services || []"
        :key="svc.id"
        class="svc-card"
        shadow="hover"
      >
        <template #header>
          <div class="card-header">
            <div class="card-title">
              <span :class="['dot', svc.online ? 'dot-online' : 'dot-offline']" />
              <span class="name">{{ svc.name }}</span>
              <el-tag size="small" :type="svc.online ? 'success' : 'danger'" effect="dark">
                {{ svc.online ? 'online' : 'offline' }}
              </el-tag>
              <el-tag v-if="!svc.supervised" size="small" effect="plain" type="info">
                not supervised
              </el-tag>
            </div>
            <div class="card-actions">
              <template v-if="svc.supervised && configFor(svc.id)">
                <el-switch
                  :model-value="configFor(svc.id)!.enabled"
                  size="small"
                  @change="onToggleEnable(configFor(svc.id)!)"
                  active-text="启用"
                  inactive-text="禁用"
                />
                <el-button size="small" link @click="openEditor(configFor(svc.id)!.name)">
                  编辑配置
                </el-button>
              </template>
            </div>
          </div>
        </template>

        <div class="meta-row">
          <span class="meta-label">HTTP</span>
          <span :class="svc.http_probe.ok ? 'meta-good' : svc.http_probe.configured ? 'meta-bad' : 'meta-muted'">
            {{ probeLabel(svc.http_probe) }}
          </span>
          <span v-if="svc.http_probe.url" class="meta-url">{{ svc.http_probe.url }}</span>
        </div>

        <div v-if="svc.supervised && svc.programs.length === 0" class="hint">
          配置未启用 — 打开开关让 supervisord 接管
        </div>

        <el-table
          v-if="svc.programs.length"
          :data="svc.programs"
          stripe
          size="small"
          style="background: transparent"
        >
          <el-table-column label="Program" min-width="180">
            <template #default="{ row }">
              <span class="prog-name">{{ row.name }}</span>
              <div class="prog-meta">{{ row.full_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="stateTagType(row.statename)" effect="dark" size="small">
                {{ row.statename }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="PID" width="80">
            <template #default="{ row }">{{ row.pid || '-' }}</template>
          </el-table-column>
          <el-table-column label="Uptime" width="100">
            <template #default="{ row }">
              {{ row.statename === 'RUNNING' ? formatUptime(Math.floor(Date.now() / 1000) - row.uptime_sec, Math.floor(Date.now() / 1000)) : '-' }}
            </template>
          </el-table-column>
          <el-table-column label="退出 / 错误">
            <template #default="{ row }">
              <span v-if="row.spawnerr" class="err">{{ row.spawnerr }}</span>
              <span v-else class="muted">{{ row.description || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="260">
            <template #default="{ row }">
              <!--
                Gate buttons by current supervisord state so we don't fire
                actions that map to error responses:
                  UNKNOWN          → not registered (config disabled); enable switch is the only path
                  RUNNING/STARTING → start would fail with ALREADY_STARTED
                  STOPPED/EXITED/FATAL/BACKOFF → stop would fail with NOT_RUNNING
                Restart is always safe when the program is registered.
              -->
              <el-button
                size="small"
                :disabled="!canStart(row)"
                @click="doProgramAction(row, 'start')"
              >start</el-button>
              <el-button
                size="small"
                :disabled="!canStop(row)"
                @click="doProgramAction(row, 'stop')"
              >stop</el-button>
              <el-button
                size="small"
                :disabled="!supervisordOk || row.statename === 'UNKNOWN'"
                @click="doProgramAction(row, 'restart')"
              >restart</el-button>
              <el-dropdown size="small">
                <el-button size="small" link>日志 ▾</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item @click="openLog(row, 'stdout')">stdout (follow)</el-dropdown-item>
                    <el-dropdown-item @click="openLog(row, 'stderr')">stderr (follow)</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="!svc.supervised" class="hint">
          此服务未纳入 supervisord 托管 — 由各自的 deploy 脚本管理生命周期
        </div>
      </el-card>
    </div>

    <!-- === Infrastructure === -->
    <template v-if="overview?.infrastructure?.length">
      <h3 class="section-title">Infrastructure</h3>
      <div class="grid">
        <el-card
          v-for="infra in overview.infrastructure"
          :key="infra.group"
          class="svc-card"
          shadow="hover"
        >
          <template #header>
            <div class="card-header">
              <div class="card-title">
                <span :class="['dot', infra.online ? 'dot-online' : 'dot-offline']" />
                <span class="name">{{ infra.group }}</span>
                <el-tag size="small" :type="infra.online ? 'success' : 'danger'" effect="dark">
                  {{ infra.online ? 'online' : 'offline' }}
                </el-tag>
              </div>
              <div class="card-actions" v-if="configForGroup(infra.group)">
                <el-switch
                  :model-value="configForGroup(infra.group)!.enabled"
                  size="small"
                  @change="onToggleEnable(configForGroup(infra.group)!)"
                  active-text="启用"
                  inactive-text="禁用"
                />
                <el-button size="small" link @click="openEditor(configForGroup(infra.group)!.name)">
                  编辑配置
                </el-button>
              </div>
            </div>
          </template>

          <el-table :data="infra.programs" stripe size="small" style="background: transparent">
            <el-table-column label="Program" min-width="180">
              <template #default="{ row }">
                <span class="prog-name">{{ row.name }}</span>
                <div class="prog-meta">{{ row.full_name }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag :type="stateTagType(row.statename)" effect="dark" size="small">
                  {{ row.statename }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="PID" width="80">
              <template #default="{ row }">{{ row.pid || '-' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="260">
              <template #default="{ row }">
                <el-button size="small" :disabled="!supervisordOk" @click="doProgramAction(row, 'start')">start</el-button>
                <el-button size="small" :disabled="!supervisordOk" @click="doProgramAction(row, 'stop')">stop</el-button>
                <el-button size="small" :disabled="!supervisordOk" @click="doProgramAction(row, 'restart')">restart</el-button>
                <el-dropdown size="small">
                  <el-button size="small" link>日志 ▾</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item @click="openLog(row, 'stdout')">stdout</el-dropdown-item>
                      <el-dropdown-item @click="openLog(row, 'stderr')">stderr</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </template>

    <ConfigEditor v-model:open="editorOpen" :name="editorTarget" @saved="refresh" />
    <LogViewer
      v-model:open="logOpen"
      :title="logTarget ? `${logTarget.name} :: ${logTarget.stream}` : ''"
      :url="logTarget ? tailLogUrl(logTarget.name, logTarget.stream) : ''"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.actions {
  display: flex;
  gap: 8px;
}
.section-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--eid-text-muted);
  margin: 24px 0 12px 0;
}
.section-title:first-of-type {
  margin-top: 4px;
}
.health-panel {
  margin-bottom: 8px;
}
.grid {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.svc-card :deep(.el-card__header) {
  padding: 12px 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.card-title .name {
  font-size: 15px;
  font-weight: 600;
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-online {
  background: var(--eid-success);
  box-shadow: 0 0 8px var(--eid-success);
}
.dot-offline {
  background: var(--eid-danger);
}
.meta-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0 12px 0;
  font-size: 12px;
}
.meta-label {
  color: var(--eid-text-muted);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.meta-good {
  color: var(--eid-success);
}
.meta-bad {
  color: var(--eid-danger);
}
.meta-muted {
  color: var(--eid-text-muted);
}
.meta-url {
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  margin-left: auto;
}
.hint {
  font-size: 12px;
  color: var(--eid-text-muted);
  padding: 4px 0;
}
.prog-name {
  font-weight: 500;
}
.prog-meta {
  font-size: 11px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
}
.err {
  color: var(--eid-danger);
  font-size: 12px;
}
.muted {
  color: var(--eid-text-muted);
  font-size: 12px;
}
</style>
