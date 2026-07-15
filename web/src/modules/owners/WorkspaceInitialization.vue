<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import { getOwnerOverview, initializeOwnerWorkspace, type OwnerOverviewResponse } from '@/api/eidolonData'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage } from '@/utils/format'

const router = useRouter()
const ownersStore = useOwnersStore()
const loading = ref(false)
const initializing = ref(false)
const overview = ref<OwnerOverviewResponse | null>(null)
const ownerId = computed(() => ownersStore.currentId)

const initForm = ref({
  companion_id: '',
  companion_display_name: '',
  companion_kind: 'companion',
  companion_profile_json: '{}',
  companion_runtime_config_json: '{}',
  companion_metadata_json: '{}',
  genome_id: '',
  genome_source_json: '{}',
  genome_json: JSON.stringify({
    schema_version: 'eidolon.persona_genome',
    constitution: {
      name: 'Companion',
      archetype: 'companion',
      self_concept: '',
      values: ['be warm', 'be honest', 'respect owner sovereignty'],
      boundaries: [],
    },
    character: {
      portrait: 'A grounded, attentive long-term companion.',
      traits: {
        'core.playfulness': { value: 0.5, confidence: 0.5, source: 'template' },
        'core.grounding': { value: 0.65, confidence: 0.5, source: 'template' },
        'core.structure': { value: 0.55, confidence: 0.5, source: 'template' },
      },
      tensions: [],
      growth_edges: [],
    },
    relationship: {
      stage: 'new',
      narrative: '',
      commitments: [],
      pinned_facts: [],
      owner_preferences: {},
      safety_boundaries: [],
    },
    expression: {
      voice_portrait: 'Warm, clear, and grounded.',
      behavior_guidance: [],
      dialogue_examples: [],
      modality_notes: {},
      signature_phrases: {},
    },
    memory_policy: { recall_policy: {}, relation_policies: {} },
    evolution_policy: {
      enabled: true,
      auto_apply_low_risk: true,
      max_delta_per_commit: 0.05,
      review_required_traits: [],
    },
    provenance: { origin: 'owner_authored', base_genome_id: null, evidence_refs: [] },
  }, null, 2),
  realm_id: '',
  memory_engine: 'mempalace',
  memory_engine_config_json: '{}',
  memory_policy_json: JSON.stringify({ scope: 'owner', recall: 'companion_default' }, null, 2),
})

onMounted(async () => {
  await ownersStore.load()
  await loadOverview()
})

watch(ownerId, loadOverview)

async function loadOverview() {
  overview.value = null
  if (!ownerId.value) return
  loading.value = true
  try {
    overview.value = await getOwnerOverview(ownerId.value)
  } catch (error) {
    ElMessage.error(`加载空间状态失败: ${extractErrorMessage(error)}`)
  } finally {
    loading.value = false
  }
}

async function submitInitialize() {
  if (!ownerId.value) {
    ElMessage.warning('请先选择一个 Eidolon 空间')
    return
  }
  initializing.value = true
  try {
    const companionName = initForm.value.companion_display_name.trim()
    await initializeOwnerWorkspace(ownerId.value, {
      companion_id: nullableText(initForm.value.companion_id),
      companion_display_name: companionName,
      companion_kind: initForm.value.companion_kind,
      companion_profile_json: parseJson(initForm.value.companion_profile_json, 'Companion profile JSON'),
      companion_runtime_config_json: parseJson(initForm.value.companion_runtime_config_json, 'Companion runtime JSON'),
      companion_metadata_json: parseJson(initForm.value.companion_metadata_json, 'Companion metadata JSON'),
      genome_id: nullableText(initForm.value.genome_id),
      genome_source_json: parseJson(initForm.value.genome_source_json, 'Genome source JSON'),
      genome_json: normalizedGenomeJson(companionName),
      realm_id: nullableText(initForm.value.realm_id),
      memory_engine: initForm.value.memory_engine.trim() || 'mempalace',
      memory_engine_config_json: parseJson(initForm.value.memory_engine_config_json, 'Memory engine config JSON'),
      memory_policy_json: parseJson(initForm.value.memory_policy_json, 'Memory policy JSON'),
    })
    ElMessage.success('Companion 工作区已初始化')
    await router.push({ name: 'companions', query: { owner_id: ownerId.value } })
  } catch (error) {
    ElMessage.error(`初始化失败: ${extractErrorMessage(error)}`)
  } finally {
    initializing.value = false
  }
}

