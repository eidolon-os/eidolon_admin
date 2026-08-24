<script setup lang="ts">
// The primary view: a sovereign-domain constellation. Owner sun at the centre,
// companion planets on an orbit, each with three asset moons (body / memory /
// activity). Geometry + orbital motion are presentational and live here; the
// data comes from the composable's `companionUnits`.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { RuntimeActivity, RuntimeDevice } from '@/api/missionControl'
import { currentStageKey, directedLegPath, eventToPulse, eventTone, flowDur, flowEventDur, flowLegs, flowPath, flowStagger, shouldFlow, stageMoon, type FlowLeg, type PulseTone } from '../flow'
import { devicePresenceClass, devicePresenceLabel, deviceShort, deviceType, fmtLatency, genomeStateLabel, memoryRealmStateLabel, statusClass } from '../format'
import { activityKindLabel, currentActivityHop, isActiveActivity, summarizeActivityBadges } from '../activity'
import type { CompanionUnit, GalaxyNode, Sat, SatKind, SatTone } from '../types'
import type { MissionControlStream } from '../useMissionControlStream'

const props = defineProps<{ mc: MissionControlStream; focusedId?: string; selectedKind?: SatKind }>()
const emit = defineEmits<{
  (e: 'open-owner'): void
  (e: 'open-companion', c: CompanionUnit): void
  (e: 'open-moon', s: Sat): void
  (e: 'clear-focus'): void
  (e: 'select-turn', turnId: string, companionId: string): void
}>()

const { companionUnits, unboundDevices, ownerName, activePulses, pipelineActive, selectedTurnId, highlightedEvent, now } = props.mc

// D5 ignite/settle: when a turn takes the pipeline idle→live, the sovereign core
// flares once ("点火"). Wire hot/cold + node glows ease via CSS transition
// ("收束") rather than snapping on each snapshot swap.
const igniting = ref(false)
let igniteTimer = 0
watch(pipelineActive, (live, was) => {
  if (live && !was) {
    igniting.value = true
    clearTimeout(igniteTimer)
    igniteTimer = window.setTimeout(() => (igniting.value = false), 900)
  }
})
onBeforeUnmount(() => clearTimeout(igniteTimer))

// ── geometry ──────────────────────────────────────────────────────────
const VBW = 1000, VBH = 700, CX = 500, CY = 350, RX = 306, RY = 220, RSAT = 92, PI = Math.PI

// Runtime state shown on every planet, always visible (no hover). This is the
// extension point for the future device→agent→return event flow.
function runtime(c: CompanionUnit): { text: string; cls: string } {
  if (c.activeActivity) {
    const hop = currentActivityHop(c.activeActivity)
    return {
      text: c.activeActivity.kind === 'voice_turn'
        ? c.activeVoiceTurn?.latency_ms != null ? `对话中 · ${fmtLatency(c.activeVoiceTurn.latency_ms)}` : '对话中'
        : `${activityKindLabel(c.activeActivity.kind)} · ${hop?.label || c.activeActivity.status}`,
      cls: 'live',
    }
  }
  if (c.turn) return { text: `已选 · ${(c.turn.status || '').toUpperCase()}`, cls: statusClass(c.turn.status) }
  if (c.jobs.length) return { text: `${c.jobs.length} 个任务`, cls: 'warn' }
  return { text: '空闲', cls: 'idle' }
}

function ptStyle(x: number, y: number) {
  return { left: `${(x / VBW) * 100}%`, top: `${(y / VBH) * 100}%` }
}
function satOf(c: CompanionUnit, kind: SatKind): Omit<Sat, 'c' | 'x' | 'y' | 'link'> {
  if (kind === 'body') {
    const n = c.devices.length
    return {
      kind, label: '身体', glyph: '⬡',
      value: n ? n === 1 ? deviceType(c.devices[0]!) : `${n} 个身体` : '未绑定',
      tone: c.devices.some((d) => d.online) ? 'ok' : n ? 'idle' : 'off',
      empty: !n, accent: 'cyan',
    }
  }
  if (kind === 'mem') {
    return {
      kind, label: '记忆', glyph: '◈',
      value: c.realm ? (c.isActiveRealm ? `${c.recall} 召回` : '已配置') : '无空间',
      tone: c.realm ? 'ok' : 'off', empty: !c.realm, accent: 'yellow',
    }
  }
  return {
    kind, label: '活动', glyph: '⚡',
    value: c.activeActivity ? activityKindLabel(c.activeActivity.kind) : c.activities.length ? `${c.activities.length} 条` : '空闲',
    tone: c.activeActivity ? 'live' : c.activities.some((a) => a.outcome === 'failure') ? 'bad' : c.activities.length ? 'ok' : 'off',
    empty: !c.activities.length && !c.activeActivity, accent: 'mag',
  }
}

