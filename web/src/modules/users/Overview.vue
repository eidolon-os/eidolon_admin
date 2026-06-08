<script setup lang="ts">
/**
 * /users — admin's authoritative user list.
 *
 * Each row composes memory's per-user health, admin's KV (active_agent_id),
 * and the agent-id set. Create is gated by tenant existence; we keep
 * the form simple — palace_path defaults to memory's own derivation.
 */
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createUser,
  deleteUser,
  listUsers,
  setActiveAgent,
  updateUser,
  type UserView,
} from '@/api/users'
import { listTenants, type TenantSpec } from '@/api/tenants'
import { extractErrorMessage } from '@/utils/format'
import { userHealthDetail, userHealthLabel, userHealthType } from '@/utils/userHealth'
import CatalogPage from '@/modules/common/CatalogPage.vue'

const rows = ref<UserView[]>([])
const tenants = ref<TenantSpec[]>([])
const memoryAvailable = ref(true)
const loading = ref(false)
let refreshTimer: ReturnType<typeof setInterval> | null = null

const dialogOpen = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const form = reactive({
  user_id: '',
  tenant_id: 'default',
  display_name: '',
})
const submitting = ref(false)

async function refresh() {
  loading.value = true
  try {
    const [u, t] = await Promise.all([listUsers(), listTenants()])
    rows.value = u.users
    memoryAvailable.value = u.memory_available
    tenants.value = t
  } catch (e: any) {
    memoryAvailable.value = false
    ElMessage.error(`加载用户失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  form.user_id = ''
  form.tenant_id = tenants.value[0]?.tenant_id || 'default'
  form.display_name = ''
  dialogOpen.value = true
}

function openEdit(row: UserView) {
  dialogMode.value = 'edit'
  form.user_id = row.spec.user_id
  form.tenant_id = row.spec.tenant_id
  form.display_name = row.spec.display_name
  dialogOpen.value = true
}

async function submit() {
  if (!form.display_name.trim()) {
    ElMessage.warning('请输入显示名')
    return
  }
  submitting.value = true
  try {
    if (dialogMode.value === 'create') {
      if (!form.user_id.trim()) {
        ElMessage.warning('请输入 user_id')
        return
      }
      await createUser({
        user_id: form.user_id.trim(),
        tenant_id: form.tenant_id,
        display_name: form.display_name.trim(),
      })
      ElMessage.success('用户已创建')
    } else {
      await updateUser(form.user_id, {
        display_name: form.display_name.trim(),
      })
      ElMessage.success('已更新')
    }
    dialogOpen.value = false
    await refresh()
  } catch (e: any) {
    ElMessage.error(`提交失败: ${extractErrorMessage(e)}`)
  } finally {
    submitting.value = false
  }
}

async function remove(row: UserView) {
  try {
    await ElMessageBox.confirm(
      `确认删除用户 "${row.spec.user_id}"? 所有该用户的 agent 也会被级联删除, palace 会移入回收。`,
      '删除用户',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const res = await deleteUser(row.spec.user_id)
    ElMessage.success(
      res.palace_trashed_to
        ? `已删除, palace 已归档至 ${res.palace_trashed_to}`
        : '已删除',
    )
    await refresh()
  } catch (e: any) {
    ElMessage.error(`删除失败: ${extractErrorMessage(e)}`)
  }
}

async function setActive(row: UserView, agent_id: string) {
  try {
    await setActiveAgent(row.spec.user_id, agent_id)
    // Phase 33.B4: surface the "future sessions only" semantic. Channel
    // resolves device JWTs once per LK session (see resolver.py's
    // docstring) — switching active_agent does NOT hot-rotate the
    // currently-running conversation. Operators have repeatedly
    // mistaken the green "成功" toast for "the user is now talking to
    // the new agent right now"; this longer hint corrects that.
    ElMessage({
      type: 'success',
      message:
        '已设置 active agent — 仅对该用户的下一次新会话生效。' +
        '当前正在进行的会话仍走旧 agent,如需立即切换请走"撤销会话"。',
      duration: 6000,
      showClose: true,
    })
    await refresh()
  } catch (e: any) {
    ElMessage.error(`设置失败: ${extractErrorMessage(e)}`)
  }
}

onMounted(async () => {
  await refresh()
  refreshTimer = setInterval(() => {
    if (!loading.value) void refresh()
  }, 10_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <CatalogPage
    title="用户管理"
    hint="每个用户对应 memory 服务里的一个独立 palace 进程, 创建可能耗时 10-30s。用户必须先存在,才能为其创建 agent。"
  >
    <template #head-actions>
      <el-button :loading="loading" size="small" @click="refresh">刷新</el-button>
      <el-button type="primary" size="small" @click="openCreate">新建用户</el-button>
    </template>

    <el-alert
      v-if="!memoryAvailable"
      title="Memory 服务不可达"
      type="warning"
      :closable="false"
      description="无法查询用户健康状态。Memory 服务可能正在启动,请稍后刷新。"
    />

    <el-table v-loading="loading" :data="rows" stripe>
      <el-table-column prop="spec.user_id" label="User ID" width="180" />
      <el-table-column prop="spec.display_name" label="显示名" />
      <el-table-column prop="spec.tenant_id" label="Tenant" width="120" />
      <el-table-column label="健康" width="150">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="userHealthType(row.health)"
            :title="userHealthDetail(row.health)"
          >
            {{ userHealthLabel(row.health) }}
          </el-tag>
          <div v-if="row.health.note" class="health-note">{{ row.health.note }}</div>
        </template>
      </el-table-column>
      <el-table-column label="Active Agent" min-width="200">
        <template #default="{ row }">
          <el-select
            v-if="row.agent_ids.length > 0"
            :model-value="row.active_agent_id || ''"
            size="small"
            style="width: 100%"
            @change="(v: string) => v && setActive(row, v)"
          >
            <el-option label="(无)" value="" disabled />
            <el-option
              v-for="aid in row.agent_ids"
              :key="aid"
              :label="aid"
              :value="aid"
            />
          </el-select>
          <span v-else class="muted">无 agent</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" align="right">
        <template #default="{ row }">
          <el-button size="small" link @click="openEdit(row)">编辑</el-button>
          <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogOpen"
      :title="dialogMode === 'create' ? '新建用户' : '编辑用户'"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form label-width="100px">
        <el-form-item label="User ID">
          <el-input
            v-model="form.user_id"
            :disabled="dialogMode === 'edit'"
            placeholder="例如: alice"
          />
        </el-form-item>
        <el-form-item label="Tenant">
          <el-select v-model="form.tenant_id" :disabled="dialogMode === 'edit'">
            <el-option
              v-for="t in tenants"
              :key="t.tenant_id"
              :label="`${t.display_name} (${t.tenant_id})`"
              :value="t.tenant_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" />
        </el-form-item>
      </el-form>
      <p v-if="dialogMode === 'create'" class="dialog-hint">
        创建会启动 memory 子进程,可能耗时 10-30 秒,请耐心等待。
      </p>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
/* Layout chrome lives in <CatalogPage>. Page-local styles only. */
.muted { color: var(--eid-text-muted); font-size: 12px; }
.health-note {
  margin-top: 3px;
  color: var(--eid-text-muted);
  font-size: 11px;
  line-height: 1.2;
}
.dialog-hint { margin: 8px 0 0 100px; font-size: 12px; color: var(--eid-text-muted); }
</style>
