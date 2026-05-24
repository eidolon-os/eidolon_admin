<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Promotion } from '@element-plus/icons-vue'
import {
  compilePersonaPreview,
  evolvePersona,
  listPersonaInstances,
  mockMemoryTrigger,
  type PersonaInstance,
} from '@/api/agent'
import JsonViewer from '@/modules/common/JsonViewer.vue'

// ── instance picker (driven by query params for deep-linking) ───────────────

const route = useRoute()
const router = useRouter()
const instances = ref<PersonaInstance[]>([])
const loadingInstances = ref(false)

const selectedKey = ref<string>('')
const selected = computed(() => instances.value.find((x) => instanceKey(x) === selectedKey.value))

function instanceKey(i: PersonaInstance): string {
  return `${i.tenant_id}|${i.user_id}|${i.instance_id}`
}

async function loadInstances() {
  loadingInstances.value = true
  try {
    instances.value = await listPersonaInstances()
    // sync from query params on first load
    const t = String(route.query.tenant || '')
    const u = String(route.query.user || '')
    const i = String(route.query.instance || '')
    if (t && u && i) selectedKey.value = `${t}|${u}|${i}`
    else if (!selectedKey.value && instances.value.length) selectedKey.value = instanceKey(instances.value[0])
  } finally {
    loadingInstances.value = false
  }
}

onMounted(loadInstances)
watch(selectedKey, (v) => {
  // keep URL in sync so the lab page is shareable
  const inst = instances.value.find((x) => instanceKey(x) === v)
  if (!inst) return
  router.replace({
    query: { tenant: inst.tenant_id, user: inst.user_id, instance: inst.instance_id },
  })
})

// ── tab state ───────────────────────────────────────────────────────────────

const activeTab = ref<'compile' | 'evolve' | 'mock'>('compile')

// ── compile-preview tab ─────────────────────────────────────────────────────

const compileForm = ref({ user_text: '', template_id: '', memory_hits: '[]' })
const compileResult = ref<any>(null)
const compileBusy = ref(false)

async function runCompile() {
  if (!selected.value || !compileForm.value.user_text.trim()) return
  let memory_hits: any[] | undefined
  try {
    memory_hits = compileForm.value.memory_hits.trim() ? JSON.parse(compileForm.value.memory_hits) : undefined
  } catch (e: any) {
    ElMessage.error(`memory_hits JSON 解析失败：${e.message}`)
    return
  }
  compileBusy.value = true
  try {
    compileResult.value = await compilePersonaPreview(
      selected.value.tenant_id,
      selected.value.user_id,
      selected.value.instance_id,
      {
        user_text: compileForm.value.user_text,
        template_id: compileForm.value.template_id || undefined,
        memory_hits,
      },
    )
  } catch (e: any) {
    compileResult.value = { error: e?.response?.data?.detail || e.message }
  } finally {
    compileBusy.value = false
  }
}

// ── evolve tab ──────────────────────────────────────────────────────────────

const EVOLVE_EXAMPLE = JSON.stringify(
  [
    {
      type: 'belief.add',
      payload: { key: 'favorite_color', value: 'indigo', confidence: 0.9 },
    },
  ],
  null,
  2,
)

const evolveForm = ref({
  events: '[]',
  dry_run: true,
  template_id: '',
})
const evolveResult = ref<any>(null)
const evolveBusy = ref(false)

function insertEvolveExample() {
  evolveForm.value.events = EVOLVE_EXAMPLE
}

async function runEvolve() {
  if (!selected.value) return
  let events: any[]
  try {
    events = JSON.parse(evolveForm.value.events || '[]')
    if (!Array.isArray(events)) throw new Error('events 必须是数组')
  } catch (e: any) {
    ElMessage.error(`events JSON 解析失败：${e.message}`)
    return
  }
  evolveBusy.value = true
  try {
    evolveResult.value = await evolvePersona(
      selected.value.tenant_id,
      selected.value.user_id,
      selected.value.instance_id,
      {
        events,
        dry_run: evolveForm.value.dry_run,
        template_id: evolveForm.value.template_id || undefined,
      },
    )
    ElMessage.success(`evolve ${evolveForm.value.dry_run ? '(dry run) ' : ''}完成`)
  } catch (e: any) {
    evolveResult.value = { error: e?.response?.data?.detail || e.message }
  } finally {
    evolveBusy.value = false
  }
}

