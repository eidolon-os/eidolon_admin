// Companion internal circulation driven by its own current runtime activity.
// A focused or low-density active companion shows a light point looping
//
//   body → brain → memory → brain → body
//
// along its moon legs, expressing "this companion is running": device input →
// agent turn → recall/write → return. This module is pure geometry + state
// predicates (no Vue, no side-effects) so the component stays thin and the
// behaviour is unit-testable. Motion timing comes from the shared `motion.ts`
// tokens — never hardcoded.
import type { RuntimeDevice, RuntimeEvent, RuntimeTurn } from '@/api/missionControl'
import { SPINE_ORDER } from './constants'
import { DURATION } from './motion'
import type { CompanionUnit } from './types'

/** A point in the constellation's viewBox coordinate space. */
export interface Pt {
  x: number
  y: number
}

/** Which loop legs are lit, and how bright the memory leg burns. */
export interface FlowLegs {
  /** Body↔brain leg — lit when the companion has an online device. */
  body: boolean
  /** Memory↔brain leg — lit when the scoped voice detail recalled anything. */
  mem: boolean
  /** 0..1 memory-leg intensity, saturating with recall hit count. */
  memBright: number
  /** Companion↔activity leg — lit while a turn is active. */
  act: boolean
}

// ── voice-detail stage fallback ────────────────────────────────────────────
// RuntimeActivity.current_hop_id is authoritative for concurrent projection.
// This helper keeps the selected voice detail internally consistent when only
// legacy RuntimeTurn stages are available.
const STAGE_RUNNING = ['running', 'pending', 'active']
const STAGE_DONE = ['done', 'ok', 'succeeded']

/** Key of the stage a turn is currently at, or '' when there are none. */
export function currentStageKey(turn: RuntimeTurn | null | undefined): string {
  const stages = turn?.stages || []
  const running = stages.find((s) => STAGE_RUNNING.includes(String(s.status || '').toLowerCase()))
  if (running) return running.key
  const last = [...stages].reverse().find((s) => STAGE_DONE.includes(String(s.status || '').toLowerCase()))
  return last?.key || ''
}

/**
 * Whether the request-spine edge ending at `edgeTo` has been reached by the
 * current stage's service `hot` — i.e. the signal has travelled at least this
 * far, so the flow reads as a wavefront arriving where it is now rather than the
 * whole spine lighting at once. Empty `hot` (no stage info) or an off-spine hot
 * service flows the whole spine as a graceful default (never goes dark).
 */
export function spineReached(hot: string, edgeTo: string): boolean {
  if (!hot) return true
  const hi = SPINE_ORDER.indexOf(hot)
  const ti = SPINE_ORDER.indexOf(edgeTo)
  return hi < 0 || ti < 0 ? true : ti <= hi
}

/** Which of a companion's three moons the current stage lights: input arrives at
 * the body, recall/write touch memory, the agent turn (+ its tools) is the
 * activity itself. '' when the stage maps to no moon. Same stage vocabulary as
 * STAGE_SVC, so constellation moon, bus wavefront and trace playhead agree. */
export type StageMoon = 'body' | 'mem' | 'act'
const STAGE_MOON: Record<string, StageMoon> = {
  input: 'body', speech: 'body', duck: 'body', eot: 'body', commit: 'act',
  memory_recall: 'mem', agent_turn: 'act', brain: 'act', response: 'act',
  tools: 'act', tts: 'body', playback: 'body', memory_write: 'mem',
}
export function stageMoon(stageKey: string): StageMoon | '' {
  return STAGE_MOON[stageKey] || ''
}

/** Active-companion count at/below which unfocused companions still circulate. */
export const AUTO_FLOW_MAX = 2

/**
 * Whether a companion should show internal circulation. The focused companion
 * always circulates; otherwise an active companion circulates while few are
 * active at once (<= AUTO_FLOW_MAX), so the default god's-eye view looks alive
 * without a click and denser scenes fall back to the lighter node pulse (density
 * restraint, A3.3). Never circulates without a current runtime activity.
 */
