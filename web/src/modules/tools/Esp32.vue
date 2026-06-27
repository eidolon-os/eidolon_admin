<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CircleClose,
  Connection,
  Cpu,
  Delete,
  Promotion,
  Refresh,
  Tools,
  VideoPlay,
  Warning,
} from '@element-plus/icons-vue'
import { useEventStream } from '@/components/useEventStream'
import {
  cancelEsp32Job,
  createEsp32Job,
  esp32JobStreamUrl,
  esp32SerialStreamUrl,
  getEsp32BoardInfo,
  getEsp32Environment,
  listEsp32Boards,
  listEsp32Jobs,
  listEsp32Ports,
  type Esp32Action,
  type Esp32BoardInfo,
  type Esp32BoardProfile,
  type Esp32Capability,
  type Esp32EnvironmentStatus,
  type Esp32Job,
  type Esp32Port,
} from '@/api/esp32Tools'
import { listDevices, sendCommand, type AdminDevice } from '@/api/hub'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

const boards = ref<Esp32BoardProfile[]>([])
const ports = ref<Esp32Port[]>([])
const environment = ref<Esp32EnvironmentStatus | null>(null)
const boardInfo = ref<Esp32BoardInfo | null>(null)
const jobs = ref<Esp32Job[]>([])
const hubDevices = ref<AdminDevice[]>([])
const selectedBoardId = ref(localStorage.getItem('eidolon-admin.esp32.board') || '')
const selectedPort = ref(localStorage.getItem('eidolon-admin.esp32.port') || '')
const baud = ref(Number(localStorage.getItem('eidolon-admin.esp32.baud') || 115200))
const activeTab = ref<'prepare' | 'actions' | 'serial' | 'history'>('prepare')
const loading = ref(false)
const actionBusy = ref(false)
const error = ref('')
const currentJob = ref<Esp32Job | null>(null)
const jobLogPane = ref<HTMLElement | null>(null)
const serialPane = ref<HTMLElement | null>(null)
const drawerOpen = ref(false)
const drawerJob = ref<Esp32Job | null>(null)
const serialFilter = ref('')
const serialFollow = ref(true)
const jobFollow = ref(true)

let pollTimer: ReturnType<typeof setInterval> | null = null

const jobStream = useEventStream({ maxLines: 2000 })
const serialStream = useEventStream({ maxLines: 3000 })
const drawerStream = useEventStream({ maxLines: 5000 })

const selectedBoard = computed(() =>
  boards.value.find((board) => board.id === selectedBoardId.value) || boards.value[0] || null,
)

const capabilityMap = computed(() => {
  const map = new Map<Esp32Action, Esp32Capability>()
  for (const cap of selectedBoard.value?.capabilities || []) map.set(cap.action, cap)
  return map
})

const selectedBoardEnv = computed(() =>
  environment.value?.boards.find((board) => board.id === selectedBoardId.value) || null,
)

const hasPort = computed(() => !!selectedPort.value)
const runningJob = computed(() =>
  jobs.value.find((job) => job.status === 'queued' || job.status === 'running') || null,
)
const contextReady = computed(() => {
  if (!selectedBoard.value) return false
  const env = selectedBoardEnv.value
  return !!env?.script_exists && !!env?.partition_csv_exists
})
const environmentTag = computed(() => {
  if (!environment.value) return { type: 'info' as const, text: '未检查' }
  if (!environment.value.client_root_exists || !selectedBoardEnv.value?.script_exists) {
    return { type: 'danger' as const, text: '脚本缺失' }
  }
  if (!environment.value.idf_available || !environment.value.esptool_available) {
    return { type: 'warning' as const, text: '环境不完整' }
  }
  return { type: 'success' as const, text: '就绪' }
})

const visibleSerialLines = computed(() => {
  const q = serialFilter.value.trim().toLowerCase()
  const lines = serialStream.lines.value.slice(-600)
  return q ? lines.filter((line) => line.toLowerCase().includes(q)) : lines
})

const actionGroups: Array<{ title: string; hint: string; actions: Esp32Action[] }> = [
  { title: '常用流程', hint: '日常固件迭代优先从这里走', actions: ['build', 'flash', 'run'] },
  { title: '串口调试', hint: '看日志、确认启动和运行状态', actions: ['monitor'] },
  { title: '维护', hint: '处理脏 build 或只烧录部分产物', actions: ['clean', 'build_clean', 'flash_app', 'flash_assets'] },
  { title: '诊断', hint: '不改变设备状态的检查', actions: ['diagnose', 'chip_id', 'flash_id'] },
]
const dangerousActions: Esp32Action[] = ['erase_nvs', 'erase_config', 'erase_assets', 'erase_flash']

