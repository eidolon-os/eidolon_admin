<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import TableSkeleton from '@/modules/common/TableSkeleton.vue'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

const store = useOwnersStore()
const router = useRouter()
const route = useRoute()
const creating = ref(false)
const createOpen = ref(false)
const form = reactive({
  owner_id: '',
  display_name: '',
  kind: 'person',
})

onMounted(async () => {
  await store.load()
  openCreateFromQuery()
})

watch(() => route.query.create, openCreateFromQuery)

async function openOwner(ownerId: string) {
  store.setCurrent(ownerId)
  await router.push({ name: 'owner-workspace', params: { ownerId, section: 'overview' } })
}

function openCreate() {
  form.owner_id = ''
  form.display_name = ''
  form.kind = 'person'
  createOpen.value = true
}

function openCreateFromQuery() {
  if (route.query.create !== '1') return
  openCreate()
  const query = { ...route.query }
  delete query.create
  router.replace({ name: 'owners', query })
}

async function submitCreate() {
  const ownerId = form.owner_id.trim()
  if (!ownerId) {
    ElMessage.warning('请输入 owner_id')
    return
  }
  creating.value = true
  try {
    const owner = await store.createAndSelect({
      owner_id: ownerId,
      display_name: form.display_name.trim() || ownerId,
      kind: form.kind,
    })
    createOpen.value = false
    await openOwner(owner.owner_id)
  } catch (e) {
    ElMessage.error(`创建 Owner 失败: ${extractErrorMessage(e)}`)
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <CatalogPage
    title="Owners"
    hint="业务数据以 owner 为主权边界；选择一个 owner 后进入对应 workspace 管理 companion、设备、会话、记忆域、任务和审计事件。"
  >
    <template #head-actions>
      <el-button size="small" @click="store.load(true)">刷新</el-button>
      <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建 Owner</el-button>
    </template>

    <TableSkeleton v-if="store.loading && !store.owners.length" :rows="6" />
    <el-table v-else :data="store.owners" v-loading="store.loading" size="small" stripe>
      <el-table-column prop="owner_id" label="owner_id" min-width="180" />
      <el-table-column prop="display_name" label="显示名" min-width="160" />
      <el-table-column prop="kind" label="类型" width="110">
        <template #default="{ row }">
          <el-tag size="small">{{ row.kind }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="status" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="created" width="190">
        <template #default="{ row }">{{ formatTimestamp(row.created_at) }}</template>
      </el-table-column>
      <el-table-column align="right" width="120">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="openOwner(row.owner_id)">进入</el-button>
        </template>
      </el-table-column>
    </el-table>

    <section v-if="!store.loading && store.owners.length === 0" class="empty-onboarding">
      <el-empty description="还没有 owner" />
      <el-button type="primary" :icon="Plus" @click="openCreate">创建第一个 Owner</el-button>
    </section>

    <el-dialog v-model="createOpen" title="Create Owner" width="520px">
      <el-form label-position="top" class="create-form" @submit.prevent="submitCreate">
        <div class="form-grid">
          <el-form-item label="owner_id">
            <el-input v-model="form.owner_id" placeholder="owner-default" />
          </el-form-item>
          <el-form-item label="显示名">
            <el-input v-model="form.display_name" placeholder="Manson" />
          </el-form-item>
          <el-form-item label="类型">
            <el-select v-model="form.kind">
              <el-option label="person" value="person" />
              <el-option label="family" value="family" />
              <el-option label="team" value="team" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建 Owner</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
.empty-onboarding {
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
  padding: 16px;
}
.create-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 720px;
  margin: 0 auto 8px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}
</style>
