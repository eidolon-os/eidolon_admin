<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Aim,
  ChatDotRound,
  CircleCheck,
  Connection,
  Cpu,
  DataAnalysis,
  Lock,
  MagicStick,
  Microphone,
  Monitor,
  Refresh,
  Timer,
  VideoCamera,
  Warning,
} from '@element-plus/icons-vue'
import { listOwners, type OwnerView } from '@/api/eidolonData'
import {
  getMissionControlSnapshot,
  missionControlEventsUrl,
  type RuntimeCapabilityCard,
  type RuntimeDevice,
  type RuntimeEvent,
  type RuntimeLane,
  type RuntimeSnapshot,
  type RuntimeStoryStep,
} from '@/api/missionControl'
import { formatTimestamp } from '@/utils/format'

const owners = ref<OwnerView[]>([])
const ownerId = ref('')
const snapshot = ref<RuntimeSnapshot | null>(null)
const liveEvents = ref<RuntimeEvent[]>([])
const constellationCanvas = ref<HTMLCanvasElement | null>(null)
const loading = ref(false)
const error = ref('')
const streamState = ref<'connecting' | 'live' | 'degraded'>('connecting')
let pollTimer: number | undefined
let stream: EventSource | null = null
let constellationFrame = 0
let constellationResize: ResizeObserver | undefined

const experience = computed(() => snapshot.value?.experience)
const ownerLabel = computed(() => snapshot.value?.owner?.display_name || snapshot.value?.owner?.owner_id || '未选择用户')
const companionLabel = computed(() => snapshot.value?.companion?.display_name || snapshot.value?.companion?.companion_id || '小忆')
const devices = computed(() => snapshot.value?.devices || [])
const services = computed(() => snapshot.value?.services || [])
const onlineDevices = computed(() => devices.value.filter((item) => item.online).length)
const onlineServices = computed(() => services.value.filter((item) => item.online).length)
const sourceStatus = computed(() => snapshot.value?.source_status || [])
const degradedSources = computed(() => sourceStatus.value.filter((item) => !item.ok))
const lanes = computed<RuntimeLane[]>(() => experience.value?.lanes || fallbackLanes.value)
const cards = computed<RuntimeCapabilityCard[]>(() => experience.value?.capability_cards || [])
const storyline = computed<RuntimeStoryStep[]>(() => experience.value?.storyline || [])
const agentService = computed(() => serviceById('agent'))
const memoryService = computed(() => serviceById('memory'))
const channelService = computed(() => serviceById('channel'))

const recentEvents = computed(() => {
  const merged = [...liveEvents.value, ...(snapshot.value?.recent_events || [])]
  const seen = new Set<string>()
  return merged.filter((event) => {
    if (seen.has(event.event_id)) return false
    seen.add(event.event_id)
    return true
  }).slice(0, 10)
})

const mainStateText = computed(() => {
  if (streamState.value === 'connecting') return '正在连接实时信号'
  if (streamState.value === 'degraded') return '部分信号暂时不可用'
  return '实时信号已连接'
})

const architectureNodes = computed(() => [
  {
    key: 'bodies',
    title: '身体层',
    metric: `${onlineDevices.value}/${devices.value.length}`,
    detail: '2.06 / BOX-3 / 视觉节点',
    status: onlineDevices.value > 0 ? 'done' : 'pending',
  },
  {
    key: 'channel',
    title: '感知通道',
    metric: channelService.value?.online ? '在线' : '待信号',
    detail: '语音房间 / STT / TTS',
    status: channelService.value?.online || snapshot.value?.active_turn ? 'done' : 'pending',
  },
  {
    key: 'agent',
    title: '小忆大脑',
    metric: agentService.value?.online ? '在线' : '待连接',
    detail: '理解 / 规划 / 工具',
    status: agentService.value?.online ? 'done' : 'degraded',
  },
  {
    key: 'memory',
    title: '主权记忆',
    metric: `${snapshot.value?.memory.last_recall_hits ?? 0}`,
    detail: `${snapshot.value?.memory.realms_total ?? 0} 个记忆空间`,
    status: memoryService.value?.online || (snapshot.value?.memory.realms_total || 0) > 0 ? 'done' : 'pending',
  },
  {
    key: 'actions',
    title: '行动调度',
    metric: `${snapshot.value?.jobs.length ?? 0}`,
    detail: '任务 / 设备控制 / 委托',
    status: (snapshot.value?.jobs.length || 0) > 0 ? 'done' : 'pending',
  },
  {
    key: 'guard',
    title: '权限边界',
    metric: degradedSources.value.length ? `${degradedSources.value.length}` : 'OK',
    detail: '授权 / 脱敏 / 审计',
    status: degradedSources.value.length ? 'degraded' : 'done',
  },
])

const cockpitStats = computed(() => [
  { label: '身体在线', value: `${onlineDevices.value}/${devices.value.length}` },
  { label: '核心服务', value: `${onlineServices.value}/${services.value.length}` },
  { label: '记忆命中', value: `${snapshot.value?.memory.last_recall_hits ?? 0}` },
])

const flowSteps = computed(() => {
  if (storyline.value.length) return storyline.value
  return [
    { key: 'body', title: '身体接入', detail: '等待身体信号', status: 'pending', source: 'hub', ts: null },
    { key: 'identity', title: '身份归一', detail: '等待 owner / companion', status: 'pending', source: 'data', ts: null },
    { key: 'turn', title: '小忆处理', detail: '等待交互', status: 'pending', source: 'agent', ts: null },
    { key: 'memory', title: '记忆参与', detail: '等待记忆链路', status: 'pending', source: 'memory', ts: null },
    { key: 'tools', title: '行动调度', detail: '等待行动', status: 'pending', source: 'agent', ts: null },
    { key: 'permission', title: '权限可见', detail: '等待高敏能力调用', status: 'pending', source: 'hub', ts: null },
  ]
})

