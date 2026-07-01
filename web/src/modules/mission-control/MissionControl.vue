<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Aim, Lock, Refresh } from '@element-plus/icons-vue'
import { listOwners, type OwnerView } from '@/api/eidolonData'
import {
  getMissionControlSnapshot,
  missionControlEventsUrl,
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

const experience = computed(() => snapshot.value?.experience)
const memory = computed(() => snapshot.value?.memory)
const devices = computed(() => snapshot.value?.devices || [])
const services = computed(() => snapshot.value?.services || [])
const jobs = computed(() => snapshot.value?.jobs || [])
const companions = computed(() => {
  const list = snapshot.value?.companions || []
  if (list.length) return list
  return snapshot.value?.companion ? [snapshot.value.companion] : []
})
const ownerLabel = computed(
  () => snapshot.value?.owner?.display_name || snapshot.value?.owner?.owner_id || '未选择 OWNER',
)
const companionLabel = computed(
  () => snapshot.value?.companion?.display_name || snapshot.value?.companion?.companion_id || '伙伴',
)
const onlineDevices = computed(() => devices.value.filter((d) => d.online).length)
const activeJobs = computed(
  () => jobs.value.filter((j) => ['running', 'queued', 'pending', 'active'].includes((j.status || '').toLowerCase())).length,
)
const sourceStatus = computed(() => snapshot.value?.source_status || [])
const degradedSources = computed(() => sourceStatus.value.filter((s) => !s.ok))
const activeTurn = computed<RuntimeTurn | null>(
  () => snapshot.value?.active_turn || snapshot.value?.recent_turns?.[0] || null,
)
const pipelineActive = computed(() => {
  const t = snapshot.value?.active_turn
  if (!t) return false
  return ['running', 'active', 'pending', 'queued'].includes((t.status || '').toLowerCase())
})

const recentEvents = computed(() => {
  const merged = [...liveEvents.value, ...(snapshot.value?.recent_events || [])]
  const seen = new Set<string>()
  return merged
    .filter((e) => (seen.has(e.event_id) ? false : (seen.add(e.event_id), true)))
    .slice(0, 20)
})

function svc(id: string): RuntimeService | null {
  return services.value.find((s) => s.service_id === id) || null
}
function tone(ok: boolean, active = false): string {
  if (!ok) return 'bad'
  return active ? 'live' : 'ok'
}

// ── SCHEMATIC LAYOUT (viewBox 1000 × 640) ───────────────────────────────────
const VB = { w: 1000, h: 640 }
function box(x: number, y: number, w: number, h: number) {
  return {
    left: `${(x / VB.w) * 100}%`,
    top: `${(y / VB.h) * 100}%`,
    width: `${(w / VB.w) * 100}%`,
    height: `${(h / VB.h) * 100}%`,
  }
}

const modules = computed(() => {
  const t = activeTurn.value
  const chOnline = !!svc('channel')?.online
  const agOnline = !!svc('agent')?.online
  const memOnline = !!svc('memory')?.online || (memory.value?.realms_total || 0) > 0
  const m = memory.value
  return [
    {
      id: 'bodies', code: 'SYS-01', title: '身体入口', box: box(40, 60, 236, 134),
      tone: tone(onlineDevices.value > 0), active: false,
      big: `${onlineDevices.value}/${devices.value.length}`, unit: '在线',
      rows: [
        ['NODE', devices.value[0] ? friendlyDeviceName(devices.value[0]) : '无身体接入'],
        ['MODE', devices.value[0]?.interaction_mode || devices.value[0]?.kind || '—'],
      ],
    },
    {
      id: 'channel', code: 'SYS-02', title: '感知通道', box: box(382, 60, 236, 134),
      tone: tone(chOnline, pipelineActive.value), active: pipelineActive.value,
      big: chOnline ? 'LINKED' : 'STANDBY', unit: 'STT · TTS',
      rows: [
        ['ROOM', activeTurn.value?.device_id ? 'ENGAGED' : (chOnline ? 'READY' : 'IDLE')],
        ['LAT', fmtLatency(svc('channel')?.latency_ms)],
      ],
    },
    {
      id: 'agent', code: 'CPU-00', title: '智能体引擎', box: box(724, 60, 236, 134),
      tone: tone(agOnline, pipelineActive.value), active: pipelineActive.value,
      big: (t?.status || 'idle').toUpperCase(), unit: t?.trigger || 'reasoner',
      rows: [
        ['LAT', fmtLatency(t?.latency_ms)],
        ['TOOLS', `×${t?.tool_names?.length ?? 0}`],
      ],
    },
    {
      id: 'memory', code: 'MEM-01', title: '主权记忆', box: box(724, 446, 236, 134),
      tone: tone(memOnline, (m?.last_recall_hits || 0) > 0), active: (m?.last_recall_hits || 0) > 0,
      big: `${m?.last_recall_hits ?? 0}`, unit: '召回命中',
      rows: [
        ['REALM', `${m?.realms_total ?? 0} · ${m?.runners_online ?? 0}/${m?.runners_total ?? 0} RUN`],
        ['WRITE', (m?.fanout_allowed ? m?.last_write_disposition || 'ALLOW' : 'HOLD') as string],
      ],
    },
    {
      id: 'tools', code: 'IO-01', title: '行动调度', box: box(382, 446, 236, 134),
      tone: activeJobs.value ? 'live' : (jobs.value.length ? 'ok' : 'idle'), active: activeJobs.value > 0,
      big: `${activeJobs.value}/${jobs.value.length}`, unit: '任务队列',
      rows: [
        ['KIND', jobs.value[0]?.kind || 'IDLE QUEUE'],
        ['VIA', jobs.value[0]?.provider || '—'],
      ],
    },
    {
      id: 'guard', code: 'SEC-01', title: '权限边界', box: box(40, 446, 236, 134),
      tone: degradedSources.value.length ? 'bad' : 'ok', active: false,
      big: degradedSources.value.length ? `${degradedSources.value.length}` : 'NOMINAL',
      unit: degradedSources.value.length ? '信号源降级' : '边界正常',
      rows: [
        ['PRIV', privacyModeLabel.value],
        ['SCOPE', 'AUTH · 脱敏 · 审计'],
      ],
    },
  ]
})

const ownerKernel = computed(() => ({
  box: box(438, 258, 124, 124),
  name: ownerLabel.value,
  completion: experience.value?.completion ?? 0,
  companions: companions.value.length || (snapshot.value?.companion ? 1 : 0),
}))

const linkCluster = computed(() => ({
  box: box(40, 250, 236, 150),
  leds: sourceStatus.value.map((s) => ({
    key: s.source,
    label: s.source.replace(/^data\./, '').replace(/^agent\./, 'a.').replace(/^memory\./, 'm.'),
    ok: s.ok,
    detail: s.detail,
  })),
}))

const segments = computed(() => {
  const t = activeTurn.value
  const hits = t?.memory_hits ?? 0
  const tools = t?.tool_names?.length ?? 0
  const act = pipelineActive.value
  return [
    { id: 's1', kind: 'sig', d: 'M276 127 H382', active: act, label: '① 身体→通道', lx: 329, ly: 114, anchor: 'middle' },
    { id: 's2', kind: 'sig', d: 'M618 127 H724', active: act, label: '② 输入→大脑', lx: 671, ly: 114, anchor: 'middle' },
    { id: 's3', kind: 'sig', d: 'M842 194 V446', active: act, label: `③ 召回 ${hits}`, lx: 852, ly: 326, anchor: 'start' },
    { id: 's4', kind: 'sig', d: 'M724 513 H618', active: act, label: `④ 工具 ×${tools}`, lx: 671, ly: 535, anchor: 'middle' },
    { id: 's5', kind: 'sig', d: 'M382 513 H276', active: act, label: '⑤ 权限校验', lx: 329, ly: 535, anchor: 'middle' },
    { id: 'sp1', kind: 'spine', d: 'M500 194 V258', active: true, label: '', lx: 0, ly: 0, anchor: 'middle' },
    { id: 'sp2', kind: 'spine', d: 'M500 382 V446', active: true, label: '', lx: 0, ly: 0, anchor: 'middle' },
  ]
})

const pulseDots = computed(() => {
  const out: { id: string; d: string; dur: string; begin: string; kind: string }[] = []
  for (const s of segments.value) {
    const n = s.active && s.kind === 'sig' ? 3 : 1
    const durN = s.active && s.kind === 'sig' ? 1.3 : 3.6
    for (let i = 0; i < n; i++) {
      out.push({ id: `${s.id}-${i}`, d: s.d, dur: `${durN}s`, begin: `${((i * durN) / n).toFixed(2)}s`, kind: s.kind })
    }
  }
  return out
})

const junctions = [
  { x: 276, y: 127 }, { x: 382, y: 127 }, { x: 618, y: 127 }, { x: 724, y: 127 },
  { x: 842, y: 194 }, { x: 842, y: 446 }, { x: 724, y: 513 }, { x: 618, y: 513 },
  { x: 382, y: 513 }, { x: 276, y: 513 }, { x: 500, y: 194 }, { x: 500, y: 446 },
]

const clock = computed(() => {
  const d = new Date(now.value)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
})
const snapshotAge = computed(() => timeAgo(snapshot.value?.generated_at))
const systemStateLabel = computed(() => {
  const s = experience.value?.system_state
  return { active: '处理中', working: '后台推进', watching: '感知中', standby: '待命' }[s || 'standby'] || 'STANDBY'
})
const stateTone = computed(() => {
  const s = experience.value?.system_state
  return { active: 'live', working: 'warn', watching: 'info', standby: 'idle' }[s || 'standby'] || 'idle'
})
const streamLabel = computed(() => ({ connecting: 'SYNC', live: 'LIVE', degraded: 'DEGRADED' })[streamState.value])
const streamTone = computed(() => ({ connecting: 'warn', live: 'ok', degraded: 'bad' })[streamState.value])
const privacyModeLabel = computed(() => {
  const mode = memory.value?.privacy_mode || activeTurn.value?.privacy_mode || 'safe'
  return { safe: '安全模式', summary: '摘要模式', restricted: '受限模式' }[mode] || '安全模式'
})

// ── lifecycle ───────────────────────────────────────────────────────────────
onMounted(async () => {
  await loadOwners()
  await refresh()
  openStream()
  pollTimer = window.setInterval(refresh, 8000)
  clockTimer = window.setInterval(() => (now.value = Date.now()), 1000)
})
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  if (clockTimer) window.clearInterval(clockTimer)
  if (stream) stream.close()
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
    error.value = e?.response?.status === 404 ? `Mission Control API 尚未加载：${detail}。` : detail
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
  stream.onerror = () => (streamState.value = 'degraded')
}

