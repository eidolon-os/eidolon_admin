<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  createOnboardingCompanion,
  getOnboardingState,
  initializeOnboarding,
  launchOnboardingCompanion,
  type LaunchIdentity,
  type OnboardingState,
} from '@/api/onboarding'
import type { CompanionView } from '@/api/eidolonData'
import { useOwnersStore } from '@/stores/owners'
import { webBodyLaunchUrl } from '@/utils/clientWeb'

const ownersStore = useOwnersStore()
const router = useRouter()

const loading = ref(true)
const saving = ref(false)
const launching = ref(false)
const creatingCompanion = ref(false)
const deletingOwner = ref(false)
const companionDialogOpen = ref(false)
const wizardStep = ref(0)
const state = ref<OnboardingState | null>(null)
const loadError = ref('')

const setupForm = reactive({
  owner_display_name: '',
  companion_display_name: '',
  companion_description: '',
  relationship: '',
  speaking_style: '',
  important_memories: '',
})

const companionForm = reactive({
  companion_display_name: '',
  companion_description: '',
  relationship: '',
  speaking_style: '',
  important_memories: '',
  create_web_device: false,
})

const missingLabels: Record<string, string> = {
  owner: '身份',
  master_companion: '主伙伴',
  current_genome: '人格',
  memory_realm: '记忆域',
  web_device: '网页端设备',
}

const ownerId = computed(() => state.value?.owner?.owner_id || ownersStore.currentId || '')
const hasOwner = computed(() => Boolean(state.value?.owner))
const ready = computed(() => Boolean(state.value?.master_ready))
const master = computed(() => state.value?.master_companion || null)
const companions = computed(() => state.value?.companions || [])
const slaveCompanions = computed(() =>
  companions.value.filter((item) => item.companion_id !== master.value?.companion_id),
)
const missing = computed(() => state.value?.missing || [])
const setupTitle = computed(() => {
  if (!hasOwner.value) return '创建你的 Eidolon'
  if (missing.value.includes('master_companion')) return '继续创建第一个伙伴'
  return '修复初始化配置'
})
const primaryName = computed(() => master.value?.display_name || setupForm.companion_display_name || 'Eidolon')
const ownerName = computed(() => state.value?.owner?.display_name || setupForm.owner_display_name || '我的身份')
const readyStats = computed(() => [
  { label: 'Companions', value: companions.value.length || 0 },
  { label: 'Web Device', value: state.value?.web_device?.status || 'missing' },
  { label: 'Memory', value: master.value?.default_memory_realm_id ? 'ready' : 'missing' },
  { label: 'Genome', value: master.value?.current_genome_id ? 'ready' : 'missing' },
])

watch(
  () => state.value,
  (next) => {
    if (!next) return
    if (next.owner && !setupForm.owner_display_name) setupForm.owner_display_name = next.owner.display_name
    if (next.master_companion && !setupForm.companion_display_name) {
      setupForm.companion_display_name = next.master_companion.display_name
    }
    if (next.owner?.owner_id) ownersStore.setCurrent(next.owner.owner_id)
  },
)

watch(
  () => ownersStore.currentId,
  async (next, previous) => {
    if (!next || next === previous || next === state.value?.owner?.owner_id) return
    await load()
  },
)

onMounted(async () => {
  await load()
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    await ownersStore.load(true)
    state.value = await getOnboardingState(ownersStore.currentId || undefined)
  } catch (error: any) {
    loadError.value = error?.response?.data?.detail || error?.message || '无法读取初始化状态'
  } finally {
    loading.value = false
  }
}

function validateWizardStep(): boolean {
  if (wizardStep.value === 0) {
    if (!hasOwner.value && !setupForm.owner_display_name.trim()) {
      ElMessage.warning('先给你的身份起个名字')
      return false
    }
    if (!master.value && !setupForm.companion_display_name.trim()) {
      ElMessage.warning('给第一个伙伴起个名字')
      return false
    }
  }
  return true
}

function nextWizardStep() {
  if (!validateWizardStep()) return
  wizardStep.value = 1
}

async function submitSetup() {
  if (!validateWizardStep()) return
  saving.value = true
  try {
    const response = await initializeOnboarding({
      owner_id: state.value?.owner?.owner_id || ownersStore.currentId || undefined,
      owner_display_name: setupForm.owner_display_name,
      companion_display_name: setupForm.companion_display_name,
      companion_description: setupForm.companion_description,
      relationship: setupForm.relationship,
      speaking_style: setupForm.speaking_style,
      important_memories: setupForm.important_memories,
    })
    state.value = response.state
    await ownersStore.load(true)
    if (response.state.owner?.owner_id) ownersStore.setCurrent(response.state.owner.owner_id)
    ElMessage.success(response.state.master_ready ? '初始化完成' : '已保存')
  } finally {
    saving.value = false
  }
}