const galaxy = computed(() => {
  const comps = companionUnits.value
  const N = comps.length || 1
  // Diagonal distribution (rotated −45°): small N never collapses onto the flat
  // horizontal axis, so companions use the vertical extent instead of leaving a
  // void above and below. N=2 → upper-right + lower-left; N=4 → an X.
  const start = -45
  // Static placement: planets and moons sit at fixed positions so runtime state
  // is glanceable and every node is instantly clickable (no orbital chase).
  const nodes: GalaxyNode[] = comps.map((c, i) => {
    const a = (start + (i * 360) / N) * (PI / 180)
    const x = CX + RX * Math.cos(a), y = CY + RY * Math.sin(a)
    const sats: Sat[] = (['body', 'mem', 'act'] as SatKind[]).map((kind, si) => {
      const sa = a + ((si - 1) * 42) * (PI / 180)
      const sx = x + RSAT * Math.cos(sa), sy = y + RSAT * Math.sin(sa)
      return { ...satOf(c, kind), c, x: sx, y: sy, link: `M${x.toFixed(1)} ${y.toFixed(1)} L${sx.toFixed(1)} ${sy.toFixed(1)}` }
    })
    return { c, x, y, link: `M${CX} ${CY} L${x.toFixed(1)} ${y.toFixed(1)}`, active: !!c.activeActivity, sats }
  })
  const devicePorts = nodes.flatMap((node) => {
    const body = node.sats.find((sat) => sat.kind === 'body')
    if (!body) return []
    const activeDeviceIds = new Set(
      node.c.activities
        .filter(isActiveActivity)
        .flatMap((activity) => [activity.origin_device_id, ...activity.target_device_ids])
        .filter((id): id is string => !!id),
    )
    return node.c.devices.slice(0, 5).map((device, index) => {
      const angle = (-110 + index * (220 / Math.max(1, Math.min(4, node.c.devices.length - 1)))) * (PI / 180)
      const x = body.x + 54 * Math.cos(angle)
      const y = body.y + 54 * Math.sin(angle)
      return {
        device, companionId: node.c.id, body, x, y,
        active: activeDeviceIds.has(device.device_id),
        link: `M${body.x.toFixed(1)} ${body.y.toFixed(1)} L${x.toFixed(1)} ${y.toFixed(1)}`,
      }
    })
  })
  return {
    nodes, sats: nodes.flatMap((n) => n.sats), devicePorts,
    links: nodes.map((n) => ({ id: n.c.id, d: n.link, active: n.active })),
  }
})

// Per-companion internal circulation. Focused or low-density active companions
// circulate independently; body / memory legs light per device presence and
// scoped voice evidence. Reuses the same `.pulse` + <animateMotion> mechanism
// as the sun→planet line — no new animation machinery, no new deps.
const flows = computed(() => {
  const nodes = galaxy.value.nodes
  const activeCount = nodes.filter((n) => !!n.c.activeActivity).length
  return nodes
    .filter((n) => shouldFlow(n.c.id, props.focusedId, !!n.c.activeActivity, activeCount))
    .map((n) => {
      const body = n.sats.find((s) => s.kind === 'body')
      const mem = n.sats.find((s) => s.kind === 'mem')
      const act = n.sats.find((s) => s.kind === 'act')
      const legs = flowLegs(n.c)
      const path = body && mem && act ? flowPath({ x: n.x, y: n.y }, body, mem, act, legs) : ''
      return { id: n.c.id, path, legs, dur: flowDur(path), stagger: flowStagger(path) }
    })
    .filter((f) => f.path)
})
// Which sat legs a flow lights, keyed `id:kind` → brightness, so the underlying
// leg wire can glow as an energized conduit (memory leg scales with hits).
const flowLegBright = computed(() => {
  const m = new Map<string, number>()
  for (const f of flows.value) {
    if (f.legs.body) m.set(`${f.id}:body`, 1)
    if (f.legs.mem) m.set(`${f.id}:mem`, f.legs.memBright)
    if (f.legs.act) m.set(`${f.id}:act`, 1)
  }
  return m
})
function legBright(s: Sat): number | undefined {
  return flowLegBright.value.get(`${s.c.id}:${s.kind}`)
}
// Concrete (JS-resolved) glow for a lit flow leg. Chrome doesn't resolve
// `calc(var(--fb))` inside SVG stroke-opacity / filter, so we bind the numbers
// directly; the CSS rule keeps only the transition so changes still ease.
function flowStyle(s: Sat): Record<string, string> | undefined {
  const b = legBright(s)
  if (b === undefined) return undefined
  const color = s.kind === 'mem' ? '251, 255, 159' : s.kind === 'act' ? '255, 138, 200' : '0, 234, 255'
  return {
    stroke: `rgba(${color}, ${(0.55 + 0.35 * b).toFixed(2)})`,
    strokeOpacity: (0.4 + 0.5 * b).toFixed(3),
    filter: `drop-shadow(0 0 ${(2 + 2 * b).toFixed(1)}px rgba(${color}, ${(0.5 * b).toFixed(2)}))`,
  }
}

