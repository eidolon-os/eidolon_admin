<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, ChatDotRound, Loading, Monitor, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { useOwnersStore } from '@/stores/owners'
import {
  clearCompanionFace,
  companionFaceImageUrl,
  companionIdleVideoUrl,
  createCompanionWebBody,
  getCompanionFace,
  listCompanionDevices,
  listCompanionGenomes,
  listOwnerCompanions,
  listOwnerConversations,
  listOwnerEvents,
  listOwnerMemoryRealms,
  regenerateCompanionIdle,
  resetCompanionGenome,
  uploadCompanionFace,
  type CompanionFaceView,
  type CompanionView,
  type ConversationView,
  type DeviceView,
  type EventView,
  type MemoryRealmView,
  type PersonaGenomeHistoryResponse,
} from '@/api/eidolonData'
import type { UploadFile } from 'element-plus'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'
import { webBodyLaunchUrl } from '@/utils/clientWeb'
import { describeIdleFace, idleTagType } from './idleFace'

type Section = 'overview' | 'persona' | 'memory' | 'bodies' | 'activity'

const route = useRoute()
const router = useRouter()
const ownersStore = useOwnersStore()
const companionId = computed(() => String(route.params.companionId || ''))
const ownerId = computed(() => ownersStore.currentId)
const companion = ref<CompanionView | null>(null)
const genomes = ref<PersonaGenomeHistoryResponse>({ current_genome: null, history: [] })
const devices = ref<DeviceView[]>([])
const realms = ref<MemoryRealmView[]>([])
const conversations = ref<ConversationView[]>([])
const events = ref<EventView[]>([])
const loading = ref(false)
const launching = ref(false)
const resetting = ref(false)
const face = ref<CompanionFaceView | null>(null)
const faceUploading = ref(false)
const idleRegenerating = ref(false)
const idle = computed(() => describeIdleFace(face.value))
const idleVideoUrl = computed(() =>
  ownerId.value && idle.value?.ready
    ? companionIdleVideoUrl(ownerId.value, companionId.value, face.value?.updated_at || '')
    : '',
)

const section = computed<Section>({
  get: () => {
    const value = String(route.params.section || 'overview')
    return ['overview', 'persona', 'memory', 'bodies', 'activity'].includes(value) ? value as Section : 'overview'
  },
  set: (value) => router.replace({
    name: 'companion-detail',
    params: { companionId: companionId.value, section: value },
    query: { ...route.query, owner_id: ownerId.value || undefined },
  }),
})

const currentGenome = computed(() => genomes.value.current_genome || genomes.value.history[0] || null)
const genomePayload = computed(() => (currentGenome.value?.genome_json || {}) as Record<string, any>)
const constitution = computed(() => (genomePayload.value.constitution || {}) as Record<string, any>)
const character = computed(() => (genomePayload.value.character || {}) as Record<string, any>)
const relationship = computed(() => (genomePayload.value.relationship || {}) as Record<string, any>)
const expression = computed(() => (genomePayload.value.expression || {}) as Record<string, any>)
const companionRealms = computed(() => realms.value.filter((realm) => realm.companion_id === companionId.value))
const companionConversations = computed(() => conversations.value.filter((item) => item.companion_id === companionId.value))
const companionEvents = computed(() => events.value.filter((item) => item.companion_id === companionId.value))
const isGuard = computed(() => companion.value?.kind === 'guard' || companion.value?.companion_type === 'guard')
const faceImageUrl = computed(() =>
  ownerId.value && face.value
    ? companionFaceImageUrl(ownerId.value, companionId.value, face.value.sha256)
    : '',
)

onMounted(load)
watch([ownerId, companionId], load)

