<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Brush,
  Connection,
  Cpu,
  Download,
  Refresh,
  SwitchButton,
  Tools,
  VideoPlay,
} from '@element-plus/icons-vue'
import { useEventStream } from '@/components/useEventStream'
import {
  cancelMobileJob,
  createMobileJob,
  getMobileEnvironment,
  listMobileDevices,
  listMobileJobs,
  mobileJobStreamUrl,
  mobileLogStreamUrl,
  type MobileAction,
  type MobileBuildMode,
  type MobileDevice,
  type MobileEnvironmentStatus,
  type MobileJob,
} from '@/api/mobileTools'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

const devices = ref<MobileDevice[]>([])
const environment = ref<MobileEnvironmentStatus | null>(null)
const jobs = ref<MobileJob[]>([])
const selectedSerial = ref(localStorage.getItem('eidolon-admin.mobile.serial') || '')
const buildMode = ref<MobileBuildMode>(
  (localStorage.getItem('eidolon-admin.mobile.mode') as MobileBuildMode) || 'debug',
)
const loading = ref(false)
const actionBusy = ref(false)
const error = ref('')
const currentJob = ref<MobileJob | null>(null)
const logFilter = ref('')
const logFollow = ref(true)
const jobFollow = ref(true)
const jobPane = ref<HTMLElement | null>(null)
const logPane = ref<HTMLElement | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null
const jobStream = useEventStream({ maxLines: 3000 })
const adbStream = useEventStream({ maxLines: 5000 })

const selectedDevice = computed(() =>
  devices.value.find((device) => device.serial === selectedSerial.value) || null,
)
const runningJob = computed(() =>
  jobs.value.find((job) => job.status === 'queued' || job.status === 'running') || null,
)
const environmentReady = computed(() => {
  const env = environment.value
  return !!env?.client_root_exists && !!env?.script_exists && !!env?.flutter_available && !!env?.java_available && !!env?.adb_available
})
const visibleLogLines = computed(() => {
  const query = logFilter.value.trim().toLowerCase()
  const lines = adbStream.lines.value.slice(-1000)
  return query ? lines.filter((line) => line.toLowerCase().includes(query)) : lines
})

onMounted(async () => {
  await refreshAll()
  pollTimer = setInterval(() => {
    if (!loading.value) void refreshLight()
  }, 3000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  jobStream.close()
  adbStream.close()
})

watch(selectedSerial, (serial) => {
  if (serial) localStorage.setItem('eidolon-admin.mobile.serial', serial)
  if (adbStream.connected.value) adbStream.close()
})

watch(buildMode, async (mode) => {
  localStorage.setItem('eidolon-admin.mobile.mode', mode)
  await loadEnvironment()
})

watch(jobStream.lines, async () => {
  if (!jobFollow.value) return
  await nextTick()
  jobPane.value?.scrollTo({ top: jobPane.value.scrollHeight })
})

watch(adbStream.lines, async () => {
  if (!logFollow.value) return
  await nextTick()
  logPane.value?.scrollTo({ top: logPane.value.scrollHeight })
})

async function refreshAll() {
  loading.value = true
  error.value = ''
  try {
    const [nextDevices, nextEnvironment, nextJobs] = await Promise.all([
      listMobileDevices(),
      getMobileEnvironment(buildMode.value),
      listMobileJobs(),
    ])
    devices.value = nextDevices
    environment.value = nextEnvironment
    jobs.value = nextJobs
    ensureDevice()
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    loading.value = false
  }
}

async function refreshLight() {
  try {
    const [nextDevices, nextJobs] = await Promise.all([
      listMobileDevices(),
      listMobileJobs(),
    ])
    devices.value = nextDevices
    jobs.value = nextJobs
    ensureDevice()
    if (currentJob.value) {
      currentJob.value = nextJobs.find((job) => job.id === currentJob.value?.id) || currentJob.value
    }
  } catch {
    // Background polling stays quiet.
  }
}

async function loadEnvironment() {
  try {
    environment.value = await getMobileEnvironment(buildMode.value)
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  }
}

function ensureDevice() {
  if (!devices.value.some((device) => device.serial === selectedSerial.value)) {
    selectedSerial.value =
      devices.value.find((device) => device.selected)?.serial || devices.value[0]?.serial || ''
  }
}

function canRun(action: MobileAction) {
  if (actionBusy.value || runningJob.value) return false
  if (!environment.value?.script_exists) return false
  if (action === 'build' || action === 'diagnose') return environmentReady.value
  if (!selectedDevice.value || selectedDevice.value.state !== 'device') return false
  if (action === 'install' || action === 'reinstall') return !!environment.value?.apk_exists
  return true
}

async function startJob(action: MobileAction, skipBuild = false) {
  if (!canRun(action)) return
  if (action === 'reinstall') {
    try {
      await ElMessageBox.confirm(
        '这会卸载应用并清除应用数据和 AndroidKeyStore 私钥。稳定 Device ID 不会变化，但 Hub 会要求在原设备记录上重新批准新密钥。',
        '确认干净重装',
        {
          confirmButtonText: '卸载并重新安装',
          cancelButtonText: '取消',
          type: 'warning',
        },
      )
    } catch {
      return
    }
  }
  actionBusy.value = true
  try {
    const requiresDevice = !['build', 'diagnose'].includes(action)
    const job = await createMobileJob({
      action,
      serial: requiresDevice ? selectedSerial.value : null,
      mode: buildMode.value,
      skip_build: skipBuild,
    })
    currentJob.value = job
    jobStream.clear()
    jobStream.open(mobileJobStreamUrl(job.id))
    ElMessage.success(`任务已启动：${actionLabel(action)}`)
    await refreshLight()
  } catch (err: unknown) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    actionBusy.value = false
  }
}

