<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getKnowledgeGraph, getPalaceGraph, type GraphSnapshot } from '@/api/memory'
import { useMemoryUserStore } from '@/stores/memoryUser'
import MemoryPageShell from './components/MemoryPageShell.vue'
import PalaceGraph from './components/PalaceGraph.vue'

const store = useMemoryUserStore()
const kind = ref<'knowledge' | 'palace'>('knowledge')
const entity = ref('')
const currentOnly = ref(true)
const includeSensitive = ref(false)
const maxTriples = ref(400)
const snapshot = ref<GraphSnapshot | null>(null)
const loading = ref(false)

async function load() {
  if (!store.currentId) return
  loading.value = true
  try {
    if (kind.value === 'knowledge') {
      snapshot.value = await getKnowledgeGraph(
        store.currentId, maxTriples.value, currentOnly.value, entity.value || undefined,
        includeSensitive.value,
      )
    } else {
      snapshot.value = await getPalaceGraph(store.currentId)
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => store.currentId, load)
watch(kind, load)
</script>

<template>
  <MemoryPageShell title="Graph">
    <template #default>
      <el-card>
        <template #header>
          <div class="bar">
            <el-radio-group v-model="kind" size="small">
              <el-radio-button label="knowledge">Knowledge Graph</el-radio-button>
              <el-radio-button label="palace">Palace Graph</el-radio-button>
            </el-radio-group>
            <div class="filter-bar">
              <template v-if="kind === 'knowledge'">
                <el-input v-model="entity" placeholder="entity 过滤" size="small" clearable style="width: 200px" />
                <el-input-number v-model="maxTriples" :min="1" :max="2000" size="small" />
                <el-checkbox v-model="currentOnly">仅当前</el-checkbox>
                <el-checkbox v-model="includeSensitive">含 sensitive</el-checkbox>
              </template>
              <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
            </div>
          </div>
        </template>

        <div class="meta">
          <span v-if="snapshot">
            {{ snapshot.nodes.length }} nodes · {{ snapshot.edges.length }} edges
            <span v-if="snapshot.reason" class="hint">· {{ snapshot.reason }}</span>
            <el-tag v-if="snapshot.capped" type="warning" size="small" effect="plain" style="margin-left: 8px">
              capped
            </el-tag>
          </span>
        </div>

        <!-- Legend (颜色含义) -->
        <div v-if="snapshot && snapshot.nodes.length" class="legend">
          <template v-if="kind === 'knowledge'">
            <span class="lg"><span class="dot dot-self" />self</span>
            <span class="lg"><span class="dot dot-person" />person</span>
            <span class="lg"><span class="dot dot-pet" />pet</span>
            <span class="lg"><span class="dot dot-place" />place</span>
            <span class="lg"><span class="dot dot-project" />project</span>
            <span class="lg"><span class="dot dot-other" />other</span>
            <span class="lg edge-dashed">虚线 = 已失效（valid_to set）</span>
          </template>
          <template v-else>
            <span class="lg"><span class="dot dot-room" />room（tunnel：跨 wing 共享的 room）</span>
            <span class="lg">完整 4 层结构请去 <b>Hierarchy</b> 页</span>
          </template>
        </div>

        <PalaceGraph :snapshot="snapshot" />
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.filter-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.meta { font-size: 12px; color: var(--eid-text-muted); padding: 8px 0; }
.hint { color: var(--eid-text-muted); margin-left: 6px; }

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding: 6px 0 14px 0;
  font-size: 11px;
  color: var(--eid-text-secondary);
}
.lg { display: inline-flex; align-items: center; gap: 6px; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
.dot-self    { background: #6366f1; }
.dot-person  { background: #a78bfa; }
.dot-pet     { background: #f472b6; }
.dot-place   { background: #fbbf24; }
.dot-project { background: #34d399; }
.dot-other   { background: #9ca3af; }
.dot-room    { background: #60a5fa; }
.edge-dashed::before {
  content: '';
  display: inline-block;
  width: 16px;
  height: 0;
  border-top: 1px dashed var(--eid-text-muted);
  margin-right: 6px;
  vertical-align: middle;
}
</style>
