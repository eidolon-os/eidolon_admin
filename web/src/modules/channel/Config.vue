<script setup lang="ts">
import ServiceConfig from '@/modules/common/ServiceConfig.vue'
import { getChannelConfig } from '@/api/channel'
</script>

<template>
  <div class="page">
    <ServiceConfig title="Channel Config" :loader="getChannelConfig">
      <template #note>
        只读视图 — channel 的 .env 由 channel 项目自己维护。如需修改，编辑该文件后在 Supervisor 页 restart channel-worker。
      </template>
    </ServiceConfig>
  </div>
</template>
