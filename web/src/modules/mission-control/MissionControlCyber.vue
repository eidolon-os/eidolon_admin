<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { listOwners, type OwnerView } from '@/api/eidolonData'
import {
  getMissionControlSnapshot,
  missionControlEventsUrl,
  type RuntimeCompanion,
  type RuntimeDevice,
  type RuntimeEvent,
  type RuntimeSnapshot,
  type RuntimeService,
  type RuntimeTurn,
} from '@/api/missionControl'

const owners = ref<OwnerView[]>([])
const ownerId = ref('')
const snapshot = ref<RuntimeSnapshot | null>(null)
const liveEvents = ref<RuntimeEvent[]>([])
const loading = ref(false)
const error = ref('')
const streamState = ref<'connecting' | 'live' | 'degraded'>('connecting')
const now = ref(Date.now())
let pollTimer: number | undefined
let clockTimer: number | undefined
let stream: EventSource | null = null

interface InfraDef { id: string; cn: string; code: string; role: string; mode: string }
const INFRA: InfraDef[] = [
  { id: 'hub', cn: '设备中枢', code: 'eidolon_hub', mode: 'proxy', role: '管理硬件身体的接入、发现与指令下发，连接 LiveKit 语音房间。' },
  { id: 'channel', cn: '语音通道', code: 'eidolon_channel', mode: 'process', role: '语音转文字（STT）、文字转语音（TTS），运行在语音房间里。' },
  { id: 'agent', cn: '智能体引擎', code: 'eidolon_agent', mode: 'proxy', role: '通用推理引擎（PersonasService）：理解、规划、调用工具、生成回应。它运行每个伙伴的人格（persona / genome），伙伴的名字与身份存在 eidolon_data，不在这里。' },
  { id: 'memory', cn: '记忆服务', code: 'eidolon_memory', mode: 'native', role: '保存与召回伙伴的长期记忆，管理记忆空间与后台整理。' },
  { id: 'admin', cn: '控制台', code: 'eidolon_admin', mode: 'native', role: '你正在看的管理网关，聚合并转发各子项目的接口。' },
  { id: 'client-web', cn: '网页端', code: 'client_web', mode: 'process', role: '浏览器里的对话入口页面。' },
  { id: 'mementos', cn: 'Mementos', code: 'mementos', mode: 'process', role: '回忆 / 纪念相关扩展服务。' },
  { id: 'nats', cn: 'NATS', code: 'nats-server', mode: 'infra', role: '消息总线 / JetStream —— 各子项目之间的事件与数据流通道。' },
  { id: 'livekit', cn: 'LiveKit', code: 'livekit-server', mode: 'infra', role: '实时音视频服务器 —— 承载语音房间，Hub 与语音通道都连它。' },
]
const SVC_GLYPH: Record<string, string> = { 'client-web': '⌂', hub: '⎔', channel: '◍', agent: '◊', memory: '◈', admin: '▦', mementos: '✦', nats: '⇄', livekit: '⧉' }
const BUS_SPINE = ['client-web', 'hub', 'channel', 'agent', 'memory']
const BUS_AUX = ['admin', 'mementos', 'nats', 'livekit']
const MODE_CN: Record<string, string> = { native: '内建', proxy: '代理', process: '托管', device: '设备', infra: '基础' }
const MODE_EXP: Record<string, string> = {
  native: '管理接口内建在网关里', proxy: '透明转发到子项目自己的接口', process: '由 supervisord 托管，只读状态', device: '硬件入口，经 Hub 接入', infra: '共享基础设施，由 supervisord 托管',
}

const experience = computed(() => snapshot.value?.experience)
const memory = computed(() => snapshot.value?.memory)
const devices = computed(() => snapshot.value?.devices || [])
const services = computed(() => snapshot.value?.services || [])
const jobs = computed(() => snapshot.value?.jobs || [])
const companions = computed<RuntimeCompanion[]>(() => {
  const list = snapshot.value?.companions || []
  return list.length ? list : snapshot.value?.companion ? [snapshot.value.companion] : []
})
const ownerName = computed(() => snapshot.value?.owner?.display_name || snapshot.value?.owner?.owner_id || '未选择主人')
const onlineDevices = computed(() => devices.value.filter((d) => d.online).length)
const onlineServices = computed(() => services.value.filter((s) => s.online).length)
const activeJobs = computed(() => jobs.value.filter((j) => ['running', 'queued', 'pending', 'active'].includes((j.status || '').toLowerCase())).length)
const degradedSources = computed(() => (snapshot.value?.source_status || []).filter((s) => !s.ok))
const activeTurn = computed<RuntimeTurn | null>(() => snapshot.value?.active_turn || snapshot.value?.recent_turns?.[0] || null)
const pipelineActive = computed(() => {
  const t = snapshot.value?.active_turn
  return t ? ['running', 'active', 'pending', 'queued'].includes((t.status || '').toLowerCase()) : false
})
const completion = computed(() => experience.value?.completion ?? 0)
const primaryCompanionId = computed(() => snapshot.value?.companion?.companion_id || '')
const privacyModeLabel = computed(() => {
  const mode = memory.value?.privacy_mode || activeTurn.value?.privacy_mode || 'safe'
  return { safe: '安全', summary: '摘要', restricted: '受限' }[mode] || '安全'
})
const recentEvents = computed(() => {
  const merged = [...liveEvents.value, ...(snapshot.value?.recent_events || [])]
  const seen = new Set<string>()
  return merged.filter((e) => (seen.has(e.event_id) ? false : (seen.add(e.event_id), true))).slice(0, 16)
})
const traceId = computed(() => (activeTurn.value?.turn_id || snapshot.value?.owner?.owner_id || 'STANDBY').slice(0, 14).toUpperCase())
function svc(id: string): RuntimeService | null {
  return services.value.find((s) => s.service_id === id) || null
}

const companionUnits = computed(() => {
  const t = snapshot.value?.active_turn
  const m = memory.value
  const activeRealm = m?.active_realm_id
  return companions.value.map((c) => {
    const devs = devices.value.filter((d) => d.companion_id === c.companion_id)
    const cJobs = jobs.value.filter((j) => j.companion_id === c.companion_id)
    const turn = t && t.companion_id === c.companion_id ? t : null
    const isActiveRealm = !!c.memory_realm_id && c.memory_realm_id === activeRealm
    return {
      id: c.companion_id || 'unknown', name: c.display_name || c.companion_id || '未命名伙伴',
      kind: c.kind || 'companion', status: c.status || 'idle', genome: c.genome_id || '', realm: c.memory_realm_id || '',
      isActiveRealm, recall: isActiveRealm ? m?.last_recall_hits ?? 0 : null, runners: isActiveRealm ? `${m?.runners_online ?? 0}/${m?.runners_total ?? 0}` : '',
      write: isActiveRealm ? (m?.fanout_allowed ? m?.last_write_disposition || 'ALLOW' : 'HOLD') : '',
      devices: devs, turn, jobs: cJobs, isPrimary: c.companion_id === primaryCompanionId.value,
    }
  })
})
const boundIds = computed(() => new Set(companions.value.map((c) => c.companion_id)))
const unboundDevices = computed(() => devices.value.filter((d) => !d.companion_id || !boundIds.value.has(d.companion_id)))

function deviceType(d: RuntimeDevice) {
  const k = `${d.kind} ${d.role}`.toLowerCase()
  if (k.includes('esp32') || k.includes('box') || k.includes('camera') || k.includes('atk') || k.includes('ptt')) return '物理身体'
  if (k.includes('web') || k.includes('virtual')) return '虚拟身体'
  return '设备'
}
function deviceShort(d: RuntimeDevice) {
  const n = d.name || d.device_id || ''
  return n.length > 13 ? '…' + n.slice(-10) : n
}