const fallbackLanes = computed<RuntimeLane[]>(() => [
  {
    key: 'body',
    title: '身体网络',
    headline: `${onlineDevices.value}/${devices.value.length} 个身体在线`,
    detail: '硬件身体会在这里出现，显示小忆从哪里听、从哪里说。',
    status: onlineDevices.value ? 'done' : 'pending',
    items: [],
  },
])

function serviceById(id: string) {
  return services.value.find((service) => service.service_id === id) || null
}

onMounted(async () => {
  await loadOwners()
  await refresh()
  openStream()
  await nextTick()
  startConstellation()
  pollTimer = window.setInterval(refresh, 8000)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  if (stream) stream.close()
  if (constellationFrame) window.cancelAnimationFrame(constellationFrame)
  constellationResize?.disconnect()
})

watch(ownerId, async () => {
  liveEvents.value = []
  await refresh()
  openStream()
})

async function loadOwners() {
  try {
    owners.value = await listOwners()
    if (!ownerId.value && owners.value.length) ownerId.value = owners.value[0].owner_id
  } catch (e: any) {
    error.value = e?.message || '无法加载用户列表'
  }
}

async function refresh() {
  loading.value = true
  try {
    snapshot.value = await getMissionControlSnapshot(ownerId.value || undefined)
    error.value = ''
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || 'Mission Control 暂时不可用'
    error.value = e?.response?.status === 404
      ? `Mission Control API 尚未加载：${detail}。请重启 admin-api。`
      : detail
  } finally {
    loading.value = false
  }
}

function openStream() {
  if (stream) stream.close()
  streamState.value = 'connecting'
  stream = new EventSource(missionControlEventsUrl(ownerId.value || undefined))
  stream.addEventListener('runtime_event', (message) => {
    try {
      const event = JSON.parse((message as MessageEvent).data) as RuntimeEvent
      liveEvents.value = [event, ...liveEvents.value].slice(0, 80)
      streamState.value = event.severity === 'warn' && event.type.includes('degraded') ? 'degraded' : 'live'
      if (event.source === 'hub' || event.source === 'mission_control') void refresh()
    } catch {
      streamState.value = 'degraded'
    }
  })
  stream.onerror = () => {
    streamState.value = 'degraded'
  }
}

function statusClass(status: string | undefined) {
  const value = (status || '').toLowerCase()
  if (['online', 'ok', 'done', 'succeeded', 'completed', 'active', 'live', 'success'].includes(value)) return 'is-ok'
  if (['running', 'pending', 'queued', 'connecting', 'degraded', 'warn', 'warning'].includes(value)) return 'is-warn'
  if (['failed', 'error', 'errored', 'offline', 'timeout'].includes(value)) return 'is-bad'
  return 'is-idle'
}

function friendlyDeviceName(device: RuntimeDevice) {
  const text = `${device.name} ${device.kind} ${device.role}`.toLowerCase()
  if (text.includes('2.06') || text.includes('pocket') || text.includes('ptt')) return '随身对讲身体'
  if (text.includes('box')) return '房间语音身体'
  if (text.includes('camera') || text.includes('vision') || text.includes('atk')) return '视觉观察身体'
  if (text.includes('web')) return '网页身体'
  return device.role || '扩展身体'
}

function deviceIcon(device: RuntimeDevice) {
  const caps = device.capabilities.join(' ')
  if (caps.includes('camera') || caps.includes('vision')) return VideoCamera
  if (caps.includes('ptt') || caps.includes('voice')) return Microphone
  if (device.role.includes('Web')) return Monitor
  return Cpu
}

function laneIcon(key: string) {
  if (key === 'body') return Connection
  if (key === 'turn') return ChatDotRound
  if (key === 'memory') return DataAnalysis
  if (key === 'task') return MagicStick
  if (key === 'permission') return Lock
  return Cpu
}

function storyIcon(step: RuntimeStoryStep) {
  if (step.status === 'failed') return Warning
  if (step.status === 'done') return CircleCheck
  return Timer
}

function sourceLabel(source: string) {
  return {
    hub: '身体',
    channel: '语音',
    agent: '大脑',
    memory: '记忆',
    data: '账本',
    admin: '控制台',
    mission_control: '飞控台',
  }[source] || source
}

function eventTone(event: RuntimeEvent) {
  if (event.severity === 'error') return 'danger'
  if (event.severity === 'warn') return 'warning'
  if (event.source === 'memory') return 'success'
  if (event.source === 'hub') return 'primary'
  return 'info'
}

function friendlyEventSummary(event: RuntimeEvent) {
  const text = event.summary || event.type
  const lower = text.toLowerCase()
  if (lower.includes('mission control event stream connected')) return '实时信号已接入飞控台'
  if (lower.includes('hub probe detected 0 known device')) return '身体网络完成探测，暂未发现在线身体'
  if (lower.includes('hub probe detected')) return text.replace('Hub probe detected', '身体网络探测到').replace('known device(s)', '个身体节点')
  if (lower.includes('channel room lifecycle')) return '语音房间状态发生变化'
  if (lower.includes('device command updated')) return '身体控制指令已更新'
  if (lower.includes('memory')) return '记忆链路产生一次状态变化'
  return text
}

function nodeTone(status: string | undefined) {
  const value = statusClass(status)
  if (value === 'is-ok') return '#5eead4'
  if (value === 'is-warn') return '#facc15'
  if (value === 'is-bad') return '#fb7185'
  return '#8aa7a0'
}

