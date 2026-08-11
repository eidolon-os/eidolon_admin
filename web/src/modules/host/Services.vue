<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  changeHostService,
  hostServiceTagType,
  listHostServices,
  type HostService,
} from '@/api/hostServices'

const services = ref<HostService[]>([])
const driver = ref('')
const loading = ref(false)
const error = ref('')
const busy = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  loading.value = true
  try {
    const page = await listHostServices()
    services.value = page.services
    driver.value = page.driver
    error.value = ''
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}

async function restart(service: HostService) {
  busy.value = service.service_id
  try {
    // The revision from the table, not a fresh read: a stale row must lose.
    await changeHostService(service.service_id, 'restart', service.revision)
    ElMessage.success(`${service.service_id} 已请求重启`)
    await refresh()
  } catch (exc: any) {
    ElMessage.error(exc?.message ?? String(exc))
  } finally {
    busy.value = null
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 5000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="host-services">
    <div class="header">
      <div>
        <h2>Host Services</h2>
        <p class="hint">
          由 eidolond 管理，Mac 与 Pi 同一接口；发布、激活与回滚仍属于 eidolon-ops。
        </p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新</el-button>
    </div>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" />

    <el-table v-else :data="services" v-loading="loading" row-key="service_id">
      <el-table-column prop="service_id" label="Service" min-width="200" />
      <el-table-column label="State" width="130">
        <template #default="{ row }">
          <el-tag :type="hostServiceTagType(row.runtime_state)" disable-transitions>
            {{ row.runtime_state }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Desired" width="110">
        <template #default="{ row }">{{ row.enabled ? 'enabled' : 'disabled' }}</template>
      </el-table-column>
      <el-table-column prop="revision" label="Rev" width="80" />
      <el-table-column prop="detail" label="Detail" min-width="220" />
      <el-table-column label="" width="110" align="right">
        <template #default="{ row }">
          <el-button
            size="small"
            :loading="busy === row.service_id"
            @click="restart(row)"
          >
            重启
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 4px 0 0;
}
</style>
