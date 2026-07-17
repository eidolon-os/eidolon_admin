<script setup lang="ts">
// Eidolon OS substrate: the stable sovereign identity waist above the real
// service graph. Mission Control remains an observer — the graph projects
// health and independently observed routes, but never coordinates runtime work.
import { computed } from 'vue'
import {
  EDGE_LABEL,
  IDENTITY_ENVELOPE,
  INFRA_EDGES,
  INFRA_LAYOUT,
  INFRA_VB,
  MODE_CN,
  MODE_EXP,
  OS_INVARIANTS,
  OS_PLANE_BANDS,
} from '../constants'
import { spineReached } from '../flow'
import { fmtTime } from '../format'
import type { InfraNode } from '../types'

const props = defineProps<{
  nodes: InfraNode[]
  hotServices: string[]
  activityOwners: Record<string, string[]>
  pipelineActive: boolean
}>()
defineEmits<{ (e: 'open-service', n: InfraNode): void }>()

const layoutById = new Map(INFRA_LAYOUT.map((l) => [l.id, l]))
const hotSet = computed(() => new Set(props.hotServices))
const coreNodes = computed(() => props.nodes.filter((node) => node.tier !== 'external'))
const extensionNodes = computed(() => props.nodes.filter((node) => node.tier === 'external'))
const coreOnlineCount = computed(() => coreNodes.value.filter((node) => node.online).length)
const extensionOnlineCount = computed(() => extensionNodes.value.filter((node) => node.online).length)
const activeCompanions = computed(() => new Set(Object.values(props.activityOwners).flat()).size)
const systemState = computed(() => coreNodes.value.some((node) => node.state !== 'online') ? 'DEGRADED' : 'NOMINAL')
function edgeReached(target: string): boolean {
  return props.hotServices.some((service) => spineReached(service, target))
}

// Product planes are captions around factual services, never synthetic nodes.
const planesView = OS_PLANE_BANDS.map((b) => ({
  ...b,
  left: (b.x0 / INFRA_VB.w) * 100,
  width: ((b.x1 - b.x0) / INFRA_VB.w) * 100,
}))

const nodesView = computed(() =>
  INFRA_LAYOUT.map((l) => {
    const node = props.nodes.find((n) => n.id === l.id)
    if (!node) return null
    return { node, px: (l.x / INFRA_VB.w) * 100, py: (l.y / INFRA_VB.h) * 100 }
  }).filter((v): v is { node: InfraNode; px: number; py: number } => v !== null),
)

const edgesView = computed(() =>
  INFRA_EDGES.map((e) => {
    const a = layoutById.get(e.from)
    const b = layoutById.get(e.to)
    if (!a || !b) return null
    const x1 = (a.x / INFRA_VB.w) * 100
    const y1 = (a.y / INFRA_VB.h) * 100
    const x2 = (b.x / INFRA_VB.w) * 100
    const y2 = (b.y / INFRA_VB.h) * 100
    return { ...e, d: `M${x1} ${y1} L${x2} ${y2}`, mx: (x1 + x2) / 2, my: (y1 + y2) / 2 }
  }).filter((v) => v !== null),
)
</script>