onMounted(async () => {
  await refreshAll()
  pollTimer = setInterval(() => {
    if (!loading.value) void refreshLight()
  }, 3000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  jobStream.close()
  serialStream.close()
  drawerStream.close()
})

watch(selectedBoardId, async (boardId) => {
  if (boardId) localStorage.setItem('eidolon-admin.esp32.board', boardId)
  await loadBoardInfo()
})

watch(selectedPort, (port) => {
  if (port) localStorage.setItem('eidolon-admin.esp32.port', port)
})

watch(baud, (value) => {
  localStorage.setItem('eidolon-admin.esp32.baud', String(value))
})

watch(jobStream.lines, async () => {
  if (!jobFollow.value) return
  await nextTick()
  jobLogPane.value?.scrollTo({ top: jobLogPane.value.scrollHeight })
})

watch(serialStream.lines, async () => {
  if (!serialFollow.value) return
  await nextTick()
  serialPane.value?.scrollTo({ top: serialPane.value.scrollHeight })
})

async function refreshAll() {
  loading.value = true
  error.value = ''
  try {
    const [nextBoards, nextPorts, nextEnvironment, nextJobs, nextDevices] = await Promise.all([
      listEsp32Boards(),
      listEsp32Ports(),
      getEsp32Environment(),
      listEsp32Jobs(),
      listDevices().catch(() => []),
    ])
    boards.value = nextBoards
    ports.value = nextPorts
    environment.value = nextEnvironment
    jobs.value = nextJobs
    hubDevices.value = nextDevices.filter((device) => device.kind === 'esp32' || device.device_id.startsWith('esp32'))
    ensureDefaults()
    await loadBoardInfo()
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
  } finally {
    loading.value = false
  }
}

async function refreshLight() {
  try {
    const [nextJobs, nextDevices] = await Promise.all([
      listEsp32Jobs(),
      listDevices().catch(() => []),
    ])
    jobs.value = nextJobs
    hubDevices.value = nextDevices.filter((device) => device.kind === 'esp32' || device.device_id.startsWith('esp32'))
    if (currentJob.value) {
      currentJob.value = nextJobs.find((job) => job.id === currentJob.value?.id) || currentJob.value
    }
  } catch {
    // Polling stays quiet; explicit refresh surfaces errors.
  }
}

function ensureDefaults() {
  if (!selectedBoardId.value || !boards.value.some((board) => board.id === selectedBoardId.value)) {
    selectedBoardId.value = boards.value[0]?.id || ''
  }
  if (!selectedPort.value || !ports.value.some((port) => port.path === selectedPort.value)) {
    selectedPort.value = ports.value.find((port) => port.selected)?.path || ports.value[0]?.path || ''
  }
  if (!baud.value && selectedBoard.value) baud.value = selectedBoard.value.default_baud
}

async function loadBoardInfo() {
  if (!selectedBoardId.value) {
    boardInfo.value = null
    return
  }
  try {
    boardInfo.value = await getEsp32BoardInfo(selectedBoardId.value)
  } catch (err: unknown) {
    error.value = extractErrorMessage(err)
    boardInfo.value = null
  }
}

function canRun(action: Esp32Action) {
  const cap = capabilityMap.value.get(action)
  if (!cap || actionBusy.value) return false
  if (runningJob.value && action !== 'monitor') return false
  if (!contextReady.value && action !== 'diagnose') return false
  if (cap.requires_port && !hasPort.value) return false
  return true
}

async function startJob(action: Esp32Action) {
  const board = selectedBoard.value
  const cap = capabilityMap.value.get(action)
  if (!board || !cap || !canRun(action)) return
  let confirmToken: string | null = null
  if (cap.dangerous) {
    try {
      const result = await ElMessageBox.prompt(
        dangerMessage(action),
        cap.label,
        {
          inputPlaceholder: cap.confirm_token || '',
          confirmButtonText: '确认执行',
          cancelButtonText: '取消',
          type: 'warning',
          inputValidator: (value) => value === cap.confirm_token || `请输入 ${cap.confirm_token}`,
        },
      )
      confirmToken = result.value
    } catch {
      return
    }
  }
  actionBusy.value = true
  try {
    const job = await createEsp32Job({
      board_id: board.id,
      action,
      port: cap.requires_port ? selectedPort.value : null,
      baud: baud.value,
      confirm_token: confirmToken,
    })
    currentJob.value = job
    activeTab.value = action === 'monitor' ? 'serial' : 'actions'
    jobStream.clear()
    jobStream.open(esp32JobStreamUrl(job.id))
    await refreshLight()
    ElMessage.success(`任务已启动：${actionLabel(action)}`)
  } catch (err: unknown) {
    ElMessage.error(extractErrorMessage(err))
  } finally {
    actionBusy.value = false
  }
}

async function cancelCurrentJob() {
  if (!currentJob.value) return
  try {
    currentJob.value = await cancelEsp32Job(currentJob.value.id)
    await refreshLight()
  } catch (err: unknown) {
    ElMessage.error(extractErrorMessage(err))
  }
}

function startSerial() {
  if (!selectedBoard.value || !selectedPort.value) return
  activeTab.value = 'serial'
  serialStream.clear()
  serialStream.open(esp32SerialStreamUrl(selectedBoard.value.id, selectedPort.value, baud.value))
}

function stopSerial() {
  serialStream.close()
}

function openJobLog(job: Esp32Job) {
  drawerJob.value = job
  drawerOpen.value = true
  drawerStream.clear()
  drawerStream.open(esp32JobStreamUrl(job.id))
}

function closeDrawer() {
  drawerOpen.value = false
  drawerStream.close()
}

async function sendDeviceOp(device: AdminDevice, op: 'config.refresh' | 'playback.stop' | 'room.join') {
  try {
    await sendCommand(device.device_id, {
      topic: 'eidolon.control',
      op,
      payload: { source: 'admin_esp32_tools' },
    } as any)
    ElMessage.success(`已发送 ${op}`)
  } catch (err: unknown) {
    ElMessage.error(extractErrorMessage(err))
  }
}

function actionLabel(action: Esp32Action) {
  return capabilityMap.value.get(action)?.label || action
}

function statusType(status?: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'succeeded' || status === 'online') return 'success'
  if (status === 'running' || status === 'queued' || status === 'degraded') return 'warning'
  if (status === 'failed' || status === 'offline') return 'danger'
  return 'info'
}

