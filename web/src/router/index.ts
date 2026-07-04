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
      { path: '', redirect: { name: 'owners' } },
      {
        path: 'supervisor',
        name: 'supervisor',
        component: () => import('@/modules/supervisor/Overview.vue'),
      },
      {
        path: 'owners',
        name: 'owners',
        component: () => import('@/modules/owners/Overview.vue'),
      },
      {
        path: 'owners/:ownerId/:section?',
        name: 'owner-workspace',
        component: () => import('@/modules/owners/Workspace.vue'),
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
