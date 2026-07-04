<script setup lang="ts">
import { Back, Refresh } from '@element-plus/icons-vue'
import DataNumber from '../primitives/DataNumber.vue'
import logoUrl from '@/assets/brand/logo-full-neon.svg'
import type { MissionControlStream } from '../useMissionControlStream'

const props = defineProps<{ mc: MissionControlStream }>()
defineEmits<{ (e: 'return-console'): void }>()

const {
  owners, ownerId, ownerName, streamLabelText, systemStateText, traceId,
  pipelineActive, companions, onlineDevices, devices, deviceRatio, memory,
  activeJobs, jobs, infraNodes, onlineServices, services,
  clock, now, loading, replay, refresh,
} = props.mc
</script>

<template>
  <header class="cy-head">
    <div class="brand">
      <img :src="logoUrl" class="brand-logo" alt="EIDOLON · Personal Sovereign Agent OS" />
      <div class="brand-meta">
        <span class="brand-state" :class="pipelineActive ? 'ok' : 'idle'"><i class="led" />{{ streamLabelText }}</span>
        <span v-if="replay" class="brand-replay">◇ REPLAY</span>
        <span class="brand-owner">OWNER · {{ ownerName }}</span>
        <span class="brand-trace">SYS {{ systemStateText }} · TRACE::{{ traceId }}</span>
      </div>
    </div>

    <div class="hud">
      <div class="meter"><span class="mg cyan">◉</span><b class="num"><DataNumber :value="companions.length" /></b><small>伙伴</small></div>
      <div class="meter">
        <span class="mg cyan">⬡</span><b class="num"><DataNumber :value="onlineDevices" /><i>/{{ devices.length }}</i></b>
        <span class="mbar"><i :style="{ width: deviceRatio + '%' }" /></span><small>身体在线</small>
      </div>
      <div class="meter"><span class="mg yellow">◈</span><b class="num"><DataNumber :value="memory?.realms_total ?? 0" /></b><small>记忆空间</small></div>
      <div class="meter"><span class="mg yellow">⟐</span><b class="num"><DataNumber :value="memory?.last_recall_hits ?? 0" /></b><small>记忆召回</small></div>
      <div class="meter"><span class="mg mag">⚡</span><b class="num"><DataNumber :value="activeJobs" /><i>/{{ jobs.length }}</i></b><small>活动任务</small></div>
      <div class="meter meter-svc">
        <span class="mg">▦</span>
        <span class="svc-leds"><i v-for="n in infraNodes" :key="n.id" class="led" :class="'st-' + n.state" :title="`${n.cn} · ${n.stateCn}`" /></span>
        <small>底座 {{ onlineServices }}/{{ services.length }}</small>
      </div>
    </div>

    <div class="head-ctrl">
      <span class="clock num">{{ clock }}<em>{{ new Date(now).toLocaleDateString() }}</em></span>
      <el-select v-model="ownerId" class="owner-pick" filterable placeholder="OWNER">
        <el-option v-for="o in owners" :key="o.owner_id" :label="o.display_name || o.owner_id" :value="o.owner_id" />
      </el-select>
      <button class="icon-btn" :disabled="loading" title="刷新" @click="refresh"><el-icon :class="{ spin: loading }"><Refresh /></el-icon></button>
      <button class="icon-btn ghost" title="返回控制台" @click="$emit('return-console')"><el-icon><Back /></el-icon></button>
    </div>
  </header>
</template>

<style scoped>
.cy-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.brand-logo { display: block; height: 46px; width: auto; filter: drop-shadow(0 0 12px rgba(0, 234, 255, 0.28)); }
.brand { display: flex; align-items: center; gap: 14px; flex: 0 0 auto; }
.brand-meta { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
.brand-state { display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.08em; border: 1px solid currentColor; clip-path: polygon(6px 0, 100% 0, 100% 100%, 0 100%, 0 6px); }
.brand-state .led { width: 6px; height: 6px; }
.brand-replay { font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-yellow); padding: 2px 6px; border: 1px solid var(--cy-yellow); }
.brand-owner { font: 700 11px/1 var(--cy-mono); color: #fff; letter-spacing: 0.04em; }
.brand-trace { font: 600 9px/1 var(--cy-mono); color: var(--cy-txt-dim); letter-spacing: 0.06em; }

.hud { display: flex; align-items: stretch; gap: 0; flex: 1 1 auto; justify-content: space-evenly; margin: 0 8px; }
.meter { display: grid; grid-template-columns: auto auto; grid-template-rows: auto auto; align-items: center; gap: 1px 8px; padding: 4px 14px; border-left: 1px solid rgba(0, 234, 255, 0.12); }
.meter:first-child { border-left: 0; }
.mg { grid-row: 1 / 3; font-size: 20px; font-style: normal; line-height: 1; color: var(--cy-cyan); text-shadow: 0 0 10px currentColor; }
.mg.yellow { color: var(--cy-yellow); } .mg.cyan { color: var(--cy-cyan); } .mg.mag { color: var(--cy-mag); }
.meter b { font: 900 22px/1 var(--cy-mono); color: #fff; }
.meter b i { font-size: 13px; font-style: normal; color: var(--cy-txt-dim); }
.meter small { grid-column: 2; font: 600 9px/1 var(--cy-mono); color: var(--cy-txt-dim); letter-spacing: 0.06em; }
.mbar { grid-column: 2; width: 100%; height: 3px; background: rgba(0, 234, 255, 0.12); overflow: hidden; }
.mbar i { display: block; height: 100%; background: var(--cy-cyan); box-shadow: 0 0 8px var(--cy-cyan); transition: width var(--dur-slow) var(--ease-out); }
.meter-svc .svc-leds { grid-column: 2; display: inline-flex; gap: 4px; }
.svc-leds .led { width: 7px; height: 7px; }
.head-ctrl { display: flex; align-items: center; gap: 10px; flex: 0 0 auto; }
.clock { display: flex; flex-direction: column; align-items: flex-end; font: 900 18px/1 var(--cy-mono); color: var(--cy-cyan); text-shadow: 0 0 14px rgba(0, 234, 255, 0.5); }
.clock em { margin-top: 3px; font: 600 9px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; letter-spacing: 0.08em; }
.owner-pick { width: 140px; }
.icon-btn { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--cy-cyan); color: var(--cy-cyan); background: rgba(0, 234, 255, 0.08); cursor: pointer; clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px); transition: background var(--dur-fast) var(--ease-out); }
.icon-btn:hover { background: rgba(0, 234, 255, 0.2); }
.icon-btn.ghost { border-color: var(--cy-txt-dim); color: var(--cy-txt-dim); background: transparent; }
.icon-btn.ghost:hover { border-color: var(--cy-cyan); color: var(--cy-cyan); background: rgba(0, 234, 255, 0.08); }
.spin { animation: spin 900ms linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 1080px) { .cy-head { flex-wrap: wrap; } }
</style>
