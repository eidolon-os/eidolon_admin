<script setup lang="ts">
import { computed } from 'vue'
import type { CompanionInspectorTab, CompanionUnit } from '../types'
import { activityKindLabel, activityStatusLabel, isActiveActivity } from '../activity'
import { devicePresenceClass, devicePresenceLabel, deviceShort, deviceType, fmtLatency, genomeStateLabel, memoryRealmStateLabel, statusClass } from '../format'

const props = defineProps<{
  companion: CompanionUnit
  tab: CompanionInspectorTab
}>()
defineEmits<{
  (e: 'change-tab', tab: CompanionInspectorTab): void
  (e: 'details', companion: CompanionUnit): void
  (e: 'close'): void
}>()

const tabs: Array<{ id: CompanionInspectorTab; label: string; glyph: string }> = [
  { id: 'overview', label: '概览', glyph: '◎' },
  { id: 'body', label: '身体', glyph: '⬡' },
  { id: 'mem', label: '记忆', glyph: '◈' },
  { id: 'act', label: '活动', glyph: '⚡' },
]

const onlineDevices = computed(() => props.companion.devices.filter((device) => device.online).length)
const activeActivities = computed(() => props.companion.activities.filter(isActiveActivity))
</script>

<template>
  <aside class="companion-inspector" aria-label="伙伴检查器">
    <header class="ci-head">
      <div>
        <span class="ci-kick"><i class="led" :class="statusClass(companion.status)" />COMPANION FOCUS</span>
        <h3>{{ companion.name }}<em v-if="companion.isPrimary">★ 主伙伴</em></h3>
        <p>{{ companion.kind }} · {{ companion.status }}</p>
      </div>
      <button class="ci-close" type="button" title="取消聚焦" aria-label="取消聚焦" @click="$emit('close')">×</button>
    </header>

    <nav class="ci-tabs" aria-label="伙伴资产">
      <button
        v-for="item in tabs"
        :key="item.id"
        type="button"
        :class="{ active: tab === item.id }"
        :aria-current="tab === item.id ? 'page' : undefined"
        @click="$emit('change-tab', item.id)"
      ><i>{{ item.glyph }}</i>{{ item.label }}</button>
    </nav>

    <div class="ci-body">
      <section v-if="tab === 'overview'" class="ci-panel">
        <div class="ci-hero">
          <span><small>身体</small><b>{{ onlineDevices }}/{{ companion.devices.length }}</b><em>在线</em></span>
          <span><small>记忆</small><b :class="companion.realm ? 'ok' : 'idle'">{{ memoryRealmStateLabel(companion.realm) }}</b></span>
          <span><small>活动</small><b :class="activeActivities.length ? 'warn' : 'idle'">{{ activeActivities.length || companion.activities.length }}</b><em>{{ activeActivities.length ? '进行中' : '记录' }}</em></span>
        </div>
        <dl class="ci-facts">
          <div><dt>基因</dt><dd :title="companion.genome || undefined">{{ genomeStateLabel(companion.genome) }}</dd></div>
          <div><dt>记忆召回</dt><dd>{{ companion.recall ?? '—' }}</dd></div>
          <div><dt>后台整理</dt><dd>{{ companion.runners || '—' }}</dd></div>
          <div><dt>写入策略</dt><dd>{{ companion.write || '—' }}</dd></div>
        </dl>
        <p class="ci-note">选择周围的身体、记忆或活动卫星，可在这里原地切换，不会打断星图浏览。</p>
      </section>

      <section v-else-if="tab === 'body'" class="ci-panel">
        <div class="ci-section-head"><span>身体 / 化身</span><b>{{ companion.devices.length }}</b></div>
        <div v-if="companion.devices.length" class="ci-list">
          <article v-for="device in companion.devices" :key="device.device_id" class="ci-row">
            <i class="led" :class="devicePresenceClass(device)" />
            <span><b>{{ deviceType(device) }}</b><small :title="device.device_id">{{ deviceShort(device) }}</small></span>
            <em :class="devicePresenceClass(device)">{{ devicePresenceLabel(device) }}</em>
          </article>
        </div>
        <p v-else class="ci-empty">尚未绑定身体</p>
      </section>

      <section v-else-if="tab === 'mem'" class="ci-panel">
        <div class="ci-section-head"><span>长期记忆</span><b :class="companion.realm ? 'ok' : 'idle'">{{ memoryRealmStateLabel(companion.realm) }}</b></div>
        <div class="ci-memory-core" :class="{ active: companion.realm }"><i>◈</i><span>{{ companion.realm ? '伙伴记忆域已连接' : '尚未开通记忆空间' }}</span></div>
        <dl class="ci-facts">
          <div><dt>召回命中</dt><dd>{{ companion.recall ?? '—' }}</dd></div>
          <div><dt>后台整理</dt><dd>{{ companion.runners || '—' }}</dd></div>
          <div><dt>写入策略</dt><dd>{{ companion.write || '—' }}</dd></div>
          <div><dt>当前记忆域</dt><dd :title="companion.realm || undefined">{{ memoryRealmStateLabel(companion.realm) }}</dd></div>
        </dl>
      </section>

      <section v-else class="ci-panel">
        <div class="ci-section-head"><span>最近活动</span><b>{{ companion.activities.length }}</b></div>
        <div v-if="companion.activities.length" class="ci-list">
          <article v-for="activity in companion.activities.slice(0, 5)" :key="activity.activity_id" class="ci-row activity">
            <i class="led" :class="statusClass(activity.status)" />
            <span><b>{{ activityKindLabel(activity.kind) }}</b><small>{{ activity.summary }}</small></span>
            <em :class="statusClass(activity.status)">{{ activityStatusLabel(activity.status) }}</em>
          </article>
        </div>
        <p v-else class="ci-empty">当前没有活动记录</p>
        <p v-if="companion.activeVoiceTurn" class="ci-live"><i class="led warn" />当前对话 · {{ fmtLatency(companion.activeVoiceTurn.latency_ms) }}</p>
      </section>
    </div>

    <footer class="ci-foot">
      <span>只读聚焦 · ESC 退出</span>
      <button type="button" @click="$emit('details', companion)">完整详情 <i>↗</i></button>
    </footer>
  </aside>
