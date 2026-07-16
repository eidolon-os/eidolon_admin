<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import {
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
const route = useRoute()

const loading = ref(true)
const saving = ref(false)
const launching = ref(false)
const state = ref<OnboardingState | null>(null)
const loadError = ref('')

const missingLabels: Record<string, string> = {
  owner: '身份',
  master_companion: '主伙伴',
  current_genome: '人格',
  memory_realm: '记忆域',
  web_device: '网页端设备',
}

const ownerId = computed(() => state.value?.owner?.owner_id || ownersStore.currentId || '')
const requestedOwnerId = computed(() => typeof route.query.owner_id === 'string' ? route.query.owner_id : '')
const hasOwner = computed(() => Boolean(state.value?.owner))
const ready = computed(() => Boolean(state.value?.master_ready))
const master = computed(() => state.value?.master_companion || null)
const companions = computed(() => state.value?.companions || [])
const slaveCompanions = computed(() =>
  companions.value.filter((item) => (
    item.companion_id !== master.value?.companion_id
    && item.kind !== 'guard'
    && item.companion_type !== 'guard'
  )),
)
const guardCompanions = computed(() => companions.value.filter((item) => item.kind === 'guard' || item.companion_type === 'guard'))
const missing = computed(() => state.value?.missing || [])
const primaryName = computed(() => master.value?.display_name || 'Eidolon')
const ownerName = computed(() => state.value?.owner?.display_name || '我的身份')
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

watch(requestedOwnerId, async (next) => {
  if (!next || next === state.value?.owner?.owner_id) return
  await load()
})

onMounted(async () => {
  await load()
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    await ownersStore.load(true)
    if (requestedOwnerId.value && ownersStore.owners.some((owner) => owner.owner_id === requestedOwnerId.value)) {
      ownersStore.setCurrent(requestedOwnerId.value)
    }
    state.value = await getOnboardingState(ownersStore.currentId || undefined)
  } catch (error: any) {
    loadError.value = error?.response?.data?.detail || error?.message || '无法读取初始化状态'
  } finally {
    loading.value = false
  }
}

async function repair() {
  saving.value = true
  try {
    const response = await initializeOnboarding({
      owner_id: state.value?.owner?.owner_id || ownersStore.currentId || undefined,
      owner_display_name: state.value?.owner?.display_name || '',
      companion_display_name: master.value?.display_name || '',
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

function openCompanionCreator() {
  router.push({ name: 'companion-create', query: { owner_id: ownerId.value || undefined } })
}

function goCompanions() {
  router.push({ name: 'companions', query: { owner_id: ownerId.value || undefined } })
}

function goDevices() {
  router.push({ name: 'devices', params: { section: 'overview' }, query: { owner_id: ownerId.value || undefined } })
}

function goSecurity() {
  router.push({ name: 'identity-security', query: { owner_id: ownerId.value || undefined } })
}

function goCockpit() {
  if (!ownerId.value) return
  router.push({ name: 'mission-control', query: { owner_id: ownerId.value } })
}

function goSpaces() {
  router.push({ name: 'spaces', query: { owner_id: ownerId.value || undefined } })
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
              <h2>{{ missing.includes('master_companion') || !hasOwner ? '创建你的 Eidolon' : '修复初始化配置' }}</h2>
            </div>
            <el-tag v-if="missing.length" type="warning" effect="dark">
              {{ missing.map(statusText).join(' / ') }}
            </el-tag>
          </div>

          <div class="setup-callout">
            <el-icon><Avatar /></el-icon>
            <div>
              <strong>{{ hasOwner ? ownerName : '新身份' }}</strong>
              <span>{{ missing.includes('master_companion') || !hasOwner ? '尚未创建长期伙伴' : '已有伙伴，但运行资源需要修复' }}</span>
            </div>
          </div>

          <div class="wizard-actions">
            <el-button
              v-if="missing.includes('master_companion') || !hasOwner"
              type="primary"
              size="large"
              @click="openCompanionCreator"
            >
              创建伙伴
              <el-icon><ArrowRight /></el-icon>
            </el-button>
            <el-button v-else type="warning" size="large" :loading="saving" @click="repair">
              修复配置
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
            <el-button size="large" @click="goCockpit">
              <el-icon><DataLine /></el-icon>
              运行时驾驶舱
            </el-button>
            <el-button size="large" @click="openCompanionCreator">
              <el-icon><Plus /></el-icon>
              创建伙伴
            </el-button>
            <el-button size="large" @click="goDevices">
              <el-icon><Monitor /></el-icon>
              绑定设备
            </el-button>
            <el-button v-if="guardCompanions.length" size="large" @click="goSecurity">
              <el-icon><Lock /></el-icon>
              身份与安全
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
            <el-button plain @click="goSpaces">
              管理空间
            </el-button>
          </div>
        </section>
      </section>
    </template>

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
.setup-callout {
  min-height: 124px;
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 20px;
  padding: 18px;
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  color: var(--eid-text-secondary);
  background: var(--eid-bg-inset);
}
.setup-callout > .el-icon {
  color: var(--eid-accent);
  font-size: 28px;
}
.setup-callout strong,
.setup-callout span {
  display: block;
}
.setup-callout strong {
  color: var(--eid-text-primary);
  font-size: 17px;
}
.setup-callout span {
  margin-top: 6px;
  color: var(--eid-text-muted);
  font-size: 13px;
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
