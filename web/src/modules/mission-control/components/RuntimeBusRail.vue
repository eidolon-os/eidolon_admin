<script setup lang="ts">
// The runtime SUBSTRATE as an architecture graph (not a linear bus): infra
// services placed by role and connected by their real relationships. The
// request spine (hub→livekit→channel→agent→memory) flows when a turn is live;
// the stage's service pulses. client-web is intentionally absent — it's a body.
import { computed } from 'vue'
import { EDGE_LABEL, INFRA_EDGES, INFRA_LAYOUT, INFRA_VB, MODE_CN, MODE_EXP, TIER_BANDS } from '../constants'
import { fmtTime } from '../format'
import type { InfraNode } from '../types'

const props = defineProps<{
  nodes: InfraNode[]
  hotService: string
  pipelineActive: boolean
}>()
defineEmits<{ (e: 'open-service', n: InfraNode): void }>()

const layoutById = new Map(INFRA_LAYOUT.map((l) => [l.id, l]))

// Tier bands (业务组件 / 中间件 / 外挂) as background zones + captions.
const bandsView = TIER_BANDS.map((b) => ({
  ...b,
  top: (b.y0 / INFRA_VB.h) * 100,
  height: ((b.y1 - b.y0) / INFRA_VB.h) * 100,
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
    <span class="bus-cap">运行底座 · ARCHITECTURE</span>
    <div class="topo">
      <div
        v-for="b in bandsView"
        :key="b.tier"
        class="tier-band"
        :class="'tb-' + b.tier"
        :style="{ top: b.top + '%', height: b.height + '%' }"
      >
        <span class="tier-label">{{ b.label }}</span>
      </div>
      <svg class="topo-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <path
          v-for="(e, i) in edgesView"
          :key="i"
          :d="e.d"
          class="edge"
          :class="[`k-${e.kind}`, { spine: e.spine, flow: pipelineActive && e.spine }]"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      <span v-for="(e, i) in edgesView" :key="'l' + i" class="edge-label" :class="`k-${e.kind}`" :style="{ left: e.mx + '%', top: e.my + '%' }">{{ EDGE_LABEL[e.kind] }}</span>

      <el-popover v-for="v in nodesView" :key="v.node.id" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
        <template #reference>
          <div
            class="topo-node"
            :class="[`st-${v.node.state}`, `t-${v.node.tier}`, { hot: v.node.id === hotService }]"
            :style="{ left: v.px + '%', top: v.py + '%' }"
            @click="$emit('open-service', v.node)"
          >
            <i class="tn-glyph">{{ v.node.glyph }}</i>
            <div class="tn-body">
              <b>{{ v.node.cn }}</b>
              <em><i class="led" />{{ v.node.stateCn }}{{ v.node.online ? ' · ' + v.node.latency : '' }}</em>
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
  </footer>
</template>

<style scoped>
.cy-bus { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 6px; padding: 10px 16px; border: 1px solid rgba(0, 234, 255, 0.2); background: var(--cy-panel); clip-path: polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 calc(100% - 14px)); }
.bus-cap { font: 700 9px/1.3 var(--cy-mono); letter-spacing: 0.08em; color: var(--cy-txt-dim); }
.topo { position: relative; width: 100%; height: 208px; }
.topo-wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.tier-band { position: absolute; left: 0; right: 0; border: 1px dashed rgba(255, 255, 255, 0.06); border-radius: 4px; pointer-events: none; }
.tier-band.tb-service { background: rgba(0, 234, 255, 0.035); }
.tier-band.tb-middleware { background: rgba(247, 255, 74, 0.03); }
.tier-band.tb-external { background: rgba(255, 46, 136, 0.03); }
.tier-label { position: absolute; top: 4px; left: 8px; font: 700 8px/1 var(--cy-mono); letter-spacing: 0.12em; color: var(--cy-txt-dim); }

.edge { fill: none; stroke-width: 1.4; opacity: 0.5; }
.edge.k-rtc { stroke: var(--cy-cyan); }
.edge.k-grpc { stroke: var(--cy-yellow); }
.edge.k-nats { stroke: var(--cy-green); }
.edge.k-task { stroke: var(--cy-mag); }
.edge.k-ctrl { stroke: var(--cy-txt-dim); stroke-dasharray: 3 4; opacity: 0.35; }
.edge.spine { opacity: 0.7; stroke-width: 1.8; }
.cy-bus.live .edge.flow { opacity: 1; stroke-dasharray: 5 5; animation: dashflow 0.7s linear infinite; }
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
.topo-node.hot { border-color: var(--cy-cyan); box-shadow: 0 0 22px rgba(0, 234, 255, 0.5); animation: nodepulse 1.2s ease-in-out infinite; z-index: 3; }
.topo-node.hot .tn-glyph, .topo-node.hot .tn-body em .led { color: var(--cy-cyan); }
@keyframes nodepulse { 0%, 100% { box-shadow: 0 0 14px rgba(0, 234, 255, 0.3); } 50% { box-shadow: 0 0 26px rgba(0, 234, 255, 0.6); } }

@media (prefers-reduced-motion: reduce) {
  .cy-bus.live .edge.flow { animation: none !important; }
  .topo-node.hot { animation: none !important; }
}
@media (max-width: 1080px) { .topo { height: 220px; } }
</style>
