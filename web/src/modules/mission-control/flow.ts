// Companion internal circulation — the Tier-1 state-driven "flow" effect
// (§11 of docs/跨系统/事件审计追踪补全方案.md). A single *focused* companion
// with an active turn shows a light point looping
//
//   body → brain → memory → brain → body
//
// along its moon legs, expressing "this companion is running": device input →
// agent turn → recall/write → return. This module is pure geometry + state
// predicates (no Vue, no side-effects) so the component stays thin and the
// behaviour is unit-testable. Motion timing comes from the shared `motion.ts`
// tokens — never hardcoded.
import type { RuntimeDevice, RuntimeTurn } from '@/api/missionControl'
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
  /** Memory↔brain leg — lit when the active turn recalled anything. */
  mem: boolean
  /** 0..1 memory-leg intensity, saturating with recall hit count. */
  memBright: number
}

/**
 * Whether a companion should show internal circulation. Tier 1 is deliberately
 * narrow: ONLY the focused companion flows, and only while it has an active
 * turn. Active-but-unfocused companions keep the lighter `.gx-comp.active`
 * node pulse — no screen-wide traffic (density restraint, A3.3).
 */
export function shouldFlow(
  companionId: string,
  focusedId: string | undefined,
  hasTurn: boolean,
): boolean {
  return !!focusedId && companionId === focusedId && hasTurn
}

/** Recall hits at which the memory leg reaches full brightness. */
const MEM_SATURATION = 6
/** Floor brightness for a lit memory leg, so even a single hit is legible. */
const MEM_FLOOR = 0.45

/**
 * Resolve which legs light up for a companion's flow. Body follows live device
 * presence; memory follows recall hits on the active turn (brightness scales
 * from a legibility floor up to full at MEM_SATURATION). A turn with zero hits
 * leaves the memory leg dark.
 */
export function flowLegs(c: CompanionUnit): FlowLegs {
  const body = c.devices.some((d) => d.online)
  const hits = c.turn?.memory_hits ?? 0
  const mem = hits > 0
  const memBright = mem ? MEM_FLOOR + (1 - MEM_FLOOR) * Math.min(1, hits / MEM_SATURATION) : 0
  return { body, mem, memBright }
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
export function flowPath(brain: Pt, body: Pt, mem: Pt, legs: FlowLegs): string {
  const P = (p: Pt) => `${f1(p.x)} ${f1(p.y)}`
  if (legs.body && legs.mem) return `M${P(body)} L${P(brain)} L${P(mem)} L${P(brain)} L${P(body)}`
  if (legs.body) return `M${P(body)} L${P(brain)} L${P(body)}`
  if (legs.mem) return `M${P(brain)} L${P(mem)} L${P(brain)}`
  return ''
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
// mode doesn't help — its companions/active_turn still come from the real DB).
// Wired in useMissionControlStream, gated to import.meta.env.DEV — inert in prod.

/** Synthetic "running" turn with recall hits, so both flow legs light. */
export function demoFlowTurn(companionId: string): RuntimeTurn {
  return {
    turn_id: `demo-flow-${companionId}`, conversation_id: 'demo-flow',
    owner_id: '', companion_id: companionId, device_id: null, status: 'running',
    trigger: 'demo', started_at: null, finished_at: null, latency_ms: 180,
    memory_hits: 4, tool_names: [], privacy_mode: null, stages: [],
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

// ── Tier 2: event-driven directed pulses (§11, P4 skeleton) ────────────────
// Where Tier 1 is a state-driven ambient loop, Tier 2 overlays discrete,
// one-shot darts fired by real activity events off the SSE stream: each event
// maps by `source` to a leg + direction. Baseline loop = 底色; these = 定向脉冲.
// Pure geometry/mapping here; the transient queue + render live in the
// composable/component and are gated to dev behind `?flow2`.

/** A leg the circuit can light. (`agent` has no leg — it rides the body return.) */
export type FlowLeg = 'body' | 'mem'

/** A directed pulse: which leg, and whether it travels toward or away from the brain. */
export interface DirectedPulse {
  leg: FlowLeg
  /** 'in' = moon → brain (input/return); 'out' = brain → moon (query/response). */
  dir: 'in' | 'out'
}

/**
 * Map a runtime event's `source` to a directed pulse on the circuit:
 *   channel / hub → device input arriving   (body → brain, 'in')
 *   memory        → recall / write          (brain → mem,  'out')
 *   agent         → response to the body     (brain → body, 'out')
 * Sources that don't touch a leg (data / admin / mission_control) return null.
 * Mapping kept in one place so P4 can refine it (e.g. per event type/outcome).
 */
export function eventToPulse(source: string): DirectedPulse | null {
  switch (source) {
    case 'channel':
    case 'hub':
      return { leg: 'body', dir: 'in' }
    case 'memory':
      return { leg: 'mem', dir: 'out' }
    case 'agent':
      return { leg: 'body', dir: 'out' }
    default:
      return null
  }
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
 * Resolve a pulse's tone from event health. `severity` (info/warn/error) is on
 * the RuntimeEvent wire today; `outcome` is not surfaced there yet, so it's
 * optional and forward-compatible — a failed outcome or an error escalates to
 * 'bad' (alarm), a degraded outcome or a warn to 'warn' (caution), else 'normal'.
 */
export function eventTone(severity?: string, outcome?: string): PulseTone {
  if (outcome === 'failure' || severity === 'error') return 'bad'
  if (outcome === 'degraded' || severity === 'warn') return 'warn'
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
