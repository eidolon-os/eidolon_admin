<script setup lang="ts">
import ServiceConfig from '@/modules/common/ServiceConfig.vue'
import { getClientWebConfig } from '@/api/clientWeb'
</script>

<template>
  <div class="page">
    <ServiceConfig title="Client Web Config" :loader="getClientWebConfig">
      <template #note>
        只读视图。修改该 .env 后需重启 client-web 才会生效（它不由 eidolond 管理，不在「主机服务」页）。
        <code>NEXT_PUBLIC_*</code> 变量会被 Next 嵌入到 JS bundle，重启后刷新浏览器才能拿到新值。
      </template>
    </ServiceConfig>
  </div>
</template>