// ── constellation geometry ────────────────────────────────────────────────
const VBW = 1000, VBH = 700, CX = 500, CY = 350, RX = 306, RY = 220, RSAT = 92, PI = Math.PI
const spin = ref(0)
let spinTimer: number | undefined
const reduceMotion = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const stars = ref<{ x: number; y: number; r: number; d: number }[]>([])
function seedStars() {
  const out: { x: number; y: number; r: number; d: number }[] = []
  for (let i = 0; i < 70; i++) out.push({ x: Math.round(Math.random() * VBW), y: Math.round(Math.random() * VBH), r: +(Math.random() * 1.3 + 0.3).toFixed(2), d: +(Math.random() * 4).toFixed(2) })
  stars.value = out
}
function ptStyle(x: number, y: number) {
  return { left: `${(x / VBW) * 100}%`, top: `${(y / VBH) * 100}%` }
}
function satOf(c: any, kind: string) {
  if (kind === 'body') {
    const n = c.devices.length
    return { kind, label: '身体', glyph: '⬡', value: n ? deviceType(c.devices[0]) : '未绑定', tone: c.devices.some((d: RuntimeDevice) => d.online) ? 'ok' : n ? 'idle' : 'off', empty: !n, accent: 'cyan' }
  }
  if (kind === 'mem') {
    return { kind, label: '记忆', glyph: '◈', value: c.realm ? (c.isActiveRealm ? `${c.recall} 召回` : '已配置') : '无空间', tone: c.realm ? 'ok' : 'off', empty: !c.realm, accent: 'yellow' }
  }
  return { kind, label: '活动', glyph: '⚡', value: c.turn ? '对话中' : c.jobs.length ? `${c.jobs.length} 任务` : '空闲', tone: c.turn ? 'live' : c.jobs.length ? 'warn' : 'off', empty: !c.turn && !c.jobs.length, accent: 'mag' }
}
const galaxy = computed(() => {
  const comps = companionUnits.value
  const N = comps.length || 1
  const start = N === 1 ? 0 : -90 + 180 / N
  const rev = spin.value * 0.22 // companions slowly revolve around owner
  const moonRev = spin.value * 1.1 // moons orbit their companion faster
  const nodes = comps.map((c, i) => {
    const a = (start + (i * 360) / N + rev) * (PI / 180)
    const x = CX + RX * Math.cos(a), y = CY + RY * Math.sin(a)
    const sats = ['body', 'mem', 'act'].map((kind, si) => {
      const sa = a + ((si - 1) * 42 + moonRev) * (PI / 180)
      const sx = x + RSAT * Math.cos(sa), sy = y + RSAT * Math.sin(sa)
      return { ...satOf(c, kind), c, x: sx, y: sy, link: `M${x.toFixed(1)} ${y.toFixed(1)} L${sx.toFixed(1)} ${sy.toFixed(1)}` }
    })
    return { c, x, y, link: `M${CX} ${CY} L${x.toFixed(1)} ${y.toFixed(1)}`, active: !!c.turn, sats }
  })
  return { nodes, sats: nodes.flatMap((n) => n.sats), links: nodes.map((n) => ({ id: n.c.id, d: n.link, active: n.active })) }
})

// ── runtime fidelity: which bus node the active turn's current stage lights ─
const STAGE_SVC: Record<string, string> = { input: 'channel', memory_recall: 'memory', agent_turn: 'agent', tools: 'agent', memory_write: 'memory' }
const hotService = computed(() => {
  const t = snapshot.value?.active_turn
  if (!t) return ''
  const stages: any[] = t.stages || []
  const running = stages.find((s) => ['running', 'pending', 'active'].includes(String(s.status || '').toLowerCase()))
  const last = [...stages].reverse().find((s) => ['done', 'ok', 'succeeded'].includes(String(s.status || '').toLowerCase()))
  const key = (running || last)?.key
  return key ? STAGE_SVC[key] || '' : ''
})

// ── click drilldown drawer ──────────────────────────────────────────────
const drawer = ref<{ type: 'owner' | 'companion' | 'moon' | 'service'; c?: any; s?: any; n?: any } | null>(null)
function openOwner() { drawer.value = { type: 'owner' } }
function openComp(c: any) { drawer.value = { type: 'companion', c } }
function openMoon(s: any) { drawer.value = { type: 'moon', s } }
function openSvc(n: any) { drawer.value = { type: 'service', n } }
function closeDrawer() { drawer.value = null }
const drawerComp = computed(() => (drawer.value?.type === 'companion' ? drawer.value.c : drawer.value?.type === 'moon' ? drawer.value.s?.c : null))
const drawerTurns = computed(() => {
  const c = drawerComp.value
  if (!c) return []
  return (snapshot.value?.recent_turns || []).filter((t) => t.companion_id === c.id).slice(0, 6)
})

const infraNodes = computed(() =>
  INFRA.map((f) => {
    const s = svc(f.id)
    const online = !!s?.online
    const checked = !!s?.checked
    // three-state: online / offline(confirmed down) / unknown(no health probe → supervisord-managed)
    const state = online ? 'online' : checked ? 'offline' : 'unknown'
    const stateCn = online ? '在线' : checked ? '离线' : '未探测'
    return { ...f, glyph: SVC_GLYPH[f.id] || '◆', online, checked, state, stateCn, latency: fmtLatency(s?.latency_ms), detail: s?.detail || '', events: recentEvents.value.filter((e) => e.source === f.id).slice(0, 3) }
  }),
)
const busSpine = computed(() => BUS_SPINE.map((id) => infraNodes.value.find((n) => n.id === id)!).filter(Boolean))
const busAux = computed(() => BUS_AUX.map((id) => infraNodes.value.find((n) => n.id === id)!).filter(Boolean))
const deviceRatio = computed(() => (devices.value.length ? Math.round((onlineDevices.value / devices.value.length) * 100) : 0))

const clock = computed(() => {
  const d = new Date(now.value)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
})
const streamLabel = computed(() => ({ connecting: 'SYNC', live: 'ONLINE', degraded: 'UNSTABLE' })[streamState.value])
const systemStateLabel = computed(() => {
  const s = experience.value?.system_state
  return { active: '正在处理', working: '后台推进', watching: '感知中', standby: '待命中' }[s || 'standby'] || '待命中'
})

onMounted(async () => {
  seedStars()
  await loadOwners(); await refresh(); openStream()
  pollTimer = window.setInterval(refresh, 8000)
  clockTimer = window.setInterval(() => (now.value = Date.now()), 1000)
  if (!reduceMotion) spinTimer = window.setInterval(() => (spin.value = (spin.value + 0.35) % 3600), 66)
})
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  if (clockTimer) window.clearInterval(clockTimer)
  if (spinTimer) window.clearInterval(spinTimer)
  if (stream) stream.close()
})
watch(ownerId, async () => { liveEvents.value = []; await refresh(); openStream() })

async function loadOwners() {
  try {
    owners.value = await listOwners()
    if (!ownerId.value && owners.value.length) ownerId.value = owners.value[0].owner_id
  } catch (e: any) { error.value = e?.message || 'OWNER LIST FAULT' }
}
async function refresh() {
  loading.value = true
  try {
    snapshot.value = await getMissionControlSnapshot(ownerId.value || undefined)
    error.value = ''
  } catch (e: any) { error.value = e?.response?.data?.detail || e?.message || 'LINK FAULT // MCC OFFLINE' } finally { loading.value = false }
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
    } catch { streamState.value = 'degraded' }
  })
  stream.onerror = () => (streamState.value = 'degraded')
}

