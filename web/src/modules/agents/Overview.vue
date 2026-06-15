<script setup lang="ts">
/**
 * /agents — agent catalog.
 *
 * Master/detail: left list filterable by user, right panel shows
 * detail incl. rendered soul preview and knob overlays. Create requires
 * a pre-existing user + template (we don't auto-create either — that's
 * the whole point of the five-entity model).
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createAgent,
  deleteAgent,
  getAgent,
  listAgents,
  type AgentDetail,
  type AgentRef,
} from '@/api/agents'
import { listUsers, type UserView } from '@/api/users'
import { listTemplates, type TemplateRef } from '@/api/templates'
import { extractErrorMessage } from '@/utils/format'
import CatalogPage from '@/modules/common/CatalogPage.vue'

const rows = ref<AgentRef[]>([])
const users = ref<UserView[]>([])
const templates = ref<TemplateRef[]>([])
const upstreamAvailable = ref(true)
const loading = ref(false)
const filterUser = ref<string>('')
const selectedId = ref<string | null>(null)
const detail = ref<AgentDetail | null>(null)
const detailLoading = ref(false)

const dialogOpen = ref(false)
const form = reactive({
  user_id: '',
  template_id: '',
  display_name: '',
  set_active: false,
})
const submitting = ref(false)

const filteredRows = computed(() =>
  filterUser.value
    ? rows.value.filter((r) => r.user_id === filterUser.value)
    : rows.value,
)

async function refresh() {
  loading.value = true
  try {
    const [a, u, t] = await Promise.all([
      listAgents(filterUser.value ? { user_id: filterUser.value } : undefined),
      listUsers(),
      listTemplates(),
    ])
    rows.value = a.agents
    upstreamAvailable.value = a.upstream_available
    users.value = u.users
    templates.value = t.templates
    if (!selectedId.value && rows.value.length > 0) {
      void select(rows.value[0].agent_id)
    }
  } catch (e: any) {
    ElMessage.error(`加载失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function select(id: string) {
  selectedId.value = id
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getAgent(id)
  } catch (e: any) {
    ElMessage.error(`加载详情失败: ${extractErrorMessage(e)}`)
  } finally {
    detailLoading.value = false
  }
}

function openCreate() {
  form.user_id = users.value[0]?.spec.user_id || ''
  form.template_id = templates.value[0]?.template_id || ''
  form.display_name = ''
  form.set_active = false
  dialogOpen.value = true
}

async function submit() {
  if (!form.user_id || !form.template_id) {
    ElMessage.warning('请选择 user 和 template')
    return
  }
  submitting.value = true
  try {
    const ref = await createAgent({
      user_id: form.user_id,
      template_id: form.template_id,
      display_name: form.display_name.trim() || undefined,
      set_active: form.set_active,
    })
    ElMessage.success(`已创建 ${ref.agent_id}`)
    dialogOpen.value = false
    await refresh()
    await select(ref.agent_id)
  } catch (e: any) {
    ElMessage.error(`创建失败: ${extractErrorMessage(e)}`)
  } finally {
    submitting.value = false
  }
}

async function remove() {
  if (!detail.value) return
  const d = detail.value
  try {
    await ElMessageBox.confirm(
      `确认删除 agent "${d.ref.agent_id}"? 会级联清除引用该 agent 的 user.active_agent 和 device 绑定。`,
      '删除 Agent',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const res = await deleteAgent(d.ref.agent_id)
    const cleared = res.active_agent_cleared_for_users.length
    const unbound = res.unbound_devices.length
    ElMessage.success(
      `已删除。清除了 ${cleared} 个 user.active_agent, 解绑了 ${unbound} 个设备。`,
    )
    selectedId.value = null
    detail.value = null
    await refresh()
  } catch (e: any) {
    ElMessage.error(`删除失败: ${extractErrorMessage(e)}`)
  }
}

function knobEntries(overlays: Record<string, number> | undefined): [string, number][] {
  if (!overlays) return []
  return Object.entries(overlays)
}

onMounted(refresh)
</script>

<template>
  <CatalogPage
    title="Agent 管理"
    hint="每个 agent = 一个 user + 一个 template + 个性化 overlay。创建 agent 前请确保对应的 user 和 template 已存在。"
  >
    <template #head-actions>
      <el-select
        v-model="filterUser"
        placeholder="按 user 过滤"
        clearable
        size="small"
        style="width: 200px"
        @change="refresh"
      >
        <el-option
          v-for="u in users"
          :key="u.spec.user_id"
          :label="`${u.spec.display_name} (${u.spec.user_id})`"
          :value="u.spec.user_id"
        />
      </el-select>
      <el-button :loading="loading" size="small" @click="refresh">刷新</el-button>
      <el-button type="primary" size="small" @click="openCreate">新建 Agent</el-button>
    </template>

    <el-alert
      v-if="!upstreamAvailable"
      title="Agent 服务不可达"
      type="warning"
      :closable="false"
    />

    <div class="split">
      <div class="left">
        <el-table
          v-loading="loading"
          :data="filteredRows"
          stripe
          highlight-current-row
          @row-click="(row: AgentRef) => select(row.agent_id)"
        >
          <el-table-column prop="agent_id" label="Agent ID" width="220">
            <template #default="{ row }">
              <code class="mono">{{ row.agent_id.slice(0, 12) }}…</code>
            </template>
          </el-table-column>
          <el-table-column prop="display_name" label="名称" />
          <el-table-column prop="user_id" label="User" width="120" />
          <el-table-column label="Active" width="80">
            <template #default="{ row }">
              <el-tag v-if="row.is_active_for_user" type="success" size="small">★</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="right">
        <div v-if="!detail" class="placeholder">
          <span v-if="detailLoading">加载中…</span>
          <span v-else>选择左侧 agent 查看详情</span>
        </div>
        <template v-else>
          <div class="detail-head">
            <div>
              <h3>{{ detail.ref.display_name || detail.ref.agent_id }}</h3>
              <p class="meta">
                <code>{{ detail.ref.agent_id }}</code>
              </p>
              <p class="meta">
                <span>user: {{ detail.ref.user_id }}</span>
                <span>•</span>
                <span>template: {{ detail.ref.template_id }} (rev {{ detail.ref.template_revision }})</span>
              </p>
            </div>
            <div class="head-actions">
              <el-button size="small" type="danger" @click="remove">删除</el-button>
            </div>
          </div>

          <div class="section">
            <h4>Knob Overlays</h4>
            <div v-if="knobEntries(detail.knob_overlays).length === 0" class="muted">
              无 (使用模板默认值)
            </div>
            <el-table
              v-else
              :data="knobEntries(detail.knob_overlays).map(([k, v]) => ({ knob: k, value: v }))"
              size="small"
            >
              <el-table-column prop="knob" label="Knob" />
              <el-table-column prop="value" label="Value" width="120" />
            </el-table>
          </div>

          <div class="section">
            <h4>Soul Preview ({{ detail.soul_size_bytes }} bytes)</h4>
            <pre v-if="detail.soul_md" class="soul">{{ detail.soul_md }}</pre>
            <div v-else class="muted">无法获取 soul (agent 或 template 服务可能不可达)</div>
          </div>
        </template>
      </div>
    </div>

    <el-dialog v-model="dialogOpen" title="新建 Agent" width="520px" :close-on-click-modal="false">
      <el-form label-width="100px">
        <el-form-item label="User">
          <el-select v-model="form.user_id" placeholder="选择 user" style="width: 100%">
            <el-option
              v-for="u in users"
              :key="u.spec.user_id"
              :label="`${u.spec.display_name} (${u.spec.user_id})`"
              :value="u.spec.user_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Template">
          <el-select v-model="form.template_id" placeholder="选择 template" style="width: 100%">
            <el-option
              v-for="t in templates"
              :key="t.template_id"
              :label="`${t.display_name} (${t.template_id})`"
              :value="t.template_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" placeholder="可选, 默认用模板的显示名" />
        </el-form-item>
        <el-form-item label="设为 Active">
          <el-switch v-model="form.set_active" />
        </el-form-item>
      </el-form>
      <p class="dialog-hint">
        创建只登记 Agent 实例；开启 Active 后才会成为该用户未来新会话的默认 Agent。
      </p>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
/* Layout chrome lives in <CatalogPage>. ``head-actions`` here adds
   ``align-items: center`` because we mix a <el-select> with buttons
   in the header — the base ``head-actions`` keeps default alignment. */
:deep(.head-actions) { align-items: center; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.split { display: grid; grid-template-columns: minmax(420px, 1fr) 2fr; gap: 16px; }
.left, .right { background: var(--eid-bg-panel); border: 1px solid var(--eid-border); border-radius: var(--eid-radius-sm); padding: 8px; min-height: 480px; }
.placeholder { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--eid-text-muted); }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; padding: 8px 4px 12px; border-bottom: 1px solid var(--eid-border); }
.detail-head h3 { margin: 0; font-size: 15px; color: var(--eid-text-primary); }
.meta { margin: 4px 0 0; font-size: 12px; color: var(--eid-text-muted); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.section { margin-top: 16px; padding: 8px 4px; }
.section h4 { margin: 0 0 8px; font-size: 13px; color: var(--eid-text-primary); font-weight: 600; }
.soul { margin: 0; padding: 12px; background: var(--eid-bg-canvas); border-radius: var(--eid-radius-sm); font-family: var(--eid-font-mono); font-size: 12px; max-height: 500px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
code, .mono { font-family: var(--eid-font-mono); padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; font-size: 11px; }
.dialog-hint { margin: 8px 0 0 100px; font-size: 12px; color: var(--eid-text-muted); }
</style>
