<script setup lang="ts">
/**
 * System Health panel: shows the live audit from /api/system/health.
 *
 * Two sections inside the panel:
 *   1. Per-service port grid (one badge per declared port) — at-a-glance
 *      "are all the things I expect listening from the right owners?"
 *   2. Orphans table — explicit list of "port held by a process
 *      supervisord doesn't know about" with one-click SIGTERM.
 *
 * Self-refreshes every 5s. No props (top-level Supervisor page just
 * mounts it). The orphan-kill action surfaces success/failure inline
 * via Element Plus messages.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  formatAge,
  getSystemHealth,
  killOrphan,
  portStateBadge,
  type OrphanProcess,
  type PortStatus,
  type ServiceHealth,
  type SystemHealthResponse,
} from '@/api/systemHealth'

const data = ref<SystemHealthResponse | null>(null)
const loading = ref(false)
const acting = ref<Record<number, boolean>>({})
let timer: ReturnType<typeof setInterval> | null = null

const orphanCount = computed(() => data.value?.orphans.length ?? 0)
const downCount = computed(() => {
  let n = 0
  for (const s of data.value?.services ?? []) {
    for (const p of s.ports) if (p.state === 'down') n++
  }
  return n
})

async function refresh() {
  loading.value = true
  try {
    data.value = await getSystemHealth()
  } catch (e: any) {
    ElMessage.error(`加载 system health 失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(() => { if (!loading.value) refresh() }, 5_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

async function onKillOrphan(o: OrphanProcess) {
  try {
    await ElMessageBox.confirm(
      `SIGTERM 进程 ${o.pid} (占用 :${o.port}, 已存活 ${formatAge(o.age_seconds)})？\n\n` +
      `命令: ${o.command.slice(0, 200)}${o.command.length > 200 ? '…' : ''}`,
      '确认清理孤儿',
      { type: 'warning', confirmButtonText: '杀掉', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  acting.value[o.pid] = true
  try {
    const r = await killOrphan(o.pid, o.port)
    if (r.signaled) {
      ElMessage.success(`已 SIGTERM pid ${o.pid}`)
    } else {
      ElMessage.warning(`未能信号 pid ${o.pid}: ${r.error || 'unknown'}`)
    }
    await refresh()
  } catch (e: any) {
    ElMessage.error(`kill 失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    acting.value[o.pid] = false
  }
}

function shortCommand(cmd: string | null, maxLen = 80): string {
  if (!cmd) return '—'
  return cmd.length > maxLen ? cmd.slice(0, maxLen) + '…' : cmd
}

function rowKey(svc: ServiceHealth, port: PortStatus): string {
  return `${svc.service_id}:${port.port}`
}
</script>

<template>
  <el-card class="panel" shadow="never">
    <template #header>
      <div class="hdr">
        <div class="hdr-left">
          <h3>System Health</h3>
          <p class="hint">
            每个服务声明的端口 ↔ 实际 LISTEN 进程 ↔ supervisord 子进程清单, 三者对照.
            <span v-if="orphanCount > 0" class="warn">⚠ {{ orphanCount }} 孤儿</span>
            <span v-if="downCount > 0" class="warn">⚠ {{ downCount }} 端口未占用</span>
            <span v-if="orphanCount === 0 && downCount === 0" class="ok">✓ 全部正常</span>
          </p>
        </div>
        <div class="hdr-right">
          <el-tag
            v-if="data"
            :type="data.supervisord_reachable ? 'success' : 'danger'"
            effect="dark"
            size="small"
          >
            supervisord {{ data.supervisord_reachable ? `pid ${data.supervisord_pid ?? '?'}` : 'unreachable' }}
          </el-tag>
          <el-button
            size="small"
            :icon="Refresh"
            :loading="loading"
            @click="refresh"
          >刷新</el-button>
        </div>
      </div>
    </template>

    <!-- Per-service port grid -->
    <div v-if="data" class="grid">
      <div v-for="svc in data.services" :key="svc.service_id" class="svc">
        <div class="svc-head">
          <span class="svc-name">{{ svc.service_name }}</span>
          <span class="svc-id mono">{{ svc.service_id }}</span>
        </div>
        <div v-if="svc.ports.length === 0" class="svc-empty">
          (无声明端口)
        </div>
        <div v-else class="ports">
          <div
            v-for="p in svc.ports"
            :key="rowKey(svc, p)"
            class="port"
          >
            <div class="port-row">
              <code class="port-num">:{{ p.port }}</code>
              <el-tag
                :type="portStateBadge(p.state).tone"
                effect="dark"
                size="small"
              >{{ portStateBadge(p.state).label }}</el-tag>
              <span v-if="p.listener_pid" class="muted mono port-pid">pid {{ p.listener_pid }}</span>
            </div>
            <div v-if="p.listener_command" class="port-cmd mono">
              {{ shortCommand(p.listener_command) }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Orphans -->
    <div v-if="data && data.orphans.length > 0" class="orphans">
      <h4>孤儿进程 ({{ data.orphans.length }})</h4>
      <p class="hint">
        这些进程占着我们声明的端口, 但 supervisord 不认它们 (一般是上一次 stop
        没杀干净留下来的). 点【SIGTERM】让它们立刻释放端口.
      </p>
      <el-table :data="data.orphans" size="small" stripe>
        <el-table-column prop="pid" label="pid" width="80" />
        <el-table-column prop="ppid" label="ppid" width="80" />
        <el-table-column label="占的端口" width="100">
          <template #default="{ row }">
            <code>:{{ row.port }}</code>
          </template>
        </el-table-column>
        <el-table-column label="声明给" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.declared_for_service }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="存活" width="100">
          <template #default="{ row }">
            <span class="muted">{{ formatAge(row.age_seconds) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="命令" min-width="280">
          <template #default="{ row }">
            <code class="mono port-cmd" :title="row.command">
              {{ shortCommand(row.command, 120) }}
            </code>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="danger"
              link
              :loading="acting[row.pid]"
              @click="onKillOrphan(row)"
            >SIGTERM</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-card>
</template>

<style scoped>
.panel { background: var(--eid-bg-panel); border: 1px solid var(--eid-border); }
.hdr {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.hdr h3 {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--eid-text-primary);
}
.hint {
  margin: 0;
  font-size: 12px;
  color: var(--eid-text-secondary);
  line-height: 1.5;
}
.hdr-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.warn { color: var(--eid-warning); margin-left: 8px; }
.ok { color: var(--eid-success); margin-left: 8px; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.svc {
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius-sm);
  padding: 10px 12px;
}
.svc-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
.svc-name { font-size: 12.5px; font-weight: 600; color: var(--eid-text-primary); }
.svc-id { font-size: 11px; color: var(--eid-text-muted); }
.svc-empty { font-size: 11px; color: var(--eid-text-muted); font-style: italic; }
.ports {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.port {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.port-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.port-num { font-weight: 600; min-width: 64px; }
.port-pid { font-size: 11px; }
.port-cmd {
  font-size: 11px;
  color: var(--eid-text-muted);
  word-break: break-all;
  padding-left: 64px;
}
.mono { font-family: var(--eid-font-mono); }
.muted { color: var(--eid-text-muted); }
.orphans {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--eid-border);
}
.orphans h4 {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--eid-danger);
}
</style>
