<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowRight, ChatDotRound, Close, UserFilled } from '@element-plus/icons-vue'
import {
  createOnboardingCompanion,
  getOnboardingState,
  getPersonaAuthoringDefaults,
  initializeOnboarding,
  previewPersonaAuthoring,
  type OnboardingCompanionCreateRequest,
  type OnboardingState,
  type PersonaAuthoringDraft,
  type PersonaGenomePreview,
} from '@/api/onboarding'
import { useOwnersStore } from '@/stores/owners'
import SemanticListEditor from './components/SemanticListEditor.vue'

const ownersStore = useOwnersStore()
const router = useRouter()
const loading = ref(true)
const submitting = ref(false)
const previewing = ref(false)
const step = ref(0)
const state = ref<OnboardingState | null>(null)
const preview = ref<PersonaGenomePreview | null>(null)

const form = reactive({
  owner_display_name: '',
  companion_display_name: '',
  self_concept: '',
  character_portrait: '',
  relationship_narrative: '',
  voice_portrait: '',
  values: [] as string[],
  boundaries: [] as string[],
  commitments: [] as string[],
  behavior_guidance: [] as string[],
  dialogue_examples: [] as string[],
  pinned_facts: [] as string[],
  safety_boundaries: [] as string[],
})

const hasOwner = computed(() => Boolean(state.value?.owner))
const ownerId = computed(() => state.value?.owner?.owner_id || ownersStore.currentId || '')
const draftKey = computed(() => `eidolon.persona-authoring.${ownerId.value || 'new-owner'}`)
const steps = ['TA 是谁', '你们的关系', 'TA 如何表达', '确认']

const cleanPayload = computed<OnboardingCompanionCreateRequest>(() => ({
  owner_id: ownerId.value || undefined,
  companion_display_name: form.companion_display_name.trim(),
  self_concept: form.self_concept.trim(),
  character_portrait: form.character_portrait.trim(),
  relationship_narrative: form.relationship_narrative.trim(),
  voice_portrait: form.voice_portrait.trim(),
  values: cleanList(form.values),
  boundaries: cleanList(form.boundaries),
  commitments: cleanList(form.commitments),
  behavior_guidance: cleanList(form.behavior_guidance),
  dialogue_examples: cleanList(form.dialogue_examples),
  pinned_facts: cleanList(form.pinned_facts),
  safety_boundaries: cleanList(form.safety_boundaries),
  create_web_device: false,
}))

const reviewSections = computed(() => {
  const genome = preview.value
  if (!genome) return []
  return [
    {
      title: '自我认知',
      prose: genome.constitution.self_concept,
      items: genome.constitution.values,
    },
    {
      title: '人格画像',
      prose: genome.character.portrait,
      items: genome.constitution.boundaries,
    },
    {
      title: '关系',
      prose: genome.relationship.narrative,
      items: [...genome.relationship.commitments, ...genome.relationship.pinned_facts],
    },
    {
      title: '表达',
      prose: genome.expression.voice_portrait,
      items: genome.expression.behavior_guidance,
    },
  ]
})

onMounted(async () => {
  try {
    await ownersStore.load(true)
    state.value = await getOnboardingState(ownersStore.currentId || undefined)
    const defaults = await getPersonaAuthoringDefaults()
    applyDefaults(defaults)
    restoreDraft()
  } finally {
    loading.value = false
  }
})

watch(
  form,
  () => {
    if (loading.value) return
    localStorage.setItem(draftKey.value, JSON.stringify(form))
    preview.value = null
  },
  { deep: true },
)

function applyDefaults(draft: PersonaAuthoringDraft) {
  form.self_concept = draft.self_concept
  form.character_portrait = draft.character_portrait
  form.relationship_narrative = draft.relationship_narrative
  form.voice_portrait = draft.voice_portrait
  form.values = [...draft.values]
  form.boundaries = [...draft.boundaries]
  form.commitments = [...draft.commitments]
  form.behavior_guidance = [...draft.behavior_guidance]
  form.dialogue_examples = [...draft.dialogue_examples]
  form.pinned_facts = [...draft.pinned_facts]
  form.safety_boundaries = [...draft.safety_boundaries]
}

function restoreDraft() {
  const raw = localStorage.getItem(draftKey.value)
  if (!raw) return
  try {
    const saved = JSON.parse(raw)
    Object.assign(form, saved)
  } catch {
    localStorage.removeItem(draftKey.value)
  }
}

function cleanList(items: string[]) {
  return items.map((item) => item.trim()).filter(Boolean)
}

function validateCurrentStep() {
  if (step.value === 0) {
    if (!hasOwner.value && !form.owner_display_name.trim()) {
      ElMessage.warning('请填写你的身份名')
      return false
    }
    if (!form.companion_display_name.trim()) {
      ElMessage.warning('请给伙伴一个名字')
      return false
    }
    if (!form.character_portrait.trim()) {
      ElMessage.warning('请先写下 TA 的人格画像')
      return false
    }
  }
  return true
}

