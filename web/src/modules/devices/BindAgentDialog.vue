<script setup lang="ts">
/**
 * Dialog for "create a new agent on this device".
 *
 * Decoupling: this component only knows how to render the form + call
 * createAgent. The parent owns "do we open / which device / refresh
 * afterwards". This keeps the dialog reusable from any device row.
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createAgent } from '@/api/devices'
import { gatewayCall } from '@/api/services'

const props = defineProps<{
  open: boolean
  deviceId: string
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'created'): void
}>()

interface TemplateOption {
  template_id: string
  name: string
  archetype: string
}

const templates = ref<TemplateOption[]>([])
const templatesLoading = ref(false)
const templateId = ref<string>('')
const userId = ref<string>('default')
const submitting = ref(false)

const canSubmit = computed(
  () => !!templateId.value && !!userId.value && !submitting.value,
)

// Re-fetch templates each open so a newly-uploaded template appears.
watch(
  () => props.open,
  async (v) => {
    if (!v) return
    templateId.value = ''
    submitting.value = false
    await loadTemplates()
  },
)

async function loadTemplates() {
  templatesLoading.value = true
  try {
    // The gateway proxies hub/agent so the frontend never directly addresses
    // agent's port. /api/services/agent/personas/templates → agent admin route.
    const resp = await gatewayCall('agent', 'personas/templates')
    const raw = resp.data as any[]
    templates.value = raw.map((t) => ({
      template_id: t.metadata?.template_id ?? t.template_id,
      name: t.metadata?.name ?? t.name ?? t.template_id,
      archetype: t.metadata?.archetype ?? t.archetype ?? '',
    }))
  } catch (e: any) {
    ElMessage.error(`加载模板失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    templatesLoading.value = false
  }
}

async function submit() {
  submitting.value = true
  try {
    const r = await createAgent(props.deviceId, {
      template_id: templateId.value,
      user_id: userId.value.trim(),
    })
    ElMessage.success(`已绑定 agent ${r.agent_id.slice(0, 8)}…`)
    emit('created')
    emit('update:open', false)
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || String(e)
    ElMessage.error(`绑定失败: ${detail}`)
  } finally {
    submitting.value = false
  }
}

function cancel() {
  if (submitting.value) return
  emit('update:open', false)
}
</script>

<template>
  <el-dialog
    :model-value="open"
    @update:model-value="(v: boolean) => emit('update:open', v)"
    title="新建 Agent (绑定模板)"
    width="480px"
    :close-on-click-modal="false"
    @close="cancel"
  >
    <el-form label-position="top" size="small">
      <el-form-item label="设备 ID">
        <code class="device-id">{{ deviceId }}</code>
      </el-form-item>
      <el-form-item label="Persona 模板">
        <el-select
          v-model="templateId"
          placeholder="选择一个模板"
          :loading="templatesLoading"
          style="width: 100%"
        >
          <el-option
            v-for="t in templates"
            :key="t.template_id"
            :label="`${t.name} (${t.template_id})`"
            :value="t.template_id"
          >
            <span style="float: left">{{ t.name }}</span>
            <span style="float: right; color: var(--eid-text-muted); font-size: 11px">
              {{ t.archetype }}
            </span>
          </el-option>
        </el-select>
      </el-form-item>
      <el-form-item label="User ID">
        <el-input
          v-model="userId"
          placeholder="例如 default"
          maxlength="64"
        />
        <div class="hint">
          创建出来的 agent 会归属此 (tenant, user) 标识。MVP 默认 tenant=default。
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button :disabled="submitting" @click="cancel">取消</el-button>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="!canSubmit"
        @click="submit"
      >
        绑定
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.device-id {
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.hint {
  font-size: 11px;
  color: var(--eid-text-muted);
  margin-top: 4px;
}
</style>
