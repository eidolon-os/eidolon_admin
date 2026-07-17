<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RuntimeActivity, RuntimeRouteHop } from '@/api/missionControl'
import { activityKindLabel, activityPhases, activityStatusLabel, isActiveActivity } from '../activity'
import { compactId, fmtLatency, statusClass } from '../format'

const props = defineProps<{
  activities: RuntimeActivity[]
  companionNames: Record<string, string>
  deviceNames: Record<string, string>
  scope?: string
}>()
const emit = defineEmits<{ (e: 'open', activity: RuntimeActivity): void }>()

const expandedIds = ref(new Set<string>())

function hopLabel(hop: RuntimeRouteHop): string {
  if (hop.node_type === 'device') return props.deviceNames[hop.node_id] || '身体'
  if (hop.node_type === 'companion') return props.companionNames[hop.node_id] || 'Companion'
  return hop.label
}

const cards = computed(() => props.activities.slice(0, 4).map((activity) => ({
  activity,
  phases: activityPhases(activity, hopLabel),
})))

function companionLabel(activity: RuntimeActivity): string {
  if (!activity.companion_id) return 'OWNER'
  return props.companionNames[activity.companion_id] || compactId(activity.companion_id)
}

function isExpanded(id: string): boolean {
  return expandedIds.value.has(id)
}

function toggleExpanded(id: string): void {
  const next = new Set(expandedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedIds.value = next
}

function openFromKeyboard(event: KeyboardEvent, activity: RuntimeActivity): void {
  if (event.target === event.currentTarget) emit('open', activity)
}
</script>

<template>
  <section class="activity-board" :class="{ standby: !activities.length }">
    <header class="ab-head">
      <div class="ab-title">
        <i class="led" :class="activities.some(isActiveActivity) ? 'ok' : 'idle'" />
        <span>运行活动<small>RUNTIME ACTIVITY</small></span>
      </div>
      <div class="ab-meta">
        <em v-if="scope">聚焦：{{ scope }}</em>
        <b v-if="activities.length">{{ cards.length }}<i v-if="activities.length > cards.length"> / {{ activities.length }}</i></b>
      </div>
    </header>

    <div v-if="cards.length" class="ab-cards">
      <article
        v-for="card in cards"
        :key="card.activity.activity_id"
        class="ab-lane"
        :class="[
          statusClass(card.activity.status),
          { live: isActiveActivity(card.activity), expanded: isExpanded(card.activity.activity_id) },
        ]"
        role="button"
        tabindex="0"
        :aria-label="`查看${companionLabel(card.activity)}的${activityKindLabel(card.activity.kind)}详情`"
        @click="emit('open', card.activity)"
        @keydown.enter="openFromKeyboard($event, card.activity)"
        @keydown.space.prevent="openFromKeyboard($event, card.activity)"
      >
        <div class="ab-lane-head">
          <div class="ab-identity">
            <span class="ab-kind" :class="statusClass(card.activity.status)">{{ activityKindLabel(card.activity.kind) }}</span>
            <span>
              <b :title="card.activity.companion_id || undefined">{{ companionLabel(card.activity) }}</b>
              <small>{{ card.activity.summary }}</small>
            </span>
          </div>
          <span class="ab-status" :class="statusClass(card.activity.status)" :title="card.activity.status">
            <i />{{ activityStatusLabel(card.activity.status) }}
          </span>
        </div>

        <ol
          v-if="card.phases.length"
          class="ab-phases"
          :style="{ '--phase-count': card.phases.length }"
          aria-label="活动阶段"
        >
          <li
            v-for="phase in card.phases"
            :key="phase.key"
            class="ab-phase"
            :class="[statusClass(phase.status), { current: phase.current }]"
            :title="phase.hops.map(hopLabel).join(' → ')"
          >
            <i class="ab-phase-glyph">{{ phase.glyph }}</i>
            <span><b>{{ phase.label }}</b><small>{{ phase.hops.length }} 节点</small></span>
            <em v-if="phase.latency_ms != null" class="num">{{ fmtLatency(phase.latency_ms) }}</em>
          </li>
        </ol>
        <p v-else class="ab-summary">{{ card.activity.summary }}</p>

        <footer class="ab-foot">
          <span>⟐ {{ card.activity.route.length }} 个事实节点</span>
          <button
            v-if="card.activity.route.length"
            type="button"
            :aria-expanded="isExpanded(card.activity.activity_id)"
            @click.stop="toggleExpanded(card.activity.activity_id)"
          >
            {{ isExpanded(card.activity.activity_id) ? '收起链路' : '展开链路' }}
            <i>{{ isExpanded(card.activity.activity_id) ? '⌃' : '⌄' }}</i>
          </button>
          <em>详情 ↗</em>
        </footer>

        <transition name="ab-detail">
          <ol v-if="isExpanded(card.activity.activity_id)" class="ab-steps" aria-label="完整事实链路" @click.stop>
            <li v-for="(hop, index) in card.activity.route" :key="hop.hop_id" :class="statusClass(hop.status)">
              <span class="ab-step-index num">{{ String(index + 1).padStart(2, '0') }}</span>
              <i class="led" :class="statusClass(hop.status)" />
              <b>{{ hopLabel(hop) }}</b>
              <small>{{ hop.stage || hop.node_type }}</small>
              <em v-if="hop.latency_ms != null" class="num">{{ fmtLatency(hop.latency_ms) }}</em>
            </li>
          </ol>
        </transition>
      </article>
    </div>
    <p v-else class="ab-idle">待命中 · 对话、Guard、设备命令和后台任务都会在这里形成独立路径</p>
  </section>