async function repair() {
  saving.value = true
  try {
    const response = await initializeOnboarding({
      owner_id: state.value?.owner?.owner_id || ownersStore.currentId || undefined,
      owner_display_name: state.value?.owner?.display_name || setupForm.owner_display_name,
      companion_display_name: master.value?.display_name || setupForm.companion_display_name,
    })
    state.value = response.state
    ElMessage.success('配置已修复')
  } finally {
    saving.value = false
  }
}

async function startChat(companion?: CompanionView | null) {
  launching.value = true
  try {
    const response = await launchOnboardingCompanion({
      owner_id: ownerId.value || undefined,
      companion_id: companion?.companion_id,
    })
    openLaunch(response)
    state.value = await getOnboardingState(ownerId.value || undefined)
  } finally {
    launching.value = false
  }
}

function openLaunch(identity: LaunchIdentity) {
  const url = identity.launch_url || webBodyLaunchUrl({
    ownerId: identity.owner_id,
    companionId: identity.companion_id,
    deviceId: identity.device_id,
  })
  window.open(url, '_blank', 'noopener')
}

async function createCompanion() {
  if (!ownerId.value) {
    ElMessage.warning('先完成身份初始化')
    return
  }
  if (!companionForm.companion_display_name.trim()) {
    ElMessage.warning('给新伙伴起个名字')
    return
  }
  creatingCompanion.value = true
  try {
    const response = await createOnboardingCompanion({
      owner_id: ownerId.value,
      companion_display_name: companionForm.companion_display_name,
      companion_description: companionForm.companion_description,
      relationship: companionForm.relationship,
      speaking_style: companionForm.speaking_style,
      important_memories: companionForm.important_memories,
      create_web_device: companionForm.create_web_device,
    })
    state.value = response.state
    companionDialogOpen.value = false
    resetCompanionForm()
    ElMessage.success('伙伴已创建')
    if (response.launch_identity) openLaunch(response.launch_identity)
  } finally {
    creatingCompanion.value = false
  }
}

async function deleteCurrentOwner() {
  if (!ownerId.value) return
  let confirmed = ''
  try {
    const result = await ElMessageBox.prompt(
      `删除后会移除 ${ownerName.value} 的 owner、companions、devices、memory、conversations、jobs 和事件数据。请输入 owner_id 确认。`,
      '删除当前身份',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        inputPlaceholder: ownerId.value,
        inputValidator: (value) => value === ownerId.value || `请输入 ${ownerId.value}`,
        confirmButtonClass: 'el-button--danger',
        type: 'warning',
      },
    )
    confirmed = String(result.value || '')
  } catch {
    return
  }
  deletingOwner.value = true
  try {
    await ownersStore.deleteLocal(ownerId.value, confirmed)
    state.value = await getOnboardingState(ownersStore.currentId || undefined)
    resetCompanionForm()
    setupForm.owner_display_name = ''
    setupForm.companion_display_name = ''
    wizardStep.value = 0
    ElMessage.success('当前身份已删除')
  } finally {
    deletingOwner.value = false
  }
}

function resetCompanionForm() {
  companionForm.companion_display_name = ''
  companionForm.companion_description = ''
  companionForm.relationship = ''
  companionForm.speaking_style = ''
  companionForm.important_memories = ''
  companionForm.create_web_device = false
}

function goCompanions() {
  router.push({ name: 'companions' })
}

function goDevices() {
  router.push({ name: 'devices', params: { tab: 'fleet' } })
}

function statusText(value: string) {
  return missingLabels[value] || value
}
</script>

