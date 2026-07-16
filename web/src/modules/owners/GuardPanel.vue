<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  activateOwnerFaceProfile,
  claimOwnerGuard,
  clearOwnerFaceProfile,
  createOwnerFaceProfileDraft,
  disableOwnerGuard,
  getOwnerFaceProfileStatus,
  listOwnerGuardBindings,
  listPendingGuardDevices,
  uploadOwnerFaceReference,
  type DeviceView,
  type GuardBindingView,
  type OwnerFacePose,
  type OwnerFaceProfileStatusResponse,
} from '@/api/eidolonData'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import { ElMessage, ElMessageBox } from 'element-plus'

const props = defineProps<{ ownerId: string }>()

const loading = ref(false)
const pending = ref<DeviceView[]>([])
const bindings = ref<GuardBindingView[]>([])
const busy = ref('')
const faceStatus = ref<OwnerFaceProfileStatusResponse>({ desired: null, deliveries: [] })
const faceFiles = ref<Partial<Record<OwnerFacePose, File>>>({})
const facePreviewUrls = ref<Partial<Record<OwnerFacePose, string>>>({})
const faceFormKey = ref(0)
const cameraOpen = ref(false)
const cameraPose = ref<OwnerFacePose | null>(null)
const cameraVideo = ref<HTMLVideoElement | null>(null)
const cameraError = ref('')
const cameraBusy = ref(false)
let cameraStream: MediaStream | null = null

const facePoses: Array<{ pose: OwnerFacePose; label: string; required: boolean }> = [
  { pose: 'front', label: '正脸', required: true },
  { pose: 'left', label: '左侧脸', required: true },
  { pose: 'right', label: '右侧脸', required: true },
  { pose: 'down', label: '低头', required: false },
  { pose: 'up', label: '抬头', required: false },
]

async function load() {
  if (!props.ownerId) return
  loading.value = true
  try {
    const [nextBindings, nextPending, nextFaceStatus] = await Promise.all([
      listOwnerGuardBindings(props.ownerId),
      listPendingGuardDevices(),
      getOwnerFaceProfileStatus(props.ownerId),
    ])
    bindings.value = nextBindings
    pending.value = nextPending
    faceStatus.value = nextFaceStatus
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function selectFaceFile(pose: OwnerFacePose, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) setFaceFile(pose, file)
  input.value = ''
}

function setFaceFile(pose: OwnerFacePose, file: File) {
  const previousUrl = facePreviewUrls.value[pose]
  if (previousUrl) URL.revokeObjectURL(previousUrl)
  faceFiles.value = { ...faceFiles.value, [pose]: file }
  facePreviewUrls.value = {
    ...facePreviewUrls.value,
    [pose]: URL.createObjectURL(file),
  }
}

function clearFaceFiles() {
  for (const url of Object.values(facePreviewUrls.value)) {
    if (url) URL.revokeObjectURL(url)
  }
  faceFiles.value = {}
  facePreviewUrls.value = {}
}

function stopCamera() {
  for (const track of cameraStream?.getTracks() || []) track.stop()
  cameraStream = null
  if (cameraVideo.value) cameraVideo.value.srcObject = null
  cameraBusy.value = false
}

async function openCamera(pose: OwnerFacePose) {
  stopCamera()
  cameraPose.value = pose
  cameraError.value = ''
  cameraOpen.value = true
  await nextTick()
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraError.value = '当前浏览器不支持摄像头，请改用“从文件选择”。'
    return
  }
  cameraBusy.value = true
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: 'user',
        width: { ideal: 640 },
        height: { ideal: 480 },
        aspectRatio: { ideal: 4 / 3 },
      },
    })
    if (!cameraOpen.value || cameraPose.value !== pose) {
      for (const track of stream.getTracks()) track.stop()
      return
    }
    cameraStream = stream
    if (cameraVideo.value) {
      cameraVideo.value.srcObject = stream
      await cameraVideo.value.play()
    }
  } catch (error) {
    cameraError.value = error instanceof DOMException && error.name === 'NotAllowedError'
      ? '摄像头权限被拒绝，请在 Chrome 地址栏中允许摄像头后重试。'
      : '无法打开摄像头，请检查设备占用或改用“从文件选择”。'
  } finally {
    cameraBusy.value = false
  }
}