export function shouldFlow(
  companionId: string,
  focusedId: string | undefined,
  hasActivity: boolean,
  activeCount: number,
): boolean {
  if (!hasActivity) return false
  if (focusedId && companionId === focusedId) return true
  return activeCount <= AUTO_FLOW_MAX
}

/** Recall hits at which the memory leg reaches full brightness. */
const MEM_SATURATION = 6
/** Floor brightness for a lit memory leg, so even a single hit is legible. */
const MEM_FLOOR = 0.45

/**
 * Resolve which legs light up for a companion's flow. Body follows live device
 * presence; memory follows recall hits on the scoped voice detail (brightness
 * scales from a legibility floor up to full at MEM_SATURATION). A voice turn
 * with zero hits leaves the memory leg dark.
 */
export function flowLegs(c: CompanionUnit): FlowLegs {
  const body = c.devices.some((d) => d.online)
  const hits = c.turn?.memory_hits ?? 0
  const mem = hits > 0
  const memBright = mem ? MEM_FLOOR + (1 - MEM_FLOOR) * Math.min(1, hits / MEM_SATURATION) : 0
  return { body, mem, memBright, act: !!c.activeActivity }
}

const f1 = (n: number) => n.toFixed(1)

/**
 * Build the closed loop path for the flow pulse. The loop threads
 * body → brain → memory → brain → body so a dot animating with
 * `repeatCount="indefinite"` returns to its start seamlessly (no teleport),
 * tracing the input → recall → return circuit. Unlit legs are dropped and the
 * loop always closes back on where it began. Returns '' when nothing is lit
 * (the node pulse alone then signals "brain active").
 */
export function flowPath(brain: Pt, body: Pt, mem: Pt, act: Pt, legs: FlowLegs): string {
  const P = (p: Pt) => `${f1(p.x)} ${f1(p.y)}`
  const moons = [legs.body ? body : null, legs.act ? act : null, legs.mem ? mem : null]
    .filter((point): point is Pt => point !== null)
  if (!moons.length) return ''
  const path = [`M${P(moons[0])}`]
  for (let i = 1; i < moons.length; i += 1) path.push(`L${P(brain)}`, `L${P(moons[i])}`)
  path.push(`L${P(brain)}`, `L${P(moons[0])}`)
  return path.join(' ')
}

/** Straight legs in a loop path (one per `L` command). */
export function loopSegments(path: string): number {
  return (path.match(/L/g) || []).length
}

/**
 * animateMotion `dur` for a loop: DURATION.ambient per leg, so the dot holds a
 * roughly constant, calm speed whether the loop has two legs or four. Constant
 * velocity (no easing) is intentional for circulation — timing is derived from
 * the shared motion token, never a magic number.
 */
export function flowDur(path: string): string {
  const secs = (DURATION.ambient * Math.max(1, loopSegments(path))) / 1000
  return `${secs.toFixed(2)}s`
}

/**
 * `begin` offset for a trailing dot: half a loop, negative so the dot starts
 * already mid-path (no first-frame flash at the origin). Two dots then stay on
 * opposite sides of the loop.
 */
export function flowStagger(path: string): string {
  const secs = (DURATION.ambient * Math.max(1, loopSegments(path))) / 2000
  return `-${secs.toFixed(2)}s`
}

// ── dev-only demo hook ────────────────────────────────────────────────────
// `?demoFlow=<companionId>` (or bare `?demoFlow` → first companion) overlays a
// synthetic in-conversation turn + online body onto one companion so the
// circulation effect can be *seen* without staging a real agent turn (replay
// mode doesn't help because companions and recent turns still come from real DB).
// Wired in useMissionControlStream, gated to import.meta.env.DEV — inert in prod.

