import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AdminLayout from '@/layouts/AdminLayout.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AdminLayout,
    children: [
      { path: '', redirect: { name: 'supervisor' } },
      {
        path: 'supervisor',
        name: 'supervisor',
        component: () => import('@/modules/supervisor/Overview.vue'),
      },
      {
        path: 'configs',
        name: 'configs',
        component: () => import('@/modules/configs/Overview.vue'),
      },
      // Phase 29 catalog: Tenant → Template → User → Agent.
      // Order in the menu matches the dependency chain so operators
      // build top-down.
      {
        path: 'tenants',
        name: 'tenants',
        component: () => import('@/modules/tenants/Overview.vue'),
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('@/modules/templates/Overview.vue'),
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/modules/users/Overview.vue'),
      },
      {
        path: 'agents',
        name: 'agents',
        component: () => import('@/modules/agents/Overview.vue'),
      },
      {
        path: 'devices',
        name: 'devices',
        redirect: {
          name: 'feature',
          params: { serviceId: 'hub', feature: 'devices' },
        },
      },
      // Phase 34.B: read-only browse over agent's SQLite turn log.
      // Lives outside the catalog (it's a "what happened" surface,
      // not a "what exists" surface) but sits next to it in the menu
      // for now until we add a dedicated "Activity" section.
      {
        path: 'conversations',
        name: 'conversations',
        component: () => import('@/modules/conversations/Overview.vue'),
      },
      {
        path: 'replay-reports',
        name: 'replay-reports',
        component: () => import('@/modules/reports/ReplayReports.vue'),
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