<template>
  <section class="my-eidolon">
    <div class="page-head">
      <div>
        <p class="eyebrow">MY EIDOLON</p>
        <h1>我的 Eidolon</h1>
      </div>
      <div class="head-actions">
        <el-tooltip content="刷新状态" placement="bottom">
          <el-button circle :loading="loading" @click="load">
            <el-icon><RefreshRight /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      class="top-alert"
      type="error"
      :closable="false"
      :title="loadError"
      show-icon
    />

    <div v-if="loading && !state" class="loading-block">
      <el-skeleton :rows="8" animated />
    </div>

    <template v-else-if="!ready">
      <section class="setup-layout">
        <div class="setup-panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">SETUP</p>
              <h2>{{ setupTitle }}</h2>
            </div>
            <el-tag v-if="missing.length" type="warning" effect="dark">
              {{ missing.map(statusText).join(' / ') }}
            </el-tag>
          </div>

          <el-steps :active="wizardStep" finish-status="success" simple>
            <el-step title="身份与伙伴" />
            <el-step title="性格与记忆" />
          </el-steps>

          <div v-if="wizardStep === 0" class="wizard-body">
            <el-form label-position="top">
              <el-form-item v-if="!hasOwner" label="你的身份名">
                <el-input
                  v-model="setupForm.owner_display_name"
                  size="large"
                  placeholder="例如 Manson"
                  maxlength="48"
                  show-word-limit
                />
              </el-form-item>
              <el-form-item v-else label="当前身份">
                <div class="identity-row">
                  <el-icon><UserFilled /></el-icon>
                  <span>{{ ownerName }}</span>
                </div>
              </el-form-item>
              <el-form-item label="第一个伙伴的名字">
                <el-input
                  v-model="setupForm.companion_display_name"
                  size="large"
                  placeholder="例如 小艺"
                  maxlength="64"
                  show-word-limit
                />
              </el-form-item>
            </el-form>
          </div>

          <div v-else class="wizard-body">
            <el-form label-position="top">
              <el-form-item label="性格描述">
                <el-input
                  v-model="setupForm.companion_description"
                  type="textarea"
                  :rows="3"
                  placeholder="TA 给你的感觉、擅长陪伴的方式"
                />
              </el-form-item>
              <div class="two-fields">
                <el-form-item label="关系">
                  <el-input v-model="setupForm.relationship" placeholder="例如 可信赖的个人 AI 伙伴" />
                </el-form-item>
                <el-form-item label="说话风格">
                  <el-input v-model="setupForm.speaking_style" placeholder="例如 温和、直接、有一点幽默" />
                </el-form-item>
              </div>
              <el-form-item label="重要记忆">
                <el-input
                  v-model="setupForm.important_memories"
                  type="textarea"
                  :rows="4"
                  placeholder="每行一条，例如你的偏好、习惯、重要背景"
                />
              </el-form-item>
            </el-form>
          </div>

          <div class="wizard-actions">
            <el-button v-if="wizardStep === 1" @click="wizardStep = 0">返回</el-button>
            <el-button v-if="wizardStep === 0" type="primary" @click="nextWizardStep">
              继续
              <el-icon><ArrowRight /></el-icon>
            </el-button>
            <el-button v-else type="primary" :loading="saving" @click="submitSetup">
              完成初始化
              <el-icon><CircleCheck /></el-icon>
            </el-button>
          </div>
        </div>

        <aside class="status-panel">
          <div class="status-title">
            <el-icon><WarningFilled v-if="missing.length" /><CircleCheckFilled v-else /></el-icon>
            <span>{{ missing.length ? '需要补全' : '准备就绪' }}</span>
          </div>
          <ul class="missing-list">
            <li
              v-for="key in ['owner', 'master_companion', 'current_genome', 'memory_realm', 'web_device']"
              :key="key"
              :class="{ done: !missing.includes(key) }"
            >
              <el-icon><CircleCheckFilled v-if="!missing.includes(key)" /><Warning v-else /></el-icon>
              <span>{{ statusText(key) }}</span>
            </li>
          </ul>
          <el-button
            v-if="hasOwner && !missing.includes('master_companion')"
            class="repair-button"
            type="warning"
            :loading="saving"
            @click="repair"
          >
            修复配置
          </el-button>
        </aside>
      </section>
    </template>

    <template v-else>
      <section class="ready-shell">
        <div class="primary-panel">
          <div class="identity-stack">
            <div class="avatar-mark">
              <el-icon><Avatar /></el-icon>
            </div>
            <div class="identity-copy">
              <p class="eyebrow">CURRENT COMPANION</p>
              <h2>{{ primaryName }}</h2>
              <span>{{ ownerName }}</span>
            </div>
          </div>
          <div class="primary-actions">
            <el-button type="primary" size="large" :loading="launching" @click="startChat(master)">
              <el-icon><VideoPlay /></el-icon>
              启动对话
            </el-button>
            <el-button size="large" @click="companionDialogOpen = true">
              <el-icon><Plus /></el-icon>
              创建伙伴
            </el-button>
            <el-button size="large" @click="goDevices">
              <el-icon><Monitor /></el-icon>
              绑定设备
            </el-button>
          </div>
        </div>

        <div class="stat-grid">
          <div v-for="item in readyStats" :key="item.label" class="stat-tile">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>

        <section class="section-band">
          <div class="section-head">
            <div>
              <p class="eyebrow">COMPANIONS</p>
              <h3>伙伴</h3>
            </div>
            <el-button text @click="goCompanions">查看全部</el-button>
          </div>
          <div class="companion-grid">
            <article v-if="master" class="companion-card master-card">
              <div class="card-main">
                <el-icon><Avatar /></el-icon>
                <div>
                  <strong>{{ master.display_name || master.companion_id }}</strong>
                  <span>默认对话伙伴</span>
                </div>
              </div>
              <el-button :loading="launching" @click="startChat(master)">
                启动
              </el-button>
            </article>
            <article v-for="companion in slaveCompanions.slice(0, 4)" :key="companion.companion_id" class="companion-card">
              <div class="card-main">
                <el-icon><Avatar /></el-icon>
                <div>
                  <strong>{{ companion.display_name || companion.companion_id }}</strong>
                  <span>{{ companion.status }}</span>
                </div>
              </div>
              <el-button @click="startChat(companion)">启动</el-button>
            </article>
          </div>
        </section>

        <section v-if="state?.owner" class="section-band owner-band">
          <div class="section-head">
            <div>
              <p class="eyebrow">IDENTITY</p>
              <h3>当前身份</h3>
            </div>
          </div>
          <div class="owner-row">
            <div class="owner-meta">
              <el-icon><UserFilled /></el-icon>
              <div>
                <strong>{{ ownerName }}</strong>
                <span>{{ state.owner.owner_id }}</span>
              </div>
            </div>
            <el-button type="danger" plain :loading="deletingOwner" @click="deleteCurrentOwner">
              删除当前身份
            </el-button>
          </div>
        </section>
      </section>
    </template>

    <el-dialog
      v-model="companionDialogOpen"
      title="创建伙伴"
      width="680px"
      append-to-body
      destroy-on-close
      class="companion-dialog"
    >
      <el-form label-position="top">
        <el-form-item label="名字">
          <el-input v-model="companionForm.companion_display_name" placeholder="例如 Study Buddy" maxlength="64" />
        </el-form-item>
        <el-form-item label="性格描述">
          <el-input v-model="companionForm.companion_description" type="textarea" :rows="3" />
        </el-form-item>
        <div class="two-fields">
          <el-form-item label="关系">
            <el-input v-model="companionForm.relationship" />
          </el-form-item>
          <el-form-item label="说话风格">
            <el-input v-model="companionForm.speaking_style" />
          </el-form-item>
        </div>
        <el-form-item label="重要记忆">
          <el-input v-model="companionForm.important_memories" type="textarea" :rows="4" />
        </el-form-item>
        <el-checkbox v-model="companionForm.create_web_device">创建后直接准备网页端对话</el-checkbox>
      </el-form>
      <template #footer>
        <el-button @click="companionDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="creatingCompanion" @click="createCompanion">创建</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.my-eidolon {
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: min(1180px, 100%);
  margin: 0 auto;
  padding: 4px 0 32px;
}
.page-head,
.panel-head,
.section-head,
.primary-panel,
.identity-stack,
.primary-actions,
.head-actions,
.card-main,
.status-title,
.wizard-actions {
  display: flex;
  align-items: center;
}
.page-head,
.panel-head,
.section-head,
.primary-panel {
  justify-content: space-between;
  gap: 16px;
}
.page-head h1,
.setup-panel h2,
.primary-panel h2,
.section-head h3 {
  margin: 0;
  color: var(--eid-text-primary);
  letter-spacing: 0;
}
.page-head h1 {
  font-size: 30px;
  line-height: 1.1;
}
.setup-panel h2,
.primary-panel h2 {
  font-size: 22px;
  line-height: 1.18;
}
.section-head h3 {
  font-size: 18px;
}
.eyebrow {
  margin: 0 0 6px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.08em;
}
.top-alert {
  border-radius: 8px;
}
.loading-block,
.setup-panel,
.status-panel,
.primary-panel,
.stat-tile,
.section-band {
  border: 1px solid color-mix(in srgb, var(--eid-border-strong) 72%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--eid-bg-panel) 88%, transparent);
  box-shadow: var(--eid-shadow-sm);
}
.loading-block,
.setup-panel,
.status-panel,
.primary-panel,
.section-band {
  padding: 18px;
}
.setup-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 18px;
  align-items: start;
}
.setup-panel {
  min-width: 0;
}
.wizard-body {
  min-height: 330px;
  padding: 22px 0 4px;
}
.two-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.identity-row {
  width: 100%;
  min-height: 40px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  color: var(--eid-text-secondary);
  background: var(--eid-bg-inset);
}
.wizard-actions {
  justify-content: flex-end;
  gap: 10px;
}
.status-panel {
  position: sticky;
  top: 12px;
}
.status-title {
  gap: 10px;
  color: var(--eid-text-primary);
  font-weight: 720;
}
.missing-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 18px 0;
  padding: 0;
  list-style: none;
}
.missing-list li {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  color: var(--eid-warning);
  font-size: 13px;
}
.missing-list li.done {
  color: var(--eid-success);
}
.missing-list span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.repair-button {
  width: 100%;
}
.ready-shell {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.primary-panel {
  min-height: 122px;
  border-color: color-mix(in srgb, var(--eid-accent) 32%, var(--eid-border));
  background:
    linear-gradient(90deg, rgba(34, 211, 238, 0.13), rgba(52, 211, 153, 0.05), transparent),
    color-mix(in srgb, var(--eid-bg-panel) 90%, transparent);
}
.identity-stack {
  min-width: 0;
  gap: 14px;
}
.avatar-mark {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 46%, var(--eid-border));
  border-radius: 8px;
  color: var(--eid-accent-hover);
  background: var(--eid-accent-soft);
  font-size: 24px;
}
.identity-copy {
  min-width: 0;
}
.identity-copy h2,
.identity-copy span {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.identity-copy span {
  margin-top: 6px;
  color: var(--eid-text-secondary);
  font-size: 13px;
}
.primary-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.stat-tile {
  min-width: 0;
  padding: 14px;
}
.stat-tile span,
.stat-tile strong {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stat-tile span {
  color: var(--eid-text-muted);
  font-size: 12px;
}
.stat-tile strong {
  margin-top: 8px;
  color: var(--eid-text-primary);
  font-size: 18px;
  text-transform: capitalize;
}
.companion-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.companion-card,
.owner-row {
  min-width: 0;
  min-height: 86px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-inset);
}
.master-card {
  border-color: color-mix(in srgb, var(--eid-success) 36%, var(--eid-border));
}
.card-main {
  min-width: 0;
  gap: 10px;
}
.card-main > .el-icon {
  flex: 0 0 auto;
  color: var(--eid-accent);
}
.card-main div {
  min-width: 0;
}
.card-main strong,
.card-main span {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-main strong {
  color: var(--eid-text-primary);
  font-size: 14px;
}
.card-main span {
  margin-top: 4px;
  color: var(--eid-text-muted);
  font-size: 12px;
}
.owner-band {
  border-color: color-mix(in srgb, var(--eid-danger) 22%, var(--eid-border));
}
.owner-row {
  margin-top: 14px;
  min-height: 72px;
}
.owner-meta {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}
.owner-meta > .el-icon {
  flex: 0 0 auto;
  color: var(--eid-accent);
}
.owner-meta div {
  min-width: 0;
}
.owner-meta strong,
.owner-meta span {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.owner-meta strong {
  color: var(--eid-text-primary);
  font-size: 14px;
}
.owner-meta span {
  margin-top: 4px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 12px;
}
:deep(.el-step__title) {
  white-space: nowrap;
}

@media (max-width: 980px) {
  .setup-layout,
  .stat-grid {
    grid-template-columns: 1fr;
  }
  .status-panel {
    position: static;
  }
  .primary-panel {
    align-items: flex-start;
    flex-direction: column;
  }
  .primary-actions {
    width: 100%;
    justify-content: flex-start;
  }
}

@media (max-width: 640px) {
  .my-eidolon {
    gap: 14px;
    padding-bottom: 20px;
  }
  .page-head,
  .panel-head,
  .section-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .two-fields {
    grid-template-columns: 1fr;
  }
  .companion-grid {
    grid-template-columns: 1fr;
  }
  .companion-card,
  .owner-row {
    align-items: stretch;
    flex-direction: column;
  }
  .companion-card .el-button,
  .owner-row .el-button {
    width: 100%;
  }
  .primary-actions .el-button {
    width: 100%;
  }
  .page-head h1 {
    font-size: 26px;
  }
}
</style>
