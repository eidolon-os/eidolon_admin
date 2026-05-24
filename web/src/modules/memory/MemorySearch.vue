<script setup lang="ts">
import { ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { searchMemories, type MemoryRecord } from '@/api/memory'
import { useMemoryUserStore } from '@/stores/memoryUser'
import MemoryPageShell from './components/MemoryPageShell.vue'

const store = useMemoryUserStore()
const query = ref('')
const topK = ref(8)
const wing = ref('')
const room = ref('')
const loading = ref(false)
const records = ref<MemoryRecord[]>([])
const lastQuery = ref('')

async function search() {
  if (!store.currentId || !query.value.trim()) return
  loading.value = true
  try {
    const data = await searchMemories(
      store.currentId, query.value, topK.value, wing.value || undefined, room.value || undefined,
    )
    records.value = (data.records || []).sort(
      (a, b) => (b.metadata?.similarity || 0) - (a.metadata?.similarity || 0),
    )
    lastQuery.value = query.value
  } finally {
    loading.value = false
  }
}

watch(() => store.currentId, () => {
  records.value = []
  lastQuery.value = ''
})

function shortText(s: string | undefined, max = 240): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max)}…` : s
}

function similarity(r: MemoryRecord): number {
  return Number(r.metadata?.similarity || 0)
}
</script>

<template>
  <MemoryPageShell title="Memory Search">
    <template #default>
      <el-card>
        <el-form inline @submit.prevent="search">
          <el-form-item label="Query" style="flex: 1">
            <el-input
              v-model="query" placeholder="语义查询..."
              style="width: 360px"
              :prefix-icon="Search"
              @keyup.enter="search"
            />
          </el-form-item>
          <el-form-item label="Top K">
            <el-input-number v-model="topK" :min="1" :max="100" size="small" />
          </el-form-item>
          <el-form-item label="Wing">
            <el-input v-model="wing" placeholder="可选" style="width: 140px" />
          </el-form-item>
          <el-form-item label="Room">
            <el-input v-model="room" placeholder="可选" style="width: 140px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">搜索</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card v-if="lastQuery" style="margin-top: 16px">
        <template #header>
          <div class="bar">
            <span>"{{ lastQuery }}" — {{ records.length }} 条结果</span>
          </div>
        </template>
        <el-table :data="records" v-loading="loading" stripe>
          <el-table-column label="Key" width="220">
            <template #default="{ row }"><span class="mono">{{ row.key }}</span></template>
          </el-table-column>
          <el-table-column label="Similarity" width="160">
            <template #default="{ row }">
              <div class="sim-cell">
                <span class="sim-val">{{ similarity(row).toFixed(4) }}</span>
                <el-progress
                  :percentage="Math.round(similarity(row) * 100)"
                  :show-text="false"
                  :stroke-width="4"
                  style="flex: 1"
                />
              </div>
            </template>
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
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.sim-cell { display: flex; align-items: center; gap: 8px; }
.sim-val { font-family: var(--eid-font-mono); font-size: 12px; min-width: 50px; }
</style>
