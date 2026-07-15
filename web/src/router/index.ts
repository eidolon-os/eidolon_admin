import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AdminLayout from '@/layouts/AdminLayout.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/mission-control',
    name: 'mission-control',
    component: () => import('@/modules/mission-control/MissionControl.vue'),
  },
  {
    path: '/',
    component: AdminLayout,
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/modules/home/MyEidolon.vue'),
      },
      {
        path: 'supervisor',
        name: 'supervisor',
        component: () => import('@/modules/supervisor/Overview.vue'),
      },
      {
        path: 'spaces',
        name: 'spaces',
        component: () => import('@/modules/owners/Overview.vue'),
      },
      {
        path: 'owners',
        redirect: { name: 'spaces' },
      },
      {
        path: 'owners/:ownerId/:section?',
        name: 'owner-workspace',
        redirect: (to) => {
          const ownerId = String(to.params.ownerId || '')
          const section = String(to.params.section || 'overview')
          const query = { ...to.query, owner_id: ownerId }
          if (section === 'initialize') return { name: 'workspace-initialize', query }
          if (section === 'companions' || section === 'persona') return { name: 'companions', query }
          if (section === 'devices') return { name: 'devices', params: { tab: 'fleet' }, query }
          if (['conversations', 'memory', 'jobs', 'events'].includes(section)) {
            return { name: 'data-inspector', params: { section }, query }
          }
          return { name: 'home', query }
        },
      },
      {
        path: 'advanced/data/:section?',
        name: 'data-inspector',
        component: () => import('@/modules/owners/DataInspector.vue'),
      },
      {
        path: 'advanced/workspace-initialize',
        name: 'workspace-initialize',
        component: () => import('@/modules/owners/WorkspaceInitialization.vue'),
      },
      {
        path: 'configs',
        name: 'configs',
        component: () => import('@/modules/configs/Overview.vue'),
      },
      {
        path: 'benchmarks/:project',
        name: 'benchmarks',
        component: () => import('@/modules/benchmark/Overview.vue'),
      },
      {
        path: 'companions/new',
        name: 'companion-create',
        component: () => import('@/modules/companions/CompanionCreate.vue'),
      },
      {
        path: 'companions/:section?',
        name: 'companions',
        component: () => import('@/modules/companions/Companions.vue'),
      },
      {
        path: 'devices/:tab?',
        name: 'devices',
        component: () => import('@/modules/devices/DeviceCenter.vue'),
      },
      // Legacy deep link — the ESP32 flasher is now the Device Center's firmware tab.
      {
        path: 'tools/esp32',
        redirect: { name: 'devices', params: { tab: 'firmware' } },
      },
      {
        path: 'services/:serviceId/:feature',
        name: 'feature',
        component: () => import('@/modules/FeatureDispatcher.vue'),
      },
    ],
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
