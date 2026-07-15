<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Plus, Switch } from '@element-plus/icons-vue'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import TableSkeleton from '@/modules/common/TableSkeleton.vue'
import { useOwnersStore } from '@/stores/owners'
import type { OwnerDeleteResponse, OwnerView } from '@/api/eidolonData'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

const store = useOwnersStore()
const router = useRouter()
const route = useRoute()
const creating = ref(false)
const saving = ref(false)
const deleting = ref(false)
const createOpen = ref(false)
const editOpen = ref(false)
const deleteOpen = ref(false)
const editingId = ref('')
const deletingOwner = ref<OwnerView | null>(null)
const deleteConfirmText = ref('')
const deleteResult = ref<OwnerDeleteResponse | null>(null)
const createForm = reactive({ owner_id: '', display_name: '', kind: 'person' })
const editForm = reactive({ display_name: '', kind: 'person' })

onMounted(async () => {
  await store.load()
  openCreateFromQuery()
})

watch(() => route.query.create, openCreateFromQuery)

function openCreateFromQuery() {
  if (route.query.create !== '1') return
  openCreate()
  const query = { ...route.query }
  delete query.create
  void router.replace({ name: 'spaces', query })
}

function openCreate() {
  createForm.owner_id = ''
  createForm.display_name = ''
  createForm.kind = 'person'
  createOpen.value = true
}

async function submitCreate() {
  const ownerId = createForm.owner_id.trim()
  if (!ownerId) {
    ElMessage.warning('请输入空间 ID')
    return
  }
  creating.value = true
  try {
    const owner = await store.createAndSelect({
      owner_id: ownerId,
      display_name: createForm.display_name.trim() || ownerId,
      kind: createForm.kind,
    })
    createOpen.value = false
    ElMessage.success('空间已创建，继续创建主伙伴')
    await router.push({ name: 'companion-create', query: { owner_id: owner.owner_id } })
  } catch (error) {
    ElMessage.error(`创建空间失败: ${extractErrorMessage(error)}`)
  } finally {
    creating.value = false
  }
}

function openEdit(owner: OwnerView) {
  editingId.value = owner.owner_id
  editForm.display_name = owner.display_name
  editForm.kind = owner.kind
  editOpen.value = true
}

async function submitEdit() {
  if (!editingId.value || !editForm.display_name.trim()) {
    ElMessage.warning('请输入显示名')
    return
  }
  saving.value = true
  try {
    await store.updateLocal(editingId.value, {
      display_name: editForm.display_name.trim(),
      kind: editForm.kind,
    })
    editOpen.value = false
    ElMessage.success('空间信息已更新')
  } catch (error) {
    ElMessage.error(`更新失败: ${extractErrorMessage(error)}`)
  } finally {
    saving.value = false
  }
}

async function selectSpace(owner: OwnerView) {
  store.setCurrent(owner.owner_id)
  await router.push({ name: 'home', query: { owner_id: owner.owner_id } })
}

async function archiveSpace(owner: OwnerView) {
  try {
    await ElMessageBox.confirm(
      `归档 ${owner.display_name || owner.owner_id}？数据会保留，但该空间将变为 archived 状态。`,
      '归档 Eidolon 空间',
      { type: 'warning', confirmButtonText: '归档' },
    )
  } catch {
    return
  }
  try {
    await store.archiveLocal(owner.owner_id)
    ElMessage.success('空间已归档')
  } catch (error) {
    ElMessage.error(`归档失败: ${extractErrorMessage(error)}`)
  }
}

function openDelete(owner: OwnerView) {
  deletingOwner.value = owner
  deleteConfirmText.value = ''
  deleteResult.value = null
  deleteOpen.value = true
}

