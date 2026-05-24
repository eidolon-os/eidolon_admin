<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Promotion } from '@element-plus/icons-vue'
import { listCommands, listDevices, sendCommand, type AdminCommand, type AdminDevice } from '@/api/hub'
import JsonViewer from '@/modules/common/JsonViewer.vue'

const items = ref<AdminCommand[]>([])
const devices = ref<AdminDevice[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const form = ref({
  device_id: '',
  topic: '',
  payloadJson: '{}',
})
const sending = ref(false)
const detail = ref<AdminCommand | null>(null)

async function load() {
  loading.value = true
  try {
    const [c, d] = await Promise.all([listCommands(50), listDevices()])
    items.value = c
    devices.value = d
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (!loading.value) load() }, 5_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

async function onSend() {
  if (!form.value.device_id || !form.value.topic) {
    ElMessage.warning('请填写 device_id + topic')
    return
  }
  let payload: Record<string, any> = {}
  try {
    payload = JSON.parse(form.value.payloadJson || '{}')
  } catch (e: any) {
    ElMessage.error(`payload JSON 解析失败：${e.message}`)
    return
  }
  sending.value = true
  try {
    const r = await sendCommand(form.value.device_id, { topic: form.value.topic, payload })
    ElMessage.success(`命令已下发：${r.command_id}`)
    form.value.payloadJson = '{}'
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    sending.value = false
  }
}

function statusTagType(s?: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === 'ack' || s === 'delivered') return 'success'
  if (s === 'pending' || s === 'sent') return 'info'
  if (s === 'failed' || s === 'timeout') return 'danger'
  return 'warning'
}
</script>

<template>
  <div class="page">
    <h2 class="title">Commands</h2>

    <el-card>
      <template #header>下发命令</template>
      <el-form>
        <div class="form-grid">
          <el-form-item label="Device">
            <el-select
              v-model="form.device_id"
              filterable allow-create placeholder="选择 device_id"
              style="width: 100%"
            >
              <el-option
                v-for="d in devices" :key="d.device_id"
                :value="d.device_id"
                :label="d.device_id"
              >
                <span class="mono">{{ d.device_id }}</span>
                <span class="muted" style="margin-left: 8px">{{ d.status || '?' }}</span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="Topic">
            <el-input v-model="form.topic" placeholder="如：lk.notify" />
          </el-form-item>
        </div>
        <el-form-item label="Payload (JSON)">
          <el-input v-model="form.payloadJson" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Promotion" :loading="sending" @click="onSend">发送</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header>
        <div class="bar">
          <span>最近命令</span>
          <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>

      <el-table :data="items" v-loading="loading" stripe>
        <el-table-column label="ID" width="220">
          <template #default="{ row }"><span class="mono">{{ row.command_id }}</span></template>
        </el-table-column>
        <el-table-column label="Device" width="180">
          <template #default="{ row }"><span class="mono">{{ row.device_id }}</span></template>
        </el-table-column>
        <el-table-column label="Topic" prop="topic" width="160" />
        <el-table-column label="Status" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Created" width="200" prop="created_at" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" link @click="detail = row">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer
      :model-value="!!detail"
      @update:model-value="(v: boolean) => { if (!v) detail = null }"
      :title="detail ? `Command · ${detail.command_id}` : ''"
      size="50%"
      direction="rtl"
    >
      <JsonViewer v-if="detail" :data="detail" />
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.title { margin: 0 0 16px 0; font-size: 18px; font-weight: 600; }
.bar { display: flex; justify-content: space-between; align-items: center; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.muted { color: var(--eid-text-muted); font-size: 11px; }
</style>
