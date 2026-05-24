<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { readConfig, reread, writeConfig } from '@/api/supervisor'

const props = defineProps<{
  open: boolean
  name: string
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'saved'): void
}>()

const loading = ref(false)
const saving = ref(false)
const text = ref('')
const original = ref('')
const meta = ref<{ programs: string[]; groups: string[]; enabled: boolean } | null>(null)

watch(
  () => props.open,
  async (v) => {
    if (!v || !props.name) return
    await load()
  },
  { immediate: true },
)

async function load() {
  loading.value = true
  try {
    const data = await readConfig(props.name)
    text.value = data.text
    original.value = data.text
    meta.value = {
      programs: data.programs,
      groups: data.groups,
      enabled: data.enabled,
    }
  } catch (e: any) {
    ElMessage.error(`加载失败：${e?.response?.data?.detail || e.message}`)
    emit('update:open', false)
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (text.value === original.value) {
    ElMessage.info('内容未更改')
    return
  }
  saving.value = true
  try {
    await writeConfig(props.name, text.value)
    original.value = text.value
    ElMessage.success('已保存')
    // 询问是否 reread + update
    try {
      await ElMessageBox.confirm(
        '是否立即 reread + update supervisord，让改动生效？',
        '同步到 supervisord',
        { confirmButtonText: '是', cancelButtonText: '稍后', type: 'info' },
      )
      const { data } = await reread()
      ElMessage.success(`reread 完成：+${data.added?.length || 0} ~${data.changed?.length || 0} -${data.removed?.length || 0}`)
    } catch (_) {
      // user said 稍后
    }
    emit('saved')
  } catch (e: any) {
    ElMessage.error(`保存失败：${e?.response?.data?.detail || e.message}`)
  } finally {
    saving.value = false
  }
}

const dirty = () => text.value !== original.value

function onBeforeClose(done: () => void) {
  if (dirty()) {
    ElMessageBox.confirm('有未保存的修改，确定关闭？', '提示', { type: 'warning' })
      .then(() => done())
      .catch(() => {})
  } else {
    done()
  }
}
</script>

<template>
  <el-drawer
    :model-value="open"
    @update:model-value="(v: boolean) => emit('update:open', v)"
    :title="`编辑配置 — ${name}.conf`"
    size="70%"
    direction="rtl"
    :before-close="onBeforeClose"
  >
    <div v-loading="loading" class="editor-wrap">
      <div class="meta-bar">
        <span v-if="meta">
          programs: <code>{{ meta.programs.join(', ') || '(none)' }}</code>
          ·
          groups: <code>{{ meta.groups.join(', ') || '(none)' }}</code>
          ·
          <el-tag size="small" :type="meta.enabled ? 'success' : 'info'" effect="plain">
            {{ meta.enabled ? 'enabled' : 'disabled' }}
          </el-tag>
        </span>
        <span class="spacer" />
        <span v-if="dirty()" class="dirty-flag">● 未保存</span>
      </div>

      <textarea
        v-model="text"
        class="editor"
        spellcheck="false"
        autocorrect="off"
        autocapitalize="off"
        wrap="off"
      />

      <div class="footer">
        <el-button @click="emit('update:open', false)">关闭</el-button>
        <el-button :disabled="!dirty()" :loading="saving" type="primary" @click="onSave">保存</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.editor-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.meta-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px 12px 4px;
  font-size: 12px;
  color: var(--eid-text-secondary, #9ca3af);
}
.meta-bar code {
  background: var(--eid-bg-inset, #0f172a);
  color: var(--eid-text-primary, #f3f4f6);
  padding: 1px 6px;
  border-radius: 3px;
  font-family: var(--eid-font-mono, ui-monospace, monospace);
}
.spacer {
  flex: 1;
}
.dirty-flag {
  color: var(--eid-warning, #f59e0b);
}
.editor {
  flex: 1;
  width: 100%;
  min-height: 480px;
  resize: none;
  background: var(--eid-bg-inset, #0f172a);
  color: var(--eid-text-primary, #f3f4f6);
  border: 1px solid var(--eid-border-strong, #374151);
  border-radius: 6px;
  padding: 12px 14px;
  font-family: var(--eid-font-mono, ui-monospace, "JetBrains Mono", Menlo, monospace);
  font-size: 13px;
  line-height: 1.55;
  tab-size: 4;
}
.editor:focus {
  outline: none;
  border-color: var(--eid-accent, #6366f1);
}
.footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
}
</style>