async function submitDelete() {
  const owner = deletingOwner.value
  if (!owner || deleteConfirmText.value !== owner.owner_id) return
  deleting.value = true
  try {
    deleteResult.value = await store.deleteLocal(owner.owner_id, deleteConfirmText.value)
    if (route.query.owner_id === owner.owner_id) {
      await router.replace({ name: 'spaces', query: { owner_id: store.currentId || undefined } })
    }
    ElMessage.success('空间已备份并删除')
  } catch (error) {
    ElMessage.error(`删除失败: ${extractErrorMessage(error)}`)
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <CatalogPage
    title="Eidolon 空间"
    hint="Owner 是数据主权边界。这里统一管理个人、家庭或团队空间；具体伙伴、设备和活动从左侧对应功能进入。"
  >
    <template #head-actions>
      <el-button size="small" @click="store.load(true)">刷新</el-button>
      <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建空间</el-button>
    </template>

    <TableSkeleton v-if="store.loading && !store.owners.length" :rows="6" />
    <el-table v-else :data="store.owners" v-loading="store.loading" size="small" stripe>
      <el-table-column label="空间" min-width="220">
        <template #default="{ row }">
          <div class="space-name">
            <strong>{{ row.display_name || row.owner_id }}</strong>
            <code>{{ row.owner_id }}</code>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="kind" label="类型" width="110">
        <template #default="{ row }"><el-tag size="small">{{ row.kind }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="190">
        <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" align="right" width="330" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :icon="Switch" @click="selectSpace(row)">进入</el-button>
          <el-button size="small" :icon="Edit" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" :disabled="row.status === 'archived'" @click="archiveSpace(row)">归档</el-button>
          <el-button size="small" type="danger" plain :icon="Delete" @click="openDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <section v-if="!store.loading && store.owners.length === 0" class="empty-onboarding">
      <el-empty description="还没有 Eidolon 空间" />
      <el-button type="primary" :icon="Plus" @click="openCreate">创建第一个空间</el-button>
    </section>

    <el-dialog v-model="createOpen" title="新建 Eidolon 空间 · 1/2" width="520px" append-to-body>
      <el-alert class="dialog-alert" type="info" :closable="false" title="创建空间后，将继续创建这个空间的主伙伴。" />
      <el-form label-position="top" @submit.prevent="submitCreate">
        <el-form-item label="空间 ID"><el-input v-model="createForm.owner_id" placeholder="owner-default" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="createForm.display_name" placeholder="我的 Eidolon" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="createForm.kind">
            <el-option label="个人" value="person" />
            <el-option label="家庭" value="family" />
            <el-option label="团队" value="team" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">下一步：创建伙伴</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editOpen" title="编辑 Eidolon 空间" width="480px" append-to-body>
      <el-form label-position="top" @submit.prevent="submitEdit">
        <el-form-item label="空间 ID"><el-input :model-value="editingId" disabled /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="editForm.display_name" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="editForm.kind">
            <el-option label="个人" value="person" />
            <el-option label="家庭" value="family" />
            <el-option label="团队" value="team" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deleteOpen" title="备份并删除 Eidolon 空间" width="620px" append-to-body :close-on-click-modal="!deleting">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="删除会移除该空间的伙伴、设备、记忆、会话、任务和事件；服务端会先生成备份。"
      />
      <el-form v-if="!deleteResult" label-position="top" class="delete-form">
        <el-form-item :label="`输入空间 ID 确认：${deletingOwner?.owner_id || ''}`">
          <el-input v-model="deleteConfirmText" :disabled="deleting" autocomplete="off" />
        </el-form-item>
      </el-form>
      <el-result v-else icon="success" title="空间已删除" sub-title="备份已完成">
        <template #extra><code class="backup-path">{{ deleteResult.backup?.path || '备份路径未返回' }}</code></template>
      </el-result>
      <template #footer>
        <el-button :disabled="deleting" @click="deleteOpen = false">{{ deleteResult ? '关闭' : '取消' }}</el-button>
        <el-button
          v-if="!deleteResult"
          type="danger"
          :disabled="deleteConfirmText !== deletingOwner?.owner_id"
          :loading="deleting"
          @click="submitDelete"
        >备份并删除</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
.empty-onboarding { border: 1px solid var(--eid-border); border-radius: 8px; background: var(--eid-bg-panel); padding: 16px; }
.space-name { display: flex; flex-direction: column; gap: 3px; }
.space-name code { color: var(--eid-text-muted); font-size: 11px; }
.dialog-alert { margin-bottom: 16px; }
.delete-form { margin-top: 18px; }
.backup-path { display: block; max-width: 100%; overflow-wrap: anywhere; color: var(--eid-text-secondary); }
</style>