async function captureFace() {
  const pose = cameraPose.value
  const video = cameraVideo.value
  if (!pose || !video || !video.videoWidth || !video.videoHeight) {
    cameraError.value = '摄像头画面尚未就绪，请稍后重试。'
    return
  }
  const canvas = document.createElement('canvas')
  canvas.width = 320
  canvas.height = 240
  const context = canvas.getContext('2d')
  if (!context) {
    cameraError.value = '浏览器无法生成照片。'
    return
  }
  const sourceRatio = video.videoWidth / video.videoHeight
  const targetRatio = canvas.width / canvas.height
  let sourceX = 0
  let sourceY = 0
  let sourceWidth = video.videoWidth
  let sourceHeight = video.videoHeight
  if (sourceRatio > targetRatio) {
    sourceWidth = video.videoHeight * targetRatio
    sourceX = (video.videoWidth - sourceWidth) / 2
  } else if (sourceRatio < targetRatio) {
    sourceHeight = video.videoWidth / targetRatio
    sourceY = (video.videoHeight - sourceHeight) / 2
  }
  context.drawImage(
    video,
    sourceX,
    sourceY,
    sourceWidth,
    sourceHeight,
    0,
    0,
    canvas.width,
    canvas.height,
  )
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(resolve, 'image/jpeg', 0.92)
  })
  if (!blob) {
    cameraError.value = '照片编码失败，请重试。'
    return
  }
  setFaceFile(pose, new File([blob], `${pose}-${Date.now()}.jpg`, { type: 'image/jpeg' }))
  cameraOpen.value = false
}

async function publishOwnerFaceProfile() {
  const missing = facePoses
    .filter((item) => item.required && !faceFiles.value[item.pose])
    .map((item) => item.label)
  if (missing.length) {
    ElMessage.warning(`请补齐必需照片：${missing.join('、')}`)
    return
  }
  busy.value = 'owner-face:publish'
  try {
    const draft = await createOwnerFaceProfileDraft(props.ownerId)
    for (const item of facePoses) {
      const file = faceFiles.value[item.pose]
      if (file) {
        await uploadOwnerFaceReference(
          props.ownerId,
          draft.profile_revision_id,
          item.pose,
          file,
        )
      }
    }
    await activateOwnerFaceProfile(props.ownerId, draft.profile_revision_id)
    clearFaceFiles()
    faceFormKey.value += 1
    ElMessage.success('Owner Face 新版本已进入设备下发队列')
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    busy.value = ''
  }
}

async function clearOwnerFace() {
  try {
    await ElMessageBox.confirm(
      '确认让所有绑定 Guard 清除本地 Owner 模板？离线设备会在重新上线后执行。',
      '清除 Owner Face',
      { type: 'warning' },
    )
  } catch {
    return
  }
  busy.value = 'owner-face:clear'
  try {
    await clearOwnerFaceProfile(props.ownerId)
    ElMessage.success('清除指令已进入设备下发队列')
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    busy.value = ''
  }
}

function deliveryTag(status: string) {
  if (status === 'applied') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'dispatched' || status === 'dispatching') return 'warning'
  return 'info'
}

function ownerPresence(binding: GuardBindingView) {
  const value = binding.status_json?.owner_presence
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { label: '等待信号', type: 'info' as const }
  }
  const expiresAt = typeof value.expires_at === 'string' ? Date.parse(value.expires_at) : 0
  if (value.state === 'present' && expiresAt > Date.now()) {
    return { label: 'Owner 在场', type: 'success' as const }
  }
  return { label: 'Owner 不在场', type: 'info' as const }
}

async function claim(device: DeviceView, replace = false) {
  if (!replace && bindings.value.some((binding) => binding.state === 'active')) {
    ElMessage.warning('当前 Owner 已有启用中的 Guard；请使用替换操作。')
    return
  }
  const key = `claim:${device.device_id}:${replace}`
  busy.value = key
  try {
    await claimOwnerGuard(props.ownerId, { device_id: device.device_id, replace })
    ElMessage.success(replace ? 'Guard 已替换' : 'Guard 已认领')
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    busy.value = ''
  }
}

async function disable(binding: GuardBindingView, revoke = false) {
  const verb = revoke ? '注销' : '停用'
  try {
    await ElMessageBox.confirm(`确认${verb}此 Guard？`, `${verb} Guard`, { type: 'warning' })
  } catch {
    return
  }
  busy.value = `${verb}:${binding.binding_id}`
  try {
    await disableOwnerGuard(props.ownerId, binding.binding_id, revoke)
    ElMessage.success(`Guard 已${verb}`)
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    busy.value = ''
  }
}

