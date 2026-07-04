<script setup lang="ts">
// The primary view: a sovereign-domain constellation. Owner sun at the centre,
// companion planets on an orbit, each with three asset moons (body / memory /
// activity). Geometry + orbital motion are presentational and live here; the
// data comes from the composable's `companionUnits`.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { RuntimeDevice } from '@/api/missionControl'
import { deviceShort, deviceType, fmtLatency, statusClass } from '../format'
import { prefersReducedMotion } from '../motion'
import type { CompanionUnit, GalaxyNode, Sat, SatKind } from '../types'
import type { MissionControlStream } from '../useMissionControlStream'

const props = defineProps<{ mc: MissionControlStream; focusedId?: string }>()
defineEmits<{
  (e: 'open-owner'): void
  (e: 'open-companion', c: CompanionUnit): void
  (e: 'open-moon', s: Sat): void
}>()

const { companionUnits, unboundDevices, ownerName, completion } = props.mc

// ── geometry ──────────────────────────────────────────────────────────
const VBW = 1000, VBH = 700, CX = 500, CY = 350, RX = 306, RY = 220, RSAT = 92, PI = Math.PI
const spin = ref(0)
const paused = ref(false) // freeze the orbit while the pointer is over it, so nodes are clickable
let spinTimer: number | undefined