function fmtLatency(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`
}
function fmtTime(iso: string | null | undefined) {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function statusClass(status: string) {
  const v = (status || '').toLowerCase()
  if (['ok', 'done', 'succeeded', 'completed', 'active', 'success'].includes(v)) return 'ok'
  if (['running', 'pending', 'queued', 'degraded', 'warn'].includes(v)) return 'warn'
  if (['failed', 'error', 'errored', 'offline'].includes(v)) return 'bad'
  return 'idle'
}
</script>

<template>
  <main class="cy" :class="{ live: pipelineActive }">
    <div class="cy-grid" aria-hidden="true" />
    <div class="cy-glow" aria-hidden="true" />
    <div class="cy-scan" aria-hidden="true" />
    <div class="cy-flicker" aria-hidden="true" />

    <header class="cy-head">
      <div class="brand">
        <h1 class="glitch" data-text="EIDOLON//OS">EIDOLON//OS</h1>
        <div class="brand-meta">
          <span class="brand-state" :class="pipelineActive ? 'ok' : 'idle'"><i class="led" />{{ streamLabel }}</span>
          <span class="brand-owner">OWNER · {{ ownerName }}</span>
          <span class="brand-trace">SYS {{ systemStateLabel }} · TRACE::{{ traceId }}</span>
        </div>
      </div>

      <div class="hud">
        <div class="meter"><span class="mg cyan">◉</span><b>{{ companions.length }}</b><small>伙伴</small></div>
        <div class="meter">
          <span class="mg cyan">⬡</span><b>{{ onlineDevices }}<i>/{{ devices.length }}</i></b>
          <span class="mbar"><i :style="{ width: deviceRatio + '%' }" /></span><small>身体在线</small>
        </div>
        <div class="meter"><span class="mg yellow">◈</span><b>{{ memory?.realms_total ?? 0 }}</b><small>记忆空间</small></div>
        <div class="meter"><span class="mg yellow">⟐</span><b>{{ memory?.last_recall_hits ?? 0 }}</b><small>记忆召回</small></div>
        <div class="meter"><span class="mg mag">⚡</span><b>{{ activeJobs }}<i>/{{ jobs.length }}</i></b><small>活动任务</small></div>
        <div class="meter meter-svc">
          <span class="mg">▦</span>
          <span class="svc-leds"><i v-for="n in infraNodes" :key="n.id" class="led" :class="'st-' + n.state" :title="`${n.cn} · ${n.stateCn}`" /></span>
          <small>底座 {{ onlineServices }}/{{ services.length }}</small>
        </div>
        <div class="meter meter-int">
          <span class="int-ring" :style="{ '--v': completion }"><b>{{ completion }}</b></span><small>完整度</small>
        </div>
      </div>

      <div class="head-ctrl">
        <span class="clock">{{ clock }}<em>{{ new Date(now).toLocaleDateString() }}</em></span>
        <el-select v-model="ownerId" class="owner-pick" filterable placeholder="OWNER">
          <el-option v-for="o in owners" :key="o.owner_id" :label="o.display_name || o.owner_id" :value="o.owner_id" />
        </el-select>
        <button class="icon-btn" :disabled="loading" @click="refresh"><el-icon :class="{ spin: loading }"><Refresh /></el-icon></button>
      </div>
    </header>

    <p class="state-line">
      <b :class="pipelineActive ? 'ok' : 'idle'">● {{ systemStateLabel }}</b>
      {{ experience?.headline || 'Agent OS 待命中' }} —— {{ experience?.plain_summary || '下面所有虚拟伙伴、它们的身体和记忆，都属于这位主人。' }}
    </p>
    <p v-if="error" class="cy-error">// {{ error }}</p>

    <!-- SOVEREIGN CONSTELLATION -->
    <section class="galaxy">
      <svg class="gx-wires" :viewBox="`0 0 ${VBW} ${VBH}`" preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="sun" cx="50%" cy="42%" r="60%">
            <stop offset="0%" stop-color="#fff7cc" /><stop offset="30%" stop-color="#ffd23f" /><stop offset="100%" stop-color="rgba(164,75,255,0)" />
          </radialGradient>
        </defs>
        <!-- starfield -->
        <g class="stars"><circle v-for="(st, i) in stars" :key="'st' + i" :cx="st.x" :cy="st.y" :r="st.r" :style="{ animationDelay: st.d + 's' }" /></g>
        <!-- companion orbit ellipse -->
        <ellipse :cx="CX" :cy="CY" :rx="RX" :ry="RY" fill="none" stroke="rgba(0,234,255,.14)" stroke-width="1" stroke-dasharray="3 7" class="orbit-ring" />
        <!-- owner rotating rings -->
        <g>
          <circle :cx="CX" :cy="CY" r="96" fill="none" stroke="rgba(0,234,255,.22)" stroke-width="1" stroke-dasharray="4 6">
            <animateTransform attributeName="transform" type="rotate" :from="`0 ${CX} ${CY}`" :to="`360 ${CX} ${CY}`" dur="28s" repeatCount="indefinite" />
          </circle>
          <circle :cx="CX" :cy="CY" r="120" fill="none" stroke="rgba(164,75,255,.2)" stroke-width="1" stroke-dasharray="2 10">
            <animateTransform attributeName="transform" type="rotate" :from="`360 ${CX} ${CY}`" :to="`0 ${CX} ${CY}`" dur="40s" repeatCount="indefinite" />
          </circle>
        </g>
        <!-- satellite links -->
        <path v-for="s in galaxy.sats" :key="'sl' + s.c.id + s.kind" :d="s.link" class="wire sat" :class="{ dim: s.empty }" />
        <!-- companion links -->
        <path v-for="l in galaxy.links" :key="'cl' + l.id" :d="l.d" class="wire comp" :class="{ hot: l.active }" />
        <!-- pulses on active companion links -->
        <template v-for="l in galaxy.links.filter((x) => x.active)" :key="'p' + l.id">
          <circle r="4" class="pulse"><animateMotion dur="1.4s" repeatCount="indefinite" :path="l.d" /></circle>
          <circle r="4" class="pulse"><animateMotion dur="1.4s" begin="0.7s" repeatCount="indefinite" :path="l.d" /></circle>
        </template>
        <!-- sun glow -->
        <circle :cx="CX" :cy="CY" r="150" fill="url(#sun)" opacity="0.5" />
      </svg>

      <!-- OWNER sun -->
      <div class="gx-owner" :style="ptStyle(CX, CY)" @click="openOwner" title="点击查看主人全景">
        <span class="o-kick">OWNER · 主人</span>
        <strong>{{ ownerName }}</strong>
        <div class="o-int"><b>{{ completion }}%</b><em>完整度</em></div>
      </div>

      <!-- companion planets -->
      <el-popover v-for="n in galaxy.nodes" :key="'c' + n.c.id" placement="top" :width="300" trigger="hover" popper-class="cy-pop" :show-after="60">
        <template #reference>
          <div class="gx-comp" :class="{ primary: n.c.isPrimary, active: n.active }" :style="ptStyle(n.x, n.y)" @click="openComp(n.c)">
            <i class="led" :class="statusClass(n.c.status)" />
            <b>{{ n.c.name }}</b>
            <em>{{ n.c.isPrimary ? '★ 主伙伴' : n.c.kind }}</em>
          </div>
        </template>
        <div class="pop">
          <div class="pop-h"><b>{{ n.c.name }}</b><em>{{ n.c.isPrimary ? 'PRIMARY' : 'companion' }}</em></div>
          <p class="pop-role">虚拟伙伴（agent），归属于主人 {{ ownerName }}。它拥有自己的身体、记忆与活动。</p>
          <div class="pop-rows">
            <div><dt>状态</dt><dd :class="statusClass(n.c.status)">{{ n.c.status }}</dd></div>
            <div><dt>基因 genome</dt><dd>{{ n.c.genome || '—' }}</dd></div>
            <div><dt>身体</dt><dd>{{ n.c.devices.length }} 台 · {{ n.c.devices.filter((d) => d.online).length }} 在线</dd></div>
            <div><dt>记忆空间</dt><dd :class="n.c.realm ? 'ok' : 'idle'">{{ n.c.realm || '未开通' }}</dd></div>
          </div>
        </div>
      </el-popover>

      <!-- asset moons -->
      <el-popover v-for="s in galaxy.sats" :key="'s' + s.c.id + s.kind" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
        <template #reference>
          <div class="gx-sat" :class="[`a-${s.accent}`, `t-${s.tone}`, { empty: s.empty }]" :style="ptStyle(s.x, s.y)" @click="openMoon(s)">
            <i class="s-glyph">{{ s.glyph }}</i>
            <span class="s-label">{{ s.label }}</span>
            <b class="s-val">{{ s.value }}</b>
          </div>
        </template>
        <div class="pop">
          <div class="pop-h"><b>{{ s.c.name }} · {{ s.label }}</b><em>{{ s.tone.toUpperCase() }}</em></div>
          <!-- device -->
          <template v-if="s.kind === 'body'">
            <p class="pop-role">伙伴的物理 / 虚拟入口。设备只是入口，身份仍归属主人。</p>
            <div v-if="s.c.devices.length" class="pop-rows">
              <div v-for="d in s.c.devices" :key="d.device_id"><dt>{{ deviceType(d) }}</dt><dd :class="d.online ? 'ok' : 'idle'">{{ deviceShort(d) }} · {{ d.online ? '在线' : '离线' }}</dd></div>
            </div>
            <p v-else class="pop-role dim">这个伙伴还没有绑定任何身体。</p>
          </template>
          <!-- memory -->
          <template v-else-if="s.kind === 'mem'">
            <p class="pop-role">属于这个伙伴的长期记忆资产，让它记得主人、保持连续性。</p>
            <div class="pop-rows">
              <div><dt>记忆空间</dt><dd>{{ s.c.realm || '未开通' }}</dd></div>
              <div><dt>基因 genome</dt><dd>{{ s.c.genome || '—' }}</dd></div>
              <div v-if="s.c.isActiveRealm"><dt>召回命中</dt><dd class="ok">{{ s.c.recall }}</dd></div>
              <div v-if="s.c.isActiveRealm"><dt>后台整理</dt><dd>{{ s.c.runners }}</dd></div>
              <div v-if="s.c.isActiveRealm"><dt>写入策略</dt><dd>{{ s.c.write }}</dd></div>
            </div>
          </template>
          <!-- activity -->
          <template v-else>
            <p class="pop-role">这个伙伴此刻在做什么：前台对话与后台任务。</p>
            <div v-if="s.c.turn" class="pop-rows">
              <div><dt>对话状态</dt><dd :class="statusClass(s.c.turn.status)">{{ (s.c.turn.status || '').toUpperCase() }}</dd></div>
              <div><dt>延迟</dt><dd>{{ fmtLatency(s.c.turn.latency_ms) }}</dd></div>
              <div><dt>记忆命中</dt><dd>{{ s.c.turn.memory_hits }}</dd></div>
              <div><dt>触发</dt><dd>{{ s.c.turn.trigger || '—' }}</dd></div>
            </div>
            <p v-else-if="s.c.jobs.length" class="pop-role">{{ s.c.jobs.length }} 个后台任务在授权边界内推进。</p>
            <p v-else class="pop-role dim">当前空闲，等待一次交互点亮链路。</p>
          </template>
        </div>
      </el-popover>

      <!-- unbound devices bay -->
      <el-popover v-if="unboundDevices.length" placement="left" :width="280" trigger="hover" popper-class="cy-pop" :show-after="60">
        <template #reference>
          <div class="gx-unbound"><i class="led warn" /><span>待认领设备</span><b>{{ unboundDevices.length }}</b></div>
        </template>
        <div class="pop">
          <div class="pop-h"><b>待认领设备</b><em>UNCLAIMED</em></div>
          <p class="pop-role">这些身体还没有绑定到任何主人 / 伙伴，等待认领与授权。</p>
          <div class="pop-rows">
            <div v-for="d in unboundDevices" :key="d.device_id"><dt>{{ deviceType(d) }}</dt><dd :class="d.online ? 'ok' : 'idle'">{{ deviceShort(d) }} · {{ d.online ? '在线' : '离线' }}</dd></div>
          </div>
        </div>
      </el-popover>
    </section>

    <!-- runtime architecture bus: sub-projects wired in request-flow order -->
    <footer class="cy-bus" :class="{ live: pipelineActive }">
      <div class="bus-line">
        <span class="bus-cap">运行链路 · REQUEST FLOW</span>
        <template v-for="(n, i) in busSpine" :key="n.id">
          <el-popover placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
            <template #reference>
              <div class="bus-node" :class="[`st-${n.state}`, { hot: n.id === hotService }]" @click="openSvc(n)">
                <i class="bn-glyph">{{ n.glyph }}</i>
                <div class="bn-body"><b>{{ n.cn }}</b><em>{{ n.code }}</em></div>
                <div class="bn-stat"><i class="led" /><span>{{ n.stateCn }}{{ n.online ? ' · ' + n.latency : '' }}</span></div>
              </div>
            </template>
            <div class="pop">
              <div class="pop-h"><b>{{ n.cn }}</b><em>{{ n.code }}</em></div>
              <p class="pop-role">{{ n.role }}</p>
              <div class="pop-rows">
                <div><dt>状态</dt><dd :class="{ ok: n.state === 'online', bad: n.state === 'offline', warn: n.state === 'unknown' }">{{ n.stateCn }}{{ n.online ? ' · ' + n.latency : '' }}</dd></div>
                <div v-if="n.state === 'unknown'"><dt>说明</dt><dd class="warn">无健康接口，存活由 supervisord 托管</dd></div>
                <div><dt>集成</dt><dd>{{ MODE_CN[n.mode] }}（{{ MODE_EXP[n.mode] }}）</dd></div>
                <div v-if="n.detail"><dt>探针</dt><dd>{{ n.detail }}</dd></div>
              </div>
              <div v-if="n.events.length" class="pop-ev"><span class="pop-ev-h">最近事件</span><p v-for="e in n.events" :key="e.event_id"><em>{{ fmtTime(e.ts) }}</em>{{ e.summary || e.type }}</p></div>
            </div>
          </el-popover>
          <i v-if="i < busSpine.length - 1" class="bus-link"><b /></i>
        </template>

        <span class="bus-div" />

        <el-popover v-for="n in busAux" :key="n.id" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
          <template #reference>
            <div class="bus-node aux" :class="`st-${n.state}`" @click="openSvc(n)">
              <i class="bn-glyph">{{ n.glyph }}</i>
              <div class="bn-body"><b>{{ n.cn }}</b><em>{{ n.stateCn }}</em></div>
            </div>
          </template>
          <div class="pop">
            <div class="pop-h"><b>{{ n.cn }}</b><em>{{ n.code }}</em></div>
            <p class="pop-role">{{ n.role }}</p>
            <div class="pop-rows">
              <div><dt>状态</dt><dd :class="{ ok: n.state === 'online', bad: n.state === 'offline', warn: n.state === 'unknown' }">{{ n.stateCn }}{{ n.online ? ' · ' + n.latency : '' }}</dd></div>
              <div v-if="n.state === 'unknown'"><dt>说明</dt><dd class="warn">无健康接口，存活由 supervisord 托管</dd></div>
              <div><dt>集成</dt><dd>{{ MODE_CN[n.mode] }}（{{ MODE_EXP[n.mode] }}）</dd></div>
              <div v-if="n.detail"><dt>探针</dt><dd>{{ n.detail }}</dd></div>
            </div>
          </div>
        </el-popover>
      </div>

      <!-- live telemetry crawl -->
      <div class="bus-crawl">
        <span class="crawl-tag" :class="pipelineActive ? 'ok' : 'idle'"><i class="led" />TELEMETRY</span>
        <div class="crawl-track">
          <div class="crawl-run" v-if="recentEvents.length">
            <template v-for="pass in 2" :key="pass">
              <span v-for="e in recentEvents" :key="pass + e.event_id" class="cev" :class="'sev-' + e.severity">
                <em>{{ fmtTime(e.ts) }}</em><b>{{ (SVC_GLYPH[e.source] || '·') }} {{ e.source.toUpperCase() }}</b>{{ e.summary || e.type }}<u>◇</u>
              </span>
            </template>
          </div>
          <div class="crawl-run" v-else><span class="cev">等待实时信号进入视野…</span></div>
        </div>
      </div>
    </footer>

    <!-- click drilldown drawer -->
    <transition name="dw">
      <aside v-if="drawer" class="drawer" @click.self="closeDrawer">
        <div class="dw-panel">
          <button class="dw-close" @click="closeDrawer">✕</button>

          <template v-if="drawer.type === 'owner'">
            <span class="dw-kick purple">OWNER · 主人</span>
            <h3>{{ ownerName }}</h3>
            <p class="dw-role">主权主体 —— 一切虚拟伙伴、身体与记忆都归属于此。</p>
            <div class="dw-grid">
              <div><span>虚拟伙伴</span><b>{{ companions.length }}</b></div>
              <div><span>身体在线</span><b>{{ onlineDevices }}/{{ devices.length }}</b></div>
              <div><span>记忆空间</span><b>{{ memory?.realms_total ?? 0 }}</b></div>
              <div><span>完整度</span><b>{{ completion }}%</b></div>
            </div>
            <span class="dw-sect">伙伴</span>
            <div class="dw-list">
              <button v-for="c in companionUnits" :key="c.id" class="dw-row link" @click="openComp(c)">
                <i class="led" :class="statusClass(c.status)" /><b>{{ c.name }}</b><em>{{ c.devices.length }} 身体 · {{ c.realm ? '有记忆' : '无记忆' }}</em>
              </button>
            </div>
          </template>

          <template v-else-if="drawerComp">
            <span class="dw-kick">COMPANION · 虚拟伙伴</span>
            <h3>{{ drawerComp.name }}<i v-if="drawerComp.isPrimary" class="dw-pri">★ 主</i></h3>
            <p class="dw-role">{{ drawerComp.kind }} · {{ drawerComp.status }} · 归属 {{ ownerName }}</p>
            <div class="dw-grid">
              <div><span>genome</span><b class="mono sm">{{ drawerComp.genome || '—' }}</b></div>
              <div><span>记忆空间</span><b class="mono sm">{{ drawerComp.realm || '未开通' }}</b></div>
              <div><span>召回命中</span><b>{{ drawerComp.recall ?? '—' }}</b></div>
              <div><span>后台整理</span><b>{{ drawerComp.runners || '—' }}</b></div>
            </div>
            <span class="dw-sect">身体 / 化身 · {{ drawerComp.devices.length }}</span>
            <div class="dw-list">
              <div v-for="d in drawerComp.devices" :key="d.device_id" class="dw-row">
                <i class="led" :class="d.online ? 'ok' : 'idle'" /><b>{{ deviceType(d) }}</b><em>{{ deviceShort(d) }} · {{ d.online ? '在线' : '离线' }}{{ d.interaction_mode ? ' · ' + d.interaction_mode : '' }}</em>
              </div>
              <p v-if="!drawerComp.devices.length" class="dw-empty">未绑定身体</p>
            </div>
            <span class="dw-sect">最近对话</span>
            <div class="dw-list">
              <div v-for="t in drawerTurns" :key="t.turn_id" class="dw-row">
                <i class="led" :class="statusClass(t.status)" /><b>{{ (t.status || '').toUpperCase() }}</b><em>{{ fmtLatency(t.latency_ms) }} · 召回 {{ t.memory_hits }} · 工具 ×{{ t.tool_names?.length ?? 0 }}</em>
              </div>
              <p v-if="!drawerTurns.length" class="dw-empty">暂无对话记录</p>
            </div>
          </template>

          <template v-else-if="drawer.type === 'service'">
            <span class="dw-kick">SUBSYSTEM · 子项目</span>
            <h3>{{ drawer.n.cn }}<i class="dw-code">{{ drawer.n.code }}</i></h3>
            <p class="dw-role">{{ drawer.n.role }}</p>
            <div class="dw-grid">
              <div><span>状态</span><b :class="drawer.n.state === 'online' ? 'ok' : drawer.n.state === 'offline' ? 'bad' : 'warn'">{{ drawer.n.stateCn }}</b></div>
              <div><span>延迟</span><b>{{ drawer.n.online ? drawer.n.latency : '—' }}</b></div>
              <div><span>集成</span><b>{{ MODE_CN[drawer.n.mode] }}</b></div>
              <div><span>探针</span><b class="mono sm">{{ drawer.n.detail || '—' }}</b></div>
            </div>
            <span class="dw-sect">最近事件</span>
            <div class="dw-list">
              <div v-for="e in drawer.n.events" :key="e.event_id" class="dw-row"><em class="mono">{{ fmtTime(e.ts) }}</em><span class="dw-ev">{{ e.summary || e.type }}</span></div>
              <p v-if="!drawer.n.events.length" class="dw-empty">暂无事件</p>
            </div>
          </template>
        </div>
      </aside>
    </transition>
  </main>
</template>

<style scoped>
.cy {
  --cyan: #00eaff; --mag: #ff2e88; --yellow: #f7ff4a; --purple: #a44bff;
  --bg: #060210; --txt: #d9e6ff; --txt-dim: #6d6a99;
  position: relative; margin: 0; padding: 20px 22px 14px; min-height: 100vh;
  display: flex; flex-direction: column; gap: 10px; overflow: hidden; color: var(--txt); font-family: var(--eid-font-mono, monospace);
  background: radial-gradient(circle at 20% 0%, rgba(255, 46, 136, 0.12), transparent 40%), radial-gradient(circle at 82% 8%, rgba(0, 234, 255, 0.12), transparent 42%), var(--bg); isolation: isolate;
}
.cy-grid { position: fixed; inset: 0; z-index: -3; pointer-events: none; background-image: linear-gradient(rgba(0, 234, 255, 0.2) 1px, transparent 1px), linear-gradient(90deg, rgba(255, 46, 136, 0.16) 1px, transparent 1px); background-size: 46px 46px; transform: perspective(440px) rotateX(70deg); transform-origin: bottom; mask-image: linear-gradient(to top, #000, transparent 60%); animation: gridrun 5s linear infinite; opacity: 0.45; }
.cy-glow { position: fixed; inset: 0; z-index: -2; pointer-events: none; background: radial-gradient(circle at 50% 46%, rgba(164, 75, 255, 0.16), transparent 52%); }
.cy-scan { position: fixed; inset: 0; z-index: 5; pointer-events: none; background: repeating-linear-gradient(transparent 0 2px, rgba(0, 0, 0, 0.22) 3px 4px); mix-blend-mode: multiply; opacity: 0.5; }
.cy-flicker { position: fixed; inset: 0; z-index: 4; pointer-events: none; background: rgba(0, 234, 255, 0.02); animation: flicker 5s steps(30) infinite; }

.ok { color: #37f5b3; } .warn { color: var(--yellow); } .bad { color: var(--mag); } .idle { color: var(--txt-dim); } .cyan { color: var(--cyan); } .yellow { color: var(--yellow); }
.dim { color: var(--txt-dim) !important; }
.led { width: 8px; height: 8px; border-radius: 50%; background: currentColor; box-shadow: 0 0 10px currentColor; flex: 0 0 auto; display: inline-block; }

/* header */
.cy-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.glitch { position: relative; margin: 0; font: 900 26px/1 var(--eid-font-mono); letter-spacing: 0.06em; color: #fff; text-shadow: 0 0 18px rgba(0, 234, 255, 0.5); }
.glitch::before, .glitch::after { content: attr(data-text); position: absolute; inset: 0; pointer-events: none; }
.glitch::before { color: var(--mag); transform: translate(-2px, 0); text-shadow: 0 0 12px var(--mag); animation: glitchA 3.2s steps(2) infinite; }
.glitch::after { color: var(--cyan); transform: translate(2px, 0); text-shadow: 0 0 12px var(--cyan); animation: glitchB 2.6s steps(2) infinite; }
.brand { display: flex; align-items: center; gap: 14px; flex: 0 0 auto; }
.brand-meta { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
.brand-state { display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px; font: 700 9px/1 var(--eid-font-mono); letter-spacing: 0.08em; border: 1px solid currentColor; clip-path: polygon(6px 0, 100% 0, 100% 100%, 0 100%, 0 6px); }
.brand-state .led { width: 6px; height: 6px; }
.brand-owner { font: 700 11px/1 var(--eid-font-mono); color: #fff; letter-spacing: 0.04em; }
.brand-trace { font: 600 9px/1 var(--eid-font-mono); color: var(--txt-dim); letter-spacing: 0.06em; }

/* header telemetry HUD (visual, not text chips) */
.hud { display: flex; align-items: stretch; gap: 0; flex: 1 1 auto; justify-content: space-evenly; margin: 0 8px; }
.meter { display: grid; grid-template-columns: auto auto; grid-template-rows: auto auto; align-items: center; gap: 1px 8px; padding: 4px 14px; border-left: 1px solid rgba(0, 234, 255, 0.12); }
.meter:first-child { border-left: 0; }
.mg { grid-row: 1 / 3; font-size: 20px; font-style: normal; line-height: 1; color: var(--cyan); text-shadow: 0 0 10px currentColor; }
.mg.yellow { color: var(--yellow); } .mg.cyan { color: var(--cyan); } .mg.mag { color: var(--mag); }
.meter b { font: 900 22px/1 var(--eid-font-mono); color: #fff; }
.meter b i { font-size: 13px; font-style: normal; color: var(--txt-dim); }
.meter small { grid-column: 2; font: 600 9px/1 var(--eid-font-mono); color: var(--txt-dim); letter-spacing: 0.06em; }
.mbar { grid-column: 2; width: 100%; height: 3px; background: rgba(0, 234, 255, 0.12); overflow: hidden; }
.mbar i { display: block; height: 100%; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); transition: width 0.5s ease; }
.meter-svc .svc-leds { grid-column: 2; display: inline-flex; gap: 4px; }
.svc-leds .led { width: 7px; height: 7px; }
.led.st-online { color: #37f5b3; } .led.st-offline { color: var(--mag); } .led.st-unknown { color: var(--yellow); }
.meter-int { grid-template-columns: auto; justify-items: center; }
.int-ring { position: relative; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; background: conic-gradient(from -90deg, var(--cyan) calc(var(--v) * 1%), rgba(0, 234, 255, 0.1) 0); }
.int-ring::after { content: ""; position: absolute; inset: 4px; border-radius: 50%; background: #060210; }
.int-ring b { position: relative; font: 900 13px/1 var(--eid-font-mono); color: #fff; }
.meter-int small { grid-column: 1; }
.head-ctrl { display: flex; align-items: center; gap: 10px; flex: 0 0 auto; }
.clock { display: flex; flex-direction: column; align-items: flex-end; font: 900 18px/1 var(--eid-font-mono); color: var(--cyan); text-shadow: 0 0 14px rgba(0, 234, 255, 0.5); }
.clock em { margin-top: 3px; font: 600 9px/1 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; letter-spacing: 0.08em; }
.owner-pick { width: 140px; }
.icon-btn { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--cyan); color: var(--cyan); background: rgba(0, 234, 255, 0.08); cursor: pointer; clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px); }
.icon-btn:hover { background: rgba(0, 234, 255, 0.2); }

.state-line { font-family: var(--eid-font-sans); font-size: 12.5px; color: var(--txt); opacity: 0.82; line-height: 1.5; }
.state-line b { margin-right: 8px; font-family: var(--eid-font-mono); font-size: 11px; }
.cy-error { padding: 9px 13px; color: var(--mag); border: 1px solid var(--mag); background: rgba(255, 46, 136, 0.08); }

/* constellation */
.galaxy { position: relative; flex: 1 1 auto; width: 100%; max-width: 1180px; aspect-ratio: 1000 / 700; max-height: 66vh; margin: 0 auto; }
.gx-wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.wire { fill: none; }
.wire.comp { stroke: rgba(0, 234, 255, 0.4); stroke-width: 1.4; }
.wire.comp.hot { stroke: var(--cyan); stroke-width: 2.2; filter: drop-shadow(0 0 5px var(--cyan)); }
.wire.sat { stroke: rgba(0, 234, 255, 0.24); stroke-width: 1; stroke-dasharray: 3 4; }
.wire.sat.dim { stroke: rgba(109, 106, 153, 0.3); }
.pulse { fill: #d6fbff; filter: drop-shadow(0 0 6px var(--cyan)); }

.gx-owner, .gx-comp, .gx-sat, .gx-unbound { position: absolute; transform: translate(-50%, -50%); display: grid; place-content: center; text-align: center; }
.gx-owner { width: 150px; height: 150px; border-radius: 50%; border: 2px solid rgba(255, 210, 63, 0.6); background: radial-gradient(circle at 42% 34%, rgba(255, 240, 190, 0.35), rgba(10, 6, 24, 0.92) 62%); box-shadow: 0 0 60px rgba(255, 210, 63, 0.4), inset 0 0 40px rgba(255, 210, 63, 0.2); animation: sun 5s ease-in-out infinite; z-index: 3; }
.o-kick { font: 700 8.5px/1 var(--eid-font-mono); letter-spacing: 0.14em; color: var(--yellow); }
.gx-owner strong { display: block; max-width: 120px; margin: 5px auto 6px; font: 800 20px/1 var(--eid-font-sans); color: #fff; text-shadow: 0 0 16px rgba(255, 210, 63, 0.6); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.o-int b { font: 900 22px/1 var(--eid-font-mono); color: var(--cyan); }
.o-int em { display: block; margin-top: 2px; font: 600 8px/1 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; }

.gx-comp { width: 108px; height: 108px; border-radius: 50%; border: 1.5px solid var(--cyan); background: radial-gradient(circle at 40% 34%, rgba(0, 234, 255, 0.22), rgba(8, 5, 20, 0.94) 66%); box-shadow: 0 0 26px rgba(0, 234, 255, 0.25); cursor: default; z-index: 2; transition: transform 0.15s, box-shadow 0.2s; }
.gx-comp:hover { transform: translate(-50%, -50%) scale(1.06); box-shadow: 0 0 36px rgba(0, 234, 255, 0.5); }
.gx-comp .led { margin: 0 auto 4px; }
.gx-comp b { display: block; max-width: 92px; font: 800 15px/1.05 var(--eid-font-sans); color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gx-comp em { display: block; margin-top: 3px; font: 700 9px/1 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; }
.gx-comp.primary { border-color: var(--yellow); box-shadow: 0 0 30px rgba(247, 255, 74, 0.35); background: radial-gradient(circle at 40% 34%, rgba(247, 255, 74, 0.2), rgba(8, 5, 20, 0.94) 66%); }
.gx-comp.primary em { color: var(--yellow); }
.gx-comp.active { animation: nodepulse 1.5s ease-in-out infinite; }

.gx-sat { width: 78px; height: 78px; border-radius: 50%; border: 1px solid var(--cyan); background: radial-gradient(circle at 42% 36%, rgba(0, 234, 255, 0.16), rgba(8, 5, 20, 0.95) 68%); cursor: default; z-index: 2; transition: transform 0.15s, box-shadow 0.2s; }
.gx-sat:hover { transform: translate(-50%, -50%) scale(1.1); box-shadow: 0 0 22px currentColor; }
.gx-sat.a-yellow { border-color: var(--yellow); color: var(--yellow); background: radial-gradient(circle at 42% 36%, rgba(247, 255, 74, 0.14), rgba(8, 5, 20, 0.95) 68%); }
.gx-sat.a-mag { border-color: var(--mag); color: var(--mag); background: radial-gradient(circle at 42% 36%, rgba(255, 46, 136, 0.14), rgba(8, 5, 20, 0.95) 68%); }
.gx-sat.a-cyan { color: var(--cyan); }
.gx-sat.t-off { border-style: dashed; border-color: var(--txt-dim); color: var(--txt-dim); opacity: 0.62; }
.gx-sat.t-live { animation: nodepulse 1.3s ease-in-out infinite; }
.s-glyph { font-size: 15px; font-style: normal; line-height: 1; color: currentColor; text-shadow: 0 0 8px currentColor; }
.s-label { display: block; margin: 3px 0 2px; font: 700 8.5px/1 var(--eid-font-mono); color: var(--txt-dim); letter-spacing: 0.04em; }
.s-val { display: block; max-width: 68px; margin: 0 auto; font: 800 10px/1.05 var(--eid-font-sans); color: #eaf6ff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gx-sat.t-off .s-val { color: var(--txt-dim); }

.gx-unbound { top: auto; bottom: 6px; left: auto; right: 8px; transform: none; display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border: 1px dashed rgba(247, 255, 74, 0.4); background: rgba(247, 255, 74, 0.05); z-index: 3; }
.gx-unbound span { font: 700 11px/1 var(--eid-font-mono); color: var(--yellow); }
.gx-unbound b { font: 900 14px/1 var(--eid-font-mono); color: #fff; }

/* runtime architecture bus */
.cy-bus { display: flex; flex-direction: column; gap: 9px; padding: 11px 16px 9px; border: 1px solid rgba(0, 234, 255, 0.2); background: rgba(6, 4, 18, 0.55); clip-path: polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 calc(100% - 14px)); }
.bus-line { display: flex; align-items: center; gap: 0; }
.bus-cap { flex: 0 0 auto; margin-right: 12px; font: 700 9px/1.3 var(--eid-font-mono); letter-spacing: 0.08em; color: var(--txt-dim); writing-mode: vertical-rl; text-orientation: mixed; transform: rotate(180deg); max-height: 46px; }
.bus-node { position: relative; display: flex; align-items: center; gap: 9px; padding: 7px 12px; border: 1px solid rgba(0, 234, 255, 0.28); background: rgba(0, 234, 255, 0.04); clip-path: polygon(0 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%); cursor: default; transition: box-shadow 0.2s, transform 0.15s; }
.bus-node:hover { transform: translateY(-2px); box-shadow: 0 0 18px rgba(0, 234, 255, 0.35); }
.bus-node .bn-glyph { font-size: 19px; font-style: normal; line-height: 1; color: #37f5b3; text-shadow: 0 0 9px currentColor; }
.bn-body { display: flex; flex-direction: column; gap: 2px; }
.bn-body b { font: 700 12.5px/1 var(--eid-font-sans); color: #fff; white-space: nowrap; }
.bn-body em { font: 600 8.5px/1 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; letter-spacing: 0.03em; white-space: nowrap; }
.bn-stat { display: flex; align-items: center; gap: 5px; padding-left: 9px; border-left: 1px solid rgba(255, 255, 255, 0.08); }
.bn-stat .led { width: 7px; height: 7px; color: #37f5b3; }
.bn-stat span { font: 700 9px/1 var(--eid-font-mono); color: var(--txt-dim); white-space: nowrap; }
.bus-node.st-offline { border-color: rgba(255, 46, 136, 0.4); }
.bus-node.st-offline .bn-glyph, .bus-node.st-offline .led { color: var(--mag); }
.bus-node.st-offline .bn-stat span { color: var(--mag); }
.bus-node.st-unknown { border-style: dashed; border-color: rgba(247, 255, 74, 0.32); }
.bus-node.st-unknown .bn-glyph, .bus-node.st-unknown .led { color: var(--yellow); }
.bus-node.st-unknown .bn-stat span { color: var(--yellow); }
.bus-node.aux { padding: 7px 10px; }
.bus-node.aux .bn-glyph { font-size: 15px; }
.bus-link { position: relative; flex: 1 1 auto; min-width: 18px; height: 2px; background: rgba(0, 234, 255, 0.2); overflow: hidden; }
.bus-link b { position: absolute; top: -1px; left: -20%; width: 20%; height: 4px; border-radius: 2px; background: var(--cyan); box-shadow: 0 0 10px var(--cyan); opacity: 0; }
.cy-bus.live .bus-link { background: rgba(0, 234, 255, 0.28); }
.cy-bus.live .bus-link b { opacity: 1; animation: busflow 1.5s linear infinite; }
.bus-div { width: 1px; align-self: stretch; margin: 2px 14px; background: rgba(0, 234, 255, 0.18); }
.bus-node.aux + .bus-node.aux { margin-left: 8px; }

/* telemetry crawl */
.bus-crawl { display: flex; align-items: center; gap: 12px; padding-top: 8px; border-top: 1px solid rgba(0, 234, 255, 0.1); }
.crawl-tag { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--eid-font-mono); letter-spacing: 0.1em; color: var(--txt-dim); }
.crawl-tag .led { width: 6px; height: 6px; }
.crawl-track { flex: 1 1 auto; overflow: hidden; mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent); }
.crawl-run { display: inline-flex; white-space: nowrap; animation: crawl 48s linear infinite; }
.crawl-run:hover { animation-play-state: paused; }
.cev { display: inline-flex; align-items: center; gap: 7px; margin-right: 6px; font-size: 11.5px; color: var(--txt); }
.cev em { font: 700 10px/1 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; }
.cev b { font: 700 10px/1 var(--eid-font-mono); color: var(--cyan); letter-spacing: 0.04em; }
.cev u { text-decoration: none; color: rgba(0, 234, 255, 0.3); margin-left: 4px; }
.cev.sev-warn { color: var(--yellow); } .cev.sev-error { color: var(--mag); }
@keyframes busflow { from { left: -20%; } to { left: 100%; } }
@keyframes crawl { from { transform: translateX(0); } to { transform: translateX(-50%); } }

.spin { animation: spin 900ms linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes flow { from { background-position: 200% 0; } to { background-position: 0 0; } }
@keyframes gridrun { from { background-position: 0 0; } to { background-position: 0 46px; } }
@keyframes flicker { 0%, 96%, 100% { opacity: 0.4; } 97% { opacity: 0.05; } 98% { opacity: 0.7; } }
@keyframes sun { 0%, 100% { box-shadow: 0 0 50px rgba(255, 210, 63, 0.35), inset 0 0 36px rgba(255, 210, 63, 0.18); } 50% { box-shadow: 0 0 76px rgba(255, 210, 63, 0.5), inset 0 0 44px rgba(255, 210, 63, 0.26); } }
@keyframes nodepulse { 0%, 100% { box-shadow: 0 0 16px currentColor; } 50% { box-shadow: 0 0 30px currentColor; } }
@keyframes glitchA { 0%, 92%, 100% { clip-path: inset(0 0 0 0); transform: translate(-2px, 0); } 93% { clip-path: inset(20% 0 40% 0); transform: translate(-5px, -1px); } 96% { clip-path: inset(60% 0 10% 0); transform: translate(3px, 1px); } }
@keyframes glitchB { 0%, 90%, 100% { clip-path: inset(0 0 0 0); transform: translate(2px, 0); } 91% { clip-path: inset(50% 0 20% 0); transform: translate(5px, 1px); } 95% { clip-path: inset(10% 0 60% 0); transform: translate(-3px, -1px); } }
/* starfield + motion */
.stars circle { fill: #cfe8ff; opacity: 0.5; animation: twinkle 4s ease-in-out infinite; }
@keyframes twinkle { 0%, 100% { opacity: 0.15; } 50% { opacity: 0.7; } }
.orbit-ring { animation: spinslow 90s linear infinite; transform-origin: center; transform-box: fill-box; }
@keyframes spinslow { to { transform: rotate(360deg); } }

/* clickable galaxy nodes */
.gx-owner, .gx-comp, .gx-sat { cursor: pointer; }

/* active-turn stage lights up its bus node */
.bus-node.hot { border-color: var(--cyan); box-shadow: 0 0 22px rgba(0, 234, 255, 0.5); animation: nodepulse 1.2s ease-in-out infinite; }
.bus-node.hot .bn-glyph, .bus-node.hot .bn-stat .led { color: var(--cyan); }

/* drilldown drawer */
.drawer { position: fixed; inset: 0; z-index: 40; background: rgba(3, 1, 10, 0.62); backdrop-filter: blur(3px); display: flex; justify-content: flex-end; }
.dw-panel { position: relative; width: min(400px, 92vw); height: 100%; overflow-y: auto; padding: 22px 22px 30px; border-left: 1px solid rgba(0, 234, 255, 0.4); background: linear-gradient(160deg, rgba(12, 8, 26, 0.98), rgba(6, 3, 16, 0.98)); box-shadow: -20px 0 60px rgba(0, 0, 0, 0.6); }
.dw-panel::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: repeating-linear-gradient(transparent 0 2px, rgba(0, 0, 0, 0.18) 3px 4px); opacity: 0.4; }
.dw-close { position: absolute; top: 16px; right: 16px; width: 30px; height: 30px; border: 1px solid rgba(0, 234, 255, 0.3); background: rgba(0, 234, 255, 0.06); color: var(--cyan); font-size: 13px; cursor: pointer; clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px); }
.dw-close:hover { background: rgba(0, 234, 255, 0.18); }
.dw-kick { font: 700 10px/1 var(--eid-font-mono); letter-spacing: 0.14em; color: var(--cyan); }
.dw-kick.purple { color: var(--purple); }
.dw-panel h3 { margin: 10px 0 6px; font: 800 22px/1.1 var(--eid-font-sans); color: #fff; display: flex; align-items: baseline; gap: 8px; }
.dw-pri { font: 700 11px/1 var(--eid-font-mono); color: var(--yellow); font-style: normal; }
.dw-code { font: 700 11px/1 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; }
.dw-role { margin: 0 0 14px; font: 400 12.5px/1.6 var(--eid-font-sans); color: #aab6d8; }
.dw-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: rgba(0, 234, 255, 0.14); border: 1px solid rgba(0, 234, 255, 0.14); margin-bottom: 16px; }
.dw-grid > div { padding: 10px 12px; background: rgba(8, 5, 20, 0.92); }
.dw-grid span { font: 700 9px/1 var(--eid-font-mono); color: var(--txt-dim); letter-spacing: 0.05em; }
.dw-grid b { display: block; margin-top: 6px; font: 900 18px/1 var(--eid-font-mono); color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-grid b.mono { font-weight: 700; } .dw-grid b.sm { font-size: 11px; }
.dw-grid b.ok { color: #37f5b3; } .dw-grid b.bad { color: var(--mag); } .dw-grid b.warn { color: var(--yellow); }
.dw-sect { display: block; margin: 4px 0 8px; padding-bottom: 5px; border-bottom: 1px solid rgba(0, 234, 255, 0.14); font: 700 10px/1 var(--eid-font-mono); letter-spacing: 0.08em; color: var(--mag); }
.dw-list { display: grid; gap: 5px; margin-bottom: 18px; }
.dw-row { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 10px; border: 1px solid rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); text-align: left; }
.dw-row .led { width: 7px; height: 7px; }
.dw-row b { font: 700 12px/1 var(--eid-font-sans); color: var(--txt); flex: 0 0 auto; }
.dw-row em { font: 600 10px/1.3 var(--eid-font-mono); color: var(--txt-dim); font-style: normal; margin-left: auto; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-row.link { cursor: pointer; } .dw-row.link:hover { border-color: rgba(0, 234, 255, 0.4); background: rgba(0, 234, 255, 0.06); }
.dw-row .mono { font-family: var(--eid-font-mono); color: var(--txt-dim); }
.dw-ev { font-size: 11.5px; color: #aab6d8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-empty { padding: 10px; font-size: 11.5px; color: var(--txt-dim); }
.dw-enter-active, .dw-leave-active { transition: opacity 0.2s; }
.dw-enter-active .dw-panel, .dw-leave-active .dw-panel { transition: transform 0.25s ease; }
.dw-enter-from, .dw-leave-to { opacity: 0; }
.dw-enter-from .dw-panel, .dw-leave-to .dw-panel { transform: translateX(40px); }

@media (prefers-reduced-motion: reduce) { .cy-grid, .cy-flicker, .glitch::before, .glitch::after, .gx-owner, .gx-comp.active, .gx-sat.t-live, .pulse, .stars circle, .orbit-ring, .bus-node.hot, .cy-bus.live .bus-link b, .crawl-run { animation: none !important; } .gx-wires animateTransform { display: none; } }

@media (max-width: 1080px) {
  .cy-head { flex-wrap: wrap; }
  .head-mid { order: 3; width: 100%; }
  .galaxy { max-height: none; }
}
</style>

<style>
.cy-pop.el-popover.el-popper { background: rgba(8, 5, 20, 0.97) !important; border: 1px solid rgba(0, 234, 255, 0.4) !important; border-radius: 0 !important; clip-path: polygon(0 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%); box-shadow: 0 0 30px rgba(0, 234, 255, 0.25) !important; color: #d9e6ff; font-family: var(--eid-font-mono, monospace); padding: 12px 14px !important; }
.cy-pop.el-popper .el-popper__arrow::before { background: rgba(8, 5, 20, 0.97) !important; border-color: rgba(0, 234, 255, 0.4) !important; }
.cy-pop .pop-h { display: flex; align-items: baseline; gap: 8px; margin-bottom: 7px; }
.cy-pop .pop-h b { font: 800 15px/1 var(--eid-font-sans); color: #fff; }
.cy-pop .pop-h em { font: 700 10px/1 var(--eid-font-mono); color: #00eaff; font-style: normal; }
.cy-pop .pop-role { margin: 0 0 10px; font: 400 12px/1.55 var(--eid-font-sans); color: #b9c4e0; }
.cy-pop .pop-role.dim { color: #6d6a99; }
.cy-pop .pop-rows { display: grid; gap: 4px; padding: 8px 0; border-top: 1px solid rgba(0, 234, 255, 0.14); border-bottom: 1px solid rgba(0, 234, 255, 0.14); }
.cy-pop .pop-rows > div { display: flex; justify-content: space-between; gap: 12px; }
.cy-pop .pop-rows dt { font: 700 10px/1.5 var(--eid-font-mono); color: #6d6a99; letter-spacing: 0.05em; flex: 0 0 auto; }
.cy-pop .pop-rows dd { margin: 0; font: 700 11px/1.5 var(--eid-font-mono); color: #d9e6ff; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cy-pop .pop-rows dd.ok { color: #37f5b3; } .cy-pop .pop-rows dd.idle { color: #6d6a99; } .cy-pop .pop-rows dd.warn { color: #f7ff4a; } .cy-pop .pop-rows dd.bad { color: #ff2e88; }
.cy-pop .pop-ev { margin-top: 9px; }
.cy-pop .pop-ev-h { font: 700 9px/1 var(--eid-font-mono); color: #ff2e88; letter-spacing: 0.1em; }
.cy-pop .pop-ev p { display: flex; gap: 7px; margin: 5px 0 0; font-size: 11px; line-height: 1.4; color: #b9c4e0; }
.cy-pop .pop-ev em { font: 700 9px/1.4 var(--eid-font-mono); color: #6d6a99; font-style: normal; flex: 0 0 auto; }
</style>