function dangerMessage(action: Esp32Action) {
  if (action === 'erase_nvs') {
    return '这会擦除 NVS，也就是首版定义的长期记忆/本机配置分区：设备身份、Wi-Fi、激活/绑定状态都会被清空。'
  }
  if (action === 'erase_flash') return '这会擦除整片 Flash，设备需要重新烧录后才能启动。'
  if (action === 'erase_assets') return '这会擦除 assets 分区，模型、主题、资源文件可能丢失。'
  return '这会擦除设备持久配置，请确认设备和串口选择无误。'
}

function failureHint(job: Esp32Job | null) {
  if (!job || job.status !== 'failed') return ''
  if (job.action === 'build' || job.action === 'build_clean') {
    return '建议：查看完整日志；如果 CMake/cache 异常，先执行“清理后编译”。'
  }
  if (job.action.includes('flash') || job.action.includes('erase')) {
    return '建议：确认串口没有被占用、设备处于下载模式、端口选择正确，然后重试。'
  }
  return '建议：查看完整日志后重试，或先运行环境诊断。'
}

function sizeText(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="esp32-page">
    <header class="context-bar">
      <div class="selector">
        <span>板型</span>
        <el-select v-model="selectedBoardId" filterable>
          <el-option
            v-for="board in boards"
            :key="board.id"
            :label="board.label"
            :value="board.id"
          />
        </el-select>
      </div>
      <div class="selector">
        <span>串口</span>
        <el-select v-model="selectedPort" filterable allow-create placeholder="未检测到串口">
          <el-option v-for="port in ports" :key="port.path" :label="port.path" :value="port.path" />
        </el-select>
      </div>
      <div class="baud">
        <span>Baud</span>
        <el-input-number v-model="baud" :min="1" :max="2000000" :step="9600" controls-position="right" />
      </div>
      <el-tag :type="environmentTag.type" effect="dark">{{ environmentTag.text }}</el-tag>
      <el-tag v-if="runningJob" :type="statusType(runningJob.status)" effect="plain">
        {{ actionLabel(runningJob.action) }} · {{ runningJob.status }}
      </el-tag>
      <el-button :icon="Refresh" :loading="loading" @click="refreshAll">刷新</el-button>
    </header>

    <el-alert
      v-if="error"
      class="top-alert"
      type="error"
      :title="error"
      show-icon
      :closable="false"
    />

    <el-tabs v-model="activeTab" class="tabs">
      <el-tab-pane label="准备" name="prepare">
        <div class="prepare-grid">
          <section class="panel">
            <div class="panel-head">
              <h2>工作上下文</h2>
              <el-tag :type="contextReady ? 'success' : 'warning'" effect="dark">
                {{ contextReady ? '可执行' : '需检查' }}
              </el-tag>
            </div>
            <dl class="facts">
              <dt>Board</dt><dd>{{ selectedBoard?.label || '-' }}</dd>
              <dt>Vendor</dt><dd>{{ selectedBoard?.vendor || '-' }}</dd>
              <dt>Target</dt><dd>{{ selectedBoard?.target || '-' }}</dd>
              <dt>Board Type</dt><dd class="mono">{{ selectedBoard?.board_type || '-' }}</dd>
              <dt>Script</dt><dd class="mono">{{ selectedBoard?.script_path || '-' }}</dd>
              <dt>Client Root</dt><dd class="mono">{{ environment?.client_root || '-' }}</dd>
            </dl>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>环境诊断</h2>
              <el-button size="small" :icon="Tools" @click="startJob('diagnose')">运行诊断</el-button>
            </div>
            <div class="check-list">
              <div class="check-row">
                <span>ESP-IDF</span>
                <el-tag :type="environment?.idf_available ? 'success' : 'warning'" size="small">
                  {{ environment?.idf_available ? 'detected' : 'not found' }}
                </el-tag>
              </div>
              <div v-if="environment?.idf_export_path" class="tool-path">
                export.sh <span class="mono">{{ environment.idf_export_path }}</span>
              </div>
              <div v-if="environment?.idf_py_path" class="tool-path">
                idf.py <span class="mono">{{ environment.idf_py_path }}</span>
              </div>
              <div class="check-row">
                <span>esptool</span>
                <el-tag :type="environment?.esptool_available ? 'success' : 'warning'" size="small">
                  {{ environment?.esptool_available ? 'detected' : 'not found' }}
                </el-tag>
              </div>
              <div v-if="environment?.esptool_path" class="tool-path">
                esptool <span class="mono">{{ environment.esptool_path }}</span>
              </div>
              <div class="check-row">
                <span>脚本</span>
                <el-tag :type="selectedBoardEnv?.script_exists ? 'success' : 'danger'" size="small">
                  {{ selectedBoardEnv?.script_exists ? 'exists' : 'missing' }}
                </el-tag>
              </div>
              <div class="check-row">
                <span>分区表</span>
                <el-tag :type="selectedBoardEnv?.partition_csv_exists ? 'success' : 'danger'" size="small">
                  {{ selectedBoardEnv?.partition_csv_exists ? 'exists' : 'missing' }}
                </el-tag>
              </div>
            </div>
            <p v-if="environment?.warnings.length" class="hint">
              {{ environment.warnings.join(' · ') }}
            </p>
          </section>

          <section class="panel">
            <div class="panel-head"><h2>分区</h2></div>
            <el-table :data="boardInfo?.partitions || []" size="small" height="260">
              <el-table-column prop="name" label="Name" width="110" />
              <el-table-column prop="offset" label="Offset" width="110" />
              <el-table-column prop="size" label="Size" />
            </el-table>
          </section>

          <section class="panel">
            <div class="panel-head"><h2>产物</h2></div>
            <div class="artifact-list">
              <div v-if="!boardInfo?.artifacts.length" class="empty">暂无 build 产物</div>
              <div v-for="artifact in boardInfo?.artifacts || []" :key="artifact.path" class="artifact">
                <span class="mono">{{ artifact.name }}</span>
                <small>{{ sizeText(artifact.size) }}</small>
              </div>
            </div>
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="操作" name="actions">
        <div class="actions-layout">
          <section class="panel action-panel">
            <div v-for="group in actionGroups" :key="group.title" class="action-group">
              <header>
                <div>
                  <h2>{{ group.title }}</h2>
                  <p>{{ group.hint }}</p>
                </div>
              </header>
              <div class="button-grid">
                <el-button
                  v-for="action in group.actions"
                  :key="action"
                  :type="action === 'run' ? 'primary' : 'default'"
                  :icon="action === 'monitor' ? Connection : action === 'run' ? Promotion : VideoPlay"
                  :disabled="!canRun(action)"
                  :loading="actionBusy"
                  @click="action === 'monitor' ? startSerial() : startJob(action)"
                >
                  {{ actionLabel(action) }}
                </el-button>
              </div>
            </div>

            <el-collapse class="danger-collapse">
              <el-collapse-item name="danger">
                <template #title>
                  <span class="danger-title"><el-icon><Warning /></el-icon> 危险操作</span>
                </template>
                <div class="button-grid danger-grid">
                  <el-button
                    v-for="action in dangerousActions"
                    :key="action"
                    :icon="Delete"
                    type="danger"
                    plain
                    :disabled="!canRun(action)"
                    @click="startJob(action)"
                  >
                    {{ actionLabel(action) }}
                  </el-button>
                </div>
              </el-collapse-item>
            </el-collapse>
          </section>

          <section class="panel live-panel">
            <div class="panel-head">
              <h2>当前任务</h2>
              <div class="panel-actions">
                <el-button v-if="currentJob && ['queued', 'running'].includes(currentJob.status)" size="small" :icon="CircleClose" @click="cancelCurrentJob">
                  取消
                </el-button>
                <el-checkbox v-model="jobFollow" size="small">跟随</el-checkbox>
              </div>
            </div>
            <div v-if="currentJob" class="job-summary">
              <el-tag :type="statusType(currentJob.status)" effect="dark">{{ currentJob.status }}</el-tag>
              <span>{{ actionLabel(currentJob.action) }}</span>
              <small class="mono">{{ currentJob.id }}</small>
            </div>
            <el-alert v-if="failureHint(currentJob)" type="warning" :title="failureHint(currentJob)" show-icon :closable="false" />
            <pre ref="jobLogPane" class="log-pane">{{ jobStream.lines.value.slice(-200).join('\n') }}</pre>
          </section>
        </div>
      </el-tab-pane>

      <el-tab-pane label="串口" name="serial">
        <section class="panel serial-panel">
          <div class="panel-head">
            <h2>串口日志</h2>
            <div class="serial-tools">
              <el-tag :type="serialStream.connected.value ? 'success' : 'info'" effect="dark">
                {{ serialStream.connected.value ? '已连接' : '未连接' }}
              </el-tag>
              <el-input v-model="serialFilter" placeholder="过滤日志" clearable />
              <el-checkbox v-model="serialFollow" size="small">跟随</el-checkbox>
              <el-button :icon="Connection" :disabled="!hasPort || !selectedBoard" @click="startSerial">开始监控</el-button>
              <el-button :icon="CircleClose" @click="stopSerial">停止</el-button>
              <el-button @click="serialStream.clear">清空</el-button>
            </div>
          </div>
          <pre ref="serialPane" class="log-pane serial-log">{{ visibleSerialLines.join('\n') }}</pre>
        </section>
      </el-tab-pane>

      <el-tab-pane label="历史" name="history">
        <section class="panel">
          <div class="panel-head">
            <h2>任务历史</h2>
            <el-button size="small" :icon="Refresh" @click="refreshLight">刷新</el-button>
          </div>
          <el-table :data="jobs" stripe>
            <el-table-column label="Action" width="170">
              <template #default="{ row }">{{ actionLabel(row.action) }}</template>
            </el-table-column>
            <el-table-column label="Board" width="250" prop="board_id" />
            <el-table-column label="Status" width="120">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small" effect="dark">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Started" width="190">
              <template #default="{ row }">{{ row.started_at ? formatTimestamp(row.started_at) : '-' }}</template>
            </el-table-column>
            <el-table-column label="Error" min-width="180" prop="error" show-overflow-tooltip />
            <el-table-column label="操作" width="180">
              <template #default="{ row }">
                <el-button size="small" link @click="openJobLog(row)">日志</el-button>
                <el-button size="small" link :disabled="!canRun(row.action)" @click="startJob(row.action)">重试</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section class="panel hub-panel">
          <div class="panel-head">
            <h2>Hub 在线设备</h2>
            <span class="hint">USB 串口设备和 Hub 设备首版并排展示，不自动绑定。</span>
          </div>
          <el-table :data="hubDevices" stripe>
            <el-table-column label="Device" min-width="180" prop="device_id" />
            <el-table-column label="Status" width="110">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Room" min-width="160" prop="room_name" />
            <el-table-column label="Last Seen" width="180">
              <template #default="{ row }">{{ row.last_seen_at || row.last_seen ? formatTimestamp(row.last_seen_at || row.last_seen) : '-' }}</template>
            </el-table-column>
            <el-table-column label="控制" width="270">
              <template #default="{ row }">
                <el-button size="small" @click="sendDeviceOp(row, 'config.refresh')">刷新配置</el-button>
                <el-button size="small" @click="sendDeviceOp(row, 'playback.stop')">停止播放</el-button>
                <el-button size="small" @click="sendDeviceOp(row, 'room.join')">入房</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </el-tab-pane>
    </el-tabs>

    <el-drawer
      :model-value="drawerOpen"
      :title="drawerJob ? `ESP32 Job · ${drawerJob.id}` : 'ESP32 Job'"
      size="62%"
      direction="rtl"
      @update:model-value="(v: boolean) => { if (!v) closeDrawer() }"
    >
      <pre class="log-pane drawer-log">{{ drawerStream.lines.value.join('\n') }}</pre>
    </el-drawer>
  </div>
</template>

<style scoped>
.esp32-page {
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
  grid-template-columns: minmax(240px, 1.2fr) minmax(220px, 1fr) 170px auto auto auto;
  gap: 12px;
  align-items: end;
  padding: 12px;
  background: color-mix(in srgb, var(--eid-bg-panel) 92%, transparent);
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  backdrop-filter: blur(10px);
}
.selector,
.baud {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.selector span,
.baud span {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.tabs {
  min-width: 0;
}
.top-alert {
  margin: 0;
}
.prepare-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.8fr);
  gap: 14px;
}
.actions-layout {
  display: grid;
  grid-template-columns: minmax(360px, 0.95fr) minmax(420px, 1.05fr);
  gap: 14px;
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
.panel-head h2,
.action-group h2 {
  margin: 0;
  color: var(--eid-text-primary);
  font-size: 15px;
  font-weight: 720;
}
.panel-actions,
.serial-tools {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.serial-tools .el-input {
  width: 220px;
}
.facts {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
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
.check-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.check-row,
.artifact,
.job-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.tool-path {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 7px 9px;
  color: var(--eid-text-muted);
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  border-radius: 6px;
  font-size: 11px;
}
.tool-path span {
  color: var(--eid-text-primary);
  overflow-wrap: anywhere;
}
.hint {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.artifact-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
}
.artifact {
  padding: 8px 10px;
  border: 1px solid var(--eid-border);
  border-radius: 6px;
  background: var(--eid-bg-inset);
}
.artifact small,
.job-summary small {
  color: var(--eid-text-muted);
}
.empty {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.action-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.action-group {
  padding-bottom: 14px;
  border-bottom: 1px solid var(--eid-border);
}
.action-group header {
  margin-bottom: 10px;
}
.action-group p {
  margin: 4px 0 0;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.button-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.button-grid .el-button {
  width: 100%;
  margin-left: 0;
}
.danger-collapse {
  border-top: 0;
  border-bottom: 0;
}
.danger-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--el-color-danger);
}
.danger-grid {
  padding-top: 8px;
}
.live-panel {
  display: flex;
  flex-direction: column;
  min-height: 520px;
}
.serial-panel {
  min-height: 620px;
}
.log-pane {
  flex: 1;
  min-height: 360px;
  max-height: 58vh;
  padding: 12px;
  margin: 0;
  overflow: auto;
  color: var(--eid-text-primary);
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border-strong);
  border-radius: 6px;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
.serial-log {
  min-height: 540px;
}
.drawer-log {
  height: 100%;
  max-height: none;
}
.hub-panel {
  margin-top: 14px;
}
@media (max-width: 1180px) {
  .context-bar,
  .prepare-grid,
  .actions-layout {
    grid-template-columns: 1fr;
  }
  .serial-tools {
    flex-wrap: wrap;
  }
  .serial-tools .el-input {
    width: 100%;
  }
}
</style>
