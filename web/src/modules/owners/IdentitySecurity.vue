<script setup lang="ts">
import { computed } from 'vue'
import GuardPanel from './GuardPanel.vue'
import { useOwnersStore } from '@/stores/owners'

const ownersStore = useOwnersStore()
const ownerId = computed(() => ownersStore.currentId)
</script>

<template>
  <section class="identity-security">
    <header class="page-head">
      <div>
        <p>IDENTITY &amp; SECURITY</p>
        <h1>身份与安全</h1>
        <span>管理当前 Eidolon 空间的 Guard、Owner Face 与身份策略下发。</span>
      </div>
      <el-tag v-if="ownerId" size="small" type="info" effect="plain">Owner scoped</el-tag>
    </header>
    <GuardPanel v-if="ownerId" :owner-id="ownerId" />
    <el-empty v-else description="请先选择一个 Eidolon 空间" />
  </section>
</template>

<style scoped>
.identity-security { display: flex; width: min(1180px, 100%); margin: 0 auto; padding-bottom: 32px; flex-direction: column; gap: 18px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 16px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.page-head p { margin: 0; color: var(--eid-text-muted); font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: .12em; }
.page-head h1 { margin: 5px 0; color: var(--eid-text-primary); font-size: 24px; }
.page-head span { color: var(--eid-text-secondary); font-size: 12px; }
</style>