/** Synthetic "running" turn with recall hits, so both flow legs light. */
export function demoFlowTurn(companionId: string): RuntimeTurn {
  return {
    turn_id: `demo-flow-${companionId}`, conversation_id: 'demo-flow',
    owner_id: '', companion_id: companionId, device_id: null, status: 'running',
    trigger: 'demo', started_at: null, finished_at: null, latency_ms: 180,
    memory_hits: 4, tool_names: [], privacy_mode: null,
    // A mid-flight turn so the demo also exercises the shared playhead: input +
    // recall done, agent_turn running (→ hotService 'agent', bus wavefront +
    // trace playhead land on it), write still pending.
    stages: [
      { key: 'input', label: '输入', status: 'done', latency_ms: 40 },
      { key: 'memory_recall', label: '记忆召回', status: 'done', latency_ms: 55 },
      { key: 'agent_turn', label: '推理', status: 'running', latency_ms: null },
      { key: 'memory_write', label: '写回', status: 'pending', latency_ms: null },
    ],
    trace_id: `demo-flow-${companionId}`, channel_turn_id: `demo-flow-${companionId}`,
    agent_turn_id: null, phase: 'agent_turn', outcome: 'deferred', terminal_reason: '',
    event_ids: [], missing_milestones: [],
  }
}

/** Synthetic online body, so the body leg lights when a demo companion has none. */
export function demoFlowDevice(companionId: string): RuntimeDevice {
  return {
    device_id: `demo-body-${companionId}`, name: 'demo body', role: '', kind: 'virtual',
    status: 'online', online: true, approved: true, owner_id: null,
    companion_id: companionId, interaction_mode: null, room_name: '',
    participant_sid: '', last_seen_at: null, capabilities: [], signals: {},
  }
}

/**
 * Whether a companion is the `?demoFlow` target: an exact id match, or — when
 * the param is present but empty (bare `?demoFlow`) — the first companion.
 * `undefined` (param absent) never matches.
 */
export function isDemoFlowTarget(companionId: string, index: number, demoFlow: string | undefined): boolean {
  if (demoFlow === undefined) return false
  return demoFlow ? companionId === demoFlow : index === 0
}

// ── event-driven directed pulses ───────────────────────────────────────────
// Discrete one-shot darts are fired by observed activity events off the SSE
// stream; each event maps by `source` to a leg + direction. The ambient loop is
// the baseline and these are directional pulses.
// Pure geometry/mapping here; the transient queue + render live in the
// composable/component. On by default for every companion (`?flow2=off`
// disables; callers may explicitly narrow the scope to one companion).

/** A leg the circuit can light. */
export type FlowLeg = 'body' | 'mem' | 'act'

/** A directed pulse: which leg, and whether it travels toward or away from the brain. */
export interface DirectedPulse {
  leg: FlowLeg
  /** 'in' = moon → brain (input/return); 'out' = brain → moon (query/response). */
  dir: 'in' | 'out'
}

/**
 * Map an event's semantic phase/milestone to a directed pulse. Source is only
 * the fallback: Channel may touch body, activity, or playback depending on the
 * exact turn fact; memory and Agent events use their respective moons.
 * Sources that don't touch a leg (data / admin / mission_control) return null.
 * Mapping is kept in one place so the cloud and event list cannot disagree.
 */
export function eventToPulse(event: Pick<RuntimeEvent, 'source' | 'type' | 'payload'>): DirectedPulse | null {
  const semantic = String(event.payload?.milestone || event.payload?.phase || '')
  if (event.source === 'channel') {
    if (['generating', 'brain_request_sent'].includes(semantic)) return { leg: 'act', dir: 'out' }
    if (['brain_first_delta', 'brain_done', 'brain_cancelled', 'brain_error', 'llm_error'].includes(semantic))
      return { leg: 'act', dir: 'in' }
    if (['tts_provider_first_audio', 'first_audio', 'playback_done'].includes(semantic))
      return { leg: 'body', dir: 'out' }
    if (event.type === 'channel.turn.completed') return { leg: 'body', dir: 'out' }
    return { leg: 'body', dir: 'in' }
  }
  if (event.source === 'hub') return { leg: 'body', dir: 'in' }
  if (event.source === 'memory')
    return { leg: 'mem', dir: event.type.includes('recalled') || event.type.includes('result') ? 'in' : 'out' }
  if (event.source === 'agent') return { leg: 'act', dir: 'in' }
  return null
}

