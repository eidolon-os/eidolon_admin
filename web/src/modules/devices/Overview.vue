<script setup lang="ts">
/**
 * /devices — device catalog (Phase 29.G shape).
 *
 * Devices no longer "own" agents. Each device points at a pre-existing
 * agent via bind/unbind. The bound agent already encodes the user +
 * template — devices are just the runtime endpoint.
 *
 * Workflow:
 *   1. Device discovered (via mDNS to hub) → appears here unapproved.
 *   2. Operator clicks 【批准】.
 *   3. Operator clicks 【绑定 agent】 to pick one from /agents.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveDevice,
  bindDevice,
  listDevices,
  unbindDevice,
  unregisterDevice,
  type DeviceView,
} from '@/api/devices'
import { listAgents, type AgentRef } from '@/api/agents'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import CatalogPage from '@/modules/common/CatalogPage.vue'

const devices = ref<DeviceView[]>([])
const agents = ref<AgentRef[]>([])
const hubAvailable = ref(true)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const bindDialogOpen = ref(false)
const bindTarget = ref<DeviceView | null>(null)
const bindAgentId = ref<string>('')
const submitting = ref(false)

const pendingCount = computed(
  () => devices.value.filter((d) => !d.approved).length,
)

async function refresh() {
  loading.value = true
  try {
    const [d, a] = await Promise.all([listDevices(), listAgents()])
    devices.value = d.devices
    hubAvailable.value = d.hub_available
    agents.value = a.agents
  } catch (e: any) {
    ElMessage.error(`加载失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => {
    if (!loading.value) void refresh()
  }, 10_000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

async function onApprove(d: DeviceView) {
  try {
    await approveDevice(d.device_id)
    ElMessage.success(`已批准 ${d.device_id}`)
    await refresh()
  } catch (e: any) {
    ElMessage.error(`批准失败: ${extractErrorMessage(e)}`)
  }
}

function openBind(d: DeviceView) {
  bindTarget.value = d
  bindAgentId.value = d.binding?.agent_id || agents.value[0]?.agent_id || ''
  bindDialogOpen.value = true
}

async function submitBind() {
  if (!bindTarget.value || !bindAgentId.value) {
    ElMessage.warning('请选择 agent')
    return
  }
  submitting.value = true
  try {
    await bindDevice(bindTarget.value.device_id, bindAgentId.value)
    ElMessage.success('已绑定')
    bindDialogOpen.value = false
    await refresh()
  } catch (e: any) {
    ElMessage.error(`绑定失败: ${extractErrorMessage(e)}`)
  } finally {
    submitting.value = false
  }
}

async function onUnbind(d: DeviceView) {
  try {
    await ElMessageBox.confirm(`确认解绑 ${d.device_id}?`, '解绑', { type: 'warning' })
  } catch {
    return
  }
  try {
    await unbindDevice(d.device_id)
    ElMessage.success('已解绑')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`解绑失败: ${extractErrorMessage(e)}`)
  }
}

async function onUnregister(d: DeviceView) {
  try {
    await ElMessageBox.confirm(
      `确认从 hub 注销 ${d.device_id}? 设备会失去身份, 重新发现时再走一遍批准流程。`,
      '注销设备',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await unregisterDevice(d.device_id)
    ElMessage.success('已注销')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`注销失败: ${extractErrorMessage(e)}`)
  }
}

function statusTag(d: DeviceView): { label: string; type: 'success' | 'warning' | 'info' | 'danger' } {
  if (!d.approved) return { label: 'pending', type: 'warning' }
  if (d.binding) return { label: 'bound', type: 'success' }
  return { label: 'unbound', type: 'info' }
}

function agentLabel(agent_id: string): string {
  const a = agents.value.find((x) => x.agent_id === agent_id)
  if (!a) return agent_id.slice(0, 12) + '…'
  return `${a.display_name || a.agent_id.slice(0, 8)} (${a.user_id})`
}
</script>

<template>
  <CatalogPage title="设备管理">
    <template #hint-html>
      设备通过 mDNS 发现 hub 后自动出现。操作员先【批准】, 再【绑定 Agent】
      将该设备指向一个已存在的 agent (在 /agents 中创建)。
      <span v-if="!hubAvailable" class="warn">⚠ Hub 不可达, 状态可能过期</span>
    </template>
    <template #head-actions>
      <el-tag v-if="pendingCount > 0" size="small" type="warning" effect="dark">
        {{ pendingCount }} 待批准
      </el-tag>
      <el-button :icon="Refresh" :loading="loading" size="small" @click="refresh">
        刷新
      </el-button>
    </template>

    <el-table v-loading="loading && devices.length === 0" :data="devices" size="small" stripe>
      <el-table-column label="device_id" min-width="160">
        <template #default="{ row }">
          <code class="mono">{{ row.device_id }}</code>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="kind" label="类型" width="100" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusTag(row).type" size="small" effect="dark">
            {{ statusTag(row).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="绑定 Agent" min-width="220">
        <template #default="{ row }">
          <span v-if="row.binding" class="mono">{{ agentLabel(row.binding.agent_id) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="last_seen" width="160">
        <template #default="{ row }">
          <span class="muted">{{ formatTimestamp(row.last_seen) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240" align="right">
        <template #default="{ row }">
          <el-button v-if="!row.approved" size="small" type="primary" @click="onApprove(row)">
            批准
          </el-button>
          <el-button v-if="row.approved" size="small" @click="openBind(row)">
            {{ row.binding ? '改绑' : '绑定 Agent' }}
          </el-button>
          <el-button
            v-if="row.binding"
            size="small"
            type="warning"
            link
            @click="onUnbind(row)"
          >
            解绑
          </el-button>
          <el-button size="small" type="danger" link @click="onUnregister(row)">
            注销
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="!loading && devices.length === 0" class="empty">
      还没有设备 — 让设备通过 mDNS 找到 hub 后这里就会出现。
    </div>

    <el-dialog v-model="bindDialogOpen" title="绑定 Agent" width="480px" :close-on-click-modal="false">
      <p class="dialog-hint">
        将设备 <code class="mono">{{ bindTarget?.device_id }}</code> 绑定到一个已存在的 agent。
      </p>
      <el-select v-model="bindAgentId" placeholder="选择 agent" style="width: 100%">
        <el-option
          v-for="a in agents"
          :key="a.agent_id"
          :label="`${a.display_name || a.agent_id.slice(0, 8)} — ${a.user_id} / ${a.template_id}`"
          :value="a.agent_id"
        />
      </el-select>
      <template #footer>
        <el-button @click="bindDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitBind">绑定</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
/* Layout chrome lives in <CatalogPage>. */
.warn { color: var(--eid-warning); margin-left: 8px; }
:deep(.head-actions) { align-items: center; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; padding: 1px 6px; background: var(--eid-bg-canvas); border-radius: 3px; }
.empty { padding: 32px; text-align: center; color: var(--eid-text-muted); font-size: 12px; background: var(--eid-bg-panel); border: 1px dashed var(--eid-border); border-radius: var(--eid-radius); }
.dialog-hint { margin: 0 0 12px; font-size: 12px; color: var(--eid-text-muted); }
</style>
