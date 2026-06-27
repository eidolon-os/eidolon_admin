<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useServicesStore } from '@/stores/services'
import { useOwnersStore } from '@/stores/owners'

const store = useServicesStore()
const ownersStore = useOwnersStore()
const route = useRoute()
const router = useRouter()

const MemoryUserSelector = defineAsyncComponent(
  () => import('@/modules/memory/components/UserSelector.vue'),
)
const OwnerSelector = defineAsyncComponent(
  () => import('@/modules/owners/OwnerSelector.vue'),
)

const commandOpen = ref(false)
const commandQuery = ref('')
const commandScope = ref<string | null>(null)
const sidebarOpen = ref(localStorage.getItem('eidolon-admin.sidebar_open') !== '0')

type CommandItem = {
  id: string
  label: string
  group: string
  hint: string
  icon: string
  route: Record<string, unknown>
  scope: string
}

type MenuItem = {
  id: string
  label: string
  hint?: string
  icon: string
  route: Record<string, unknown>
  active: boolean
}

type MenuGroup = {
  id: string
  label: string
  items: MenuItem[]
}

onMounted(() => {
  store.load()
  ownersStore.load()
  window.addEventListener('keydown', handleGlobalKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
})

const isMemoryRoute = computed(() => {
  return route.name === 'feature' && route.params.serviceId === 'memory'
})

const activeKey = computed(() => {
  if (route.name === 'owners' || route.name === 'owner-workspace') return 'owners'
  if (route.name === 'supervisor') return 'supervisor'
  if (route.name === 'configs') return 'configs'
  if (route.name === 'benchmarks') return 'benchmark'
  if (route.name === 'tool-esp32') return 'tools::esp32'
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

const staticCommands = computed<CommandItem[]>(() => [
  {
    id: 'owners',
    label: 'Owners',
    group: 'Owners',
    hint: 'Select or create a sovereign owner workspace',
    icon: 'UserFilled',
    route: { name: 'owners' },
    scope: 'owners',
  },
  {
    id: 'supervisor',
    label: 'Supervisor',
    group: 'Core',
    hint: 'System processes, health, runtime controls',
    icon: 'Cpu',
    route: { name: 'supervisor' },
    scope: 'core',
  },
  {
    id: 'configs',
    label: 'Configs',
    group: 'Core',
    hint: 'Edit service configs and reload runtime',
    icon: 'Document',
    route: { name: 'configs' },
    scope: 'core',
  },
  {
    id: 'benchmark-agent',
    label: 'Benchmark Center',
    group: 'Benchmark',
    hint: 'Cross-project benchmark artifacts and run management',
    icon: 'DataAnalysis',
    route: { name: 'benchmarks', params: { project: 'agent' } },
    scope: 'benchmark',
  },
  {
    id: 'tool-esp32',
    label: 'ESP32',
    group: 'Tools',
    hint: 'Serial logs, build, flash, erase, diagnostics',
    icon: 'Tools',
    route: { name: 'tool-esp32' },
    scope: 'tools',
  },
])

const serviceCommands = computed<CommandItem[]>(() =>
  navigableServices.value.flatMap((svc) =>
    svc.features.map((f) => ({
      id: `${svc.id}::${f.key}`,
      label: f.label,
      group: svc.name,
      hint: `${svc.id}/${f.key}`,
      icon: serviceIcon(svc.id),
      route: { name: 'feature', params: { serviceId: svc.id, feature: f.key } },
      scope: svc.id,
    })),
  ),
)

const commandItems = computed(() => [...staticCommands.value, ...serviceCommands.value])

const currentTitle = computed(() => {
  if (route.name === 'owners') return 'Owners'
  if (route.name === 'owner-workspace') return `Owner / ${route.params.ownerId || ''}`
  if (route.name === 'supervisor') return 'Supervisor'
  if (route.name === 'configs') return 'Configs'
  if (route.name === 'benchmarks') return `Benchmark / ${route.params.project || 'agent'}`
  if (route.name === 'tool-esp32') return 'Tools / ESP32'
  if (route.params.serviceId) {
    const serviceName = store.findService(route.params.serviceId as string)?.name || route.params.serviceId
    return `${serviceName} / ${route.params.feature}`
  }
  return 'Command Center'
})

const scopeLabel = computed(() => {
  if (!commandScope.value) return 'All systems'
  if (commandScope.value === 'owners') return 'Owners'
  if (commandScope.value === 'core') return 'Core'
  if (commandScope.value === 'benchmark') return 'Benchmark'
  if (commandScope.value === 'tools') return 'Tools'
  return store.findService(commandScope.value)?.name || commandScope.value
})

const filteredCommands = computed(() => {
  const q = commandQuery.value.trim().toLowerCase()
  return commandItems.value.filter((item) => {
    if (commandScope.value && item.scope !== commandScope.value) return false
    if (!q) return true
    return `${item.label} ${item.group} ${item.hint}`.toLowerCase().includes(q)
  })
})

const groupedCommands = computed(() => {
  const groups: Array<{ name: string; items: CommandItem[] }> = []
  for (const item of filteredCommands.value) {
    let group = groups.find((g) => g.name === item.group)
    if (!group) {
      group = { name: item.group, items: [] }
      groups.push(group)
    }
    group.items.push(item)
  }
  return groups
})

const railItems = computed(() => [
  { id: 'owners', label: 'Owners', code: 'OWN', icon: 'UserFilled', active: activeKey.value === 'owners' },
  { id: 'core', label: 'Core', code: 'SYS', icon: 'Monitor', active: ['supervisor', 'configs'].includes(activeKey.value) },
  { id: 'benchmark', label: 'Benchmark', code: 'BMK', icon: 'DataAnalysis', active: activeKey.value === 'benchmark' },
  { id: 'tools', label: 'Tools', code: 'TLS', icon: 'Tools', active: typeof activeKey.value === 'string' && activeKey.value.startsWith('tools::') },
  ...navigableServices.value.map((svc) => ({
    id: svc.id,
    label: svc.name,
    code: serviceCode(svc.id),
    icon: serviceIcon(svc.id),
    active: typeof activeKey.value === 'string' && activeKey.value.startsWith(`${svc.id}::`),
  })),
])

const ownerWorkspaceMenu = computed<MenuItem[]>(() => {
  return [
    {
      id: 'owners-list',
      label: 'Owner Directory',
      hint: 'All owners',
      icon: 'UserFilled',
      route: { name: 'owners' },
      active: route.name === 'owners',
    },
    {
      id: 'owners-create',
      label: 'Create Owner',
      hint: 'New sovereignty root',
      icon: 'CirclePlus',
      route: { name: 'owners', query: { create: '1' } },
      active: route.name === 'owners' && route.query.create === '1',
    },
  ]
})

const menuGroups = computed<MenuGroup[]>(() => [
  {
    id: 'owners',
    label: 'Owners',
    items: ownerWorkspaceMenu.value,
  },
  {
    id: 'system',
    label: 'System',
    items: [
      {
        id: 'supervisor',
        label: 'Supervisor',
        hint: 'Processes and health',
        icon: 'Cpu',
        route: { name: 'supervisor' },
        active: route.name === 'supervisor',
      },
      {
        id: 'configs',
        label: 'Configs',
        hint: 'Runtime configuration',
        icon: 'Document',
        route: { name: 'configs' },
        active: route.name === 'configs',
      },
    ],
  },
  {
    id: 'benchmark',
    label: 'Benchmark',
    items: [
      {
        id: 'benchmark-agent',
        label: 'Benchmark Center',
        icon: 'DataAnalysis',
        route: { name: 'benchmarks', params: { project: 'agent' } },
        active: route.name === 'benchmarks',
      },
    ],
  },
  {
    id: 'tools',
    label: 'Tools',
    items: [
      {
        id: 'tool-esp32',
        label: 'ESP32',
        hint: 'Serial / flash',
        icon: 'Tools',
        route: { name: 'tool-esp32' },
        active: route.name === 'tool-esp32',
      },
    ],
  },
  ...navigableServices.value.map((svc) => ({
    id: svc.id,
    label: svc.name,
    items: svc.features.map((feature) => ({
      id: `${svc.id}-${feature.key}`,
      label: feature.label,
      hint: feature.key,
      icon: serviceIcon(svc.id),
      route: { name: 'feature', params: { serviceId: svc.id, feature: feature.key } },
      active: route.name === 'feature'
        && route.params.serviceId === svc.id
        && route.params.feature === feature.key,
    })),
  })),
])

function serviceIcon(serviceId: string): string {
  if (serviceId === 'agent') return 'Avatar'
  if (serviceId === 'hub') return 'Share'
  if (serviceId === 'memory') return 'Collection'
  if (serviceId === 'channel') return 'DataLine'
  if (serviceId === 'client-web') return 'ChromeFilled'
  return 'Box'
}

function serviceCode(serviceId: string): string {
  if (serviceId === 'agent') return 'AGT'
  if (serviceId === 'hub') return 'HUB'
  if (serviceId === 'memory') return 'MEM'
  if (serviceId === 'channel') return 'CHN'
  if (serviceId === 'client-web') return 'WEB'
  return serviceId.slice(0, 3).toUpperCase()
}

function openCommand(scope: string | null = null) {
  commandScope.value = scope
  commandQuery.value = ''
  commandOpen.value = true
}

function runCommand(item: CommandItem) {
  commandOpen.value = false
  router.push(item.route)
}

function runMenuItem(item: MenuItem) {
  router.push(item.route)
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
  localStorage.setItem('eidolon-admin.sidebar_open', sidebarOpen.value ? '1' : '0')
}

function handleRailClick(id: string) {
  if (id === 'owners' || id === 'core' || id === 'benchmark' || id === 'tools') {
    openCommand(id)
    return
  }
  openCommand(id)
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    openCommand(null)
  }
}
</script>

<template>
  <el-container class="admin-shell">
    <el-aside :width="sidebarOpen ? '272px' : '72px'" class="aside" :class="{ expanded: sidebarOpen }">
      <div class="logo">
        <span class="logo-dot" />
        <span v-if="sidebarOpen" class="logo-word">Eidolon Admin</span>
        <button class="sidebar-toggle" :title="sidebarOpen ? 'Collapse menu' : 'Expand menu'" @click="toggleSidebar">
          <el-icon><Fold v-if="sidebarOpen" /><Expand v-else /></el-icon>
        </button>
      </div>
      <nav v-if="!sidebarOpen" class="rail">
        <button
          v-for="item in railItems"
          :key="item.id"
          class="rail-button"
          :class="{ active: item.active }"
          :title="item.label"
          @click="handleRailClick(item.id)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.code }}</span>
        </button>
      </nav>
      <nav v-else class="expanded-menu">
        <section v-for="group in menuGroups" :key="group.id" class="menu-group">
          <h3>{{ group.label }}</h3>
          <button
            v-for="item in group.items"
            :key="item.id"
            class="menu-item"
            :class="{ active: item.active }"
            @click="runMenuItem(item)"
          >
            <span class="menu-icon"><el-icon><component :is="item.icon" /></el-icon></span>
            <span class="menu-copy">
              <strong>{{ item.label }}</strong>
              <small v-if="item.hint">{{ item.hint }}</small>
            </span>
          </button>
        </section>
      </nav>
      <button class="rail-command" title="Command Center" @click="openCommand(null)">
        <el-icon><Search /></el-icon>
        <span v-if="sidebarOpen">Command Center</span>
      </button>
    </el-aside>
    <el-container class="workspace">
      <el-header class="header">
        <button class="command-chip" @click="openCommand(null)">
          <span class="chip-pulse" />
          <span class="chip-kicker">COMMAND</span>
          <span class="crumb">{{ currentTitle }}</span>
          <kbd>⌘K</kbd>
        </button>
        <div class="header-actions">
          <OwnerSelector />
          <MemoryUserSelector v-if="isMemoryRoute" />
          <el-button size="small" @click="store.load(true)">刷新菜单</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <RouterView />
      </el-main>
    </el-container>

    <el-dialog
      v-model="commandOpen"
      class="command-dialog"
      width="760px"
      append-to-body
      :show-close="false"
      align-center
    >
      <div class="command-console">
        <header class="command-head">
          <div>
            <span class="console-kicker">Command Center</span>
            <h2>{{ scopeLabel }}</h2>
          </div>
          <button class="console-close" @click="commandOpen = false">ESC</button>
        </header>

        <div class="command-input">
          <el-icon><Search /></el-icon>
          <input
            v-model="commandQuery"
            autofocus
            placeholder="Search systems, reports, memory, devices..."
            @keydown.enter="filteredCommands[0] && runCommand(filteredCommands[0])"
            @keydown.esc="commandOpen = false"
          >
        </div>

        <div class="scope-strip">
          <button :class="{ active: commandScope === null }" @click="openCommand(null)">All</button>
          <button :class="{ active: commandScope === 'owners' }" @click="openCommand('owners')">Owners</button>
          <button :class="{ active: commandScope === 'core' }" @click="openCommand('core')">Core</button>
          <button :class="{ active: commandScope === 'benchmark' }" @click="openCommand('benchmark')">Benchmark</button>
          <button :class="{ active: commandScope === 'tools' }" @click="openCommand('tools')">Tools</button>
          <button
            v-for="svc in navigableServices"
            :key="svc.id"
            :class="{ active: commandScope === svc.id }"
            @click="openCommand(svc.id)"
          >
            {{ svc.name }}
          </button>
        </div>

        <div class="command-results">
          <section v-for="group in groupedCommands" :key="group.name" class="command-group">
            <h3>{{ group.name }}</h3>
            <button
              v-for="item in group.items"
              :key="item.id"
              class="command-card"
              @click="runCommand(item)"
            >
              <span class="command-icon"><el-icon><component :is="item.icon" /></el-icon></span>
              <span class="command-copy">
                <strong>{{ item.label }}</strong>
                <small>{{ item.hint }}</small>
              </span>
              <span class="command-arrow">↵</span>
            </button>
          </section>
          <div v-if="filteredCommands.length === 0" class="command-empty">
            No matching command
          </div>
        </div>
      </div>
    </el-dialog>
  </el-container>
</template>

<style scoped>
.aside {
  position: relative;
  background:
    linear-gradient(180deg, rgba(34, 211, 238, 0.045), transparent 150px),
    var(--eid-bg-panel);
  border-right: 1px solid color-mix(in srgb, var(--eid-accent) 28%, var(--eid-border));
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  transition: width 0.16s ease;
}
.aside::after {
  content: "";
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 1px;
  background: linear-gradient(180deg, transparent, var(--eid-accent), transparent);
  opacity: 0.45;
  pointer-events: none;
}
.admin-shell {
  height: 100vh;
  overflow: hidden;
  background:
    linear-gradient(rgba(34, 211, 238, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 211, 238, 0.026) 1px, transparent 1px),
    linear-gradient(180deg, rgba(34, 211, 238, 0.055), transparent 210px),
    var(--eid-bg-canvas);
  background-size: 28px 28px, 28px 28px, auto;
}
.workspace {
  min-width: 0;
}
.logo {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 57px;
  padding: 0 10px;
  border-bottom: 1px solid color-mix(in srgb, var(--eid-accent) 18%, var(--eid-border));
}
.logo::after {
  content: "";
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, var(--eid-accent), transparent);
  opacity: 0.7;
}
.logo-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--eid-accent);
  align-self: center;
  box-shadow: 0 0 10px var(--eid-accent), 0 0 28px rgba(34, 211, 238, 0.45);
}
.logo-word {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--eid-text-primary);
  font-size: 13px;
  font-weight: 760;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.sidebar-toggle {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid color-mix(in srgb, var(--eid-border-strong) 72%, transparent);
  border-radius: 8px;
  background: rgba(13, 17, 20, 0.74);
  color: var(--eid-text-muted);
  cursor: pointer;
}
.aside:not(.expanded) .sidebar-toggle {
  position: absolute;
  right: 8px;
}
.sidebar-toggle:hover {
  border-color: color-mix(in srgb, var(--eid-accent) 48%, var(--eid-border));
  color: var(--eid-accent-hover);
  background: var(--eid-bg-elev);
}
.rail {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding: 12px 10px;
}
.rail-button,
.rail-command {
  position: relative;
  width: 50px;
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 2px;
  border: 1px solid color-mix(in srgb, var(--eid-border-strong) 72%, transparent);
  border-radius: 8px;
  background: rgba(13, 17, 20, 0.74);
  color: var(--eid-text-muted);
  cursor: pointer;
  transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease, box-shadow 0.14s ease;
}
.expanded-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 10px 8px;
}
.menu-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 14px;
}
.menu-group h3 {
  margin: 0 0 4px;
  padding: 0 8px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 10px;
  font-weight: 780;
  letter-spacing: 0.12em;
  line-height: 1.4;
  text-transform: uppercase;
}
.menu-item {
  width: 100%;
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--eid-text-secondary);
  cursor: pointer;
  text-align: left;
}
.menu-item:hover,
.menu-item.active {
  border-color: color-mix(in srgb, var(--eid-accent) 34%, var(--eid-border));
  background:
    linear-gradient(90deg, rgba(34, 211, 238, 0.10), rgba(251, 191, 36, 0.025), transparent),
    rgba(13, 17, 20, 0.72);
  color: var(--eid-text-primary);
}
.menu-item.active {
  box-shadow: inset 2px 0 0 var(--eid-accent);
}
.menu-icon {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: inherit;
}
.menu-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.menu-copy strong,
.menu-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.menu-copy strong {
  font-size: 12px;
  font-weight: 680;
  line-height: 1.25;
}
.menu-copy small {
  color: var(--eid-text-muted);
  font-size: 10px;
  line-height: 1.2;
}
.rail-button span {
  max-width: 44px;
  overflow: hidden;
  color: inherit;
  font-size: 9px;
  font-weight: 760;
  line-height: 1;
  letter-spacing: 0.08em;
}
.rail-button:hover,
.rail-command:hover,
.rail-button.active {
  border-color: color-mix(in srgb, var(--eid-accent) 52%, var(--eid-border));
  background:
    linear-gradient(180deg, rgba(34, 211, 238, 0.13), rgba(34, 211, 238, 0.035)),
    var(--eid-bg-elev);
  color: var(--eid-accent-hover);
  box-shadow: 0 0 22px rgba(34, 211, 238, 0.12), inset 0 0 18px rgba(34, 211, 238, 0.035);
}
.rail-button.active::before {
  content: "";
  position: absolute;
  left: -11px;
  width: 3px;
  height: 28px;
  border-radius: 2px;
  background: var(--eid-accent);
  box-shadow: 0 0 12px var(--eid-accent);
}
.rail-command {
  margin: 10px;
  color: var(--eid-accent);
}
.aside.expanded .rail-command {
  width: auto;
  height: 38px;
  flex-direction: row;
  gap: 8px;
  justify-content: flex-start;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 650;
}
.header {
  position: relative;
  background:
    linear-gradient(90deg, rgba(34, 211, 238, 0.045), rgba(251, 191, 36, 0.025), transparent),
    color-mix(in srgb, var(--eid-bg-canvas) 84%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--eid-accent) 24%, var(--eid-border));
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--eid-header-h);
  padding: 0 20px;
  backdrop-filter: blur(14px);
}
.header::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--eid-accent), var(--eid-accent-warm), transparent);
  opacity: 0.45;
}
.command-chip {
  min-width: min(560px, 64vw);
  height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 0 12px;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 24%, var(--eid-border));
  border-radius: 8px;
  background:
    linear-gradient(90deg, rgba(34, 211, 238, 0.095), rgba(251, 191, 36, 0.035), transparent),
    rgba(8, 13, 16, 0.84);
  color: var(--eid-text-primary);
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.035);
}
.command-chip:hover {
  border-color: color-mix(in srgb, var(--eid-accent) 48%, var(--eid-border));
  box-shadow: 0 0 28px rgba(34, 211, 238, 0.09), inset 0 1px 0 rgba(255, 255, 255, 0.045);
}
.chip-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--eid-success);
  box-shadow: 0 0 12px var(--eid-success);
}
.chip-kicker {
  color: var(--eid-accent);
  font-family: var(--eid-font-mono);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.14em;
}
.crumb {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--eid-text-primary);
  font-weight: 650;
  letter-spacing: 0.02em;
}
.command-chip kbd {
  padding: 2px 6px;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 22%, var(--eid-border));
  border-radius: 4px;
  background: rgba(34, 211, 238, 0.08);
  color: var(--eid-text-secondary);
  font-size: 11px;
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
  height: calc(100vh - var(--eid-header-h));
  min-width: 0;
  overflow: auto;
  background: transparent;
  padding: 20px;
}

