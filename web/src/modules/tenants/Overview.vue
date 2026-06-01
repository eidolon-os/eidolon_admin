<script setup lang="ts">
/**
 * /tenants — admin's tenant CRUD page.
 *
 * Lowest-traffic page; most installs run on the seeded ``default``
 * tenant. We still surface it so operators can create extra tenants
 * when isolating environments (e.g. demo vs staging within one admin).
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createTenant,
  deleteTenant,
  listTenants,
  updateTenant,
  type TenantSpec,
} from '@/api/tenants'
import { extractErrorMessage } from '@/utils/format'

const rows = ref<TenantSpec[]>([])
const loading = ref(false)
const dialogOpen = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const form = reactive({ tenant_id: '', display_name: '' })
const submitting = ref(false)

async function refresh() {
  loading.value = true
  try {
    rows.value = await listTenants()
  } catch (e: any) {
    ElMessage.error(`加载租户失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dialogMode.value = 'create'
  form.tenant_id = ''
  form.display_name = ''
  dialogOpen.value = true
}

function openEdit(row: TenantSpec) {
  dialogMode.value = 'edit'
  form.tenant_id = row.tenant_id
  form.display_name = row.display_name
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
      if (!form.tenant_id.trim()) {
        ElMessage.warning('请输入 tenant_id')
        return
      }
      await createTenant({
        tenant_id: form.tenant_id.trim(),
        display_name: form.display_name.trim(),
      })
      ElMessage.success('租户已创建')
    } else {
      await updateTenant(form.tenant_id, {
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

async function remove(row: TenantSpec) {
  try {
    await ElMessageBox.confirm(
      `确认删除租户 "${row.tenant_id}"? 该租户必须先没有用户。`,
      '删除租户',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteTenant(row.tenant_id)
    ElMessage.success('已删除')
    await refresh()
  } catch (e: any) {
    ElMessage.error(`删除失败: ${extractErrorMessage(e)}`)
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h2>租户管理</h2>
        <p class="hint">
          租户用于在同一 admin 实例上隔离不同环境的数据。默认 <code>default</code> 已自动创建,
          一般无需新增。
        </p>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" size="small" @click="refresh">刷新</el-button>
        <el-button type="primary" size="small" @click="openCreate">新建租户</el-button>
      </div>
    </header>

    <el-table v-loading="loading" :data="rows" stripe>
      <el-table-column prop="tenant_id" label="Tenant ID" width="220" />
      <el-table-column prop="display_name" label="显示名" />
      <el-table-column prop="created_at" label="创建时间" width="220" />
      <el-table-column label="操作" width="200" align="right">
        <template #default="{ row }">
          <el-button size="small" link @click="openEdit(row)">编辑</el-button>
          <el-button
            size="small"
            link
            type="danger"
            :disabled="row.tenant_id === 'default'"
            @click="remove(row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogOpen"
      :title="dialogMode === 'create' ? '新建租户' : '编辑租户'"
      width="480px"
    >
      <el-form label-width="100px">
        <el-form-item label="Tenant ID">
          <el-input
            v-model="form.tenant_id"
            :disabled="dialogMode === 'edit'"
            placeholder="例如: demo"
          />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" placeholder="Demo Environment" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; }
.page-head h2 { margin: 0; font-size: 18px; color: var(--eid-text-primary); }
.hint { margin: 6px 0 0; font-size: 12px; color: var(--eid-text-muted); max-width: 720px; }
.head-actions { display: flex; gap: 8px; }
code { font-family: var(--eid-font-mono); padding: 1px 6px; background: var(--eid-bg-panel); border-radius: 3px; }
</style>
