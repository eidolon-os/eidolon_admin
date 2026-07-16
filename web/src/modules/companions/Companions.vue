<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Plus, Setting, VideoPlay } from '@element-plus/icons-vue'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import TableSkeleton from '@/modules/common/TableSkeleton.vue'
import { useOwnersStore } from '@/stores/owners'
import {
  createCompanionWebBody,
  listOwnerCompanions,
  listOwnerPersonaGenomes,
  type CompanionView,
  type PersonaGenomeView,
} from '@/api/eidolonData'
import { extractErrorMessage } from '@/utils/format'
import { webBodyLaunchUrl } from '@/utils/clientWeb'

const ownersStore = useOwnersStore()
const router = useRouter()
const ownerId = computed(() => ownersStore.currentId)
const companions = ref<CompanionView[]>([])
const genomes = ref<PersonaGenomeView[]>([])
const loading = ref(false)
const launching = ref('')
const query = ref('')
const statusFilter = ref('active')

const cards = computed(() => companions.value.map((companion) => {
  const versions = genomes.value.filter((genome) => genome.companion_id === companion.companion_id)
  const current = versions.find((genome) => genome.genome_id === companion.current_genome_id)
    || [...versions].sort((a, b) => b.version - a.version)[0]
    || null
  const payload = (current?.genome_json || {}) as Record<string, any>
  return {
    companion,
    current,
    character: (payload.character || {}) as Record<string, any>,
    relationship: (payload.relationship || {}) as Record<string, any>,
    expression: (payload.expression || {}) as Record<string, any>,
  }
}))

const visibleCards = computed(() => cards.value.filter((card) => {
  if (statusFilter.value !== 'all' && card.companion.status !== statusFilter.value) return false
  const needle = query.value.trim().toLowerCase()
  if (!needle) return true
  return `${card.companion.display_name} ${card.companion.companion_id}`.toLowerCase().includes(needle)
}))

const companionGroups = computed(() => [
  {
    key: 'primary',
    title: '主要伙伴',
    hint: '当前 Eidolon 默认使用的长期伙伴',
    cards: visibleCards.value.filter((card) => isPrimary(card.companion) && !isGuard(card.companion)),
  },
  {
    key: 'additional',
    title: '其他伙伴',
    hint: '拥有独立人格、记忆和身体的其他 Companion',
    cards: visibleCards.value.filter((card) => !isPrimary(card.companion) && !isGuard(card.companion)),
  },
])

const systemCards = computed(() => visibleCards.value.filter((card) => isGuard(card.companion)))

onMounted(load)
watch(ownerId, load)