// Resolve each in-flight event pulse to a directed dart on the right
// companion's leg. Owner-wide pulses are on by default; `?flow2=off` disables
// them. Colour comes from tone (leg hue, or alarm palette).
const EVENT_DUR = flowEventDur()
// Normal darts take the leg's own hue; warn/bad override to the alarm palette
// (matches the cockpit tone tokens) so failures read at a glance regardless of leg.
const LEG_COLOR: Record<FlowLeg, string> = { body: '#9ff0ff', mem: '#fbff9f', act: '#ff8ac8' }
const TONE_COLOR: Record<Exclude<PulseTone, 'normal'>, string> = { warn: '#f7ff4a', bad: '#ff2e88' }
function pulseColor(leg: FlowLeg, tone: PulseTone): string {
  return tone === 'normal' ? LEG_COLOR[leg] : TONE_COLOR[tone]
}
interface EventPulseVM {
  id: string
  path: string
  style: Record<string, string>
}
const eventPulses = computed<EventPulseVM[]>(() =>
  activePulses.value
    .map((p): EventPulseVM | null => {
      const node = galaxy.value.nodes.find((n) => n.c.id === p.companionId)
      const moon = node?.sats.find((s) => s.kind === p.leg)
      if (!node || !moon) return null
      const port = p.leg === 'body' && p.deviceId
        ? galaxy.value.devicePorts.find((item) => item.companionId === p.companionId && item.device.device_id === p.deviceId)
        : null
      const color = pulseColor(p.leg, p.tone)
      return {
        id: p.id,
        path: port ? directedDevicePath({ x: node.x, y: node.y }, moon, port, p.dir) : directedLegPath({ x: node.x, y: node.y }, moon, p.dir),
        style: { fill: color, filter: `drop-shadow(0 0 7px ${color})` },
      }
    })
    .filter((p): p is EventPulseVM => p !== null),
)

const highlightedPath = computed<EventPulseVM | null>(() => {
  const event = highlightedEvent.value
  if (!event?.companion_id) return null
  const pulse = eventToPulse(event)
  if (!pulse) return null
  const node = galaxy.value.nodes.find((item) => item.c.id === event.companion_id)
  const moon = node?.sats.find((sat) => sat.kind === pulse.leg)
  if (!node || !moon) return null
  const port = pulse.leg === 'body' && event.device_id
    ? galaxy.value.devicePorts.find((item) => item.companionId === event.companion_id && item.device.device_id === event.device_id)
    : null
  const color = pulseColor(pulse.leg, eventTone(event.severity, event.outcome))
  return {
    id: event.event_id,
    path: port ? directedDevicePath({ x: node.x, y: node.y }, moon, port, pulse.dir) : directedLegPath({ x: node.x, y: node.y }, moon, pulse.dir),
    style: { stroke: color, filter: `drop-shadow(0 0 9px ${color})` },
  }
})

// §one signal: light the moon matching each active companion's current stage, so
// the constellation points at the same moment as the bus wavefront (hot service)
// and the trace playhead. Keyed `id:kind`.
const stageHere = computed(() => {
  const m = new Set<string>()
  for (const n of galaxy.value.nodes) {
    const stage = n.c.activeActivity
      ? currentActivityHop(n.c.activeActivity)?.stage || ''
      : currentStageKey(n.c.turn)
    const moon = stageMoon(stage)
    if (moon) m.add(`${n.c.id}:${moon}`)
  }
  return m
})
function isStageHere(s: Sat): boolean {
  return stageHere.value.has(`${s.c.id}:${s.kind}`)
}

const activityBadges = computed(() => galaxy.value.nodes.flatMap((node) => {
  const activity = node.sats.find((sat) => sat.kind === 'act')
  if (!activity) return []
  return summarizeActivityBadges(node.c.activities, now.value).map((group, index) => {
    const item = group.activity
    const angle = (-90 + index * 42) * (PI / 180)
    const radius = 43
    return {
      activity: item,
      activityMoon: activity,
      label: group.label,
      companionId: node.c.id,
      x: activity.x + radius * Math.cos(angle),
      y: activity.y + radius * Math.sin(angle),
      live: isActiveActivity(item),
    }
  })
}))

function directedDevicePath(
  brain: { x: number; y: number },
  body: { x: number; y: number },
  port: { x: number; y: number },
  direction: 'in' | 'out',
): string {
  const inbound = `M${port.x.toFixed(1)} ${port.y.toFixed(1)} L${body.x.toFixed(1)} ${body.y.toFixed(1)} L${brain.x.toFixed(1)} ${brain.y.toFixed(1)}`
  return direction === 'in'
    ? inbound
    : `M${brain.x.toFixed(1)} ${brain.y.toFixed(1)} L${body.x.toFixed(1)} ${body.y.toFixed(1)} L${port.x.toFixed(1)} ${port.y.toFixed(1)}`
}

