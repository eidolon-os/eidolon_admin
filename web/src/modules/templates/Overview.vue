<script setup lang="ts">
/**
 * /templates — persona template browser.
 *
 * Read-mostly: most ops list + view detail. Create/fork/edit are
 * power-user actions. YAML editing is delegated to ConfigEditor's
 * monaco-like textarea pattern in a future iteration; this page does
 * raw textarea for now, which matches how operators author personas
 * today.
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createTemplate,
  deleteTemplate,
  forkTemplate,
  getTemplate,
  listTemplates,
  updateTemplate,
  type TemplateDetail,
  type TemplateRef,
} from '@/api/templates'
import { extractErrorMessage } from '@/utils/format'
import CatalogPage from '@/modules/common/CatalogPage.vue'

const rows = ref<TemplateRef[]>([])
const upstreamAvailable = ref(true)
const loading = ref(false)
const detail = ref<TemplateDetail | null>(null)
const detailLoading = ref(false)
const selectedId = ref<string | null>(null)

const dialogOpen = ref(false)
const dialogMode = ref<'create' | 'edit' | 'fork'>('create')
const form = reactive({
  template_id: '',
  tenant_id: 'default',
  display_name: '',
  yaml_body: '',
})
const submitting = ref(false)

async function refresh() {
  loading.value = true
  try {
    const r = await listTemplates()
    rows.value = r.templates
    upstreamAvailable.value = r.upstream_available
    if (!selectedId.value && rows.value.length > 0) {
      void select(rows.value[0].template_id)
    }
  } catch (e: any) {
    upstreamAvailable.value = false
    ElMessage.error(`加载模板失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function select(id: string) {
  selectedId.value = id
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getTemplate(id)
  } catch (e: any) {
    ElMessage.error(`加载模板详情失败: ${extractErrorMessage(e)}`)
  } finally {
    detailLoading.value = false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  form.template_id = ''
  form.tenant_id = 'default'
  form.display_name = ''
  form.yaml_body = 'identity_core:\n  archetype: ""\n  name: ""\n'
  dialogOpen.value = true
}

function openEdit() {
  if (!detail.value) return
  dialogMode.value = 'edit'
  form.template_id = detail.value.ref.template_id
  form.tenant_id = detail.value.ref.tenant_id
  form.display_name = detail.value.ref.display_name
  form.yaml_body = detail.value.yaml_body
  dialogOpen.value = true
}

function openFork() {
  if (!detail.value) return
  dialogMode.value = 'fork'
  form.template_id = `${detail.value.ref.template_id}_copy`
  form.tenant_id = detail.value.ref.tenant_id
  form.display_name = `${detail.value.ref.display_name} (copy)`
  form.yaml_body = detail.value.yaml_body
  dialogOpen.value = true
}

async function submit() {
  submitting.value = true
  try {
    if (dialogMode.value === 'create') {
      await createTemplate({
        template_id: form.template_id.trim(),
        tenant_id: form.tenant_id.trim() || 'default',
        display_name: form.display_name.trim(),
        yaml_body: form.yaml_body,
      })
      ElMessage.success('模板已创建')
    } else if (dialogMode.value === 'edit') {
      await updateTemplate(form.template_id, {
        display_name: form.display_name.trim(),
        yaml_body: form.yaml_body,
      })
      ElMessage.success('已保存')
    } else if (dialogMode.value === 'fork' && selectedId.value) {
      await forkTemplate(selectedId.value, {
        new_template_id: form.template_id.trim(),
        target_tenant_id: form.tenant_id.trim() || 'default',
        new_display_name: form.display_name.trim(),
      })
      ElMessage.success('已 fork')
    }
    dialogOpen.value = false
    await refresh()
    if (dialogMode.value !== 'fork') {
      await select(form.template_id)
    }
  } catch (e: any) {
    ElMessage.error(`提交失败: ${extractErrorMessage(e)}`)
  } finally {
    submitting.value = false
  }
}

async function remove() {
  if (!detail.value) return
  const t = detail.value
  if (t.agent_refcount > 0) {
    ElMessage.warning(`不能删除: 还有 ${t.agent_refcount} 个 Agent 引用该模板`)
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除模板 "${t.ref.template_id}"? 该操作不可恢复。`,
      '删除模板',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteTemplate(t.ref.template_id)
    ElMessage.success('已删除')
    selectedId.value = null
    detail.value = null
    await refresh()
  } catch (e: any) {
    ElMessage.error(`删除失败: ${extractErrorMessage(e)}`)
  }
}

onMounted(refresh)
</script>

<template>
  <CatalogPage
    title="Persona 模板"
    hint='模板是 agent 的"灵魂"来源。一个模板可以被多个 agent 引用,删除前需先删除所有引用该模板的 agent。'
  >
    <template #head-actions>
      <el-button :loading="loading" size="small" @click="refresh">刷新</el-button>
      <el-button type="primary" size="small" @click="openCreate">新建模板</el-button>
    </template>

    <el-alert
      v-if="!upstreamAvailable"
      title="Agent 服务不可达"
      type="warning"
      :closable="false"
      description="模板存储在 agent 项目内,该服务当前不可用。请检查 supervisor 状态。"
    />

    <div class="split">
      <div class="left">
        <el-table
          v-loading="loading"
          :data="rows"
          stripe
          highlight-current-row
          @row-click="(row: TemplateRef) => select(row.template_id)"
        >
          <el-table-column prop="template_id" label="ID" width="180" />
          <el-table-column prop="display_name" label="名称" />
          <el-table-column prop="archetype" label="原型" width="120" />
          <el-table-column prop="source" label="来源" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.source === 'builtin' ? 'info' : 'success'">
                {{ row.source }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="revision" label="rev" width="80" />
        </el-table>
      </div>
      <div class="right">
        <div v-if="!detail" class="placeholder">
          <span v-if="detailLoading">加载中…</span>
          <span v-else>选择左侧模板查看详情</span>
        </div>
        <template v-else>
          <div class="detail-head">
            <div>
              <h3>{{ detail.ref.display_name }}</h3>
              <p class="meta">
                <code>{{ detail.ref.template_id }}</code>
                <span>•</span>
                <span>tenant: {{ detail.ref.tenant_id }}</span>
                <span>•</span>
                <span>引用 agent 数: {{ detail.agent_refcount }}</span>
              </p>
            </div>
            <div class="head-actions">
              <el-button size="small" @click="openFork">Fork</el-button>
              <el-button size="small" @click="openEdit">编辑</el-button>
              <el-button
                size="small"
                type="danger"
                :disabled="detail.agent_refcount > 0"
                @click="remove"
              >
                删除
              </el-button>
            </div>
          </div>
          <pre class="yaml">{{ detail.yaml_body }}</pre>
        </template>
      </div>
    </div>

    <el-dialog
      v-model="dialogOpen"
      :title="
        dialogMode === 'create'
          ? '新建模板'
          : dialogMode === 'fork'
            ? 'Fork 模板'
            : '编辑模板'
      "
      width="720px"
      :close-on-click-modal="false"
    >
      <el-form label-width="100px">
        <el-form-item label="Template ID">
          <el-input
            v-model="form.template_id"
            :disabled="dialogMode === 'edit'"
            placeholder="例如: caretaker_jiezhi"
          />
        </el-form-item>
        <el-form-item label="Tenant">
          <el-input v-model="form.tenant_id" :disabled="dialogMode === 'edit'" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" />
        </el-form-item>
        <el-form-item label="YAML">
          <el-input
            v-model="form.yaml_body"
            type="textarea"
            :rows="14"
            :autosize="{ minRows: 14, maxRows: 30 }"
            style="font-family: var(--eid-font-mono); font-size: 12px"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
/* Layout chrome (page / page-head / hint / head-actions) lives in
   <CatalogPage>. Page-local styles only here. */
.split { display: grid; grid-template-columns: minmax(360px, 1fr) 2fr; gap: 16px; }
.left, .right { background: var(--eid-bg-panel); border: 1px solid var(--eid-border); border-radius: var(--eid-radius-sm); padding: 8px; min-height: 480px; }
.placeholder { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--eid-text-muted); }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; padding: 8px 4px 12px; border-bottom: 1px solid var(--eid-border); }
.detail-head h3 { margin: 0; font-size: 15px; color: var(--eid-text-primary); }
.meta { margin: 4px 0 0; font-size: 12px; color: var(--eid-text-muted); display: flex; gap: 8px; align-items: center; }
.yaml { margin: 12px 0 0; padding: 12px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); font-family: var(--eid-font-mono); font-size: 12.5px; line-height: 1.62; max-height: 600px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
code { font-family: var(--eid-font-mono); padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; }
</style>
