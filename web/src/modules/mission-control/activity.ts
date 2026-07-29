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
  if (kind === 'presence_auth') return '到场认证'
  return '活动'
}

export function activityStatusLabel(status: string | null | undefined): string {
  const key = (status || '').toLowerCase()
  return ({
    running: '进行中', active: '进行中', pending: '等待中', queued: '排队中',
    completed: '已完成', succeeded: '已完成', success: '已完成',
    interrupted: '已打断', rejected: '已拒绝', denied: '已拒绝',
    timeout: '已超时', failed: '失败', error: '失败', orphaned: '已中断',
  } as Record<string, string>)[key] || status || '未知'
}

export interface ActivityPhase {
  key: string
  label: string
  glyph: string
  status: string
  latency_ms: number | null
  current: boolean
  hops: RuntimeRouteHop[]
}

const VOICE_PHASES: Record<string, { label: string; glyph: string }> = {
  access: { label: '接入', glyph: '⬡' },
  input: { label: '收音', glyph: '◉' },
  brain: { label: '思考', glyph: '◊' },
  output: { label: '回应', glyph: '◍' },
  memory: { label: '记忆', glyph: '◈' },
}

function voicePhaseKey(hop: RuntimeRouteHop): keyof typeof VOICE_PHASES {
  if (hop.stage.startsWith('memory_') || hop.node_type === 'memory') return 'memory'
  if (['brain', 'response', 'tools', 'tool'].includes(hop.stage)) return 'brain'
  if (['tts', 'playback'].includes(hop.stage)) return 'output'
  if (hop.node_type === 'device' || hop.node_type === 'companion') return 'access'
  return 'input'
}

function phaseStatus(hops: RuntimeRouteHop[]): string {
  const states = hops.map((hop) => (hop.status || '').toLowerCase())
  if (states.some((state) => ['failed', 'error', 'rejected', 'denied', 'orphaned'].includes(state))) return 'failed'
  if (states.some((state) => state === 'running')) return 'running'
  if (states.some((state) => ['pending', 'queued', 'degraded', 'interrupted'].includes(state))) return 'degraded'
  return hops.at(-1)?.status || 'done'
}

/**
 * Turns a raw route into a small number of semantic phases for the activity
 * board. The original hops stay attached for the expandable detail view.
 */
export function activityPhases(
  activity: RuntimeActivity,
  labelHop: (hop: RuntimeRouteHop) => string = (hop) => hop.label,
): ActivityPhase[] {
  const current = currentActivityHop(activity)
  if (activity.kind !== 'voice_turn') {
    return activity.route.map((hop) => ({
      key: hop.hop_id,
      label: activity.kind === 'presence_auth' ? hop.label : labelHop(hop),
      glyph: hop.node_type === 'device' ? '⬡' : hop.node_type === 'companion' ? '◎' : '◇',
      status: hop.status,
      latency_ms: hop.latency_ms,
      current: current?.hop_id === hop.hop_id,
      hops: [hop],
    }))
  }

  const grouped = new Map<string, RuntimeRouteHop[]>()
  for (const hop of activity.route) {
    const key = voicePhaseKey(hop)
    const list = grouped.get(key) || []
    list.push(hop)
    grouped.set(key, list)
  }
  return Object.entries(VOICE_PHASES).flatMap(([key, meta]) => {
    const hops = grouped.get(key) || []
    if (!hops.length) return []
    const latencies = hops.map((hop) => hop.latency_ms).filter((value): value is number => value != null)
    return [{
      key,
      label: meta.label,
      glyph: meta.glyph,
      status: phaseStatus(hops),
      latency_ms: latencies.length ? latencies.reduce((sum, value) => sum + value, 0) : null,
      current: !!current && hops.some((hop) => hop.hop_id === current.hop_id),
      hops,
    }]
  })
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