function startConstellation() {
  const canvas = constellationCanvas.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const resize = () => {
    const rect = canvas.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.max(1, Math.floor(rect.width * dpr))
    canvas.height = Math.max(1, Math.floor(rect.height * dpr))
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  constellationResize = new ResizeObserver(resize)
  constellationResize.observe(canvas)
  resize()

  const draw = (time: number) => {
    const rect = canvas.getBoundingClientRect()
    const width = rect.width
    const height = rect.height
    const cx = width / 2
    const cy = height / 2
    const base = Math.min(width, height)
    const nodes = architectureNodes.value

    ctx.clearRect(0, 0, width, height)

    const field = ctx.createRadialGradient(cx, cy, base * 0.05, cx, cy, base * 0.58)
    field.addColorStop(0, 'rgba(94, 234, 212, 0.24)')
    field.addColorStop(0.42, 'rgba(13, 34, 33, 0.45)')
    field.addColorStop(1, 'rgba(2, 9, 10, 0)')
    ctx.fillStyle = field
    ctx.fillRect(0, 0, width, height)

    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(Math.sin(time / 9000) * 0.08)
    for (let i = 0; i < 4; i += 1) {
      const radius = base * (0.18 + i * 0.1)
      ctx.beginPath()
      ctx.ellipse(0, 0, radius * 1.35, radius * 0.78, i * 0.34, 0, Math.PI * 2)
      ctx.strokeStyle = i === 1 ? 'rgba(250, 204, 21, 0.26)' : 'rgba(94, 234, 212, 0.2)'
      ctx.lineWidth = 1
      ctx.stroke()
    }
    ctx.restore()

    nodes.forEach((node, index) => {
      const angle = -Math.PI / 2 + (index / nodes.length) * Math.PI * 2
      const orbitX = Math.cos(angle) * base * 0.34
      const orbitY = Math.sin(angle) * base * 0.22
      const tone = nodeTone(node.status)
      const pulse = (time / 1100 + index / nodes.length) % 1
      const px = orbitX * pulse
      const py = orbitY * pulse

      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(cx + orbitX, cy + orbitY)
      ctx.strokeStyle = 'rgba(94, 234, 212, 0.16)'
      ctx.lineWidth = 1
      ctx.stroke()

      ctx.beginPath()
      ctx.arc(cx + px, cy + py, 2.5, 0, Math.PI * 2)
      ctx.fillStyle = tone
      ctx.shadowBlur = 18
      ctx.shadowColor = tone
      ctx.fill()
      ctx.shadowBlur = 0

      ctx.beginPath()
      ctx.arc(cx + orbitX, cy + orbitY, 5.5, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(4, 18, 18, 0.92)'
      ctx.fill()
      ctx.strokeStyle = tone
      ctx.lineWidth = 1.4
      ctx.stroke()
    })

    const corePulse = 0.5 + Math.sin(time / 820) * 0.5
    const core = ctx.createRadialGradient(cx - base * 0.03, cy - base * 0.04, 0, cx, cy, base * 0.17)
    core.addColorStop(0, 'rgba(238, 247, 244, 0.96)')
    core.addColorStop(0.22, 'rgba(94, 234, 212, 0.72)')
    core.addColorStop(1, 'rgba(6, 20, 22, 0.95)')
    ctx.beginPath()
    ctx.arc(cx, cy, base * (0.118 + corePulse * 0.008), 0, Math.PI * 2)
    ctx.fillStyle = core
    ctx.shadowBlur = 34
    ctx.shadowColor = 'rgba(94, 234, 212, 0.45)'
    ctx.fill()
    ctx.shadowBlur = 0

    ctx.beginPath()
    ctx.arc(cx, cy, base * 0.145, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(94, 234, 212, 0.72)'
    ctx.lineWidth = 1
    ctx.stroke()

    constellationFrame = window.requestAnimationFrame(draw)
  }

  constellationFrame = window.requestAnimationFrame(draw)
}
</script>

<template>
  <main class="mission-shell">
    <div class="grid-floor" />

    <header class="hero-bar">
      <section class="brand-block">
        <div class="brand-mark">
          <el-icon><Aim /></el-icon>
        </div>
        <div>
          <p class="eyebrow">Eidolon Agent OS</p>
          <h1>Mission Control</h1>
        </div>
      </section>

      <section class="top-controls">
        <el-select v-model="ownerId" class="owner-pick" size="large" filterable placeholder="选择用户">
          <el-option
            v-for="owner in owners"
            :key="owner.owner_id"
            :label="owner.display_name || owner.owner_id"
            :value="owner.owner_id"
          />
        </el-select>
        <button class="icon-button" :disabled="loading" title="刷新当前状态" @click="refresh">
          <el-icon :class="{ spin: loading }"><Refresh /></el-icon>
        </button>
      </section>
    </header>

    <section class="hero-stage">
      <div class="cockpit-radar">
        <div class="radar-grid" />
        <canvas ref="constellationCanvas" class="constellation-canvas" aria-hidden="true" />

        <div class="core-callout">
          <span>Agent OS Core</span>
          <strong>{{ companionLabel }}</strong>
          <small>{{ ownerLabel }}</small>
        </div>

        <article
          v-for="node in architectureNodes"
          :key="node.key"
          class="orbit-node"
          :class="[statusClass(node.status), `orbit-${node.key}`]"
        >
          <span>{{ node.title }}</span>
          <strong>{{ node.metric }}</strong>
          <small>{{ node.detail }}</small>
        </article>
      </div>

      <aside class="cockpit-left">
        <p class="state-pill" :class="statusClass(streamState)">{{ mainStateText }}</p>
        <h2>{{ experience?.headline || 'Eidolon 正在等待一次交互' }}</h2>
        <p>{{ experience?.subheadline || '身份、记忆、工具和设备状态正在被串成一条可见链路。' }}</p>
        <div class="cockpit-stats">
          <div v-for="stat in cockpitStats" :key="stat.label">
            <span>{{ stat.label }}</span>
            <strong>{{ stat.value }}</strong>
          </div>
        </div>
      </aside>

      <aside class="cockpit-right">
        <div class="completion-dial" :style="{ '--completion': `${experience?.completion ?? 0}%` }">
          <span>链路完整度</span>
          <strong>{{ experience?.completion ?? 0 }}%</strong>
        </div>
        <div class="next-vector">
          <span>下一步</span>
          <strong>{{ experience?.next_best_action }}</strong>
        </div>
        <div class="signal-stack">
          <article v-for="event in recentEvents.slice(0, 3)" :key="event.event_id">
            <span>{{ sourceLabel(event.source) }}</span>
            <strong>{{ friendlyEventSummary(event) }}</strong>
          </article>
        </div>
      </aside>
    </section>

    <section class="story-panel">
      <div class="flight-head">
        <div>
          <p class="eyebrow">Runtime Flow</p>
          <h2>一次交互如何穿过 Agent OS</h2>
        </div>
        <span>{{ flowSteps.length }} 层</span>
      </div>
      <ol class="flowline">
        <li v-for="step in flowSteps" :key="step.key" :class="statusClass(step.status)">
          <div class="story-icon">
            <el-icon><component :is="storyIcon(step)" /></el-icon>
          </div>
          <div>
            <strong>{{ step.title }}</strong>
            <p>{{ step.detail }}</p>
            <span>{{ sourceLabel(step.source) }} · {{ step.ts ? formatTimestamp(step.ts) : '等待信号' }}</span>
          </div>
        </li>
      </ol>
    </section>

    <section class="plain-summary">
      <div>
        <span>系统视角</span>
        <strong>{{ ownerLabel }} 只是在和 {{ companionLabel }} 互动</strong>
        <p>{{ experience?.plain_summary }}</p>
      </div>
      <div class="completion-box">
        <span>链路完整度</span>
        <strong>{{ experience?.completion ?? 0 }}%</strong>
      </div>
      <div class="next-action">
        <span>下一步</span>
        <strong>{{ experience?.next_best_action }}</strong>
      </div>
    </section>

    <p v-if="error" class="error-strip">{{ error }}</p>

    <section class="capability-strip">
      <article v-for="card in cards" :key="card.key" :class="statusClass(card.status)">
        <span>{{ card.title }}</span>
        <strong>{{ card.metric }}</strong>
        <p>{{ card.detail }}</p>
      </article>
    </section>

    <section class="mission-grid">
      <section class="body-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">身体网络</p>
            <h2>小忆可以出现在哪里</h2>
          </div>
          <span class="panel-stat">{{ onlineDevices }}/{{ devices.length }} 在线</span>
        </div>

        <div class="body-map">
          <article
            v-for="device in devices"
            :key="device.device_id"
            class="device-node"
            :class="[statusClass(device.status), { 'is-online': device.online }]"
          >
            <div class="node-core">
              <el-icon><component :is="deviceIcon(device)" /></el-icon>
            </div>
            <div class="node-copy">
              <h3>{{ friendlyDeviceName(device) }}</h3>
              <p>{{ device.name || device.device_id }}</p>
              <span>{{ device.online ? '可作为当前身体' : '暂时离线' }}</span>
            </div>
            <div class="cap-row">
              <span v-for="cap in device.capabilities.slice(0, 5)" :key="cap">{{ cap }}</span>
            </div>
          </article>

          <article v-if="!devices.length" class="empty-state">
            <el-icon><Connection /></el-icon>
            <span>还没有检测到身体节点</span>
          </article>
        </div>
      </section>

      <section class="lanes-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">体验分层</p>
            <h2>Agent OS 的实时分工</h2>
          </div>
          <span class="panel-stat">{{ degradedSources.length ? `${degradedSources.length} 项需关注` : '稳定' }}</span>
        </div>

        <div class="lane-list">
          <article v-for="lane in lanes" :key="lane.key" :class="statusClass(lane.status)">
            <div class="lane-icon">
              <el-icon><component :is="laneIcon(lane.key)" /></el-icon>
            </div>
            <div class="lane-copy">
              <span>{{ lane.title }}</span>
              <strong>{{ lane.headline }}</strong>
              <p>{{ lane.detail }}</p>
              <div class="lane-items">
                <b v-for="item in lane.items.slice(0, 3)" :key="`${lane.key}-${item.label}-${item.value}`">
                  {{ item.label }}：{{ item.value }}
                </b>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="event-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">安全摘要</p>
            <h2>系统刚刚发生了什么</h2>
          </div>
          <span class="panel-stat">{{ recentEvents.length }} 条</span>
        </div>
        <div class="event-feed">
          <article v-for="event in recentEvents" :key="event.event_id">
            <el-tag size="small" :type="eventTone(event)" effect="dark">{{ sourceLabel(event.source) }}</el-tag>
            <div>
              <strong>{{ friendlyEventSummary(event) }}</strong>
              <span>{{ formatTimestamp(event.ts) }} · 默认脱敏</span>
            </div>
          </article>
        </div>
      </section>

      <section class="privacy-panel">
        <div>
          <el-icon><Lock /></el-icon>
          <strong>隐私保护默认开启</strong>
        </div>
        <p>{{ snapshot?.privacy_notice || '默认只展示摘要、数量、状态和 hash，不展示完整私密文本、图片或音频。' }}</p>
      </section>
    </section>
  </main>
</template>

<style scoped>
.mission-shell {
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  padding: 24px;
  color: #eef7f4;
  background:
    radial-gradient(circle at 50% 0%, rgba(41, 175, 160, 0.16), transparent 34%),
    linear-gradient(135deg, #071113 0%, #0d1514 48%, #11100c 100%);
}

.grid-floor {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(125, 211, 190, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(125, 211, 190, 0.07) 1px, transparent 1px);
  background-size: 54px 54px;
  mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.8), transparent 82%);
}

.hero-bar,
.hero-stage,
.plain-summary,
.capability-strip,
.story-panel,
.mission-grid,
.error-strip {
  position: relative;
  z-index: 1;
}

.hero-bar,
.brand-block,
.top-controls,
.panel-head,
.privacy-panel div {
  display: flex;
  align-items: center;
}

.hero-bar {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.brand-block {
  gap: 14px;
}

.brand-mark,
.icon-button,
.story-icon,
.lane-icon,
.node-core {
  display: grid;
  place-items: center;
  border-radius: 8px;
}

.brand-mark {
  width: 50px;
  height: 50px;
  border: 1px solid rgba(94, 234, 212, 0.36);
  color: #8ff5de;
  background: rgba(7, 28, 27, 0.76);
  box-shadow: inset 0 0 26px rgba(94, 234, 212, 0.14), 0 0 30px rgba(94, 234, 212, 0.08);
}

.eyebrow {
  margin: 0 0 5px;
  color: #91aaa3;
  font: 700 11px/1 var(--eid-font-mono);
  text-transform: uppercase;
  letter-spacing: 0;
}

h1,
h2,
h3,
p {
  margin: 0;
  letter-spacing: 0;
}

h1 {
  font-size: 30px;
  line-height: 1.05;
}

h2 {
  font-size: 22px;
  line-height: 1.18;
}

h3 {
  font-size: 15px;
}

.top-controls {
  gap: 10px;
}

.owner-pick {
  width: min(280px, 42vw);
}

.icon-button {
  width: 42px;
  height: 42px;
  border: 1px solid rgba(94, 234, 212, 0.3);
  color: #c8fff3;
  background: rgba(8, 24, 24, 0.88);
  cursor: pointer;
}

.icon-button:disabled {
  cursor: wait;
  opacity: 0.62;
}

.hero-stage {
  --cockpit-cyan: #5eead4;
  --cockpit-blue: #38bdf8;
  --cockpit-gold: #facc15;
  position: relative;
  display: grid;
  grid-template-areas: "left radar right";
  grid-template-columns: minmax(230px, 0.62fr) minmax(460px, 1.2fr) minmax(230px, 0.62fr);
  gap: 18px;
  align-items: center;
  min-height: 540px;
  margin-bottom: 16px;
  padding: 26px;
  overflow: hidden;
  border: 1px solid rgba(94, 234, 212, 0.24);
  border-radius: 10px;
  background:
    radial-gradient(circle at 50% 50%, rgba(94, 234, 212, 0.14), transparent 36%),
    radial-gradient(circle at 78% 14%, rgba(56, 189, 248, 0.13), transparent 30%),
    linear-gradient(120deg, rgba(8, 28, 29, 0.95), rgba(9, 16, 16, 0.9) 46%, rgba(20, 18, 12, 0.78)),
    rgba(8, 16, 15, 0.92);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.05), inset 0 -90px 120px rgba(0, 0, 0, 0.24), 0 32px 80px rgba(0, 0, 0, 0.34);
}

.hero-stage::before,
.hero-stage::after {
  content: "";
  position: absolute;
  pointer-events: none;
}

.hero-stage::before {
  inset: 12px;
  border: 1px solid rgba(94, 234, 212, 0.12);
  clip-path: polygon(0 12%, 4% 0, 96% 0, 100% 12%, 100% 88%, 96% 100%, 4% 100%, 0 88%);
}

.hero-stage::after {
  right: 26px;
  bottom: 20px;
  left: 26px;
  height: 26%;
  background:
    linear-gradient(rgba(94, 234, 212, 0.18), transparent 1px),
    linear-gradient(90deg, rgba(94, 234, 212, 0.12), transparent 1px);
  background-size: 28px 28px;
  transform: perspective(520px) rotateX(62deg);
  transform-origin: bottom;
  opacity: 0.42;
}

.state-pill {
  display: inline-flex;
  margin-bottom: 14px;
  padding: 7px 10px;
  border: 1px solid rgba(94, 234, 212, 0.28);
  border-radius: 999px;
  font: 700 12px/1 var(--eid-font-mono);
  background: rgba(94, 234, 212, 0.08);
}

.cockpit-left,
.cockpit-right,
.cockpit-radar {
  position: relative;
  z-index: 1;
}

.cockpit-left {
  grid-area: left;
  align-self: stretch;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  z-index: 2;
  padding: 8px 0 8px 2px;
}

.cockpit-left h2 {
  max-width: 350px;
  font-size: clamp(32px, 3.8vw, 56px);
  line-height: 1.06;
  text-shadow: 0 0 34px rgba(94, 234, 212, 0.12);
}

.cockpit-left p:not(.state-pill) {
  max-width: 480px;
  margin-top: 14px;
  color: #b9cbc5;
  font-size: 16px;
  line-height: 1.65;
}

.cockpit-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 28px;
}

.cockpit-stats div {
  min-width: 0;
  padding: 10px 0;
  border-top: 1px solid rgba(94, 234, 212, 0.28);
}

.cockpit-stats span,
.completion-dial span,
.next-vector span,
.signal-stack span,
.orbit-node span {
  display: block;
  color: #8aa7a0;
  font: 700 10px/1.2 var(--eid-font-mono);
  text-transform: uppercase;
}

.cockpit-stats strong {
  display: block;
  margin-top: 7px;
  color: #eafff9;
  font: 800 25px/1 var(--eid-font-mono);
}

.cockpit-radar {
  grid-area: radar;
  position: relative;
  box-sizing: border-box;
  width: min(48vw, 620px);
  min-width: 440px;
  aspect-ratio: 1 / 1;
  justify-self: center;
  border-radius: 50%;
  isolation: isolate;
}

.radar-grid,
.constellation-canvas {
  position: absolute;
  inset: 0;
  border-radius: 50%;
}

.radar-grid {
  background:
    linear-gradient(rgba(94, 234, 212, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(94, 234, 212, 0.08) 1px, transparent 1px),
    radial-gradient(circle, transparent 28%, rgba(94, 234, 212, 0.08) 29%, transparent 30%, transparent 46%, rgba(250, 204, 21, 0.14) 47%, transparent 48%, transparent 64%, rgba(94, 234, 212, 0.1) 65%, transparent 66%);
  background-size: 34px 34px, 34px 34px, auto;
  mask-image: radial-gradient(circle, black 0 66%, transparent 74%);
  opacity: 0.9;
}

.constellation-canvas {
  width: 100%;
  height: 100%;
  filter: saturate(1.2);
}

.core-callout {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 3;
  display: grid;
  place-items: center;
  width: min(33%, 190px);
  min-width: 140px;
  aspect-ratio: 1;
  padding: 18px;
  border: 1px solid rgba(94, 234, 212, 0.44);
  border-radius: 50%;
  text-align: center;
  transform: translate(-50%, -50%);
  background:
    radial-gradient(circle at 35% 28%, rgba(238, 247, 244, 0.14), transparent 18%),
    rgba(4, 18, 19, 0.72);
  box-shadow: 0 0 70px rgba(94, 234, 212, 0.24), inset 0 0 34px rgba(94, 234, 212, 0.12);
  backdrop-filter: blur(8px);
}

.core-callout span,
.core-callout small {
  max-width: 100%;
  overflow: hidden;
  color: #91aaa3;
  font: 10px/1.2 var(--eid-font-mono);
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
}

.core-callout strong {
  max-width: 100%;
  overflow: hidden;
  color: #eef7f4;
  font-size: clamp(22px, 2.2vw, 34px);
  line-height: 1.06;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.orbit-node {
  position: absolute;
  z-index: 4;
  width: min(35%, 176px);
  min-width: 132px;
  padding: 11px 12px;
  border-left: 2px solid rgba(94, 234, 212, 0.54);
  background:
    linear-gradient(90deg, rgba(94, 234, 212, 0.16), rgba(3, 14, 15, 0.64) 66%, transparent);
  box-shadow: -18px 0 34px rgba(94, 234, 212, 0.05);
  backdrop-filter: blur(6px);
}

.orbit-node strong,
.orbit-node small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.orbit-node strong {
  margin-top: 6px;
  color: #eef7f4;
  font: 800 28px/0.95 var(--eid-font-mono);
}

.orbit-node small {
  margin-top: 7px;
  color: #b9cbc5;
  font-size: 11px;
}

.orbit-bodies {
  top: 9%;
  right: 6%;
}

.orbit-channel {
  top: 42%;
  right: -1%;
}

.orbit-agent {
  right: 8%;
  bottom: 10%;
}

.orbit-guard {
  top: 9%;
  left: 6%;
}

.orbit-actions {
  top: 42%;
  left: 4%;
}

.orbit-memory {
  bottom: 10%;
  left: 8%;
}

.cockpit-right {
  grid-area: right;
  align-self: stretch;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  gap: 14px;
}

.completion-dial {
  display: grid;
  place-items: center;
  align-self: end;
  width: min(100%, 178px);
  aspect-ratio: 1;
  border-radius: 50%;
  background:
    conic-gradient(from -90deg, rgba(94, 234, 212, 0.9) var(--completion, 0%), rgba(94, 234, 212, 0.08) 0),
    radial-gradient(circle, rgba(5, 17, 18, 0.98) 0 56%, transparent 58%);
  box-shadow: 0 0 44px rgba(94, 234, 212, 0.14);
}

.completion-dial span {
  color: #b8fff2;
}

.completion-dial strong {
  color: #eafff9;
  font: 900 38px/1 var(--eid-font-mono);
  text-shadow: 0 0 24px rgba(94, 234, 212, 0.28);
}

.next-vector,
.signal-stack article {
  min-width: 0;
  padding: 13px 0 13px 16px;
  border-left: 1px solid rgba(94, 234, 212, 0.28);
  background: linear-gradient(90deg, rgba(94, 234, 212, 0.09), transparent 74%);
}

.next-vector strong,
.signal-stack strong {
  display: block;
  margin-top: 7px;
  overflow: hidden;
  color: #eef7f4;
  font-size: 15px;
  line-height: 1.35;
  text-overflow: ellipsis;
}

.signal-stack {
  display: grid;
  gap: 8px;
}

.architecture-map {
  position: relative;
  box-sizing: border-box;
  display: none;
  grid-template-areas:
    "guard core bodies"
    "actions core channel"
    "memory core agent";
  grid-template-columns: minmax(96px, 1fr) minmax(96px, 116px) minmax(96px, 1fr);
  grid-template-rows: repeat(3, minmax(0, 1fr));
  gap: 8px;
  align-items: stretch;
  width: min(420px, 32vw);
  min-width: 360px;
  height: 330px;
  padding: 12px;
  justify-self: center;
  border: 1px solid rgba(94, 234, 212, 0.22);
  border-radius: 8px;
  background:
    linear-gradient(90deg, rgba(94, 234, 212, 0.07) 1px, transparent 1px),
    linear-gradient(rgba(94, 234, 212, 0.07) 1px, transparent 1px),
    radial-gradient(circle at 50% 50%, rgba(94, 234, 212, 0.2), rgba(8, 18, 17, 0.45) 42%, rgba(8, 14, 13, 0.84));
  background-size: 26px 26px, 26px 26px, auto;
  overflow: hidden;
}

.architecture-map::before,
.architecture-map::after {
  content: "";
  position: absolute;
  inset: 36px;
  border: 1px solid rgba(94, 234, 212, 0.22);
  border-radius: 50%;
}

.architecture-map::after {
  inset: 82px;
  border-color: rgba(250, 204, 21, 0.2);
}

.map-core {
  position: relative;
  box-sizing: border-box;
  grid-area: core;
  z-index: 2;
  display: grid;
  place-items: center;
  width: min(108px, 100%);
  height: 108px;
  align-self: center;
  justify-self: center;
  padding: 12px;
  border: 1px solid rgba(94, 234, 212, 0.62);
  border-radius: 50%;
  text-align: center;
  background: rgba(5, 19, 18, 0.96);
  box-shadow: 0 0 42px rgba(94, 234, 212, 0.22), inset 0 0 30px rgba(94, 234, 212, 0.08);
}

.map-core strong,
.map-core span,
.map-core small {
  display: block;
  max-width: 92px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.map-core strong {
  font-size: 18px;
}

.map-core span,
.map-core small {
  color: #91aaa3;
  font: 10px/1.2 var(--eid-font-mono);
  text-transform: uppercase;
}

.arch-node {
  position: relative;
  box-sizing: border-box;
  z-index: 3;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  padding: 9px;
  border: 1px solid rgba(94, 234, 212, 0.3);
  border-radius: 8px;
  background: rgba(7, 20, 19, 0.92);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
}

.arch-node::before {
  content: none;
}

.arch-node span,
.arch-node strong,
.arch-node small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.arch-node span {
  color: #91aaa3;
  font-size: 10px;
}

.arch-node strong {
  margin: 3px 0;
  color: #eef7f4;
  font: 800 16px/1 var(--eid-font-mono);
}

.arch-node small {
  color: #aabdba;
  font-size: 10px;
}

.node-bodies {
  grid-area: bodies;
}

.node-channel {
  grid-area: channel;
}

.node-agent {
  grid-area: agent;
}

.node-memory {
  grid-area: memory;
}

.node-actions {
  grid-area: actions;
}

.node-guard {
  grid-area: guard;
}

.plain-summary {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 180px minmax(220px, 0.8fr);
  gap: 1px;
  overflow: hidden;
  margin-bottom: 16px;
  border: 1px solid rgba(91, 121, 111, 0.68);
  border-radius: 8px;
  background: rgba(91, 121, 111, 0.68);
}

.plain-summary > div {
  min-width: 0;
  padding: 15px;
  background: rgba(8, 16, 15, 0.92);
}

.plain-summary span,
.capability-strip span {
  display: block;
  margin-bottom: 6px;
  color: #91aaa3;
  font-size: 12px;
}

.plain-summary strong {
  display: block;
  overflow: hidden;
  font-size: 16px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plain-summary p {
  margin-top: 8px;
  color: #b9cbc5;
  line-height: 1.55;
}

.completion-box strong {
  font: 800 34px/1 var(--eid-font-mono);
  color: #8ff5de;
}

.next-action strong {
  white-space: normal;
}

.error-strip {
  margin: 0 0 16px;
  padding: 11px 13px;
  border: 1px solid rgba(251, 113, 133, 0.44);
  border-radius: 8px;
  color: #fecdd3;
  background: rgba(75, 13, 27, 0.62);
}

.capability-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  margin-bottom: 16px;
  border: 1px solid rgba(94, 234, 212, 0.18);
  border-radius: 8px;
  background: rgba(94, 234, 212, 0.18);
}

.capability-strip article,
.body-panel,
.lanes-panel,
.event-panel,
.privacy-panel {
  border: 1px solid rgba(91, 121, 111, 0.7);
  border-radius: 8px;
  background: rgba(8, 16, 15, 0.88);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.04), 0 20px 60px rgba(0, 0, 0, 0.22);
}

.capability-strip article {
  min-height: 112px;
  padding: 14px;
  border: 0;
  border-radius: 0;
  background:
    linear-gradient(180deg, rgba(94, 234, 212, 0.07), rgba(8, 16, 15, 0.9)),
    rgba(8, 16, 15, 0.9);
}

.capability-strip strong {
  display: block;
  font: 800 26px/1.05 var(--eid-font-mono);
}

.capability-strip p {
  margin-top: 10px;
  color: #aabdba;
  font-size: 12px;
  line-height: 1.45;
}

.body-panel,
.lanes-panel,
.event-panel,
.privacy-panel {
  padding: 18px;
}

.story-panel {
  position: relative;
  overflow: hidden;
  margin-bottom: 16px;
  padding: 18px 18px 22px;
  border: 1px solid rgba(94, 234, 212, 0.18);
  border-radius: 8px;
  background:
    linear-gradient(90deg, rgba(94, 234, 212, 0.08), transparent 26%, rgba(250, 204, 21, 0.05) 72%, transparent),
    rgba(4, 14, 15, 0.72);
}

.story-panel::before {
  content: "";
  position: absolute;
  top: 76px;
  right: 30px;
  left: 30px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(94, 234, 212, 0.82), rgba(250, 204, 21, 0.42), transparent);
  box-shadow: 0 0 24px rgba(94, 234, 212, 0.28);
}

.flight-head {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.flight-head span {
  align-self: start;
  color: #a7f3d0;
  font: 800 13px/1 var(--eid-font-mono);
}

.panel-head {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.panel-stat {
  flex: 0 0 auto;
  padding: 7px 9px;
  border: 1px solid rgba(94, 234, 212, 0.28);
  border-radius: 6px;
  color: #a7f3d0;
  font: 700 12px/1 var(--eid-font-mono);
  background: rgba(94, 234, 212, 0.08);
}

.flowline {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.flowline::before {
  content: none;
}

.flowline li {
  position: relative;
  min-width: 0;
  min-height: 150px;
  padding: 50px 0 0;
  background: transparent;
}

.flowline li::before {
  content: "";
  position: absolute;
  top: 30px;
  left: 39px;
  width: calc(100% - 18px);
  height: 1px;
  background: linear-gradient(90deg, rgba(94, 234, 212, 0.52), transparent);
}

.flowline li:last-child::before {
  display: none;
}

.story-icon {
  position: relative;
  z-index: 1;
  width: 42px;
  height: 42px;
  margin: -50px 0 12px;
  border: 1px solid rgba(94, 234, 212, 0.42);
  border-radius: 50%;
  color: #8ff5de;
  background: radial-gradient(circle, rgba(94, 234, 212, 0.2), rgba(7, 26, 24, 0.98) 68%);
  box-shadow: 0 0 22px rgba(94, 234, 212, 0.16);
}

.flowline strong,
.lane-copy strong,
.event-feed strong,
.node-copy h3,
.node-copy p,
.lane-items b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flowline strong,
.flowline p,
.flowline span {
  display: block;
}

.flowline p {
  min-height: 54px;
  margin-top: 8px;
  color: #b9cbc5;
  font-size: 12px;
  line-height: 1.5;
}

.flowline span {
  color: #819891;
  font: 11px/1.25 var(--eid-font-mono);
}

.mission-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 0.9fr);
  gap: 16px;
}

.body-map {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px;
}

.device-node,
.empty-state {
  min-width: 0;
  min-height: 150px;
  padding: 13px;
  border: 1px solid rgba(91, 121, 111, 0.5);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(14, 29, 28, 0.95), rgba(8, 15, 14, 0.95));
}

.device-node {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 12px;
}

.device-node.is-online {
  border-color: rgba(94, 234, 212, 0.54);
  box-shadow: inset 0 0 34px rgba(94, 234, 212, 0.08);
}

.node-core {
  width: 46px;
  height: 46px;
  border: 1px solid rgba(94, 234, 212, 0.35);
  color: #8ff5de;
  background: #071716;
}

.node-copy p {
  margin: 6px 0;
  color: #c8d8d3;
  font-size: 12px;
}

.node-copy span {
  color: #91aaa3;
  font-size: 12px;
}

.cap-row {
  display: flex;
  grid-column: 1 / -1;
  flex-wrap: wrap;
  gap: 6px;
  align-self: end;
}

.cap-row span {
  padding: 4px 6px;
  border: 1px solid rgba(145, 170, 163, 0.22);
  border-radius: 6px;
  color: #aabdba;
  font: 10px/1 var(--eid-font-mono);
  background: rgba(255, 255, 255, 0.04);
}

.empty-state {
  display: grid;
  place-items: center;
  color: #91aaa3;
}

.lanes-panel {
  grid-row: span 2;
}

.lane-list {
  display: grid;
  gap: 10px;
}

.lane-list article {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
  padding: 11px;
  border: 1px solid rgba(91, 121, 111, 0.48);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
}

.lane-icon {
  width: 40px;
  height: 40px;
  color: #8ff5de;
  background: rgba(94, 234, 212, 0.08);
}

.lane-copy {
  min-width: 0;
}

.lane-copy span {
  color: #91aaa3;
  font-size: 12px;
}

.lane-copy strong {
  display: block;
  margin-top: 3px;
  font-size: 15px;
}

.lane-copy p {
  margin-top: 6px;
  color: #aabdba;
  font-size: 12px;
  line-height: 1.45;
}

.lane-items {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.lane-items b {
  max-width: 100%;
  padding: 5px 7px;
  border-radius: 6px;
  color: #dff8f1;
  font: 700 11px/1 var(--eid-font-mono);
  background: rgba(255, 255, 255, 0.05);
}

.event-panel {
  min-height: 300px;
}

.event-feed {
  display: grid;
  gap: 9px;
}

.event-feed article {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  min-width: 0;
  padding: 10px;
  border: 1px solid rgba(91, 121, 111, 0.42);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
}

.event-feed span {
  display: block;
  margin-top: 5px;
  overflow: hidden;
  color: #819891;
  font: 11px/1.2 var(--eid-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.privacy-panel {
  display: grid;
  gap: 10px;
}

.privacy-panel div {
  gap: 10px;
}

.privacy-panel .el-icon {
  color: #facc15;
}

.privacy-panel p {
  color: #aabdba;
  line-height: 1.55;
}

.is-ok {
  color: #8ff5de;
}

.is-warn {
  color: #fde68a;
}

.is-bad {
  color: #fda4af;
}

.is-idle {
  color: #aabdba;
}

.spin {
  animation: spin 900ms linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1120px) {
  .capability-strip,
  .flowline {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .hero-stage {
    grid-template-areas:
      "left"
      "radar"
      "right";
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
  }

  .cockpit-radar,
  .architecture-map {
    width: 100%;
    max-width: 680px;
    min-width: 0;
  }

  .cockpit-left,
  .cockpit-right {
    justify-content: start;
  }

  .cockpit-right {
    display: grid;
    grid-template-columns: 210px minmax(0, 1fr);
    align-items: center;
  }

  .signal-stack {
    grid-column: 1 / -1;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .mission-grid {
    grid-template-columns: 1fr;
  }

  .lanes-panel {
    grid-row: auto;
  }
}

@media (max-width: 820px) {
  .mission-shell {
    padding: 14px;
  }

  .hero-bar {
    align-items: flex-start;
    flex-direction: column;
  }

  .top-controls,
  .owner-pick {
    width: 100%;
  }

  .hero-stage,
  .plain-summary {
    grid-template-columns: 1fr;
  }

  .hero-stage {
    gap: 16px;
    padding: 18px;
  }

  .cockpit-left h2 {
    font-size: clamp(30px, 10vw, 46px);
  }

  .cockpit-stats,
  .cockpit-right,
  .signal-stack {
    grid-template-columns: 1fr;
  }

  .completion-dial {
    justify-self: center;
    align-self: center;
    width: min(180px, 62vw);
  }

  .cockpit-radar {
    max-width: 520px;
  }

  .orbit-node {
    width: 38%;
    min-width: 118px;
    padding: 9px 10px;
  }

  .orbit-node strong {
    font-size: 22px;
  }

  .orbit-node small {
    display: none;
  }

  .architecture-map {
    display: grid;
    height: auto;
    padding: 14px;
    grid-template-areas: none;
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .architecture-map::before,
  .architecture-map::after {
    display: none;
  }

  .map-core,
  .arch-node,
  .node-bodies,
  .node-channel,
  .node-agent,
  .node-memory,
  .node-actions,
  .node-guard {
    position: static;
    grid-area: auto;
    width: 100%;
    height: auto;
    min-height: 0;
    transform: none;
    border-radius: 8px;
  }

  .arch-node::before {
    display: none;
  }

  .capability-strip,
  .flowline {
    grid-template-columns: 1fr;
  }

  .flowline::before {
    display: none;
  }
}
</style>
