<script setup lang="ts">
import ServiceOverview from '@/modules/common/ServiceOverview.vue'
import { getClientWebConfig } from '@/api/clientWeb'
import { clientWebBase } from '@/utils/clientWeb'

const clientWebUrl = clientWebBase()
</script>

<template>
  <ServiceOverview
    service-id="client-web"
    program="client-web:client-web"
    title="Client Web (Next.js)"
    show-http-probe
    :config-loader="getClientWebConfig"
  >
    <template #log-note>Next.js dev 把所有输出都打到 stdout（不区分 info/error）。stderr 通常只在崩溃栈时有内容。</template>
    <template #extra>
      <el-card style="margin-top: 16px">
        <template #header>访问</template>
        <p class="link">浏览器打开 <a :href="clientWebUrl" target="_blank">{{ clientWebUrl }}</a></p>
        <p class="hint">客户端依赖 hub (token 接口) 和 livekit (信令)，需要都在线。</p>
      </el-card>
    </template>
    <template #config-note>
      只读视图。修改 client-web 的 .env 后在 Supervisor 页 restart client-web 才会生效。
      <code>NEXT_PUBLIC_*</code> 变量会被 Next 嵌入到 JS bundle，重启后刷新浏览器才能拿到新值。
    </template>
  </ServiceOverview>
</template>

<style scoped>
.link { font-family: var(--eid-font-mono); font-size: 12px; }
.link a { color: var(--eid-accent); text-decoration: none; }
.link a:hover { color: var(--eid-accent-hover); text-decoration: underline; }
.hint { font-size: 12px; color: var(--eid-text-secondary); margin-top: 8px; }
</style>