<template>
  <footer class="cy-bus" :class="{ live: pipelineActive }">
    <header class="bus-head">
      <div class="bus-title">
        <span>EIDOLON OS</span>
        <b>主权运行内核</b>
        <small>SOVEREIGN RUNTIME ARCHITECTURE</small>
      </div>
      <div class="bus-vitals">
        <span><small>CORE</small><b :class="systemState === 'NOMINAL' ? 'ok' : 'bad'">{{ coreOnlineCount }}/{{ coreNodes.length }}</b></span>
        <span><small>EXT</small><b :class="extensionOnlineCount === extensionNodes.length ? 'ok' : 'idle'">{{ extensionOnlineCount }}/{{ extensionNodes.length }}</b></span>
        <span><small>ACTIVE ROUTES</small><b :class="activeCompanions ? 'cyan' : 'idle'">{{ activeCompanions }}</b></span>
        <span class="observer"><i class="led ok" />READ-ONLY OBSERVER</span>
      </div>
    </header>

    <section class="identity-envelope" aria-label="Runtime Identity Envelope">
      <div class="ie-label">
        <span>主权控制平面</span>
        <small>SOVEREIGN CONTROL PLANE</small>
      </div>
      <ol>
        <li v-for="(segment, index) in IDENTITY_ENVELOPE" :key="segment">
          <small>{{ String(index + 1).padStart(2, '0') }}</small>
          <b>{{ segment }}</b>
          <i v-if="index < IDENTITY_ENVELOPE.length - 1">›</i>
        </li>
      </ol>
      <em>RUNTIME IDENTITY ENVELOPE</em>
    </section>

    <div class="topo-scroll">
      <div class="topo">
      <div
        v-for="plane in planesView"
        :key="plane.id"
        class="os-plane"
        :class="'op-' + plane.id"
        :style="{ left: plane.left + '%', width: plane.width + '%' }"
      >
        <span class="plane-label"><b>{{ plane.label }}</b><small>{{ plane.code }}</small></span>
      </div>
      <svg class="topo-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <path
          v-for="(e, i) in edgesView"
          :key="i"
          :d="e.d"
          class="edge"
          :class="[`k-${e.kind}`, { spine: e.spine, flow: pipelineActive && e.spine && edgeReached(e.to), front: pipelineActive && e.spine && hotSet.has(e.to) }]"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      <span v-for="(e, i) in edgesView" :key="'l' + i" class="edge-label" :class="`k-${e.kind}`" :style="{ left: e.mx + '%', top: e.my + '%' }">{{ EDGE_LABEL[e.kind] }}</span>

      <el-popover v-for="v in nodesView" :key="v.node.id" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
        <template #reference>
          <div
            class="topo-node"
            :class="[`st-${v.node.state}`, `t-${v.node.tier}`, { hot: hotSet.has(v.node.id) }]"
            :style="{ left: v.px + '%', top: v.py + '%' }"
            @click="$emit('open-service', v.node)"
          >
            <i class="tn-glyph">{{ v.node.glyph }}</i>
            <div class="tn-body">
              <b>{{ v.node.cn }}</b>
              <em><i class="led" />{{ v.node.stateCn }}{{ v.node.online ? ' · ' + v.node.latency : '' }}</em>
              <small v-if="activityOwners[v.node.id]?.length" class="tn-active">{{ activityOwners[v.node.id].slice(0, 2).join(' · ') }}<i v-if="activityOwners[v.node.id].length > 2"> +{{ activityOwners[v.node.id].length - 2 }}</i></small>
            </div>
          </div>
        </template>
        <div class="pop">
          <div class="pop-h"><b>{{ v.node.cn }}</b><em>{{ v.node.code }}</em></div>
          <p class="pop-role">{{ v.node.role }}</p>
          <div class="pop-rows">
            <div><dt>状态</dt><dd :class="{ ok: v.node.state === 'online', bad: v.node.state === 'offline', warn: v.node.state === 'unknown' }">{{ v.node.stateCn }}{{ v.node.online ? ' · ' + v.node.latency : '' }}</dd></div>
            <div v-if="v.node.state === 'unknown'"><dt>说明</dt><dd class="warn">无健康接口，存活由 supervisord 托管</dd></div>
            <div><dt>集成</dt><dd>{{ MODE_CN[v.node.mode] }}（{{ MODE_EXP[v.node.mode] }}）</dd></div>
            <div v-if="v.node.detail"><dt>探针</dt><dd>{{ v.node.detail }}</dd></div>
          </div>
          <div v-if="v.node.events.length" class="pop-ev"><span class="pop-ev-h">最近事件</span><p v-for="e in v.node.events" :key="e.event_id"><em>{{ fmtTime(e.ts) }}</em>{{ e.summary || e.type }}</p></div>
        </div>
      </el-popover>
      </div>
    </div>

    <div class="kernel-invariants">
      <span class="ki-label">KERNEL INVARIANTS</span>
      <span v-for="item in OS_INVARIANTS" :key="item"><i>◆</i>{{ item }}</span>
      <em>状态来自真实探针与事件投影 · Mission Control 不参与调度</em>
    </div>
  </footer>
