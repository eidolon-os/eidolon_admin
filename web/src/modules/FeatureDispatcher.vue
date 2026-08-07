<script setup lang="ts">
import { computed, defineAsyncComponent, type Component } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const moduleMap: Record<string, Record<string, Component>> = {
  agent: { console: defineAsyncComponent(() => import('./common/ApiConsole.vue')) },
  hub: { console: defineAsyncComponent(() => import('./common/ApiConsole.vue')) },
  channel: {
    overview: defineAsyncComponent(() => import('./channel/Overview.vue')),
    config: defineAsyncComponent(() => import('./channel/Config.vue')),
  },
  'client-web': {
    overview: defineAsyncComponent(() => import('./client-web/Overview.vue')),
    config: defineAsyncComponent(() => import('./client-web/Config.vue')),
  },
  memory: { console: defineAsyncComponent(() => import('./common/ApiConsole.vue')) },
}

const component = computed(() => {
  const sid = route.params.serviceId as string
  const feat = route.params.feature as string
  return moduleMap[sid]?.[feat] || null
})
</script>

<template>
  <component
    :is="component"
    v-if="component"
    :key="`${route.params.serviceId}/${route.params.feature}`"
  />
  <div v-else class="page unknown-feature">
    <el-empty description="This admin feature is not available." />
  </div>
</template>

<style scoped>
.unknown-feature {
  min-height: 100%;
  display: grid;
  place-items: center;
}
</style>