function openActivityBadge(activity: RuntimeActivity, companionId: string, moon: Sat) {
  if (activity.turn_id) emit('select-turn', activity.turn_id, companionId)
  else emit('open-moon', moon)
}

function turnTone(status: string, outcome: string): SatTone {
  if (outcome === 'failure' || ['failed', 'error', 'errored'].includes(status)) return 'bad'
  if (outcome === 'denied' || ['rejected', 'deferred'].includes(status)) return 'warn'
  if (['running', 'pending', 'generating', 'speaking'].includes(status)) return 'live'
  return 'ok'
}

function deviceOnline(d: RuntimeDevice) {
  return d.online
}
</script>

<template>
  <section class="galaxy" :class="{ 'has-focus': !!focusedId }" @click="$emit('clear-focus')">
    <svg class="gx-wires" :viewBox="`0 0 ${VBW} ${VBH}`" preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="sun" cx="50%" cy="42%" r="60%">
          <stop offset="0%" stop-color="#ffffff" /><stop offset="34%" stop-color="#ff2e88" /><stop offset="70%" stop-color="rgba(164,75,255,.45)" /><stop offset="100%" stop-color="rgba(164,75,255,0)" />
        </radialGradient>
      </defs>
      <ellipse :cx="CX" :cy="CY" :rx="RX" :ry="RY" fill="none" stroke="rgba(0,234,255,.14)" stroke-width="1" stroke-dasharray="3 7" class="orbit-ring" />
      <g>
        <circle :cx="CX" :cy="CY" r="96" fill="none" stroke="rgba(0,234,255,.22)" stroke-width="1" stroke-dasharray="4 6">
          <animateTransform attributeName="transform" type="rotate" :from="`0 ${CX} ${CY}`" :to="`360 ${CX} ${CY}`" dur="28s" repeatCount="indefinite" />
        </circle>
        <circle :cx="CX" :cy="CY" r="120" fill="none" stroke="rgba(164,75,255,.2)" stroke-width="1" stroke-dasharray="2 10">
          <animateTransform attributeName="transform" type="rotate" :from="`360 ${CX} ${CY}`" :to="`0 ${CX} ${CY}`" dur="40s" repeatCount="indefinite" />
        </circle>
      </g>
      <path
        v-for="s in galaxy.sats"
        :key="'sl' + s.c.id + s.kind"
        :d="s.link"
        class="wire sat"
        :class="[`link-${s.kind}`, { dim: s.empty, flow: legBright(s) !== undefined, selected: s.c.id === focusedId }]"
        :style="flowStyle(s)"
      />
      <path
        v-for="port in galaxy.devicePorts"
        :key="'dl-' + port.device.device_id"
        :d="port.link"
        class="wire device-link"
        :class="{ hot: port.active, offline: !port.device.online }"
      />
      <path v-for="l in galaxy.links" :key="'cl' + l.id" :d="l.d" class="wire comp ownership" :class="{ hot: l.active, selected: l.id === focusedId }" />
      <!-- Companion internal circulation: body↔brain↔memory loop (focused + active only). -->
      <template v-for="f in flows" :key="'fp' + f.id">
        <circle r="3.4" class="pulse flow-dot"><animateMotion :dur="f.dur" repeatCount="indefinite" :path="f.path" /></circle>
        <circle r="3.4" class="pulse flow-dot"><animateMotion :dur="f.dur" :begin="f.stagger" repeatCount="indefinite" :path="f.path" /></circle>
      </template>
      <!-- One-shot directed pulses fired by observed live events (?flow2). -->
      <path v-if="highlightedPath" :d="highlightedPath.path" class="event-highlight" :style="highlightedPath.style" />
      <circle v-for="p in eventPulses" :key="p.id" r="4.4" class="pulse event-dot" :style="p.style">
        <animateMotion :dur="EVENT_DUR" fill="freeze" :path="p.path" />
      </circle>
      <circle :cx="CX" :cy="CY" r="150" fill="url(#sun)" opacity="0.5" />
    </svg>

    <div class="gx-owner" :class="{ igniting }" :style="ptStyle(CX, CY)" title="点击查看主人全景" @click.stop="$emit('open-owner')">
      <span class="o-kick">OWNER · 主人</span>
      <strong>{{ ownerName }}</strong>
      <em class="o-sub">{{ companionUnits.length }} 位伙伴</em>
    </div>

    <button
      v-for="bead in activityBadges"
      :key="'activity-' + bead.activity.activity_id"
      class="gx-activity"
      :class="[
        't-' + turnTone(bead.activity.status, bead.activity.outcome),
        { selected: selectedTurnId === bead.activity.turn_id, live: bead.live },
      ]"
      :style="ptStyle(bead.x, bead.y)"
      :title="`${activityKindLabel(bead.activity.kind)} · ${bead.activity.summary}`"
      @click.stop="openActivityBadge(bead.activity, bead.companionId, bead.activityMoon)"
    ><span>{{ bead.label }}</span></button>

    <button
      v-for="port in galaxy.devicePorts"
      :key="'device-' + port.device.device_id"
      class="gx-device"
      :class="{ online: port.device.online, active: port.active }"
      :style="ptStyle(port.x, port.y)"
      :title="`${deviceShort(port.device)} · ${port.device.device_id} · ${devicePresenceLabel(port.device)}`"
      @click.stop="$emit('open-moon', port.body)"
    >{{ port.device.online ? '●' : '○' }}</button>

    <el-popover v-for="n in galaxy.nodes" :key="'c' + n.c.id" placement="top" :width="300" trigger="hover" popper-class="cy-pop" :show-after="60">
      <template #reference>
        <div class="gx-comp" :class="{ primary: n.c.isDefault, active: n.active, focused: n.c.id === focusedId }" :style="ptStyle(n.x, n.y)" @click.stop="$emit('open-companion', n.c)">
          <i class="led" :class="statusClass(n.c.status)" />
          <b>{{ n.c.isDefault ? '★ ' : '' }}{{ n.c.name }}</b>
          <span class="c-rt" :class="'t-' + runtime(n.c).cls">{{ runtime(n.c).text }}</span>
        </div>
      </template>
      <div class="pop">
        <div class="pop-h"><b>{{ n.c.name }}</b><em>{{ n.c.isDefault ? 'DEFAULT' : 'companion' }}</em></div>
        <p class="pop-role">虚拟伙伴（agent），归属于主人 {{ ownerName }}。它拥有自己的身体、记忆与活动。</p>
        <div class="pop-rows">
          <div><dt>状态</dt><dd :class="statusClass(n.c.status)">{{ n.c.status }}</dd></div>
          <div><dt>基因 genome</dt><dd :class="n.c.genome ? 'ok' : 'idle'" :title="n.c.genome || undefined">{{ genomeStateLabel(n.c.genome) }}</dd></div>
          <div><dt>身体</dt><dd>{{ n.c.devices.length }} 台 · {{ n.c.devices.filter(deviceOnline).length }} 在线</dd></div>
          <div><dt>记忆空间</dt><dd :class="n.c.realm ? 'ok' : 'idle'" :title="n.c.realm || undefined">{{ memoryRealmStateLabel(n.c.realm) }}</dd></div>
        </div>
      </div>
    </el-popover>

    <el-popover v-for="s in galaxy.sats" :key="'s' + s.c.id + s.kind" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
      <template #reference>
        <div class="gx-sat" :class="[`a-${s.accent}`, `t-${s.tone}`, { empty: s.empty, 'focused-sat': s.c.id === focusedId, 'selected-asset': s.c.id === focusedId && s.kind === selectedKind, 'stage-here': isStageHere(s) }]" :style="ptStyle(s.x, s.y)" @click.stop="$emit('open-moon', s)">
          <i class="s-glyph">{{ s.glyph }}</i>
          <span class="s-label">{{ s.label }}</span>
          <b class="s-val">{{ s.value }}</b>
        </div>
      </template>
      <div class="pop">
        <div class="pop-h"><b>{{ s.c.name }} · {{ s.label }}</b><em>{{ s.tone.toUpperCase() }}</em></div>
        <template v-if="s.kind === 'body'">
          <p class="pop-role">伙伴的物理 / 虚拟入口。设备只是入口，身份仍归属主人。</p>
          <div v-if="s.c.devices.length" class="pop-rows">
            <div v-for="d in s.c.devices" :key="d.device_id"><dt>{{ deviceType(d) }}</dt><dd :class="devicePresenceClass(d)" :title="d.device_id">{{ deviceShort(d) }} · {{ devicePresenceLabel(d) }}</dd></div>
          </div>
          <p v-else class="pop-role dim">这个伙伴还没有绑定任何身体。</p>
        </template>
        <template v-else-if="s.kind === 'mem'">
          <p class="pop-role">属于这个伙伴的长期记忆资产，让它记得主人、保持连续性。</p>
          <div class="pop-rows">
            <div><dt>记忆空间</dt><dd :class="s.c.realm ? 'ok' : 'idle'" :title="s.c.realm || undefined">{{ memoryRealmStateLabel(s.c.realm) }}</dd></div>
            <div><dt>基因 genome</dt><dd :class="s.c.genome ? 'ok' : 'idle'" :title="s.c.genome || undefined">{{ genomeStateLabel(s.c.genome) }}</dd></div>
            <div v-if="s.c.isActiveRealm"><dt>召回命中</dt><dd class="ok">{{ s.c.recall }}</dd></div>
            <div v-if="s.c.isActiveRealm"><dt>后台整理</dt><dd>{{ s.c.runners }}</dd></div>
            <div v-if="s.c.isActiveRealm"><dt>写入策略</dt><dd>{{ s.c.write }}</dd></div>
          </div>
        </template>
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

    <el-popover v-if="unboundDevices.length" placement="left" :width="280" trigger="hover" popper-class="cy-pop" :show-after="60">
      <template #reference>
        <div class="gx-unbound"><i class="led warn" /><span>待认领设备</span><b class="num">{{ unboundDevices.length }}</b></div>
      </template>
      <div class="pop">
        <div class="pop-h"><b>待认领设备</b><em>UNCLAIMED</em></div>
        <p class="pop-role">这些身体还没有绑定到任何主人 / 伙伴，等待认领与授权。</p>
        <div class="pop-rows">
          <div v-for="d in unboundDevices" :key="d.device_id"><dt>{{ deviceType(d) }}</dt><dd :class="devicePresenceClass(d)" :title="d.device_id">{{ deviceShort(d) }} · {{ devicePresenceLabel(d) }}</dd></div>
        </div>
      </div>
    </el-popover>
  </section>