// ── formatting ────────────────────────────────────────────────────────────
function fmtLatency(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`
}
function timeAgo(iso: string | null | undefined) {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const s = Math.round((now.value - t) / 1000)
  if (s < 0) return 'now'
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`
}
function fmtTime(iso: string | null | undefined) {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function friendlyDeviceName(device: { name: string; kind: string; role: string }) {
  const text = `${device.name} ${device.kind} ${device.role}`.toLowerCase()
  if (text.includes('2.06') || text.includes('pocket') || text.includes('ptt')) return '随身对讲身体'
  if (text.includes('box')) return '房间语音身体'
  if (text.includes('camera') || text.includes('vision') || text.includes('atk')) return '视觉观察身体'
  if (text.includes('web')) return '网页身体'
  return device.role || '扩展身体'
}
function sourceLabel(source: string) {
  return ({ hub: 'HUB', channel: 'VOICE', agent: 'AGENT', memory: 'MEMORY', data: 'DATA', admin: 'ADMIN', mission_control: 'MCC' } as Record<string, string>)[source] || source.toUpperCase()
}
function privacyTag(mode: string) {
  return ({ safe: 'SAFE', summary: 'SUM', restricted: 'RSTR' } as Record<string, string>)[mode] || mode.toUpperCase()
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
</script>

<template>
  <main class="mc">
    <!-- ══ COMMAND STRIP ══ -->
    <header class="strip">
      <div class="brand">
        <span class="glyph"><el-icon><Aim /></el-icon></span>
        <div>
          <p class="eyebrow">EIDOLON · AGENT OS</p>
          <h1>MISSION CONTROL</h1>
        </div>
      </div>
      <div class="gauges">
        <div class="g"><span>SYS</span><b :class="stateTone">{{ systemStateLabel }}</b></div>
        <div class="g"><span>UPLINK</span><b :class="streamTone"><i class="led" />{{ streamLabel }}</b></div>
        <div class="g"><span>INTEGRITY</span><b class="ok">{{ experience?.completion ?? 0 }}%</b></div>
        <div class="g"><span>OWNER</span><b class="cyan">{{ ownerLabel }}</b></div>
        <div class="g clock"><b>{{ clock }}</b><span>SNAP {{ snapshotAge }}</span></div>
      </div>
      <div class="ctrl">
        <el-select v-model="ownerId" class="owner-pick" filterable placeholder="OWNER">
          <el-option v-for="o in owners" :key="o.owner_id" :label="o.display_name || o.owner_id" :value="o.owner_id" />
        </el-select>
        <button class="icon-btn" :disabled="loading" title="刷新快照" @click="refresh">
          <el-icon :class="{ spin: loading }"><Refresh /></el-icon>
        </button>
      </div>
    </header>

    <p v-if="error" class="mc-error">{{ error }}</p>

    <!-- ══ SYSTEM SCHEMATIC ══ -->
    <section class="board">
      <div class="board-title">
        <span>SYSTEM SCHEMATIC · REV 1.0</span>
        <span>SOVEREIGN BUS · {{ companionLabel }} · {{ privacyModeLabel }}</span>
      </div>

      <div class="schematic">
        <svg class="wires" :viewBox="`0 0 ${VB.w} ${VB.h}`" preserveAspectRatio="xMidYMid meet">
          <defs>
            <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M40 0 H0 V40" fill="none" stroke="rgba(34,211,238,.05)" stroke-width="1" vector-effect="non-scaling-stroke" />
            </pattern>
            <pattern id="minor" width="8" height="8" patternUnits="userSpaceOnUse">
              <path d="M8 0 H0 V8" fill="none" stroke="rgba(34,211,238,.025)" stroke-width="1" vector-effect="non-scaling-stroke" />
            </pattern>
          </defs>

          <rect x="0" y="0" :width="VB.w" :height="VB.h" fill="url(#minor)" />
          <rect x="0" y="0" :width="VB.w" :height="VB.h" fill="url(#grid)" />
          <rect x="8" y="8" :width="VB.w - 16" :height="VB.h - 16" fill="none" stroke="rgba(34,211,238,.14)" stroke-width="1" stroke-dasharray="2 6" vector-effect="non-scaling-stroke" />

          <!-- corner registration marks -->
          <g stroke="rgba(34,211,238,.4)" stroke-width="1" vector-effect="non-scaling-stroke">
            <path d="M20 20 h16 M20 20 v16" /><path d="M980 20 h-16 M980 20 v16" />
            <path d="M20 620 h16 M20 620 v-16" /><path d="M980 620 h-16 M980 620 v-16" />
          </g>

          <!-- bus traces -->
          <g fill="none" stroke-linecap="round">
            <path v-for="s in segments" :key="s.id + '-base'" :d="s.d"
              :class="['trace', s.kind, { hot: s.active }]" vector-effect="non-scaling-stroke" />
          </g>

          <!-- junction solder points -->
          <circle v-for="(j, i) in junctions" :key="'j' + i" :cx="j.x" :cy="j.y" r="3.2" class="solder" />

          <!-- travelling pulses -->
          <circle v-for="p in pulseDots" :key="p.id" r="3.4" :class="['pulse', p.kind]">
            <animateMotion :dur="p.dur" :begin="p.begin" repeatCount="indefinite" :path="p.d" calcMode="linear" />
          </circle>

          <!-- bus annotations -->
          <text v-for="s in segments.filter((x) => x.label)" :key="s.id + '-t'" :x="s.lx" :y="s.ly"
            :text-anchor="s.anchor" :class="['anno', { hot: s.active }]">{{ s.label }}</text>
        </svg>

        <!-- subsystem modules -->
        <article v-for="m in modules" :key="m.id" class="mod" :class="[`t-${m.tone}`, { on: m.active }]" :style="m.box">
          <header>
            <b>{{ m.title }}</b>
            <span>{{ m.code }}</span>
            <i class="led" />
          </header>
          <div class="mod-big">
            <strong>{{ m.big }}</strong>
            <em>{{ m.unit }}</em>
          </div>
          <dl>
            <div v-for="(r, i) in m.rows" :key="i"><dt>{{ r[0] }}</dt><dd>{{ r[1] }}</dd></div>
          </dl>
        </article>

        <!-- LINK / source cluster -->
        <article class="link-cluster" :style="linkCluster.box">
          <header><b>信号源</b><span>LINK</span></header>
          <div class="link-grid">
            <span v-for="l in linkCluster.leds" :key="l.key" class="pip" :class="l.ok ? 't-ok' : 't-bad'" :title="l.detail">
              <i class="led" />{{ l.label }}
            </span>
          </div>
        </article>

        <!-- OWNER kernel -->
        <div class="kernel" :style="ownerKernel.box">
          <span>OWNER</span>
          <strong>{{ ownerKernel.completion }}%</strong>
          <b>{{ ownerKernel.name }}</b>
          <em>{{ ownerKernel.companions }} COMPANION</em>
        </div>
      </div>
    </section>

    <!-- ══ TELEMETRY CRAWL ══ -->
    <footer class="crawl">
      <span class="crawl-tag"><i class="led" :class="'t-' + streamTone" />TELEMETRY</span>
      <div class="crawl-track">
        <div class="crawl-run" v-if="recentEvents.length">
          <template v-for="pass in 2" :key="pass">
            <span v-for="e in recentEvents" :key="pass + e.event_id" class="ev" :class="'sev-' + e.severity">
              <em>{{ fmtTime(e.ts) }}</em>
              <b>{{ sourceLabel(e.source) }}</b>
              {{ friendlyEventSummary(e) }}
              <u :class="{ warn: e.privacy !== 'safe' }">{{ privacyTag(e.privacy) }}</u>
              <span class="diamond">◇</span>
            </span>
          </template>
        </div>
        <div class="crawl-run" v-else><span class="ev">等待实时信号进入视野…</span></div>
      </div>
      <span class="crawl-privacy"><el-icon><Lock /></el-icon>{{ snapshot?.privacy_notice ? '隐私默认开启' : privacyModeLabel }}</span>
    </footer>
  </main>
</template>

<style scoped>
.mc {
  --cyan: var(--eid-accent, #22d3ee);
  --amber: var(--eid-accent-warm, #fbbf24);
  --ok: var(--eid-success, #34d399);
  --warn: var(--eid-warning, #fbbf24);
  --bad: var(--eid-danger, #fb7185);
  --info: var(--eid-info, #38bdf8);
  --line: rgba(34, 211, 238, 0.16);
  --line-soft: rgba(34, 211, 238, 0.08);
  --txt-1: var(--eid-text-primary, #eef7f8);
  --txt-2: var(--eid-text-secondary, #9fb0b7);
  --txt-3: var(--eid-text-muted, #64747c);
  --panel: linear-gradient(160deg, rgba(17, 27, 31, 0.7), rgba(7, 12, 15, 0.86));
  position: relative;
  margin: -20px;
  padding: 14px 16px 12px;
  min-height: calc(100vh - var(--eid-header-h, 56px));
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: var(--txt-1);
  font-family: var(--eid-font-sans);
  background: radial-gradient(circle at 50% -10%, rgba(34, 211, 238, 0.07), transparent 45%), #04070a;
}
.eyebrow, .g span, .board-title span, .crawl-tag, .crawl-privacy { font: 700 10px/1.2 var(--eid-font-mono); letter-spacing: 0.1em; text-transform: uppercase; color: var(--txt-3); }
h1 { margin: 2px 0 0; font: 800 18px/1 var(--eid-font-mono); letter-spacing: 0.16em; }
.led { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 9px currentColor; flex: 0 0 auto; }
.ok, .t-ok { color: var(--ok); }
.warn { color: var(--warn); }
.bad, .t-bad { color: var(--bad); }
.info { color: var(--info); }
.idle, .t-idle { color: var(--txt-3); }
.cyan { color: var(--cyan); }
.live, .t-live { color: var(--cyan); }

/* command strip */
.strip { display: flex; align-items: center; gap: 22px; padding: 11px 16px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
.brand { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; }
.glyph { display: grid; place-items: center; width: 40px; height: 40px; border: 1px solid rgba(34, 211, 238, 0.4); border-radius: 9px; color: var(--cyan); font-size: 19px; background: rgba(34, 211, 238, 0.08); box-shadow: inset 0 0 22px rgba(34, 211, 238, 0.14); }
.gauges { display: flex; margin-left: auto; }
.g { padding: 1px 18px; border-left: 1px solid var(--line-soft); }
.g:first-child { border-left: 0; }
.g b { display: flex; align-items: center; gap: 6px; margin-top: 5px; font: 800 15px/1 var(--eid-font-mono); }
.g b .led { width: 6px; height: 6px; }
.g.clock b { font-size: 20px; color: var(--cyan); text-shadow: 0 0 16px rgba(34, 211, 238, 0.4); }
.g.clock span { margin-top: 4px; text-transform: none; letter-spacing: 0; }
.ctrl { display: flex; align-items: center; gap: 9px; flex: 0 0 auto; }
.owner-pick { width: 170px; }
.icon-btn { display: grid; place-items: center; width: 37px; height: 37px; border: 1px solid var(--line); border-radius: 8px; color: var(--cyan); background: rgba(34, 211, 238, 0.06); cursor: pointer; }
.icon-btn:hover { background: rgba(34, 211, 238, 0.14); }
.icon-btn:disabled { cursor: wait; opacity: 0.6; }

.mc-error { padding: 10px 14px; border: 1px solid rgba(251, 113, 133, 0.4); border-radius: 8px; color: #fecdd3; background: rgba(75, 13, 27, 0.5); }

/* board */
.board { position: relative; flex: 1 1 auto; display: flex; flex-direction: column; padding: 12px 14px 14px; border: 1px solid var(--line); border-radius: 12px; background: radial-gradient(circle at 50% 42%, rgba(34, 211, 238, 0.05), transparent 55%), var(--panel); box-shadow: inset 0 1px rgba(255, 255, 255, 0.04), 0 24px 60px rgba(0, 0, 0, 0.38); }
.board-title { display: flex; justify-content: space-between; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--line-soft); }
.board-title span { color: var(--txt-2); }

.schematic { position: relative; flex: 1 1 auto; width: 100%; aspect-ratio: 1000 / 640; max-height: 74vh; margin: 0 auto; }
.wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.trace { stroke: rgba(34, 211, 238, 0.5); stroke-width: 2.4; filter: drop-shadow(0 0 2px rgba(34, 211, 238, 0.35)); }
.trace.spine { stroke: rgba(251, 191, 36, 0.6); stroke-width: 2; stroke-dasharray: 5 4; filter: drop-shadow(0 0 2px rgba(251, 191, 36, 0.4)); }
.trace.hot { stroke: var(--cyan); stroke-width: 3; filter: drop-shadow(0 0 6px rgba(34, 211, 238, 0.85)); }
.solder { fill: #061417; stroke: var(--cyan); stroke-width: 1.6; vector-effect: non-scaling-stroke; filter: drop-shadow(0 0 3px rgba(34, 211, 238, 0.5)); }
.pulse { fill: #d6fbff; filter: drop-shadow(0 0 7px var(--cyan)); }
.pulse.spine { fill: #fff0c6; filter: drop-shadow(0 0 6px var(--amber)); }
.anno { fill: var(--txt-2); font: 700 13px var(--eid-font-mono); letter-spacing: 0.04em; }
.anno.hot { fill: var(--cyan); }

/* subsystem module */
.mod { position: absolute; display: flex; flex-direction: column; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: linear-gradient(150deg, rgba(13, 22, 26, 0.92), rgba(6, 11, 14, 0.94)); box-shadow: 0 12px 26px rgba(0, 0, 0, 0.4); overflow: hidden; transition: border-color 0.3s, box-shadow 0.3s; }
.mod::before { content: ""; position: absolute; top: 0; left: 10px; right: 10px; height: 1px; background: linear-gradient(90deg, transparent, currentColor, transparent); opacity: 0.5; }
.mod header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; color: currentColor; }
.mod header b { flex: 1 1 auto; color: var(--txt-1); font: 700 13.5px/1 var(--eid-font-sans); }
.mod header span { font: 700 9px/1 var(--eid-font-mono); color: var(--txt-3); letter-spacing: 0.08em; }
.mod-big { display: flex; align-items: baseline; gap: 8px; }
.mod-big strong { font: 900 30px/1 var(--eid-font-mono); color: var(--txt-1); letter-spacing: 0.01em; }
.mod-big em { font: 600 11px/1 var(--eid-font-sans); color: var(--txt-2); font-style: normal; }
.mod dl { margin: auto 0 0; display: grid; gap: 3px; }
.mod dl div { display: flex; justify-content: space-between; gap: 8px; }
.mod dt { font: 700 9px/1.4 var(--eid-font-mono); color: var(--txt-3); letter-spacing: 0.06em; }
.mod dd { margin: 0; font: 600 10.5px/1.4 var(--eid-font-mono); color: var(--txt-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 62%; }
.mod.t-ok { color: var(--ok); }
.mod.t-live { color: var(--cyan); border-color: rgba(34, 211, 238, 0.4); }
.mod.t-bad { color: var(--bad); border-color: rgba(251, 113, 133, 0.4); }
.mod.t-idle { color: var(--txt-3); }
.mod.on { border-color: rgba(34, 211, 238, 0.55); box-shadow: 0 0 26px rgba(34, 211, 238, 0.18), 0 12px 26px rgba(0, 0, 0, 0.4); }
.mod.on header .led { animation: pulse 1.5s ease-in-out infinite; }

/* link cluster */
.link-cluster { position: absolute; padding: 9px 11px; border: 1px solid var(--line-soft); border-radius: 8px; background: rgba(7, 13, 16, 0.72); }
.link-cluster header { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.link-cluster header b { color: var(--txt-1); font: 700 12px/1 var(--eid-font-sans); }
.link-cluster header span { font: 700 9px/1 var(--eid-font-mono); color: var(--txt-3); letter-spacing: 0.1em; }
.link-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 8px; }
.pip { display: flex; align-items: center; gap: 6px; font: 600 9.5px/1.3 var(--eid-font-mono); color: var(--txt-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pip .led { width: 6px; height: 6px; }

/* owner kernel */
.kernel { position: absolute; display: grid; place-content: center; text-align: center; border-radius: 50%; border: 1px solid rgba(251, 191, 36, 0.5); background: radial-gradient(circle at 40% 32%, rgba(251, 191, 36, 0.22), rgba(6, 15, 17, 0.96) 66%); box-shadow: 0 0 44px rgba(251, 191, 36, 0.22), inset 0 0 30px rgba(251, 191, 36, 0.12); animation: breathe 5s ease-in-out infinite; }
.kernel span { font: 700 8.5px/1 var(--eid-font-mono); letter-spacing: 0.16em; color: var(--amber); }
.kernel strong { display: block; margin: 3px 0 2px; font: 900 26px/1 var(--eid-font-mono); color: #fde9b0; text-shadow: 0 0 18px rgba(251, 191, 36, 0.4); }
.kernel b { display: block; max-width: 108px; margin: 0 auto; font: 700 11px/1.1 var(--eid-font-sans); color: var(--txt-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kernel em { font: 600 8px/1.2 var(--eid-font-mono); color: var(--txt-3); font-style: normal; letter-spacing: 0.06em; }

/* telemetry crawl */
.crawl { display: flex; align-items: center; gap: 14px; padding: 0 14px; height: 40px; border: 1px solid var(--line); border-radius: 9px; background: rgba(6, 11, 14, 0.8); overflow: hidden; }
.crawl-tag { display: inline-flex; align-items: center; gap: 7px; flex: 0 0 auto; color: var(--txt-2); }
.crawl-tag .led { width: 6px; height: 6px; }
.crawl-track { flex: 1 1 auto; overflow: hidden; mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent); }
.crawl-run { display: inline-flex; align-items: center; white-space: nowrap; animation: crawl 60s linear infinite; }
.crawl-run:hover { animation-play-state: paused; }
.ev { display: inline-flex; align-items: center; gap: 8px; margin-right: 4px; font-size: 12.5px; color: var(--txt-2); }
.ev em { font: 700 11px/1 var(--eid-font-mono); color: var(--txt-3); font-style: normal; }
.ev b { font: 700 10px/1 var(--eid-font-mono); color: var(--cyan); letter-spacing: 0.05em; }
.ev u { text-decoration: none; font: 700 9px/1 var(--eid-font-mono); color: var(--txt-3); }
.ev u.warn { color: var(--amber); }
.ev .diamond { color: var(--line); margin: 0 6px; }
.ev.sev-error { color: #fecdd3; }
.ev.sev-warn { color: #fde68a; }
.crawl-privacy { display: inline-flex; align-items: center; gap: 6px; flex: 0 0 auto; color: var(--amber); }

.spin { animation: spin 900ms linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
@keyframes breathe { 0%, 100% { box-shadow: 0 0 40px rgba(251, 191, 36, 0.18), inset 0 0 28px rgba(251, 191, 36, 0.1); } 50% { box-shadow: 0 0 60px rgba(251, 191, 36, 0.3), inset 0 0 34px rgba(251, 191, 36, 0.16); } }
@keyframes crawl { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@media (prefers-reduced-motion: reduce) { .crawl-run, .kernel, .mod.on header .led, .pulse { animation: none !important; } }

/* responsive: stack the schematic under narrow widths */
@media (max-width: 820px) {
  .gauges { flex-wrap: wrap; }
  .schematic { aspect-ratio: auto; max-height: none; display: flex; flex-direction: column; gap: 8px; }
  .wires, .link-cluster, .kernel { display: none; }
  .mod { position: static; width: 100%; height: auto; min-height: 96px; }
}
</style>
