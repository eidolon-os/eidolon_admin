<script setup lang="ts">
/**
 * Drawer that shows ALL agents bound to one device, lets the operator
 * switch active / view soul / edit soul / delete agent / bind a new one.
 *
 * State ownership: the drawer keeps a local copy of the device's binding
 * (re-fetched after each mutation) so the parent's table doesn't have to
 * refresh on every action — until the drawer closes, when the parent
 * refreshes once.
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteAgent,
  formatTimestamp,
  listDevices,
  switchActiveAgent,
  type AgentEntry,
  type DeviceView,
} from '@/api/devices'
import BindAgentDialog from './BindAgentDialog.vue'
import SoulEditor from './SoulEditor.vue'

const props = defineProps<{
  open: boolean
  device: DeviceView | null
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'changed'): void  // fired when parent should refresh its table
}>()

const localDevice = ref<DeviceView | null>(null)
const loading = ref(false)
const bindDialogOpen = ref(false)
const soulEditorOpen = ref(false)
const soulTarget = ref<{ agentId: string; label: string } | null>(null)
const acting = ref<Record<string, boolean>>({})

watch(
  () => [props.open, props.device?.device_id],
  ([open, _id]) => {
    if (!open) return
    localDevice.value = props.device
  },
  { immediate: true },
)

const agents = computed<AgentEntry[]>(() => localDevice.value?.binding?.agents ?? [])

async function refreshLocal() {
  if (!localDevice.value) return
  loading.value = true
  try {
    const r = await listDevices()
    const fresh = r.devices.find((d) => d.device_id === localDevice.value?.device_id)
    if (fresh) localDevice.value = fresh
  } catch (e: any) {
    ElMessage.error(`刷新失败: ${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

async function onSwitchActive(agent: AgentEntry) {
  if (!localDevice.value || agent.is_active) return
  acting.value[agent.agent_id] = true
  try {
    await switchActiveAgent(localDevice.value.device_id, agent.agent_id)
    ElMessage.success(`已切换 active → ${shortId(agent.agent_id)}`)
    await refreshLocal()
    emit('changed')
  } catch (e: any) {
    ElMessage.error(`切换失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    acting.value[agent.agent_id] = false
  }
}

async function onDeleteAgent(agent: AgentEntry) {
  if (!localDevice.value) return
  const remaining = agents.value.length - 1
  const msg = agent.is_active
    ? remaining > 0
      ? `这是当前 active agent，删除后会自动 fallback 到下一个 (剩余 ${remaining} 个)。继续？`
      : '这是设备绑定的最后一个 agent，删除后设备将无 active agent。继续？'
    : `删除 agent ${shortId(agent.agent_id)} (template: ${agent.template_id})？`
  try {
    await ElMessageBox.confirm(msg, '确认删除', { type: 'warning' })
  } catch {
    return
  }
  acting.value[agent.agent_id] = true
  try {
    const r = await deleteAgent(localDevice.value.device_id, agent.agent_id)
    const tail =
      r.fallback_kind === 'next_newest'
        ? `，active fallback → ${shortId(r.new_active_agent_id ?? '')}`
        : r.fallback_kind === 'cleared'
        ? '，active 已清空'
        : ''
    ElMessage.success(`已删除${tail}`)
    await refreshLocal()
    emit('changed')
  } catch (e: any) {
    ElMessage.error(`删除失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    acting.value[agent.agent_id] = false
  }
}

function openSoul(agent: AgentEntry) {
  soulTarget.value = { agentId: agent.agent_id, label: `${agent.template_id} · ${shortId(agent.agent_id)}` }
  soulEditorOpen.value = true
}

function openBindDialog() {
  bindDialogOpen.value = true
}

async function onBindCreated() {
  await refreshLocal()
  emit('changed')
}

function shortId(id: string): string {
  return id ? id.slice(0, 8) : '—'
}
</script>

<template>
  <el-drawer
    :model-value="open"
    @update:model-value="(v: boolean) => emit('update:open', v)"
    :title="`Agents · ${localDevice?.device_id ?? ''}`"
    size="62%"
    direction="rtl"
  >
    <div class="wrap" v-loading="loading">
      <header class="head">
        <div class="meta">
          <span class="label">设备</span>
          <code>{{ localDevice?.device_id }}</code>
          <el-tag size="small" :type="localDevice?.approved ? 'success' : 'info'" effect="dark">
            {{ localDevice?.approved ? 'approved' : 'discovered' }}
          </el-tag>
        </div>
        <el-button size="small" type="primary" @click="openBindDialog">
          + 新建 Agent
        </el-button>
      </header>

      <div v-if="agents.length === 0" class="empty">
        该设备尚未绑定任何 agent。点【+ 新建 Agent】选个模板试试。
      </div>

      <el-table v-else :data="agents" size="small" stripe class="table">
        <el-table-column label="" width="36">
          <template #default="{ row }">
            <span v-if="row.is_active" class="star" title="active">★</span>
          </template>
        </el-table-column>
        <el-table-column label="agent_id" min-width="100">
          <template #default="{ row }">
            <code>{{ shortId(row.agent_id) }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="template_id" label="template" min-width="140" />
        <el-table-column prop="owner_user_id" label="user" min-width="100" />
        <el-table-column label="created" min-width="160">
          <template #default="{ row }">
            <span class="muted">{{ formatTimestamp(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="240" align="right">
          <template #default="{ row }">
            <el-button
              size="small"
              link
              :disabled="row.is_active || acting[row.agent_id]"
              @click="onSwitchActive(row)"
            >
              {{ row.is_active ? 'active' : '设为 active' }}
            </el-button>
            <el-button size="small" link @click="openSoul(row)">soul</el-button>
            <el-button
              size="small"
              link
              type="danger"
              :loading="acting[row.agent_id]"
              @click="onDeleteAgent(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <BindAgentDialog
        v-if="localDevice"
        v-model:open="bindDialogOpen"
        :device-id="localDevice.device_id"
        @created="onBindCreated"
      />

      <SoulEditor
        v-if="localDevice && soulTarget"
        v-model:open="soulEditorOpen"
        :device-id="localDevice.device_id"
        :agent-id="soulTarget.agentId"
        :agent-label="soulTarget.label"
      />
    </div>
  </el-drawer>
</template>

<style scoped>
.wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.label {
  font-size: 11px;
  color: var(--eid-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.empty {
  padding: 32px;
  text-align: center;
  color: var(--eid-text-muted);
  font-size: 13px;
  border: 1px dashed var(--eid-border);
  border-radius: var(--eid-radius);
}
.table {
  flex-shrink: 0;
}
.star {
  color: var(--eid-warning);
  font-size: 16px;
}
.muted { color: var(--eid-text-muted); font-size: 12px; }
</style>