async function next() {
  if (!validateCurrentStep()) return
  if (step.value === 2) {
    previewing.value = true
    try {
      preview.value = await previewPersonaAuthoring(cleanPayload.value)
    } finally {
      previewing.value = false
    }
  }
  step.value = Math.min(3, step.value + 1)
}

function back() {
  step.value = Math.max(0, step.value - 1)
}

async function createAndChat() {
  if (!validateCurrentStep()) return
  submitting.value = true
  try {
    let createdOwnerId = ownerId.value
    let companionId = ''
    if (hasOwner.value) {
      const response = await createOnboardingCompanion(cleanPayload.value)
      createdOwnerId = response.state.owner?.owner_id || createdOwnerId
      companionId = response.companion.companion_id
    } else {
      const response = await initializeOnboarding({
        ...cleanPayload.value,
        owner_display_name: form.owner_display_name.trim(),
      })
      createdOwnerId = response.state.owner?.owner_id || ''
      companionId = response.state.master_companion?.companion_id || ''
    }
    localStorage.removeItem(draftKey.value)
    await ownersStore.load(true)
    if (createdOwnerId) ownersStore.setCurrent(createdOwnerId)
    ElMessage.success('伙伴已创建')
    await router.replace({
      name: 'feature',
      params: { serviceId: 'agent', feature: 'chat-test' },
      query: { owner_id: createdOwnerId, companion_id: companionId },
    })
  } finally {
    submitting.value = false
  }
}

function close() {
  router.push({ name: 'home' })
}
</script>

<template>
  <section class="creator-page">
    <header class="creator-head">
      <div>
        <p class="eyebrow">PERSONA AUTHORING</p>
        <h1>创建伙伴</h1>
      </div>
      <el-tooltip content="返回" placement="bottom">
        <el-button circle :icon="Close" @click="close" />
      </el-tooltip>
    </header>

    <el-skeleton v-if="loading" :rows="10" animated />

    <template v-else>
      <el-steps class="creator-steps" :active="step" finish-status="success" align-center>
        <el-step v-for="item in steps" :key="item" :title="item" />
      </el-steps>

      <div class="creator-layout">
        <main class="creator-main">
          <section v-if="step === 0" class="form-section">
            <div v-if="!hasOwner" class="field-block compact-field">
              <label>你的身份名</label>
              <el-input v-model="form.owner_display_name" size="large" maxlength="48" show-word-limit />
            </div>
            <div v-else class="owner-strip">
              <el-icon><UserFilled /></el-icon>
              <span>{{ state?.owner?.display_name }}</span>
              <code>{{ state?.owner?.owner_id }}</code>
            </div>

            <div class="field-block compact-field">
              <label>伙伴名字</label>
              <el-input v-model="form.companion_display_name" size="large" maxlength="64" show-word-limit />
            </div>
            <div class="field-block">
              <label>自我认知</label>
              <el-input v-model="form.self_concept" type="textarea" :rows="3" resize="vertical" />
            </div>
            <div class="field-block">
              <label>人格画像</label>
              <el-input v-model="form.character_portrait" type="textarea" :rows="5" resize="vertical" maxlength="1200" show-word-limit />
            </div>
            <div class="two-column">
              <div class="field-block">
                <label>价值观</label>
                <SemanticListEditor v-model="form.values" placeholder="一条长期坚持的价值" />
              </div>
              <div class="field-block">
                <label>不可突破的边界</label>
                <SemanticListEditor v-model="form.boundaries" placeholder="一条绝不跨过的边界" />
              </div>
            </div>
          </section>

          <section v-else-if="step === 1" class="form-section">
            <div class="field-block">
              <label>关系叙事</label>
              <el-input v-model="form.relationship_narrative" type="textarea" :rows="5" resize="vertical" />
            </div>
            <div class="two-column">
              <div class="field-block">
                <label>关系承诺</label>
                <SemanticListEditor v-model="form.commitments" placeholder="TA 对这段关系的一条承诺" />
              </div>
              <div class="field-block">
                <label>已确认事实</label>
                <SemanticListEditor v-model="form.pinned_facts" placeholder="关于 owner 已确认的事实" />
              </div>
            </div>
            <div class="field-block">
              <label>关系安全边界</label>
              <SemanticListEditor v-model="form.safety_boundaries" placeholder="在关系中需要始终遵守的边界" />
            </div>
          </section>

          <section v-else-if="step === 2" class="form-section">
            <div class="field-block">
              <label>表达画像</label>
              <el-input v-model="form.voice_portrait" type="textarea" :rows="4" resize="vertical" />
            </div>
            <div class="field-block">
              <label>行为引导</label>
              <SemanticListEditor v-model="form.behavior_guidance" placeholder="一条可观察的表达习惯" />
            </div>
            <div class="field-block">
              <label>典型对话示例</label>
              <SemanticListEditor v-model="form.dialogue_examples" placeholder="一段能代表 TA 的自然表达" />
            </div>
          </section>

          <section v-else class="review-section">
            <div v-for="section in reviewSections" :key="section.title" class="review-band">
              <h2>{{ section.title }}</h2>
              <p v-if="section.prose">{{ section.prose }}</p>
              <ul v-if="section.items.length">
                <li v-for="item in section.items" :key="item">{{ item }}</li>
              </ul>
            </div>
          </section>

          <footer class="creator-actions">
            <el-button v-if="step > 0" :icon="ArrowLeft" @click="back">上一步</el-button>
            <span />
            <el-button v-if="step < 3" type="primary" :icon="ArrowRight" :loading="previewing" @click="next">
              继续
            </el-button>
            <el-button v-else type="primary" :icon="ChatDotRound" :loading="submitting" @click="createAndChat">
              创建并试聊
            </el-button>
          </footer>
        </main>

        <aside class="semantic-summary">
          <span>PERSONA</span>
          <strong>{{ form.companion_display_name || 'Companion' }}</strong>
          <p>{{ form.character_portrait }}</p>
          <dl>
            <div><dt>价值观</dt><dd>{{ cleanList(form.values).length }}</dd></div>
            <div><dt>边界</dt><dd>{{ cleanList(form.boundaries).length }}</dd></div>
            <div><dt>承诺</dt><dd>{{ cleanList(form.commitments).length }}</dd></div>
            <div><dt>表达引导</dt><dd>{{ cleanList(form.behavior_guidance).length }}</dd></div>
          </dl>
        </aside>
      </div>
    </template>
  </section>
