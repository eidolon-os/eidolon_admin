<script setup lang="ts">
import { computed } from 'vue'
import { MODE_CN, SVC_GLYPH } from '../constants'
import { compactId, devicePresenceClass, devicePresenceLabel, deviceShort, deviceType, fmtLatency, fmtTime, statusClass } from '../format'
import AgentSpanInspector from './AgentSpanInspector.vue'
import CapabilityRegistry from './CapabilityRegistry.vue'
import ProofChainTiles from './ProofChainTiles.vue'
import MemoryEvidenceLane from './MemoryEvidenceLane.vue'
import TaskWorkflowTimeline from './TaskWorkflowTimeline.vue'
import PermissionLedger from './PermissionLedger.vue'
import type { CompanionUnit, DrawerTarget } from '../types'
import type { MissionControlStream } from '../useMissionControlStream'
import { activityKindLabel, currentActivityHop } from '../activity'

const props = defineProps<{ mc: MissionControlStream; target: DrawerTarget | null }>()
defineEmits<{ (e: 'open-companion', c: CompanionUnit): void; (e: 'close'): void }>()

const {
  ownerName, companionUnits, companionNames, onlineDevices, devices, memory,
  snapshot, experience, traceSpans,
  scopedTurn, scopedJobs, scopedPermissions, companionEvents, focusedCompanion, evidenceChains,
} = props.mc

const drawerComp = computed<CompanionUnit | null>(() => {
  const t = props.target
  if (t?.type === 'companion') return t.c
  if (t?.type === 'moon') return t.s.c
  return null
})
const drawerTurns = computed(() => {
  const c = drawerComp.value
  if (!c) return []
  return (snapshot.value?.recent_turns || []).filter((t) => t.companion_id === c.id).slice(0, 6)
})
</script>

