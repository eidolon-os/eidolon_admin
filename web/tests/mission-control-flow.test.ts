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
  currentStageKey,
  demoFlowDevice,
  demoFlowTurn,
  directedLegPath,
  eventBelongsToTurn,
  eventToPulse,
  eventTone,
  flowDur,
  flowEventDur,
  flowLegs,
  flowPath,
  flowStagger,
  isDemoFlowTarget,
  loopSegments,
  PULSE_MIN_GAP_MS,
  pulseInScope,
  pulseThrottled,
  shouldFlow,
  spineReached,
  stageMoon,
  type FlowLegs,
  type Pt,
} from '../src/modules/mission-control/flow'
import { DURATION } from '../src/modules/mission-control/motion'
import { statusClass } from '../src/modules/mission-control/format'
import type { CompanionUnit } from '../src/modules/mission-control/types'
import type { RuntimeDevice, RuntimeEvent, RuntimeTurn, RuntimeTurnStage } from '../src/api/missionControl'

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
    trace_id: 't1', channel_turn_id: 't1', agent_turn_id: null,
    device_id: 'd1', status: 'running', trigger: 'voice', started_at: null,
    finished_at: null, latency_ms: 120, memory_hits: 0, tool_names: [],
    privacy_mode: null, phase: 'user_speech_open', outcome: 'deferred',
    terminal_reason: '', event_ids: [], missing_milestones: [], stages: [], ...over,
  }
}
function companion(over: Partial<CompanionUnit> = {}): CompanionUnit {
  return {
    id: 'c1', name: 'Aria', kind: 'companion', status: 'active', genome: '',
    realm: '', isActiveRealm: false, recall: null, runners: '', write: '',
    devices: [], activeTurn: null, turn: null, turns: [], jobs: [], isPrimary: false, ...over,
  }
}

it('renders a session-reconciled orphan turn as a failure', () => {
  expect(statusClass('orphaned')).toBe('bad')
  expect(statusClass('interrupted')).toBe('warn')
})