</template>

<style scoped>
.creator-page {
  width: min(1180px, 100%);
  margin: 0 auto;
  padding: 4px 0 40px;
}
.creator-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.creator-head h1,
.form-section h2,
.review-band h2 {
  margin: 0;
  letter-spacing: 0;
}
.creator-head h1 { font-size: 30px; }
.eyebrow {
  margin: 0 0 6px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.08em;
}
.creator-steps {
  margin-bottom: 22px;
  padding: 18px 12px;
  border-block: 1px solid var(--eid-border);
}
.creator-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 22px;
  align-items: start;
}
.creator-main { min-width: 0; }
.form-section,
.review-section {
  display: grid;
  gap: 20px;
  min-height: 490px;
}
.field-block {
  display: grid;
  gap: 9px;
  min-width: 0;
}
.field-block > label {
  color: var(--eid-text-secondary);
  font-size: 13px;
  font-weight: 680;
}
.compact-field { max-width: 520px; }
.two-column {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  align-items: start;
}
.owner-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--eid-border);
  border-radius: 6px;
  background: var(--eid-bg-inset);
}
.owner-strip code {
  margin-left: auto;
  color: var(--eid-text-muted);
}
.creator-actions {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--eid-border);
}
.semantic-summary {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--eid-border-strong);
  border-radius: 8px;
  background: var(--eid-bg-inset);
}
.semantic-summary > span {
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
}
.semantic-summary > strong {
  color: var(--eid-text-primary);
  font-size: 20px;
}
.semantic-summary > p {
  margin: 0;
  color: var(--eid-text-secondary);
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.semantic-summary dl { margin: 4px 0 0; }
.semantic-summary dl div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0;
  border-top: 1px solid var(--eid-border);
}
.semantic-summary dt { color: var(--eid-text-muted); }
.semantic-summary dd { margin: 0; font-weight: 720; }
.review-band {
  padding: 18px 0;
  border-bottom: 1px solid var(--eid-border);
}
.review-band h2 { font-size: 16px; }
.review-band p {
  margin: 10px 0 0;
  color: var(--eid-text-secondary);
  line-height: 1.7;
}
.review-band ul {
  margin: 10px 0 0;
  padding-left: 20px;
  color: var(--eid-text-secondary);
  line-height: 1.7;
}
@media (max-width: 860px) {
  .creator-layout { grid-template-columns: 1fr; }
  .semantic-summary { position: static; order: -1; }
}
@media (max-width: 620px) {
  .creator-page { padding-inline: 2px; }
  .creator-head h1 { font-size: 25px; }
  .two-column { grid-template-columns: 1fr; }
  .creator-steps :deep(.el-step__title) { font-size: 11px; }
  .owner-strip { flex-wrap: wrap; padding-block: 8px; }
  .owner-strip code { width: 100%; margin-left: 24px; overflow-wrap: anywhere; }
}
</style>
