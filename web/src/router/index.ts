import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AdminLayout from '@/layouts/AdminLayout.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AdminLayout,
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/modules/control-plane/ControlPlane.vue'),
      },
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
      {
        path: 'benchmarks/:project',
        name: 'benchmarks',
        component: () => import('@/modules/benchmark/Overview.vue'),
      },
      {
        path: 'advanced/system/firmware',
        name: 'system-firmware',
        component: () => import('@/modules/tools/Esp32.vue'),
      },
      {
        path: 'advanced/system/mobile',
        name: 'system-mobile',
        component: () => import('@/modules/tools/Mobile.vue'),
      },
      {
        path: 'services/:serviceId/:feature',
        name: 'feature',
        component: () => import('@/modules/FeatureDispatcher.vue'),
      },
      { path: '/:pathMatch(.*)*', redirect: { name: 'home' } },
    ],
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