/**
 * Decide whether an event belongs to one logical turn. Empty correlation ids
 * never match each other; legacy Agent-only turns fall back to their Agent id,
 * while unified voice turns can match Channel id, Agent id, or trace id.
 */
export function eventBelongsToTurn(
  event: Pick<RuntimeEvent, 'event_id' | 'trace_id' | 'turn_id' | 'payload'>,
  turn: Pick<RuntimeTurn, 'turn_id' | 'trace_id' | 'channel_turn_id' | 'agent_turn_id' | 'event_ids'>,
): boolean {
  if (turn.event_ids?.includes(event.event_id)) return true
  if (turn.trace_id && event.trace_id === turn.trace_id) return true
  const eventTurnIds = [
    event.turn_id,
    String(event.payload?.channel_turn_id || '') || null,
    String(event.payload?.brain_turn_id || '') || null,
  ].filter((value): value is string => !!value)
  const turnIds = [turn.turn_id, turn.channel_turn_id, turn.agent_turn_id].filter(
    (value): value is string => !!value,
  )
  return eventTurnIds.some((eventTurnId) => turnIds.includes(eventTurnId))
}

/** Single-leg path: 'in' travels moon→brain, 'out' travels brain→moon. */
export function directedLegPath(brain: Pt, moon: Pt, dir: 'in' | 'out'): string {
  const P = (p: Pt) => `${f1(p.x)} ${f1(p.y)}`
  return dir === 'in' ? `M${P(moon)} L${P(brain)}` : `M${P(brain)} L${P(moon)}`
}

/** How long a one-shot event dart takes to cross a leg (from the motion token). */
export const EVENT_PULSE_MS = DURATION.slow
export function flowEventDur(): string {
  return `${(DURATION.slow / 1000).toFixed(2)}s`
}

/** Health tone of a directed pulse — drives its colour. */
export type PulseTone = 'normal' | 'warn' | 'bad'

/**
 * Resolve a pulse's tone from event health, using both wire axes: `severity`
 * ("how loud": info/warn/error) and `outcome` ("what happened": success/failure/
 * denied/deferred). A failure or an error escalates to 'bad' (alarm); a denied
 * outcome or a warn to 'warn' (caution); success/deferred/info stay 'normal'.
 * `outcome` is optional so synthesised/legacy events without it still map.
 */
export function eventTone(severity?: string, outcome?: string): PulseTone {
  if (outcome === 'failure' || severity === 'error') return 'bad'
  if (outcome === 'denied' || severity === 'warn') return 'warn'
  return 'normal'
}

/** Min gap between darts on the same leg — one dart finishes before the next. */
export const PULSE_MIN_GAP_MS = EVENT_PULSE_MS

/**
 * Whether a new pulse on a leg should be dropped as too soon after the last one
 * (flood control, §9). Callers let 'bad' tones bypass this so failures are never
 * swallowed. Pure so the throttle window is unit-testable.
 */
export function pulseThrottled(lastMs: number, nowMs: number, minGapMs: number = PULSE_MIN_GAP_MS): boolean {
  return nowMs - lastMs < minGapMs
}

/** Which companions' events emit darts. */
export type PulseScope = 'focused' | 'all'

/**
 * Whether an event pulse for `companionId` is in scope. Mission Control uses
 * owner-wide `all` by default so concurrent companions remain visible; callers
 * may explicitly choose `focused` for a quieter detail view.
 */
export function pulseInScope(companionId: string, focusedId: string | undefined, scope: PulseScope): boolean {
  if (scope === 'all') return true
  return !!focusedId && companionId === focusedId
}