onMounted(load)
onBeforeUnmount(() => {
  stopCamera()
  clearFaceFiles()
})
watch(() => props.ownerId, load)
</script>

<template>
  <section class="guard-panel" v-loading="loading">
    <div class="section-head">
      <div>
        <h3>ATK Guard</h3>
        <p>Guard 只负责报告本地事实；策略与执行回执由 Hub 控制面处理。</p>
      </div>
      <el-button size="small" @click="load">刷新</el-button>
    </div>

    <el-table :data="bindings" size="small" stripe>
      <template #empty>当前 Owner 没有 Guard 绑定</template>
      <el-table-column prop="device_id" label="device" min-width="180" />
      <el-table-column prop="guard_companion_id" label="guard companion" min-width="170" />
      <el-table-column prop="policy_id" label="policy" min-width="150" />
      <el-table-column prop="state" label="state" width="110" />
      <el-table-column label="owner presence" width="130">
        <template #default="{ row }">
          <el-tag size="small" :type="ownerPresence(row).type">
            {{ ownerPresence(row).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="activated" width="180">
        <template #default="{ row }">{{ formatTimestamp(row.activated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <div v-if="row.state === 'active'" class="row-actions">
            <el-button size="small" :loading="busy === `停用:${row.binding_id}`" @click="disable(row)">停用</el-button>
            <el-button size="small" type="danger" :loading="busy === `注销:${row.binding_id}`" @click="disable(row, true)">注销</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <div class="section-head owner-face-head">
      <div>
        <h3>Owner Face Profile</h3>
        <p>Admin 保留归一化参考图；Guard 拉取后本地生成模板并立即丢弃图片。</p>
      </div>
      <el-button
        v-if="faceStatus.desired?.desired_state === 'active'"
        size="small"
        type="danger"
        plain
        :loading="busy === 'owner-face:clear'"
        @click="clearOwnerFace"
      >清除模板</el-button>
    </div>

    <el-card shadow="never" class="owner-face-status">
      <template v-if="faceStatus.desired">
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="desired">
            <el-tag :type="faceStatus.desired.desired_state === 'active' ? 'success' : 'danger'">
              {{ faceStatus.desired.desired_state }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="revision">{{ faceStatus.desired.revision }}</el-descriptions-item>
          <el-descriptions-item label="references">{{ faceStatus.desired.references.length }}</el-descriptions-item>
          <el-descriptions-item label="model" :span="2">{{ faceStatus.desired.model_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="updated">{{ formatTimestamp(faceStatus.desired.updated_at) }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="faceStatus.deliveries.length" class="delivery-list">
          <div v-for="delivery in faceStatus.deliveries" :key="delivery.delivery_id" class="delivery-row">
            <code>{{ delivery.device_id }}</code>
            <el-tag size="small" :type="deliveryTag(delivery.status)">{{ delivery.status }}</el-tag>
            <span v-if="delivery.last_error" class="delivery-error">{{ delivery.last_error }}</span>
          </div>
        </div>
        <p v-else class="empty-hint">尚无 active Guard 绑定；未来认领设备时会自动补投当前 desired 版本。</p>
      </template>
      <p v-else class="empty-hint">尚未配置 Owner Face Profile。</p>
    </el-card>

    <el-card shadow="never" class="owner-face-editor">
      <div :key="faceFormKey" class="pose-grid">
        <div v-for="item in facePoses" :key="item.pose" class="pose-upload">
          <span>{{ item.label }} <b v-if="item.required">必需</b><em v-else>可选</em></span>
          <div class="pose-preview">
            <img v-if="facePreviewUrls[item.pose]" :src="facePreviewUrls[item.pose]" :alt="`${item.label}预览`" />
            <span v-else>等待拍摄</span>
          </div>
          <el-button size="small" type="primary" plain @click="openCamera(item.pose)">
            {{ faceFiles[item.pose] ? '重拍' : '拍照' }}
          </el-button>
          <label class="file-fallback">
            从文件选择
            <input type="file" accept="image/jpeg,image/png,image/webp" @change="selectFaceFile(item.pose, $event)" />
          </label>
        </div>
      </div>
      <div class="privacy-note">使用当前浏览器摄像头逐张拍摄；必需正脸、左侧脸、右侧脸，可增加低头、抬头。原始照片只在浏览器与请求内存中处理，不落盘。</div>
      <el-button
        type="primary"
        :loading="busy === 'owner-face:publish'"
        @click="publishOwnerFaceProfile"
      >创建并下发新版本</el-button>
    </el-card>

    <el-dialog
      v-model="cameraOpen"
      :title="`拍摄${facePoses.find((item) => item.pose === cameraPose)?.label || ''}`"
      width="min(720px, 92vw)"
      destroy-on-close
      @closed="stopCamera"
    >
      <div class="camera-stage" v-loading="cameraBusy">
        <video ref="cameraVideo" autoplay muted playsinline />
        <div class="face-guide" aria-hidden="true" />
      </div>
      <el-alert v-if="cameraError" :title="cameraError" type="error" :closable="false" show-icon />
      <p class="camera-hint">保持头肩完整，脸部高度约占画面 35–50%，避免顶灯或窗户在身后；左右侧仅轻转 15–25°，双眼仍应可见。</p>
      <template #footer>
        <el-button @click="cameraOpen = false">取消</el-button>
        <el-button type="primary" :disabled="cameraBusy || !!cameraError" @click="captureFace">拍下这张</el-button>
      </template>
    </el-dialog>

    <div class="section-head pending-head">
      <div>
        <h3>待认领 Guard 设备</h3>
        <p>设备声明 guard capability 后仍需 Hub 批准和管理员显式认领。</p>
      </div>
    </div>
    <el-table :data="pending" size="small" stripe>
      <template #empty>暂无待认领 Guard 设备</template>
      <el-table-column prop="device_id" label="device_id" min-width="190" />
      <el-table-column prop="name" label="name" min-width="140" />
      <el-table-column prop="kind" label="kind" width="120" />
      <el-table-column label="protocol" min-width="130">
        <template #default="{ row }">{{ row.capabilities_json.guard?.protocol_versions?.join(', ') || '-' }}</template>
      </el-table-column>
      <el-table-column label="last seen" width="180">
        <template #default="{ row }">{{ formatTimestamp(row.last_seen_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <div class="row-actions">
            <el-button size="small" type="primary" :loading="busy === `claim:${row.device_id}:false`" @click="claim(row)">认领</el-button>
            <el-button size="small" type="warning" :loading="busy === `claim:${row.device_id}:true`" @click="claim(row, true)">替换</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.guard-panel { display: flex; flex-direction: column; gap: 12px; }
.section-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.section-head h3 { margin: 0; font-size: 15px; }
.section-head p { margin: 4px 0 0; color: var(--eid-text-secondary); font-size: 12px; }
.pending-head { margin-top: 8px; }
.row-actions { display: flex; gap: 6px; }
.owner-face-head { margin-top: 10px; }
.owner-face-status, .owner-face-editor { border-color: var(--eid-border-color); }
.pose-grid { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 8px; }
.pose-upload { display: flex; min-height: 180px; padding: 9px; border: 1px solid var(--eid-border-color); border-radius: 6px; flex-direction: column; gap: 7px; }
.pose-upload span { font-size: 13px; }
.pose-upload b { color: var(--el-color-danger); font-size: 11px; }
.pose-upload em { color: var(--eid-text-secondary); font-size: 11px; font-style: normal; }
.pose-preview { display: flex; overflow: hidden; width: 100%; aspect-ratio: 4 / 3; align-items: center; justify-content: center; border-radius: 4px; background: color-mix(in srgb, var(--eid-surface-color) 82%, black); color: var(--eid-text-secondary); }
.pose-preview img { width: 100%; height: 100%; object-fit: cover; }
.file-fallback { position: relative; align-self: center; color: var(--el-color-primary); cursor: pointer; font-size: 11px; }
.file-fallback input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.privacy-note, .empty-hint { margin: 10px 0; color: var(--eid-text-secondary); font-size: 12px; }
.camera-stage { position: relative; overflow: hidden; width: 100%; aspect-ratio: 4 / 3; border-radius: 8px; background: #05090b; }
.camera-stage video { width: 100%; height: 100%; object-fit: cover; transform: scaleX(-1); }
.face-guide { position: absolute; inset: 24% 34%; border: 2px solid rgb(87 225 255 / 80%); border-radius: 50%; box-shadow: 0 0 0 999px rgb(0 0 0 / 22%); pointer-events: none; }
.camera-hint { margin: 10px 0 0; color: var(--eid-text-secondary); font-size: 12px; }
.delivery-list { display: flex; margin-top: 10px; flex-direction: column; gap: 6px; }
.delivery-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.delivery-error { color: var(--el-color-danger); }
@media (max-width: 1100px) {
  .pose-grid { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
}
</style>