</template>

<style scoped>
.cy-bus { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 8px; padding: 12px 16px 10px; overflow: hidden; border: 1px solid rgba(0, 234, 255, .24); background: linear-gradient(145deg, rgba(10, 7, 27, .94), rgba(4, 3, 14, .92)), repeating-linear-gradient(90deg, transparent 0 39px, rgba(0, 234, 255, .025) 40px); clip-path: polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 calc(100% - 14px)); }
.cy-bus::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at 52% 30%, rgba(0, 234, 255, .055), transparent 38%), linear-gradient(105deg, transparent 24%, rgba(164, 75, 255, .025) 50%, transparent 76%); }
.bus-head { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.bus-title { display: flex; align-items: baseline; gap: 9px; min-width: 0; }
.bus-title > span { color: var(--cy-cyan); font: 900 10px/1 var(--cy-mono); letter-spacing: .16em; text-shadow: 0 0 10px rgba(0, 234, 255, .5); }
.bus-title > b { color: #fff; font: 850 14px/1 var(--cy-sans); }
.bus-title > small { color: var(--cy-txt-dim); font: 700 7px/1 var(--cy-mono); letter-spacing: .12em; }
.bus-vitals { display: flex; align-items: center; gap: 6px; }
.bus-vitals > span:not(.observer) { display: flex; align-items: center; gap: 6px; min-height: 25px; padding: 4px 8px; border: 1px solid rgba(134, 151, 210, .13); background: rgba(255, 255, 255, .018); }
.bus-vitals small { color: var(--cy-txt-dim); font: 700 7px/1 var(--cy-mono); letter-spacing: .08em; }
.bus-vitals b { font: 900 10px/1 var(--cy-mono); }
.observer { display: inline-flex; align-items: center; gap: 6px; padding: 0 2px 0 8px; color: var(--cy-green); font: 800 7px/1 var(--cy-mono); letter-spacing: .08em; }
.observer .led { width: 5px; height: 5px; }

.identity-envelope { position: relative; display: grid; grid-template-columns: 172px minmax(560px, 1fr) auto; align-items: stretch; min-height: 48px; overflow-x: auto; border: 1px solid rgba(164, 75, 255, .28); background: linear-gradient(90deg, rgba(164, 75, 255, .08), rgba(0, 234, 255, .025) 58%, rgba(55, 245, 179, .04)); box-shadow: inset 0 0 24px rgba(164, 75, 255, .025); scrollbar-width: thin; }
.ie-label { display: grid; align-content: center; gap: 5px; padding: 8px 11px; border-right: 1px solid rgba(164, 75, 255, .22); }
.ie-label span { color: #fff; font: 850 10px/1 var(--cy-sans); }
.ie-label small { color: var(--cy-purple); font: 800 6.5px/1 var(--cy-mono); letter-spacing: .11em; }
.identity-envelope ol { display: grid; grid-template-columns: repeat(5, minmax(100px, 1fr)); min-width: 560px; margin: 0; padding: 0; list-style: none; }
.identity-envelope li { position: relative; display: flex; align-items: center; gap: 6px; min-width: 0; padding: 8px 13px; }
.identity-envelope li:not(:last-child) { border-right: 1px solid rgba(0, 234, 255, .09); }
.identity-envelope li small { color: rgba(134, 151, 210, .48); font: 700 6px/1 var(--cy-mono); }
.identity-envelope li b { overflow: hidden; color: var(--cy-txt); font: 850 8px/1 var(--cy-mono); letter-spacing: .06em; text-overflow: ellipsis; white-space: nowrap; }
.identity-envelope li i { position: absolute; z-index: 1; right: -5px; color: var(--cy-cyan); font: 700 15px/1 var(--cy-mono); font-style: normal; text-shadow: 0 0 8px currentColor; }
.identity-envelope > em { display: grid; place-items: center; padding: 0 11px; color: var(--cy-green); font: 800 6.5px/1.2 var(--cy-mono); font-style: normal; letter-spacing: .09em; text-align: center; }

.topo-scroll { position: relative; overflow-x: auto; overflow-y: hidden; scrollbar-color: rgba(0, 234, 255, .25) transparent; scrollbar-width: thin; }
.topo { position: relative; width: 100%; min-width: 900px; height: 230px; }
.topo-wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.os-plane { position: absolute; top: 0; bottom: 0; overflow: hidden; border: 1px solid rgba(255, 255, 255, .065); pointer-events: none; }
.os-plane::after { content: ""; position: absolute; inset: 26px 0 0; background-image: linear-gradient(rgba(134, 151, 210, .025) 1px, transparent 1px), linear-gradient(90deg, rgba(134, 151, 210, .025) 1px, transparent 1px); background-size: 22px 22px; }
.os-plane.op-embodiment { border-color: rgba(0, 234, 255, .13); background: linear-gradient(145deg, rgba(0, 234, 255, .045), transparent 60%); }
.os-plane.op-cognition { border-color: rgba(247, 255, 74, .12); background: linear-gradient(145deg, rgba(247, 255, 74, .035), transparent 65%); }
.os-plane.op-execution { border-color: rgba(255, 46, 136, .13); background: linear-gradient(145deg, rgba(255, 46, 136, .04), transparent 65%); }
.plane-label { position: absolute; z-index: 1; top: 0; left: 0; right: 0; display: flex; align-items: baseline; gap: 7px; padding: 7px 9px 6px; border-bottom: 1px solid rgba(255, 255, 255, .055); }
.plane-label b { color: var(--cy-txt); font: 800 8px/1 var(--cy-sans); }
.plane-label small { color: var(--cy-txt-dim); font: 700 6px/1 var(--cy-mono); letter-spacing: .09em; }

.edge { fill: none; stroke-width: 1.4; opacity: 0.5; }
.edge.k-rtc { stroke: var(--cy-cyan); }
.edge.k-grpc { stroke: var(--cy-yellow); }
.edge.k-nats { stroke: var(--cy-green); }
.edge.k-task { stroke: var(--cy-mag); }
.edge.k-ctrl { stroke: var(--cy-txt-dim); stroke-dasharray: 3 4; opacity: 0.35; }
.edge.spine { opacity: 0.7; stroke-width: 1.8; }
.cy-bus.live .edge.flow { opacity: 1; stroke-dasharray: 5 5; animation: dashflow 0.7s linear infinite; }
/* The wavefront's leading edge (arriving at the current service) runs faster
   and glows, so the eye lands on where the signal is right now. */
.cy-bus.live .edge.flow.front { animation-duration: 0.42s; filter: drop-shadow(0 0 3px currentColor); }
@keyframes dashflow { to { stroke-dashoffset: -20; } }

.edge-label { position: absolute; transform: translate(-50%, -50%); padding: 0 3px; font: 700 8px/1.2 var(--cy-mono); letter-spacing: 0.04em; color: var(--cy-txt-dim); background: rgba(6, 4, 18, 0.7); pointer-events: none; }
.edge-label.k-rtc { color: var(--cy-cyan); }
.edge-label.k-grpc { color: var(--cy-yellow); }
.edge-label.k-nats { color: var(--cy-green); }
.edge-label.k-task { color: var(--cy-mag); }
.edge-label.k-ctrl { display: none; }

.topo-node { position: absolute; transform: translate(-50%, -50%); display: flex; align-items: center; gap: 8px; padding: 6px 10px; border: 1px solid rgba(0, 234, 255, 0.28); background: rgba(6, 4, 18, 0.92); clip-path: polygon(0 0, 100% 0, 100% calc(100% - 7px), calc(100% - 7px) 100%, 0 100%); cursor: pointer; transition: box-shadow var(--dur-base) var(--ease-out), transform var(--dur-fast) var(--ease-out); }
.topo-node:hover { transform: translate(-50%, -50%) scale(1.05); box-shadow: 0 0 18px rgba(0, 234, 255, 0.35); z-index: 3; }
.tn-glyph { font-size: 17px; font-style: normal; line-height: 1; color: var(--cy-green); text-shadow: 0 0 9px currentColor; }
.tn-body { display: flex; flex-direction: column; gap: 1px; }
.tn-body b { font: 700 12px/1 var(--cy-sans); color: #fff; white-space: nowrap; }
.tn-body em { display: inline-flex; align-items: center; gap: 4px; font: 600 8.5px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; white-space: nowrap; }
.tn-body em .led { width: 6px; height: 6px; color: var(--cy-green); }
.tn-active { display: block; max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 700 7.5px/1.2 var(--cy-mono); color: var(--cy-cyan); }
.tn-active i { font-style: normal; color: var(--cy-yellow); }
/* Tier identity on the glyph (health stays on the led). State rules below win. */
.topo-node.t-service .tn-glyph { color: var(--cy-cyan); }
.topo-node.t-middleware .tn-glyph { color: var(--cy-yellow); }
.topo-node.t-middleware { border-color: rgba(247, 255, 74, 0.28); }
.topo-node.t-external .tn-glyph { color: var(--cy-mag); }
.topo-node.t-external { border-color: rgba(255, 46, 136, 0.28); border-style: dashed; }
.topo-node.st-offline { border-color: rgba(255, 46, 136, 0.4); }
.topo-node.st-offline .tn-glyph, .topo-node.st-offline .tn-body em .led { color: var(--cy-mag); }
.topo-node.st-offline .tn-body em { color: var(--cy-mag); }
.topo-node.st-unknown { border-style: dashed; border-color: rgba(247, 255, 74, 0.32); }
.topo-node.st-unknown .tn-glyph, .topo-node.st-unknown .tn-body em .led { color: var(--cy-yellow); }
.topo-node.hot { border-color: var(--cy-cyan); box-shadow: 0 0 22px rgba(0, 234, 255, 0.5); animation: nodepulse var(--dur-breath) ease-in-out infinite; z-index: 3; }
.topo-node.hot .tn-glyph, .topo-node.hot .tn-body em .led { color: var(--cy-cyan); }
@keyframes nodepulse { 0%, 100% { box-shadow: 0 0 14px rgba(0, 234, 255, 0.3); } 50% { box-shadow: 0 0 26px rgba(0, 234, 255, 0.6); } }

.kernel-invariants { position: relative; display: flex; align-items: center; gap: 13px; min-height: 27px; overflow-x: auto; padding: 6px 9px; border-top: 1px solid rgba(0, 234, 255, .12); background: rgba(0, 0, 0, .12); white-space: nowrap; scrollbar-width: thin; }
.kernel-invariants > span { display: inline-flex; align-items: center; gap: 5px; color: var(--cy-txt-dim); font: 750 7px/1 var(--cy-mono); letter-spacing: .055em; }
.kernel-invariants > span i { color: var(--cy-green); font-size: 5px; font-style: normal; text-shadow: 0 0 7px currentColor; }
.kernel-invariants .ki-label { padding-right: 11px; border-right: 1px solid rgba(0, 234, 255, .16); color: var(--cy-cyan); font-weight: 900; letter-spacing: .1em; }
.kernel-invariants > em { margin-left: auto; color: rgba(134, 151, 210, .62); font: 600 7px/1 var(--cy-sans); font-style: normal; }

@media (prefers-reduced-motion: reduce) {
  .cy-bus.live .edge.flow { animation: none !important; }
  .topo-node.hot { animation: none !important; }
}
@media (max-width: 1080px) {
  .bus-head { align-items: flex-start; }
  .bus-title { display: grid; gap: 4px; }
  .bus-title > small { display: none; }
  .identity-envelope { grid-template-columns: 160px minmax(560px, 1fr); }
  .identity-envelope > em { display: none; }
  .topo { height: 230px; }
}
@media (max-width: 760px) {
  .cy-bus { padding-inline: 10px; }
  .bus-head { display: grid; }
  .bus-vitals { overflow-x: auto; padding-bottom: 2px; }
  .identity-envelope { grid-template-columns: 148px minmax(560px, 1fr); }
  .kernel-invariants > em { display: none; }
}
</style>