function nullableText(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function parseJson(value: string, label: string): Record<string, any> {
  try {
    return JSON.parse(value || '{}')
  } catch {
    throw new Error(`${label} 格式不正确`)
  }
}

function normalizedGenomeJson(companionName: string): Record<string, any> {
  const genome = parseJson(initForm.value.genome_json, 'Genome JSON')
  const constitution = { ...(genome.constitution || {}) }
  if (!String(constitution.name || '').trim()) constitution.name = companionName || 'Companion'
  if (!String(constitution.archetype || '').trim()) constitution.archetype = 'companion'
  genome.schema_version = 'eidolon.persona_genome'
  genome.constitution = constitution
  return genome
}
</script>

<template>
  <CatalogPage
    title="Workspace Initialization"
    hint="高级初始化：直接创建 Companion、Persona Genome 与 Memory Realm。普通创建请使用 Companions / 新建伙伴。"
  >
    <template #head-actions>
      <el-tag size="small" type="info" effect="plain">{{ ownersStore.currentOwner?.display_name || ownerId || '未选择空间' }}</el-tag>
      <el-button size="small" :loading="loading" @click="loadOverview">刷新状态</el-button>
    </template>

    <el-empty v-if="!ownerId" description="请先从顶部选择一个 Eidolon 空间" />

    <template v-else>
      <el-alert
        v-if="overview?.initialized"
        title="当前空间已有 Companion；提交后会继续创建新的 Companion 工作区。"
        type="info"
        :closable="false"
        show-icon
        class="init-alert"
      />
      <el-alert
        title="此页面会写入原始配置。请确认 JSON 结构和 ID 后再提交。"
        type="warning"
        :closable="false"
        show-icon
        class="init-alert"
      />

      <el-form label-position="top" class="init-form" @submit.prevent="submitInitialize">
        <section class="init-block">
          <h3>Companion</h3>
          <div class="form-grid">
            <el-form-item label="companion_id"><el-input v-model="initForm.companion_id" placeholder="c:owner-default:default" /></el-form-item>
            <el-form-item label="显示名"><el-input v-model="initForm.companion_display_name" placeholder="Eidolon Companion" /></el-form-item>
            <el-form-item label="kind"><el-input v-model="initForm.companion_kind" /></el-form-item>
          </div>
          <el-collapse>
            <el-collapse-item title="Advanced JSON" name="companion-json">
              <div class="json-grid">
                <el-form-item label="profile_json"><el-input v-model="initForm.companion_profile_json" type="textarea" :rows="5" /></el-form-item>
                <el-form-item label="runtime_config_json"><el-input v-model="initForm.companion_runtime_config_json" type="textarea" :rows="5" /></el-form-item>
                <el-form-item label="metadata_json"><el-input v-model="initForm.companion_metadata_json" type="textarea" :rows="5" /></el-form-item>
              </div>
            </el-collapse-item>
          </el-collapse>
        </section>

        <section class="init-block">
          <h3>Persona Genome</h3>
          <el-form-item label="genome_id"><el-input v-model="initForm.genome_id" placeholder="g:owner-default:origin" /></el-form-item>
          <el-form-item label="genome_json"><el-input v-model="initForm.genome_json" type="textarea" :rows="14" /></el-form-item>
          <el-collapse>
            <el-collapse-item title="Source JSON" name="source-json">
              <el-form-item label="source_json"><el-input v-model="initForm.genome_source_json" type="textarea" :rows="5" /></el-form-item>
            </el-collapse-item>
          </el-collapse>
        </section>

        <section class="init-block">
          <h3>Memory Realm</h3>
          <div class="form-grid">
            <el-form-item label="realm_id"><el-input v-model="initForm.realm_id" placeholder="r:owner-default:default" /></el-form-item>
            <el-form-item label="engine"><el-input v-model="initForm.memory_engine" /></el-form-item>
          </div>
          <div class="json-grid two-columns">
            <el-form-item label="engine_config_json"><el-input v-model="initForm.memory_engine_config_json" type="textarea" :rows="7" /></el-form-item>
            <el-form-item label="policy_json"><el-input v-model="initForm.memory_policy_json" type="textarea" :rows="7" /></el-form-item>
          </div>
        </section>

        <div class="form-actions">
          <el-button type="primary" :loading="initializing" @click="submitInitialize">创建 Companion 工作区</el-button>
        </div>
      </el-form>
    </template>
  </CatalogPage>
</template>

<style scoped>
.init-alert { margin-bottom: 12px; }
.init-form { display: flex; flex-direction: column; gap: 16px; }
.init-block { padding: 16px; border: 1px solid var(--eid-border); border-radius: 8px; background: var(--eid-bg-panel); }
.init-block h3 { margin: 0 0 14px; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.json-grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
.json-grid.two-columns { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.form-actions { display: flex; justify-content: flex-end; }
</style>