</template>

<style scoped>
.activity-board { width: 100%; padding: 10px 12px 12px; border: 1px solid var(--cy-hair); background: linear-gradient(135deg, rgba(9, 6, 24, .94), rgba(4, 3, 14, .9)); }
.ab-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 9px; }
.ab-title { display: flex; align-items: center; gap: 8px; color: var(--cy-txt); }
.ab-title > .led { width: 7px; height: 7px; }
.ab-title span { font: 800 10px/1 var(--cy-mono); letter-spacing: .08em; }
.ab-title small { margin-left: 8px; color: var(--cy-txt-dim); font: 700 8px/1 var(--cy-mono); letter-spacing: .12em; }
.ab-meta { display: flex; align-items: center; gap: 9px; font: 700 9px/1 var(--cy-mono); }
.ab-meta em { color: var(--cy-cyan); font-style: normal; }
.ab-meta b { min-width: 26px; padding: 4px 7px; border: 1px solid rgba(0, 234, 255, .22); color: var(--cy-txt); text-align: center; }
.ab-meta b i { color: var(--cy-txt-dim); font-style: normal; }
.ab-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(520px, 100%), 1fr)); gap: 8px; }
.ab-lane { --lane-tone: var(--cy-txt-dim); position: relative; min-width: 0; overflow: hidden; padding: 10px 11px 8px; border: 1px solid rgba(134, 151, 210, .14); background: linear-gradient(145deg, rgba(11, 8, 28, .92), rgba(5, 4, 16, .88)); color: inherit; cursor: pointer; transition: transform var(--dur-fast) var(--ease-out), border-color var(--dur-fast), box-shadow var(--dur-fast); }
.ab-lane.expanded { grid-column: 1 / -1; }
.ab-lane::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 2px; background: var(--lane-tone); box-shadow: 0 0 12px var(--lane-tone); opacity: .8; }
.ab-lane::after { content: ""; position: absolute; inset: 0; pointer-events: none; background: linear-gradient(105deg, transparent 25%, color-mix(in srgb, var(--lane-tone) 7%, transparent) 50%, transparent 75%); transform: translateX(-110%); opacity: 0; }
.ab-lane.ok { --lane-tone: var(--cy-green); }
.ab-lane.warn { --lane-tone: var(--cy-yellow); }
.ab-lane.bad { --lane-tone: var(--cy-mag); }
.ab-lane:hover, .ab-lane:focus-visible { transform: translateY(-2px); border-color: color-mix(in srgb, var(--lane-tone) 55%, transparent); box-shadow: 0 8px 24px rgba(0, 0, 0, .28), 0 0 18px color-mix(in srgb, var(--lane-tone) 9%, transparent); outline: none; }
.ab-lane.live::after { opacity: 1; animation: laneScan 2.8s linear infinite; }
.ab-lane-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; min-width: 0; }
.ab-identity { display: flex; align-items: flex-start; gap: 9px; min-width: 0; }
.ab-identity > span:last-child { display: grid; gap: 4px; min-width: 0; }
.ab-identity b { overflow: hidden; color: #eef3ff; font: 800 12px/1 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.ab-identity small { overflow: hidden; color: var(--cy-txt-dim); font: 600 9px/1 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.ab-kind { flex: 0 0 auto; padding: 4px 6px; border: 1px solid currentColor; font: 800 8px/1 var(--cy-mono); letter-spacing: .08em; }
.ab-kind.ok { color: var(--cy-green); } .ab-kind.warn { color: var(--cy-yellow); } .ab-kind.bad { color: var(--cy-mag); } .ab-kind.idle { color: var(--cy-txt-dim); }
.ab-status { display: inline-flex; align-items: center; gap: 5px; flex: 0 0 auto; padding: 4px 7px; border-radius: 999px; background: color-mix(in srgb, currentColor 8%, transparent); font: 800 8px/1 var(--cy-mono); letter-spacing: .05em; }
.ab-status i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; box-shadow: 0 0 7px currentColor; }
.ab-status.ok { color: var(--cy-green); } .ab-status.warn { color: var(--cy-yellow); } .ab-status.bad { color: var(--cy-mag); } .ab-status.idle { color: var(--cy-txt-dim); }
.ab-phases { display: grid; grid-template-columns: repeat(var(--phase-count), minmax(70px, 1fr)); gap: 0; min-width: 0; margin: 11px 0 7px; padding: 0; list-style: none; }
.ab-phase { position: relative; display: grid; grid-template-columns: 24px minmax(0, 1fr); align-items: center; gap: 6px; min-width: 0; padding-right: 10px; color: var(--cy-txt-dim); }
.ab-phase:not(:last-child)::after { content: ""; position: absolute; z-index: 0; top: 12px; left: 23px; right: -1px; height: 1px; background: linear-gradient(90deg, currentColor, rgba(134, 151, 210, .16)); opacity: .48; }
.ab-phase-glyph { position: relative; z-index: 1; display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid currentColor; border-radius: 50%; background: #080617; font: 700 9px/1 var(--cy-mono); font-style: normal; box-shadow: 0 0 9px color-mix(in srgb, currentColor 12%, transparent); }
.ab-phase > span { position: relative; z-index: 1; display: grid; gap: 3px; min-width: 0; background: linear-gradient(90deg, #080617 78%, transparent); }
.ab-phase b { overflow: hidden; color: currentColor; font: 800 9px/1 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.ab-phase small { color: var(--cy-txt-dim); font: 600 7px/1 var(--cy-mono); }
.ab-phase > em { position: absolute; right: 8px; bottom: -1px; color: var(--cy-txt-dim); font: 700 7px/1 var(--cy-mono); font-style: normal; }
.ab-phase.ok { color: var(--cy-green); } .ab-phase.warn { color: var(--cy-yellow); } .ab-phase.bad { color: var(--cy-mag); }
.ab-phase.current { color: var(--cy-cyan); text-shadow: 0 0 8px currentColor; }
.ab-phase.current .ab-phase-glyph { animation: phasePulse var(--dur-breath) ease-in-out infinite; }
.ab-summary { margin: 10px 0 8px; color: var(--cy-txt-dim); font: 600 10px/1.4 var(--cy-sans); }
.ab-foot { display: flex; align-items: center; gap: 10px; min-height: 18px; padding-top: 6px; border-top: 1px solid rgba(134, 151, 210, .1); font: 700 8px/1 var(--cy-mono); }
.ab-foot > span { color: var(--cy-txt-dim); }
.ab-foot button { padding: 2px 5px; border: 0; background: transparent; color: var(--cy-cyan); font: inherit; cursor: pointer; }
.ab-foot button:hover { text-shadow: 0 0 8px currentColor; }
.ab-foot button i { font-style: normal; }
.ab-foot > em { margin-left: auto; color: var(--cy-txt-dim); font-style: normal; }
.ab-steps { display: grid; gap: 3px; margin: 7px 0 0; padding: 7px 0 0; border-top: 1px dashed rgba(0, 234, 255, .16); list-style: none; cursor: default; }
.ab-lane.expanded .ab-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.ab-steps li { display: grid; grid-template-columns: 20px 7px minmax(0, 1fr) auto auto; align-items: center; gap: 7px; min-width: 0; padding: 4px 5px; background: rgba(255, 255, 255, .018); }
.ab-steps .led { width: 6px; height: 6px; }
.ab-step-index { color: rgba(134, 151, 210, .5); font: 700 7px/1 var(--cy-mono); }
.ab-steps b { overflow: hidden; color: var(--cy-txt); font: 700 9px/1.2 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.ab-steps small { color: var(--cy-txt-dim); font: 600 7px/1 var(--cy-mono); }
.ab-steps em { min-width: 38px; color: var(--cy-txt-dim); font: 700 8px/1 var(--cy-mono); font-style: normal; text-align: right; }
.ab-detail-enter-active, .ab-detail-leave-active { transition: opacity var(--dur-fast), transform var(--dur-fast); }
.ab-detail-enter-from, .ab-detail-leave-to { opacity: 0; transform: translateY(-5px); }
.ab-idle { margin: 8px 0 3px; color: var(--cy-txt-dim); font: 400 11px/1.3 var(--cy-sans); text-align: center; }
@keyframes laneScan { to { transform: translateX(110%); } }
@keyframes phasePulse { 50% { transform: scale(1.08); box-shadow: 0 0 16px currentColor; } }
@media (max-width: 720px) {
  .ab-title small { display: none; }
  .ab-cards { grid-template-columns: 1fr; }
  .ab-lane.expanded .ab-steps { grid-template-columns: 1fr; }
  .ab-phases { overflow-x: auto; grid-template-columns: repeat(var(--phase-count), minmax(92px, 1fr)); padding-bottom: 3px; }
  .ab-foot > em { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .ab-lane, .ab-lane::after, .ab-phase.current .ab-phase-glyph, .ab-detail-enter-active, .ab-detail-leave-active { animation: none; transition: none; }
}
</style>
