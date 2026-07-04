<script setup lang="ts">
// Companion-first genome authoring surface. Owners create a companion and author
// its genome directly (free-text-primary), edit it (new version), reset it to the
// authored origin, and preview the rendered prompt_markdown. Authoring proxies to
// the agent (assembly/validation there); reads + reset use eidolon_data.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import TableSkeleton from '@/modules/common/TableSkeleton.vue'
import { useOwnersStore } from '@/stores/owners'
import {
  authorCompanionGenome,
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
const ownerId = computed(() => ownersStore.currentId)

const companions = ref<CompanionView[]>([])
const genomes = ref<PersonaGenomeView[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogOpen = ref(false)
const isEdit = ref(false)

const form = reactive({
  companion_id: '',
  name: '',
  archetype: 'companion',
  description: '',
  values: '',
  taboos: '',
  unbreakable_rules: '',
  style: '',
  goals: '',
  pinned_facts: '',
  example_dialogs: '',
})

const cards = computed(() =>
  companions.value.map((c) => {
    const gs = genomes.value.filter((g) => g.companion_id === c.companion_id)
    const current =
      gs.find((g) => g.genome_id === c.current_genome_id) ||
      [...gs].sort((a, b) => b.version - a.version)[0] ||
      null
    return { companion: c, current }
  }),
)

async function load() {
  if (!ownerId.value) {
    companions.value = []
    genomes.value = []
    return
  }
  loading.value = true
  try {
    const [c, g] = await Promise.all([
      listOwnerCompanions(ownerId.value),
      listOwnerPersonaGenomes(ownerId.value),
    ])
    companions.value = c
    genomes.value = g
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(ownerId, load)

function resetForm() {
  form.companion_id = `c-${Date.now().toString(36)}`
  form.name = ''
  form.archetype = 'companion'
  form.description = ''
  form.values = ''
  form.taboos = ''
  form.unbreakable_rules = ''
  form.style = ''
  form.goals = ''
  form.pinned_facts = ''
  form.example_dialogs = ''
}

function openCreate() {
  isEdit.value = false
  resetForm()
  dialogOpen.value = true
}

function openEdit(companion: CompanionView) {
  isEdit.value = true
  resetForm()
  form.companion_id = companion.companion_id
  form.name = companion.display_name || companion.companion_id
  dialogOpen.value = true
}

function lines(value: string): string[] {
  return value
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
}

async function submit() {
  if (!ownerId.value) {
    ElMessage.warning('请先在右上角选择一个 owner')
    return
  }
  if (!form.name.trim()) {
    ElMessage.warning('请填写伙伴名字')
    return
  }
  saving.value = true
  try {
    await authorCompanionGenome(ownerId.value, form.companion_id.trim(), {
      name: form.name.trim(),
      archetype: form.archetype.trim() || 'companion',
      description: form.description.trim(),
      values: lines(form.values),
      taboos: lines(form.taboos),
      unbreakable_rules: lines(form.unbreakable_rules),
      style: lines(form.style),
      goals: lines(form.goals),
      pinned_facts: lines(form.pinned_facts),
      example_dialogs: lines(form.example_dialogs),
    })
    ElMessage.success(isEdit.value ? '已出新版本基因' : '伙伴已创建并绑定基因')
    dialogOpen.value = false
    await load()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  } finally {
    saving.value = false
  }
}

const launching = ref('')

// Ensure a host-local web body exists (idempotent), then open the standalone
// client in a new tab connected as that body. Works for any companion; the
// master already has one provisioned.
async function launchWebBody(companion: CompanionView) {
  if (!ownerId.value) return
  launching.value = companion.companion_id
  try {
    const body = await createCompanionWebBody(ownerId.value, companion.companion_id)
    const url = webBodyLaunchUrl({
      ownerId: ownerId.value,
      companionId: companion.companion_id,
      deviceId: body.device_id,
    })
    window.open(url, '_blank', 'noopener')
    ElMessage.success('已启动本机身体（新标签页）')
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  } finally {
    launching.value = ''
  }
}

async function reset(companion: CompanionView) {
  try {
    await ElMessageBox.confirm(
      `将 ${companion.display_name || companion.companion_id} 重置到最初创作版本（丢弃演化漂移）？`,
      '重置到初始',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await resetCompanionGenome(ownerId.value, companion.companion_id)
    ElMessage.success('已重置到初始版本')
    await load()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e))
  }
}
</script>

<template>
  <CatalogPage title="Companions" hint="companion-first：为伙伴直接创作性格基因（自由文本为主），编辑出新版本，或重置回最初创作版。">
    <template #head-actions>
      <el-button size="small" @click="load">刷新</el-button>
      <el-button size="small" type="primary" :icon="Plus" :disabled="!ownerId" @click="openCreate">新建伙伴</el-button>
    </template>

    <el-alert v-if="!ownerId" type="info" :closable="false" show-icon title="请先在右上角选择一个 owner，再创作伙伴。" />

    <TableSkeleton v-else-if="loading && !companions.length" :rows="5" />

    <div v-else class="cmp-grid">
      <el-card v-for="card in cards" :key="card.companion.companion_id" class="cmp-card" shadow="never">
        <div class="cmp-head">
          <div>
            <b class="cmp-name">{{ card.companion.display_name || card.companion.companion_id }}</b>
            <span class="cmp-id mono">{{ card.companion.companion_id }}</span>
          </div>
          <div class="cmp-tags">
            <el-tag v-if="card.companion.is_master" size="small" type="warning" effect="dark">★ 主</el-tag>
            <el-tag size="small" :type="card.companion.status === 'active' ? 'success' : 'info'">{{ card.companion.status }}</el-tag>
          </div>
        </div>
        <div class="cmp-meta">
          <span>genome v{{ card.current?.version ?? '—' }}</span>
          <span>{{ card.current?.status || '—' }}</span>
        </div>
        <details v-if="card.current?.prompt_markdown" class="cmp-preview">
          <summary>prompt 预览</summary>
          <pre>{{ card.current.prompt_markdown }}</pre>
        </details>
        <div class="cmp-actions">
          <el-button size="small" @click="openEdit(card.companion)">重新创作</el-button>
          <el-button size="small" @click="reset(card.companion)">重置到初始</el-button>
          <el-button
            size="small"
            type="primary"
            plain
            :loading="launching === card.companion.companion_id"
            @click="launchWebBody(card.companion)"
          >启动本机身体</el-button>
        </div>
      </el-card>
      <el-empty v-if="!cards.length" description="还没有伙伴，点右上角新建" />
    </div>

    <el-dialog v-model="dialogOpen" :title="isEdit ? '重新创作基因（出新版本）' : '新建伙伴'" width="640px">
      <el-form label-position="top" class="cmp-form">
        <div class="row-2">
          <el-form-item label="名字">
            <el-input v-model="form.name" placeholder="例如：小马" />
          </el-form-item>
          <el-form-item label="companion_id">
            <el-input v-model="form.companion_id" :disabled="isEdit" />
          </el-form-item>
        </div>
        <div class="row-2">
          <el-form-item label="原型 archetype">
            <el-input v-model="form.archetype" />
          </el-form-item>
          <el-form-item label="TA 是谁（自由描述）">
            <el-input v-model="form.description" type="textarea" :rows="2" />
          </el-form-item>
        </div>
        <p class="form-hint">下面每行一条（自由文本为主）：</p>
        <div class="row-2">
          <el-form-item label="价值观"><el-input v-model="form.values" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="禁忌"><el-input v-model="form.taboos" type="textarea" :rows="3" /></el-form-item>
        </div>
        <div class="row-2">
          <el-form-item label="铁律（绝不做）"><el-input v-model="form.unbreakable_rules" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="说话风格"><el-input v-model="form.style" type="textarea" :rows="3" /></el-form-item>
        </div>
        <div class="row-2">
          <el-form-item label="目标"><el-input v-model="form.goals" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="关于主人（钉住·永不遗忘）"><el-input v-model="form.pinned_facts" type="textarea" :rows="3" /></el-form-item>
        </div>
        <el-form-item label="示例对话（few-shot）"><el-input v-model="form.example_dialogs" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">{{ isEdit ? '出新版本' : '创建并绑定' }}</el-button>
      </template>
    </el-dialog>
  </CatalogPage>
</template>

<style scoped>
.cmp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.cmp-card { border: 1px solid var(--eid-border); }
.cmp-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.cmp-tags { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.cmp-name { font-size: 15px; font-weight: 700; color: var(--eid-text-primary); }
.cmp-id { display: block; margin-top: 2px; font-size: 11px; color: var(--eid-text-muted); }
.mono { font-family: var(--eid-font-mono); }
.cmp-meta { display: flex; gap: 12px; margin: 8px 0; font-size: 12px; color: var(--eid-text-secondary); }
.cmp-preview { margin-bottom: 8px; }
.cmp-preview summary { cursor: pointer; font-size: 12px; color: var(--eid-accent); }
.cmp-preview pre { max-height: 220px; overflow: auto; margin: 6px 0 0; padding: 8px; background: var(--eid-bg-panel); border-radius: 6px; font-size: 11px; white-space: pre-wrap; }
.cmp-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-hint { margin: 4px 0 8px; font-size: 12px; color: var(--eid-text-muted); }
</style>
