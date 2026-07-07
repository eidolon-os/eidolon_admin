/**
 * Pure-unit tests for the companion internal-circulation logic
 * (``src/modules/mission-control/flow.ts``) — the Tier-1 state-driven "flow"
 * effect (§11 of docs/跨系统/事件审计追踪补全方案.md).
 *
 * These are deterministic functions over their arguments — no admin gateway,
 * no DOM, no SMIL. They pin the three decisions the design calls out:
 *   1. which companions circulate (focused + active turn),
 *   2. which legs light up (memory_hits / devices.online),
 *   3. how the loop path + timing are generated (reusing DURATION tokens).
 */
import { describe, expect, it } from 'vitest'
import {
  demoFlowDevice,
  demoFlowTurn,
  directedLegPath,
  eventToPulse,
  flowDur,
  flowEventDur,
  flowLegs,
  flowPath,
  flowStagger,
  isDemoFlowTarget,
  loopSegments,
  shouldFlow,
  type FlowLegs,
  type Pt,
} from '../src/modules/mission-control/flow'
import { DURATION } from '../src/modules/mission-control/motion'
import type { CompanionUnit } from '../src/modules/mission-control/types'
import type { RuntimeDevice, RuntimeTurn } from '../src/api/missionControl'

// ── fixtures ────────────────────────────────────────────────────────────
function device(over: Partial<RuntimeDevice> = {}): RuntimeDevice {
  return {
    device_id: 'd1', name: 'dev', role: '', kind: 'esp32', status: 'ok',
    online: true, approved: true, owner_id: 'o1', companion_id: 'c1',
    interaction_mode: null, room_name: '', participant_sid: '',
    last_seen_at: null, capabilities: [], signals: {}, ...over,
  }
}
function turn(over: Partial<RuntimeTurn> = {}): RuntimeTurn {
  return {
    turn_id: 't1', conversation_id: 'cv1', owner_id: 'o1', companion_id: 'c1',
    device_id: 'd1', status: 'running', trigger: 'voice', started_at: null,
    finished_at: null, latency_ms: 120, memory_hits: 0, tool_names: [],
    privacy_mode: null, stages: [], ...over,
  }
}
function companion(over: Partial<CompanionUnit> = {}): CompanionUnit {
  return {
    id: 'c1', name: 'Aria', kind: 'companion', status: 'active', genome: '',
    realm: '', isActiveRealm: false, recall: null, runners: '', write: '',
    devices: [], turn: null, jobs: [], isPrimary: false, ...over,
  }
}

// ── shouldFlow: which companions circulate ────────────────────────────────
describe('shouldFlow', () => {
  it('flows only the focused companion with an active turn', () => {
    expect(shouldFlow('c1', 'c1', true)).toBe(true)
  })
  it('does not flow when no companion is focused', () => {
    expect(shouldFlow('c1', '', true)).toBe(false)
    expect(shouldFlow('c1', undefined, true)).toBe(false)
  })
  it('does not flow a non-focused companion even if active', () => {
    // active-but-unfocused keeps the lighter node pulse, not screen-wide traffic
    expect(shouldFlow('c2', 'c1', true)).toBe(false)
  })
  it('does not flow the focused companion when it has no active turn', () => {
    expect(shouldFlow('c1', 'c1', false)).toBe(false)
  })
})

// ── flowLegs: leg-lit determination ───────────────────────────────────────
describe('flowLegs', () => {
  it('lights the body leg when any device is online', () => {
    const c = companion({ devices: [device({ online: false }), device({ online: true })] })
    expect(flowLegs(c).body).toBe(true)
  })
  it('darkens the body leg when every device is offline', () => {
    const c = companion({ devices: [device({ online: false })] })
    expect(flowLegs(c).body).toBe(false)
  })
  it('darkens the body leg when there are no devices', () => {
    expect(flowLegs(companion({ devices: [] })).body).toBe(false)
  })
  it('lights the memory leg only when the turn recalled hits', () => {
    expect(flowLegs(companion({ turn: turn({ memory_hits: 0 }) })).mem).toBe(false)
    expect(flowLegs(companion({ turn: turn({ memory_hits: 3 }) })).mem).toBe(true)
  })
  it('leaves the memory leg dark with no active turn', () => {
    const legs = flowLegs(companion({ turn: null }))
    expect(legs.mem).toBe(false)
    expect(legs.memBright).toBe(0)
  })
  it('scales memory brightness with hit count (floor .45 → full 1)', () => {
    const one = flowLegs(companion({ turn: turn({ memory_hits: 1 }) })).memBright
    const many = flowLegs(companion({ turn: turn({ memory_hits: 99 }) })).memBright
    expect(one).toBeGreaterThanOrEqual(0.45)
    expect(one).toBeLessThan(1)
    expect(many).toBe(1)
    // monotonic: more hits never darker
    expect(flowLegs(companion({ turn: turn({ memory_hits: 4 }) })).memBright).toBeGreaterThan(one)
  })
})

