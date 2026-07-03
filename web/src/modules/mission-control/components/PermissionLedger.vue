<script setup lang="ts">
// Region 6 · Permission & Sensor Ledger: high-sensitivity capability calls,
// summary-only. Shown when there is anything to audit.
import type { PermissionLedgerItem } from '@/api/missionControl'
import { fmtTime } from '../format'

defineProps<{ items: PermissionLedgerItem[] }>()

const KIND_LABEL: Record<string, string> = {
  'camera.take_photo': '摄像头拍照',
  'room.join': '加入语音房',
  'device.identify': '设备点名',
  'device.volume': '音量',
  'device.brightness': '亮度',
  'device.command': '设备命令',
}
</script>

<template>
  <div class="perm-ledger">
    <span class="pl-cap"><i class="led mag" />权限台账</span>
    <ul class="pl-list">
      <li v-for="(it, i) in items" :key="i" class="pl-row" :class="{ sensitive: it.privacy_level === 'sensitive' }">
        <em class="num">{{ fmtTime(it.ts) }}</em>
        <b>{{ KIND_LABEL[it.kind] || it.kind }}</b>
        <span class="pl-status">{{ it.status || '—' }}</span>
        <u v-if="it.privacy_level === 'sensitive'">仅摘要·不留原图</u>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.perm-ledger { display: flex; flex-direction: column; gap: 6px; padding: 8px 14px; border: 1px solid rgba(255, 46, 136, 0.28); background: var(--cy-panel); min-width: 240px; }
.pl-cap { display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-mag); }
.pl-cap .led { width: 6px; height: 6px; }
.pl-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; max-height: 96px; overflow-y: auto; }
.pl-row { display: flex; align-items: center; gap: 8px; font: 600 10.5px/1.4 var(--cy-mono); color: var(--cy-txt); }
.pl-row em { color: var(--cy-txt-dim); font-style: normal; flex: 0 0 auto; }
.pl-row b { font-family: var(--cy-sans); font-weight: 700; }
.pl-status { color: var(--cy-txt-dim); }
.pl-row u { margin-left: auto; text-decoration: none; font-size: 9px; color: var(--cy-mag); border: 1px solid currentColor; padding: 1px 4px; }
.pl-row.sensitive b { color: var(--cy-mag); }
</style>
