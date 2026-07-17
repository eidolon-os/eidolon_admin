<script setup lang="ts">
// Live kernel backplane. The center is a logical runtime-identity boundary,
// never a synthetic health node; every surrounding process comes from a real
// service probe and every highlighted route comes from observed activities.
import { computed } from 'vue'
import { EDGE_LABEL, INFRA_EDGES, INFRA_LAYOUT, INFRA_VB, MODE_CN, MODE_EXP } from '../constants'
import { spineReached } from '../flow'
import { fmtTime } from '../format'
import type { InfraNode } from '../types'

const props = defineProps<{
  nodes: InfraNode[]
  hotServices: string[]
  activityOwners: Record<string, string[]>
  pipelineActive: boolean
  ownerName: string
  scopeName?: string
  companionCount: number
  bodyOnline: number
  bodyTotal: number
  realmCount: number
  activeJobs: number
  activeRoutes: number
}>()
defineEmits<{ (e: 'open-service', n: InfraNode): void }>()

const layoutById = new Map(INFRA_LAYOUT.map((layout) => [layout.id, layout]))
const hotSet = computed(() => new Set(props.hotServices))
const coreNodes = computed(() => props.nodes.filter((node) => node.tier !== 'external'))
const extensionNodes = computed(() => props.nodes.filter((node) => node.tier === 'external'))
const coreOnlineCount = computed(() => coreNodes.value.filter((node) => node.online).length)
const extensionOnlineCount = computed(() => extensionNodes.value.filter((node) => node.online).length)
const coreNominal = computed(() => coreNodes.value.every((node) => node.state === 'online'))

function edgeReached(target: string): boolean {
  return props.hotServices.some((service) => spineReached(service, target))
}

const nodesView = computed(() =>
  INFRA_LAYOUT.map((layout) => {
    const node = props.nodes.find((item) => item.id === layout.id)
    if (!node) return null
    return {
      node,
      px: (layout.x / INFRA_VB.w) * 100,
      py: (layout.y / INFRA_VB.h) * 100,
    }
  }).filter((view): view is { node: InfraNode; px: number; py: number } => view !== null),
)

