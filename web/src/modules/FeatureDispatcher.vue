<script setup lang="ts">
import { computed, defineAsyncComponent, type Component } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const moduleMap: Record<string, Record<string, Component>> = {
  agent: {
    'chat-test': defineAsyncComponent(() => import('./agent/ChatTest.vue')),
    conversations: defineAsyncComponent(() => import('./agent/Conversations.vue')),
    'long-tasks': defineAsyncComponent(() => import('./agent/LongTasks.vue')),
    'replay-reports': defineAsyncComponent(() => import('./agent/ReplayReports.vue')),
  },
  hub: {
    devices: defineAsyncComponent(() => import('./hub/Devices.vue')),
    discovery: defineAsyncComponent(() => import('./hub/Discovery.vue')),
    commands: defineAsyncComponent(() => import('./hub/Commands.vue')),
    events: defineAsyncComponent(() => import('./hub/Events.vue')),
    metrics: defineAsyncComponent(() => import('./hub/Metrics.vue')),
  },
  channel: {
    overview: defineAsyncComponent(() => import('./channel/Overview.vue')),
    config: defineAsyncComponent(() => import('./channel/Config.vue')),
  },
  'client-web': {
    overview: defineAsyncComponent(() => import('./client-web/Overview.vue')),
    config: defineAsyncComponent(() => import('./client-web/Config.vue')),
  },
  memory: {
    runners: defineAsyncComponent(() => import('./memory/Runners.vue')),
    memories: defineAsyncComponent(() => import('./memory/Memories.vue')),
    search: defineAsyncComponent(() => import('./memory/MemorySearch.vue')),
    graph: defineAsyncComponent(() => import('./memory/Graph.vue')),
    kg: defineAsyncComponent(() => import('./memory/KG.vue')),
    mcp: defineAsyncComponent(() => import('./memory/McpTools.vue')),
  },
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
