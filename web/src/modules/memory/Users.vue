<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  createMemoryUser,
  initMemoryUserPalace,
  setMemoryUserEnabled,
  startMemoryUser,
  stopMemoryUser,
} from '@/api/memory'
import { useMemoryUserStore } from '@/stores/memoryUser'

const store = useMemoryUserStore()
const busy = ref<Record<string, boolean>>({})
const dialogOpen = ref(false)
const submitting = ref(false)
const form = ref({ id: '', port: 8030, enabled: true, palace_path: '' })

onMounted(() => store.load())

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
  // Pick next free port.
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

const usersFile = computed(() => store.users.length ? 'eidolon_memory/config/users.yaml' : '')
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

    <el-table :data="store.users" v-loading="store.loading" stripe>
      <el-table-column label="User ID" min-width="140">
        <template #default="{ row }">
          <span class="user-id">{{ row.user_id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Port" width="90">
        <template #default="{ row }"><span class="mono">{{ row.port }}</span></template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            size="small"
            :loading="busy[row.user_id]"
            @change="(v: boolean) => onEnable(row.user_id, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="状态" width="150">
        <template #default="{ row }">
          <el-tag
            v-if="row.enabled && row.agent_reachable"
            type="success" effect="dark" size="small"
          >RUNNING</el-tag>
          <el-tag
            v-else-if="row.enabled && !row.agent_reachable"
            type="warning" effect="dark" size="small"
          >STARTING / DOWN</el-tag>
          <el-tag v-else type="info" effect="plain" size="small">disabled</el-tag>
          <el-tag
            v-if="row.palace_initialized"
            type="success" effect="plain" size="small"
            style="margin-left: 4px"
          >palace ✓</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Palace path" min-width="240">
        <template #default="{ row }">
          <span v-if="row.palace_path" class="mono path">{{ row.palace_path }}</span>
          <span v-else class="muted">(default)</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280">
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
.muted { color: var(--eid-text-muted); font-size: 12px; }
</style>