</template>

<style scoped>
.galaxy { position: relative; flex: 1 1 auto; width: 100%; max-width: 1480px; aspect-ratio: 1000 / 700; max-height: 72vh; margin: 0 auto; }
.gx-wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.wire { fill: none; }
.wire.comp { stroke: rgba(164, 75, 255, 0.4); stroke-width: 1.15; stroke-dasharray: 2 8; transition: stroke var(--dur-base) var(--ease-out), stroke-width var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out); }
.wire.comp.hot { stroke: rgba(164, 75, 255, 0.76); stroke-width: 1.6; filter: drop-shadow(0 0 4px rgba(164, 75, 255, 0.72)); }
.wire.comp.selected { stroke: rgba(0, 234, 255, 0.72); stroke-width: 1.6; }
.wire.sat { stroke-width: 1.15; stroke-dasharray: 3 4; transition: stroke var(--dur-base) var(--ease-out), stroke-width var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out); }
.wire.sat.link-body { stroke: rgba(0, 234, 255, .38); }
.wire.sat.link-mem { stroke: rgba(247, 255, 74, .34); }
.wire.sat.link-act { stroke: rgba(255, 46, 136, .38); }
.wire.sat.selected { stroke-width: 1.55; opacity: .92; filter: drop-shadow(0 0 3px currentColor); }
.wire.sat.dim { stroke: rgba(109, 106, 153, 0.3); }
.wire.device-link { stroke: rgba(0, 234, 255, .28); stroke-width: .9; stroke-dasharray: 2 3; }
.wire.device-link.hot { stroke: var(--cy-cyan); stroke-width: 1.5; filter: drop-shadow(0 0 4px var(--cy-cyan)); }
.wire.device-link.offline { stroke: rgba(109, 106, 153, .25); }
.event-highlight { fill: none; stroke-width: 4; stroke-linecap: round; stroke-dasharray: 8 5; opacity: .9; animation: dashflow .7s linear infinite; }
/* A lit flow leg reads as an energized conduit: solid, brighter than the idle
   dashed leg. stroke-opacity + glow are bound inline (JS-resolved, scaled by
   the memory-leg brightness); here we set the conduit look and let brightness
   changes ease with the shared motion tokens (mirrors motion.ts). */
