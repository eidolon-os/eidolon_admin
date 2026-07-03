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
    <template #log-note>channel 是纯 LiveKit worker，没有 HTTP / NATS admin 接口。状态和日志是当前可见的全部维度。</template>
    <template #config-note>
      只读视图 — channel 的 .env 由 channel 项目自己维护。如需修改，编辑该文件后在 Supervisor 页 restart channel-worker。
    </template>
  </ServiceOverview>
</template>
