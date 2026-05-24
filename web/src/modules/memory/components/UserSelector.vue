<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useMemoryUserStore } from '@/stores/memoryUser'

const store = useMemoryUserStore()
onMounted(() => store.load())

const options = computed(() =>
  store.users.map((u) => ({
    label: `${u.user_id}${u.enabled ? '' : ' (disabled)'} · :${u.port}`,
    value: u.user_id,
    enabled: u.enabled,
    reachable: u.agent_reachable,
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
        <span :class="['dot', opt.reachable ? 'dot-on' : opt.enabled ? 'dot-warn' : 'dot-off']" />
        {{ opt.label }}
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
.dot-on  { background: var(--eid-success); }
.dot-warn{ background: var(--eid-warning); }
.dot-off { background: var(--eid-text-muted); }
</style>
