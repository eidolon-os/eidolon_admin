<script setup lang="ts">
import { ref } from 'vue'
import { recall, type KgTripleOut, type MemoryRecord, type RecallResponse } from '@/api/memory'
import { useMemoryRealmStore } from '@/stores/memoryRealm'
import MemoryPageShell from './components/MemoryPageShell.vue'

const store = useMemoryRealmStore()
const query = ref('')
const topK = ref(5)
const voice = ref(false)
const includeKgMode = ref<'auto' | 'on' | 'off'>('auto')
const sensitive = ref(false)
const loading = ref(false)
const elapsedMs = ref(0)
const result = ref<RecallResponse | null>(null)

async function run() {
  if (!store.currentId || !query.value.trim()) return
  loading.value = true
  const t0 = performance.now()
  try {
    const include_kg = includeKgMode.value === 'auto' ? null
      : includeKgMode.value === 'on' ? true : false
    result.value = await recall(store.currentId, {
      query: query.value,
      top_k: topK.value,
      voice: voice.value,
      include_kg,
      include_sensitive_kg: sensitive.value,
    })
    elapsedMs.value = Math.round(performance.now() - t0)
  } finally {
    loading.value = false
  }
}

function tripleClass(t: KgTripleOut): string {
  return t.valid_to ? 'triple-ended' : ''
}
function shortText(s: string | undefined, max = 240): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max)}…` : s
}
function recordSim(r: MemoryRecord): number {
  return Number(r.metadata?.similarity || 0)
}
</script>

<template>
  <MemoryPageShell title="Recall (向量 + KG 融合)">
    <template #default>
      <el-card>
        <el-form @submit.prevent="run">
          <el-form-item label="Query">
            <el-input v-model="query" type="textarea" :rows="2"
              placeholder="比如：alice 喜欢什么？"
              @keydown.ctrl.enter="run"
            />
          </el-form-item>
          <div class="opt-row">
            <el-form-item label="Top K">
              <el-input-number v-model="topK" :min="1" :max="50" size="small" />
            </el-form-item>
            <el-form-item label="Voice">
              <el-switch v-model="voice" />
            </el-form-item>
            <el-form-item label="Include KG">
              <el-radio-group v-model="includeKgMode" size="small">
                <el-radio-button label="auto">auto</el-radio-button>
                <el-radio-button label="on">on</el-radio-button>
                <el-radio-button label="off">off</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="Sensitive KG">
              <el-switch v-model="sensitive" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="loading" @click="run">召回</el-button>
            </el-form-item>
          </div>
        </el-form>
      </el-card>

      <template v-if="result">
        <el-card style="margin-top: 16px">
          <template #header>
            <div class="bar">
              <span>Context</span>
              <span class="hint">耗时 {{ elapsedMs }} ms</span>
            </div>
          </template>
          <pre class="ctx">{{ result.context || '(空)' }}</pre>
        </el-card>

        <el-card style="margin-top: 16px">
          <template #header>
            <span>KG Triples ({{ result.kg_triples.length }})</span>
          </template>
          <el-table v-if="result.kg_triples.length" :data="result.kg_triples" size="small" stripe>
            <el-table-column label="Subject" prop="subject" width="180" />
            <el-table-column label="Predicate" prop="predicate" width="160" />
            <el-table-column label="Object" prop="object" />
            <el-table-column label="Valid from" prop="valid_from" width="180" />
            <el-table-column label="Valid to" width="180">
              <template #default="{ row }">
                <span :class="tripleClass(row)">{{ row.valid_to || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="无" :image-size="60" />
        </el-card>

        <el-card style="margin-top: 16px">
          <template #header>
            <span>Records ({{ result.records.length }})</span>
          </template>
          <el-table v-if="result.records.length" :data="result.records" size="small" stripe>
            <el-table-column label="Key" width="200">
              <template #default="{ row }"><span class="mono">{{ row.key }}</span></template>
            </el-table-column>
            <el-table-column label="Sim" width="80">
              <template #default="{ row }">{{ recordSim(row).toFixed(3) }}</template>
            </el-table-column>
            <el-table-column label="Value" min-width="320">
              <template #default="{ row }">{{ shortText(row.value) }}</template>
            </el-table-column>
            <el-table-column label="Wing / Room" width="200">
              <template #default="{ row }">
                <span v-if="row.wing || row.room" class="mono">{{ row.wing }} / {{ row.room }}</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="无" :image-size="60" />
        </el-card>
      </template>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.opt-row { display: flex; gap: 12px; flex-wrap: wrap; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.hint { font-size: 12px; color: var(--eid-text-muted); }
.ctx {
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  padding: 16px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12.5px;
  line-height: 1.6;
  max-height: 50vh;
  overflow: auto;
  margin: 0;
}
.triple-ended { color: var(--eid-text-muted); text-decoration: line-through; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
</style>
