<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useMemoryRealmStore } from '@/stores/memoryRealm'
import { memoryAgentStatus } from '@/utils/memoryRuntime'

const store = useMemoryRealmStore()
let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  // Respect a prior selection (sticky across sessions); don't force-reset to
  // the backend default on every mount. The interval still force-refreshes.
  await store.load()
  refreshTimer = setInterval(() => {
    if (!store.loading) void store.load(true)
  }, 10_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

const options = computed(() =>
  store.realms.map((realm) => ({
    label: `${realm.memory_realm_id}${realm.enabled ? '' : ' (disabled)'} · :${realm.port}`,
    hint: `${realm.owner_id} / ${realm.companion_id}`,
    status: memoryAgentStatus(realm),
    value: realm.memory_realm_id,
  })),
)

const current = computed({
  get: () => store.currentId,
  set: (v: string) => store.setCurrent(v),
})
</script>

<template>
  <div class="realm-selector">
    <span class="prefix">Memory realm</span>
    <el-select
      v-model="current"
      placeholder="选择 realm"
      size="small"
      style="width: 260px"
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
