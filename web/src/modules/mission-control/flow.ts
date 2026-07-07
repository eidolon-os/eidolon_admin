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