function ptStyle(x: number, y: number) {
  return { left: `${(x / VBW) * 100}%`, top: `${(y / VBH) * 100}%` }
}
function satOf(c: CompanionUnit, kind: SatKind): Omit<Sat, 'c' | 'x' | 'y' | 'link'> {
  if (kind === 'body') {
    const n = c.devices.length
    return {
      kind, label: '身体', glyph: '⬡',
      value: n ? deviceType(c.devices[0]) : '未绑定',
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
    value: c.turn ? '对话中' : c.jobs.length ? `${c.jobs.length} 任务` : '空闲',
    tone: c.turn ? 'live' : c.jobs.length ? 'warn' : 'off',
    empty: !c.turn && !c.jobs.length, accent: 'mag',
  }
}

const galaxy = computed(() => {
  const comps = companionUnits.value
  const N = comps.length || 1
  const start = N === 1 ? 0 : -90 + 180 / N
  const rev = spin.value * 0.22
  const moonRev = spin.value * 1.1
  const nodes: GalaxyNode[] = comps.map((c, i) => {
    const a = (start + (i * 360) / N + rev) * (PI / 180)
    const x = CX + RX * Math.cos(a), y = CY + RY * Math.sin(a)
    const sats: Sat[] = (['body', 'mem', 'act'] as SatKind[]).map((kind, si) => {
      const sa = a + ((si - 1) * 42 + moonRev) * (PI / 180)
      const sx = x + RSAT * Math.cos(sa), sy = y + RSAT * Math.sin(sa)
      return { ...satOf(c, kind), c, x: sx, y: sy, link: `M${x.toFixed(1)} ${y.toFixed(1)} L${sx.toFixed(1)} ${sy.toFixed(1)}` }
    })
    return { c, x, y, link: `M${CX} ${CY} L${x.toFixed(1)} ${y.toFixed(1)}`, active: !!c.turn, sats }
  })
  return { nodes, sats: nodes.flatMap((n) => n.sats), links: nodes.map((n) => ({ id: n.c.id, d: n.link, active: n.active })) }
})

onMounted(() => {
  if (!prefersReducedMotion()) {
    spinTimer = window.setInterval(() => {
      if (!paused.value) spin.value = (spin.value + 0.35) % 3600
    }, 66)
  }
})
onBeforeUnmount(() => {
  if (spinTimer) window.clearInterval(spinTimer)
})

function deviceOnline(d: RuntimeDevice) {
  return d.online
}
</script>

<template>
  <section class="galaxy" :class="{ 'has-focus': !!focusedId }" @pointerenter="paused = true" @pointerleave="paused = false">
    <svg class="gx-wires" :viewBox="`0 0 ${VBW} ${VBH}`" preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="sun" cx="50%" cy="42%" r="60%">
          <stop offset="0%" stop-color="#fff7cc" /><stop offset="30%" stop-color="#ffd23f" /><stop offset="100%" stop-color="rgba(164,75,255,0)" />
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
      <path v-for="s in galaxy.sats" :key="'sl' + s.c.id + s.kind" :d="s.link" class="wire sat" :class="{ dim: s.empty }" />
      <path v-for="l in galaxy.links" :key="'cl' + l.id" :d="l.d" class="wire comp" :class="{ hot: l.active }" />
      <template v-for="l in galaxy.links.filter((x) => x.active)" :key="'p' + l.id">
        <circle r="4" class="pulse"><animateMotion dur="1.4s" repeatCount="indefinite" :path="l.d" /></circle>
        <circle r="4" class="pulse"><animateMotion dur="1.4s" begin="0.7s" repeatCount="indefinite" :path="l.d" /></circle>
      </template>
      <circle :cx="CX" :cy="CY" r="150" fill="url(#sun)" opacity="0.5" />
    </svg>

    <div class="gx-owner" :style="ptStyle(CX, CY)" title="点击查看主人全景" @click="$emit('open-owner')">
      <span class="o-kick">OWNER · 主人</span>
      <strong>{{ ownerName }}</strong>
      <div class="o-int"><b class="num">{{ completion }}%</b><em>完整度</em></div>
    </div>

    <el-popover v-for="n in galaxy.nodes" :key="'c' + n.c.id" placement="top" :width="300" trigger="hover" popper-class="cy-pop" :show-after="60">
      <template #reference>
        <div class="gx-comp" :class="{ primary: n.c.isPrimary, active: n.active, focused: n.c.id === focusedId }" :style="ptStyle(n.x, n.y)" @click="$emit('open-companion', n.c)">
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
          <div><dt>身体</dt><dd>{{ n.c.devices.length }} 台 · {{ n.c.devices.filter(deviceOnline).length }} 在线</dd></div>
          <div><dt>记忆空间</dt><dd :class="n.c.realm ? 'ok' : 'idle'">{{ n.c.realm || '未开通' }}</dd></div>
        </div>
      </div>
    </el-popover>

    <el-popover v-for="s in galaxy.sats" :key="'s' + s.c.id + s.kind" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
      <template #reference>
        <div class="gx-sat" :class="[`a-${s.accent}`, `t-${s.tone}`, { empty: s.empty, 'focused-sat': s.c.id === focusedId }]" :style="ptStyle(s.x, s.y)" @click="$emit('open-moon', s)">
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
            <div v-for="d in s.c.devices" :key="d.device_id"><dt>{{ deviceType(d) }}</dt><dd :class="d.online ? 'ok' : 'idle'">{{ deviceShort(d) }} · {{ d.online ? '在线' : '离线' }}</dd></div>
          </div>
          <p v-else class="pop-role dim">这个伙伴还没有绑定任何身体。</p>
        </template>
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
          <div v-for="d in unboundDevices" :key="d.device_id"><dt>{{ deviceType(d) }}</dt><dd :class="d.online ? 'ok' : 'idle'">{{ deviceShort(d) }} · {{ d.online ? '在线' : '离线' }}</dd></div>
        </div>
      </div>
    </el-popover>
  </section>
</template>

<style scoped>
.galaxy { position: relative; flex: 1 1 auto; width: 100%; max-width: 1180px; aspect-ratio: 1000 / 700; max-height: 66vh; margin: 0 auto; }
.gx-wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.wire { fill: none; }
.wire.comp { stroke: rgba(0, 234, 255, 0.4); stroke-width: 1.4; }
.wire.comp.hot { stroke: var(--cy-cyan); stroke-width: 2.2; filter: drop-shadow(0 0 5px var(--cy-cyan)); }
.wire.sat { stroke: rgba(0, 234, 255, 0.24); stroke-width: 1; stroke-dasharray: 3 4; }
.wire.sat.dim { stroke: rgba(109, 106, 153, 0.3); }
.pulse { fill: #d6fbff; filter: drop-shadow(0 0 6px var(--cy-cyan)); }

.gx-owner, .gx-comp, .gx-sat, .gx-unbound { position: absolute; transform: translate(-50%, -50%); display: grid; place-content: center; text-align: center; }
.gx-owner, .gx-comp, .gx-sat { cursor: pointer; }
.gx-owner { width: 150px; height: 150px; border-radius: 50%; border: 2px solid rgba(255, 210, 63, 0.6); background: radial-gradient(circle at 42% 34%, rgba(255, 240, 190, 0.35), rgba(10, 6, 24, 0.92) 62%); box-shadow: 0 0 60px rgba(255, 210, 63, 0.4), inset 0 0 40px rgba(255, 210, 63, 0.2); animation: sun 5s ease-in-out infinite; z-index: 3; }
.o-kick { font: 700 8.5px/1 var(--cy-mono); letter-spacing: 0.14em; color: var(--cy-yellow); }
.gx-owner strong { display: block; max-width: 120px; margin: 5px auto 6px; font: 800 20px/1 var(--cy-sans); color: #fff; text-shadow: 0 0 16px rgba(255, 210, 63, 0.6); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.o-int b { font: 900 22px/1 var(--cy-mono); color: var(--cy-cyan); }
.o-int em { display: block; margin-top: 2px; font: 600 8px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }

.gx-comp { width: 108px; height: 108px; border-radius: 50%; border: 1.5px solid var(--cy-cyan); background: radial-gradient(circle at 40% 34%, rgba(0, 234, 255, 0.22), rgba(8, 5, 20, 0.94) 66%); box-shadow: 0 0 26px rgba(0, 234, 255, 0.25); z-index: 2; transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out); }
.gx-comp:hover { transform: translate(-50%, -50%) scale(1.06); box-shadow: 0 0 36px rgba(0, 234, 255, 0.5); }
.gx-comp .led { margin: 0 auto 4px; }
.gx-comp b { display: block; max-width: 92px; font: 800 15px/1.05 var(--cy-sans); color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gx-comp em { display: block; margin-top: 3px; font: 700 9px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }
.gx-comp.primary { border-color: var(--cy-yellow); box-shadow: 0 0 30px rgba(247, 255, 74, 0.35); background: radial-gradient(circle at 40% 34%, rgba(247, 255, 74, 0.2), rgba(8, 5, 20, 0.94) 66%); }
.gx-comp.primary em { color: var(--cy-yellow); }
.gx-comp.active { animation: nodepulse 1.5s ease-in-out infinite; }

.gx-sat { width: 78px; height: 78px; border-radius: 50%; border: 1px solid var(--cy-cyan); background: radial-gradient(circle at 42% 36%, rgba(0, 234, 255, 0.16), rgba(8, 5, 20, 0.95) 68%); z-index: 2; transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out), filter var(--dur-base) var(--ease-out); }
.gx-sat:hover { transform: translate(-50%, -50%) scale(1.1); box-shadow: 0 0 22px currentColor; }
.gx-sat.a-yellow { border-color: var(--cy-yellow); color: var(--cy-yellow); background: radial-gradient(circle at 42% 36%, rgba(247, 255, 74, 0.14), rgba(8, 5, 20, 0.95) 68%); }
.gx-sat.a-mag { border-color: var(--cy-mag); color: var(--cy-mag); background: radial-gradient(circle at 42% 36%, rgba(255, 46, 136, 0.14), rgba(8, 5, 20, 0.95) 68%); }
.gx-sat.a-cyan { color: var(--cy-cyan); }
.gx-sat.t-off { border-style: dashed; border-color: var(--cy-txt-dim); color: var(--cy-txt-dim); opacity: 0.62; }
.gx-sat.t-live { animation: nodepulse 1.3s ease-in-out infinite; }
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
.galaxy.has-focus .wire.comp:not(.hot) { stroke-opacity: 0.4; }
.gx-comp.focused { transform: translate(-50%, -50%) scale(1.14); box-shadow: 0 0 46px rgba(0, 234, 255, 0.6); border-width: 2px; z-index: 5; }
.gx-comp.focused:hover { transform: translate(-50%, -50%) scale(1.16); }
@keyframes sun { 0%, 100% { box-shadow: 0 0 50px rgba(255, 210, 63, 0.35), inset 0 0 36px rgba(255, 210, 63, 0.18); } 50% { box-shadow: 0 0 76px rgba(255, 210, 63, 0.5), inset 0 0 44px rgba(255, 210, 63, 0.26); } }
@keyframes nodepulse { 0%, 100% { box-shadow: 0 0 16px currentColor; } 50% { box-shadow: 0 0 30px currentColor; } }
@media (prefers-reduced-motion: reduce) {
  .gx-owner, .gx-comp.active, .gx-sat.t-live, .pulse, .orbit-ring { animation: none !important; }
  .gx-wires animateTransform, .gx-wires animateMotion { display: none; }
}
@media (max-width: 1080px) { .galaxy { max-height: none; } }
</style>