// ── mock-memory-trigger tab ─────────────────────────────────────────────────

const MOCK_EXAMPLE = JSON.stringify(
  [
    { key: 'mem-1', text: 'user mentioned they have a cat named Mochi', score: 0.81 },
  ],
  null,
  2,
)

const mockForm = ref({
  user_text: '',
  template_id: '',
  memory_hits: '[]',
  apply: false,
})
const mockResult = ref<any>(null)
const mockBusy = ref(false)

function insertMockExample() {
  mockForm.value.memory_hits = MOCK_EXAMPLE
}

async function runMock() {
  if (!selected.value || !mockForm.value.user_text.trim()) return
  let memory_hits: any[]
  try {
    memory_hits = JSON.parse(mockForm.value.memory_hits || '[]')
    if (!Array.isArray(memory_hits)) throw new Error('memory_hits 必须是数组')
  } catch (e: any) {
    ElMessage.error(`memory_hits JSON 解析失败：${e.message}`)
    return
  }
  mockBusy.value = true
  try {
    mockResult.value = await mockMemoryTrigger(
      selected.value.tenant_id,
      selected.value.user_id,
      selected.value.instance_id,
      {
        user_text: mockForm.value.user_text,
        template_id: mockForm.value.template_id || undefined,
        memory_hits,
        apply: mockForm.value.apply,
      },
    )
  } catch (e: any) {
    mockResult.value = { error: e?.response?.data?.detail || e.message }
  } finally {
    mockBusy.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <div>
        <h2 class="title">Persona Instance Lab</h2>
        <div class="subtitle">编译预览 · 演化注入 · 记忆触发模拟（开发者调试工具）</div>
      </div>
      <div class="actions">
        <el-select
          v-model="selectedKey"
          filterable
          placeholder="选择 instance"
          style="width: 380px"
          :loading="loadingInstances"
        >
          <el-option
            v-for="i in instances"
            :key="instanceKey(i)"
            :value="instanceKey(i)"
            :label="`${i.tenant_id} / ${i.user_id} / ${i.instance_id}`"
          >
            <span class="mono">{{ i.tenant_id }} / {{ i.user_id }} / {{ i.instance_id }}</span>
            <span class="muted" style="margin-left: 8px">{{ i.template_id }}</span>
          </el-option>
        </el-select>
        <el-button size="small" :icon="Refresh" :loading="loadingInstances" @click="loadInstances" />
      </div>
    </div>

    <el-empty v-if="!selected" description="选一个实例开始" />

    <el-tabs v-else v-model="activeTab" class="lab-tabs">

      <!-- COMPILE PREVIEW -->
      <el-tab-pane label="编译预览" name="compile">
        <div class="lab-grid">
          <el-card>
            <template #header>Input</template>
            <el-form label-position="top">
              <el-form-item label="User text" required>
                <el-input v-model="compileForm.user_text" type="textarea" :rows="3"
                  placeholder="模拟用户的一句话…" />
              </el-form-item>
              <el-form-item label="Template override (可选)">
                <el-input v-model="compileForm.template_id" placeholder="留空使用实例当前 template" />
              </el-form-item>
              <el-form-item label="Memory hits (JSON 数组)">
                <el-input v-model="compileForm.memory_hits" type="textarea" :rows="4" />
              </el-form-item>
              <el-button
                type="primary"
                :icon="Promotion"
                :loading="compileBusy"
                :disabled="!compileForm.user_text.trim()"
                @click="runCompile"
              >
                Compile
              </el-button>
            </el-form>
          </el-card>

          <el-card>
            <template #header>
              <span>Compiled prompt</span>
              <span class="hint" style="float: right">不会触发 LLM，仅返回会发送的 prompt</span>
            </template>
            <JsonViewer :data="compileResult" max-height="60vh" />
          </el-card>
        </div>
      </el-tab-pane>

      <!-- EVOLVE -->
      <el-tab-pane label="演化注入" name="evolve">
        <div class="lab-grid">
          <el-card>
            <template #header>
              <span>Events</span>
              <el-button size="small" link style="float: right" @click="insertEvolveExample">
                插入示例
              </el-button>
            </template>
            <el-form label-position="top">
              <el-form-item label="Events (PersonaEvolutionEvent[])">
                <el-input v-model="evolveForm.events" type="textarea" :rows="12" placeholder="[]" />
              </el-form-item>
              <el-form-item label="Template override (可选)">
                <el-input v-model="evolveForm.template_id" />
              </el-form-item>
              <el-form-item>
                <el-switch
                  v-model="evolveForm.dry_run"
                  active-text="dry run"
                  inactive-text="实写"
                />
              </el-form-item>
              <el-button
                :type="evolveForm.dry_run ? 'primary' : 'danger'"
                :icon="Promotion"
                :loading="evolveBusy"
                @click="runEvolve"
              >
                {{ evolveForm.dry_run ? 'Preview evolve' : 'Apply evolve' }}
              </el-button>
            </el-form>
          </el-card>

          <el-card>
            <template #header>
              <span>Result (PersonaEvolutionResult)</span>
              <span class="hint" style="float: right">
                dry run：仅返回 delta；实写：bump overlay_version + 触发 worker
              </span>
            </template>
            <JsonViewer :data="evolveResult" max-height="60vh" />
          </el-card>
        </div>
      </el-tab-pane>

      <!-- MOCK MEMORY TRIGGER -->
      <el-tab-pane label="Mock memory trigger" name="mock">
        <div class="lab-grid">
          <el-card>
            <template #header>
              <span>Input</span>
              <el-button size="small" link style="float: right" @click="insertMockExample">
                插入示例
              </el-button>
            </template>
            <el-form label-position="top">
              <el-form-item label="User text" required>
                <el-input v-model="mockForm.user_text" type="textarea" :rows="3" />
              </el-form-item>
              <el-form-item label="Memory hits (JSON 数组)">
                <el-input v-model="mockForm.memory_hits" type="textarea" :rows="8"
                  placeholder='[{"key":"mem-1","text":"...","score":0.8}]' />
              </el-form-item>
              <el-form-item label="Template override (可选)">
                <el-input v-model="mockForm.template_id" />
              </el-form-item>
              <el-form-item>
                <el-switch
                  v-model="mockForm.apply"
                  active-text="apply (写入)"
                  inactive-text="dry (只看回调)"
                />
              </el-form-item>
              <el-button
                :type="mockForm.apply ? 'danger' : 'primary'"
                :icon="Promotion"
                :loading="mockBusy"
                :disabled="!mockForm.user_text.trim()"
                @click="runMock"
              >
                {{ mockForm.apply ? 'Run + apply' : 'Run (dry)' }}
              </el-button>
            </el-form>
          </el-card>

          <el-card>
            <template #header>Result (PersonaMockResult)</template>
            <JsonViewer :data="mockResult" max-height="60vh" />
          </el-card>
        </div>
      </el-tab-pane>

    </el-tabs>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  gap: 16px;
}
.title { margin: 0; font-size: 18px; font-weight: 600; }
.subtitle { font-size: 12px; color: var(--eid-text-muted); margin-top: 4px; }
.actions { display: flex; gap: 8px; align-items: center; }
.lab-tabs :deep(.el-tabs__nav-wrap::after) {
  background: var(--eid-border);
}
.lab-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}
.mono { font-family: var(--eid-font-mono); font-size: 12px; }
.muted { color: var(--eid-text-muted); font-size: 11px; }
.hint { font-size: 12px; color: var(--eid-text-muted); font-weight: normal; }
</style>
