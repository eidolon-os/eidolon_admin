<script setup lang="ts">
// The runtime substrate, demoted to an infra rail: sub-projects wired in
// request-flow order, lighting up as the active turn traverses stages.
import { MODE_CN, MODE_EXP } from '../constants'
import { fmtTime } from '../format'
import type { InfraNode } from '../types'

defineProps<{
  busSpine: InfraNode[]
  busAux: InfraNode[]
  hotService: string
  pipelineActive: boolean
}>()
defineEmits<{ (e: 'open-service', n: InfraNode): void }>()
</script>

<template>
  <footer class="cy-bus" :class="{ live: pipelineActive }">
    <div class="bus-line">
      <span class="bus-cap">运行链路 · REQUEST FLOW</span>
      <template v-for="(n, i) in busSpine" :key="n.id">
        <el-popover placement="top" :width="290" trigger="hover" popper-class="cy-pop" :show-after="60">
          <template #reference>
            <div class="bus-node" :class="[`st-${n.state}`, { hot: n.id === hotService }]" @click="$emit('open-service', n)">
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
          <div class="bus-node aux" :class="`st-${n.state}`" @click="$emit('open-service', n)">
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
  </footer>
</template>

<style scoped>
.cy-bus { display: flex; flex-direction: column; gap: 9px; padding: 11px 16px 9px; border: 1px solid rgba(0, 234, 255, 0.2); background: var(--cy-panel); clip-path: polygon(0 0, 100% 0, 100% 100%, 14px 100%, 0 calc(100% - 14px)); }
.bus-line { display: flex; align-items: center; gap: 0; }
.bus-cap { flex: 0 0 auto; margin-right: 12px; font: 700 9px/1.3 var(--cy-mono); letter-spacing: 0.08em; color: var(--cy-txt-dim); writing-mode: vertical-rl; text-orientation: mixed; transform: rotate(180deg); max-height: 46px; }
.bus-node { position: relative; display: flex; align-items: center; gap: 9px; padding: 7px 12px; border: 1px solid rgba(0, 234, 255, 0.28); background: rgba(0, 234, 255, 0.04); clip-path: polygon(0 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%); cursor: pointer; transition: box-shadow var(--dur-base) var(--ease-out), transform var(--dur-fast) var(--ease-out); }
.bus-node:hover { transform: translateY(-2px); box-shadow: 0 0 18px rgba(0, 234, 255, 0.35); }
.bus-node .bn-glyph { font-size: 19px; font-style: normal; line-height: 1; color: var(--cy-green); text-shadow: 0 0 9px currentColor; }
.bn-body { display: flex; flex-direction: column; gap: 2px; }
.bn-body b { font: 700 12.5px/1 var(--cy-sans); color: #fff; white-space: nowrap; }
.bn-body em { font: 600 8.5px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; letter-spacing: 0.03em; white-space: nowrap; }
.bn-stat { display: flex; align-items: center; gap: 5px; padding-left: 9px; border-left: 1px solid rgba(255, 255, 255, 0.08); }
.bn-stat .led { width: 7px; height: 7px; color: var(--cy-green); }
.bn-stat span { font: 700 9px/1 var(--cy-mono); color: var(--cy-txt-dim); white-space: nowrap; }
.bus-node.st-offline { border-color: rgba(255, 46, 136, 0.4); }
.bus-node.st-offline .bn-glyph, .bus-node.st-offline .led { color: var(--cy-mag); }
.bus-node.st-offline .bn-stat span { color: var(--cy-mag); }
.bus-node.st-unknown { border-style: dashed; border-color: rgba(247, 255, 74, 0.32); }
.bus-node.st-unknown .bn-glyph, .bus-node.st-unknown .led { color: var(--cy-yellow); }
.bus-node.st-unknown .bn-stat span { color: var(--cy-yellow); }
.bus-node.aux { padding: 7px 10px; }
.bus-node.aux .bn-glyph { font-size: 15px; }
.bus-node.aux + .bus-node.aux { margin-left: 8px; }
.bus-node.hot { border-color: var(--cy-cyan); box-shadow: 0 0 22px rgba(0, 234, 255, 0.5); animation: nodepulse 1.2s ease-in-out infinite; }
.bus-node.hot .bn-glyph, .bus-node.hot .bn-stat .led { color: var(--cy-cyan); }
.bus-link { position: relative; flex: 1 1 auto; min-width: 18px; height: 2px; background: rgba(0, 234, 255, 0.2); overflow: visible; }
/* Signature moment: a light packet comet travels the spine when a turn is live. */
.bus-link b { position: absolute; top: -2px; left: -32%; width: 32%; height: 6px; border-radius: 3px; background: linear-gradient(90deg, transparent, rgba(0, 234, 255, 0.9)); box-shadow: 0 0 14px var(--cy-cyan); opacity: 0; }
.bus-link b::after { content: ""; position: absolute; right: -2px; top: 50%; transform: translateY(-50%); width: 6px; height: 6px; border-radius: 50%; background: #eafcff; box-shadow: 0 0 12px var(--cy-cyan), 0 0 4px #fff; }
.cy-bus.live .bus-link { background: rgba(0, 234, 255, 0.3); }
.cy-bus.live .bus-link b { opacity: 1; animation: busflow 1.6s var(--ease-inout) infinite; }
.bus-div { width: 1px; align-self: stretch; margin: 2px 14px; background: rgba(0, 234, 255, 0.18); }
@keyframes busflow { from { left: -32%; } to { left: 100%; } }
@keyframes nodepulse { 0%, 100% { box-shadow: 0 0 16px currentColor; } 50% { box-shadow: 0 0 30px currentColor; } }
@media (prefers-reduced-motion: reduce) { .bus-node.hot, .cy-bus.live .bus-link b { animation: none !important; } }
</style>
