<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  createMemoryUser,
  initMemoryUserPalace,
  removeMemoryUserConsolidator,
  setMemoryUserEnabled,
  startMemoryUser,
  stopMemoryUser,
  updateMemoryUserConsolidator,
  type ConsolidatorStatus,
  type ConsolidatorUpdateBody,
} from '@/api/memory'
import { useMemoryUserStore } from '@/stores/memoryUser'
import { memoryAgentStatus } from '@/utils/memoryRuntime'

const store = useMemoryUserStore()
const busy = ref<Record<string, boolean>>({})
let refreshTimer: ReturnType<typeof setInterval> | null = null
const dialogOpen = ref(false)
const submitting = ref(false)
const form = ref({ id: '', port: 8030, enabled: true, palace_path: '' })

const consDialogOpen = ref(false)
const consSubmitting = ref(false)
const consUserId = ref('')
const consOriginalEnabled = ref(false)
const consForm = ref<ConsolidatorUpdateBody>({
  enabled: false,
  interval_hours: 6,
  window_days: 30,
  min_drawers: 3,
  min_confidence: 0.6,
})

onMounted(async () => {
  await store.load(true)
  refreshTimer = setInterval(() => {
    if (!store.loading) void store.load(true)
  }, 5_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

async function refresh() {
  await store.load(true)
}

function lock(id: string) { busy.value[id] = true }
function unlock(id: string) { busy.value[id] = false }

async function withBusy(id: string, fn: () => Promise<unknown>) {
  lock(id)
  try { await fn() }
  finally { unlock(id) }
  await refresh()
}

async function onEnable(userId: string, enabled: boolean) {
  if (!enabled) {
    try {
      await ElMessageBox.confirm(
        `停用 ${userId} 的 memory user？这会停止该用户的 agent_runner。`,
        '停用 Memory User',
        { type: 'warning' },
      )
    } catch {
      await refresh()
      return
    }
  }
  await withBusy(userId, async () => {
    try {
      await setMemoryUserEnabled(userId, enabled)
      ElMessage.success(`${userId}: enabled=${enabled}`)
    } catch (e: any) {
      ElMessage.error(`${userId}: ${e?.response?.data?.detail || e.message}`)
    }
  })
}

async function onStart(userId: string) {
  await withBusy(userId, async () => {
    await startMemoryUser(userId)
    ElMessage.success(`${userId}: starting…（agent_runner reconcile 中）`)
  })
}

async function onStop(userId: string) {
  await ElMessageBox.confirm(`停止 ${userId} 的 agent_runner？`, '确认', { type: 'warning' })
  await withBusy(userId, async () => {
    await stopMemoryUser(userId)
    ElMessage.success(`${userId}: stopped`)
  })
}

async function onInit(userId: string) {
  await withBusy(userId, async () => {
    const r = await initMemoryUserPalace(userId)
    ElMessage.success(r.message)
  })
}

function openCreate() {
  const used = new Set(store.users.map((u) => u.port))
  let p = 8030
  while (used.has(p)) p += 1
  form.value = { id: '', port: p, enabled: true, palace_path: '' }
  dialogOpen.value = true
}

async function onCreate() {
  if (!form.value.id.trim()) {
    ElMessage.warning('请填写 user id')
    return
  }
  submitting.value = true
  try {
    const r = await createMemoryUser({ ...form.value, id: form.value.id.trim() })
    ElMessage.success(r.message)
    dialogOpen.value = false
    await refresh()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    submitting.value = false
  }
}

function openConsolidator(userId: string, c: ConsolidatorStatus | null | undefined) {
  consUserId.value = userId
  consOriginalEnabled.value = c?.enabled ?? false
  consForm.value = {
    enabled: c?.enabled ?? false,
    interval_hours: c?.interval_hours ?? 6,
    window_days: c?.window_days ?? 30,
    min_drawers: c?.min_drawers ?? 3,
    min_confidence: c?.min_confidence ?? 0.6,
  }
  consDialogOpen.value = true
}

async function onSaveConsolidator() {
  if (consOriginalEnabled.value && !consForm.value.enabled) {
    try {
      await ElMessageBox.confirm(
        `停用 ${consUserId.value} 的 consolidator？后台主题整理 worker 会停止运行。`,
        '停用 Consolidator',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  consSubmitting.value = true
  try {
    const r = await updateMemoryUserConsolidator(consUserId.value, { ...consForm.value })
    ElMessage.success(r.message)
    consDialogOpen.value = false
    await refresh()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    consSubmitting.value = false
  }
}

async function onRemoveConsolidator(userId: string) {
  await ElMessageBox.confirm(
    `移除 ${userId} 的 consolidator 配置块？`,
    '确认',
    { type: 'warning' },
  )
  await withBusy(userId, async () => {
    const r = await removeMemoryUserConsolidator(userId)
    ElMessage.success(r.message)
  })
}

function consolidatorLabel(c: ConsolidatorStatus | null | undefined) {
  if (!c?.configured) return '未配置'
  if (!c.enabled) return '已配置 / 未启用'
  if (c.running) return '运行中'
  return '已启用 / 未运行'
}

function consolidatorTagType(c: ConsolidatorStatus | null | undefined) {
  if (!c?.configured) return 'info'
  if (!c.enabled) return 'info'
  if (c.running) return 'success'
  return 'warning'
}

const usersFile = computed(() => store.users.length ? 'eidolon_admin/var/registry.sqlite3' : '')
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Memory Users</h2>
        <div class="subtitle">{{ usersFile }}</div>
      </div>
      <div class="actions">
        <el-button size="small" :icon="Refresh" :loading="store.loading" @click="refresh">刷新</el-button>
        <el-button type="primary" size="small" :icon="Plus" @click="openCreate">新建 user</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
      <template #title>
        Consolidator 为 opt-in 后台主题 worker，会调用 LLM；由 memory-supervisor 子进程管理，无需单独 supervisord program。
      </template>
    </el-alert>

    <el-table :data="store.users" v-loading="store.loading" stripe>
      <el-table-column label="User ID" min-width="120">
        <template #default="{ row }">
          <span class="user-id">{{ row.user_id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Port" width="80">
        <template #default="{ row }"><span class="mono">{{ row.port }}</span></template>
      </el-table-column>
      <el-table-column label="启用" width="72">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            size="small"
            :loading="busy[row.user_id]"
            @change="(v: boolean) => onEnable(row.user_id, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="Agent" width="190">
        <template #default="{ row }">
          <el-tag
            :type="memoryAgentStatus(row).type"
            :effect="row.enabled ? 'dark' : 'plain'"
            :title="memoryAgentStatus(row).hint"
            size="small"
          >
            {{ memoryAgentStatus(row).label }}
          </el-tag>
          <span v-if="row.pid" class="pid-hint mono">pid {{ row.pid }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Consolidator" width="150">
        <template #default="{ row }">
          <el-tag :type="consolidatorTagType(row.consolidator)" size="small" effect="plain">
            {{ consolidatorLabel(row.consolidator) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Palace" min-width="180">
        <template #default="{ row }">
          <span v-if="row.palace_path" class="mono path">{{ row.palace_path }}</span>
          <span v-else class="muted">(default)</span>
          <el-tag
            v-if="row.palace_initialized"
            type="success" effect="plain" size="small"
            style="margin-left: 4px"
          >✓</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="360" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :loading="busy[row.user_id]" @click="onInit(row.user_id)">init</el-button>
          <el-button
            size="small" type="success"
            :disabled="row.enabled"
            :loading="busy[row.user_id]"
            @click="onStart(row.user_id)"
          >start</el-button>
          <el-button
            size="small" type="warning"
            :disabled="!row.enabled"
            :loading="busy[row.user_id]"
            @click="onStop(row.user_id)"
          >stop</el-button>
          <el-button
            size="small"
            :loading="busy[row.user_id]"
            @click="openConsolidator(row.user_id, row.consolidator)"
          >consolidator</el-button>
          <el-button
            v-if="row.consolidator?.configured"
            size="small" type="danger" link
            :loading="busy[row.user_id]"
            @click="onRemoveConsolidator(row.user_id)"
          >移除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogOpen" title="新建 Memory User" width="480px">
      <el-form label-position="top" :model="form">
        <el-form-item label="User ID" required>
          <el-input v-model="form.id" placeholder="例：alice" maxlength="64" />
        </el-form-item>
        <el-form-item label="Port" required>
          <el-input-number v-model="form.port" :min="1024" :max="65535" />
        </el-form-item>
        <el-form-item label="Palace 路径（留空则用默认）">
          <el-input v-model="form.palace_path" placeholder="可选" />
        </el-form-item>
        <el-form-item label="立即启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="consDialogOpen" :title="`Consolidator — ${consUserId}`" width="520px">
      <el-form label-position="top" :model="consForm">
        <el-form-item label="启用（opt-in，会消耗 LLM）">
          <el-switch v-model="consForm.enabled" />
        </el-form-item>
        <el-form-item label="interval_hours">
          <el-input-number v-model="consForm.interval_hours" :min="0.1" :step="0.5" />
        </el-form-item>
        <el-form-item label="window_days">
          <el-input-number v-model="consForm.window_days" :min="1" />
        </el-form-item>
        <el-form-item label="min_drawers">
          <el-input-number v-model="consForm.min_drawers" :min="1" />
        </el-form-item>
        <el-form-item label="min_confidence">
          <el-input-number v-model="consForm.min_confidence" :min="0" :max="1" :step="0.05" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="consDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="consSubmitting" @click="onSaveConsolidator">保存并 SIGHUP</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.subtitle {
  font-size: 12px;
  color: var(--eid-text-muted);
  margin-top: 4px;
  font-family: var(--eid-font-mono);
}
.actions { display: flex; gap: 8px; }
.user-id { font-weight: 600; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.path { color: var(--eid-text-secondary); }
.pid-hint {
  display: block;
  margin-top: 3px;
  color: var(--eid-text-muted);
  font-size: 11px;
}
.muted { color: var(--eid-text-muted); font-size: 12px; }
</style>
