<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { listMemories, type MemoryRecord } from '@/api/memory'
import { useMemoryRealmStore } from '@/stores/memoryRealm'
import MemoryPageShell from './components/MemoryPageShell.vue'

const store = useMemoryRealmStore()
const records = ref<MemoryRecord[]>([])
const totalHint = ref(0)
const loading = ref(false)
const limit = ref(50)
const offset = ref(0)
const includePrivate = ref(false)

async function load() {
  if (!store.currentId) return
  loading.value = true
  try {
    const data = await listMemories(store.currentId, limit.value, offset.value, includePrivate.value)
    records.value = data.records
    totalHint.value = data.total_hint
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => store.currentId, () => { offset.value = 0; load() })

function nextPage() {
  if (records.value.length < limit.value) return
  offset.value += limit.value
  load()
}
function prevPage() {
  offset.value = Math.max(0, offset.value - limit.value)
  load()
}

function shortText(s: string | undefined, max = 200): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max)}…` : s
}
</script>

<template>
  <MemoryPageShell title="Memories">
    <template #default>
      <el-card>
        <template #header>
          <div class="bar">
            <el-form inline>
              <el-form-item label="Limit">
                <el-input-number v-model="limit" :min="1" :max="500" size="small" />
              </el-form-item>
              <el-form-item label="Include private">
                <el-switch v-model="includePrivate" @change="load" />
              </el-form-item>
            </el-form>
            <div class="actions">
              <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
            </div>
          </div>
        </template>

        <el-table :data="records" v-loading="loading" stripe>
          <el-table-column label="Key" width="220">
            <template #default="{ row }"><span class="mono">{{ row.key }}</span></template>
          </el-table-column>
          <el-table-column label="Value" min-width="320">
            <template #default="{ row }">{{ shortText(row.value) }}</template>
          </el-table-column>
          <el-table-column label="Wing" width="160" prop="wing" />
          <el-table-column label="Room" width="160" prop="room" />
        </el-table>

        <div class="pager">
          <div class="hint">offset {{ offset }} · 当前页 {{ records.length }} 条 · total_hint {{ totalHint }}</div>
          <el-button-group>
            <el-button size="small" :disabled="offset === 0" @click="prevPage">上一页</el-button>
            <el-button size="small" :disabled="records.length < limit" @click="nextPage">下一页</el-button>
          </el-button-group>
        </div>
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.pager { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; }
.hint { font-size: 12px; color: var(--eid-text-muted); }
.actions { display: flex; gap: 8px; }
</style>
