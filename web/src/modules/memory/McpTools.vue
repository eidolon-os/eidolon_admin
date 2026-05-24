<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { listMcpTools, type McpToolOut } from '@/api/memory'
import { useMemoryUserStore } from '@/stores/memoryUser'
import MemoryPageShell from './components/MemoryPageShell.vue'

const store = useMemoryUserStore()
const tools = ref<McpToolOut[]>([])
const loading = ref(false)
const filter = ref('')
const activeNames = ref<string[]>([])

async function load() {
  if (!store.currentId) return
  loading.value = true
  try {
    const r = await listMcpTools(store.currentId)
    tools.value = r.tools
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => store.currentId, load)

function tagFor(name: string): { type: 'info' | 'primary' | 'success'; label: string } {
  if (name.includes('_kg_')) return { type: 'primary', label: 'KG' }
  if (name.includes('memory')) return { type: 'success', label: 'memory' }
  return { type: 'info', label: 'other' }
}

const visible = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return tools.value
  return tools.value.filter(
    (t) => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q),
  )
})

function expandAll() { activeNames.value = visible.value.map((t) => t.name) }
function collapseAll() { activeNames.value = [] }
</script>

<template>
  <MemoryPageShell title="MCP Tools">
    <template #default>
      <el-card>
        <template #header>
          <div class="bar">
            <el-input v-model="filter" placeholder="按名称 / 描述过滤" size="small" style="width: 280px" clearable />
            <div class="actions">
              <span class="hint">{{ visible.length }} / {{ tools.length }}</span>
              <el-button size="small" @click="expandAll">展开全部</el-button>
              <el-button size="small" @click="collapseAll">收起全部</el-button>
              <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
            </div>
          </div>
        </template>

        <el-collapse v-model="activeNames" v-loading="loading">
          <el-collapse-item
            v-for="tool in visible"
            :key="tool.name"
            :name="tool.name"
          >
            <template #title>
              <el-tag :type="tagFor(tool.name).type" size="small" effect="dark" style="margin-right: 12px">
                {{ tagFor(tool.name).label }}
              </el-tag>
              <span class="mono">{{ tool.name }}</span>
              <span class="desc">{{ tool.description }}</span>
            </template>
            <pre class="schema">{{ JSON.stringify(tool.input_schema, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>

        <el-empty v-if="!loading && visible.length === 0" description="无工具" />
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; align-items: center; }
.hint { font-size: 12px; color: var(--eid-text-muted); }
.mono { font-family: var(--eid-font-mono); font-weight: 500; }
.desc {
  font-size: 12px;
  color: var(--eid-text-muted);
  margin-left: 16px;
  font-weight: normal;
}
.schema {
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  padding: 12px 14px;
  border-radius: 6px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
</style>
