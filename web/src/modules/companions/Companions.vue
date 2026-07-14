<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatDotRound, Plus, VideoPlay } from '@element-plus/icons-vue'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import TableSkeleton from '@/modules/common/TableSkeleton.vue'
import { useOwnersStore } from '@/stores/owners'
import {
  createCompanionWebBody,
  listOwnerCompanions,
  listOwnerPersonaGenomes,
  resetCompanionGenome,
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

const cards = computed(() => companions.value.map((companion) => {
  const versions = genomes.value.filter((genome) => genome.companion_id === companion.companion_id)
  const current = versions.find((genome) => genome.genome_id === companion.current_genome_id)
    || [...versions].sort((a, b) => b.version - a.version)[0]
    || null
  const payload = (current?.genome_json || {}) as Record<string, any>
  return {
    companion,
    current,
    constitution: (payload.constitution || {}) as Record<string, any>,
    character: (payload.character || {}) as Record<string, any>,
    relationship: (payload.relationship || {}) as Record<string, any>,
    expression: (payload.expression || {}) as Record<string, any>,
  }
}))

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
  router.push({ name: 'companion-create' })
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

async function reset(companion: CompanionView) {
  try {
    await ElMessageBox.confirm(
      `将 ${companion.display_name || companion.companion_id} 切回最初的 committed genome？`,
      '重置人格版本',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await resetCompanionGenome(ownerId.value, companion.companion_id)
    ElMessage.success('已切回初始版本')
    await load()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error))
  }
}
</script>

<template>
  <CatalogPage title="Companions" hint="伙伴与当前语义人格快照">
    <template #head-actions>
      <el-button size="small" @click="load">刷新</el-button>
      <el-button size="small" type="primary" :icon="Plus" @click="createCompanion">新建伙伴</el-button>
    </template>

    <TableSkeleton v-if="loading && !cards.length" :rows="5" />

    <div v-else class="companion-list">
      <article v-for="card in cards" :key="card.companion.companion_id" class="companion-row">
        <header>
          <div>
            <strong>{{ card.companion.display_name || card.companion.companion_id }}</strong>
            <code>{{ card.companion.companion_id }}</code>
          </div>
          <div class="tags">
            <el-tag v-if="isPrimary(card.companion)" size="small" type="warning" effect="dark">默认</el-tag>
            <el-tag v-if="isGuard(card.companion)" size="small" type="info" effect="dark">Guard</el-tag>
            <el-tag size="small" type="success">v{{ card.current?.version || 1 }}</el-tag>
          </div>
        </header>

        <p v-if="isGuard(card.companion)" class="guard-note">
          Guard 控制面身份：独立绑定设备和兼容工作区，不提供普通聊天或 Web Body。
        </p>
        <div v-else class="semantic-grid">
          <section>
            <span>人格画像</span>
            <p>{{ card.character.portrait || '尚未填写' }}</p>
          </section>
          <section>
            <span>关系</span>
            <p>{{ card.relationship.narrative || '尚未填写' }}</p>
          </section>
          <section>
            <span>表达</span>
            <p>{{ card.expression.voice_portrait || '尚未填写' }}</p>
          </section>
        </div>

        <footer>
          <code class="hash">{{ card.current?.genome_hash }}</code>
          <div class="actions">
            <el-button v-if="!isGuard(card.companion)" size="small" :icon="ChatDotRound" @click="openChatTest(card.companion)">试聊</el-button>
            <el-button
              v-if="!isGuard(card.companion)"
              size="small"
              :icon="VideoPlay"
              :loading="launching === card.companion.companion_id"
              @click="launchWebBody(card.companion)"
            >启动</el-button>
            <el-button v-if="!isGuard(card.companion) && (card.current?.version || 1) > 1" size="small" @click="reset(card.companion)">重置版本</el-button>
          </div>
        </footer>
      </article>
      <el-empty v-if="!cards.length" description="还没有伙伴" />
    </div>
  </CatalogPage>
</template>

<style scoped>
.companion-list {
  display: grid;
  gap: 12px;
}
.companion-row {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--eid-border);
  border-radius: 8px;
  background: var(--eid-bg-panel);
}
.companion-row header,
.companion-row footer,
.tags,
.actions {
  display: flex;
  align-items: center;
}
.companion-row header,
.companion-row footer {
  justify-content: space-between;
  gap: 14px;
}
.companion-row header strong,
.companion-row header code {
  display: block;
}
.companion-row header strong {
  color: var(--eid-text-primary);
  font-size: 16px;
}
.companion-row header code,
.hash {
  margin-top: 4px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.tags,
.actions { gap: 8px; }
.semantic-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin: 16px 0;
  padding: 14px 0;
  border-block: 1px solid var(--eid-border);
}
.semantic-grid section { min-width: 0; }
.guard-note {
  margin: 16px 0;
  padding: 14px;
  border: 1px solid var(--eid-border);
  border-radius: 6px;
  color: var(--eid-text-muted);
}
.semantic-grid span {
  color: var(--eid-text-muted);
  font-size: 11px;
}
.semantic-grid p {
  margin: 7px 0 0;
  color: var(--eid-text-secondary);
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.hash { min-width: 0; margin: 0; }
@media (max-width: 760px) {
  .semantic-grid { grid-template-columns: 1fr; gap: 12px; }
  .companion-row footer { align-items: flex-start; flex-direction: column; }
  .actions { width: 100%; flex-wrap: wrap; }
}
</style>
