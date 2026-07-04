<script setup lang="ts">
// Region 4 · Memory Evidence Lane: recall/write/runner facts, with an on-demand
// Palace graph (reuses the existing vis-network PalaceGraph + /memory/graph API).
import { ref } from 'vue'
import { getPalaceGraph, type GraphSnapshot } from '@/api/memory'
import PalaceGraph from '@/modules/memory/components/PalaceGraph.vue'
import type { RuntimeMemory } from '@/api/missionControl'

const props = defineProps<{ memory: RuntimeMemory | undefined }>()

const expanded = ref(false)
const loading = ref(false)
const graph = ref<GraphSnapshot | null>(null)
const graphError = ref('')

async function toggle() {
  expanded.value = !expanded.value
  const realm = props.memory?.active_realm_id
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
      <span class="lane-cap"><i class="led" :class="memory && memory.last_recall_hits > 0 ? 'ok' : 'idle'" />记忆证据 · MEMORY</span>
      <button v-if="memory?.active_realm_id" class="graph-btn" @click="toggle">{{ expanded ? '收起' : '宫殿图' }}</button>
    </div>
    <div class="mem-rows">
      <div><dt>召回命中</dt><dd class="num" :class="{ ok: (memory?.last_recall_hits ?? 0) > 0 }">{{ memory?.last_recall_hits ?? 0 }}</dd></div>
      <div><dt>写入</dt><dd>{{ memory?.last_write_disposition || (memory?.fanout_allowed ? 'ALLOW' : 'HOLD') }}</dd></div>
      <div><dt>后台整理</dt><dd class="num">{{ memory?.runners_online ?? 0 }}/{{ memory?.runners_total ?? 0 }}</dd></div>
      <div><dt>记忆空间</dt><dd class="num">{{ memory?.realms_total ?? 0 }}</dd></div>
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
.mem-rows dd { margin: 0; color: var(--cy-txt); }
.mem-rows dd.ok { color: var(--cy-green); }
.mem-graph { height: 240px; margin-top: 6px; border: 1px solid var(--cy-hair); overflow: hidden; }
/* Fit the fixed-600px PalaceGraph into this compact box — only the container,
   not every nested div (that would break vis-network's internal layout). */
.mem-graph :deep(.palace-graph) { height: 100%; min-height: 0; }
.mem-err { margin: 8px; font-size: 11px; color: var(--cy-mag); }
.lane-idle { margin: 8px; font: 400 11px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
