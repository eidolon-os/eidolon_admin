<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useMemoryUserStore } from '@/stores/memoryUser'
import { memoryAgentStatus } from '@/utils/memoryRuntime'

const store = useMemoryUserStore()
let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await store.load(true)
  refreshTimer = setInterval(() => {
    if (!store.loading) void store.load(true)
  }, 10_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

const options = computed(() =>
  store.users.map((u) => ({
    label: `${u.user_id}${u.enabled ? '' : ' (disabled)'} · :${u.port}`,
    status: memoryAgentStatus(u),
    value: u.user_id,
  })),
)

const current = computed({
  get: () => store.currentId,
  set: (v: string) => store.setCurrent(v),
})
</script>

<template>
  <div class="user-selector">
    <span class="prefix">Memory user</span>
    <el-select
      v-model="current"
      placeholder="选择用户"
      size="small"
      style="width: 220px"
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
        <span v-if="opt.status.label !== 'RUNNING'" class="status-note">
          {{ opt.status.label.toLowerCase() }}
        </span>
      </el-option>
    </el-select>
    <el-button size="small" link @click="store.load(true)" :loading="store.loading">↻</el-button>
  </div>
</template>

<style scoped>
.user-selector {
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