// ── flowPath: loop-path generation ─────────────────────────────────────────
describe('flowPath', () => {
  const brain: Pt = { x: 100, y: 100 }
  const body: Pt = { x: 40, y: 160 }
  const mem: Pt = { x: 160, y: 160 }
  const legs = (over: Partial<FlowLegs>): FlowLegs => ({ body: false, mem: false, memBright: 0, ...over })

  it('threads a closed body→brain→memory→brain→body loop when both legs lit', () => {
    const d = flowPath(brain, body, mem, legs({ body: true, mem: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L160.0 160.0 L100.0 100.0 L40.0 160.0')
    // closed: starts and ends on the same point (seamless repeat, no teleport)
    expect(d.startsWith('M40.0 160.0')).toBe(true)
    expect(d.endsWith('L40.0 160.0')).toBe(true)
    expect(loopSegments(d)).toBe(4)
  })
  it('bounces body↔brain when only the body leg is lit', () => {
    const d = flowPath(brain, body, mem, legs({ body: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L40.0 160.0')
    expect(loopSegments(d)).toBe(2)
  })
  it('bounces brain↔memory when only the memory leg is lit', () => {
    const d = flowPath(brain, body, mem, legs({ mem: true }))
    expect(d).toBe('M100.0 100.0 L160.0 160.0 L100.0 100.0')
    expect(loopSegments(d)).toBe(2)
  })
  it('returns empty when no leg is lit (node pulse alone signals the brain)', () => {
    expect(flowPath(brain, body, mem, legs({}))).toBe('')
  })
})

// ── flowDur / flowStagger: timing from DURATION tokens ─────────────────────
describe('flow timing', () => {
  const four = 'M0 0 L1 1 L2 2 L3 3 L0 0'
  const two = 'M0 0 L1 1 L0 0'

  it('derives dur from DURATION.ambient × leg count (never hardcoded)', () => {
    expect(flowDur(four)).toBe(`${((DURATION.ambient * 4) / 1000).toFixed(2)}s`)
    expect(flowDur(two)).toBe(`${((DURATION.ambient * 2) / 1000).toFixed(2)}s`)
    // a 4-leg loop takes exactly twice as long as a 2-leg loop (constant speed)
    expect(parseFloat(flowDur(four))).toBeCloseTo(2 * parseFloat(flowDur(two)))
  })
  it('never yields a zero duration, even for a degenerate path', () => {
    expect(parseFloat(flowDur(''))).toBeGreaterThan(0)
  })
  it('staggers the trailing dot by a negative half-loop (starts mid-path)', () => {
    const s = flowStagger(four)
    expect(s.startsWith('-')).toBe(true)
    // half of the loop duration
    expect(Math.abs(parseFloat(s))).toBeCloseTo(parseFloat(flowDur(four)) / 2)
  })
})

// ── dev-only demo hook: ?demoFlow ─────────────────────────────────────────
describe('demo flow hook', () => {
  it('targets an exact companion id, or the first when the param is empty', () => {
    expect(isDemoFlowTarget('c2', 1, 'c2')).toBe(true)
    expect(isDemoFlowTarget('c1', 0, 'c2')).toBe(false)
    // bare `?demoFlow` (empty string) → first companion only
    expect(isDemoFlowTarget('c1', 0, '')).toBe(true)
    expect(isDemoFlowTarget('c2', 1, '')).toBe(false)
  })
  it('never targets anything when the param is absent (undefined)', () => {
    expect(isDemoFlowTarget('c1', 0, undefined)).toBe(false)
  })
  it('synthesises a turn + body that light BOTH flow legs', () => {
    const c = companion({
      turn: demoFlowTurn('c1'),
      devices: [demoFlowDevice('c1')],
    })
    const legs = flowLegs(c)
    expect(legs.body).toBe(true)
    expect(legs.mem).toBe(true)
    expect(legs.memBright).toBeGreaterThan(0)
    expect(demoFlowTurn('c1').memory_hits).toBeGreaterThan(0)
    expect(demoFlowDevice('c1').online).toBe(true)
  })
})

// ── Tier 2: event-driven directed pulses ──────────────────────────────────
describe('eventToPulse', () => {
  it('maps device sources to an inbound body pulse', () => {
    expect(eventToPulse('channel')).toEqual({ leg: 'body', dir: 'in' })
    expect(eventToPulse('hub')).toEqual({ leg: 'body', dir: 'in' })
  })
  it('maps memory to an outbound memory pulse (brain→mem)', () => {
    expect(eventToPulse('memory')).toEqual({ leg: 'mem', dir: 'out' })
  })
  it('maps agent to an outbound body pulse (response returning)', () => {
    expect(eventToPulse('agent')).toEqual({ leg: 'body', dir: 'out' })
  })
  it('returns null for sources that touch no leg', () => {
    expect(eventToPulse('data')).toBeNull()
    expect(eventToPulse('admin')).toBeNull()
    expect(eventToPulse('mission_control')).toBeNull()
    expect(eventToPulse('nonsense')).toBeNull()
  })
})

describe('directedLegPath', () => {
  const brain: Pt = { x: 100, y: 100 }
  const moon: Pt = { x: 40, y: 160 }
  it("'in' travels moon → brain", () => {
    expect(directedLegPath(brain, moon, 'in')).toBe('M40.0 160.0 L100.0 100.0')
  })
  it("'out' travels brain → moon (reverse of 'in')", () => {
    expect(directedLegPath(brain, moon, 'out')).toBe('M100.0 100.0 L40.0 160.0')
  })
  it('derives the dart duration from a motion token, non-zero', () => {
    expect(flowEventDur()).toMatch(/^\d+\.\d{2}s$/)
    expect(parseFloat(flowEventDur())).toBeGreaterThan(0)
  })
})
