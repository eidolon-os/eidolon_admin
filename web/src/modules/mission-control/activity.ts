import type { RuntimeActivity, RuntimeRouteHop, RuntimeTraceSpan, RuntimeTurn } from '@/api/missionControl'

export const ACTIVE_ACTIVITY_STATES = new Set([
  'running', 'active', 'pending', 'queued', 'accepted', 'processing',
  'generating', 'speaking', 'deferred',
])

export function isActiveActivity(activity: RuntimeActivity | null | undefined): boolean {
  return !!activity && ACTIVE_ACTIVITY_STATES.has((activity.status || '').toLowerCase())
}

export function currentActivityHop(activity: RuntimeActivity): RuntimeRouteHop | null {
  if (activity.current_hop_id) {
    const explicit = activity.route.find((hop) => hop.hop_id === activity.current_hop_id)
    if (explicit) return explicit
  }
  const running = [...activity.route].reverse().find((hop) => hop.status === 'running')
  if (running) return running
  return isActiveActivity(activity) ? activity.route.at(-1) || null : null
}

export function activityServiceId(activity: RuntimeActivity): string {
  const current = currentActivityHop(activity)
  const index = current ? activity.route.indexOf(current) : activity.route.length - 1
  for (let i = index; i >= 0; i -= 1) {
    const hop = activity.route[i]
    if (hop && ['service', 'memory', 'provider'].includes(hop.node_type)) return hop.node_id
  }
  return ''
}

export function activityKindLabel(kind: string): string {
  if (kind === 'voice_turn') return '对话'
  if (kind === 'guard_event') return '守护'
  if (kind === 'device_command') return '指令'
  if (kind === 'device_event') return '设备'
  if (kind === 'background_job') return '任务'
  return '活动'
}

export function activityBadgeLabel(activity: RuntimeActivity, nowMs = Date.now()): string {
  if (isActiveActivity(activity)) return '当前'
  if (activity.outcome === 'failure') return '失败'
  if (activity.outcome === 'denied') return '拒绝'
  if (activity.kind !== 'voice_turn') return activityKindLabel(activity.kind)
  const raw = activity.finished_at || activity.updated_at || activity.started_at
  const ts = raw ? Date.parse(raw) : Number.NaN
  if (!Number.isFinite(ts)) return '已完成'
  const elapsed = Math.max(0, nowMs - ts)
  if (elapsed < 60_000) return '刚刚'
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}分`
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}时`
  return `${Math.floor(elapsed / 86_400_000)}天`
}

export function activitySortTime(activity: RuntimeActivity): number {
  const raw = activity.updated_at || activity.finished_at || activity.started_at
  const parsed = raw ? Date.parse(raw) : Number.NaN
  return Number.isFinite(parsed) ? parsed : 0
}

/** Voice spans belong to one selected/focused turn, never to owner scope. */
export function traceSpansForTurn(
  spans: RuntimeTraceSpan[],
  turn: RuntimeTurn | null | undefined,
): RuntimeTraceSpan[] {
  return turn ? spans.filter((span) => span.turn_id === turn.turn_id) : []
}

export interface ActivityBadgeGroup {
  activity: RuntimeActivity
  count: number
  label: string
}

export function summarizeActivityBadges(
  activities: RuntimeActivity[],
  nowMs = Date.now(),
  limit = 4,
): ActivityBadgeGroup[] {
  const ordered = [...activities].sort(
    (a, b) => Number(isActiveActivity(b)) - Number(isActiveActivity(a)) || activitySortTime(b) - activitySortTime(a),
  )
  const groups: ActivityBadgeGroup[] = []
  const grouped = new Map<string, ActivityBadgeGroup>()
  for (const activity of ordered) {
    const groupable = !isActiveActivity(activity) && activity.kind !== 'voice_turn'
    const key = groupable ? `${activity.kind}:${activity.outcome}` : activity.activity_id
    const existing = grouped.get(key)
    if (existing) {
      existing.count += 1
      existing.label = `${activityBadgeLabel(existing.activity, nowMs)}×${existing.count}`
      continue
    }
    const row = { activity, count: 1, label: activityBadgeLabel(activity, nowMs) }
    grouped.set(key, row)
    groups.push(row)
  }
  return groups.slice(0, limit)
}
