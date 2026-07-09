<script setup lang="ts">
// Owner → companion body overview for the Device Center. This is the user-facing
// inventory: host-local web bodies from eidolon_data plus physical devices from
// Hub, merged server-side by /api/devices/fleet.
import { onMounted, ref, watch } from 'vue'
import { VideoPlay } from '@element-plus/icons-vue'
import { getFleet, type FleetResponse } from '@/api/devices'
import type { RuntimeDevice } from '@/api/missionControl'
import { devicePresenceClass, devicePresenceLabel, deviceShort, deviceType, isPreparedWebBody } from '@/modules/mission-control/format'
import { webBodyLaunchUrl } from '@/utils/clientWeb'

const props = defineProps<{ ownerId: string }>()

const fleet = ref<FleetResponse | null>(null)
const loading = ref(false)

async function load() {
  if (!props.ownerId) {
    fleet.value = null
    return
  }
  loading.value = true
  try {
    fleet.value = await getFleet(props.ownerId)
  } catch {
    fleet.value = null
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(() => props.ownerId, load)

function isWebBody(d: RuntimeDevice): boolean {
  return String(d.kind || '').toLowerCase() === 'web'
}

function sourceLabel(d: RuntimeDevice): string {
  const source = String(d.signals?.source || '')
  if (source === 'data') return 'owner 数据'
  if (source === 'hub+data') return 'owner + Hub'
  if (source === 'hub') return 'Hub'
  return source || '未知来源'
}

function bodyHint(d: RuntimeDevice): string {
  if (isPreparedWebBody(d)) return '网页端入口已准备，可直接启动'
  if (d.online) return d.room_name ? `已进入 ${d.room_name}` : '运行时在线'
  if (d.status === 'active') return '已绑定，等待会话连接'
  if (d.status === 'degraded') return '连接不稳定'
  return '等待连接'
}

function launchBody(companionId: string, d: RuntimeDevice) {
  if (!props.ownerId || !isWebBody(d)) return
  window.open(
    webBodyLaunchUrl({ ownerId: props.ownerId, companionId, deviceId: d.device_id }),
    '_blank',
    'noopener',
  )
}
</script>

<template>
  <div v-if="ownerId" class="fleet-grouping" v-loading="loading">
    <div class="fg-head">
      <div>
        <div class="fg-cap">我的身体 / 网页端入口</div>
        <p>这里列出当前 owner 已拥有的全部入口，包括本机 Web 身体和 Hub 物理设备。</p>
      </div>
      <el-tag size="small" type="info" effect="plain">Owner scoped</el-tag>
    </div>
    <div class="fg-grid">
      <div v-for="g in fleet?.groups || []" :key="g.companion_id" class="fg-card">
        <div class="fg-h"><b>{{ g.companion_name }}</b><em>{{ g.devices.length }} 身体</em></div>
        <ul>
          <li v-for="d in g.devices" :key="d.device_id" class="fg-device">
            <i class="dot" :class="'st-' + devicePresenceClass(d)" />
            <div class="fg-device-main">
              <strong>{{ d.name || deviceShort(d) }}</strong>
              <span>{{ deviceType(d) }} · {{ devicePresenceLabel(d) }} · {{ sourceLabel(d) }}</span>
              <em>{{ bodyHint(d) }}</em>
            </div>
            <el-button
              v-if="isWebBody(d)"
              size="small"
              type="primary"
              plain
              :icon="VideoPlay"
              @click="launchBody(g.companion_id, d)"
            >
              启动
            </el-button>
          </li>
          <li v-if="!g.devices.length" class="empty">未绑定身体</li>
        </ul>
      </div>
      <div v-if="fleet?.unbound?.length" class="fg-card unbound">
        <div class="fg-h"><b>未认领</b><em>{{ fleet.unbound.length }}</em></div>
        <ul>
          <li v-for="d in fleet.unbound" :key="d.device_id" class="fg-device">
            <i class="dot" :class="'st-' + devicePresenceClass(d)" />
            <div class="fg-device-main">
              <strong>{{ d.name || deviceShort(d) }}</strong>
              <span>{{ deviceType(d) }} · {{ devicePresenceLabel(d) }} · {{ sourceLabel(d) }}</span>
              <em>{{ bodyHint(d) }}</em>
            </div>
          </li>
        </ul>
      </div>
      <div v-if="!(fleet?.groups?.length) && !(fleet?.unbound?.length)" class="fg-empty">当前 owner 暂无设备</div>
    </div>
    <p class="fg-note">下方 Hub 硬件设备表只管理物理设备的发现、批准和 LiveKit 可达性；本机 Web 身体不需要 Hub 审批。</p>
  </div>
</template>

<style scoped>
.fleet-grouping { margin-bottom: 14px; padding: 14px; border: 1px solid var(--eid-border); border-radius: var(--eid-radius); background: var(--eid-bg-panel); }
.fg-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.fg-head p { margin: 4px 0 0; color: var(--eid-text-secondary); font-size: 12px; line-height: 1.5; }
.fg-cap { font-family: var(--eid-font-mono); font-size: 10px; letter-spacing: 0.1em; color: var(--eid-text-muted); }
.fg-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.fg-card { border: 1px solid var(--eid-border); border-radius: 6px; padding: 10px; background: var(--eid-bg-elev); }
.fg-card.unbound { border-style: dashed; }
.fg-h { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.fg-h b { font-size: 13px; font-weight: 700; color: var(--eid-text-primary); }
.fg-h em { font-family: var(--eid-font-mono); font-size: 10px; font-style: normal; color: var(--eid-text-muted); }
.fg-card ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.fg-device { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; align-items: center; gap: 8px; min-width: 0; }
.fg-device-main { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.fg-device-main strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--eid-text-primary); font-size: 12px; }
.fg-device-main span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--eid-text-secondary); font-size: 11px; }
.fg-device-main em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--eid-text-muted); font-style: normal; font-size: 11px; }
.fg-card li.empty { color: var(--eid-text-muted); font-style: italic; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--eid-text-muted); flex: 0 0 auto; }
.dot.st-ok { background: var(--eid-success); box-shadow: 0 0 6px var(--eid-success); }
.dot.st-warn { background: var(--eid-warning); box-shadow: 0 0 6px var(--eid-warning); }
.dot.st-bad { background: var(--eid-danger); box-shadow: 0 0 6px var(--eid-danger); }
.dot.st-idle { background: var(--eid-text-muted); }
.fg-empty { color: var(--eid-text-muted); font-size: 12px; padding: 8px; }
.fg-note { margin: 12px 0 0; color: var(--eid-text-muted); font-size: 11px; line-height: 1.5; }
@media (max-width: 720px) {
  .fg-head { flex-direction: column; }
  .fg-grid { grid-template-columns: 1fr; }
}
</style>
