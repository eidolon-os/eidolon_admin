<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  admitDevice,
  getOwnerInventory,
  type DeviceAdmissionResult,
  type OwnerInventory,
} from '@/api/controlPlane'

const ownerId = ref('')
const deviceId = ref('')
const companionId = ref('')
const requestId = ref('')
const expectedRevision = ref(0)
const replaceExisting = ref(false)
const hubCredential = ref('')
const loadingInventory = ref(false)
const submitting = ref(false)
const inventory = ref<OwnerInventory | null>(null)
const result = ref<DeviceAdmissionResult | null>(null)
const error = ref('')

const mountsByDevice = computed(() =>
  new Map((inventory.value?.mounts || []).map((mount) => [mount.device_id, mount])),
)
const bodiesByDevice = computed(() =>
  new Map((inventory.value?.body_endpoints || []).map((body) => [body.device_id, body])),
)
const deviceRows = computed(() =>
  (inventory.value?.claims || []).map((claim) => ({
    device_id: claim.device_ref.device_instance_id,
    display_name: claim.device_ref.device_instance_id,
    lifecycle_state: claim.state,
  })),
)

function detailOf(value: any): string {
  const detail = value?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.detail) return detail.detail
  const failed = value?.response?.data?.steps?.slice?.().reverse().find((step: any) => step.failure)
  if (failed?.failure?.detail) return failed.failure.detail
  return value?.message || String(value)
}

async function loadInventory() {
  if (!ownerId.value.trim() || !hubCredential.value.trim()) {
    ElMessage.warning('Owner ID 和 Hub Bearer credential 均为必填')
    return
  }
  loadingInventory.value = true
  error.value = ''
  try {
    inventory.value = await getOwnerInventory(ownerId.value.trim(), hubCredential.value.trim())
  } catch (value) {
    error.value = detailOf(value)
  } finally {
    loadingInventory.value = false
  }
}

