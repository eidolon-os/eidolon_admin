<script setup lang="ts">
import { computed, onMounted, defineAsyncComponent } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useServicesStore } from '@/stores/services'

const store = useServicesStore()
const route = useRoute()
const router = useRouter()

const MemoryUserSelector = defineAsyncComponent(
  () => import('@/modules/memory/components/UserSelector.vue'),
)

onMounted(() => store.load())

const isMemoryRoute = computed(() => {
  return route.name === 'feature' && route.params.serviceId === 'memory'
})

// Catalog routes (Phase 29) share a flat namespace with the legacy
// pages — each route name maps 1:1 to its menu index. Devices live under
// Hub because approval, binding and command delivery are hub operations.
const CATALOG_KEYS = ['tenants', 'templates', 'users', 'agents'] as const
const activeKey = computed(() => {
  if (route.name === 'supervisor') return 'supervisor'
  if (route.name === 'configs') return 'configs'
  if (typeof route.name === 'string' && (CATALOG_KEYS as readonly string[]).includes(route.name)) {
    return route.name
  }
  if (route.params.serviceId && route.params.feature) {
    return `${route.params.serviceId}::${route.params.feature}`
  }
  return ''
})

// Hide meta-services (e.g. the synthetic "admin" entry that only exposes
// configs: blocks) from the main service navigation.
const navigableServices = computed(() =>
  store.services.filter((s) => s.features.length > 0),
)

function go(serviceId: string, feature: string) {
  router.push({ name: 'feature', params: { serviceId, feature } })
}
</script>

<template>
  <el-container style="height: 100vh; background: var(--eid-bg-canvas)">
    <el-aside width="240px" class="aside">
      <div class="logo">
        <span class="logo-dot" />
        <span class="logo-text">Eidolon</span>
        <span class="logo-sub">admin</span>
      </div>
      <el-menu :default-active="activeKey" :unique-opened="false" class="menu">
        <el-menu-item index="supervisor" @click="router.push({ name: 'supervisor' })">
          <el-icon><Cpu /></el-icon>
          <span>Supervisor</span>
        </el-menu-item>
        <el-menu-item index="configs" @click="router.push({ name: 'configs' })">
          <el-icon><Document /></el-icon>
          <span>Configs</span>
        </el-menu-item>

        <!-- Phase 29 catalog. Wrapped in el-sub-menu (Phase
             30.C) so it matches the visual treatment of the service
             groups below (Agent / Channel / Memory etc.) — they're all
             "entity groups" in the sidebar's vocabulary. Order matches
             the dependency chain: Tenant → Template → User → Agent. -->
        <el-sub-menu index="catalog">
          <template #title>
            <el-icon><Files /></el-icon>
            <span>Catalog</span>
          </template>
          <el-menu-item index="tenants" @click="router.push({ name: 'tenants' })">
            <el-icon><OfficeBuilding /></el-icon>
            <span>Tenants</span>
          </el-menu-item>
          <el-menu-item index="templates" @click="router.push({ name: 'templates' })">
            <el-icon><Collection /></el-icon>
            <span>Templates</span>
          </el-menu-item>
          <el-menu-item index="users" @click="router.push({ name: 'users' })">
            <el-icon><User /></el-icon>
            <span>Users</span>
          </el-menu-item>
          <el-menu-item index="agents" @click="router.push({ name: 'agents' })">
            <el-icon><Avatar /></el-icon>
            <span>Agents</span>
          </el-menu-item>
        </el-sub-menu>
        <el-sub-menu
          v-for="svc in navigableServices"
          :key="svc.id"
          :index="svc.id"
        >
          <template #title>
            <el-icon><Box /></el-icon>
            <span>{{ svc.name }}</span>
          </template>
          <el-menu-item
            v-for="f in svc.features"
            :key="f.key"
            :index="`${svc.id}::${f.key}`"
            @click="go(svc.id, f.key)"
          >
            {{ f.label }}
          </el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="crumb">
          <template v-if="route.name === 'supervisor'">Supervisor</template>
          <template v-else-if="route.name === 'configs'">Configs</template>
          <template v-else-if="route.name === 'tenants'">Catalog <span class="sep">/</span> Tenants</template>
          <template v-else-if="route.name === 'templates'">Catalog <span class="sep">/</span> Templates</template>
          <template v-else-if="route.name === 'users'">Catalog <span class="sep">/</span> Users</template>
          <template v-else-if="route.name === 'agents'">Catalog <span class="sep">/</span> Agents</template>
          <template v-else-if="route.params.serviceId">
            {{ store.findService(route.params.serviceId as string)?.name || route.params.serviceId }}
            <span class="sep">/</span>
            {{ route.params.feature }}
          </template>
        </span>
        <div class="header-actions">
          <MemoryUserSelector v-if="isMemoryRoute" />
          <el-button size="small" @click="store.load(true)">刷新菜单</el-button>
        </div>
      </el-header>
      <el-main>
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.aside {
  background: var(--eid-bg-panel);
  border-right: 1px solid var(--eid-border);
  display: flex;
  flex-direction: column;
}
.logo {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--eid-border);
}
.logo-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--eid-accent);
  align-self: center;
  box-shadow: 0 0 12px var(--eid-accent);
}
.logo-text {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--eid-text-primary);
}
.logo-sub {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--eid-text-muted);
}
.menu {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.menu :deep(.el-menu-item),
.menu :deep(.el-sub-menu__title) {
  height: 36px;
  line-height: 36px;
  border-radius: var(--eid-radius-sm);
  margin: 2px 0;
  font-size: 13px;
}
.menu :deep(.el-sub-menu .el-menu-item) {
  height: 32px;
  line-height: 32px;
  padding-left: 40px !important;
  font-size: 12.5px;
}
.header {
  background: var(--eid-bg-canvas);
  border-bottom: 1px solid var(--eid-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--eid-header-h);
  padding: 0 20px;
}
.crumb {
  font-size: 13px;
  color: var(--eid-text-secondary);
  font-weight: 500;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.sep {
  margin: 0 8px;
  color: var(--eid-text-muted);
}
:deep(.el-main) {
  background: var(--eid-bg-canvas);
  padding: 24px;
}
</style>