// ── shouldFlow: which companions circulate ────────────────────────────────
describe('shouldFlow', () => {
  it('flows the focused companion with an active turn, at any active count', () => {
    expect(shouldFlow('c1', 'c1', true, 9)).toBe(true)
  })
  it('never flows without an active turn', () => {
    expect(shouldFlow('c1', 'c1', false, 1)).toBe(false)
    expect(shouldFlow('c1', '', false, 1)).toBe(false)
  })
  it('flows an unfocused active companion while few are active', () => {
    expect(shouldFlow('c1', '', true, 1)).toBe(true)
    expect(shouldFlow('c1', '', true, 2)).toBe(true)
    expect(shouldFlow('c1', undefined, true, 2)).toBe(true)
  })
  it('drops unfocused flow once the active count exceeds AUTO_FLOW_MAX', () => {
    // a non-focused companion in a busy scene keeps only the lighter node pulse
    expect(shouldFlow('c2', 'c1', true, 3)).toBe(false)
    expect(shouldFlow('c1', '', true, 3)).toBe(false)
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
  const act: Pt = { x: 100, y: 20 }
  const legs = (over: Partial<FlowLegs>): FlowLegs => ({ body: false, mem: false, memBright: 0, act: false, ...over })

  it('threads a closed body→brain→memory→brain→body loop when both legs lit', () => {
    const d = flowPath(brain, body, mem, act, legs({ body: true, mem: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L160.0 160.0 L100.0 100.0 L40.0 160.0')
    // closed: starts and ends on the same point (seamless repeat, no teleport)
    expect(d.startsWith('M40.0 160.0')).toBe(true)
    expect(d.endsWith('L40.0 160.0')).toBe(true)
    expect(loopSegments(d)).toBe(4)
  })
  it('bounces body↔brain when only the body leg is lit', () => {
    const d = flowPath(brain, body, mem, act, legs({ body: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L40.0 160.0')
    expect(loopSegments(d)).toBe(2)
  })
  it('bounces brain↔memory when only the memory leg is lit', () => {
    const d = flowPath(brain, body, mem, act, legs({ mem: true }))
    expect(d).toBe('M160.0 160.0 L100.0 100.0 L160.0 160.0')
    expect(loopSegments(d)).toBe(2)
  })
  it('returns empty when no leg is lit (node pulse alone signals the brain)', () => {
    expect(flowPath(brain, body, mem, act, legs({}))).toBe('')
  })
  it('includes the activity moon while a turn is live', () => {
    const d = flowPath(brain, body, mem, act, legs({ body: true, act: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L100.0 20.0 L100.0 100.0 L40.0 160.0')
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
  const event = (source: RuntimeEvent['source'], type = 'runtime.event', payload: Record<string, any> = {}) => ({ source, type, payload })
  it('maps device sources to an inbound body pulse', () => {
    expect(eventToPulse(event('channel', 'channel.turn.phase_changed', { phase: 'user_speech_open' }))).toEqual({ leg: 'body', dir: 'in' })
    expect(eventToPulse(event('hub'))).toEqual({ leg: 'body', dir: 'in' })
  })
  it('maps memory to an outbound memory pulse (brain→mem)', () => {
    expect(eventToPulse(event('memory'))).toEqual({ leg: 'mem', dir: 'out' })
  })
  it('maps agent activity to the activity moon', () => {
    expect(eventToPulse(event('agent'))).toEqual({ leg: 'act', dir: 'in' })
  })
  it('uses Channel milestone semantics for brain and playback direction', () => {
    expect(eventToPulse(event('channel', 'channel.turn.milestone', { milestone: 'generating' }))).toEqual({ leg: 'act', dir: 'out' })
    expect(eventToPulse(event('channel', 'channel.turn.milestone', { milestone: 'brain_first_delta' }))).toEqual({ leg: 'act', dir: 'in' })
    expect(eventToPulse(event('channel', 'channel.turn.milestone', { milestone: 'first_audio' }))).toEqual({ leg: 'body', dir: 'out' })
  })
  it('returns null for sources that touch no leg', () => {
    expect(eventToPulse(event('data'))).toBeNull()
    expect(eventToPulse(event('admin'))).toBeNull()
    expect(eventToPulse(event('mission_control'))).toBeNull()
  })
})

describe('eventBelongsToTurn', () => {
  it('never treats two missing trace ids as a correlation match', () => {
    const legacy = turn({
      turn_id: 'agent-1', trace_id: null, channel_turn_id: null,
      agent_turn_id: 'agent-1', event_ids: [],
    })
    expect(eventBelongsToTurn({
      event_id: 'hub-1', trace_id: null, turn_id: null, payload: {},
    }, legacy)).toBe(false)
  })

  it('matches the explicit event list, non-empty trace, and either hop id', () => {
    const unified = turn({
      turn_id: 'channel-1', trace_id: 'channel-1', channel_turn_id: 'channel-1',
      agent_turn_id: 'agent-1', event_ids: ['phase-1'],
    })
    expect(eventBelongsToTurn({
      event_id: 'phase-1', trace_id: null, turn_id: null, payload: {},
    }, unified)).toBe(true)
    expect(eventBelongsToTurn({
      event_id: 'memory-1', trace_id: 'channel-1', turn_id: null, payload: {},
    }, unified)).toBe(true)
    expect(eventBelongsToTurn({
      event_id: 'agent-event', trace_id: null, turn_id: 'agent-1', payload: {},
    }, unified)).toBe(true)
    expect(eventBelongsToTurn({
      event_id: 'channel-event', trace_id: null, turn_id: null,
      payload: { channel_turn_id: 'channel-1' },
    }, unified)).toBe(true)
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

describe('eventTone', () => {
  it("escalates errors and failed outcomes to 'bad'", () => {
    expect(eventTone('error')).toBe('bad')
    expect(eventTone('info', 'failure')).toBe('bad')
    // a failed outcome outranks an otherwise-info severity
    expect(eventTone(undefined, 'failure')).toBe('bad')
  })
  it("maps warnings and denied outcomes to 'warn'", () => {
    expect(eventTone('warn')).toBe('warn')
    expect(eventTone('info', 'denied')).toBe('warn')
  })
  it("treats info / success / deferred / missing signals as 'normal'", () => {
    expect(eventTone('info')).toBe('normal')
    expect(eventTone(undefined, 'success')).toBe('normal')
    expect(eventTone('info', 'deferred')).toBe('normal')
    expect(eventTone()).toBe('normal')
  })
  it("'bad' wins over 'warn' when both signals disagree", () => {
    expect(eventTone('warn', 'failure')).toBe('bad')
  })
})

describe('pulseInScope', () => {
  it("'focused' scope emits only for the focused companion", () => {
    expect(pulseInScope('c1', 'c1', 'focused')).toBe(true)
    expect(pulseInScope('c2', 'c1', 'focused')).toBe(false)
  })
  it("'focused' scope emits nothing when no companion is focused", () => {
    expect(pulseInScope('c1', '', 'focused')).toBe(false)
    expect(pulseInScope('c1', undefined, 'focused')).toBe(false)
  })
  it("'all' scope emits for every companion (even unfocused / no focus)", () => {
    expect(pulseInScope('c2', 'c1', 'all')).toBe(true)
    expect(pulseInScope('c9', '', 'all')).toBe(true)
    expect(pulseInScope('c9', undefined, 'all')).toBe(true)
  })
})

describe('pulseThrottled', () => {
  it('drops a pulse that arrives inside the min gap', () => {
    expect(pulseThrottled(1000, 1000 + PULSE_MIN_GAP_MS - 1)).toBe(true)
  })
  it('allows a pulse once the min gap has elapsed', () => {
    expect(pulseThrottled(1000, 1000 + PULSE_MIN_GAP_MS)).toBe(false)
    expect(pulseThrottled(1000, 5000)).toBe(false)
  })
  it('never throttles the first pulse on a leg (lastMs 0)', () => {
    expect(pulseThrottled(0, 1_000_000)).toBe(false)
  })
})

// ── currentStageKey: the one shared playhead ───────────────────────────────
describe('currentStageKey', () => {
  const stage = (key: string, status: string): RuntimeTurnStage => ({ key, label: key, status, latency_ms: null })
  it('is empty for a null/undefined turn or one with no stages', () => {
    expect(currentStageKey(null)).toBe('')
    expect(currentStageKey(undefined)).toBe('')
    expect(currentStageKey(turn({ stages: [] }))).toBe('')
  })
  it('points at the running/pending stage when one is in flight', () => {
    expect(currentStageKey(turn({ stages: [stage('input', 'done'), stage('agent_turn', 'running')] }))).toBe('agent_turn')
  })
  it('falls back to the last completed stage when nothing runs', () => {
    expect(currentStageKey(turn({ stages: [stage('input', 'done'), stage('memory_recall', 'done')] }))).toBe('memory_recall')
  })
  it('prefers a running stage over an already-completed later one', () => {
    expect(currentStageKey(turn({ stages: [stage('input', 'running'), stage('memory_recall', 'done')] }))).toBe('input')
  })
})

// ── spineReached: the bus wavefront ────────────────────────────────────────
describe('spineReached', () => {
  it('flows the whole spine when there is no current stage (graceful default)', () => {
    expect(spineReached('', 'memory')).toBe(true)
    expect(spineReached('', 'livekit')).toBe(true)
  })
  it('flows only edges up to and including the current service', () => {
    // hot = agent → hub/livekit/channel/agent reached; memory (later) not yet
    expect(spineReached('agent', 'channel')).toBe(true)
    expect(spineReached('agent', 'agent')).toBe(true)
    expect(spineReached('agent', 'memory')).toBe(false)
  })
  it('reaches the whole spine once memory (the tail) is current', () => {
    expect(spineReached('memory', 'memory')).toBe(true)
    expect(spineReached('memory', 'livekit')).toBe(true)
  })
  it('flows the whole spine for an off-spine hot service rather than going dark', () => {
    expect(spineReached('mementos', 'agent')).toBe(true)
  })
})

// ── stageMoon: constellation moon for the current stage ────────────────────
describe('stageMoon', () => {
  it('maps input to the body moon', () => {
    expect(stageMoon('input')).toBe('body')
  })
  it('maps recall and write to the memory moon', () => {
    expect(stageMoon('memory_recall')).toBe('mem')
    expect(stageMoon('memory_write')).toBe('mem')
  })
  it('maps the agent turn and its tools to the activity moon', () => {
    expect(stageMoon('agent_turn')).toBe('act')
    expect(stageMoon('tools')).toBe('act')
  })
  it('is empty for an unknown or empty stage', () => {
    expect(stageMoon('')).toBe('')
    expect(stageMoon('nonsense')).toBe('')
  })
})
