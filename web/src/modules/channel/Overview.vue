<script setup lang="ts">
import ServiceOverview from '@/modules/common/ServiceOverview.vue'
import { getChannelConfig } from '@/api/channel'
</script>

<template>
  <ServiceOverview
    service-id="channel"
    program="channel:channel-worker"
    title="Channel Worker"
    :config-loader="getChannelConfig"
  >
    <template #config-note>
      只读视图 — channel 的 .env 由 channel 项目自己维护。如需修改，编辑该文件后在「主机服务」(Host Services) 页重启 channel。
    </template>
  </ServiceOverview>
</template>
