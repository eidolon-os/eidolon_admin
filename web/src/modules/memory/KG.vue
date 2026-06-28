<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  addKgTriple,
  getKgPredicates,
  getKgStats,
  getKgTimeline,
  invalidateKg,
  type KgPredicates,
  type KgStats,
  type KgTripleOut,
} from '@/api/memory'
import { useMemoryRealmStore } from '@/stores/memoryRealm'
import MemoryPageShell from './components/MemoryPageShell.vue'

const store = useMemoryRealmStore()
const stats = ref<KgStats | null>(null)
const predicates = ref<KgPredicates>({ predicates: [], sensitive: [] })
const timeline = ref<KgTripleOut[]>([])
const loading = ref(false)

const filterEntity = ref('')
const filterLimit = ref(60)
const filterSensitive = ref(false)

const writeForm = ref({
  subject: '', predicate: '', object: '', confidence: 1.0,
  valid_from: '', valid_to: '',
})
const invalidateForm = ref({ subject: '', predicate: '', object: '' })
const submittingWrite = ref(false)
const submittingInvalidate = ref(false)

async function loadAll() {
  if (!store.currentId) return
  loading.value = true
  try {
    const [s, p, t] = await Promise.all([
      getKgStats(store.currentId),
      getKgPredicates(store.currentId),
      getKgTimeline(store.currentId, filterEntity.value || undefined, undefined, undefined, filterLimit.value, filterSensitive.value),
    ])
    stats.value = s
    predicates.value = p
    timeline.value = t.triples
  } finally {
    loading.value = false
  }
}

async function reloadTimeline() {
  if (!store.currentId) return
  loading.value = true
  try {
    const t = await getKgTimeline(store.currentId, filterEntity.value || undefined, undefined, undefined, filterLimit.value, filterSensitive.value)
    timeline.value = t.triples
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
watch(() => store.currentId, loadAll)

async function submitWrite() {
  if (!writeForm.value.subject || !writeForm.value.predicate || !writeForm.value.object) return
  submittingWrite.value = true
  try {
    const r = await addKgTriple({
      memory_realm_id: store.currentId,
      ...writeForm.value,
      valid_from: writeForm.value.valid_from || undefined,
      valid_to: writeForm.value.valid_to || undefined,
    })
    ElMessage.success(`triple ${r.status}: id=${r.triple_id || '(pending)'}`)
    writeForm.value = { subject: '', predicate: '', object: '', confidence: 1.0, valid_from: '', valid_to: '' }
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    submittingWrite.value = false
  }
}

async function submitInvalidate() {
  if (!invalidateForm.value.subject || !invalidateForm.value.predicate || !invalidateForm.value.object) return
  submittingInvalidate.value = true
  try {
    const r = await invalidateKg({ memory_realm_id: store.currentId, ...invalidateForm.value })
    ElMessage.success(`invalidate ${r.status}`)
    invalidateForm.value = { subject: '', predicate: '', object: '' }
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    submittingInvalidate.value = false
  }
}
</script>

<template>
  <MemoryPageShell title="Knowledge Graph">
    <template #default>
      <!-- Stats strip -->
      <div class="stats">
        <div class="stat-card">
          <div class="stat-label">Entities</div>
          <div class="stat-val">{{ stats?.entities ?? '-' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Triples (total)</div>
          <div class="stat-val">{{ stats?.triples_total ?? '-' }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">Active</div>
          <div class="stat-val">{{ stats?.triples_active ?? '-' }}</div>
        </div>
        <div class="stat-card warn">
          <div class="stat-label">Invalidated</div>
          <div class="stat-val">{{ stats?.triples_invalidated ?? '-' }}</div>
        </div>
      </div>

      <!-- Write / Invalidate -->
      <el-collapse style="margin-top: 16px">
        <el-collapse-item title="写 / 失效" name="write">
          <div class="write-grid">
            <div>
              <h4>Add triple</h4>
              <el-form label-position="top">
                <el-form-item label="Subject"><el-input v-model="writeForm.subject" /></el-form-item>
                <el-form-item label="Predicate">
                  <el-select v-model="writeForm.predicate" filterable allow-create placeholder="predicate">
                    <el-option v-for="p in predicates.predicates" :key="p" :value="p" :label="p" />
                  </el-select>
                </el-form-item>
                <el-form-item label="Object"><el-input v-model="writeForm.object" /></el-form-item>
                <el-form-item label="Confidence">
                  <el-input-number v-model="writeForm.confidence" :min="0" :max="1" :step="0.05" size="small" />
                </el-form-item>
                <el-form-item label="Valid from / to">
                  <el-input v-model="writeForm.valid_from" placeholder="from (ISO)" style="width: 49%; margin-right: 2%" />
                  <el-input v-model="writeForm.valid_to" placeholder="to (optional)" style="width: 49%" />
                </el-form-item>
                <el-button type="primary" :loading="submittingWrite" @click="submitWrite">Add triple</el-button>
              </el-form>
            </div>
            <div>
              <h4>Invalidate triple</h4>
              <el-form label-position="top">
                <el-form-item label="Subject"><el-input v-model="invalidateForm.subject" /></el-form-item>
                <el-form-item label="Predicate">
                  <el-select v-model="invalidateForm.predicate" filterable placeholder="predicate">
                    <el-option v-for="p in predicates.predicates" :key="p" :value="p" :label="p" />
                  </el-select>
                </el-form-item>
                <el-form-item label="Object"><el-input v-model="invalidateForm.object" /></el-form-item>
                <el-button type="warning" :loading="submittingInvalidate" @click="submitInvalidate">Invalidate</el-button>
              </el-form>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>

      <!-- Timeline -->
      <el-card style="margin-top: 16px">
        <template #header>
          <div class="bar">
            <span>Timeline ({{ timeline.length }})</span>
            <div class="filter-bar">
              <el-input v-model="filterEntity" placeholder="entity 过滤" size="small" style="width: 200px" clearable />
              <el-input-number v-model="filterLimit" :min="1" :max="500" size="small" />
              <el-checkbox v-model="filterSensitive">包含 sensitive</el-checkbox>
              <el-button size="small" :loading="loading" @click="reloadTimeline">应用</el-button>
            </div>
          </div>
        </template>
        <el-table :data="timeline" v-loading="loading" stripe size="small">
          <el-table-column label="Subject" prop="subject" width="200" />
          <el-table-column label="Predicate" prop="predicate" width="160" />
          <el-table-column label="Object" prop="object" min-width="240" />
          <el-table-column label="Valid from" prop="valid_from" width="180" />
          <el-table-column label="Valid to" width="180">
            <template #default="{ row }">
              <span v-if="row.valid_to" class="ended">{{ row.valid_to }}</span>
              <el-tag v-else type="success" size="small" effect="plain">current</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Conf" width="80">
            <template #default="{ row }">{{ row.confidence?.toFixed(2) || '-' }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.stats {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
}
.stat-card {
  background: var(--eid-bg-panel);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  padding: 14px 16px;
}
.stat-card.success { border-left: 3px solid var(--eid-success); }
.stat-card.warn { border-left: 3px solid var(--eid-warning); }
.stat-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--eid-text-muted);
}
.stat-val { font-size: 22px; font-weight: 600; margin-top: 4px; }
.write-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.write-grid h4 { margin: 0 0 8px 0; color: var(--eid-text-secondary); font-size: 13px; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.filter-bar { display: flex; gap: 8px; align-items: center; }
.ended { color: var(--eid-text-muted); text-decoration: line-through; }
</style>