:global(.command-dialog) {
  --el-dialog-bg-color: transparent;
  --el-dialog-padding-primary: 0;
  border: 0;
  box-shadow: none;
}
:global(.command-dialog .el-dialog__header) {
  display: none;
}
:global(.command-dialog .el-dialog__body) {
  padding: 0;
}
.command-console {
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 28%, var(--eid-border-strong));
  border-radius: 10px;
  background:
    linear-gradient(rgba(34, 211, 238, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 211, 238, 0.025) 1px, transparent 1px),
    linear-gradient(180deg, rgba(34, 211, 238, 0.12), rgba(251, 191, 36, 0.035) 92px, rgba(8, 13, 16, 0.98) 180px),
    var(--eid-bg-panel);
  background-size: 24px 24px, 24px 24px, auto, auto;
  box-shadow: var(--eid-shadow-lg), 0 0 80px rgba(34, 211, 238, 0.12);
}
.command-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 20px 14px;
  border-bottom: 1px solid color-mix(in srgb, var(--eid-accent) 20%, var(--eid-border));
}
.console-kicker {
  color: var(--eid-accent);
  font-family: var(--eid-font-mono);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.command-head h2 {
  margin: 4px 0 0;
  color: var(--eid-text-primary);
  font-size: 22px;
  font-weight: 780;
  letter-spacing: 0.01em;
}
.console-close {
  align-self: flex-start;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 20%, var(--eid-border));
  border-radius: 4px;
  background: rgba(8, 13, 16, 0.7);
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
  cursor: pointer;
  padding: 5px 8px;
}
.command-input {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 20px 12px;
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, var(--eid-accent) 26%, var(--eid-border));
  border-radius: 8px;
  background: rgba(6, 9, 11, 0.76);
  color: var(--eid-accent);
}
.command-input input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--eid-text-primary);
  font-size: 15px;
}
.command-input input::placeholder {
  color: var(--eid-text-muted);
}
.scope-strip {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 0 20px 14px;
}
.scope-strip button {
  flex: 0 0 auto;
  border: 1px solid var(--eid-border);
  border-radius: 999px;
  background: rgba(13, 17, 20, 0.78);
  color: var(--eid-text-secondary);
  cursor: pointer;
  font-size: 12px;
  padding: 5px 10px;
}
.scope-strip button.active,
.scope-strip button:hover {
  border-color: color-mix(in srgb, var(--eid-accent) 42%, var(--eid-border));
  background: var(--eid-accent-soft);
  color: var(--eid-accent-hover);
}
.command-results {
  max-height: min(58vh, 520px);
  overflow: auto;
  padding: 4px 20px 20px;
}
.command-group + .command-group {
  margin-top: 16px;
}
.command-group h3 {
  margin: 0 0 8px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}
