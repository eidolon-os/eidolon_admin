<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import CatalogPage from '@/modules/common/CatalogPage.vue'
import {
  listOwnerConversations,
  listOwnerEvents,
  listOwnerJobs,
  listOwnerMemoryRealms,
  type ConversationView,
  type EventView,
  type JobView,
  type MemoryRealmView,
} from '@/api/eidolonData'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage, formatTimestamp } from '@/utils/format'

type Section = 'conversations' | 'memory' | 'jobs' | 'events'

const route = useRoute()
const router = useRouter()
const ownersStore = useOwnersStore()
const loading = ref(false)
const conversations = ref<ConversationView[]>([])
const memoryRealms = ref<MemoryRealmView[]>([])
const jobs = ref<JobView[]>([])
const events = ref<EventView[]>([])
const sections: Array<{ key: Section; label: string; hint: string }> = [
  { key: 'conversations', label: 'Conversation Records', hint: 'eidolon_data 中的会话主记录' },
  { key: 'memory', label: 'Memory Realms', hint: 'Owner 与 Companion 的记忆域关系' },
  { key: 'jobs', label: 'Data Jobs', hint: '数据层任务记录，不等同于 Agent Long Tasks' },
  { key: 'events', label: 'Audit Events', hint: 'Owner 范围的完整审计事件' },
]

const ownerId = computed(() => ownersStore.currentId)
const activeSection = computed<Section>(() => {
  const value = String(route.params.section || 'conversations')
  return sections.some((item) => item.key === value) ? value as Section : 'conversations'
})
const activeHint = computed(() => sections.find((item) => item.key === activeSection.value)?.hint || '')

onMounted(async () => {
  await ownersStore.load()
  await load()
})

watch([ownerId, activeSection], load)

async function switchSection(value: string | number) {
  await router.replace({
    name: 'data-inspector',
    params: { section: String(value) },
    query: { ...route.query, owner_id: ownerId.value || undefined },
  })
}

async function load() {
  if (!ownerId.value) return
  loading.value = true
  try {
    if (activeSection.value === 'conversations') conversations.value = await listOwnerConversations(ownerId.value)
    if (activeSection.value === 'memory') memoryRealms.value = await listOwnerMemoryRealms(ownerId.value)
    if (activeSection.value === 'jobs') jobs.value = await listOwnerJobs(ownerId.value)
    if (activeSection.value === 'events') events.value = await listOwnerEvents(ownerId.value)
  } catch (error) {
    ElMessage.error(`加载原始数据失败: ${extractErrorMessage(error)}`)
  } finally {
    loading.value = false
  }
}

function jsonSummary(value: Record<string, unknown> | null | undefined) {
  const keys = Object.keys(value || {})
  return keys.length ? keys.slice(0, 4).join(', ') : '—'
}
</script>

<template>
  <CatalogPage title="Data Inspector" :hint="activeHint">
    <template #head-actions>
      <el-tag size="small" type="info" effect="plain">{{ ownersStore.currentOwner?.display_name || ownerId || '未选择空间' }}</el-tag>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </template>

    <el-alert
      type="info"
      :closable="false"
      title="这里保留 eidolon_data 的原始主权域记录；用户工作流请使用 Companions、Devices 和 Activity。"
      class="inspector-alert"
    />

    <el-tabs :model-value="activeSection" @tab-change="switchSection">
      <el-tab-pane v-for="section in sections" :key="section.key" :label="section.label" :name="section.key" />
    </el-tabs>

    <el-empty v-if="!ownerId" description="请先从顶部选择一个 Eidolon 空间" />

    <el-table v-else-if="activeSection === 'conversations'" :data="conversations" v-loading="loading" size="small" stripe>
      <el-table-column prop="conversation_id" label="conversation_id" min-width="220" />
      <el-table-column prop="title" label="title" min-width="160" />
      <el-table-column prop="companion_id" label="companion" min-width="180" />
      <el-table-column prop="status" label="status" width="110" />
      <el-table-column label="updated" width="190"><template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template></el-table-column>
    </el-table>

    <el-table v-else-if="activeSection === 'memory'" :data="memoryRealms" v-loading="loading" size="small" stripe>
      <el-table-column prop="realm_id" label="realm_id" min-width="210" />
      <el-table-column prop="companion_id" label="companion" min-width="180" />
      <el-table-column prop="engine" label="engine" width="130" />
      <el-table-column prop="status" label="status" width="110" />
      <el-table-column label="policy" min-width="180"><template #default="{ row }">{{ jsonSummary(row.policy_json) }}</template></el-table-column>
      <el-table-column label="updated" width="190"><template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template></el-table-column>
    </el-table>

    <el-table v-else-if="activeSection === 'jobs'" :data="jobs" v-loading="loading" size="small" stripe>
      <el-table-column prop="job_id" label="job_id" min-width="220" />
      <el-table-column prop="provider" label="provider" width="130" />
      <el-table-column prop="kind" label="kind" width="160" />
      <el-table-column prop="status" label="status" width="120" />
      <el-table-column prop="companion_id" label="companion" min-width="180" />
      <el-table-column label="updated" width="190"><template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template></el-table-column>
    </el-table>

    <el-table v-else :data="events" v-loading="loading" size="small" stripe>
      <el-table-column prop="event_type" label="event_type" min-width="190" />
      <el-table-column prop="subject_type" label="subject" width="130" />
      <el-table-column prop="subject_id" label="subject_id" min-width="180" />
      <el-table-column prop="severity" label="severity" width="110" />
      <el-table-column prop="outcome" label="outcome" width="110" />
      <el-table-column label="created" width="190"><template #default="{ row }">{{ formatTimestamp(row.created_at) }}</template></el-table-column>
    </el-table>
  </CatalogPage>
</template>

<style scoped>
.inspector-alert { margin-bottom: 14px; }
</style>