async function load() {
  if (!ownerId.value) {
    companions.value = []
    genomes.value = []
    return
  }
  loading.value = true
  try {
    const [companionRows, genomeRows] = await Promise.all([
      listOwnerCompanions(ownerId.value),
      listOwnerPersonaGenomes(ownerId.value),
    ])
    companions.value = companionRows
    genomes.value = genomeRows
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function createCompanion() {
  router.push({ name: 'companion-create', query: { owner_id: ownerId.value || undefined } })
}

function openDetail(companion: CompanionView, section = 'overview') {
  router.push({
    name: 'companion-detail',
    params: { companionId: companion.companion_id, section },
    query: { owner_id: ownerId.value || undefined },
  })
}

function openSecurity() {
  router.push({ name: 'identity-security', query: { owner_id: ownerId.value || undefined } })
}

function openChatTest(companion: CompanionView) {
  router.push({
    name: 'feature',
    params: { serviceId: 'agent', feature: 'chat-test' },
    query: { owner_id: ownerId.value, companion_id: companion.companion_id },
  })
}

function isPrimary(companion: CompanionView) {
  return companion.companion_type === 'master' || companion.is_master
}

function isGuard(companion: CompanionView) {
  return companion.kind === 'guard' || companion.companion_type === 'guard'
}

async function launchWebBody(companion: CompanionView) {
  if (!ownerId.value) return
  launching.value = companion.companion_id
  try {
    const body = await createCompanionWebBody(ownerId.value, companion.companion_id)
    window.open(webBodyLaunchUrl({
      ownerId: ownerId.value,
      companionId: companion.companion_id,
      deviceId: body.device_id,
    }), '_blank', 'noopener')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  } finally {
    launching.value = ''
  }
}
</script>

<template>
  <CatalogPage title="Companions" hint="伙伴、人格与身体关系">
    <template #head-actions>
      <el-button size="small" @click="load">刷新</el-button>
      <el-button size="small" type="primary" :icon="Plus" @click="createCompanion">新建伙伴</el-button>
    </template>

    <div class="filter-bar">
      <el-input v-model="query" clearable placeholder="搜索伙伴名称或 ID" style="max-width: 320px" />
      <el-select v-model="statusFilter" style="width: 150px">
        <el-option label="Active" value="active" />
        <el-option label="全部状态" value="all" />
        <el-option label="Archived" value="archived" />
      </el-select>
    </div>

    <TableSkeleton v-if="loading && !cards.length" :rows="5" />

    <div v-else class="companion-sections">
      <section v-for="group in companionGroups" :key="group.key" class="companion-section">
        <header class="section-head">
          <div><h2>{{ group.title }}</h2><p>{{ group.hint }}</p></div>
          <el-tag size="small" type="info" effect="plain">{{ group.cards.length }}</el-tag>
        </header>
        <div class="companion-grid">
          <article v-for="card in group.cards" :key="card.companion.companion_id" class="companion-card" :class="{ primary: group.key === 'primary' }">
            <header>
              <div><strong>{{ card.companion.display_name || card.companion.companion_id }}</strong><code>{{ card.companion.companion_id }}</code></div>
              <div class="tags">
                <el-tag v-if="group.key === 'primary'" size="small" type="warning" effect="dark">主要伙伴</el-tag>
                <el-tag size="small" type="success">v{{ card.current?.version || 1 }}</el-tag>
                <el-tag size="small" effect="plain">{{ card.companion.status }}</el-tag>
              </div>
            </header>
            <div class="semantic-grid">
              <section><span>人格画像</span><p>{{ card.character.portrait || '尚未填写' }}</p></section>
              <section><span>关系</span><p>{{ card.relationship.narrative || '尚未填写' }}</p></section>
              <section><span>表达</span><p>{{ card.expression.voice_portrait || '尚未填写' }}</p></section>
            </div>
            <footer>
              <el-button size="small" :icon="Setting" @click="openDetail(card.companion)">管理</el-button>
              <div class="actions">
                <el-button size="small" :icon="ChatDotRound" @click="openChatTest(card.companion)">试聊</el-button>
                <el-button size="small" type="primary" plain :icon="VideoPlay" :loading="launching === card.companion.companion_id" @click="launchWebBody(card.companion)">启动</el-button>
              </div>
            </footer>
          </article>
          <div v-if="!group.cards.length" class="section-empty">当前没有{{ group.title }}</div>
        </div>
      </section>

      <section v-if="systemCards.length" class="companion-section system-section">
        <header class="section-head">
          <div><h2>系统身份</h2><p>用于身份识别和控制面的特殊 Companion，不提供普通对话或 Web Body。</p></div>
          <el-tag size="small" type="info" effect="plain">{{ systemCards.length }}</el-tag>
        </header>
        <article v-for="card in systemCards" :key="card.companion.companion_id" class="system-card">
          <div><strong>{{ card.companion.display_name || 'Guard' }}</strong><code>{{ card.companion.companion_id }}</code></div>
          <div class="system-copy"><span>Guard</span><p>管理设备绑定、Owner Face 和身份策略下发。</p></div>
          <el-button :icon="Setting" @click="openSecurity">前往身份与安全</el-button>
        </article>
      </section>

      <el-empty v-if="!visibleCards.length" description="当前筛选下没有伙伴" />
    </div>
  </CatalogPage>
</template>

<style scoped>
.filter-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; padding: 12px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.companion-sections { display: flex; flex-direction: column; gap: 22px; }
.companion-section { display: flex; flex-direction: column; gap: 10px; }
.section-head, .companion-card > header, .companion-card > footer, .tags, .actions, .system-card { display: flex; align-items: center; }
.section-head { justify-content: space-between; gap: 14px; }
.section-head h2 { margin: 0; color: var(--eid-text-primary); font-size: 16px; }
.section-head p { margin: 4px 0 0; color: var(--eid-text-muted); font-size: 11px; }
.companion-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 12px; }
.companion-card { min-width: 0; padding: 16px; border: 1px solid var(--eid-border); border-radius: 8px; background: var(--eid-bg-panel); }
.companion-card.primary { border-color: color-mix(in srgb, var(--el-color-warning) 55%, var(--eid-border)); }
.companion-card > header, .companion-card > footer { justify-content: space-between; gap: 14px; }
.companion-card header strong, .companion-card header code, .system-card strong, .system-card code { display: block; }
.companion-card header strong, .system-card strong { color: var(--eid-text-primary); font-size: 16px; }
.companion-card header code, .system-card code { margin-top: 4px; color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; overflow-wrap: anywhere; }
.tags, .actions { gap: 7px; }
.semantic-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 14px 0; padding: 13px 0; border-block: 1px solid var(--eid-border); }
.semantic-grid span { color: var(--eid-text-muted); font-size: 10px; }
.semantic-grid p { display: -webkit-box; margin: 6px 0 0; overflow: hidden; color: var(--eid-text-secondary); font-size: 12px; line-height: 1.55; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
.section-empty { padding: 18px; border: 1px dashed var(--eid-border); border-radius: 8px; color: var(--eid-text-muted); font-size: 12px; text-align: center; }
.system-section { padding-top: 6px; border-top: 1px solid var(--eid-border); }
.system-card { justify-content: space-between; gap: 18px; padding: 14px; border: 1px dashed var(--eid-border); border-radius: 8px; background: var(--eid-bg-panel); }
.system-copy { min-width: 0; flex: 1; }
.system-copy span { color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; }
.system-copy p { margin: 4px 0 0; color: var(--eid-text-secondary); font-size: 12px; }
@media (max-width: 760px) { .filter-bar { align-items: stretch; flex-direction: column; } .companion-grid { grid-template-columns: 1fr; } .semantic-grid { grid-template-columns: 1fr; } .system-card { align-items: flex-start; flex-direction: column; } }
</style>