const edgesView = computed(() =>
  INFRA_EDGES.map((edge) => {
    const from = layoutById.get(edge.from)
    const to = layoutById.get(edge.to)
    if (!from || !to) return null
    const x1 = (from.x / INFRA_VB.w) * 100
    const y1 = (from.y / INFRA_VB.h) * 100
    const x2 = (to.x / INFRA_VB.w) * 100
    const y2 = (to.y / INFRA_VB.h) * 100
    const bend = Math.max(4, Math.abs(x2 - x1) * .08)
    const labelOffset = edge.from === 'agent' && edge.to === 'mementos'
      ? { x: 2, y: 5 }
      : edge.from === 'memory' && edge.to === 'nats'
        ? { x: -2, y: -4 }
        : { x: 0, y: 0 }
    return {
      ...edge,
      d: `M${x1} ${y1} C${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
      mx: (x1 + x2) / 2 + labelOffset.x,
      my: (y1 + y2) / 2 + labelOffset.y,
    }
  }).filter((view) => view !== null),
)

const schedulerItems = computed(() =>
  Object.entries(props.activityOwners)
    .flatMap(([serviceId, owners]) => owners.map((owner) => ({
      key: `${serviceId}:${owner}`,
      owner,
      service: props.nodes.find((node) => node.id === serviceId)?.cn || serviceId,
    })))
    .slice(0, 5),
)
const activeContextOwners = computed(() => [...new Set(schedulerItems.value.map((item) => item.owner))])
const kernelContextLabel = computed(() => {
  if (activeContextOwners.value.length === 1) return `ACTIVE · ${activeContextOwners.value[0]}`
  if (activeContextOwners.value.length > 1) return `${activeContextOwners.value.length} CONCURRENT CONTEXTS`
  if (props.scopeName) return `VIEW · ${props.scopeName}`
  return 'OWNER CONTEXT'
})
</script>

<template>
  <footer class="kernel-deck" :class="{ live: pipelineActive }">
    <header class="kd-head">
      <div class="kd-title">
        <span><i class="led" :class="pipelineActive ? 'ok' : 'idle'" />EIDOLON OS · LIVE KERNEL</span>
        <b>主权内核运行背板</b>
        <small>身份留在内核，身体与执行资源按上下文接入</small>
      </div>
      <div class="kd-vitals">
        <span><small>CORE</small><b :class="coreNominal ? 'ok' : 'bad'">{{ coreOnlineCount }}/{{ coreNodes.length }}</b></span>
        <span><small>EXT</small><b :class="extensionOnlineCount ? 'ok' : 'idle'">{{ extensionOnlineCount }}/{{ extensionNodes.length }}</b></span>
        <span><small>SYSCALLS</small><b :class="activeRoutes ? 'cyan' : 'idle'">{{ activeRoutes }}</b></span>
      </div>
    </header>

    <div class="kernel-scroll">
      <section class="kernel-stage" aria-label="Eidolon OS live kernel backplane">
        <div class="resource-zone rz-body">
          <span>BODY I/O FABRIC</span>
          <b>{{ bodyOnline }}/{{ bodyTotal }}</b>
          <small>身体在场</small>
        </div>
        <div class="resource-zone rz-agent">
          <span>AGENT EXEC</span>
          <b>{{ companionCount }}</b>
          <small>伙伴上下文</small>
        </div>
        <div class="resource-zone rz-memory">
          <span>MEMORY FS</span>
          <b>{{ realmCount }}</b>
          <small>主权记忆域</small>
        </div>
        <div class="resource-zone rz-events">
          <span>EVENT IPC</span>
          <b>NATS</b>
          <small>事件与扇出</small>
        </div>
        <div class="resource-zone rz-workers">
          <span>WORKER BAY</span>
          <b>{{ activeJobs }}</b>
          <small>活动任务</small>
        </div>

        <svg class="kernel-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <radialGradient id="kernelField" cx="50%" cy="50%" r="50%">
              <stop offset="0" stop-color="#a44bff" stop-opacity=".16" />
              <stop offset="1" stop-color="#a44bff" stop-opacity="0" />
            </radialGradient>
          </defs>
          <circle cx="50" cy="52" r="17" fill="url(#kernelField)" />
          <path class="kernel-axis" d="M41 52 H59 M50 30 V74" vector-effect="non-scaling-stroke" />
          <path
            v-for="(edge, index) in edgesView"
            :key="index"
            :d="edge.d"
            class="edge"
            :class="[`k-${edge.kind}`, { spine: edge.spine, flow: pipelineActive && edge.spine && edgeReached(edge.to), front: pipelineActive && edge.spine && hotSet.has(edge.to) }]"
            vector-effect="non-scaling-stroke"
          />
        </svg>

        <span
          v-for="(edge, index) in edgesView"
          :key="'label-' + index"
          class="edge-label"
          :class="`k-${edge.kind}`"
          :style="{ left: edge.mx + '%', top: edge.my + '%' }"
        >{{ EDGE_LABEL[edge.kind] }}</span>

        <div class="sovereign-core" :class="{ active: pipelineActive }">
          <i class="kc-orbit orbit-a" />
          <i class="kc-orbit orbit-b" />
          <div class="kc-chip">
            <small>LOGICAL KERNEL · IDENTITY BOUND</small>
            <strong>{{ ownerName }}</strong>
            <span>{{ kernelContextLabel }}</span>
            <em><i class="led" :class="coreNominal ? 'ok' : 'bad'" />{{ pipelineActive ? 'SCHEDULING' : 'RESIDENT' }}</em>
          </div>
          <span class="kc-port port-n">CONTEXT</span>
          <span class="kc-port port-e">DISPATCH</span>
          <span class="kc-port port-s">AUDIT</span>
          <span class="kc-port port-w">INGRESS</span>
        </div>

        <el-popover v-for="view in nodesView" :key="view.node.id" placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
          <template #reference>
            <button
              class="process-node"
              :class="[`st-${view.node.state}`, `t-${view.node.tier}`, { hot: hotSet.has(view.node.id) }]"
              :style="{ left: view.px + '%', top: view.py + '%' }"
              type="button"
              @click="$emit('open-service', view.node)"
            >
              <i class="pn-glyph">{{ view.node.glyph }}</i>
              <span>
                <small>{{ view.node.code }}</small>
                <b>{{ view.node.cn }}</b>
                <em><i class="led" />{{ view.node.stateCn }}{{ view.node.online ? ' · ' + view.node.latency : '' }}</em>
              </span>
              <mark v-if="activityOwners[view.node.id]?.length">@{{ activityOwners[view.node.id].slice(0, 2).join(' · ') }}</mark>
            </button>
          </template>
          <div class="pop">
            <div class="pop-h"><b>{{ view.node.cn }}</b><em>{{ view.node.code }}</em></div>
            <p class="pop-role">{{ view.node.role }}</p>
            <div class="pop-rows">
              <div><dt>状态</dt><dd :class="{ ok: view.node.state === 'online', bad: view.node.state === 'offline', warn: view.node.state === 'unknown' }">{{ view.node.stateCn }}{{ view.node.online ? ' · ' + view.node.latency : '' }}</dd></div>
              <div v-if="view.node.state === 'unknown'"><dt>说明</dt><dd class="warn">无健康接口，存活由 supervisord 托管</dd></div>
              <div><dt>集成</dt><dd>{{ MODE_CN[view.node.mode] }}（{{ MODE_EXP[view.node.mode] }}）</dd></div>
              <div v-if="view.node.detail"><dt>探针</dt><dd>{{ view.node.detail }}</dd></div>
            </div>
            <div v-if="view.node.events.length" class="pop-ev"><span class="pop-ev-h">最近事件</span><p v-for="event in view.node.events" :key="event.event_id"><em>{{ fmtTime(event.ts) }}</em>{{ event.summary || event.type }}</p></div>
          </div>
        </el-popover>
      </section>
    </div>

    <div class="scheduler-strip" :class="{ active: schedulerItems.length }">
      <span class="ss-state"><i class="led" :class="schedulerItems.length ? 'ok' : 'idle'" />{{ schedulerItems.length ? 'SCHEDULER LIVE' : 'SCHEDULER IDLE' }}</span>
      <div v-if="schedulerItems.length" class="ss-queue">
        <span v-for="item in schedulerItems" :key="item.key"><b>{{ item.owner }}</b><i>→</i>{{ item.service }}</span>
      </div>
      <span v-else class="ss-empty">NO ACTIVE SYSCALLS · 等待运行事实</span>
      <em>OBSERVE ONLY · 不参与调度</em>
    </div>
  </footer>
</template>

<style scoped>
.kernel-deck { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 8px; padding: 12px 14px 10px; overflow: hidden; border: 1px solid rgba(0, 234, 255, .24); background: linear-gradient(145deg, rgba(10, 7, 27, .96), rgba(4, 3, 14, .94)); clip-path: polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 calc(100% - 14px)); }
.kernel-deck::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at 50% 47%, rgba(164, 75, 255, .09), transparent 31%), repeating-linear-gradient(90deg, transparent 0 39px, rgba(0, 234, 255, .018) 40px); }
.kd-head { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.kd-title { display: flex; align-items: baseline; gap: 9px; min-width: 0; }
.kd-title > span { display: inline-flex; align-items: center; gap: 6px; color: var(--cy-cyan); font: 900 9px/1 var(--cy-mono); letter-spacing: .13em; }
.kd-title .led { width: 5px; height: 5px; }
.kd-title > b { color: #fff; font: 850 14px/1 var(--cy-sans); white-space: nowrap; }
.kd-title > small { overflow: hidden; color: var(--cy-txt-dim); font: 600 8px/1 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.kd-vitals { display: flex; align-items: center; gap: 6px; }
.kd-vitals > span { display: flex; align-items: center; gap: 6px; min-height: 25px; padding: 4px 8px; border: 1px solid rgba(134, 151, 210, .13); background: rgba(255, 255, 255, .018); }
.kd-vitals small { color: var(--cy-txt-dim); font: 700 7px/1 var(--cy-mono); letter-spacing: .08em; }
.kd-vitals b { font: 900 10px/1 var(--cy-mono); }

.kernel-scroll { position: relative; overflow-x: auto; overflow-y: hidden; scrollbar-color: rgba(0, 234, 255, .25) transparent; scrollbar-width: thin; }
.kernel-stage { position: relative; box-sizing: border-box; width: 100%; min-width: 940px; height: 292px; overflow: hidden; border: 1px solid rgba(134, 151, 210, .09); background-image: linear-gradient(rgba(134, 151, 210, .027) 1px, transparent 1px), linear-gradient(90deg, rgba(134, 151, 210, .027) 1px, transparent 1px); background-size: 24px 24px; }
.kernel-stage::before, .kernel-stage::after { content: ""; position: absolute; pointer-events: none; }
.kernel-stage::before { inset: 0; background: linear-gradient(90deg, rgba(0, 234, 255, .025), transparent 43%, rgba(164, 75, 255, .035) 50%, transparent 57%, rgba(247, 255, 74, .018)); }
.kernel-stage::after { left: 50%; top: 0; bottom: 0; width: 1px; background: linear-gradient(transparent, rgba(164, 75, 255, .26), transparent); }

.resource-zone { position: absolute; z-index: 0; overflow: hidden; border: 1px solid rgba(134, 151, 210, .1); background: rgba(255, 255, 255, .012); }
.resource-zone::after { content: ""; position: absolute; inset: 0; pointer-events: none; background: linear-gradient(135deg, rgba(255, 255, 255, .018), transparent 50%); }
.resource-zone > span { position: absolute; top: 8px; left: 9px; color: var(--cy-txt-dim); font: 800 7px/1 var(--cy-mono); letter-spacing: .11em; }
.resource-zone > b { position: absolute; right: 9px; top: 7px; color: var(--cy-txt); font: 900 11px/1 var(--cy-mono); }
.resource-zone > small { position: absolute; right: 9px; top: 21px; color: rgba(134, 151, 210, .52); font: 600 6px/1 var(--cy-sans); }
.rz-body { left: 1%; top: 7%; bottom: 7%; width: 41%; border-color: rgba(0, 234, 255, .14); background: linear-gradient(135deg, rgba(0, 234, 255, .04), transparent 65%); }
.rz-agent { left: 58%; top: 7%; width: 17%; height: 49%; border-color: rgba(247, 255, 74, .12); }
.rz-memory { left: 77%; top: 7%; width: 22%; height: 49%; border-color: rgba(55, 245, 179, .13); }
.rz-events { left: 58%; top: 60%; width: 17%; height: 33%; border-color: rgba(55, 245, 179, .11); }
.rz-workers { left: 77%; top: 60%; width: 22%; height: 33%; border-color: rgba(255, 46, 136, .13); }

.kernel-wires { position: absolute; z-index: 1; inset: 0; width: 100%; height: 100%; overflow: visible; }
.kernel-axis { fill: none; stroke: rgba(164, 75, 255, .18); stroke-width: 1; stroke-dasharray: 2 5; }
.edge { fill: none; stroke-width: 1.4; opacity: .42; }
.edge.k-rtc { stroke: var(--cy-cyan); }
.edge.k-grpc { stroke: var(--cy-yellow); }
.edge.k-nats { stroke: var(--cy-green); }
.edge.k-task { stroke: var(--cy-mag); }
.edge.k-ctrl { stroke: var(--cy-txt-dim); stroke-dasharray: 3 4; }
.edge.spine { opacity: .68; stroke-width: 1.8; }
.kernel-deck.live .edge.flow { opacity: 1; stroke-dasharray: 5 5; animation: packetFlow .7s linear infinite; }
.kernel-deck.live .edge.flow.front { animation-duration: .42s; filter: drop-shadow(0 0 3px currentColor); }
.edge-label { position: absolute; z-index: 2; transform: translate(-50%, -50%); padding: 1px 4px; background: rgba(6, 4, 18, .86); color: var(--cy-txt-dim); font: 800 7px/1 var(--cy-mono); letter-spacing: .06em; pointer-events: none; }
.edge-label.k-rtc { color: var(--cy-cyan); } .edge-label.k-grpc { color: var(--cy-yellow); } .edge-label.k-nats { color: var(--cy-green); } .edge-label.k-task { color: var(--cy-mag); }

.sovereign-core { position: absolute; z-index: 4; left: 50%; top: 52%; width: 158px; height: 158px; transform: translate(-50%, -50%); }
.kc-orbit { position: absolute; inset: 0; border: 1px solid rgba(164, 75, 255, .32); border-radius: 50%; font-style: normal; }
.orbit-a { border-style: dashed; clip-path: polygon(0 0, 100% 0, 100% 45%, 0 80%); animation: kernelSpin 14s linear infinite; }
.orbit-b { inset: 12px; border-color: rgba(0, 234, 255, .24); clip-path: polygon(0 12%, 100% 0, 70% 100%, 0 100%); animation: kernelSpin 10s linear infinite reverse; }
.sovereign-core.active .kc-orbit { box-shadow: 0 0 18px rgba(164, 75, 255, .13); }
.kc-chip { position: absolute; inset: 27px; display: grid; place-content: center; gap: 5px; border: 1px solid rgba(164, 75, 255, .55); background: radial-gradient(circle at 50% 35%, rgba(164, 75, 255, .2), rgba(8, 5, 22, .96) 65%); clip-path: polygon(16% 0, 84% 0, 100% 16%, 100% 84%, 84% 100%, 16% 100%, 0 84%, 0 16%); text-align: center; box-shadow: inset 0 0 24px rgba(164, 75, 255, .09), 0 0 28px rgba(164, 75, 255, .13); }
.kc-chip small { color: var(--cy-purple); font: 800 5.5px/1 var(--cy-mono); letter-spacing: .08em; }
.kc-chip strong { max-width: 88px; overflow: hidden; color: #fff; font: 900 18px/1 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.kc-chip > span { max-width: 88px; overflow: hidden; color: var(--cy-cyan); font: 800 7px/1 var(--cy-mono); text-overflow: ellipsis; white-space: nowrap; }
.kc-chip em { display: inline-flex; align-items: center; justify-content: center; gap: 5px; color: var(--cy-txt-dim); font: 700 6px/1 var(--cy-mono); font-style: normal; }
.kc-chip .led { width: 5px; height: 5px; }
.kc-port { position: absolute; color: rgba(134, 151, 210, .68); font: 700 5.5px/1 var(--cy-mono); letter-spacing: .08em; }
.port-n { left: 50%; top: 1px; transform: translateX(-50%); } .port-s { left: 50%; bottom: 1px; transform: translateX(-50%); }
.port-e { right: -4px; top: 50%; transform: translateY(-50%) rotate(90deg); } .port-w { left: -3px; top: 50%; transform: translateY(-50%) rotate(-90deg); }

.process-node { position: absolute; z-index: 5; display: flex; align-items: center; gap: 7px; min-width: 112px; transform: translate(-50%, -50%); padding: 7px 9px; border: 1px solid rgba(0, 234, 255, .28); background: rgba(6, 4, 18, .94); color: inherit; clip-path: polygon(0 0, 100% 0, 100% calc(100% - 7px), calc(100% - 7px) 100%, 0 100%); cursor: pointer; text-align: left; transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-base), border-color var(--dur-base); }
.process-node:hover, .process-node:focus-visible { z-index: 8; transform: translate(-50%, -50%) scale(1.06); border-color: var(--cy-cyan); box-shadow: 0 0 18px rgba(0, 234, 255, .26); outline: none; }
.pn-glyph { color: var(--cy-cyan); font-size: 16px; font-style: normal; line-height: 1; text-shadow: 0 0 9px currentColor; }
.process-node > span { display: grid; gap: 2px; min-width: 0; }
.process-node small { max-width: 92px; overflow: hidden; color: rgba(134, 151, 210, .58); font: 650 5.5px/1 var(--cy-mono); text-overflow: ellipsis; white-space: nowrap; }
.process-node b { color: #fff; font: 800 11px/1 var(--cy-sans); white-space: nowrap; }
.process-node em { display: inline-flex; align-items: center; gap: 4px; color: var(--cy-txt-dim); font: 650 7px/1 var(--cy-mono); font-style: normal; white-space: nowrap; }
.process-node em .led { width: 5px; height: 5px; color: var(--cy-green); }
.process-node mark { position: absolute; left: 8px; bottom: -12px; max-width: 120px; overflow: hidden; padding: 2px 5px; border: 1px solid rgba(0, 234, 255, .25); background: rgba(6, 4, 18, .96); color: var(--cy-cyan); font: 750 6px/1 var(--cy-mono); text-overflow: ellipsis; white-space: nowrap; }
.process-node.t-middleware { border-color: rgba(247, 255, 74, .27); } .process-node.t-middleware .pn-glyph { color: var(--cy-yellow); }
.process-node.t-external { border-color: rgba(255, 46, 136, .3); border-style: dashed; } .process-node.t-external .pn-glyph { color: var(--cy-mag); }
.process-node.st-offline { border-color: rgba(255, 46, 136, .45); } .process-node.st-offline .pn-glyph, .process-node.st-offline em, .process-node.st-offline em .led { color: var(--cy-mag); }
.process-node.st-unknown { border-style: dashed; border-color: rgba(247, 255, 74, .36); } .process-node.st-unknown .pn-glyph, .process-node.st-unknown em .led { color: var(--cy-yellow); }
.process-node.hot { border-color: var(--cy-cyan); box-shadow: 0 0 22px rgba(0, 234, 255, .38); animation: processPulse var(--dur-breath) ease-in-out infinite; }

.scheduler-strip { position: relative; display: flex; align-items: center; gap: 11px; min-height: 30px; overflow-x: auto; padding: 6px 8px; border-top: 1px solid rgba(0, 234, 255, .12); background: rgba(0, 0, 0, .14); white-space: nowrap; scrollbar-width: thin; }
.ss-state { display: inline-flex; align-items: center; gap: 6px; padding-right: 11px; border-right: 1px solid rgba(0, 234, 255, .14); color: var(--cy-txt-dim); font: 850 7px/1 var(--cy-mono); letter-spacing: .08em; }
.ss-state .led { width: 5px; height: 5px; }
.scheduler-strip.active .ss-state { color: var(--cy-green); }
.ss-queue { display: flex; gap: 5px; }
.ss-queue > span { display: inline-flex; align-items: center; gap: 5px; padding: 4px 7px; border: 1px solid rgba(0, 234, 255, .13); color: var(--cy-txt-dim); font: 700 7px/1 var(--cy-mono); }
.ss-queue b { color: var(--cy-txt); } .ss-queue i { color: var(--cy-cyan); font-style: normal; }
.ss-empty { color: rgba(134, 151, 210, .58); font: 650 7px/1 var(--cy-mono); letter-spacing: .06em; }
.scheduler-strip > em { margin-left: auto; color: var(--cy-green); font: 750 6.5px/1 var(--cy-mono); font-style: normal; letter-spacing: .08em; }

@keyframes packetFlow { to { stroke-dashoffset: -20; } }
@keyframes kernelSpin { to { transform: rotate(360deg); } }
@keyframes processPulse { 50% { box-shadow: 0 0 28px rgba(0, 234, 255, .55); } }
@media (prefers-reduced-motion: reduce) {
  .edge.flow, .kc-orbit, .process-node.hot { animation: none !important; }
}
@media (max-width: 940px) {
  .kd-head { align-items: flex-start; }
  .kd-title { display: grid; gap: 4px; }
  .kd-title > small { display: none; }
}
@media (max-width: 700px) {
  .kernel-deck { padding-inline: 10px; }
  .kd-head { display: grid; }
  .kd-vitals { overflow-x: auto; }
  .scheduler-strip > em { display: none; }
}
</style>
