import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AdminLayout from '@/layouts/AdminLayout.vue'

const routes: RouteRecordRaw[] = [
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
        path: 'tools/esp32',
        name: 'tool-esp32',
        component: () => import('@/modules/tools/Esp32.vue'),
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