async function submitWorkflow() {
  if (!ownerId.value.trim() || !deviceId.value.trim() || !requestId.value.trim() || !hubCredential.value.trim()) {
    ElMessage.warning('Owner、Device、Request ID 和 Hub credential 均为必填')
    return
  }
  submitting.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await admitDevice({
      request_id: requestId.value.trim(),
      owner_id: ownerId.value.trim(),
      device_id: deviceId.value.trim(),
      companion_id: companionId.value.trim() || undefined,
      expected_mount_revision: expectedRevision.value,
      replace_existing_mount: replaceExisting.value,
    }, hubCredential.value.trim())
    if (result.value.outcome === 'completed') ElMessage.success('Workflow 已完成')
    else if (result.value.outcome === 'retry_required') ElMessage.warning('Workflow 部分成功；请使用相同 Request ID 重试')
    else ElMessage.error('Workflow 已阻塞；请检查冲突并由操作员处理')
    await loadInventory()
  } catch (value) {
    error.value = detailOf(value)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="page control-plane">
    <header class="page-head">
      <div>
        <p class="eyebrow">EIDOLON OS CONTROL PLANE</p>
        <h1>Device Admission & Mount</h1>
        <p class="hint">Hub 负责准入，Kernel 负责 Mount/Attachment；Admin 只编排公开契约。</p>
      </div>
    </header>

    <el-alert
      title="该流程不是分布式事务"
      type="info"
      :closable="false"
      description="部分成功会保留为安全中间态。进程重启或响应丢失后，请使用相同 Request ID 重试。"
      show-icon
    />
    <el-alert v-if="error" class="error" :title="error" type="error" :closable="false" show-icon />

    <div class="grid">
      <el-card>
        <template #header><strong>Authority scope</strong></template>
        <el-form label-position="top">
          <el-form-item label="Owner ID">
            <el-input v-model="ownerId" placeholder="owner_..." />
          </el-form-item>
          <el-form-item label="Hub management credential">
            <el-input v-model="hubCredential" type="password" show-password placeholder="Bearer ey..." />
          </el-form-item>
          <el-button :loading="loadingInventory" @click="loadInventory">并发读取 Hub + Kernel</el-button>
        </el-form>
      </el-card>

      <el-card>
        <template #header><strong>Admission workflow</strong></template>
        <el-form label-position="top">
          <el-form-item label="Stable Request ID">
            <el-input v-model="requestId" placeholder="operator-20260806-001" />
          </el-form-item>
          <el-form-item label="Device ID">
            <el-input v-model="deviceId" placeholder="device-..." />
          </el-form-item>
          <el-form-item label="Companion ID（可选）">
            <el-input v-model="companionId" placeholder="companion-..." />
          </el-form-item>
          <div class="row">
            <el-form-item label="Expected Mount revision">
              <el-input-number v-model="expectedRevision" :min="0" />
            </el-form-item>
            <el-form-item label="Replace existing Mount">
              <el-switch v-model="replaceExisting" />
            </el-form-item>
          </div>
          <el-button type="primary" :loading="submitting" @click="submitWorkflow">执行编排</el-button>
        </el-form>
      </el-card>
    </div>

    <el-card v-if="result" class="result-card">
      <template #header>
        <div class="result-head">
          <strong>Workflow {{ result.request_id }}</strong>
          <el-tag :type="result.outcome === 'completed' ? 'success' : 'warning'">{{ result.outcome }}</el-tag>
        </div>
      </template>
      <el-steps :active="result.steps.filter((step) => ['committed', 'replayed'].includes(step.state)).length" finish-status="success">
        <el-step v-for="step in result.steps" :key="step.name" :title="step.name" :description="step.failure?.detail || `${step.state}${step.revision ? ` · r${step.revision}` : ''}`" />
      </el-steps>
    </el-card>

    <el-card v-if="inventory" class="inventory-card">
      <template #header>
        <div class="result-head">
          <strong>Owner inventory</strong>
          <el-tag :type="inventory.degraded ? 'warning' : 'success'">{{ inventory.degraded ? 'DEGRADED' : 'READY' }}</el-tag>
        </div>
      </template>
      <div class="source-row">
        <span>Hub {{ inventory.hub.state }} · {{ inventory.hub.latency_ms.toFixed(1) }} ms</span>
        <span>Kernel {{ inventory.kernel.state }} · {{ inventory.kernel.latency_ms.toFixed(1) }} ms</span>
      </div>
      <el-table :data="deviceRows" empty-text="Hub 未返回设备">
        <el-table-column prop="device_id" label="Device" min-width="180" />
        <el-table-column prop="display_name" label="Name" min-width="150" />
        <el-table-column prop="lifecycle_state" label="Admission" width="150" />
        <el-table-column label="Mount" min-width="180">
          <template #default="scope">
            <template v-if="mountsByDevice.get(scope.row.device_id)">
              r{{ mountsByDevice.get(scope.row.device_id)?.revision }} ·
              {{ mountsByDevice.get(scope.row.device_id)?.active ? 'active' : 'inactive' }}
            </template>
            <span v-else>unmounted</span>
          </template>
        </el-table-column>
        <el-table-column label="Body assignment" min-width="260">
          <template #default="scope">
            <template v-if="bodiesByDevice.get(scope.row.device_id)?.assignment">
              a{{ bodiesByDevice.get(scope.row.device_id)?.assignment?.revision }}/g{{
                bodiesByDevice.get(scope.row.device_id)?.assignment?.generation
              }}
              ·
              {{ bodiesByDevice.get(scope.row.device_id)?.assignment?.companion_id || 'nobody' }}
              ·
              {{ bodiesByDevice.get(scope.row.device_id)?.assignment?.selection_provenance }}
            </template>
            <span v-else-if="bodiesByDevice.get(scope.row.device_id)">undecided</span>
            <span v-else>no body</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </section>
</template>

<style scoped>
.control-plane { display: flex; flex-direction: column; gap: 16px; }
.page-head h1 { margin: 4px 0; }
.hint { margin: 0; color: var(--eid-text-muted); }
.eyebrow { margin: 0; color: var(--eid-accent); font-family: var(--eid-font-mono); font-size: 11px; letter-spacing: .12em; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.row, .result-head, .source-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.result-card, .inventory-card { overflow: visible; }
.source-row { justify-content: flex-start; margin-bottom: 14px; color: var(--eid-text-muted); font-family: var(--eid-font-mono); }
.error { margin-top: 0; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
