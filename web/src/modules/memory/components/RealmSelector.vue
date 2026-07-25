<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMemoryRealmStore } from '@/stores/memoryRealm'
import { memoryAgentStatus } from '@/utils/memoryRuntime'

const store = useMemoryRealmStore()
const route = useRoute()
let refreshTimer: ReturnType<typeof setInterval> | null = null

function applyRouteScope() {
  const realmId = String(route.query.memory_realm_id || '')
  const companionId = String(route.query.companion_id || '')
  store.setRouteScope(realmId, companionId)
}

applyRouteScope()

onMounted(async () => {
  await store.load()
  refreshTimer = setInterval(() => {
    if (!store.loading) void store.load(true)
  }, 10_000)
})

watch(
  () => [route.query.memory_realm_id, route.query.companion_id],
  applyRouteScope,
)

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

const options = computed(() =>
  store.realms.map((realm) => ({
    label: `${realm.companion_display_name || realm.companion_id}${realm.enabled ? '' : ' (disabled)'} · ${realm.memory_realm_id.slice(-8)}`,
    hint: `${realm.memory_realm_id} · owner ${realm.owner_id} · ${realm.configured_backend}`,
    status: memoryAgentStatus(realm),
    value: realm.memory_realm_id,
  })),
)

const scopeLabel = computed(() => {
  const realm = store.currentRealm
  if (!realm) return 'Memory scope'
  return realm.companion_display_name || realm.companion_id
})

const current = computed({
  get: () => store.currentId,
  set: (v: string) => store.setCurrent(v),
})
</script>

<template>
  <div class="realm-selector">
    <span class="prefix">{{ scopeLabel }}</span>
    <el-select
      v-model="current"
      placeholder="选择 realm"
      size="small"
      style="width: 340px"
      :loading="store.loading"
    >
      <el-option
        v-for="opt in options"
        :key="opt.value"
        :label="opt.label"
        :value="opt.value"
      >
        <span :class="['dot', `dot-${opt.status.type}`]" :title="opt.status.hint" />
        {{ opt.label }}
        <span class="status-note">{{ opt.hint }}</span>
      </el-option>
    </el-select>
    <el-button size="small" link @click="store.load(true)" :loading="store.loading">↻</el-button>
  </div>
</template>

<style scoped>
.realm-selector {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.prefix {
  font-size: 12px;
  color: var(--eid-text-muted);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 8px;
}
.dot-success { background: var(--eid-success); }
.dot-warning { background: var(--eid-warning); }
.dot-danger { background: var(--eid-danger); }
.dot-info { background: var(--eid-text-muted); }
.status-note {
  margin-left: 6px;
  color: var(--eid-text-muted);
  font-size: 12px;
}
</style>
