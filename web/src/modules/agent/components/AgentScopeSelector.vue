<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listOwnerCompanions, type CompanionView } from '@/api/eidolonData'
import { useOwnersStore } from '@/stores/owners'
import { extractErrorMessage } from '@/utils/format'

const props = withDefaults(defineProps<{
  ownerId: string
  companionId: string
  allowAllCompanions?: boolean
  disabled?: boolean
}>(), {
  allowAllCompanions: false,
  disabled: false,
})

const emit = defineEmits<{
  'update:ownerId': [value: string]
  'update:companionId': [value: string]
  changed: [value: { ownerId: string; companionId: string; companion: CompanionView | null }]
}>()

const ownersStore = useOwnersStore()
const companions = ref<CompanionView[]>([])
const loadingCompanions = ref(false)

const selectedCompanion = computed(() =>
  companions.value.find((item) => item.companion_id === props.companionId) || null,
)

onMounted(async () => {
  await ownersStore.load()
  if (!props.ownerId && ownersStore.currentId) {
    emit('update:ownerId', ownersStore.currentId)
    return
  }
  await loadCompanions()
})

watch(() => props.ownerId, async (ownerId) => {
  if (ownerId) ownersStore.setCurrent(ownerId)
  await loadCompanions()
})

watch([() => props.ownerId, () => props.companionId, selectedCompanion], () => {
  emit('changed', {
    ownerId: props.ownerId,
    companionId: props.companionId,
    companion: selectedCompanion.value,
  })
})

async function loadCompanions() {
  if (!props.ownerId) {
    companions.value = []
    emit('update:companionId', '')
    return
  }
  loadingCompanions.value = true
  try {
    companions.value = await listOwnerCompanions(props.ownerId)
    const stillValid = companions.value.some((item) => item.companion_id === props.companionId)
    if (!stillValid) {
      emit('update:companionId', props.allowAllCompanions ? '' : companions.value[0]?.companion_id || '')
    }
  } catch (e) {
    companions.value = []
    emit('update:companionId', '')
    ElMessage.error(`加载 companion 失败: ${extractErrorMessage(e)}`)
  } finally {
    loadingCompanions.value = false
  }
}
</script>

<template>
  <div class="agent-scope">
    <el-select
      :model-value="ownerId"
      filterable
      :disabled="disabled || ownersStore.loading"
      placeholder="Owner"
      class="scope-control"
      @update:model-value="emit('update:ownerId', String($event || ''))"
    >
      <el-option
        v-for="owner in ownersStore.owners"
        :key="owner.owner_id"
        :label="owner.display_name || owner.owner_id"
        :value="owner.owner_id"
      >
        <span>{{ owner.display_name || owner.owner_id }}</span>
        <span class="option-id">{{ owner.owner_id }}</span>
      </el-option>
    </el-select>
    <el-select
      :model-value="companionId"
      filterable
      :clearable="allowAllCompanions"
      :disabled="disabled || !ownerId"
      :loading="loadingCompanions"
      placeholder="Companion"
      class="scope-control"
      @update:model-value="emit('update:companionId', String($event || ''))"
    >
      <el-option v-if="allowAllCompanions" label="All companions" value="" />
      <el-option
        v-for="companion in companions"
        :key="companion.companion_id"
        :label="companion.display_name || companion.companion_id"
        :value="companion.companion_id"
      >
        <span>{{ companion.display_name || companion.companion_id }}</span>
        <span class="option-id">{{ companion.companion_id }}</span>
      </el-option>
    </el-select>
    <div v-if="selectedCompanion" class="scope-meta">
      <span>{{ selectedCompanion.current_genome_id || 'no genome' }}</span>
      <span>{{ selectedCompanion.default_memory_realm_id || 'no realm' }}</span>
    </div>
  </div>
</template>

<style scoped>
.agent-scope {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.scope-control {
  width: 240px;
}
.scope-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
}
.scope-meta span {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.option-id {
  float: right;
  margin-left: 12px;
  color: var(--eid-text-muted);
  font-size: 12px;
}
@media (max-width: 760px) {
  .scope-control {
    width: 100%;
  }
}
</style>