.command-card {
  width: 100%;
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  margin: 6px 0;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--eid-border-strong) 70%, transparent);
  border-radius: 7px;
  background: rgba(13, 17, 20, 0.74);
  color: var(--eid-text-primary);
  cursor: pointer;
  text-align: left;
}
.command-card:hover {
  border-color: color-mix(in srgb, var(--eid-accent) 46%, var(--eid-border));
  background: linear-gradient(90deg, rgba(34, 211, 238, 0.13), rgba(34, 211, 238, 0.035));
}
.command-icon {
  width: 38px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: rgba(34, 211, 238, 0.1);
  color: var(--eid-accent);
}
.command-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.command-copy strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.command-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
  font-size: 11px;
}
.command-arrow {
  color: var(--eid-accent-warm);
  font-family: var(--eid-font-mono);
}
.command-empty {
  padding: 36px;
  color: var(--eid-text-muted);
  text-align: center;
}

@media (max-width: 760px) {
  .header {
    padding: 0 12px;
  }
  .command-chip {
    min-width: 0;
    width: 100%;
  }
  .chip-kicker,
  .command-chip kbd {
    display: none;
  }
  :deep(.el-main) {
    padding: 12px;
  }
  :global(.command-dialog) {
    width: calc(100vw - 24px) !important;
  }
}
</style>