<template>
  <transition name="dw">
    <aside v-if="target" class="drawer" @click.self="$emit('close')">
      <div class="dw-panel">
        <button class="dw-close" @click="$emit('close')">✕</button>

        <template v-if="target.type === 'owner'">
          <span class="dw-kick purple">OWNER · 主人</span>
          <h3>{{ ownerName }}</h3>
          <p class="dw-role">主权主体 —— 一切虚拟伙伴、身体与记忆都归属于此。</p>
          <div class="dw-grid">
            <div><span>虚拟伙伴</span><b class="num">{{ companionUnits.length }}</b></div>
            <div><span>身体在线</span><b class="num">{{ onlineDevices }}/{{ devices.length }}</b></div>
            <div><span>记忆空间</span><b class="num">{{ memory?.realms_total ?? 0 }}</b></div>
            <div><span>记忆召回</span><b class="num">{{ memory?.last_recall_hits ?? 0 }}</b></div>
          </div>
          <span class="dw-sect">伙伴</span>
          <div class="dw-list">
            <button v-for="c in companionUnits" :key="c.id" class="dw-row link" @click="$emit('open-companion', c)">
              <i class="led" :class="statusClass(c.status)" /><b>{{ c.name }}</b><em>{{ c.devices.length }} 身体 · {{ c.realm ? '有记忆' : '无记忆' }}</em>
            </button>
          </div>
          <span class="dw-sect">能力面 · CAPABILITY</span>
          <CapabilityRegistry :cards="experience?.capability_cards || []" />
          <span class="dw-sect">证据链 · PROOF</span>
          <div class="dw-proof"><ProofChainTiles :chains="evidenceChains" /></div>
        </template>

        <template v-else-if="drawerComp">
          <span class="dw-kick">COMPANION · 虚拟伙伴</span>
          <h3>{{ drawerComp.name }}<i v-if="drawerComp.isPrimary" class="dw-pri">★ 主</i></h3>
          <p class="dw-role">{{ drawerComp.kind }} · {{ drawerComp.status }} · 归属 {{ ownerName }}</p>
          <div class="dw-grid">
            <div><span>genome</span><b class="mono sm" :title="drawerComp.genome || undefined" :aria-label="drawerComp.genome ? `完整 genome ID：${drawerComp.genome}` : undefined">{{ compactId(drawerComp.genome) || '—' }}</b></div>
            <div><span>记忆空间</span><b class="mono sm" :title="drawerComp.realm || undefined" :aria-label="drawerComp.realm ? `完整记忆空间 ID：${drawerComp.realm}` : undefined">{{ compactId(drawerComp.realm) || '未开通' }}</b></div>
            <div><span>召回命中</span><b class="num">{{ drawerComp.recall ?? '—' }}</b></div>
            <div><span>后台整理</span><b class="num">{{ drawerComp.runners || '—' }}</b></div>
          </div>
          <span class="dw-sect">身体 / 化身 · {{ drawerComp.devices.length }}</span>
          <div class="dw-list">
            <div v-for="d in drawerComp.devices" :key="d.device_id" class="dw-row">
              <i class="led" :class="devicePresenceClass(d)" /><b>{{ deviceType(d) }}</b><em>{{ deviceShort(d) }} · {{ devicePresenceLabel(d) }}{{ d.interaction_mode ? ' · ' + d.interaction_mode : '' }}</em>
            </div>
            <p v-if="!drawerComp.devices.length" class="dw-empty">未绑定身体</p>
            <p class="dw-readonly">只读观察 · 身体创建、绑定与启动请在设备管理中操作</p>
          </div>
          <span class="dw-sect">最近对话</span>
          <div class="dw-list">
            <button v-for="t in drawerTurns" :key="t.turn_id" class="dw-row link" :class="{ selected: mc.selectedTurnId.value === t.turn_id }" @click="mc.selectTurn(t.turn_id, drawerComp.id)">
              <i class="led" :class="statusClass(t.status)" /><b>{{ (t.status || '').toUpperCase() }}</b><em>{{ fmtLatency(t.latency_ms) }} · 召回 {{ t.memory_hits }} · 工具 ×{{ t.tool_names?.length ?? 0 }}</em>
            </button>
            <p v-if="!drawerTurns.length" class="dw-empty">暂无对话记录</p>
          </div>
          <template v-if="drawerComp.turn">
            <span class="dw-sect">Agent 跨度 · SPANS</span>
            <AgentSpanInspector :spans="traceSpans" />
          </template>
          <span class="dw-sect">证据 · EVIDENCE</span>
          <div class="dw-evidence">
            <MemoryEvidenceLane :memory="memory" :companion="drawerComp" />
            <TaskWorkflowTimeline :jobs="scopedJobs" />
            <PermissionLedger :items="scopedPermissions" />
          </div>
        </template>

        <template v-else-if="target.type === 'activity'">
          <span class="dw-kick">RUNTIME ACTIVITY · 运行活动</span>
          <h3>{{ activityKindLabel(target.activity.kind) }}</h3>
          <p class="dw-role">{{ target.activity.summary }}。这是只读事件投影，不参与轮次、设备命令或任务调度。</p>
          <div class="dw-grid">
            <div><span>Companion</span><b class="mono sm" :title="target.activity.companion_id || undefined">{{ target.activity.companion_id ? companionNames[target.activity.companion_id] || compactId(target.activity.companion_id) : '未归属' }}</b></div>
            <div><span>状态</span><b :class="statusClass(target.activity.status)">{{ target.activity.status }}</b></div>
            <div><span>当前节点</span><b class="mono sm">{{ currentActivityHop(target.activity)?.label || '已结束' }}</b></div>
            <div><span>关联 ID</span><b class="mono sm" :title="target.activity.turn_id || target.activity.job_id || target.activity.activity_id">{{ compactId(target.activity.turn_id || target.activity.job_id || target.activity.activity_id) }}</b></div>
          </div>
          <span class="dw-sect">路径 · ROUTE</span>
          <ol v-if="target.activity.route.length" class="dw-stages">
            <li v-for="hop in target.activity.route" :key="hop.hop_id" class="dw-stage" :class="statusClass(hop.status)">
              <i class="led" :class="statusClass(hop.status)" />
              <b>{{ hop.label }}</b>
              <em :title="hop.node_id">{{ hop.node_type }} · {{ compactId(hop.node_id) }}<template v-if="hop.latency_ms != null"> · {{ fmtLatency(hop.latency_ms) }}</template></em>
            </li>
          </ol>
          <p v-else class="dw-empty">该事件没有足够的事实节点可形成路径</p>
        </template>

        <template v-else-if="target.type === 'service'">
          <span class="dw-kick">SUBSYSTEM · 子项目</span>
          <h3>{{ target.n.cn }}<i class="dw-code">{{ target.n.code }}</i></h3>
          <p class="dw-role">{{ target.n.role }}</p>
          <div class="dw-grid">
            <div><span>状态</span><b :class="target.n.state === 'online' ? 'ok' : target.n.state === 'offline' ? 'bad' : 'warn'">{{ target.n.stateCn }}</b></div>
            <div><span>延迟</span><b class="num">{{ target.n.online ? target.n.latency : '—' }}</b></div>
            <div><span>集成</span><b>{{ MODE_CN[target.n.mode] }}</b></div>
            <div><span>探针</span><b class="mono sm">{{ target.n.detail || '—' }}</b></div>
          </div>
          <span class="dw-sect">最近事件</span>
          <div class="dw-list">
            <div v-for="e in target.n.events" :key="e.event_id" class="dw-row"><em class="mono">{{ fmtTime(e.ts) }}</em><span class="dw-ev">{{ e.summary || e.type }}</span></div>
            <p v-if="!target.n.events.length" class="dw-empty">暂无事件</p>
          </div>
        </template>

        <template v-else-if="target.type === 'trace'">
          <span class="dw-kick">LIVE TRACE · 实时链路</span>
          <h3>{{ focusedCompanion?.name || '全局链路' }}</h3>
          <p class="dw-role">一次对话从身体到大脑再回到身体的完整链路：阶段耗时、Agent 跨度、以及经过的事件流转。</p>
          <div v-if="scopedTurn" class="dw-grid">
            <div><span>状态</span><b :class="statusClass(scopedTurn.status)">{{ scopedTurn.status }}</b></div>
            <div><span>阶段</span><b class="mono sm">{{ scopedTurn.phase || '—' }}</b></div>
            <div><span>Channel Turn</span><b class="mono sm" :title="scopedTurn.channel_turn_id || undefined">{{ compactId(scopedTurn.channel_turn_id) || '未观测' }}</b></div>
            <div><span>Agent Turn</span><b class="mono sm" :title="scopedTurn.agent_turn_id || undefined">{{ compactId(scopedTurn.agent_turn_id) || '未进入' }}</b></div>
          </div>
          <p v-if="scopedTurn?.terminal_reason" class="dw-role">终态：{{ scopedTurn.terminal_reason }}</p>
          <p v-if="scopedTurn?.missing_milestones?.length" class="dw-role warn">缺失印记：{{ scopedTurn.missing_milestones.join(' · ') }}</p>
          <span class="dw-sect">阶段 · STAGES</span>
          <ol v-if="scopedTurn && scopedTurn.stages.length" class="dw-stages">
            <li v-for="s in scopedTurn.stages" :key="s.key" class="dw-stage" :class="statusClass(s.status)">
              <i class="led" :class="statusClass(s.status)" /><b>{{ s.label }}</b>
              <em v-if="s.latency_ms != null" class="num">{{ fmtLatency(s.latency_ms) }}</em>
            </li>
          </ol>
          <p v-else class="dw-empty">当前没有活跃对话</p>
          <span class="dw-sect">Agent 跨度 · SPANS</span>
          <AgentSpanInspector :spans="traceSpans" />
          <span class="dw-sect">事件流转 · FLOW</span>
          <div class="dw-list">
            <button v-for="e in companionEvents.slice(0, 20)" :key="e.event_id" class="dw-row link" :class="{ selected: mc.selectedEventId.value === e.event_id }" @click="mc.selectEvent(e)">
              <em class="mono">{{ fmtTime(e.ts) }}</em>
              <b class="dw-src">{{ SVC_GLYPH[e.source] || '·' }} {{ e.source.toUpperCase() }}</b>
              <span class="dw-ev">{{ e.summary || e.type }}</span>
            </button>
            <p v-if="!companionEvents.length" class="dw-empty">暂无事件流转（设备↔智能体的流转将在这里呈现）</p>
          </div>
        </template>
      </div>
    </aside>
  </transition>
