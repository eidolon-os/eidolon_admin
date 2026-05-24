<script setup lang="ts">
/**
 * Read / edit one agent's soul.md (markdown stored in NATS souls bucket).
 *
 * Behavioural contract:
 * - Opens as read-only with the current markdown.
 * - Clicking 编辑 swaps in an editable textarea preserving cursor.
 * - 保存 sends a PUT; on success we refresh size, drop back to read-only.
 * - Cancel discards local edits.
 *
 * No history / diff UI in this phase — NATS KV history=10 silently keeps
 * versions but the rollback surface is Phase 26.
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getSoul, updateSoul } from '@/api/devices'

const props = defineProps<{
  open: boolean
  deviceId: string
  agentId: string
  agentLabel: string  // shown in title
}>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const baseline = ref('')   // last server snapshot
const draft = ref('')      // textarea value
const sizeBytes = ref(0)

const dirty = computed(() => editing.value && draft.value !== baseline.value)

watch(
  () => props.open,
  async (v) => {
    if (!v) return
    await load()
  },
)

async function load() {
  loading.value = true
  editing.value = false
  try {
    const r = await getSoul(props.deviceId, props.agentId)
    baseline.value = r.markdown
    draft.value = r.markdown
    sizeBytes.value = r.size_bytes
  } catch (e: any) {
    ElMessage.error(`加载 soul 失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    loading.value = false
  }
}

function beginEdit() {
  draft.value = baseline.value
  editing.value = true
}

function discardEdit() {
  draft.value = baseline.value
  editing.value = false
}

async function save() {
  if (!dirty.value) {
    editing.value = false
    return
  }
  saving.value = true
  try {
    const r = await updateSoul(props.deviceId, props.agentId, draft.value)
    baseline.value = draft.value
    sizeBytes.value = r.size_bytes
    editing.value = false
    ElMessage.success(`已保存（${r.size_bytes} 字节）`)
  } catch (e: any) {
    ElMessage.error(`保存失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    saving.value = false
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}
</script>

<template>
  <el-drawer
    :model-value="open"
    @update:model-value="(v: boolean) => emit('update:open', v)"
    :title="`Soul · ${agentLabel}`"
    size="60%"
    direction="rtl"
    :close-on-click-modal="!dirty"
  >
    <div class="wrap" v-loading="loading">
      <div class="toolbar">
        <el-tag size="small" effect="plain">{{ formatBytes(sizeBytes) }}</el-tag>
        <el-tag v-if="editing" size="small" type="warning" effect="dark">编辑中</el-tag>
        <span class="spacer" />
        <template v-if="!editing">
          <el-button size="small" @click="beginEdit">编辑</el-button>
        </template>
        <template v-else>
          <el-button size="small" :disabled="saving" @click="discardEdit">取消</el-button>
          <el-button
            size="small"
            type="primary"
            :loading="saving"
            :disabled="!dirty"
            @click="save"
          >
            保存
          </el-button>
        </template>
      </div>
      <textarea
        v-model="draft"
        class="md"
        :readonly="!editing"
        spellcheck="false"
      />
    </div>
  </el-drawer>
</template>

<style scoped>
.wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 10px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.spacer { flex: 1; }
.md {
  flex: 1;
  width: 100%;
  resize: none;
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius-sm);
  color: var(--eid-text-primary);
  font-family: var(--eid-font-mono);
  font-size: 12.5px;
  line-height: 1.55;
  padding: 12px;
  outline: none;
}
.md[readonly] { opacity: 0.92; }
.md:focus:not([readonly]) {
  border-color: var(--eid-accent);
  box-shadow: 0 0 0 2px var(--eid-accent-soft);
}
</style>