</template>

<style scoped>
.companion-inspector { position: absolute; z-index: 12; top: 8px; right: 0; bottom: 8px; display: flex; flex-direction: column; width: 322px; min-height: 300px; overflow: hidden; border: 1px solid rgba(0, 234, 255, .38); background: linear-gradient(155deg, rgba(11, 8, 28, .97), rgba(5, 3, 16, .96)); box-shadow: -12px 0 34px rgba(0, 0, 0, .42), 0 0 24px rgba(0, 234, 255, .09); clip-path: polygon(0 0, calc(100% - 18px) 0, 100% 18px, 100% 100%, 18px 100%, 0 calc(100% - 18px)); }
.companion-inspector::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: linear-gradient(120deg, transparent 28%, rgba(0, 234, 255, .04) 50%, transparent 72%); animation: inspectorScan 4.2s linear infinite; }
.ci-head { position: relative; display: flex; justify-content: space-between; gap: 10px; padding: 15px 15px 11px; border-bottom: 1px solid rgba(0, 234, 255, .16); }
.ci-kick { display: inline-flex; align-items: center; gap: 6px; color: var(--cy-cyan); font: 800 8px/1 var(--cy-mono); letter-spacing: .12em; }
.ci-kick .led { width: 6px; height: 6px; }
.ci-head h3 { display: flex; align-items: baseline; gap: 7px; margin: 8px 0 4px; color: #fff; font: 850 20px/1 var(--cy-sans); }
.ci-head h3 em { color: var(--cy-sun); font: 700 8px/1 var(--cy-mono); font-style: normal; }
.ci-head p { margin: 0; color: var(--cy-txt-dim); font: 600 9px/1 var(--cy-mono); }
.ci-close { align-self: flex-start; width: 28px; height: 28px; border: 1px solid rgba(0, 234, 255, .26); background: rgba(0, 234, 255, .05); color: var(--cy-cyan); font: 400 20px/1 var(--cy-sans); cursor: pointer; }
.ci-close:hover { background: rgba(0, 234, 255, .14); }
.ci-tabs { position: relative; display: grid; grid-template-columns: repeat(4, 1fr); padding: 0 10px; border-bottom: 1px solid rgba(0, 234, 255, .13); }
.ci-tabs button { display: flex; align-items: center; justify-content: center; gap: 4px; padding: 9px 3px 8px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--cy-txt-dim); font: 700 8px/1 var(--cy-mono); cursor: pointer; }
.ci-tabs button i { font-style: normal; }
.ci-tabs button:hover { color: var(--cy-txt); }
.ci-tabs button.active { border-color: var(--cy-cyan); color: var(--cy-cyan); text-shadow: 0 0 8px currentColor; }
.ci-body { position: relative; flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 12px 14px; }
.ci-panel { display: grid; gap: 10px; }
.ci-hero { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.ci-hero > span { display: grid; min-height: 66px; place-content: center; gap: 5px; border: 1px solid rgba(134, 151, 210, .13); background: rgba(255, 255, 255, .018); text-align: center; }
.ci-hero small, .ci-hero em { color: var(--cy-txt-dim); font: 600 7px/1 var(--cy-mono); font-style: normal; }
.ci-hero b { color: #fff; font: 900 16px/1 var(--cy-mono); }
.ci-hero b.ok, .ci-facts dd.ok { color: var(--cy-green); } .ci-hero b.warn { color: var(--cy-yellow); } .ci-hero b.idle { color: var(--cy-txt-dim); }
.ci-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; margin: 0; background: rgba(0, 234, 255, .1); border: 1px solid rgba(0, 234, 255, .1); }
.ci-facts div { display: grid; gap: 6px; padding: 9px 10px; background: rgba(7, 5, 18, .96); }
.ci-facts dt { color: var(--cy-txt-dim); font: 600 8px/1 var(--cy-mono); }
.ci-facts dd { margin: 0; overflow: hidden; color: var(--cy-txt); font: 800 10px/1 var(--cy-mono); text-overflow: ellipsis; white-space: nowrap; }
.ci-note { margin: 0; padding: 8px 9px; border: 1px dashed rgba(0, 234, 255, .18); color: var(--cy-txt-dim); font: 600 8px/1.5 var(--cy-sans); }
.ci-section-head { display: flex; align-items: center; justify-content: space-between; padding-bottom: 7px; border-bottom: 1px solid rgba(0, 234, 255, .15); }
.ci-section-head span { color: var(--cy-txt); font: 800 9px/1 var(--cy-mono); letter-spacing: .06em; }
.ci-section-head b { color: var(--cy-cyan); font: 900 11px/1 var(--cy-mono); }
.ci-section-head b.ok { color: var(--cy-green); } .ci-section-head b.idle { color: var(--cy-txt-dim); }
.ci-list { display: grid; gap: 5px; }
.ci-row { display: grid; grid-template-columns: 7px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 8px 9px; border: 1px solid rgba(134, 151, 210, .1); background: rgba(255, 255, 255, .018); }
.ci-row .led { width: 6px; height: 6px; }
.ci-row > span { display: grid; gap: 4px; min-width: 0; }
.ci-row b { overflow: hidden; color: var(--cy-txt); font: 750 10px/1 var(--cy-sans); text-overflow: ellipsis; white-space: nowrap; }
.ci-row small { overflow: hidden; color: var(--cy-txt-dim); font: 600 8px/1 var(--cy-mono); text-overflow: ellipsis; white-space: nowrap; }
.ci-row em { color: var(--cy-txt-dim); font: 700 8px/1 var(--cy-mono); font-style: normal; }
.ci-row em.ok { color: var(--cy-green); } .ci-row em.warn { color: var(--cy-yellow); } .ci-row em.bad { color: var(--cy-mag); }
.ci-memory-core { display: flex; align-items: center; gap: 10px; padding: 13px; border: 1px solid rgba(247, 255, 74, .16); background: radial-gradient(circle at 16% 50%, rgba(247, 255, 74, .09), transparent 45%); color: var(--cy-txt-dim); }
.ci-memory-core i { color: var(--cy-yellow); font-size: 24px; font-style: normal; text-shadow: 0 0 12px currentColor; }
.ci-memory-core span { font: 700 9px/1.3 var(--cy-sans); }
.ci-memory-core.active { color: var(--cy-txt); border-color: rgba(247, 255, 74, .3); }
.ci-empty { margin: 8px 0; color: var(--cy-txt-dim); font: 600 10px/1.4 var(--cy-sans); text-align: center; }
.ci-live { display: flex; align-items: center; gap: 7px; margin: 0; padding: 8px; border: 1px solid rgba(247, 255, 74, .18); color: var(--cy-yellow); font: 700 8px/1 var(--cy-mono); }
.ci-live .led { width: 6px; height: 6px; }
.ci-foot { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 9px 12px 11px; border-top: 1px solid rgba(0, 234, 255, .14); }
.ci-foot span { color: var(--cy-txt-dim); font: 600 7px/1 var(--cy-mono); }
.ci-foot button { padding: 6px 9px; border: 1px solid rgba(0, 234, 255, .38); background: rgba(0, 234, 255, .08); color: var(--cy-cyan); font: 800 8px/1 var(--cy-mono); cursor: pointer; }
.ci-foot button:hover { background: rgba(0, 234, 255, .16); box-shadow: 0 0 12px rgba(0, 234, 255, .12); }
.ci-foot button i { font-style: normal; }
@keyframes inspectorScan { to { transform: translateX(100%); } }
@media (prefers-reduced-motion: reduce) { .companion-inspector::before { animation: none; } }
</style>
