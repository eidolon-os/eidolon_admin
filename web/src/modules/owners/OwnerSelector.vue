<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage } from '@/utils/format'

const store = useOwnersStore()
const route = useRoute()
const router = useRouter()

const dialogOpen = ref(false)
const submitting = ref(false)
const form = reactive({
  owner_id: '',
  display_name: '',
  kind: 'person',
})

onMounted(async () => {
  await store.load()
  syncFromRoute()
})

watch(() => route.params.ownerId, syncFromRoute)

function syncFromRoute() {
  const ownerId = typeof route.params.ownerId === 'string' ? route.params.ownerId : ''
  if (ownerId && ownerId !== store.currentId) store.setCurrent(ownerId)
}

async function handleSelect(value: string | number) {
  const ownerId = String(value)
  store.setCurrent(ownerId)
  if (ownerId) {
    await router.push({ name: 'owner-workspace', params: { ownerId, section: 'overview' } })
  }
}

function openCreate() {
  form.owner_id = ''
  form.display_name = ''
  form.kind = 'person'
  dialogOpen.value = true
}

async function submit() {
  const ownerId = form.owner_id.trim()
  if (!ownerId) {
    ElMessage.warning('请输入 owner_id')
    return
  }
  submitting.value = true
  try {
    const owner = await store.createAndSelect({
      owner_id: ownerId,
      display_name: form.display_name.trim() || ownerId,
      kind: form.kind,
    })
    dialogOpen.value = false
    ElMessage.success('Owner 已创建')
    await router.push({
      name: 'owner-workspace',
      params: { ownerId: owner.owner_id, section: 'overview' },
    })
  } catch (e) {
    ElMessage.error(`创建 Owner 失败: ${extractErrorMessage(e)}`)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="owner-selector">
    <el-select
      :model-value="store.currentId"
      size="small"
      filterable
      placeholder="Select owner"
      :loading="store.loading"
      class="owner-select"
      @change="handleSelect"
    >
      <el-option
        v-for="owner in store.owners"
        :key="owner.owner_id"
        :label="owner.display_name || owner.owner_id"
        :value="owner.owner_id"
      >
        <span>{{ owner.display_name || owner.owner_id }}</span>
        <small>{{ owner.kind }} · {{ owner.owner_id }}</small>
      </el-option>
    </el-select>
    <el-button size="small" :icon="Plus" @click="openCreate" />

    <el-dialog v-model="dialogOpen" title="Create Owner" width="420px" append-to-body>
      <el-form label-width="92px" @submit.prevent="submit">
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
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.owner-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}
.owner-select {
  width: 220px;
}
:deep(.el-select-dropdown__item) {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
:deep(.el-select-dropdown__item small) {
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
}
</style>
