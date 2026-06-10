<script setup lang="ts">
/**
 * /users — admin's authoritative user list.
 *
 * Each row composes memory's per-user health, admin's KV (active_agent_id),
 * and the agent-id set. Create is gated by tenant existence; we keep
 * the form simple — palace_path defaults to memory's own derivation.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  cancelVoiceprintEnrollment,
  completeVoiceprintEnrollment,
  createUser,
  createVoiceprintEnrollment,
  deleteVoiceprint,
  deleteUser,
  getVoiceprint,
  listUsers,
  setActiveAgent,
  testVoiceprint,
  updateUser,
  uploadVoiceprintSample,
  type UserView,
  type VoiceprintEnrollmentResponse,
  type VoiceprintSampleResponse,
  type VoiceprintStatusResponse,
  type VoiceprintTestResponse,
} from '@/api/users'
import { listTenants, type TenantSpec } from '@/api/tenants'
import { extractErrorMessage } from '@/utils/format'
import { userHealthDetail, userHealthLabel, userHealthType } from '@/utils/userHealth'
import CatalogPage from '@/modules/common/CatalogPage.vue'

const rows = ref<UserView[]>([])
const tenants = ref<TenantSpec[]>([])
const memoryAvailable = ref(true)
const loading = ref(false)
const voiceprints = ref<Record<string, VoiceprintStatusResponse | null>>({})
let refreshTimer: ReturnType<typeof setInterval> | null = null

const dialogOpen = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const form = reactive({
  user_id: '',
  tenant_id: 'default',
  display_name: '',
})
const submitting = ref(false)

const voiceprintDialogOpen = ref(false)
const selectedUser = ref<UserView | null>(null)
const enrollment = ref<VoiceprintEnrollmentResponse | null>(null)
const recordedSamples = ref<VoiceprintSampleResponse[]>([])
const recordingPrompt = ref('')
const recordingPurpose = ref<'enroll' | 'test' | null>(null)
const recording = ref(false)
const sampleUploading = ref(false)
const completingVoiceprint = ref(false)
const deletingVoiceprint = ref(false)
const testingVoiceprint = ref(false)
const testResult = ref<VoiceprintTestResponse | null>(null)
const closingVoiceprint = ref(false)
const recordingMs = ref(0)
let audioContext: AudioContext | null = null
let mediaStream: MediaStream | null = null
let processor: ScriptProcessorNode | null = null
let source: MediaStreamAudioSourceNode | null = null
let recordingTimer: ReturnType<typeof setInterval> | null = null
let recordingStartedAt = 0
let recordingSampleRate = 0
let recordingBuffers: Float32Array[] = []

const selectedVoiceprint = computed(() => {
  const userId = selectedUser.value?.spec.user_id
  return userId ? voiceprints.value[userId] || null : null
})

const canCompleteVoiceprint = computed(() => recordedSamples.value.length >= 3)
const selectedVoiceprintReady = computed(() => selectedVoiceprint.value?.status === 'ready')

const voiceprintPrompts = [
  '清晨的阳光照进窗户，桌面上的水杯和书本都显得很安静。',
  '今天的会议安排比较紧凑，我们需要提前确认时间、地点和主要议题。',
  '请用自然的语速读完这句话，保持声音清楚，不要离麦克风太近。',
  '一阵微风吹过街角，树叶轻轻晃动，远处传来车辆经过的声音。',
  '这段录音用于采集稳定的人声特征，请保持音量平稳，尽量减少背景噪声。',
  '下午三点以后，图书馆里的人逐渐多了起来，大家都在低声交流。',
  '如果需要重新录制，可以先停下来休息几秒，再用平常说话的方式开始。',
  '蓝色的文件夹放在左边，白色的便签贴在屏幕旁边，内容写得很清楚。',
  '请连续读出这一整句文字，中间可以自然停顿，但不要刻意拖长尾音。',
  '小雨落在屋檐上，声音很轻，房间里只听得到均匀而稳定的说话声。',
  '为了得到更好的录音效果，请保持坐姿稳定，并让麦克风距离嘴部适中。',
  '这是一段普通的中文朗读材料，包含不同的声母、韵母和短暂停顿。',
]

async function refresh() {
  loading.value = true
  try {
    const [u, t] = await Promise.all([listUsers(), listTenants()])
    rows.value = u.users
    memoryAvailable.value = u.memory_available
    tenants.value = t
    await loadVoiceprints(u.users)
  } catch (e: any) {
    memoryAvailable.value = false
    ElMessage.error(`加载用户失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

async function loadVoiceprints(users: UserView[]) {
  const next: Record<string, VoiceprintStatusResponse | null> = {}
  await Promise.all(users.map(async (row) => {
    const userId = row.spec.user_id
    try {
      next[userId] = await getVoiceprint(userId, true)
    } catch {
      next[userId] = null
    }
  }))
  voiceprints.value = next
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

function voiceprintLabel(row: UserView): string {
  const status = voiceprints.value[row.spec.user_id]
  if (!status) return '未知'
  if (status.status === 'ready') {
    const count = status.profile?.quality?.accepted_segments
    return count ? `已录制 ${count}` : '已录制'
  }
  return '未录制'
}

function voiceprintTagType(row: UserView): 'success' | 'info' | 'warning' {
  const status = voiceprints.value[row.spec.user_id]
  if (!status) return 'warning'
  return status.status === 'ready' ? 'success' : 'info'
}

async function openVoiceprint(row: UserView) {
  selectedUser.value = row
  enrollment.value = null
  recordedSamples.value = []
  recordingMs.value = 0
  testResult.value = null
  pickRecordingPrompt()
  voiceprintDialogOpen.value = true
  try {
    voiceprints.value[row.spec.user_id] = await getVoiceprint(row.spec.user_id, true)
  } catch (e: any) {
    ElMessage.error(`加载声纹失败: ${extractErrorMessage(e)}`)
  }
}

function pickRecordingPrompt() {
  const previous = recordingPrompt.value
  let next = voiceprintPrompts[Math.floor(Math.random() * voiceprintPrompts.length)]
  if (voiceprintPrompts.length > 1) {
    while (next === previous) {
      next = voiceprintPrompts[Math.floor(Math.random() * voiceprintPrompts.length)]
    }
  }
  recordingPrompt.value = next
}

async function startRecording(purpose: 'enroll' | 'test') {
  if (!selectedUser.value || recording.value || sampleUploading.value) return
  const userId = selectedUser.value.spec.user_id
  try {
    if (purpose === 'enroll' && !enrollment.value) {
      enrollment.value = await createVoiceprintEnrollment(userId)
      recordedSamples.value = []
    }
    if (purpose === 'test') {
      testResult.value = null
    }
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    })
    audioContext = new AudioContext()
    recordingSampleRate = audioContext.sampleRate
    source = audioContext.createMediaStreamSource(mediaStream)
    processor = audioContext.createScriptProcessor(4096, 1, 1)
    recordingBuffers = []
    processor.onaudioprocess = (event) => {
      if (!recording.value) return
      const input = event.inputBuffer.getChannelData(0)
      recordingBuffers.push(new Float32Array(input))
    }
    source.connect(processor)
    processor.connect(audioContext.destination)
    recording.value = true
    recordingPurpose.value = purpose
    recordingStartedAt = performance.now()
    recordingTimer = setInterval(() => {
      recordingMs.value = Math.max(0, Math.round(performance.now() - recordingStartedAt))
    }, 100)
  } catch (e: any) {
    stopLocalRecording()
    ElMessage.error(`录音失败: ${extractErrorMessage(e)}`)
  }
}

async function stopRecording() {
  if (!selectedUser.value || !recording.value || !recordingPurpose.value) return
  const userId = selectedUser.value.spec.user_id
  const purpose = recordingPurpose.value
  const buffers = recordingBuffers.slice()
  const sourceRate = recordingSampleRate
  const durationMs = Math.round(performance.now() - recordingStartedAt)
  recordingMs.value = durationMs
  stopLocalRecording()
  if (durationMs < 500 || buffers.length === 0) {
    ElMessage.warning('录音过短')
    return
  }
  const wav = encodeWavBlob(mergeBuffers(buffers), sourceRate, 16000)
  if (purpose === 'test') {
    testingVoiceprint.value = true
    try {
      testResult.value = await testVoiceprint(userId, wav)
      pickRecordingPrompt()
      ElMessage.success('声纹测试完成')
    } catch (e: any) {
      ElMessage.error(`声纹测试失败: ${extractErrorMessage(e)}`)
    } finally {
      testingVoiceprint.value = false
    }
    return
  }
  if (!enrollment.value) return
  sampleUploading.value = true
  try {
    const sample = await uploadVoiceprintSample(userId, enrollment.value.enrollment_id, wav)
    recordedSamples.value.push(sample)
    pickRecordingPrompt()
    ElMessage.success(`样本 ${recordedSamples.value.length} 已保存`)
  } catch (e: any) {
    ElMessage.error(`上传样本失败: ${extractErrorMessage(e)}`)
  } finally {
    sampleUploading.value = false
  }
}

async function completeVoiceprint() {
  if (!selectedUser.value || !enrollment.value) return
  if (!canCompleteVoiceprint.value) {
    ElMessage.warning('至少录制 3 段')
    return
  }
  completingVoiceprint.value = true
  try {
    await completeVoiceprintEnrollment(
      selectedUser.value.spec.user_id,
      enrollment.value.enrollment_id,
    )
    voiceprints.value[selectedUser.value.spec.user_id] = await getVoiceprint(
      selectedUser.value.spec.user_id,
      true,
    )
    enrollment.value = null
    recordedSamples.value = []
    testResult.value = null
    pickRecordingPrompt()
    ElMessage.success('声纹已注册')
  } catch (e: any) {
    ElMessage.error(`注册失败: ${extractErrorMessage(e)}`)
  } finally {
    completingVoiceprint.value = false
  }
}

async function removeVoiceprint() {
  if (!selectedUser.value) return
  const userId = selectedUser.value.spec.user_id
  try {
    await ElMessageBox.confirm(`确认删除 "${userId}" 的声纹?`, '删除声纹', {
      type: 'warning',
    })
  } catch {
    return
  }
  deletingVoiceprint.value = true
  try {
    await deleteVoiceprint(userId)
    voiceprints.value[userId] = await getVoiceprint(userId, true)
    enrollment.value = null
    recordedSamples.value = []
    testResult.value = null
      pickRecordingPrompt()
      ElMessage.success('声纹已删除')
  } catch (e: any) {
    ElMessage.error(`删除声纹失败: ${extractErrorMessage(e)}`)
  } finally {
    deletingVoiceprint.value = false
  }
}

async function closeVoiceprintDialog() {
  stopLocalRecording()
  if (selectedUser.value && enrollment.value) {
    closingVoiceprint.value = true
    try {
      await cancelVoiceprintEnrollment(
        selectedUser.value.spec.user_id,
        enrollment.value.enrollment_id,
      )
    } catch {
      // Best-effort cleanup; the explicit delete voiceprint path still
      // removes every profile/enrollment sample for the user.
    } finally {
      closingVoiceprint.value = false
      enrollment.value = null
      recordedSamples.value = []
      pickRecordingPrompt()
    }
  }
  voiceprintDialogOpen.value = false
}

function stopLocalRecording() {
  recording.value = false
  recordingPurpose.value = null
  if (recordingTimer) {
    clearInterval(recordingTimer)
    recordingTimer = null
  }
  processor?.disconnect()
  source?.disconnect()
  processor = null
  source = null
  mediaStream?.getTracks().forEach((track) => track.stop())
  mediaStream = null
  void audioContext?.close()
  audioContext = null
}

function formatMs(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return '—'
  return score.toFixed(3)
}

function testVerdictLabel(verdict: VoiceprintTestResponse['verdict']): string {
  if (verdict === 'pass') return '通过'
  if (verdict === 'uncertain') return '需复测'
  return '未通过'
}

function testVerdictType(verdict: VoiceprintTestResponse['verdict']): 'success' | 'warning' | 'danger' {
  if (verdict === 'pass') return 'success'
  if (verdict === 'uncertain') return 'warning'
  return 'danger'
}

function profileSampleCount(profile: VoiceprintStatusResponse['profile']): number {
  if (!profile) return 0
  const accepted = profile.quality?.accepted_segments
  return typeof accepted === 'number' ? accepted : profile.sample_refs.length
}

function mergeBuffers(buffers: Float32Array[]): Float32Array {
  const length = buffers.reduce((sum, buf) => sum + buf.length, 0)
  const out = new Float32Array(length)
  let offset = 0
  for (const buf of buffers) {
    out.set(buf, offset)
    offset += buf.length
  }
  return out
}

function encodeWavBlob(input: Float32Array, sourceRate: number, targetRate: number): Blob {
  const pcm = resampleToInt16(input, sourceRate, targetRate)
  const buffer = new ArrayBuffer(44 + pcm.length * 2)
  const view = new DataView(buffer)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + pcm.length * 2, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, targetRate, true)
  view.setUint32(28, targetRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, pcm.length * 2, true)
  let offset = 44
  for (const sample of pcm) {
    view.setInt16(offset, sample, true)
    offset += 2
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

function resampleToInt16(input: Float32Array, sourceRate: number, targetRate: number): Int16Array {
  if (sourceRate === targetRate) return floatToInt16(input)
  const ratio = sourceRate / targetRate
  const length = Math.max(1, Math.floor(input.length / ratio))
  const out = new Float32Array(length)
  for (let i = 0; i < length; i += 1) {
    const sourceIndex = i * ratio
    const left = Math.floor(sourceIndex)
    const right = Math.min(left + 1, input.length - 1)
    const frac = sourceIndex - left
    out[i] = input[left] * (1 - frac) + input[right] * frac
  }
  return floatToInt16(out)
}

function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i += 1) {
    const s = Math.max(-1, Math.min(1, input[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}

function writeAscii(view: DataView, offset: number, value: string) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i))
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
  stopLocalRecording()
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
      <el-table-column label="声纹" width="130">
        <template #default="{ row }">
          <el-tag size="small" :type="voiceprintTagType(row)">
            {{ voiceprintLabel(row) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" align="right">
        <template #default="{ row }">
          <el-button size="small" link @click="openEdit(row)">编辑</el-button>
          <el-button size="small" link @click="openVoiceprint(row)">录制</el-button>
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

    <el-dialog
      v-model="voiceprintDialogOpen"
      :title="selectedUser ? `声纹 · ${selectedUser.spec.user_id}` : '声纹'"
      width="560px"
      :close-on-click-modal="false"
      @close="closeVoiceprintDialog"
    >
      <div v-if="selectedUser" class="voiceprint-panel">
        <div class="voiceprint-status">
          <div>
            <div class="status-label">当前状态</div>
            <div class="status-value">
              <el-tag
                size="small"
                :type="selectedVoiceprint?.status === 'ready' ? 'success' : 'info'"
              >
                {{ selectedVoiceprint?.status === 'ready' ? '已录制' : '未录制' }}
              </el-tag>
              <span v-if="selectedVoiceprint?.profile" class="status-meta">
                {{ profileSampleCount(selectedVoiceprint.profile) }} 段 /
                {{ formatMs(selectedVoiceprint.profile.duration_ms) }}
              </span>
            </div>
          </div>
          <el-button
            size="small"
            type="danger"
            plain
            :disabled="recording"
            :loading="deletingVoiceprint"
            @click="removeVoiceprint"
          >
            删除声纹
          </el-button>
        </div>

        <div class="record-strip">
          <div class="record-meter" :class="{ active: recording }">
            <span class="dot" />
            <span>
              {{
                recording
                  ? `${recordingPurpose === 'test' ? '测试' : '样本'} ${formatMs(recordingMs)}`
                  : `样本 ${recordedSamples.length}/3`
              }}
            </span>
          </div>
          <div class="record-actions">
            <el-button
              v-if="!recording || recordingPurpose !== 'enroll'"
              type="primary"
              :disabled="recording || sampleUploading || completingVoiceprint || testingVoiceprint"
              @click="startRecording('enroll')"
            >
              录制样本
            </el-button>
            <el-button
              v-if="!recording || recordingPurpose !== 'test'"
              :disabled="recording || !selectedVoiceprintReady || sampleUploading || completingVoiceprint || testingVoiceprint"
              @click="startRecording('test')"
            >
              测试声纹
            </el-button>
            <el-button v-if="recording" type="warning" @click="stopRecording">
              停止{{ recordingPurpose === 'test' ? '测试' : '录音' }}
            </el-button>
            <el-button
              :disabled="!canCompleteVoiceprint || recording || sampleUploading || testingVoiceprint"
              :loading="completingVoiceprint"
              @click="completeVoiceprint"
            >
              完成注册
            </el-button>
          </div>
        </div>

        <div class="prompt-panel">
          <div class="prompt-head">
            <span>朗读文本</span>
            <el-button
              size="small"
              link
              :disabled="recording"
              @click="pickRecordingPrompt"
            >
              换一句
            </el-button>
          </div>
          <div class="prompt-text">{{ recordingPrompt }}</div>
        </div>

        <div v-if="testResult" class="test-result">
          <div class="test-result-head">
            <el-tag size="small" :type="testVerdictType(testResult.verdict)">
              {{ testVerdictLabel(testResult.verdict) }}
            </el-tag>
            <span>{{ testResult.provider }} / {{ testResult.model }}</span>
          </div>
          <div class="score-grid">
            <div>
              <span>Best</span>
              <strong>{{ formatScore(testResult.best_score) }}</strong>
            </div>
            <div>
              <span>Avg</span>
              <strong>{{ formatScore(testResult.average_score) }}</strong>
            </div>
            <div>
              <span>Threshold</span>
              <strong>{{ formatScore(testResult.threshold) }}</strong>
            </div>
            <div>
              <span>Latency</span>
              <strong>{{ testResult.latency_ms }}ms</strong>
            </div>
          </div>
        </div>

        <el-table
          v-if="recordedSamples.length"
          :data="recordedSamples"
          size="small"
          class="samples-table"
        >
          <el-table-column prop="sample_id" label="样本" />
          <el-table-column label="时长" width="100">
            <template #default="{ row }">{{ formatMs(row.duration_ms) }}</template>
          </el-table-column>
          <el-table-column prop="sample_rate" label="Hz" width="90" />
        </el-table>

        <div v-if="sampleUploading || testingVoiceprint" class="uploading">
          {{ testingVoiceprint ? '测试中…' : '保存中…' }}
        </div>
      </div>
      <template #footer>
        <el-button @click="closeVoiceprintDialog">关闭</el-button>
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
.voiceprint-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.voiceprint-status {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--eid-border);
}
.status-label {
  font-size: 12px;
  color: var(--eid-text-muted);
  margin-bottom: 6px;
}
.status-value {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-meta {
  color: var(--eid-text-secondary);
  font-size: 12px;
}
.record-strip {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.record-meter {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 92px;
  color: var(--eid-text-secondary);
  font-variant-numeric: tabular-nums;
}
.record-meter .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--eid-border-strong);
}
.record-meter.active .dot {
  background: var(--eid-danger);
  box-shadow: 0 0 0 6px var(--eid-danger-soft);
}
.record-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.prompt-panel {
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--eid-bg-panel);
}
.prompt-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.prompt-text {
  color: var(--eid-text-primary);
  font-size: 16px;
  line-height: 1.7;
}
.test-result {
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--eid-bg-inset);
}
.test-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: var(--eid-text-secondary);
  font-size: 12px;
}
.score-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.score-grid div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.score-grid span {
  color: var(--eid-text-muted);
  font-size: 11px;
}
.score-grid strong {
  color: var(--eid-text-primary);
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}
.samples-table {
  width: 100%;
}
.uploading {
  color: var(--eid-text-muted);
  font-size: 12px;
}
</style>
