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
      { path: 'tenants', redirect: { name: 'owners' } },
      { path: 'templates', redirect: { name: 'owners' } },
      { path: 'users', redirect: { name: 'owners' } },
      {
        path: 'agents',
        redirect: {
          name: 'feature',
          params: { serviceId: 'agent', feature: 'data' },
        },
      },
      {
        path: 'benchmarks',
        redirect: { name: 'benchmarks', params: { project: 'agent' } },
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
        path: 'devices',
        name: 'devices',
        redirect: {
          name: 'feature',
          params: { serviceId: 'hub', feature: 'devices' },
        },
      },
      {
        path: 'conversations',
        name: 'conversations',
        redirect: {
          name: 'feature',
          params: { serviceId: 'agent', feature: 'conversations' },
        },
      },
      {
        path: 'replay-reports',
        name: 'replay-reports',
        redirect: {
          name: 'feature',
          params: { serviceId: 'agent', feature: 'replay-reports' },
        },
      },
      // Old /deploy and /health surfaces have been merged into /supervisor.
      // Keep redirects so bookmarked URLs still land somewhere useful.
      { path: 'deploy', redirect: { name: 'supervisor' } },
      { path: 'health', redirect: { name: 'supervisor' } },
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
