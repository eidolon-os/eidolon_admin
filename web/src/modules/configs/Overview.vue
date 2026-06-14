<script setup lang="ts">
/**
 * Unified config browser — left tree groups every editable file by service,
 * right pane delegates to <ConfigEditor> for the selected entry.
 *
 * The list endpoint is small (~6 entries today, double-digits long-term), so
 * we just refetch the full tree on mount and after each successful save —
 * no incremental state plumbing.
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listConfigs, type ConfigEntry, type ServiceGroup } from '@/api/configs'
import { useServicesStore } from '@/stores/services'
import ConfigEditor from './ConfigEditor.vue'

const groups = ref<ServiceGroup[]>([])
const loading = ref(false)
const selectedKey = ref<string | null>(null)
const servicesStore = useServicesStore()

const selected = computed<ConfigEntry | null>(() => {
  if (!selectedKey.value) return null
  const [svc, cfg] = selectedKey.value.split('::')
  for (const g of groups.value) {
    if (g.service_id !== svc) continue
    return g.configs.find((c) => c.config_id === cfg) ?? null
  }
  return null
})

function serviceName(id: string): string {
  return servicesStore.findService(id)?.name ?? id
}

async function refresh() {
  loading.value = true
  try {
    groups.value = await listConfigs()
    // Auto-select first entry on first load so the editor isn't empty.
    if (!selectedKey.value && groups.value.length > 0) {
      const first = groups.value.find((g) => g.configs.length > 0)
      if (first) {
        const c = first.configs[0]
        selectedKey.value = `${c.service_id}::${c.config_id}`
      }
    }
  } catch (e: any) {
    ElMessage.error(`加载配置列表失败: ${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

function select(entry: ConfigEntry) {
  selectedKey.value = `${entry.service_id}::${entry.config_id}`
}

function isSelected(entry: ConfigEntry): boolean {
  return selectedKey.value === `${entry.service_id}::${entry.config_id}`
}

onMounted(() => {
  servicesStore.load()
  refresh()
})
</script>

<template>
  <div class="page eid-page">
    <header class="page-head eid-page-head">
      <div>
        <h2>配置管理</h2>
        <p class="hint eid-page-hint">
          所有子项目的配置文件统一在此查看和编辑。保存前会自动备份（最多保留 10 份），
          编辑器以原文显示，解析视图会自动脱敏 secret/key/token/password 等字段。
        </p>
      </div>
      <el-button :loading="loading" size="small" @click="refresh">刷新</el-button>
    </header>

    <div class="layout eid-split configs-split">
      <aside class="tree eid-panel eid-panel-pad eid-panel-scroll" v-loading="loading">
        <div v-if="!loading && groups.length === 0" class="empty">
          services.yaml 中尚未声明任何 configs:
        </div>
        <section v-for="g in groups" :key="g.service_id" class="group">
          <div class="group-title">{{ serviceName(g.service_id) }}</div>
          <ul class="items">
            <li
              v-for="c in g.configs"
              :key="c.config_id"
              :class="{ active: isSelected(c), missing: !c.exists }"
              @click="select(c)"
            >
              <span class="item-label">{{ c.label }}</span>
              <el-tag size="small" :type="c.exists ? 'info' : 'warning'" effect="plain">
                {{ c.format }}
              </el-tag>
              <span v-if="!c.exists" class="missing-flag">缺失</span>
            </li>
          </ul>
        </section>
      </aside>

      <section class="editor eid-panel">
        <ConfigEditor
          v-if="selected"
          :key="`${selected.service_id}::${selected.config_id}`"
          :entry="selected"
          @saved="refresh"
        />
        <div v-else class="placeholder">
          请在左侧选择一个配置文件
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  min-height: 0;
}
.page-head {
  margin-bottom: 0;
}
.hint {
  max-width: 720px;
}
.layout {
  --eid-split-left: 280px;
  --eid-split-right: minmax(0, 1fr);
  grid-template-columns: 280px 1fr;
}
.empty {
  padding: 16px;
  color: var(--eid-text-muted);
  font-size: 12px;
  text-align: center;
}
.group {
  padding: 8px 4px;
}
.group-title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--eid-text-muted);
  padding: 4px 8px;
  margin-bottom: 4px;
}
.items {
  list-style: none;
  padding: 0;
  margin: 0;
}
.items li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--eid-radius-sm);
  cursor: pointer;
  font-size: 13px;
  color: var(--eid-text-primary);
  transition: background 0.1s;
}
.items li:hover {
  background: var(--eid-bg-elev);
}
.items li.active {
  background: var(--eid-accent-soft);
  color: var(--eid-accent-hover);
}
.items li.missing .item-label {
  color: var(--eid-text-muted);
  font-style: italic;
}
.item-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.missing-flag {
  font-size: 10px;
  color: var(--eid-warning);
}
.editor {
  padding: 16px;
}
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--eid-text-muted);
  font-size: 13px;
}
</style>
