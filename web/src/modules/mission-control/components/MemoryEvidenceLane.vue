<script setup lang="ts">
// Region 4 · Memory Evidence Lane: recall/write/runner facts, with an on-demand
// Palace graph (reuses the existing vis-network PalaceGraph + /memory/graph API).
// Owner scope shows the active-realm rollup; when a companion is focused it
// shows that companion's realm.
import { computed, ref } from 'vue'
import { getPalaceGraph, type GraphSnapshot } from '@/api/memory'
import PalaceGraph from '@/modules/memory/components/PalaceGraph.vue'
import type { RuntimeMemory } from '@/api/missionControl'
import type { CompanionUnit } from '../types'
import { compactId } from '../format'

const props = defineProps<{ memory: RuntimeMemory | undefined; companion?: CompanionUnit | null }>()

// A single view-model regardless of scope, so the template stays flat.
const view = computed(() => {
  const c = props.companion
  if (c) {
    const recall = c.isActiveRealm ? c.recall ?? 0 : null
    return {
      recallText: recall == null ? '—' : String(recall),
      hasHit: recall != null && recall > 0,
      write: c.write || '—',
      runners: c.runners || '—',
      realmText: compactId(c.realm) || '未开通',
      realmFull: c.realm || '',
      graphRealm: c.realm || '',
    }
  }
  const m = props.memory
  return {
    recallText: String(m?.last_recall_hits ?? 0),
    hasHit: (m?.last_recall_hits ?? 0) > 0,
    write: m?.last_write_disposition || (m?.fanout_allowed ? 'ALLOW' : 'HOLD'),
    runners: `${m?.runners_online ?? 0}/${m?.runners_total ?? 0}`,
    realmText: `${m?.realms_total ?? 0} 个`,
    realmFull: '',
    graphRealm: m?.active_realm_id || '',
  }
})

const expanded = ref(false)
const loading = ref(false)
const graph = ref<GraphSnapshot | null>(null)
const graphError = ref('')

async function toggle() {
  expanded.value = !expanded.value
  const realm = view.value.graphRealm
  if (expanded.value && !graph.value && realm) {
    loading.value = true
    try {
      graph.value = await getPalaceGraph(realm)
    } catch (e: any) {
      graphError.value = e?.response?.data?.detail || e?.message || '记忆宫殿不可用'
    } finally {
      loading.value = false
    }
  }
}
</script>

<template>
  <div class="lane mem-lane">
    <div class="lane-head">
      <span class="lane-cap"><i class="led" :class="view.hasHit ? 'ok' : 'idle'" />记忆证据 · MEMORY</span>
      <button v-if="view.graphRealm" class="graph-btn" @click="toggle">{{ expanded ? '收起' : '宫殿图' }}</button>
    </div>
    <div class="mem-rows">
      <div><dt>召回命中</dt><dd class="num" :class="{ ok: view.hasHit }">{{ view.recallText }}</dd></div>
      <div><dt>写入</dt><dd>{{ view.write }}</dd></div>
      <div><dt>后台整理</dt><dd class="num">{{ view.runners }}</dd></div>
      <div><dt>记忆空间</dt><dd class="num" :title="view.realmFull || undefined">{{ view.realmText }}</dd></div>
    </div>
    <div v-if="expanded" class="mem-graph" v-loading="loading">
      <PalaceGraph v-if="graph" :snapshot="graph" />
      <p v-else-if="graphError" class="mem-err">{{ graphError }}</p>
      <p v-else-if="!loading" class="lane-idle">无记忆宫殿数据</p>
    </div>
  </div>
</template>

<style scoped>
.lane { display: flex; flex-direction: column; gap: 6px; padding: 8px 14px; border: 1px solid var(--cy-hair); background: var(--cy-panel); }
.lane-head { display: flex; align-items: center; justify-content: space-between; }
.lane-cap { display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-txt-dim); }
.lane-cap .led { width: 6px; height: 6px; }
.graph-btn { font: 700 9px/1 var(--cy-mono); color: var(--cy-cyan); background: rgba(0, 234, 255, 0.08); border: 1px solid rgba(0, 234, 255, 0.3); padding: 3px 7px; cursor: pointer; }
.graph-btn:hover { background: rgba(0, 234, 255, 0.18); }
.mem-rows { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 12px; }
.mem-rows > div { display: flex; justify-content: space-between; gap: 8px; font: 600 10.5px/1.4 var(--cy-mono); }
.mem-rows dt { color: var(--cy-txt-dim); }
.mem-rows dd { margin: 0; color: var(--cy-txt); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mem-rows dd.ok { color: var(--cy-green); }
.mem-graph { height: 240px; margin-top: 6px; border: 1px solid var(--cy-hair); overflow: hidden; }
/* Fit the fixed-600px PalaceGraph into this compact box — only the container,
   not every nested div (that would break vis-network's internal layout). */
.mem-graph :deep(.palace-graph) { height: 100%; min-height: 0; }
.mem-err { margin: 8px; font-size: 11px; color: var(--cy-mag); }
.lane-idle { margin: 8px; font: 400 11px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