.wire.sat.flow {
  stroke: var(--cy-cyan); stroke-dasharray: none; stroke-width: 1.2;
  transition: stroke-opacity var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out);
}
.pulse { fill: #d6fbff; filter: drop-shadow(0 0 6px var(--cy-cyan)); }
.flow-dot { fill: #eafcff; }

.gx-owner, .gx-comp, .gx-sat, .gx-unbound { position: absolute; transform: translate(-50%, -50%); display: grid; place-content: center; text-align: center; }
.gx-owner, .gx-comp, .gx-sat { cursor: pointer; }
.gx-owner { width: 150px; height: 150px; border-radius: 50%; border: 2px solid rgba(255, 46, 136, 0.6); background: radial-gradient(circle at 42% 34%, rgba(255, 255, 255, 0.32), rgba(164, 75, 255, 0.22) 40%, rgba(10, 6, 24, 0.92) 70%); box-shadow: 0 0 14px rgba(255, 255, 255, 0.42), 0 0 44px rgba(255, 46, 136, 0.5), 0 0 92px rgba(255, 46, 136, 0.28), 0 0 132px rgba(164, 75, 255, 0.24), inset 0 0 42px rgba(164, 75, 255, 0.22); animation: sun 5s ease-in-out infinite; z-index: 3; }
/* D5 "点火": a one-shot flare (scale + brightness) when a turn brings the
   pipeline live. Rides alongside the steady `sun` breathing; ends on scale(1)
   so it settles back with no jump. Disabled under reduced-motion via `.gx-owner`. */
.gx-owner.igniting { animation: sun 5s ease-in-out infinite, ignite 0.9s var(--ease-out); }
@keyframes ignite {
  0% { transform: translate(-50%, -50%) scale(1); filter: brightness(1); }
  22% { transform: translate(-50%, -50%) scale(1.09); filter: brightness(1.75); }
  100% { transform: translate(-50%, -50%) scale(1); filter: brightness(1); }
}
.o-kick { font: 700 8.5px/1 var(--cy-mono); letter-spacing: 0.14em; color: var(--cy-sun); }
.gx-owner strong { display: block; max-width: 120px; margin: 5px auto 6px; font: 800 20px/1 var(--cy-sans); color: #fff; text-shadow: 0 0 16px rgba(255, 46, 136, 0.6); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.o-sub { display: block; margin-top: 4px; font: 600 9px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }

.gx-comp { width: 108px; height: 108px; border-radius: 50%; border: 1.5px solid var(--cy-cyan); background: radial-gradient(circle at 40% 34%, rgba(0, 234, 255, 0.22), rgba(8, 5, 20, 0.94) 66%); box-shadow: 0 0 26px rgba(0, 234, 255, 0.25); z-index: 2; transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out); }
.gx-comp:hover { transform: translate(-50%, -50%) scale(1.06); box-shadow: 0 0 36px rgba(0, 234, 255, 0.5); }
.gx-comp .led { margin: 0 auto 4px; }
.gx-comp b { display: block; max-width: 92px; font: 800 15px/1.05 var(--cy-sans); color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gx-comp em { display: block; margin-top: 3px; font: 700 9px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }
/* Always-on per-companion runtime state (replaces the hover-only activity moon). */
.gx-comp .c-rt { display: inline-block; margin-top: 4px; padding: 1.5px 7px; font: 700 8.5px/1.35 var(--cy-mono); letter-spacing: 0.03em; border-radius: 8px; max-width: 92px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gx-comp .c-rt.t-live { color: var(--cy-mag); background: rgba(255, 46, 136, 0.16); }
.gx-comp .c-rt.t-warn { color: var(--cy-yellow); background: rgba(247, 255, 74, 0.12); }
.gx-comp .c-rt.t-idle { color: var(--cy-txt-dim); background: rgba(255, 255, 255, 0.04); }
.gx-comp.primary { border-color: var(--cy-sun); box-shadow: 0 0 30px rgba(255, 46, 136, 0.35); background: radial-gradient(circle at 40% 34%, rgba(255, 46, 136, 0.2), rgba(8, 5, 20, 0.94) 66%); }
.gx-comp.primary em { color: var(--cy-sun); }
.gx-comp.active { animation: nodepulse var(--dur-breath) ease-in-out infinite; }

.gx-sat { width: 78px; height: 78px; border-radius: 50%; border: 1px solid var(--cy-cyan); background: radial-gradient(circle at 42% 36%, rgba(0, 234, 255, 0.16), rgba(8, 5, 20, 0.95) 68%); z-index: 2; transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out); }
.gx-sat:hover { transform: translate(-50%, -50%) scale(1.1); box-shadow: 0 0 22px currentColor; }
.gx-sat.a-yellow { border-color: var(--cy-yellow); color: var(--cy-yellow); background: radial-gradient(circle at 42% 36%, rgba(247, 255, 74, 0.14), rgba(8, 5, 20, 0.95) 68%); }
.gx-sat.a-mag { border-color: var(--cy-mag); color: var(--cy-mag); background: radial-gradient(circle at 42% 36%, rgba(255, 46, 136, 0.14), rgba(8, 5, 20, 0.95) 68%); }
.gx-sat.a-cyan { color: var(--cy-cyan); }
.gx-sat.t-off { border-style: dashed; border-color: var(--cy-txt-dim); color: var(--cy-txt-dim); opacity: 0.62; }
.gx-sat.t-live { animation: nodepulse var(--dur-breath) ease-in-out infinite; }
.gx-sat.t-bad { border-color: var(--cy-mag); color: var(--cy-mag); box-shadow: 0 0 22px rgba(255, 46, 136, .5); }
/* §one signal: the moon matching the current stage pulses + rings, tracking the
   signal to the body / memory / activity in step with the bus wavefront + trace. */
.gx-sat.stage-here { animation: nodepulse var(--dur-breath) ease-in-out infinite; box-shadow: 0 0 22px currentColor; border-width: 1.5px; }
.gx-sat.selected-asset { transform: translate(-50%, -50%) scale(1.13); border-width: 2px; box-shadow: 0 0 28px currentColor, inset 0 0 14px color-mix(in srgb, currentColor 14%, transparent); z-index: 6; }
.gx-activity { position: absolute; z-index: 7; min-width: 32px; height: 20px; transform: translate(-50%, -50%); padding: 0 6px; border: 1px solid rgba(109, 106, 153, .7); border-radius: 10px; background: rgba(8, 5, 20, .94); color: var(--cy-txt-dim); font: 700 7.5px/1 var(--cy-mono); cursor: pointer; box-shadow: 0 0 8px rgba(109, 106, 153, .25); transition: transform var(--dur-fast) var(--ease-out), border-color var(--dur-fast), box-shadow var(--dur-fast); }
.gx-activity:hover, .gx-activity.selected { transform: translate(-50%, -50%) scale(1.16); z-index: 9; }
.gx-activity.t-ok { border-color: var(--cy-green); color: var(--cy-green); }
.gx-activity.t-warn { border-color: var(--cy-yellow); color: var(--cy-yellow); }
.gx-activity.t-bad { border-color: var(--cy-mag); color: var(--cy-mag); box-shadow: 0 0 11px rgba(255, 46, 136, .55); }
.gx-activity.t-live, .gx-activity.live { border-color: var(--cy-cyan); color: var(--cy-cyan); animation: nodepulse var(--dur-breath) ease-in-out infinite; }
.gx-activity.selected { outline: 1px solid #fff; outline-offset: 2px; }
.gx-device { position: absolute; z-index: 6; width: 25px; height: 25px; transform: translate(-50%, -50%); padding: 0; overflow: hidden; border: 1px solid rgba(109, 106, 153, .55); border-radius: 50%; background: rgba(8, 5, 20, .94); color: var(--cy-txt-dim); font: 700 6.5px/1 var(--cy-mono); cursor: pointer; }
.gx-device.online { border-color: var(--cy-green); color: var(--cy-green); }
.gx-device.active { border-color: var(--cy-cyan); color: var(--cy-cyan); box-shadow: 0 0 12px rgba(0, 234, 255, .65); animation: nodepulse var(--dur-breath) ease-in-out infinite; }
.s-glyph { font-size: 15px; font-style: normal; line-height: 1; color: currentColor; text-shadow: 0 0 8px currentColor; }
.s-label { display: block; margin: 3px 0 2px; font: 700 8.5px/1 var(--cy-mono); color: var(--cy-txt-dim); letter-spacing: 0.04em; }
.s-val { display: block; max-width: 68px; margin: 0 auto; font: 800 10px/1.05 var(--cy-sans); color: #eaf6ff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gx-sat.t-off .s-val { color: var(--cy-txt-dim); }

.gx-unbound { top: auto; bottom: 6px; left: auto; right: 8px; transform: none; display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border: 1px dashed rgba(247, 255, 74, 0.4); background: rgba(247, 255, 74, 0.05); z-index: 3; }
.gx-unbound span { font: 700 11px/1 var(--cy-mono); color: var(--cy-yellow); }
.gx-unbound b { font: 900 14px/1 var(--cy-mono); color: #fff; }

.orbit-ring { animation: spinslow 90s linear infinite; transform-origin: center; transform-box: fill-box; }
@keyframes spinslow { to { transform: rotate(360deg); } }

/* Semantic zoom (A3.4): the focused companion pops; siblings recede. */
.galaxy.has-focus .gx-comp:not(.focused) { opacity: 0.3; filter: saturate(0.5); }
.galaxy.has-focus .gx-sat:not(.focused-sat) { opacity: 0.18; filter: saturate(0.45); }
.galaxy.has-focus .gx-device:not(.active) { opacity: .38; }
.galaxy.has-focus .wire.comp:not(.hot) { stroke-opacity: 0.4; }
.gx-comp.focused { transform: translate(-50%, -50%) scale(1.14); box-shadow: 0 0 46px rgba(0, 234, 255, 0.6); border-width: 2px; z-index: 5; }
.gx-comp.focused:hover { transform: translate(-50%, -50%) scale(1.16); }
@keyframes sun {
  0%, 100% { box-shadow: 0 0 13px rgba(255, 255, 255, 0.38), 0 0 40px rgba(255, 46, 136, 0.44), 0 0 84px rgba(255, 46, 136, 0.26), 0 0 120px rgba(164, 75, 255, 0.22), inset 0 0 40px rgba(164, 75, 255, 0.2); }
  50% { box-shadow: 0 0 18px rgba(255, 255, 255, 0.55), 0 0 56px rgba(255, 46, 136, 0.6), 0 0 110px rgba(255, 46, 136, 0.34), 0 0 152px rgba(164, 75, 255, 0.3), inset 0 0 48px rgba(164, 75, 255, 0.28); }
}
@keyframes nodepulse { 0%, 100% { box-shadow: 0 0 16px currentColor; } 50% { box-shadow: 0 0 30px currentColor; } }
@keyframes dashflow { to { stroke-dashoffset: -26; } }
@media (prefers-reduced-motion: reduce) {
  .gx-owner, .gx-comp.active, .gx-sat.t-live, .gx-sat.stage-here, .gx-activity, .gx-device, .orbit-ring, .event-highlight { animation: none !important; }
  /* Hide the travelling dots entirely (not just their motion) so they don't
     clump at the origin. Lit flow legs stay brightened — a static, motion-free
     signal of which conduits are active. */
  .gx-wires .pulse { display: none; }
  .gx-wires animateTransform, .gx-wires animateMotion { display: none; }
}
@media (max-width: 1080px) { .galaxy { max-height: none; } }
</style>