async function load() {
  if (!ownerId.value || !companionId.value) return
  loading.value = true
  try {
    const [companionRows, history, bodyRows, realmRows, conversationRows, eventRows] = await Promise.all([
      listOwnerCompanions(ownerId.value),
      listCompanionGenomes(ownerId.value, companionId.value),
      listCompanionDevices(ownerId.value, companionId.value),
      listOwnerMemoryRealms(ownerId.value),
      listOwnerConversations(ownerId.value),
      listOwnerEvents(ownerId.value),
    ])
    companion.value = companionRows.find((item) => item.companion_id === companionId.value) || null
    genomes.value = history
    devices.value = bodyRows
    realms.value = realmRows
    conversations.value = conversationRows
    events.value = eventRows
    await loadFace()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadFace() {
  // The display face is a normal companion concept only — Guard has no avatar.
  if (!ownerId.value || !companion.value || isGuard.value) {
    face.value = null
    return
  }
  try {
    face.value = await getCompanionFace(ownerId.value, companionId.value)
  } catch {
    face.value = null
  }
}

async function onFaceSelected(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!ownerId.value || !raw) return
  if (!raw.type.startsWith('image/')) {
    ElMessage.error('请选择图片文件')
    return
  }
  faceUploading.value = true
  try {
    face.value = await uploadCompanionFace(ownerId.value, companionId.value, raw)
    ElMessage.success('已更新数字人形象')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    faceUploading.value = false
  }
}

async function clearFace() {
  if (!ownerId.value) return
  try {
    await ElMessageBox.confirm('恢复为默认形象？已上传的形象图将被移除。', '恢复默认形象', { type: 'warning' })
  } catch {
    return
  }
  try {
    await clearCompanionFace(ownerId.value, companionId.value)
    face.value = null
    ElMessage.success('已恢复默认形象')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  }
}

async function regenerateIdle() {
  if (!ownerId.value || idleRegenerating.value) return
  idleRegenerating.value = true
  try {
    face.value = await regenerateCompanionIdle(ownerId.value, companionId.value)
    ElMessage.success('已开始重新生成 idle 动画')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    idleRegenerating.value = false
  }
}

// While a clip is generating, refresh the face so the operator sees it become
// ready without a manual reload — bounded so a stuck job can't poll forever.
const IDLE_POLL_MS = 5000
const IDLE_POLL_MAX = 24
let idlePollTimer: ReturnType<typeof setInterval> | undefined
function stopIdlePoll() {
  if (idlePollTimer) {
    clearInterval(idlePollTimer)
    idlePollTimer = undefined
  }
}
watch(
  () => idle.value?.generating ?? false,
  (generating) => {
    stopIdlePoll()
    if (!generating) return
    let ticks = 0
    idlePollTimer = setInterval(async () => {
      ticks += 1
      await loadFace()
      if (ticks >= IDLE_POLL_MAX || !(idle.value?.generating)) stopIdlePoll()
    }, IDLE_POLL_MS)
  },
)
onBeforeUnmount(stopIdlePoll)

function back() {
  router.push({ name: 'companions', query: { owner_id: ownerId.value || undefined } })
}

function chat() {
  router.push({
    name: 'feature',
    params: { serviceId: 'agent', feature: 'chat-test' },
    query: { owner_id: ownerId.value, companion_id: companionId.value },
  })
}

function goSecurity() {
  router.push({ name: 'identity-security', query: { owner_id: ownerId.value || undefined } })
}

function goDevices() {
  router.push({ name: 'devices', params: { section: 'overview' }, query: { owner_id: ownerId.value || undefined } })
}

async function launch() {
  if (!ownerId.value || isGuard.value) return
  launching.value = true
  try {
    const body = await createCompanionWebBody(ownerId.value, companionId.value)
    window.open(webBodyLaunchUrl({ ownerId: ownerId.value, companionId: companionId.value, deviceId: body.device_id }), '_blank', 'noopener')
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    launching.value = false
  }
}

async function resetPersona() {
  if (!ownerId.value || isGuard.value) return
  try {
    await ElMessageBox.confirm('切回最初提交的人格版本？后续演化版本仍保留在历史中。', '重置人格版本', { type: 'warning' })
  } catch {
    return
  }
  resetting.value = true
  try {
    await resetCompanionGenome(ownerId.value, companionId.value)
    ElMessage.success('已切回初始人格版本')
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    resetting.value = false
  }
}
</script>

<template>
  <section class="companion-detail" v-loading="loading">
    <header class="page-head">
      <el-button text :icon="ArrowLeft" @click="back">返回 Companions</el-button>
      <div class="identity">
        <div><p>COMPANION</p><h1>{{ companion?.display_name || companionId }}</h1><code>{{ companionId }}</code></div>
        <div class="head-tags">
          <el-tag v-if="companion?.is_master || companion?.companion_type === 'master'" type="warning" effect="dark">主要伙伴</el-tag>
          <el-tag v-if="isGuard" type="info" effect="dark">系统身份</el-tag>
          <el-tag effect="plain">{{ companion?.status || 'unknown' }}</el-tag>
        </div>
      </div>
      <div class="head-actions">
        <el-button v-if="isGuard" type="primary" @click="goSecurity">身份与安全</el-button>
        <template v-else>
          <el-button :icon="ChatDotRound" @click="chat">试聊</el-button>
          <el-button type="primary" :icon="VideoPlay" :loading="launching" @click="launch">启动</el-button>
        </template>
      </div>
    </header>

    <el-tabs v-model="section" class="detail-tabs">
      <el-tab-pane label="概览" name="overview" />
      <el-tab-pane label="人格" name="persona" :disabled="isGuard" />
      <el-tab-pane label="记忆" name="memory" :disabled="isGuard" />
      <el-tab-pane label="身体" name="bodies" :disabled="isGuard" />
      <el-tab-pane label="活动" name="activity" />
    </el-tabs>

    <div v-if="section === 'overview'" class="overview-grid">
      <article><span>人格画像</span><p>{{ character.portrait || (isGuard ? 'Guard 控制面身份' : '尚未填写') }}</p></article>
      <article><span>关系</span><p>{{ relationship.narrative || (isGuard ? '负责 Owner 身份识别与策略执行' : '尚未填写') }}</p></article>
      <article><span>表达</span><p>{{ expression.voice_portrait || (isGuard ? '不提供普通对话' : '尚未填写') }}</p></article>
      <article v-if="!isGuard" class="face-card">
        <span>数字人形象</span>
        <div class="face-body">
          <div class="face-preview-group">
            <div class="face-preview">
              <el-image v-if="faceImageUrl" :src="faceImageUrl" fit="cover" />
              <div v-else class="face-placeholder">默认形象</div>
            </div>
            <span class="preview-caption">静态脸</span>
          </div>
          <div v-if="idle?.ready && idleVideoUrl" class="face-preview-group">
            <div class="face-preview is-idle" title="idle 循环预览（自动播放）">
              <video :src="idleVideoUrl" autoplay loop muted playsinline />
              <span class="idle-badge">▶ 循环</span>
            </div>
            <span class="preview-caption is-idle">idle 循环</span>
          </div>
          <div class="face-meta">
            <p v-if="face">已配置形象 · v{{ face.version }} · {{ face.width }}×{{ face.height }}<br>说话时用这张脸；静息播放生成的 idle 动画（未生成时回退微动 / 粒子头）。</p>
            <p v-else>说话时使用服务默认形象。上传一张清晰正脸图，作为数字人说话时的形象。</p>
            <div v-if="face && idle" class="idle-row">
              <el-tag :type="idleTagType(idle.tone)" size="small" effect="plain">
                idle 动画：{{ idle.label }}
                <el-icon v-if="idle.generating" class="is-loading"><Loading /></el-icon>
              </el-tag>
              <span class="idle-hint">{{ idle.hint }}</span>
            </div>
            <div class="face-actions">
              <el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="onFaceSelected">
                <el-button type="primary" :loading="faceUploading">{{ face ? '更换形象' : '上传形象' }}</el-button>
              </el-upload>
              <el-button
                v-if="face && idle?.canRegenerate"
                :icon="Refresh"
                :loading="idleRegenerating"
                @click="regenerateIdle"
              >{{ idle.status === 'failed' ? '重试生成 idle' : '重新生成 idle' }}</el-button>
              <el-button v-if="face" text @click="clearFace">恢复默认</el-button>
            </div>
          </div>
        </div>
      </article>
      <article class="stats"><span>资源</span><div><b>{{ devices.length }}</b> 身体 · <b>{{ companionRealms.length }}</b> 记忆域 · <b>{{ companionConversations.length }}</b> 对话</div></article>
    </div>

    <div v-else-if="section === 'persona'" class="panel-stack">
      <section class="info-panel">
        <header><div><h2>当前人格</h2><p>v{{ currentGenome?.version || 1 }} · {{ currentGenome?.schema_version || 'unknown schema' }}</p></div><el-tag type="success">{{ currentGenome?.status || 'unknown' }}</el-tag></header>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="自我概念">{{ constitution.self_concept || '—' }}</el-descriptions-item>
          <el-descriptions-item label="原型">{{ constitution.archetype || '—' }}</el-descriptions-item>
          <el-descriptions-item label="人格画像">{{ character.portrait || '—' }}</el-descriptions-item>
          <el-descriptions-item label="表达画像">{{ expression.voice_portrait || '—' }}</el-descriptions-item>
          <el-descriptions-item label="关系" :span="2">{{ relationship.narrative || '—' }}</el-descriptions-item>
        </el-descriptions>
      </section>
      <section class="info-panel">
        <header><div><h2>版本历史</h2><p>版本操作集中在这里，避免误触。</p></div><el-button type="warning" plain :loading="resetting" @click="resetPersona">回到初始版本</el-button></header>
        <el-table :data="genomes.history" size="small" stripe>
          <el-table-column prop="version" label="版本" width="90" />
          <el-table-column prop="status" label="状态" width="110" />
          <el-table-column prop="change_summary" label="变更摘要" min-width="220" />
          <el-table-column label="创建时间" width="180"><template #default="{ row }">{{ formatTimestamp(row.created_at) }}</template></el-table-column>
        </el-table>
      </section>
    </div>

    <section v-else-if="section === 'memory'" class="info-panel">
      <header><div><h2>记忆域</h2><p>当前 Companion 使用的长期记忆空间。</p></div></header>
      <el-table :data="companionRealms" size="small" stripe>
        <el-table-column prop="realm_id" label="Realm" min-width="220" />
        <el-table-column prop="engine" label="Engine" width="150" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column label="更新时间" width="180"><template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template></el-table-column>
      </el-table>
    </section>

    <section v-else-if="section === 'bodies'" class="info-panel">
      <header><div><h2>绑定身体</h2><p>Web Body 与物理设备统一展示。</p></div><el-button :icon="Monitor" @click="goDevices">打开设备中心</el-button></header>
      <el-table :data="devices" size="small" stripe>
        <el-table-column label="设备" min-width="220"><template #default="{ row }"><strong>{{ row.name || row.device_id }}</strong><br><code>{{ row.device_id }}</code></template></el-table-column>
        <el-table-column prop="kind" label="类型" width="120" />
        <el-table-column prop="interaction_mode" label="交互模式" width="140" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column label="最近在线" width="180"><template #default="{ row }">{{ formatTimestamp(row.last_seen_at) }}</template></el-table-column>
      </el-table>
    </section>

    <div v-else-if="section === 'activity'" class="activity-grid">
      <section class="info-panel"><header><div><h2>最近对话</h2><p>{{ companionConversations.length }} 条</p></div></header><el-table :data="companionConversations.slice(0, 20)" size="small"><el-table-column prop="title" label="标题" min-width="180" /><el-table-column prop="status" label="状态" width="100" /><el-table-column label="更新时间" width="180"><template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template></el-table-column></el-table></section>
      <section class="info-panel"><header><div><h2>最近事件</h2><p>{{ companionEvents.length }} 条</p></div></header><el-table :data="companionEvents.slice(0, 20)" size="small"><el-table-column prop="event_type" label="事件" min-width="180" /><el-table-column prop="outcome" label="结果" width="100" /><el-table-column label="时间" width="180"><template #default="{ row }">{{ formatTimestamp(row.occurred_at || row.created_at) }}</template></el-table-column></el-table></section>
    </div>
  </section>
</template>

<style scoped>
.companion-detail { display: flex; width: min(1180px, 100%); margin: 0 auto; padding-bottom: 32px; flex-direction: column; gap: 14px; }
.page-head { display: flex; flex-direction: column; gap: 12px; padding: 16px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.identity, .head-tags, .head-actions, .info-panel > header { display: flex; align-items: center; }
.identity { justify-content: space-between; gap: 16px; }
.identity p { margin: 0; color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: .12em; }
.identity h1 { margin: 4px 0; color: var(--eid-text-primary); font-size: 24px; }
.identity code, .info-panel code { color: var(--eid-text-muted); font-size: 10px; }
.head-tags, .head-actions { gap: 8px; }
.overview-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.overview-grid article, .info-panel { padding: 16px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.overview-grid article.stats, .overview-grid article.face-card { grid-column: 1 / -1; }
.face-card .face-body { display: flex; gap: 16px; margin-top: 10px; align-items: flex-start; }
.face-preview-group { display: flex; flex-direction: column; align-items: center; gap: 6px; flex: 0 0 auto; }
.preview-caption { color: var(--eid-text-muted); font-size: 11px; }
.preview-caption.is-idle { color: var(--eid-accent, #22d3ee); }
.face-preview { position: relative; width: 112px; height: 112px; border-radius: var(--eid-radius); overflow: hidden; border: 1px solid var(--eid-border); background: var(--eid-bg-elevated, rgba(255,255,255,.03)); }
.face-preview.is-idle { border-color: var(--eid-accent, #22d3ee); box-shadow: 0 0 0 1px var(--eid-accent, #22d3ee) inset; }
.idle-badge { position: absolute; left: 6px; top: 6px; padding: 1px 7px; border-radius: 999px; font-size: 10px; line-height: 1.6; background: rgba(0,0,0,.6); color: #fff; letter-spacing: .04em; }
.face-preview .el-image, .face-preview video { width: 100%; height: 100%; display: block; object-fit: cover; }
.face-placeholder { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; color: var(--eid-text-muted); font-size: 12px; }
.face-meta { display: flex; flex-direction: column; gap: 10px; }
.idle-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.idle-row .el-tag { display: inline-flex; align-items: center; gap: 4px; }
.idle-hint { color: var(--eid-text-muted); font-size: 11px; line-height: 1.5; }
.face-actions { display: flex; align-items: center; gap: 10px; }
.overview-grid span { color: var(--eid-text-muted); font-size: 10px; }
.overview-grid p { color: var(--eid-text-secondary); font-size: 13px; line-height: 1.6; }
.overview-grid .stats div { margin-top: 8px; color: var(--eid-text-secondary); }
.overview-grid .stats b { color: var(--eid-text-primary); font-size: 20px; }
.panel-stack, .activity-grid { display: grid; gap: 12px; }
.activity-grid { grid-template-columns: 1fr 1fr; }
.info-panel > header { justify-content: space-between; gap: 14px; margin-bottom: 12px; }
.info-panel h2 { margin: 0; color: var(--eid-text-primary); font-size: 16px; }
.info-panel header p { margin: 4px 0 0; color: var(--eid-text-muted); font-size: 11px; }
@media (max-width: 760px) { .identity { align-items: flex-start; flex-direction: column; } .overview-grid, .activity-grid { grid-template-columns: 1fr; } .overview-grid article.stats { grid-column: auto; } }
</style>
