// Pure, framework-free formatting helpers for the cockpit. No reactivity,
// no side effects — safe to unit-test. De-duplicated from the two former
// mission-control SFCs (fmtLatency/fmtTime/statusClass were identical).
import type { RuntimeDevice, RuntimeEvent } from '@/api/missionControl'
import type { StreamState } from './types'

export function fmtLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

export function fmtClock(ms: number): string {
  const d = new Date(ms)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

/**
 * Shortens an opaque runtime identifier for dense observability surfaces.
 * The caller should keep the original value in `title`/`aria-label`; this is
 * presentation-only and must never be used as a key or API parameter.
 */
export function compactId(value: string | null | undefined, maxLength = 24): string {
  const raw = String(value || '')
  if (!raw || raw.length <= maxLength) return raw

  const visible = Math.max(8, maxLength - 1)
  const tail = Math.max(4, Math.min(8, Math.floor(visible * 0.4)))
  const head = visible - tail
  return `${raw.slice(0, head)}…${raw.slice(-tail)}`
}

/**
 * Compacts only IDs that the event already exposes as structured fields.
 * Arbitrary summary text is left untouched, while the full summary can remain
 * available to the UI as a tooltip.
 */
export function compactEventSummary(event: RuntimeEvent): string {
  let summary = event.summary || event.type
  const ids = [
    event.turn_id,
    event.trace_id,
    event.job_id,
    event.conversation_id,
    event.companion_id,
    event.device_id,
  ]
    .filter((id): id is string => !!id && id.length > 24)
    .sort((a, b) => b.length - a.length)

  for (const id of new Set(ids)) summary = summary.split(id).join(compactId(id))
  return summary
}

/** Maps a free-form status string to a semantic tone class. */
export function statusClass(status: string | null | undefined): 'ok' | 'warn' | 'bad' | 'idle' {
  const v = (status || '').toLowerCase()
  if (['ok', 'done', 'succeeded', 'completed', 'active', 'success'].includes(v)) return 'ok'
  if (['running', 'pending', 'queued', 'degraded', 'warn', 'interrupted'].includes(v)) return 'warn'
  if (['failed', 'error', 'errored', 'offline', 'orphaned'].includes(v)) return 'bad'
  return 'idle'
}

/** Human label for a device's embodiment (physical vs virtual body). */
export function deviceType(d: RuntimeDevice): string {
  const k = `${d.kind} ${d.role}`.toLowerCase()
  if (k.includes('esp32') || k.includes('box') || k.includes('camera') || k.includes('atk') || k.includes('ptt')) return '物理身体'
  if (k.includes('web') || k.includes('virtual')) return '虚拟身体'
  return '设备'
}

export function isPreparedWebBody(d: RuntimeDevice): boolean {
  const source = String(d.signals?.source || '').toLowerCase()
  return (
    !d.online
    && String(d.kind || '').toLowerCase() === 'web'
    && (d.status === 'active' || source === 'data' || source === 'hub+data')
  )
}

export function devicePresenceLabel(d: RuntimeDevice): string {
  if (d.online) return '在线'
  if (isPreparedWebBody(d)) return '已准备'
  if (d.status === 'degraded') return '不稳定'
  if (d.status === 'active') return '已绑定'
  if (d.status === 'unknown') return '未探测'
  return '离线'
}

export function devicePresenceClass(d: RuntimeDevice): 'ok' | 'warn' | 'bad' | 'idle' {
  if (d.online) return 'ok'
  if (isPreparedWebBody(d)) return 'idle'
  if (d.status === 'degraded') return 'warn'
  if (d.status === 'offline') return 'bad'
  return 'idle'
}

/** Compact device name for tight tiles. */
export function deviceShort(d: RuntimeDevice): string {
  const n = d.name || d.device_id || ''
  return n.length > 13 ? '…' + n.slice(-10) : n
}

export function privacyModeLabel(mode: string | null | undefined): string {
  return ({ safe: '安全', summary: '摘要', restricted: '受限' } as Record<string, string>)[mode || 'safe'] || '安全'
}

export function systemStateLabel(state: string | null | undefined): string {
  return ({ active: '正在处理', working: '后台推进', watching: '感知中', standby: '待命中' } as Record<string, string>)[state || 'standby'] || '待命中'
}

export function streamLabel(state: StreamState): string {
  return ({ connecting: 'SYNC', live: 'ONLINE', degraded: 'UNSTABLE' } as Record<StreamState, string>)[state]
}
