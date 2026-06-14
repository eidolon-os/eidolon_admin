<script setup lang="ts">
/**
 * Single-config editor pane. Loads the file lazily based on the parent's
 * selection, lets the user edit raw text, and exposes:
 *   - parsed view (with secrets masked)
 *   - backups list (with restore)
 *   - save (atomic) + optional reload prompt
 *
 * Validation happens server-side on save; we mirror the parsed view in real
 * time as a soft lint by re-reading the same file after a successful save.
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  describeReload,
  formatTimestamp,
  listBackups,
  readConfig,
  reloadConfig,
  restoreBackup,
  writeConfig,
  type BackupRef,
  type ConfigDetail,
  type ConfigEntry,
} from '@/api/configs'

const props = defineProps<{ entry: ConfigEntry }>()
const emit = defineEmits<{ (e: 'saved'): void }>()

const detail = ref<ConfigDetail | null>(null)
const editorText = ref('')
const loading = ref(false)
const saving = ref(false)
const tab = ref<'raw' | 'parsed' | 'backups'>('raw')
const backups = ref<BackupRef[]>([])
const backupsLoading = ref(false)

const dirty = computed(() => {
  if (!detail.value) return false
  return editorText.value !== detail.value.text
})

const canReload = computed(() => props.entry.reload !== 'none')

watch(
  () => props.entry,
  () => {
    load()
  },
  { immediate: true },
)

watch(tab, (v) => {
  if (v === 'backups') loadBackups()
})

async function load() {
  loading.value = true
  detail.value = null
  editorText.value = ''
  tab.value = 'raw'
  try {
    const d = await readConfig(props.entry.service_id, props.entry.config_id)
    detail.value = d
    editorText.value = d.text
  } catch (e: any) {
    ElMessage.error(`加载失败: ${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

async function loadBackups() {
  backupsLoading.value = true
  try {
    backups.value = await listBackups(props.entry.service_id, props.entry.config_id)
  } catch (e: any) {
    ElMessage.error(`备份列表加载失败: ${e?.message || e}`)
  } finally {
    backupsLoading.value = false
  }
}

async function save() {
  if (!dirty.value) {
    ElMessage.info('没有变更')
    return
  }
  saving.value = true
  try {
    const result = await writeConfig(
      props.entry.service_id,
      props.entry.config_id,
      editorText.value,
    )
    ElMessage.success(
      result.backup
        ? `已保存 (备份 ${formatTimestamp(result.backup.timestamp)})`
        : '已保存',
    )
    // Refresh detail in place so the next dirty check is against the new
    // baseline; the tree refresh in the parent picks up "exists" changes.
    await load()
    emit('saved')

    // Offer reload immediately so the user doesn't have to switch tabs.
    if (canReload.value) {
      promptReload()
    }
  } catch (e: any) {
    // Format-validation errors come back as 400 with a useful message.
    const msg = e?.response?.data?.detail || e?.message || String(e)
    ElMessage.error(`保存失败: ${msg}`)
  } finally {
    saving.value = false
  }
}

async function promptReload() {
  const desc = describeReload(props.entry.reload, props.entry.reload_target)
  try {
    await ElMessageBox.confirm(
      `配置已保存。是否立刻执行 ${desc} 让变更生效？`,
      '需要 reload 吗？',
      { confirmButtonText: '执行', cancelButtonText: '稍后' },
    )
  } catch {
    return
  }
  await doReload()
}

async function doReload() {
  try {
    const r = await reloadConfig(props.entry.service_id, props.entry.config_id)
    if (r.error) {
      ElMessage.error(`reload 失败: ${r.error}`)
    } else {
      ElMessage.success(
        r.duration_ms != null ? `reload 完成 (${r.duration_ms}ms)` : 'reload 完成',
      )
    }
  } catch (e: any) {
    ElMessage.error(`reload 失败: ${e?.message || e}`)
  }
}

async function restore(ts: number) {
  try {
    await ElMessageBox.confirm(
      `将文件恢复到 ${formatTimestamp(ts)} 的版本？当前文件会先被备份。`,
      '确认恢复',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await restoreBackup(props.entry.service_id, props.entry.config_id, ts)
    ElMessage.success('已恢复')
    await load()
    await loadBackups()
    emit('saved')
  } catch (e: any) {
    ElMessage.error(`恢复失败: ${e?.message || e}`)
  }
}

function discard() {
  if (!detail.value) return
  editorText.value = detail.value.text
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}

const parsedJson = computed(() => {
  if (!detail.value?.parsed) return ''
  return JSON.stringify(detail.value.parsed, null, 2)
})
</script>

<template>
  <div class="editor-pane" v-loading="loading">
    <header class="meta">
      <div class="meta-main">
        <h3>{{ entry.label }}</h3>
        <div class="meta-row">
          <code class="path">{{ entry.path }}</code>
          <el-tag size="small" effect="plain">{{ entry.format }}</el-tag>
          <el-tag
            size="small"
            :type="entry.reload === 'none' ? 'info' : 'success'"
            effect="plain"
          >
            {{ describeReload(entry.reload, entry.reload_target) }}
          </el-tag>
          <span v-if="detail?.mtime" class="mtime">
            最后修改 {{ formatTimestamp(detail.mtime) }}
          </span>
          <span v-else-if="detail?.missing" class="warn">文件不存在</span>
        </div>
      </div>
      <div class="meta-actions">
        <el-button
          v-if="canReload"
          size="small"
          :disabled="saving || dirty"
          @click="doReload"
        >
          立即 reload
        </el-button>
        <el-button size="small" :disabled="!dirty" @click="discard">放弃修改</el-button>
        <el-button
          type="primary"
          size="small"
          :loading="saving"
          :disabled="!dirty"
          @click="save"
        >
          保存
        </el-button>
      </div>
    </header>

    <el-tabs v-model="tab" class="tabs">
      <el-tab-pane label="编辑器" name="raw">
        <textarea
          v-model="editorText"
          class="code-area"
          spellcheck="false"
          :placeholder="detail?.missing ? '文件不存在 — 保存后将创建' : ''"
        />
        <div v-if="detail?.parse_error" class="parse-error">
          ⚠ 当前文件解析失败: {{ detail.parse_error }}
        </div>
      </el-tab-pane>

      <el-tab-pane label="解析视图（敏感值已脱敏）" name="parsed">
        <div v-if="detail?.parse_error" class="parse-error">
          ⚠ 解析失败: {{ detail.parse_error }}
        </div>
        <pre v-else-if="detail?.parsed" class="parsed">{{ parsedJson }}</pre>
        <div v-else class="empty-tab">暂无解析结果</div>
      </el-tab-pane>

      <el-tab-pane :label="`备份 (${backups.length})`" name="backups">
        <div v-loading="backupsLoading">
          <el-table
            v-if="backups.length > 0"
            :data="backups"
            size="small"
            stripe
          >
            <el-table-column label="时间" min-width="180">
              <template #default="{ row }">
                {{ formatTimestamp(row.timestamp) }}
              </template>
            </el-table-column>
            <el-table-column label="大小" width="100">
              <template #default="{ row }">{{ formatBytes(row.size) }}</template>
            </el-table-column>
            <el-table-column label="路径" prop="path" show-overflow-tooltip />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button size="small" link @click="restore(row.timestamp)">
                  恢复
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-else class="empty-tab">尚无备份</div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.editor-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  gap: 12px;
}
.meta {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--eid-border);
}
.meta-main h3 {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--eid-text-primary);
}
.meta-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--eid-text-secondary);
}
.path {
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11.5px;
  color: var(--eid-text-secondary);
}
.mtime {
  color: var(--eid-text-muted);
}
.warn {
  color: var(--eid-warning);
}
.meta-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.tabs :deep(.el-tabs__content),
.tabs :deep(.el-tab-pane) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.code-area {
  flex: 1;
  width: 100%;
  resize: none;
  background:
    linear-gradient(rgba(34, 211, 238, 0.032) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 211, 238, 0.022) 1px, transparent 1px),
    var(--eid-bg-inset);
  background-size: 22px 22px;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 16%, var(--eid-border));
  border-radius: 5px;
  color: var(--eid-text-primary);
  font-family: var(--eid-font-mono);
  font-size: 12.5px;
  line-height: 1.55;
  padding: 12px;
  outline: none;
  tab-size: 2;
}
.code-area:focus {
  border-color: var(--eid-accent);
  box-shadow: 0 0 0 2px var(--eid-accent-soft);
}
.parsed {
  flex: 1;
  margin: 0;
  background:
    linear-gradient(rgba(34, 211, 238, 0.032) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 211, 238, 0.022) 1px, transparent 1px),
    var(--eid-bg-inset);
  background-size: 22px 22px;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 16%, var(--eid-border));
  border-radius: 5px;
  padding: 12px;
  font-family: var(--eid-font-mono);
  font-size: 12px;
  color: var(--eid-text-primary);
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.parse-error {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid var(--eid-danger);
  border-radius: var(--eid-radius-sm);
  color: var(--eid-danger);
  padding: 8px 12px;
  font-size: 12px;
  margin-bottom: 8px;
}
.empty-tab {
  padding: 32px;
  text-align: center;
  color: var(--eid-text-muted);
  font-size: 12px;
}
</style>