</template>

<style scoped>
.drawer { position: fixed; inset: 0; z-index: 40; background: rgba(3, 1, 10, 0.62); backdrop-filter: blur(3px); display: flex; justify-content: flex-end; }
.dw-panel { position: relative; width: min(400px, 92vw); height: 100%; overflow-y: auto; padding: 22px 22px 30px; border-left: 1px solid rgba(0, 234, 255, 0.4); background: linear-gradient(160deg, rgba(12, 8, 26, 0.98), rgba(6, 3, 16, 0.98)); box-shadow: -20px 0 60px rgba(0, 0, 0, 0.6); }
.dw-panel::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: repeating-linear-gradient(transparent 0 2px, rgba(0, 0, 0, 0.18) 3px 4px); opacity: 0.4; }
.dw-close { position: absolute; top: 16px; right: 16px; width: 30px; height: 30px; border: 1px solid rgba(0, 234, 255, 0.3); background: rgba(0, 234, 255, 0.06); color: var(--cy-cyan); font-size: 13px; cursor: pointer; clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px); }
.dw-close:hover { background: rgba(0, 234, 255, 0.18); }
.dw-kick { font: 700 10px/1 var(--cy-mono); letter-spacing: 0.14em; color: var(--cy-cyan); }
.dw-kick.purple { color: var(--cy-purple); }
.dw-panel h3 { margin: 10px 0 6px; font: 800 22px/1.1 var(--cy-sans); color: #fff; display: flex; align-items: baseline; gap: 8px; }
.dw-pri { font: 700 11px/1 var(--cy-mono); color: var(--cy-yellow); font-style: normal; }
.dw-code { font: 700 11px/1 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; }
.dw-role { margin: 0 0 14px; font: 400 12.5px/1.6 var(--cy-sans); color: #aab6d8; }
.dw-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: rgba(0, 234, 255, 0.14); border: 1px solid rgba(0, 234, 255, 0.14); margin-bottom: 16px; }
.dw-grid > div { padding: 10px 12px; background: rgba(8, 5, 20, 0.92); }
.dw-grid span { font: 700 9px/1 var(--cy-mono); color: var(--cy-txt-dim); letter-spacing: 0.05em; }
.dw-grid b { display: block; margin-top: 6px; font: 900 18px/1 var(--cy-mono); color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-grid b.mono { font-weight: 700; } .dw-grid b.sm { font-size: 11px; }
.dw-grid b.ok { color: var(--cy-green); } .dw-grid b.bad { color: var(--cy-mag); } .dw-grid b.warn { color: var(--cy-yellow); }
.dw-sect { display: block; margin: 4px 0 8px; padding-bottom: 5px; border-bottom: 1px solid rgba(0, 234, 255, 0.14); font: 700 10px/1 var(--cy-mono); letter-spacing: 0.08em; color: var(--cy-mag); }
.dw-list { display: grid; gap: 5px; margin-bottom: 18px; }
.dw-row { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 10px; border: 1px solid rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); text-align: left; }
.dw-row .led { width: 7px; height: 7px; }
.dw-row b { font: 700 12px/1 var(--cy-sans); color: var(--cy-txt); flex: 0 0 auto; }
.dw-row em { font: 600 10px/1.3 var(--cy-mono); color: var(--cy-txt-dim); font-style: normal; margin-left: auto; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-row.link { cursor: pointer; } .dw-row.link:hover { border-color: rgba(0, 234, 255, 0.4); background: rgba(0, 234, 255, 0.06); }
.dw-row.selected { border-color: rgba(0, 234, 255, .65); background: rgba(0, 234, 255, .1); }
.dw-role.warn { color: var(--cy-yellow); }
.dw-row .mono { font-family: var(--cy-mono); color: var(--cy-txt-dim); }
.dw-ev { font-size: 11.5px; color: #aab6d8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-empty { padding: 10px; font-size: 11.5px; color: var(--cy-txt-dim); }
.dw-proof :deep(.proof-tiles) { grid-template-columns: 1fr; }
.dw-stages { display: grid; gap: 5px; margin: 0 0 18px; padding: 0; list-style: none; }
.dw-stage { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); }
.dw-stage .led { width: 7px; height: 7px; }
.dw-stage b { font: 700 12px/1 var(--cy-sans); color: var(--cy-txt); }
.dw-stage em { margin-left: auto; font: 700 10px/1 var(--cy-mono); font-style: normal; color: var(--cy-txt-dim); }
.dw-stage.ok b { color: var(--cy-green); } .dw-stage.warn b { color: var(--cy-yellow); } .dw-stage.bad b { color: var(--cy-mag); }
.dw-src { flex: 0 0 auto; font: 700 10px/1 var(--cy-mono); color: var(--cy-cyan); }
.dw-evidence { display: grid; gap: 8px; margin-bottom: 18px; }
.dw-readonly { margin: 2px 0 0; padding: 7px 10px; border: 1px dashed rgba(0, 234, 255, 0.22); color: var(--cy-txt-dim); font: 600 9px/1.4 var(--cy-mono); }
.dw-enter-active, .dw-leave-active { transition: opacity var(--dur-base); }
.dw-enter-active .dw-panel, .dw-leave-active .dw-panel { transition: transform var(--dur-base) var(--ease-out); }
.dw-enter-from, .dw-leave-to { opacity: 0; }
.dw-enter-from .dw-panel, .dw-leave-to .dw-panel { transform: translateX(40px); }
</style>