async function cancelCurrentJob() {
  if (!currentJob.value) return
  try {
    currentJob.value = await cancelMobileJob(currentJob.value.id)
    await refreshLight()
  } catch (err: unknown) {
    ElMessage.error(extractErrorMessage(err))
  }
}

function startLogs() {
  if (!selectedDevice.value?.app_running) {
    ElMessage.warning('客户端尚未运行，请先执行“重启客户端”')
    return
  }
  adbStream.clear()
  adbStream.open(mobileLogStreamUrl(selectedDevice.value.serial))
}

function stopLogs() {
  adbStream.close()
}

function actionLabel(action: MobileAction) {
  return environment.value?.capabilities.find((cap) => cap.action === action)?.label || action
}

function statusType(status?: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'succeeded' || status === 'device') return 'success'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'failed' || status === 'offline') return 'danger'
  return 'info'
}

function lineClass(line: string) {
  const lower = line.toLowerCase()
  if (lower.includes('error') || lower.includes('failed') || lower.includes('exception')) return 'log-line error-line'
  if (lower.includes('warn')) return 'log-line warn-line'
  if (lower.startsWith('>>')) return 'log-line command-line'
  return 'log-line'
}
</script>

<template>
  <div class="mobile-page">
    <header class="context-bar">
      <div class="selector">
        <span>Android 设备</span>
        <el-select v-model="selectedSerial" placeholder="未检测到 ADB 设备">
          <el-option
            v-for="device in devices"
            :key="device.serial"
            :label="`${device.model || device.device || 'Android'} · ${device.serial}`"
            :value="device.serial"
            :disabled="device.state !== 'device'"
          />
        </el-select>
      </div>
      <div class="selector mode-selector">
        <span>构建模式</span>
        <el-select v-model="buildMode">
          <el-option label="Debug" value="debug" />
          <el-option label="Profile" value="profile" />
          <el-option label="Release" value="release" />
        </el-select>
      </div>
      <el-tag :type="environmentReady ? 'success' : 'warning'" effect="dark">
        {{ environmentReady ? '环境就绪' : '环境需检查' }}
      </el-tag>
      <el-tag :type="statusType(selectedDevice?.state)" effect="plain">
        {{ selectedDevice?.state || '未连接' }}
      </el-tag>
      <el-tag v-if="selectedDevice?.app_running" type="success" effect="plain">
        App · PID {{ selectedDevice.app_pid }}
      </el-tag>
      <el-button :icon="Refresh" :loading="loading" @click="refreshAll">刷新</el-button>
    </header>

    <el-alert
      v-if="error"
      type="error"
      :title="error"
      show-icon
      :closable="false"
    />

    <div class="overview-grid">
      <section class="panel">
        <div class="panel-head">
          <h2>设备上下文</h2>
          <el-tag :type="selectedDevice?.app_running ? 'success' : 'info'" size="small">
            {{ selectedDevice?.app_running ? '客户端运行中' : '客户端未运行' }}
          </el-tag>
        </div>
        <dl class="facts">
          <dt>Model</dt><dd>{{ selectedDevice?.model || '-' }}</dd>
          <dt>ADB Serial</dt><dd class="mono">{{ selectedDevice?.serial || '-' }}</dd>
          <dt>Product</dt><dd class="mono">{{ selectedDevice?.product || '-' }}</dd>
          <dt>Android ID</dt><dd class="mono">{{ selectedDevice?.android_id || '-' }}</dd>
          <dt>Eidolon ID</dt><dd class="mono stable-id">{{ selectedDevice?.eidolon_device_id || '-' }}</dd>
          <dt>App Process</dt><dd>{{ selectedDevice?.app_running ? `PID ${selectedDevice.app_pid}` : 'stopped' }}</dd>
        </dl>
        <el-alert
          class="identity-note"
          type="info"
          title="Eidolon ID 对同一 Android 用户和 APK 签名稳定；干净重装不会在 Hub 创建新设备。"
          :closable="false"
        />
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>工具链与产物</h2>
          <el-tag :type="environment?.apk_exists ? 'success' : 'info'" size="small">
            {{ environment?.apk_exists ? 'APK 已生成' : '尚未编译 APK' }}
          </el-tag>
        </div>
        <dl class="facts">
          <dt>Client Root</dt><dd class="mono">{{ environment?.client_root || '-' }}</dd>
          <dt>Script</dt><dd class="mono">{{ environment?.script_path || '-' }}</dd>
          <dt>Flutter</dt><dd class="mono">{{ environment?.flutter_path || '-' }}</dd>
          <dt>Android SDK</dt><dd class="mono">{{ environment?.android_sdk_root || '-' }}</dd>
          <dt>JDK</dt><dd class="mono">{{ environment?.java_home || '-' }}</dd>
          <dt>ADB</dt><dd class="mono">{{ environment?.adb_path || '-' }}</dd>
          <dt>APK</dt><dd class="mono">{{ environment?.apk_path || '-' }}</dd>
        </dl>
        <el-alert
          v-if="environment?.warnings.length"
          class="identity-note"
          type="warning"
          :title="environment.warnings.join('；')"
          :closable="false"
        />
      </section>
    </div>

    <div class="work-grid">
      <section class="panel">
        <div class="panel-head">
          <h2>Mobile 操作</h2>
          <el-tag v-if="runningJob" type="warning" size="small" effect="dark">
            {{ actionLabel(runningJob.action) }} · {{ runningJob.status }}
          </el-tag>
        </div>
        <div class="action-groups">
          <div class="action-group">
            <h3>构建与部署</h3>
            <p>日常迭代使用覆盖安装；需要验证首次安装状态时才使用干净重装。</p>
            <div class="buttons">
              <el-button :icon="Cpu" :disabled="!canRun('build')" @click="startJob('build')">编译 APK</el-button>
              <el-button type="primary" :icon="Download" :disabled="!canRun('install')" @click="startJob('install')">安装</el-button>
              <el-button type="primary" :icon="VideoPlay" :disabled="!canRun('run')" @click="startJob('run')">编译 + 安装 + 启动</el-button>
            </div>
          </div>
          <div class="action-group">
            <h3>设备控制</h3>
            <p>重启只操作客户端进程，不重启 Android 系统。</p>
            <div class="buttons">
              <el-button :icon="SwitchButton" :disabled="!canRun('restart')" @click="startJob('restart')">重启客户端</el-button>
              <el-button type="danger" plain :icon="Refresh" :disabled="!canRun('reinstall')" @click="startJob('reinstall')">重新安装</el-button>
              <el-button :icon="Tools" :disabled="!canRun('diagnose')" @click="startJob('diagnose')">环境诊断</el-button>
            </div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>当前任务</h2>
          <div class="panel-actions">
            <el-tag v-if="currentJob" :type="statusType(currentJob.status)" size="small" effect="dark">
              {{ currentJob.status }}
            </el-tag>
            <el-button
              v-if="currentJob && ['queued', 'running'].includes(currentJob.status)"
              size="small"
              type="danger"
              plain
              @click="cancelCurrentJob"
            >
              取消
            </el-button>
          </div>
        </div>
        <div v-if="currentJob" class="job-meta">
          <span>{{ actionLabel(currentJob.action) }}</span>
          <span class="mono">{{ currentJob.id }}</span>
          <span>{{ currentJob.serial || 'host' }}</span>
        </div>
        <div ref="jobPane" class="log-pane job-log">
          <div
            v-for="(line, index) in jobStream.lines.value"
            :key="`${index}-${line}`"
            :class="lineClass(line)"
          >
            {{ line }}
          </div>
          <div v-if="!jobStream.lines.value.length" class="empty-log">执行操作后在这里查看输出</div>
        </div>
        <el-checkbox v-model="jobFollow">自动滚动</el-checkbox>
      </section>
    </div>

    <section class="panel">
      <div class="panel-head">
        <h2>ADB 实时日志</h2>
        <div class="log-tools">
          <el-input v-model="logFilter" clearable placeholder="过滤日志" />
          <el-checkbox v-model="logFollow">自动滚动</el-checkbox>
          <el-button
            :icon="Connection"
            type="primary"
            :disabled="!selectedDevice?.app_running || adbStream.connected.value"
            @click="startLogs"
          >
            开始
          </el-button>
          <el-button :disabled="!adbStream.connected.value" @click="stopLogs">停止</el-button>
          <el-button :icon="Brush" :disabled="!canRun('clear_logs')" @click="startJob('clear_logs')">清空日志</el-button>
        </div>
      </div>
      <div ref="logPane" class="log-pane adb-log">
        <div
          v-for="(line, index) in visibleLogLines"
          :key="`${index}-${line}`"
          :class="lineClass(line)"
        >
          {{ line }}
        </div>
        <div v-if="!visibleLogLines.length" class="empty-log">选择运行中的 Android 客户端后开始查看 logcat</div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>任务历史</h2>
        <el-button size="small" :icon="Refresh" @click="refreshLight">刷新</el-button>
      </div>
      <el-table :data="jobs" stripe>
        <el-table-column label="Action" width="210">
          <template #default="{ row }">{{ actionLabel(row.action) }}</template>
        </el-table-column>
        <el-table-column label="Device" min-width="180" prop="serial" />
        <el-table-column label="Mode" width="100" prop="mode" />
        <el-table-column label="Status" width="120">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Started" width="190">
          <template #default="{ row }">{{ row.started_at ? formatTimestamp(row.started_at) : '-' }}</template>
        </el-table-column>
        <el-table-column label="Error" min-width="220" prop="error" show-overflow-tooltip />
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.mobile-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
}
.context-bar {
  position: sticky;
  top: 0;
  z-index: 6;
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 150px auto auto auto auto;
  gap: 12px;
  align-items: end;
  padding: 12px;
  background: color-mix(in srgb, var(--eid-bg-panel) 92%, transparent);
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  backdrop-filter: blur(10px);
}
.selector {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.selector span {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.overview-grid,
.work-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.work-grid {
  grid-template-columns: minmax(420px, 0.9fr) minmax(460px, 1.1fr);
}
.panel {
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.panel-head h2 {
  margin: 0;
  color: var(--eid-text-primary);
  font-size: 15px;
  font-weight: 720;
}
.panel-actions,
.log-tools,
.buttons,
.job-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
}
.log-tools .el-input {
  width: 230px;
}
.facts {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 9px 12px;
  margin: 0;
}
.facts dt {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.facts dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--eid-text-primary);
  font-size: 12px;
}
.mono {
  font-family: var(--eid-font-mono);
}
.stable-id {
  color: var(--el-color-success);
}
.identity-note {
  margin-top: 14px;
}
.action-groups {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.action-group {
  padding: 12px;
  border: 1px solid var(--eid-border);
  border-radius: 7px;
  background: var(--eid-bg-inset);
}
.action-group h3 {
  margin: 0 0 4px;
  color: var(--eid-text-primary);
  font-size: 14px;
}
.action-group p {
  margin: 0 0 12px;
  color: var(--eid-text-muted);
  font-size: 12px;
  line-height: 1.55;
}
.job-meta {
  margin-bottom: 8px;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.log-pane {
  overflow: auto;
  padding: 10px;
  color: #d7dce5;
  background: #090b10;
  border: 1px solid #272c36;
  border-radius: 7px;
  font: 12px/1.55 var(--eid-font-mono);
}
.job-log {
  height: 260px;
  margin-bottom: 8px;
}
.adb-log {
  height: 380px;
}
.log-line {
  min-height: 18px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.error-line {
  color: #ff8585;
}
.warn-line {
  color: #f6c86d;
}
.command-line {
  color: #74ded0;
}
.empty-log {
  display: grid;
  min-height: 100%;
  place-items: center;
  color: #737a87;
}
@media (max-width: 1100px) {
  .context-bar {
    grid-template-columns: minmax(240px, 1fr) 140px auto auto;
  }
  .overview-grid,
  .work-grid {
    grid-template-columns: 1fr;
  }
}
</style>
